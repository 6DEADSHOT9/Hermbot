"""A lost canary submission must retry the identical reviewed draft, never new work."""
from uuid import uuid4
from types import SimpleNamespace

import pytest

from scripts import event_provider_acceptance as acceptance


@pytest.mark.parametrize(('provider', 'resource', 'text', 'expected'), [
    ('google_drive', 'canary', 'phase52 event test', False),
    ('google_drive', 'canary', 'phase52 Owner changed', True),
    ('google_drive', 'unrelated', 'phase52 Owner changed', False),
    ('gmail', 'canary', 'phase52 Owner changed', False),
    ('gmail', 'new-message', 'phase52 Owner changed', True),
    ('slack', 'canary', 'phase52 Owner changed', True),
])
def test_creation_echo_or_unrelated_report_is_not_external_acceptance(provider, resource, text, expected):
    assert acceptance.owner_change(provider, [{'resource': resource, 'text': text}], 'canary') is expected
    assert not acceptance.owner_change(provider, [], 'canary')


@pytest.mark.parametrize(('status', 'decision', 'task_status', 'accepted'), [
    ('pending', None, 'waiting_approval', False),
    ('succeeded', 'once', 'running', False),
    ('succeeded', 'once', 'completed', True),
])
def test_canary_requires_a_settled_human_approved_receipt(monkeypatch, status, decision, task_status, accepted):
    operation = {'status': status, 'decision': decision, 'payload_hash': 'frozen', 'approval_hash': 'frozen'}
    conn = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchall=lambda: [operation], fetchone=lambda: {'status': task_status}))
    monkeypatch.setattr(acceptance, 'verify_prepared_review', lambda *args: None)
    entry = {'draft_task_id': 'test', 'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Canary', 'body': 'Test'}}
    assert bool(acceptance.approved_operation(conn, 'github', entry)) is accepted
    operation['status'], operation['decision'] = 'succeeded', 'deny'
    with pytest.raises(AssertionError):
        acceptance.approved_operation(conn, 'github', entry)


def test_lost_submission_keeps_the_same_key_prompt_and_frozen_calendar_payload(tmp_path, monkeypatch):
    proof = {'marker': 'phase52-test', 'conversation_id': str(uuid4()), 'slack_channel': 'CTEST', 'cases': {
        'google_calendar': {'source_id': str(uuid4()), 'trigger_id': str(uuid4()), 'connection_id': str(uuid4()),
                            'bot_id': 'researcher', 'action': 'calendar.create'}}}
    monkeypatch.setattr(acceptance, 'PROOF', tmp_path/'acceptance.json')
    calls = []

    def api(path, body, method, *, key):
        calls.append((path, body, method, key))
        if len(calls) == 1:
            raise TimeoutError('Response lost after submission')
        return {'id': 'original-task'}

    monkeypatch.setattr(acceptance, 'api', api)
    with pytest.raises(TimeoutError):
        acceptance.prepare(proof)
    original = proof['cases']['google_calendar']['arguments'].copy()
    acceptance.prepare(proof)
    assert calls[0] == calls[1]
    assert proof['cases']['google_calendar']['arguments'] == original
    assert proof['cases']['google_calendar']['draft_task_id'] == 'original-task'
    acceptance.prepare(proof)
    assert len(calls) == 2
