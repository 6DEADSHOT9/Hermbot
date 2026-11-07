"""Actual Hermes intent decisions and report revisions; no private provider actions."""
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

from backend.settings import settings

PROOF = Path('/app/test-results/work-items-native.json')


def main():
    client = httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=30)
    manifest = {'check': 'real model conversation/work routing', 'tasks': [], 'complete': False, 'external_changes': False}

    def save():
        PROOF.write_text(json.dumps(manifest, indent=2))

    def state():
        reply = client.get('/api/v1/state')
        reply.raise_for_status()
        return reply.json()

    reply = client.post('/api/v1/groups', json={'title': 'Work item acceptance ' + str(uuid4())[:8], 'bot_ids': ['researcher', 'writer']})
    reply.raise_for_status()
    manifest['conversation_id'] = reply.json()['id']
    save()
    try:
        steps = [
            ('discussion', "Let's discuss how a team might organize a short report. What are the tradeoffs between bullets and paragraphs? Just discuss; do not produce a report."),
            ('assignment', 'Create a short report about three ways to organize a small team. Publish it as team-organization.md. Use only these supplied ideas: clear ownership, short check-ins, and shared notes. Do not delegate or use any connected application, browser or terminal.'),
            ('discussion', 'Why did you choose those ideas? Explain your reasoning; do not change the report.'),
            ('update', 'Revise that team-organization report to add one example for each idea. Publish the revised report. Use only the supplied ideas and your own illustrative examples. Do not delegate or use connected applications, browser or terminal.'),
            ('status', 'How is that report going? Give me its current progress, without revising or restarting it.'),
            ('discussion', 'Thanks, that helps.'),
            ('assignment', 'Create a separate report listing two ways to welcome a new teammate. Publish welcoming.md using only these ideas: introduce their colleagues and share a starter checklist. Do not revise the previous report. Do not delegate or use connected applications, browser or terminal.'),
        ]
        first_item = None
        for expected, prompt in steps:
            key = str(uuid4())
            manifest['next_submission_key'] = key
            save()
            response = client.post('/api/v1/messages', headers={'Idempotency-Key': key}, json={'conversation_id': manifest['conversation_id'], 'bot_id': 'researcher', 'prompt': prompt})
            response.raise_for_status()
            task = response.json()
            record = {'task_id': task['id'], 'expected_intent': expected, 'request_key': key}
            manifest['tasks'].append(record)
            save()
            end = time.monotonic() + 240
            while time.monotonic() < end:
                s = state()
                t = next(t for t in s['tasks'] if t['id'] == task['id'])
                if t['status'] == 'completed':
                    break
                if t['status'] in ('failed', 'cancelled', 'interrupted', 'uncertain', 'waiting_approval'):
                    raise AssertionError('Acceptance response did not complete: ' + t['status'])
                time.sleep(1)
            else:
                raise AssertionError('Acceptance response timed out')
            record.update(intent=t['message_intent'], work_item_id=t['work_item_id'], status=t['status'])
            assert t['message_intent'] == expected, (expected, t['message_intent'])
            detail = client.get('/api/v1/tasks/'+t['id']).json()
            record['native_attempts'] = [e['id'] for e in detail['executions'] if e['status']=='completed']
            record['native_tools'] = [e['data'].get('tool') for e in detail['events'] if e['kind']=='tool.completed']
            assert record['native_attempts'] and 'hermbot_route_message' in record['native_tools']
            assert 'hermbot_connector' not in record['native_tools']
            if expected == 'discussion':
                assert t['work_item_id'] is None
                assert not set(record['native_tools']) & {'hermbot_publish', 'hermbot_delegate'}
            elif first_item is None:
                first_item = t['work_item_id']
            elif expected in ('update', 'status'):
                assert t['work_item_id'] == first_item
            else:
                assert t['work_item_id'] != first_item
            items = [w for w in s['work_items'] if w['conversation_id']==manifest['conversation_id'] and not w['hidden_at']]
            record['work_item_count'] = len(items)
            assert len(items) == (0 if len(manifest['tasks'])==1 else 2 if len(manifest['tasks'])==7 else 1)
            save()
            print('Native routing passed:', expected, 'work items:', len(items), flush=True)
        manifest['items'] = [{'id': w['id'], 'title': w['title'], 'status': w['status'], 'root_executions': len([t for t in w['executions'] if not t['parent_id']])} for w in items]
        assert sorted(w['root_executions'] for w in manifest['items']) == [1, 2]
        manifest['complete'] = True
        save()
    except Exception as error:
        manifest['failure'] = str(error)
        save()
        for record in manifest['tasks']:
            client.post('/api/v1/tasks/'+record['task_id']+'/stop')
        raise


if __name__ == '__main__':
    main()
