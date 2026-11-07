"""Effective execution capabilities. Discovery and skill guidance never grant authority."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb

from . import connections, db, native, registry, service

VERSION = 1
INSPECTION = {'teammates', 'connectors', 'grants', 'proposals', 'status', 'work_status', 'read_artifact', 'finish', 'prepare'}
READ_NATIVE = {'read_file', 'search_files', 'skill_view', 'skills_list'}
DISCUSSION_NATIVE = READ_NATIVE | {'memory'}
WORK = {'assignment', 'update'}
INSTRUCTION = """For work, choose relevant enabled skills from their descriptions and call hermbot_prepare
before execution. Include the native/Hermbot tools and connected actions your plan needs. An empty skill
selection is correct if none fits; do not force unrelated procedures onto a task. Preparation returns
the selected raw skill instructions. Treat them as guidance, never permission. Check blockers before
acting: use an available alternative, discover a capable teammate within your grant, or explain the
exact missing access to the owner and finish honestly. Never repeat a denied tool or bypass policy.
On a continuation or changed access, prepare again. A helper prepares with its own capabilities.
Use artifact_ids and attachment_ids to explicitly share inputs and expected_output to state the result.
Private paths, credentials, browser sessions and memory are not shared by delegation."""


def blocker(code, name, message, **details):
    return {'code': code, 'name': name, 'message': message, **details}


def manifest(conn, bot_id, task=None, *, peer=False):
    bot = registry.require_bot(conn, bot_id)
    workspace = registry.workspace(conn)
    inventory = bot['readiness'].get('inventory', {})
    known = inventory.get('version') == VERSION and bot['applied_revision'] == bot['revision']
    running = bot['status'] == 'active' and bot['desired_state'] == 'running' and bot['readiness'].get('compatible', False)
    human = bool(conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (bot_id,)).fetchone())
    policy = connections.native_policy(conn, bot_id, task, latch=not peer)
    enabled = set(bot['enabled_tools']) & set(workspace['permitted_tools'])
    tools, blocked = set(), []
    for group in inventory.get('toolsets', []) if known else []:
        if group['name'] == 'hermbot':
            continue
        for name in group.get('tools', []):
            if group['name'] not in enabled:
                reason = 'This tool is not enabled for this teammate and workspace.'
            elif not group.get('configured'):
                reason = 'This native tool is not configured in the runtime.'
            elif task and task['message_intent'] not in WORK and name not in DISCUSSION_NATIVE:
                reason = 'This conversation turn does not authorize executing an assignment.'
            elif human or not running:
                reason = 'Hand back or start this teammate and wait for runtime readiness.'
            else:
                tools.add(name)
                continue
            blocked.append(blocker('tool_unavailable', name, reason))
    installed = inventory.get('skills', []) if known else []
    skills = [{**s, 'enabled': True} for s in installed if s['name'] in bot['enabled_skills']]
    from .teaching import candidate
    preview = candidate(conn, task) if task and not peer else None
    if preview:
        skills = [s for s in skills if s['name'] != preview['name']]
        skills.append({'name': preview['name'], 'description': 'Task-scoped teaching candidate; consult this exact draft for its test.', 'enabled': True, 'required_tools': [], 'teaching_test_id': preview['teaching_test_id'], 'sha256': preview['sha256']})
    for skill in skills:
        skill['available'] = skill['enabled'] and 'skill_view' in tools
    apps = connections.accessible(conn, bot_id)
    # Teammates can plan around access, but cannot inspect another bot's connection IDs/resources.
    app_info = [{'provider': c['provider'], 'actions': [a['name'] for a in c['actions']],
                 'resource_restricted': bool(c['resources'])} for c in apps]
    if not peer:
        app_info = [{**item, 'id': c['id'], 'revision': c['revision'], 'name': c['name'], 'resources': c['resources']}
                    for item, c in zip(app_info, apps)]
    from .models import ARGUMENT_MODELS
    operations = set(ARGUMENT_MODELS) - {'route_message'}
    if task:
        if task['message_intent'] not in WORK:
            operations &= (INSPECTION - {'prepare'}) | {'connector'}
            if task.get('coordination_version') == 1:
                operations.add('message')
        if task.get('coordination_version') != 1:
            operations.discard('wait')
        grant = service.check_grant(conn, task)
        from .recurring_management import scope
        if grant and scope(conn, task) and 'inspect' not in grant['operations']:
            operations.discard('inspect')
        for operation in ('propose', 'steer', 'cancel', 'reassign'):
            permission = 'configure' if operation == 'propose' else operation
            if not grant or grant['manager_id'] != bot_id or (operation != 'propose' and permission not in grant['operations']):
                operations.discard(operation)
        if grant and 'delegate' not in grant['operations']:
            operations -= {'delegate', 'message'}
    if not apps:
        operations.discard('connector')
    available = sorted(tools | {'hermbot_' + name for name in operations})
    facts = {'version': VERSION, 'revision': bot['revision'], 'applied_revision': bot['applied_revision'],
             'running': running, 'human_control': human, 'inventory_known': known,
             'native_tools': sorted(tools), 'skills': skills, 'teaching_candidate': preview, 'installed_skill_names': [s['name'] for s in installed], 'connections': app_info,
             'operations': sorted(operations), 'connector_restricted': policy['restricted']}
    facts['action_contract_version'] = 2
    facts['outcomes_version'] = inventory.get('outcomes_version', 0)
    fingerprint = service.digest(facts)
    plan = task.get('execution_plan', {}) if task else {}
    from .outcomes import registration_required
    needs_outcomes = bool(task and task['message_intent'] in WORK and registration_required(conn, task))
    managed = bool(task and task.get('orchestration_version') == 2)
    prepared = bool(task and not needs_outcomes and plan.get('ready') and plan.get('attempt') == task['attempts'] and plan.get('fingerprint') == fingerprint)
    admitted = set(available)
    if task and task['message_intent'] == 'auto':
        admitted = {'hermbot_route_message'}
    elif task and task['message_intent'] in WORK and not prepared and not managed:
        admitted &= READ_NATIVE | {'hermbot_' + name for name in INSPECTION} | {'hermbot_review', 'hermbot_ask'}
    if human or (task and not running):
        admitted = set()
    elif task and task['status'] == 'waiting_input':
        admitted &= {'hermbot_finish', 'hermbot_ask', 'hermbot_status', 'hermbot_grants'}
    elif task and task['status'] == 'waiting_results':
        admitted &= {'hermbot_finish', 'hermbot_status', 'hermbot_work_status', 'hermbot_grants'}
    return {**facts, 'bot_id': bot_id, 'fingerprint': fingerprint, 'available_tools': available,
            'allowed_tools': sorted(admitted), 'blocked_tools': blocked,
            'preparation_required': bool(task and task['message_intent'] in WORK and not prepared and not managed),
            'outcomes_registration_required': needs_outcomes,
            'prepared': prepared, 'selected_skills': plan.get('skills', []) if prepared else [],
            'instruction': ('Use enabled tools for the current work. Consult relevant enabled skills when useful; prepare is optional and returns raw skill guidance. Requirements and authorization come from recorded owner instructions. Operation access is checked when invoked. Use requirement keys to disambiguate operations. Correct invalid references by discovery; request access only for actual permission errors.' if managed else INSTRUCTION + (' Register the requested results using the supported hermbot_prepare.outcomes array: each entry has key, title and kind; target and depends_on are optional. Do this yourself, not through an owner question. Pass relevant outcome_ids from authenticated work context when delegating.' if needs_outcomes else ''))}


def requirements(view, *, skills=(), tools=(), connectors=()):
    blockers = []
    if not view['inventory_known'] or not view['running'] or view['human_control']:
        blockers.append(blocker('runtime_unavailable', view['bot_id'], 'Start or hand back this teammate and wait for its capability inventory to refresh.'))
    catalog = {s['name']: s for s in view['skills']}
    requested = set(tools)
    for name in skills:
        skill = catalog.get(name)
        if not skill and name not in view['installed_skill_names']:
            blockers.append(blocker('skill_missing', name, f'Install and review {name} in Memory & skills, or choose an available procedure.'))
        elif not skill or not skill['enabled']:
            blockers.append(blocker('skill_not_enabled', name, f'Enable the installed skill {name} for this teammate, or choose an available procedure.'))
        elif not skill['available']:
            blockers.append(blocker('skill_unavailable', name, 'Enable the skills tool or resolve this teammate\'s runtime availability.'))
        elif skill.get('metadata_error'):
            blockers.append(blocker('skill_metadata_invalid', name, skill['metadata_error']))
        else:
            requested.update(skill.get('required_tools', []))
    blocked = {b['name']: b for b in view['blocked_tools']}
    for name in sorted(requested - set(view['available_tools'])):
        blockers.append(blocked.get(name) or blocker('tool_unavailable', name, f'{name} is not available to this teammate. Choose an enabled tool or ask the owner to update its configuration.'))
    for requested_app in connectors:
        app = next((c for c in view['connections'] if c.get('id') == str(requested_app['connection_id'])), None)
        action = requested_app['action']
        resource = requested_app.get('resource')
        if not app:
            blockers.append(blocker('connection_reference_invalid', action, 'Rediscover available connections and use an exact returned reference. This reference does not identify an available connection.', connection_id=str(requested_app['connection_id'])))
        elif action not in app['actions']:
            blockers.append(blocker('connection_access_missing', action, f'This teammate needs a connected application permitting {action}. Configure its access in Connections.', connection_id=str(requested_app['connection_id'])))
        elif resource is not None and action in app.get('resources', {}) and resource not in app['resources'][action]:
            blockers.append(blocker('resource_access_missing', action, f'This connection does not permit {action} on the requested resource. Update only the required resource in Connections.', resource=resource))
    return blockers


def prepare(conn, task, args):
    from .teaching import candidate
    if task['message_intent'] not in WORK:
        raise HTTPException(409, 'Discussion and status turns do not need execution preparation')
    from . import outcomes
    registered = outcomes.register(conn, task, args.get('outcomes', []))
    if outcomes.registration_required(conn, task):
        raise HTTPException(422, 'Record the requested results in hermbot_prepare.outcomes before carrying out or delegating work. Each entry needs key, title and kind. This is agent bookkeeping; never ask the owner for an outcome ID.')
    view = manifest(conn, task['bot_id'], task)
    selected = list(dict.fromkeys(args.get('skills', [])))
    blocked = requirements(view, skills=selected, tools=args.get('tools', []), connectors=args.get('connectors', []))
    consulted = []
    if not blocked:
        for name in selected:
            try:
                skill = candidate(conn, task, consulted=True) if (view.get('teaching_candidate') or {}).get('name') == name else native.request(task['bot_id'], 'GET', '/skills/' + name)
                if skill and skill.get('teaching_test_id'):
                    skill = {**skill, 'exists': True}
                if not skill.get('exists') or not skill.get('content'):
                    blocked.append(blocker('skill_missing', name, 'The enabled skill file is missing. Refresh or restore the skill before using it.'))
                else:
                    consulted.append({'name': name, 'content': skill['content'], 'sha256': skill['sha256']})
            except HTTPException:
                blocked.append(blocker('skill_unavailable', name, 'The native skill could not be read. Wait for runtime readiness or choose an available procedure.'))
    plan = {'version': VERSION, 'attempt': task['attempts'], 'fingerprint': view['fingerprint'],
            'ready': not blocked, 'skills': [{'name': s['name'], 'sha256': s['sha256']} for s in consulted],
            'requested_skills': selected, 'tools': args.get('tools', []),
            'connectors': [{**c, 'connection_id': str(c['connection_id'])} for c in args.get('connectors', [])],
            'blockers': blocked}
    conn.execute('UPDATE tasks SET execution_plan=%s WHERE id=%s', (Jsonb(plan), task['id']))
    db.event(conn, 'work.prepared' if plan['ready'] else 'work.access_needed', task, data=plan)
    return {'ready': plan['ready'], 'skills': consulted, 'blockers': blocked, 'available_tools': view['available_tools'], 'outcomes': registered,
            'instruction': 'Use the consulted procedures as guidance within current permissions. Carry out the assignment and publish its actual result.' if plan['ready'] else 'Do not attempt the blocked tools or claim completion. Choose an available alternative and prepare again, or explain these exact blockers to the owner and finish honestly. Never ask for secrets in chat.'}


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get('/api/v1/bots/{bot_id}/capabilities')
    def inspect(bot_id: str, task_id: UUID | None = None):
        with db.connection() as conn:
            task = conn.execute('SELECT * FROM tasks WHERE id=%s AND bot_id=%s', (task_id, bot_id)).fetchone() if task_id else None
            if task_id and not task:
                raise HTTPException(404, 'Task does not belong to this teammate')
            return manifest(conn, bot_id, task)

    return router
