"""Real model checks of outcomes, answers and helpers with no external account changes."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/work-outcomes-acceptance.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'uncertain'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['start', 'status', 'answer', 'verify', 'draft', 'check-draft', 'cleanup'])
    mode = parser.parse_args().mode
    client = httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer '+settings.owner_token}, timeout=60)

    def call(method, path, body=None, key=None):
        result = client.request(method, path, json=body, headers={'Idempotency-Key': key or str(uuid4())} if method != 'GET' else {})
        result.raise_for_status()
        return result.json()

    if mode == 'start':
        assert not PROOF.exists(), 'Continue the recorded acceptance; do not submit duplicates.'
        state = call('GET', '/api/v1/state')
        assert all(t['status'] in TERMINAL for t in state['tasks'])
        assert not state['protocol_fixture'] and state['provider_configured']
        proof = {'complete': False, 'external_changes': False, 'identity': uuid4().hex[:8], 'cases': []}
        PROOF.write_text(json.dumps(proof, indent=2))
        group = call('POST', '/api/v1/groups', {'title': 'Work outcomes acceptance '+proof['identity'], 'bot_ids': ['researcher', 'writer', 'reviewer']})
        proof['conversation_id'] = group['id']
        for name, bot, prompt, manager in [
            ('single', 'researcher', 'Do this yourself in this workspace: make a short inventory table with apples quantity 2 and bananas quantity 3, including total 5. Before publishing the final table, ask me which heading to use: Office or Home. Wait for my answer and publish the final table as a file. Do not use or change any connected account.', False),
            ('manager', 'reviewer', 'Coordinate Quill to create a short inventory table with pencils quantity 4 and pens quantity 6, including total 10. Have Quill publish the table as a file. Then check the contents and bring me the final artifact. Stay inside this workspace and do not use or change connected accounts.', True),
        ]:
            case = {'case': name, 'bot_id': bot, 'prompt': prompt, 'submission_key': str(uuid4())}
            proof['cases'].append(case)
            PROOF.write_text(json.dumps(proof, indent=2))
            body = {'bot_id': bot, 'conversation_id': group['id'], 'prompt': prompt}
            if manager:
                body['managed_bot_ids'] = ['writer']
            task = call('POST', '/api/v1/messages', body, case['submission_key'])
            case['task_id'] = task['id']
            PROOF.write_text(json.dumps(proof, indent=2))
    else:
        proof = json.loads(PROOF.read_text())
    state = call('GET', '/api/v1/state')
    items = [i for i in state['work_items'] if i['conversation_id'] == proof['conversation_id'] and any(t['id'] in {c['task_id'] for c in proof['cases']} for t in i['executions'])]
    questions = [q for q in state['input_requests'] if q['work_item_id'] in {i['id'] for i in items}]
    if mode == 'answer':
        for question in questions:
            if question['answer_type'] == 'text':
                answer = 'Office'
            else:
                answer = next(v for v in question['choices'] if 'Office' in v)
            result = call('POST', '/api/v1/input-requests/'+question['id']+'/answer', {'expected_question_hash': question['question_hash'], 'answer': answer})
            proof.setdefault('answers', []).append({'question_id': question['id'], 'answer': answer, 'result': result})
            PROOF.write_text(json.dumps(proof, indent=2))
    if mode == 'verify':
        assert len(items) == 2 and all(i['status'] == 'completed' for i in items), 'Every required result must be fulfilled.'
        assert all(i['outcomes'] for i in items) and proof.get('answers')
        assert any(t['parent_id'] for i in items for t in i['executions']), 'Manager must have a real helper.'
        assert not [a for a in state['approvals'] if a['task_id'] in {t['id'] for i in items for t in i['executions']} and a['kind'] == 'connector']
        for item in items:
            case = next(c for c in proof['cases'] if c['task_id'] in {t['id'] for t in item['executions']})
            contents = []
            for output in item['outputs']:
                artifact = call('GET', '/api/v1/library/'+output['id'])
                contents.append(artifact.get('preview', {}).get('text', '') if isinstance(artifact.get('preview'), dict) else '')
                raw = client.get('/api/v1/artifacts/'+output['id']+'?download=true')
                raw.raise_for_status()
                contents.append(raw.text)
            text = '\n'.join(contents)
            assert ('Office' in text and 'apples' in text.lower() and '5' in text) if case['case'] == 'single' else ('pencils' in text.lower() and '10' in text)
        proof['complete'] = True
        proof['items'] = [{'id':i['id'], 'status':i['status'], 'progress':i['progress'], 'outcomes':[{k:o[k] for k in ('id','title','kind','status','evidence_strength')} for o in i['outcomes']]} for i in items]
    if mode == 'draft':
        assert proof['complete'] and not proof.get('draft_task')
        assert all(t['status'] in TERMINAL for t in state['tasks'])
        connected = call('GET', '/api/v1/connections')
        assert any(c['provider']=='slack' and c['status']=='connected' and 'writer' in c['bot_ids'] and 'slack.post' in c['actions'] and not c['resources'].get('slack.post') for c in connected['connections'])
        assert not any(p['resource']=='UHERMBOTTEST' and p['action']=='slack.post' for p in connected['permissions'])
        proof['draft_key'] = str(uuid4())
        PROOF.write_text(json.dumps(proof, indent=2))
        task = call('POST', '/api/v1/messages', {'bot_id':'writer','conversation_id':proof['conversation_id'],
            'prompt':'Prepare an exact Slack DM draft to the supplied workspace member ID UHERMBOTTEST. The text must be: Hermbot outcome acceptance draft. This ID is a synthetic test target, so do not send it or look up other people. Put the draft into owner review and wait. Do not ask for an existing DM channel ID.'}, proof['draft_key'])
        proof['draft_task'] = task['id']
    if mode == 'check-draft':
        approval = next(a for a in state['approvals'] if a['task_id']==proof['draft_task'] and a['status']=='pending' and a['kind']=='connector')
        exact = approval['payload']['action']
        assert exact['action']=='slack.post' and exact['arguments']['channel']=='UHERMBOTTEST'
        assert exact['arguments']['text'] in ('Hermbot outcome acceptance draft.', 'Hermbot outcome acceptance draft') and exact.get('outcome_id')
        connected = call('GET', '/api/v1/connections')
        operation = next(o for o in connected['operations'] if o['approval_id']==approval['id'])
        assert operation['status']=='pending' and not operation['result']
        call('POST', '/api/v1/approvals/'+approval['id'], {'decision':'deny','expected_payload_hash':approval['payload_hash']})
        call('POST', '/api/v1/tasks/'+proof['draft_task']+'/stop', {})
        proof['draft_prepared'] = {'approval_id':approval['id'],'operation_id':operation['id'],'member_id_accepted':True,'outcome_bound':True,'denied_without_dispatch':True}
    if mode == 'cleanup':
        assert proof['complete']
        group = next(c for c in state['conversations'] if c['id'] == proof['conversation_id'])
        assert group['title'] == 'Work outcomes acceptance '+proof['identity']
        assert all(t['status'] in TERMINAL for t in state['tasks'] if t['conversation_id'] == group['id'])
        call('DELETE', '/api/v1/groups/'+group['id'])
        proof['fixture_archived'] = True
    PROOF.write_text(json.dumps(proof, indent=2))
    print(json.dumps({'complete':proof['complete'], 'items':[{'status':i['status'],'progress':i['progress']} for i in items], 'pending_questions':[{'id':q['id'],'answer_type':q['answer_type']} for q in questions]}))


if __name__ == '__main__':
    main()
