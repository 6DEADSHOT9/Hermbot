"""Read-only audit of the saved work-outcome checks and current owner workspace."""
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET

from native_tools_audit import BASE, PROTECTION, SOURCE, command

ROOT = Path(__file__).resolve().parent.parent


def main():
    token = next(line.split('=', 1)[1].strip().strip('"\'') for line in
                 (ROOT / '.env').read_text().splitlines() if line.startswith('OWNER_TOKEN='))

    def get(path):
        request = urllib.request.Request('http://localhost:8780' + path,
                                         headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    before = json.loads((ROOT / 'test-results/work-outcomes-before.json').read_text())
    proof = json.loads((ROOT / 'test-results/work-outcomes-acceptance.json').read_text())
    ui = json.loads((ROOT / 'test-results/work-outcomes-ui.json').read_text())
    assert proof['complete'] and proof['fixture_archived'] and not proof['external_changes']
    assert all(ui[key] for key in ('complete', 'typed_answers', 'choice_answers', 'partial_results',
                                   'readable_connector_preview', 'structured_draft_edit_preserves_binding'))
    state = get('/api/v1/state')
    assert {c['id'] for c in state['conversations'] if c['kind'] == 'group'} == set(before['groups'])
    assert state['workspace'] == before['workspace']
    assert {b['id'] for b in state['bots']} == set(before['bots'])
    connections = get('/api/v1/connections')
    fields = ('id', 'status', 'revision', 'bot_ids', 'actions', 'resources')
    assert [{key: c[key] for key in fields} for c in connections['connections']] == before['connections']
    draft = proof['draft_prepared']
    assert draft['member_id_accepted'] and draft['outcome_bound'] and draft['denied_without_dispatch']
    operation = next(o for o in connections['operations'] if o['id'] == draft['operation_id'])
    assert operation['status'] == 'denied' and not operation['result']
    assert operation['action'] == 'slack.post' and operation['arguments']['channel'] == 'UHERMBOTTEST'
    handoff_path = ROOT / 'test-results/outcome-handoff-acceptance.json'
    handoff = json.loads(handoff_path.read_text()) if handoff_path.exists() else None
    if handoff:
        assert handoff['complete'] and handoff['fixture_archived'] and handoff['original_groups_preserved']
        assert handoff['manager_registered'] and handoff['helper_received_result']
        assert handoff['exact_review_denied_without_dispatch'] and not handoff['external_changes']
        peer_operation = next(o for o in connections['operations'] if o['id'] == handoff['operation_id'])
        assert peer_operation['status'] == 'denied' and not peer_operation['result']
        prior_task = json.loads((ROOT / 'test-results/outcome-handoff-owner-task-before.json').read_text())
        task = next(t for t in state['tasks'] if t['id'] == prior_task['id'])
        assert all(task.get(key) == value for key, value in prior_task.items())

    original = json.loads(command('docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                                  '/opt/hermes/.venv/bin/python', BASE, '-c', SOURCE))
    built = json.loads(command('docker', 'run', '--rm', '--network', 'none', '--entrypoint',
                               '/opt/hermes/.venv/bin/python', 'hermbot-hermes:phase4', '-c', SOURCE))
    assert len(original) > 500 and built == original
    volumes, runtimes = [], []
    for bot in state['bots']:
        prior = before['bots'][bot['id']]
        assert all(bot[key] == value for key, value in prior.items() if key != 'revision')
        assert bot['revision'] == prior['revision'] + 1 == bot['applied_revision']
        readiness = bot['readiness']
        assert readiness['compatible'] and readiness['plugin_ready'] and readiness['adapter_ready']
        assert readiness['inventory']['outcomes_version'] == 1
        tools = next(row for row in readiness['inventory']['toolsets'] if row['name'] == 'hermbot')['tools']
        assert 'hermbot_ask' in tools and len(tools) == 19
        view = get('/api/v1/bots/' + bot['id'] + '/capabilities')
        assert not view['connector_restricted'] and view['outcomes_version'] == 1
        expected_native = {tool for row in readiness['inventory']['toolsets']
                           if row['name'] in bot['enabled_tools'] and row['configured']
                           for tool in row['tools']}
        assert expected_native.issubset(view['native_tools'])
        name = 'hermbot-bot-' + bot['id']
        template = ('{"readonly":{{json .HostConfig.ReadonlyRootfs}},'
                    '"security":{{json .HostConfig.SecurityOpt}},'
                    '"health":{{json .State.Health.Status}},"mounts":{{json .Mounts}}}')
        detail = json.loads(command('docker', 'inspect', '--format', template, name))
        assert detail['readonly'] and 'no-new-privileges:true' in detail['security']
        assert detail['health'] == 'healthy'
        private = {m['Destination']: m['Name'] for m in detail['mounts'] if m['Type'] == 'volume'}
        assert private == before['volumes'][bot['id']]
        volumes.extend(private.values())
        assert json.loads(command('docker', 'exec', name, '/opt/hermes/.venv/bin/python', '-c', SOURCE)) == original
        protection = json.loads(command('docker', 'exec', '--user', 'hermes', name,
                                        '/opt/hermes/.venv/bin/python', '-c', PROTECTION))
        assert protection['uid'] != 0 and protection['source_read_only'] and protection['workspace_writable']
        assert protection['gateway_uids'] and all(uid == protection['uid'] for uid in protection['gateway_uids'])
        assert not protection['plugin_directory_writable'] and not protection['plugin_file_writable']
        assert not protection['docker_socket_present'] and int(protection['effective_capabilities'], 16) == 0
        assert protection['no_new_privileges'] == '1'
        runtimes.append({'bot_id': bot['id'], 'prior_revision': prior['revision'],
                         'current_revision': bot['revision'], **protection})
    assert len(volumes) == len(set(volumes)) == 9
    suites = ET.parse(ROOT / 'test-results/backend.xml').getroot().findall('testsuite')
    assert suites and all(int(s.attrib['failures']) == int(s.attrib['errors']) == 0 for s in suites)
    output = {'complete': True, 'audited_at': datetime.now(timezone.utc).isoformat(),
              'backend_tests': sum(int(s.attrib['tests']) for s in suites),
              'official_base': BASE, 'unchanged_upstream_python_files': len(original),
              'running_source_matches_official_base': True, 'runtimes': runtimes,
              'owner_groups_preserved': len(before['groups']), 'owner_configuration_preserved': True,
              'isolated_private_volumes': len(volumes), 'test_group_archived': True,
              'synthetic_slack_draft_denied_without_dispatch': True, 'external_changes': False}
    if handoff:
        output.update(connected_peer_handoff_verified=True, original_aborted_task_unchanged=True)
    (ROOT / 'test-results/work-outcomes-audit.json').write_text(json.dumps(output, indent=2) + '\n')
    print(f"Work outcomes audit passed: {len(original)} unchanged source files, "
          f"{len(runtimes)} protected runtimes, {len(volumes)} preserved volumes, "
          f"{len(before['groups'])} preserved groups, no external dispatch.")


if __name__ == '__main__':
    main()
