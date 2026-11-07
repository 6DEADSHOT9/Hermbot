"""Acceptance verifier integrity; synthetic evidence here never counts as provider acceptance."""
import json
from uuid import uuid4

import pytest

from backend import service
from backend.models import ConnectorArgs
from tests.phase4_read_acceptance import verify_reads, retry_read_manifest
from tests.phase4_review_acceptance import verify_operation, ready_connection, verify_prepared_review
from tests.phase4_google_slack_acceptance import selected_resources


def read_evidence():
    child = {'id': str(uuid4()), 'bot_id': 'writer'}
    connection = str(uuid4())
    arguments = {'connection_id': connection, 'action': 'github.repository', 'arguments': {'repo': 'test/repo'}}
    call = {'id': 'native-call', 'function': {'name': 'hermbot_connector', 'arguments': json.dumps(arguments)}}
    receipt = {'id': f"writer:{child['id']}:native-call", 'request_hash': service.tool_request_hash('connector', ConnectorArgs.model_validate(arguments).model_dump()), 'response': {'result': {'id': 42}}}
    return [receipt], [child], [call], {'github': connection}, {'github': ('writer', set())}, {'github.repository': {'repo': 'test/repo'}}


def test_read_verifier_accepts_backend_receipt_without_weakening_argument_identity(make_task, monkeypatch):
    from backend import connector_providers, db, worker
    from tests.test_phase4 import connection
    account = connection(bots=['writer'])
    task = make_task('writer')
    attempt = worker.claim_new()[0]
    arguments = {'connection_id': str(account['id']), 'action': 'github.repository', 'arguments': {'repo': 'test/repo'}}
    normalized = ConnectorArgs.model_validate(arguments).model_dump()
    calls = []

    def execute(*args):
        calls.append(args)
        return {'id': 42}

    monkeypatch.setattr(connector_providers, 'execute', execute)
    with db.connection() as conn:
        result = service.tool_action(conn, 'writer', attempt['session_id'], 'connector', normalized, 'native-call')
        assert service.tool_action(conn, 'writer', attempt['session_id'], 'connector', normalized, 'native-call') == result
        receipt = conn.execute('SELECT id,request_hash,response FROM tool_receipts WHERE task_id=%s', (task['id'],)).fetchone()
    assert len(calls) == 1
    legacy_arguments = {k: normalized[k] for k in ('connection_id', 'action', 'arguments', 'summary')}
    assert receipt['request_hash'] == service.digest({'operation': 'connector', 'args': legacy_arguments})
    child = {'id': str(task['id']), 'bot_id': 'writer'}
    call = {'id': 'native-call', 'function': {'name': 'hermbot_connector', 'arguments': json.dumps(arguments)}}
    rest = ({'github': str(account['id'])}, {'github': ('writer', set())}, {'github.repository': {'repo': 'test/repo'}})
    assert verify_reads([receipt], [child], [call], *rest)[0][0] == 'github.repository'
    for field, value in (('connection', 'different account'), ('outcome_key', 'different_result'), ('outcome_id', str(uuid4()))):
        call['function']['arguments'] = json.dumps({**arguments, field: value})
        with pytest.raises(AssertionError, match='Native call must match'):
            verify_reads([receipt], [child], [call], *rest)


@pytest.mark.parametrize('bad', ['missing_action', 'wrong_resource', 'mismatched_call', 'unreceipted_call', 'error', 'review'])
def test_read_verifier_rejects_incomplete_or_unrelated_success(bad):
    values = read_evidence()
    assert verify_reads(*values)[0][0] == 'github.repository'
    receipts, children, calls, chosen, expected, required = values
    if bad == 'missing_action':
        required['github.issues'] = {'repo': 'test/repo', 'limit': 2}
    elif bad == 'wrong_resource':
        required['github.repository']['repo'] = 'other/repo'
    elif bad == 'mismatched_call':
        calls[0]['function']['arguments'] = calls[0]['function']['arguments'].replace('test/repo', 'other/repo')
    elif bad == 'unreceipted_call':
        calls.clear()
    elif bad == 'error':
        receipts[0]['response'] = {'error': 'Provider rejected the credentials'}
    else:
        receipts[0]['response'] = {'approval_id': str(uuid4()), 'status': 'waiting_approval'}
    with pytest.raises(AssertionError):
        verify_reads(receipts, children, calls, chosen, expected, required)


def test_private_review_readiness_cannot_use_standing_permission():
    identity = str(uuid4())
    connection = {'id': identity, 'provider': 'github', 'status': 'connected', 'bot_ids': ['writer'], 'actions': ['github.create_issue'], 'resources': {'github.create_issue': ['test/repo']}}
    data = {'connections': [connection], 'permissions': []}
    assert ready_connection(data, 'writer', 'github', 'github.create_issue', 'test/repo')['id'] == identity
    data['permissions'] = [{'connection_id': identity, 'bot_id': 'writer', 'action': 'github.create_issue', 'resource': 'test/repo', 'revoked_at': None, 'expires_at': '2099-01-01T00:00:00+00:00', 'used': 0, 'max_uses': 1}]
    with pytest.raises(AssertionError, match='standing permission'):
        ready_connection(data, 'writer', 'github', 'github.create_issue', 'test/repo')


@pytest.mark.parametrize('bad', ['uncertain', 'wrong_approval', 'empty_receipt', 'no_continuation'])
def test_review_verifier_rejects_unproven_provider_outcomes(bad):
    payload = {'connection_id': str(uuid4()), 'connection_revision': 1, 'action': 'github.create_issue', 'arguments': {'repo': 'test/repo', 'title': 'review', 'body': 'review'}}
    manifest = {**payload, 'provider': 'github', 'bot_id': 'writer', 'conversation_id': str(uuid4())}
    task = {'status': 'completed', 'bot_id': 'writer', 'conversation_id': manifest['conversation_id'], 'executions': [{'status': 'completed'}, {'status': 'completed'}], 'output': 'PHASE4_REVIEW_RECEIPT'}
    approval = {'id': str(uuid4()), 'kind': 'connector', 'status': 'decided', 'decision': 'once', 'payload': {'action': payload}, 'payload_hash': service.digest(payload)}
    op = {**payload, 'id': str(uuid4()), 'status': 'succeeded', 'bot_id': 'writer', 'approval_id': approval['id'], 'permission_id': None, 'payload_hash': approval['payload_hash'], 'result': {'number': 42, 'html_url': 'https://github.com/test/repo/issues/42'}, 'error': None}
    assert verify_operation(task, [op], [approval], manifest)['outcome'] == 'succeeded'
    if bad == 'uncertain':
        op['status'] = 'uncertain'
    elif bad == 'wrong_approval':
        approval['payload_hash'] = 'not-the-reviewed-action'
    elif bad == 'empty_receipt':
        op['result'] = {}
    else:
        task['executions'].pop()
    with pytest.raises(AssertionError):
        verify_operation(task, [op], [approval], manifest)


def test_lost_submission_recovery_finds_exact_original_without_replaying(make_task):
    from backend import db
    from tests.phase4_review_acceptance import recover_manifest
    task = make_task()
    manifest = {'submission_key': task['request_key'], 'bot_id': task['bot_id'], 'conversation_id': str(task['conversation_id']), 'prompt': task['input']}
    with db.connection() as conn:
        before = conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']
        recovered = recover_manifest(conn, manifest)
        assert recovered['task_id'] == str(task['id'])
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == before
        with pytest.raises(AssertionError, match='exact test'):
            recover_manifest(conn, {**manifest, 'prompt': 'Different work'})


def settled_read_manifest(conn, task):
    conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (task['id'],))
    conn.execute("INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input) VALUES (%s,%s,%s,1,%s,'completed',%s,%s)", (uuid4(), task['id'], task['bot_id'], str(uuid4()), str(uuid4()), task['input']))
    return {'submission_key': task['request_key'], 'bot_id': task['bot_id'], 'conversation_id': str(task['conversation_id']), 'task_id': str(task['id']), 'prompt': task['input'], 'verification_failed': 'Missing required read', 'previous_attempts': [{'task_id': str(uuid4()), 'verification_failed': 'Earlier failure'}]}


def test_read_retry_preserves_original_evidence_without_submitting_work(make_task):
    from backend import db
    task = make_task()
    with db.connection() as conn:
        manifest = settled_read_manifest(conn, task)
        original = json.loads(json.dumps(manifest))
        before = conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n']
        archived = retry_read_manifest(conn, manifest)
        assert manifest == original and len(archived) == 2
        assert archived[0] == original['previous_attempts'][0]
        assert archived[1]['task_id'] == str(task['id']) and archived[1]['submission_key'] == task['request_key']
        assert archived[1]['verification_failed'] == original['verification_failed']
        assert 'previous_attempts' not in archived[1]
        assert conn.execute('SELECT count(*) AS n FROM tasks').fetchone()['n'] == before


@pytest.mark.parametrize('bad', ['not_failed', 'verified', 'active_child', 'active_execution', 'missing_execution', 'external_proposal', 'wrong_identity'])
def test_read_retry_rejects_unsettled_unverified_or_external_work(make_task, bad):
    from backend import db
    from tests.test_phase4 import connection, proposal
    task = make_task()
    if bad == 'external_proposal':
        proposal(task, connection())
    with db.connection() as conn:
        manifest = settled_read_manifest(conn, task)
        if bad == 'not_failed':
            manifest.pop('verification_failed')
        elif bad == 'verified':
            manifest['verified'] = {'real_provider_reads': True}
        elif bad == 'active_child':
            child = make_task('writer')
            conn.execute("UPDATE tasks SET root_id=%s,parent_id=%s,status='running' WHERE id=%s", (task['id'], task['id'], child['id']))
        elif bad == 'active_execution':
            conn.execute("UPDATE attempts SET status='running' WHERE task_id=%s", (task['id'],))
        elif bad == 'missing_execution':
            conn.execute('DELETE FROM attempts WHERE task_id=%s', (task['id'],))
        elif bad == 'wrong_identity':
            manifest['prompt'] = 'Different task'
        with pytest.raises(AssertionError):
            retry_read_manifest(conn, manifest)


@pytest.mark.parametrize('action', ['gmail.search', 'calendar.availability'])
def test_read_verifier_rejects_empty_mail_coverage_and_calendar_errors(action):
    receipts, children, calls, chosen, expected, required = read_evidence()
    connection = chosen['github']
    arguments = {'query': 'from:me', 'limit': 1} if action == 'gmail.search' else {'calendar_id': 'primary', 'start': '2026-10-09T09:00:00+00:00', 'end': '2026-10-09T10:00:00+00:00'}
    tool_args = {'connection_id': connection, 'action': action, 'arguments': arguments}
    calls[0]['function']['arguments'] = json.dumps(tool_args)
    receipts[0]['request_hash'] = service.tool_request_hash('connector', ConnectorArgs.model_validate(tool_args).model_dump())
    receipts[0]['response'] = {'result': {'messages': []} if action == 'gmail.search' else {'calendars': {'primary': {'busy': [], 'errors': [{'reason': 'forbidden'}]}}}}
    with pytest.raises(AssertionError, match='Gmail read|Calendar availability'):
        verify_reads(receipts, children, calls, {'google': connection}, {'google': ('writer', set())}, {action: arguments})


@pytest.mark.parametrize('bad', ['ambiguous_file', 'wrong_file', 'wrong_channel'])
def test_selected_resource_discovery_rejects_ambiguous_or_unselected_private_data(bad):
    files = [{'id': 'selected', 'name': 'H200 benchmarks (1).csv', 'mimeType': 'text/csv'}]
    matches = [{'channel': {'id': 'C_TEST', 'name': 'test'}}]
    reads = [('drive.search', {}, {'result': {'files': files}}), ('slack.search', {}, {'result': {'ok': True, 'messages': {'matches': matches}}})]
    assert selected_resources(reads, 'test')['drive_file_id'] == 'selected'
    assert selected_resources(reads, 'test')['slack_channel_id'] == 'C_TEST'
    if bad == 'ambiguous_file':
        files.append({'id': 'other', 'name': 'H200 benchmarks.csv', 'mimeType': 'text/csv'})
    elif bad == 'wrong_file':
        files[0]['name'] = 'H100 benchmarks.csv'
    else:
        matches[0]['channel']['name'] = 'general'
    with pytest.raises(AssertionError):
        selected_resources(reads, 'test')


@pytest.mark.parametrize('bad', ['no_connector_operation', 'misclassified_approval'])
def test_prepared_private_review_requires_frozen_connector_authorization(make_task, bad):
    from backend import db
    from tests.test_phase4 import connection
    task = make_task()
    c = connection('google')
    arguments = {'to': 'test@example.com', 'subject': 'Test', 'body': 'Draft', 'thread_id': '', 'in_reply_to': ''}
    manifest = {'task_id': str(task['id']), 'connection_id': str(c['id']), 'bot_id': task['bot_id'], 'action': 'gmail.send', 'arguments': arguments}
    with db.connection() as conn:
        if bad == 'no_connector_operation':
            service.request_review(conn, task, 'Generic draft', {'artifact': 'draft'})
        else:
            approval = service.request_review(conn, task, 'Exact external draft', {'connection_id': str(c['id']), 'action': 'gmail.send', 'arguments': arguments}, 'connector')
            assert verify_prepared_review(conn, manifest)
            conn.execute("UPDATE approvals SET kind='review' WHERE id=%s", (approval['approval_id'],))
        with pytest.raises(AssertionError):
            verify_prepared_review(conn, manifest)


def test_prepared_private_review_binds_current_outcome_revision(make_task):
    from backend import db, outcomes
    from tests.test_phase4 import connection
    task = make_task()
    account = connection('google')
    arguments = {'to': 'test@example.com', 'subject': 'Test', 'body': 'Draft', 'thread_id': '', 'in_reply_to': ''}
    manifest = {'task_id': str(task['id']), 'connection_id': str(account['id']), 'bot_id': task['bot_id'], 'action': 'gmail.send', 'arguments': arguments}
    with db.connection() as conn:
        outcome = outcomes.register(conn, task, [{'key': 'delivery', 'title': 'Send test', 'kind': 'connector_effect'}])[0]
        review = service.request_review(conn, task, 'Test delivery', {'connection_id': str(account['id']), 'action': 'gmail.send', 'arguments': arguments, 'outcome_id': str(outcome['id'])}, 'connector')
        assert verify_prepared_review(conn, manifest)
        conn.execute('UPDATE connector_operations SET outcome_revision=outcome_revision+1 WHERE approval_id=%s', (review['approval_id'],))
        with pytest.raises(AssertionError):
            verify_prepared_review(conn, manifest)
