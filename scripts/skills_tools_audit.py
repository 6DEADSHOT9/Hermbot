"""Read-only closure audit of skill/tool acceptance and the preserved local workspace."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent.parent
results = root / 'test-results'


def main():
    token = next(line.split('=', 1)[1].strip().strip('\"\'') for line in (root / '.env').read_text().splitlines() if line.startswith('OWNER_TOKEN='))

    def request(path, *, raw=False):
        req = urllib.request.Request('http://localhost:8780' + path, headers={'Authorization': 'Bearer ' + token})
        with urllib.request.urlopen(req, timeout=40) as response:
            return response.read() if raw else json.load(response)

    proof = json.loads((results / 'skills-tools-native.json').read_text())
    assert proof['complete'] and proof['real_native_model'] and not proof['external_changes']
    assert proof['fixture_archived'] and proof['original_skills_restored']
    assert all(skill['removed'] for skill in proof['skills'])
    assert proof['raw_skill_verification'] == {'native_wrapper_raw': True, 'marker_absent': True}
    state = request('/api/v1/state')
    assert state['provider_configured'] and not state['protocol_fixture']
    terminal = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}
    assert not any(t['status'] not in terminal for t in state['tasks'])
    groups = {c['id'] for c in state['conversations'] if c['kind'] == 'group'}
    assert set(proof['original_groups']).issubset(groups) and proof['conversation_id'] not in groups
    for bot_id, skills in proof['original_skills'].items():
        bot = next(b for b in state['bots'] if b['id'] == bot_id)
        assert bot['enabled_skills'] == skills and bot['status'] == 'active'
        assert bot['revision'] == bot['applied_revision'] and bot['readiness']['compatible']
        assert bot['readiness']['inventory']['version'] == 1
    for case in proof['tasks']:
        task = request('/api/v1/tasks/' + case['task_id'])
        assert task['status'] == case['status'] and task['message_intent'] == case['intent']
        assert all(run['status'] == 'completed' for run in task['executions'])
        if case['case'] == 'discussion':
            assert task['work_item_id'] is None and not task['execution_plan']
        else:
            assert task['execution_plan'] == case['preparation']

    handoff = next(t for t in proof['tasks'] if t['case'] == 'handoff')
    child_id = handoff['child']['task_id']
    child = request('/api/v1/tasks/' + child_id)
    assert child['status'] == 'completed' and child['attachment_ids'] == [proof['attachment_id']]
    assert child['handoff_context'] == handoff['child']['handoff']
    history = request('/api/v1/conversations/' + proof['conversation_id'] + '/runtime-history/' + child['bot_id'])['data']
    reads = {call['id'] for message in history for call in message.get('tool_calls') or []
             if call['function']['name'] == 'read_file' and proof['attachment_id'] in call['function']['arguments']}
    assert reads, 'The native recipient must read the explicitly staged attachment'
    facts = ('prototype ready', 'mira', 'friday')
    assert any(message.get('tool_call_id') in reads and all(f in json.dumps(message.get('content')).lower() for f in facts)
               for message in history), 'The actual native read result must contain all supplied facts'
    artifacts = [a for a in state['artifacts'] if a['task_id'] in {child_id, handoff['task_id']}]
    assert {a['task_id'] for a in artifacts} == {child_id, handoff['task_id']}
    verified = []
    for artifact in artifacts:
        value = request('/api/v1/artifacts/' + artifact['id'], raw=True)
        assert hashlib.sha256(value).hexdigest() == artifact['sha256']
        assert all(f in value.decode().lower() for f in facts)
        verified.append({'id': artifact['id'], 'task_id': artifact['task_id'], 'sha256': artifact['sha256']})
    ui = json.loads((results / 'skills-tools-ui.json').read_text())
    routing = json.loads((results / 'work-items-ui.json').read_text())
    assert ui['complete'] and not ui['manual_skill_or_context_controls'] and ui['script_errors'] == []
    assert all(not viewport['horizontal'] for viewport in ui['overflow'])
    assert routing['complete'] and routing['manual_context_controls_removed'] and routing['script_errors'] == []
    assert {turn['intent'] for turn in routing['automatic_routes']} == {'discussion', 'update', 'status'}
    suites = ET.parse(results / 'backend.xml').getroot().findall('testsuite')
    count = sum(int(s.attrib['tests']) for s in suites)
    assert count >= 265 and all(int(s.attrib['failures']) == int(s.attrib['errors']) == 0 for s in suites)
    infrastructure = json.loads((results / 'phase4-implementation-audit.json').read_text())
    assert infrastructure['implementation_checks_passed'] and infrastructure['healthy_runtime_count'] == len(state['bots'])
    assert infrastructure['isolated_private_volumes'] == len(state['bots']) * 3
    output = {'complete': True, 'audited_at': datetime.now(timezone.utc).isoformat(), 'backend_tests': count,
              'real_model_cases': len(proof['tasks']), 'actual_native_attachment_read_verified': True,
              'recipient_and_combined_report_facts_verified': verified, 'fixture_archived': True,
              'original_skills_and_groups_preserved': True, 'unfinished_work': 0,
              'healthy_runtimes': len(state['bots']), 'private_volumes': infrastructure['isolated_private_volumes'],
              'app_image': infrastructure['app_image'], 'runtime_image': infrastructure['runtime_image'],
              'phase4_reviewed_write_exit_gate': False,
              'evidence_sha256': {name: hashlib.sha256((results / name).read_bytes()).hexdigest() for name in
                                  ('backend.xml', 'skills-tools-native.json', 'skills-tools-ui.json', 'work-items-ui.json')}}
    (results / 'skills-tools-completion-audit.json').write_text(json.dumps(output, indent=2))
    print(f'Skills/tools closure passed: {count} backend checks, four real-model cases, actual attachment bytes/reports, responsive UI and preserved workspace.')


if __name__ == '__main__':
    main()
