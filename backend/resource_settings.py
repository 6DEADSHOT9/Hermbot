"""Owner resource settings and idle-only application through the lifecycle outbox."""
from fastapi import HTTPException
from psycopg.types.json import Jsonb

from . import db, limits, service


def state(conn):
    return conn.execute('SELECT * FROM workspace_settings WHERE singleton=true').fetchone()


def bots(conn):
    return conn.execute("SELECT * FROM bots WHERE status<>'deleted' ORDER BY id").fetchall()


def signature(workspace, teammates, bot=None, affected_work=None):
    facts = {key: value for key, value in workspace['machine_facts'].items()
             if key not in ('observed_at', 'warnings', 'disk_free_bytes', 'free_inodes', 'core_services')}
    return service.digest({'facts': facts, 'max_bots': workspace['max_bots'], 'max_active': workspace['max_active'],
                           'limits_revision': workspace['limits_revision'],
                           'bot_revision': bot['revision'] if bot else None, 'affected_work': affected_work,
                           'bots': [(b['id'], b['revision'], b['memory_mb'], b['cpus']) for b in teammates]})


def read(conn, bot_id=None):
    from . import registry
    workspace, teammates = state(conn), bots(conn)
    bot = registry.require_bot(conn, bot_id) if bot_id else None
    overrides = {**workspace['limit_overrides'], **(bot['limit_overrides'] if bot else {})}
    metadata = limits.describe(workspace['machine_facts'], workspace['max_bots'], workspace['max_active'], overrides)
    if bot:
        for key, row in metadata.items():
            if key not in bot['limit_overrides'] and key in workspace['limit_overrides']:
                row['source'] = 'workspace'
    projected = []
    for teammate in teammates:
        observed = teammate['limits_observed']
        resource_errors = []
        try:
            limits.validate_resources(teammate['memory_mb'], float(teammate['cpus']), workspace['machine_facts'])
        except ValueError as error:
            resource_errors.append(str(error))
        override_errors = []
        for key, value in teammate['limit_overrides'].items():
            try:
                limits.validate_overrides({key: value}, workspace['machine_facts'], workspace['max_bots'], workspace['max_active'])
            except ValueError as error:
                override_errors.append(str(error))
        projected.append({'id': teammate['id'], 'name': teammate['name'], 'status': teammate['status'],
                          'desired_state': teammate['desired_state'], 'override_errors': override_errors,
                          'resource_errors': resource_errors,
                          'pending': teammate['applied_limits_revision'] < workspace['limits_revision'] or teammate['applied_revision'] < teammate['revision'],
                          'observed': observed, 'overrides': teammate['limit_overrides'],
                          'affected_tasks': [str(t['id']) for t in registry.busy_tasks(conn, teammate['id'])]})
    # Docker configuration is observable; it is not proof of rootless kernel enforcement.
    if workspace['machine_facts'].get('logging_driver') not in (None, '', 'json-file', 'local'):
        for key in ('log_size_mb', 'log_files'):
            metadata[key]['enforcement'] = 'managed_externally'
    history = conn.execute("SELECT data,created_at FROM events WHERE kind='settings.advanced_changed' AND data->>'scope'=%s ORDER BY id DESC LIMIT 20",
                           (bot_id or 'workspace',)).fetchall()
    core = workspace['machine_facts'].get('core_services', {})
    core_score = 0 if workspace['machine_facts'].get('rootless') else limits.DATA_OOM_SCORE_ADJ
    core_pending = []
    for name in ('postgres', 'storage'):
        observed = core.get(name, {})
        logs = observed.get('LogConfig', {})
        rotation = workspace['machine_facts'].get('logging_driver') not in ('json-file', 'local') or (
            logs.get('Config', {}).get('max-size') == f'{limits.LOG_SIZE_MB}m' and logs.get('Config', {}).get('max-file') == str(limits.LOG_FILES))
        if observed.get('OomScoreAdj') != core_score or not rotation or (name == 'postgres' and observed.get('MemoryReservation') != 512 * limits.MIB):
            core_pending.append(name)
    return {'revision': bot['revision'] if bot else workspace['limits_revision'],
            'machine_signature': signature(workspace, teammates, bot, [(b['id'], b['affected_tasks']) for b in projected]),
            'machine': {'cpus': None, 'memory_bytes': None, 'rootless': None, 'logging_driver': '',
                        'cgroup_version': None, 'observed_at': None, 'warnings': [], **workspace['machine_facts']},
            'limits': metadata, 'overrides': bot['limit_overrides'] if bot else workspace['limit_overrides'],
            'resources': limits.resource_bounds(workspace['machine_facts'], workspace['max_active']),
            'capacity': limits.capacity(workspace['machine_facts'], teammates, workspace['max_bots'], workspace['max_active']),
            'disk_preview': limits.disk_preview(workspace['machine_facts'], workspace['max_bots']),
            'bots': projected,
            'core_services_pending_restart': core_pending,
            'history': [{**item['data'], 'changed_at': item['created_at']} for item in history]}


def validate_resources(conn, config, existing=None):
    workspace = state(conn)
    try:
        limits.validate_resources(config['memory_mb'], config['cpus'], workspace['machine_facts'], existing)
        if not all(row['available'] for row in limits.describe(workspace['machine_facts']).values()):
            raise ValueError('This machine cannot support the required open-file limits')
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


def frozen(conn, bot):
    workspace = state(conn)
    values = limits.resolved_values(workspace['machine_facts'], workspace['max_bots'], workspace['max_active'],
                                    {**workspace['limit_overrides'], **bot['limit_overrides']})
    return {'limits': values, 'limits_revision': workspace['limits_revision']}


def idle(conn, bot_id):
    from . import registry
    if conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (bot_id,)).fetchone():
        return False
    if registry.pending_operation(conn, bot_id):
        return False
    if conn.execute('SELECT 1 FROM attempts WHERE bot_id=%s AND status=ANY(%s)',
                    (bot_id, list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone():
        return False
    return not conn.execute("SELECT 1 FROM tasks WHERE bot_id=%s AND status<>ALL(%s)",
                            (bot_id, list(service.TERMINAL - {'uncertain'}) + ['queued'])).fetchone()


def save(conn, body, bot_id=None):
    from . import registry
    conn.execute('SELECT pg_advisory_xact_lock(887832102)')
    workspace = conn.execute('SELECT * FROM workspace_settings WHERE singleton=true FOR UPDATE').fetchone()
    if body.apply_now:
        # Admission takes a shared bot lock; freeze the reviewed work before cancelling any of it.
        conn.execute("SELECT id FROM bots WHERE status='active' AND (%s::text IS NULL OR id=%s) ORDER BY id FOR UPDATE", (bot_id, bot_id)).fetchall()
    bot = registry.require_bot(conn, bot_id, lock=True) if bot_id else None
    current = read(conn, bot_id)
    if body.expected_revision != current['revision'] or body.machine_signature != current['machine_signature']:
        raise HTTPException(409, 'Resource settings or this machine changed. Reload before saving.')
    old = bot['limit_overrides'] if bot else workspace['limit_overrides']
    values = {**old, **body.values}
    values = {key: value for key, value in values.items() if value is not None}
    try:
        limits.validate_overrides(body.values, workspace['machine_facts'], workspace['max_bots'], workspace['max_active'])
        warnings = limits.override_warnings({k: v for k, v in body.values.items() if old.get(k) != v},
                                            workspace['machine_facts'], workspace['max_bots'], workspace['max_active'])
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if bot_id and set(body.values) - {'pids_limit'}:
        raise HTTPException(422, 'Only the process limit has a per-teammate override here.')
    affected = [b for b in current['bots'] if (not bot_id or b['id'] == bot_id)
                and b['status'] == 'active' and b['desired_state'] == 'running']
    if body.apply_now and values != old:
        tasks = [t for b in affected for t in b['affected_tasks']]
        warnings.append(f"Restart {', '.join(b['name'] for b in affected)} now; stop {len(tasks)} affected work items.")
    if values != old and not set(warnings).issubset(body.accepted_warnings):
        return {**current, 'requires_confirmation': True, 'warnings': warnings}
    if values == old:
        return current
    if bot:
        registry.reserve_lifecycle(conn, bot, body.apply_now)
        conn.execute('UPDATE bots SET limit_overrides=%s,revision=revision+1 WHERE id=%s', (Jsonb(values), bot_id))
        registry.enqueue(conn, registry.require_bot(conn, bot_id), 'configure', f'limits:{bot_id}:{bot["revision"]+1}')
    else:
        # Only the wanted marker changes here; active runtime credentials stay valid until idle.
        conn.execute('UPDATE workspace_settings SET limit_overrides=%s,limits_revision=limits_revision+1', (Jsonb(values),))
        if body.apply_now:
            for entry in affected:
                target = registry.require_bot(conn, entry['id'], lock=True)
                registry.reserve_lifecycle(conn, target, True)
    for name in body.values:
        if old.get(name) != values.get(name):
            db.event(conn, 'settings.advanced_changed', bot_id=bot_id,
                     data={'scope': bot_id or 'workspace', 'setting': name, 'from': old.get(name),
                           'to': values.get(name), 'accepted_warnings': warnings})
    return read(conn, bot_id)


def workspace_warnings(conn, body):
    workspace = state(conn)
    result = limits.capacity(workspace['machine_facts'], bots(conn), body.max_bots, body.max_active)
    if result['process_impossible']:
        raise HTTPException(422, 'Even the minimum process allowance cannot fit this maximum number of teammates.')
    changed = (body.max_bots, body.max_active) != (workspace['max_bots'], workspace['max_active'])
    if changed and result['above_capacity']:
        warning = (f"Maximum teammates {body.max_bots} with {body.max_active} concurrent runs exceeds the estimated "
                   f"capacity of {result['capacity']} ({', '.join(result['limiting_resources'])}). Memory may be exhausted.")
        if warning not in body.accepted_warnings:
            raise HTTPException(409, {'message': warning, 'code': 'capacity_confirmation_required', 'warnings': [warning]})
        db.event(conn, 'settings.capacity_confirmed', data={'max_bots': body.max_bots, 'max_active': body.max_active, 'warning': warning})
    return result


def update_machine(conn, facts):
    conn.execute('SELECT pg_advisory_xact_lock(887832102)')
    workspace = state(conn)
    changed = limits.resolved_values(workspace['machine_facts']) != limits.resolved_values(facts)
    conn.execute('UPDATE workspace_settings SET machine_facts=%s,limits_revision=limits_revision+%s',
                 (Jsonb(facts), int(changed)))
    if changed:
        db.event(conn, 'settings.machine_changed', data={'reason': 'Computed resource defaults changed with machine facts'})
