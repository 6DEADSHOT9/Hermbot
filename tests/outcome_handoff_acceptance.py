"""Real manager-to-connected-helper draft acceptance; never approve or send the draft."""
import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/outcome-handoff-acceptance.json')
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
MEMBER = 'UHERMBOTTEST'
TEXT = 'Outcome handoff acceptance draft'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['start', 'status', 'clarify', 'check', 'cleanup'])
    mode = parser.parse_args().mode
    with httpx.Client(base_url='http://api:8000', timeout=30,
                      headers={'Authorization': 'Bearer ' + settings.owner_token}) as client:
        def call(method, path, body=None, key=None):
            response = client.request(method, path, json=body,
                                      headers={'Idempotency-Key': key or str(uuid4())} if method != 'GET' else {})
            response.raise_for_status()
            return response.json()

        def save():
            PROOF.write_text(json.dumps(proof, indent=2) + '\n')

        if mode == 'start':
            assert not PROOF.exists(), 'Use the saved acceptance; do not submit duplicates.'
            state = call('GET', '/api/v1/state')
            assert all(t['status'] in TERMINAL for t in state['tasks'])
            connections = call('GET', '/api/v1/connections')
            account = next(c for c in connections['connections'] if c['provider'] == 'slack'
                           and c['status'] == 'connected' and 'writer' in c['bot_ids']
                           and 'slack.post' in c['actions'])
            assert not any(c['provider'] == 'slack' and c['status'] == 'connected'
                           and 'reviewer' in c['bot_ids'] for c in connections['connections'])
            assert not any(p['connection_id'] == account['id'] and p['resource'] in (MEMBER, '*')
                           for p in connections['permissions'])
            identity = uuid4().hex[:8]
            proof = {'complete': False, 'external_changes': False, 'identity': identity,
                     'original_groups': [c['id'] for c in state['conversations'] if c['kind'] == 'group'],
                     'submission_key': str(uuid4()), 'title': 'Outcome handoff acceptance ' + identity}
            save()
            group = call('POST', '/api/v1/groups', {'title': proof['title'], 'bot_ids': ['reviewer', 'writer']})
            proof['conversation_id'] = group['id']
            save()
            prompt = (f'I want a Slack DM to member {MEMBER} with the exact text "{TEXT}". '
                      'Coordinate with Quill, who has Slack access, to prepare the exact message for my review. '
                      'Do not send it, search Slack, or look up recipients. End the turn and wait for my review.')
            task = call('POST', '/api/v1/messages', {'bot_id': 'reviewer', 'conversation_id': group['id'],
                        'prompt': prompt, 'managed_bot_ids': ['writer']}, proof['submission_key'])
            proof['task_id'] = task['id']
            save()
        else:
            proof = json.loads(PROOF.read_text())

        state = call('GET', '/api/v1/state')
        tasks = [t for t in state['tasks'] if t['conversation_id'] == proof['conversation_id']]
        print(json.dumps({'tasks': [{'bot': t['bot_id'], 'status': t['status'], 'error': t.get('error')}
                                  for t in tasks]}, indent=2))
        if mode == 'clarify' and not proof.get('clarified'):
            question = next(q for q in state['input_requests'] if q['task_id'] == proof['task_id'])
            answer = (f'Have Quill request my approval to send the Slack DM through the application\'s '
                      f'exact Slack action review, using its own Slack connection. The exact message is "{TEXT}" '
                      f'and the recipient remains {MEMBER}. I am not approving a send or requesting another '
                      'wording question. Do not send anything; Quill must submit the actual app action for review.')
            call('POST', '/api/v1/input-requests/' + question['id'] + '/answer',
                 {'expected_question_hash': question['question_hash'], 'answer': answer})
            proof.update(clarified=True, draft_review_question=question['prompt'],
                         clarification_answer=answer)
            save()
            print('Clarified the wording-review question: request the exact app action approval, never send.')
        if mode == 'check' and not proof.get('checked'):
            review = next(a for a in state['approvals'] if a['status'] == 'pending'
                          and a['kind'] == 'connector' and a['task_id'] in {t['id'] for t in tasks})
            action = review['payload']['action']
            assert action['action'] == 'slack.post' and action['arguments']['channel'] == MEMBER
            assert action['arguments']['text'].rstrip('.') == TEXT
            helper = next(t for t in tasks if t['id'] == review['task_id'])
            assert helper['bot_id'] == 'writer' and helper['parent_id'] == proof['task_id']
            item = next(w for w in state['work_items'] if w['id'] == helper['work_item_id'])
            result = next(o for o in item['outcomes'] if o['id'] == action['outcome_id'])
            assert result['source_task_id'] == proof['task_id'] and result['kind'] == 'connector_effect'
            assert result['revision'] == action['outcome_revision'] and item['status'] == 'needs_approval'
            assert not any(q['work_item_id'] == item['id'] for q in state['input_requests'])
            operations = call('GET', '/api/v1/connections')['operations']
            operation = next(o for o in operations if o['approval_id'] == review['id'])
            assert operation['status'] == 'pending' and operation['result'] is None
            call('POST', '/api/v1/approvals/' + review['id'],
                 {'decision': 'deny', 'expected_payload_hash': review['payload_hash']})
            call('POST', '/api/v1/tasks/' + proof['task_id'] + '/stop')
            proof.update(checked=True, approval_id=review['id'], operation_id=operation['id'],
                         outcome_id=result['id'], manager_registered=True, helper_received_result=True,
                         member_id_accepted=True, owner_internal_id_question=False,
                         exact_review_denied_without_dispatch=True)
            save()
            print('Manager registered the result; connected helper prepared its exact bound draft; denied without dispatch.')
        if mode == 'cleanup':
            assert proof.get('checked') and all(t['status'] in TERMINAL for t in tasks)
            operation = next(o for o in call('GET', '/api/v1/connections')['operations']
                             if o['id'] == proof['operation_id'])
            assert operation['status'] == 'denied' and not operation['result']
            group = next(c for c in state['conversations'] if c['id'] == proof['conversation_id'])
            assert group['title'] == proof['title']
            call('DELETE', '/api/v1/groups/' + group['id'])
            groups = [c['id'] for c in call('GET', '/api/v1/state')['conversations'] if c['kind'] == 'group']
            assert set(groups) == set(proof['original_groups'])
            proof.update(complete=True, fixture_archived=True, original_groups_preserved=True)
            save()
            print('Only the settled acceptance group was archived; owner groups preserved.')


if __name__ == '__main__':
    main()
