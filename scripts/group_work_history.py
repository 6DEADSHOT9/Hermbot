"""Preview and reversibly regroup the reviewed article conversation; never run old tasks."""
import argparse
import json
from pathlib import Path

import httpx

from backend import service
from backend.settings import settings

CONVERSATION = 'eddb53bc-b44c-4d24-b626-60441900c4dc'
BLOG = '57b9f09f-1b8e-41f4-b4ff-fd73d864c0d1'
DELIVERY = 'fceee70a-ba55-482f-965a-d4a78ae0fc2b'
ANTHROPIC = '9df6847f-11b9-4213-9610-27ae9ff8e5f2'
DECISIONS = [
    (BLOG, 'assignment', None, 'Dawn of AI article'),
    ('ee7557ab-3b48-438f-a3d5-77c3c9b1ea21', 'update', BLOG, ''),
    ('c3d17618-3332-4614-b7ca-48f07e1e774a', 'update', BLOG, ''),
    ('3207ee60-34e7-405c-9546-db89486a8d6f', 'status', BLOG, ''),
    ('497724e2-35f3-47b5-bfcb-b17abb26655a', 'discussion', BLOG, ''),
    ('8e19db27-c1b6-47a6-a6f1-97fee460c563', 'update', BLOG, ''),
    (DELIVERY, 'assignment', None, 'Share the Dawn of AI article in Slack'),
    ('ff948811-5eaf-4575-9d8f-8b3f0aea634f', 'update', DELIVERY, ''),
    ('51f58d52-4d05-45b1-95b8-055278b8ad53', 'update', DELIVERY, ''),
    ('51997531-5cba-410d-9d3c-dcaea452f383', 'update', DELIVERY, ''),
    ('34141fed-98c7-43d8-97e7-e6b19d113137', 'update', DELIVERY, ''),
    (ANTHROPIC, 'assignment', None, 'Anthropic valuation article and Slack delivery'),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    options = parser.parse_args()
    path = Path('/app/test-results/work-items-history.json')
    client = httpx.Client(base_url='http://api:8000', headers={'Authorization': 'Bearer ' + settings.owner_token}, timeout=30)

    def get(path):
        result = client.get(path)
        result.raise_for_status()
        return result.json()

    state = get('/api/v1/state')
    roots = {t['id']: t for t in state['tasks'] if t['conversation_id']==CONVERSATION and not t['parent_id']}
    if path.exists() and json.loads(path.read_text()).get('applied'):
        print('Reviewed history grouping was already applied; no changes made.')
        return
    assert {task for task, _, _, _ in DECISIONS}.issubset(roots)
    assert all(roots[t]['status'] in service.TERMINAL-{'uncertain'} for t, _, _, _ in DECISIONS)
    chat = get('/api/v1/conversations/'+CONVERSATION)
    manifest = {'conversation_id': CONVERSATION, 'method': 'reviewed conversation semantics; metadata only', 'applied': False,
                'before': [{'task_id': t['id'], 'work_item_id': t['work_item_id'], 'message_intent': t['message_intent'], 'root_id': t['root_id'], 'grant_id': t['grant_id'], 'status': t['status'], 'attempts': t['attempts'], 'input_hash': t['input_hash']} for t in roots.values()],
                'message_digest': service.digest(chat['messages']), 'decisions': [], 'after': None}
    for task_id, intent, target, title in DECISIONS:
        body = {'task_id': task_id, 'intent': intent, 'title': title}
        if target:
            body['work_item_id'] = roots[target]['work_item_id']
        manifest['decisions'].append(body)
    path.write_text(json.dumps(manifest, indent=2))
    print('Reviewed preview: 12 previous turns become 3 assignments; discussion/status stay linked without new work.')
    if not options.apply:
        return
    for body in manifest['decisions']:
        response = client.post('/api/v1/work-items/messages', json=body)
        response.raise_for_status()
    after = get('/api/v1/state')
    after_roots = {t['id']: t for t in after['tasks'] if t['id'] in roots}
    for previous in manifest['before']:
        for field in ('root_id', 'grant_id', 'status', 'attempts', 'input_hash'):
            assert after_roots[previous['task_id']][field] == previous[field]
    new_chat = get('/api/v1/conversations/'+CONVERSATION)
    assert [(m['id'],m['content']) for m in chat['messages']] == [(m['id'],m['content']) for m in new_chat['messages']]
    visible = [w for w in after['work_items'] if w['conversation_id']==CONVERSATION and not w['hidden_at'] and w['current_task']]
    assert len(visible) == 3
    manifest['after'] = [{'id': w['id'], 'title': w['title'], 'status': w['status']} for w in visible]
    manifest.update(applied=True, execution_unchanged=True, message_content_unchanged=True, external_changes=False)
    path.write_text(json.dumps(manifest, indent=2))
    print('History grouping applied: 3 assignments; execution and conversation content preserved.')


if __name__ == '__main__':
    main()
