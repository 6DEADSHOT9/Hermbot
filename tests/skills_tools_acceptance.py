"""Real model preparation and explicitly shared handoffs, without provider actions."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/skills-tools-native.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}
EMPTY = hashlib.sha256(b'').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['start', 'continue', 'verify', 'cleanup'])
    mode = parser.parse_args().mode
    client = httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=90)

    def call(method, path, body=None, key=None):
        response = client.request(method, path, json=body, headers={'Idempotency-Key': key or str(uuid4())} if method in ('POST', 'PUT') else {})
        response.raise_for_status()
        return response.json()

    def state():
        return call('GET', '/api/v1/state')

    def save():
        PROOF.write_text(json.dumps(manifest, indent=2))

    def until(test, timeout=240):
        deadline = time.monotonic()+timeout
        while time.monotonic()<deadline:
            current = state()
            if test(current):
                return current
            time.sleep(1)
        raise AssertionError('Native acceptance did not reach the expected state')

    def ready(bot_id):
        return until(lambda s: any(b['id']==bot_id and b['status']=='active' and b['revision']==b['applied_revision'] and b['readiness'].get('compatible') for b in s['bots']))

    if mode=='start':
        if PROOF.exists():
            previous = json.loads(PROOF.read_text())
            assert previous.get('fixture_archived'), 'Recover or clean the recorded fixture before starting another run'
        snapshot = state()
        assert snapshot['provider_configured'] and not snapshot['protocol_fixture']
        assert not any(t['status'] not in TERMINAL for t in snapshot['tasks'])
        identity = str(uuid4())[:8]
        manifest = {'complete': False, 'check': 'real automatic skill preparation and effective handoff capabilities', 'external_changes': False,
                    'run_id': identity, 'tasks': [], 'skills': [], 'original_skills': {b['id']: b['enabled_skills'] for b in snapshot['bots']},
                    'original_groups': [c['id'] for c in snapshot['conversations'] if c['kind']=='group']}
        save()
        group = call('POST', '/api/v1/groups', {'title': 'Skills and tools acceptance '+identity, 'bot_ids': ['researcher', 'reviewer']})
        manifest['conversation_id'] = group['id']
        save()
        recipes = [
            ('researcher', 'brief', 'Create a concise project brief from supplied project notes, with three bullets for progress, owner and next step.', ['hermbot_publish'],
             'Use the supplied facts only. Publish a Markdown brief headed Project brief with exactly three bullets labelled Progress, Owner and Next step. End with Prepared from supplied notes.'),
            ('researcher', 'invoice', 'Prepare an invoice from billing rates and amounts owed.', [], 'For invoice requests only, check billing amounts and produce an invoice.'),
            ('researcher', 'local-code', 'Run a requested local Python calculation and verify its output using the terminal.', ['terminal'], 'For local Python execution, run the requested code and verify the actual process output. Stop if terminal access is unavailable.'),
            ('reviewer', 'notes', 'Read a supplied project-notes attachment and summarize progress, owner and next step as a durable report.', ['read_file', 'hermbot_publish'],
             'Read the explicitly granted attachment using read_file. Publish a concise Markdown report containing its progress, owner and next step. Preserve supplied names and dates. Never infer extra facts.'),
        ]
        for bot_id, kind, description, required, procedure in recipes:
            name = 'hermbot-'+kind+'-'+identity
            inline_path = '/workspace/skill-inline-'+identity
            content = '---\nname: '+name+'\ndescription: '+description+'\nmetadata:\n  hermbot:\n    required_tools: '+json.dumps(required)+'\n---\n\n'+procedure+'\n'
            if kind=='brief':
                content += '\nLiteral inline command example; do not execute it: !`printf unexpected > '+inline_path+'`\n'
                manifest['inline_path'] = inline_path
            record = {'bot_id': bot_id, 'name': name, 'kind': kind, 'sha256': hashlib.sha256(content.encode()).hexdigest()}
            manifest['skills'].append(record)
            save()
            call('PUT', f'/api/v1/bots/{bot_id}/knowledge/skill/{name}', {'content': content, 'expected_hash': EMPTY, 'enable': True})
            record['installed'] = True
            save()
            ready(bot_id)
        notes = 'Progress: Prototype ready\nOwner: Mira\nNext step: Review on Friday\n'
        asset = call('POST', '/api/v1/conversations/'+manifest['conversation_id']+'/attachments', {'name': 'project-notes.txt', 'content_type': 'text/plain', 'content_base64': base64.b64encode(notes.encode()).decode()})
        manifest['attachment_id'] = asset['id']
        manifest['attachment_sha256'] = hashlib.sha256(notes.encode()).hexdigest()
        save()
    else:
        manifest = json.loads(PROOF.read_text())

    if mode=='cleanup':
        for task in manifest['tasks']:
            if task.get('task_id'):
                detail = call('GET', '/api/v1/tasks/'+task['task_id'])
                if detail['status'] not in TERMINAL:
                    call('POST', '/api/v1/tasks/'+task['task_id']+'/stop', {})
        until(lambda s: all(t['status'] in TERMINAL for t in s['tasks'] if t['conversation_id']==manifest['conversation_id']))
        for skill in manifest['skills']:
            if not skill.get('removed'):
                bot_id, name = skill['bot_id'], skill['name']
                ready(bot_id)
                value = call('GET', f'/api/v1/bots/{bot_id}/knowledge/skills/{name}')
                if value['exists']:
                    call('PUT', f'/api/v1/bots/{bot_id}/knowledge/skill/{name}', {'content': '', 'expected_hash': value['sha256'], 'enable': False})
                    ready(bot_id)
                skill['removed'] = True
                save()
        call('DELETE', '/api/v1/groups/'+manifest['conversation_id'])
        current = state()
        assert not any(c['id']==manifest['conversation_id'] for c in current['conversations'])
        for bot_id, original in manifest['original_skills'].items():
            assert next(b for b in current['bots'] if b['id']==bot_id)['enabled_skills']==original
        assert set(manifest['original_groups']).issubset({c['id'] for c in current['conversations']})
        manifest['fixture_archived'] = True
        manifest['original_skills_restored'] = True
        save()
        print('Acceptance fixture archived; temporary procedures removed and original skill selections restored.', flush=True)
        return

    brief = next(s['name'] for s in manifest['skills'] if s['kind']=='brief')
    notes_skill = next(s['name'] for s in manifest['skills'] if s['kind']=='notes')
    invoice = next(s['name'] for s in manifest['skills'] if s['kind']=='invoice')
    cases = [
        ('brief', 'completed', 'Create a concise project brief using only these notes: prototype ready, owner Mira, review on Friday. Consult relevant enabled procedures automatically, and also read the chosen procedure once with skill_view. Publish the brief as project-brief.md. Do not delegate or use any connected application.'),
        ('discussion', 'completed', 'Thanks for the project brief. No changes needed.'),
        ('missing-access', 'failed', 'Run a local Python script that calculates 17 plus 25 and verify the actual process output. Automatically prepare the required local execution tools and relevant enabled procedure first. If the required local tool is unavailable, report the exact missing capability and finish without doing substitutes or creating a review. Do not use any connected application.'),
        ('handoff', 'completed', 'Ask Sage (reviewer) to summarize the attached project-notes.txt file. Delegate exactly one assignment with the explicitly shared attachment and a clear expected_output brief. Sage should read the file in its own workspace, consult its relevant enabled procedure, and publish a concise report containing progress, owner and next step. Yield for the actual report, then read its granted output and publish your combined report as combined-project-notes.md. Do not use connected apps, native commands or browser tools. Use only the supplied notes.'),
    ]
    try:
        for label, expected, prompt in cases:
            record = next((r for r in manifest['tasks'] if r['case']==label), None)
            if record is None:
                assert mode!='verify'
                record = {'case': label, 'request_key': str(uuid4())}
                manifest['tasks'].append(record)
                save()
            if not record.get('task_id'):
                recovered = next((t for t in state()['tasks'] if t.get('request_key')==record['request_key']), None)
                if recovered:
                    record['task_id'] = recovered['id']
                else:
                    assert mode!='verify'
                    payload = {'conversation_id': manifest['conversation_id'], 'bot_id': 'researcher', 'prompt': prompt}
                    if label=='handoff':
                        payload.update(attachment_ids=[manifest['attachment_id']], managed_bot_ids=['reviewer'])
                    result = call('POST', '/api/v1/messages', payload, record['request_key'])
                    record['task_id'] = result['id']
                save()
            until(lambda s: any(t['id']==record['task_id'] and t['status'] in TERMINAL for t in s['tasks']), timeout=420)
            detail = call('GET', '/api/v1/tasks/'+record['task_id'])
            assert detail['status']==expected, label+' ended '+detail['status']
            assert detail['executions'] and all(e['status']=='completed' for e in detail['executions'])
            tools = [e['data'].get('tool') for e in detail['events'] if e['kind']=='tool.completed']
            visibility = [e['data']['tools'] for e in detail['events'] if e['kind']=='capabilities.visible']
            record.update(status=detail['status'], intent=detail['message_intent'], native_tools=tools, advertised_tools=visibility,
                          preparation=detail['execution_plan'], work_item_id=detail['work_item_id'])
            assert visibility and not any('terminal' in names or 'browser_navigate' in names or 'write_file' in names for names in visibility)
            assert 'hermbot_connector' not in tools
            if label=='discussion':
                assert detail['message_intent']=='discussion' and not detail['work_item_id']
                assert 'hermbot_prepare' not in tools
            else:
                assert 'hermbot_prepare' in tools
                if expected=='completed':
                    assert detail['execution_plan']['ready']
            if label=='brief':
                names = {s['name'] for s in detail['execution_plan']['skills']}
                assert brief in names and invoice not in names
                assert 'skill_view' in tools
                assets = [a for a in state()['artifacts'] if a['task_id']==detail['id']]
                assert assets
                content = '\n'.join(client.get('/api/v1/artifacts/'+a['id']).text for a in assets)
                assert 'Prepared from supplied notes' in content
                record['artifact_ids'] = [a['id'] for a in assets]
            elif label=='missing-access':
                assert any(b['name']=='terminal' for b in detail['execution_plan']['blockers'])
                assert 'terminal' not in tools and 'hermbot_publish' not in tools
            elif label=='handoff':
                assert len(detail['children'])==1
                child = call('GET', '/api/v1/tasks/'+detail['children'][0]['id'])
                assert child['status']=='completed' and child['attachment_ids']==[manifest['attachment_id']]
                assert child['handoff_context']['expected_output']
                assert notes_skill in {s['name'] for s in child['execution_plan']['skills']}
                child_tools = [e['data'].get('tool') for e in child['events'] if e['kind']=='tool.completed']
                assert 'read_file' in child_tools and 'hermbot_prepare' in child_tools
                assert 'hermbot_read_artifact' in tools
                assert detail['attempts']>=2
                child_visibility = [e['data']['tools'] for e in child['events'] if e['kind']=='capabilities.visible']
                assert not any('terminal' in names for names in child_visibility)
                record['child'] = {'task_id': child['id'], 'native_tools': child_tools, 'preparation': child['execution_plan'], 'handoff': child['handoff_context'], 'advertised_tools': child_visibility}
            save()
            print('Native capability acceptance passed:', label, flush=True)
        current = state()
        items = [w for w in current['work_items'] if w['conversation_id']==manifest['conversation_id'] and not w['hidden_at']]
        assert len(items)==3
        manifest['work_items'] = [{'id': w['id'], 'status': w['status']} for w in items]
        manifest['complete'] = True
        manifest['real_native_model'] = True
        save()
        print('All native skill/tool checks passed. Fixture remains only for browser and raw-skill validation; cleanup is required.', flush=True)
    except Exception as error:
        manifest['failure'] = str(error)
        save()
        raise


if __name__=='__main__':
    main()
