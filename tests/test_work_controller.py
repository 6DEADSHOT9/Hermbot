"""Owner changes, recovery, scoped effects and artifacts across complete work transitions."""
import base64
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
import httpx

from backend import capabilities, connections, db, native, outcomes, service, tool_references
from backend import work_controller, work_intake, work_items, worker
from backend import inputs
from backend.artifacts import ArtifactStore
from backend.models import AskArgs
from backend import app as application
from backend.settings import settings
from tests.conftest import row
from tests.test_phase4 import connection
from tests.test_execution_capabilities import inventory  # noqa: F401


def message(prompt, bot='researcher', *, manager=False, conversation=None):
    with db.connection() as conn:
        if not conversation:
            conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='researcher'").fetchone()['id']
        conn.execute('INSERT INTO participants VALUES(%s,%s) ON CONFLICT DO NOTHING', (conversation, bot))
        return service.create_task(conn, bot_id=bot, conversation_id=conversation, prompt=prompt,
                                   request_key=str(uuid4()), message_intent='auto', orchestration_version=2,
                                   managed_bot_ids=['writer', 'reviewer'] if manager else None)


def interpret(task, *operations):
    value = work_intake.Interpretation.model_validate({'operations': list(operations)})
    with db.connection() as conn:
        work_intake.apply(conn, task['id'], value)
    return row(task['id'])


def work(prompt='Research and publish a report', *, manager=False, requirements=None, effects=None):
    return interpret(message(prompt, manager=manager), {'intent': 'assignment', 'title': 'Requested work',
                    'requirements': requirements or [{'key': 'report', 'title': 'Research report', 'kind': 'report'}],
                    'requested_effects': effects or []})


def test_intake_records_requirements_before_execution_and_is_idempotent():
    task = message('Research and publish a report')
    assert worker.claim_new() == []
    assert row(task['id'])['intake_status'] == 'pending'
    operation = {'intent': 'assignment', 'title': 'Research', 'requirements': [{'key': 'report', 'title': 'Report', 'kind': 'report'}]}
    result = interpret(task, operation)
    interpret(task, operation)
    attempt = worker.claim_new()[0]
    assert 'hermbot_prepare is optional' in attempt['response']['request']['instructions']
    with db.connection() as conn:
        assert len(work_items.list_items(conn)) == 1
        assert conn.execute('SELECT count(*) AS n FROM work_changes').fetchone()['n'] == 1
        assert outcomes.assigned(conn, result)[0]['key'] == 'report'


def test_discussion_and_status_do_not_create_work_or_start_mutations():
    task = interpret(message('What do you think?'), {'intent': 'discussion'})
    assert task['work_item_id'] is None
    attempt = worker.claim_new()[0]
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            service.tool_action(conn, task['bot_id'], attempt['session_id'], 'publish', {'name': 'bad.txt', 'content': 'bad'}, 'bad')
    with db.connection() as conn:
        assert work_items.list_items(conn) == []


def test_cross_bot_owner_revision_wakes_waiting_work_without_grant_transfer():
    original = work(manager=True)
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='waiting_children' WHERE id=%s", (original['id'],))
    revised = interpret(message('Use a different output format', 'writer'),
                        {'intent': 'update', 'work_item_id': str(original['work_item_id']), 'objective': 'Publish the report in the revised format'})
    assert revised['message_intent'] == 'steered' and revised['grant_id'] is None
    work_controller.advance()
    assert row(original['id'])['status'] == 'queued'
    assert row(original['id'])['work_revision'] == 2
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM controls WHERE task_id=%s', (original['id'],)).fetchone()['n'] == 1
    work_controller.advance()
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM controls').fetchone()['n'] == 1


def test_superseded_requirements_stop_counting_and_research_is_preserved():
    original = work(requirements=[{'key': 'research', 'title': 'Research', 'kind': 'report'},
                                  {'key': 'output', 'title': 'Original output', 'kind': 'artifact'}])
    with db.connection() as conn:
        conn.execute("UPDATE work_outcomes SET status='fulfilled',evidence_strength='model_reported' WHERE key='research'")
        conn.execute("UPDATE tasks SET status='waiting_children' WHERE id=%s", (original['id'],))
    interpret(message('Replace the output with a new format'), {'intent': 'update', 'work_item_id': str(original['work_item_id']),
              'supersede_keys': ['output'], 'requirements': [{'key': 'replacement', 'title': 'New output', 'kind': 'artifact'}]})
    work_controller.advance()
    with db.connection() as conn:
        item = work_items.list_items(conn)[0]
        assert item['progress'] == {'fulfilled': 1, 'required': 2}
        assert next(o for o in item['outcomes'] if o['key'] == 'output')['status'] == 'cancelled'
        assert 'replacement' in {o['key'] for o in outcomes.assigned(conn, row(original['id']))}


def test_cancelled_dependency_resumes_manager_without_running_dependent_branch():
    parent = work(manager=True)
    with db.connection() as conn:
        child = service.delegate(conn, parent, 'writer', 'Independent result')['task_id']
        dependent = service.delegate(conn, parent, 'reviewer', 'Review result', depends_on=[child])['task_id']
        service.cancel(conn, service.locked_task(conn, child))
    worker.advance_waiters()
    assert row(parent['id'])['status'] == 'queued'
    assert row(dependent)['status'] == 'blocked'
    assert 'cancelled' in row(parent['id'])['continuation']


def test_multiple_operations_preserve_one_owner_message_and_do_not_duplicate_work():
    original = work()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (original['id'],))
    followup = message('How is it going? Include the limitations in the report.')
    interpret(followup, {'intent': 'status', 'work_item_id': str(original['work_item_id'])},
              {'intent': 'update', 'work_item_id': str(original['work_item_id']), 'objective': 'Research and publish a report including limitations'})
    with db.connection() as conn:
        assert len(work_items.list_items(conn)) == 1
        assert conn.execute("SELECT count(*) AS n FROM messages WHERE role='user'").fetchone()['n'] == 2
        derived = conn.execute('SELECT * FROM tasks WHERE request_key=%s', (f"intake:{followup['id']}:1",)).fetchone()
        assert derived['owner_message_id'] == row(followup['id'])['owner_message_id']


def test_work_correction_can_be_recorded_with_unresolved_external_effect():
    task = work()
    c = connection()
    with db.connection() as conn:
        conn.execute("INSERT INTO connector_operations(id,connection_id,connection_revision,task_id,bot_id,action,arguments,payload_hash,status) VALUES(%s,%s,1,%s,'researcher','github.create_issue','{}','unknown','unresolved')", (uuid4(), c['id'], task['id']))
    followup = interpret(message('Add limitations to the report'), {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'objective': 'Report with limitations'})
    assert followup['intake_status'] == 'applied'
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM connector_operations').fetchone()['status'] == 'unresolved'


def test_owner_authorization_matches_scope_and_survives_delegation(monkeypatch):
    task = work('Create an issue in owner/repo', manager=True,
          requirements=[{'key': 'issue', 'title': 'Create issue', 'kind': 'connector_effect', 'target': {'action': 'github.create_issue', 'resource': 'owner/repo'}}],
          effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Create an issue in owner/repo'}])
    c = connection(bots=['writer'])
    with db.connection() as conn:
        child = service.delegate(conn, task, 'writer', 'Create requested issue')['task_id']
        result = connections.invoke(conn, service.locked_task(conn, child), {'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body'}})
        assert result['authorization'] == 'owner_request'
        assert conn.execute('SELECT count(*) AS n FROM approvals').fetchone()['n'] == 0
        with pytest.raises(HTTPException):
            connections.invoke(conn, task, {'connection_id': c['id'], 'action': 'github.repository', 'arguments': {'repo': 'owner/repo'}})
    calls = []
    monkeypatch.setattr(connections.providers, 'execute', lambda *args: calls.append(args) or {'id': 42})
    assert connections.drive_one()
    assert len(calls) == 1
    assert connections.drive_one() is False
    with db.connection() as conn:
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'fulfilled'


def test_outside_requested_scope_requires_review_and_policy_can_require_review():
    task = work('Create an issue in owner/repo',
          requirements=[{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect'}],
          effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Create an issue in owner/repo'}])
    connection()
    with db.connection() as conn:
        result = connections.invoke(conn, task, {'action': 'github.create_issue', 'arguments': {'repo': 'other/repo', 'title': 'Title', 'body': 'Body'}})
        assert result['approval_id']


def test_fabricated_authorization_quote_is_rejected_atomically():
    task = message('Draft an issue')
    with pytest.raises(HTTPException):
        interpret(task, {'intent': 'assignment', 'requirements': [{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect'}],
                  'requested_effects': [{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Send without review'}]})
    assert row(task['id'])['work_item_id'] is None
    with db.connection() as conn:
        assert conn.execute('SELECT count(*) AS n FROM work_authorizations').fetchone()['n'] == 0


def test_invalid_connection_is_not_reported_as_missing_permission():
    task = work()
    connection()
    with db.connection() as conn:
        with pytest.raises(HTTPException) as error:
            connections.invoke(conn, task, {'connection_id': uuid4(), 'action': 'github.repository', 'arguments': {'repo': 'owner/repo'}})
        assert error.value.detail['code'] == 'invalid_connection_reference'
        assert 'Discover' in error.value.detail['recovery']


def test_ambiguous_accounts_and_references_cannot_silently_change_identity():
    task = work()
    connection()
    connection()
    with db.connection() as conn:
        with pytest.raises(HTTPException) as error:
            tool_references.resolve_connection(conn, task, {'action': 'github.repository'})
        assert error.value.detail['code'] == 'ambiguous_connection'


def test_discovered_named_recipient_resolves_within_authorized_account():
    task = work('DM Sathwik in Slack', requirements=[{'key': 'delivery', 'title': 'DM', 'kind': 'connector_effect', 'target': {'action': 'slack.post'}}],
          effects=[{'outcome_key': 'delivery', 'action': 'slack.post', 'resource_label': 'Sathwik', 'instruction_quote': 'DM Sathwik in Slack'}])
    c = connection(provider='slack')
    with db.connection() as conn:
        references = tool_references.remember(conn, task, c, 'slack.members', {'members': [{'id': 'U12345678', 'profile': {'display_name': 'Sathwik'}}]})
        result = connections.invoke(conn, task, {'action': 'slack.post', 'arguments': {'channel': references[0]['reference'], 'text': 'Requested message'}})
        assert result['authorization'] == 'owner_request'
        op = conn.execute('SELECT * FROM connector_operations').fetchone()
        assert op['arguments']['channel'] == 'U12345678'


def test_owner_revision_invalidates_undispatched_action_but_preserves_history():
    task = work('Create an issue in owner/repo', requirements=[{'key': 'delivery', 'title': 'Issue', 'kind': 'connector_effect'}],
          effects=[{'outcome_key': 'delivery', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Create an issue in owner/repo'}])
    connection()
    with db.connection() as conn:
        connections.invoke(conn, task, {'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body'}})
    interpret(message('Cancel the issue delivery'), {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'supersede_keys': ['delivery']})
    assert connections.drive_one() is False
    with db.connection() as conn:
        assert conn.execute('SELECT status FROM connector_operations').fetchone()['status'] == 'cancelled'
        assert conn.execute('SELECT revoked_at FROM work_authorizations').fetchone()['revoked_at']


def test_managed_work_uses_native_tools_without_prepare(inventory):  # noqa: F811
    task = work()
    with db.connection() as conn:
        view = capabilities.manifest(conn, task['bot_id'], task)
    assert not view['preparation_required']
    assert 'terminal' in view['allowed_tools']


def test_shared_status_does_not_expose_private_inputs_or_credentials():
    task = work(manager=True)
    with db.connection() as conn:
        child = service.delegate(conn, task, 'writer', 'Private execution brief')['task_id']
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        result = service.tool_action(conn, 'writer', attempt['session_id'], 'work_status', {}, 'status')
    assert str(child) in str(result)
    assert 'Private execution brief' not in str(result)
    assert 'secret_encrypted' not in str(result)


def test_file_publication_transfers_bytes_and_records_evidence(monkeypatch):
    task = work(requirements=[{'key': 'file', 'title': 'File', 'kind': 'artifact'}])
    data = b'PDF or text bytes are transported outside model arguments\n'
    monkeypatch.setattr(native, 'request', lambda *a, **k: {'name': 'report.txt', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'content_base64': base64.b64encode(data).decode()})
    store = ArtifactStore()
    store.initialize()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        result = service.tool_action(conn, task['bot_id'], attempt['session_id'], 'publish_file', {'path': '/workspace/report.txt', 'content_type': 'text/plain', 'outcome_key': 'file'}, 'publish-file', store)
        artifact = conn.execute('SELECT * FROM artifacts WHERE id=%s', (result['id'],)).fetchone()
        assert store.read_bytes(artifact) == data
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'fulfilled'


def test_workspace_export_rejects_links_traversal_and_hidden_files(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('workspace_adapter', Path('/app/docker/runtime_api.py'))
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    monkeypatch.setattr(adapter, 'WORKSPACE', tmp_path)
    (tmp_path/'report.txt').write_bytes(b'report')
    (tmp_path/'linked.txt').symlink_to(tmp_path/'report.txt')
    assert adapter.workspace_bytes('/workspace/report.txt') == b'report'
    for path in ('/workspace/linked.txt', '/workspace/../secret', '/opt/data/config.yaml', '/workspace/.env'):
        with pytest.raises(HTTPException):
            adapter.workspace_bytes(path)


def test_stale_planned_action_is_rejected_after_owner_revision():
    task = work()
    attempt = worker.claim_new()[0]
    interpret(message('Change the requested report'), {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'objective': 'Changed report'})
    with pytest.raises(HTTPException) as error:
        with db.connection() as conn:
            service.tool_action(conn, task['bot_id'], attempt['session_id'], 'finish', {'result': 'Old report'}, 'old-plan', expected_work_revision=1)
    assert error.value.detail['code'] == 'work_revision_changed'
    assert row(task['id'])['pending_result'] is None


def test_interpretation_does_not_apply_against_edited_message_or_newer_work():
    task = work()
    update = message('Include limitations')
    value = work_intake.Interpretation.model_validate({'operations': [{'intent': 'update', 'work_item_id': str(task['work_item_id']), 'objective': 'Changed report'}]})
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            work_intake.apply(conn, update['id'], value, expected_message_hash='outdated')
    with pytest.raises(HTTPException):
        with db.connection() as conn:
            work_intake.apply(conn, update['id'], value, expected_revisions={str(task['work_item_id']): 0})
    assert row(update['id'])['message_intent'] == 'auto'


def test_typed_owner_answer_is_recorded_and_interpreted_before_resuming():
    task = work()
    with db.connection() as conn:
        requested = inputs.ask(conn, task, AskArgs(prompt='Which format?').model_dump())
        question = conn.execute('SELECT * FROM input_requests WHERE id=%s', (requested['input_request_id'],)).fetchone()
        inputs.answer(conn, question['id'], {'expected_question_hash': question['question_hash'], 'answer': 'Markdown'})
        intake = conn.execute('SELECT * FROM tasks WHERE id=(SELECT resume_after_intake FROM tasks WHERE id=%s)', (task['id'],)).fetchone()
        evidence = work_intake.context(conn, intake)
        assert evidence['latest_owner_message']['content'] == 'Markdown'
        assert 'does not approve' not in conn.execute('SELECT continuation FROM tasks WHERE id=%s', (task['id'],)).fetchone()['continuation']
    assert worker.claim_new() == []
    interpret(intake, {'intent': 'answer', 'input_request_id': str(question['id']), 'answer': 'Markdown'})
    assert worker.claim_new()[0]['task_id'] == task['id']


def test_review_policy_is_enforced_again_before_dispatch(monkeypatch):
    task = work('Create an issue in owner/repo', requirements=[{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect'}],
          effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Create an issue in owner/repo'}])
    connection()
    with db.connection() as conn:
        connections.invoke(conn, task, {'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body'}})
        conn.execute("UPDATE workspace_settings SET external_action_mode='review'")
    calls = []
    monkeypatch.setattr(connections.providers, 'execute', lambda *a: calls.append(a))
    assert connections.drive_one()
    assert not calls


def test_ambiguous_named_recipient_does_not_receive_automatic_authorization():
    task = work('DM Sathwik in Slack', requirements=[{'key': 'dm', 'title': 'DM', 'kind': 'connector_effect'}],
          effects=[{'outcome_key': 'dm', 'action': 'slack.post', 'resource_label': 'Sathwik', 'instruction_quote': 'DM Sathwik in Slack'}])
    c = connection(provider='slack')
    with db.connection() as conn:
        refs = tool_references.remember(conn, task, c, 'slack.members', {'members': [{'id': identity, 'profile': {'display_name': 'Sathwik'}} for identity in ('U12345678', 'U23456789')]})
        result = connections.invoke(conn, task, {'action': 'slack.post', 'arguments': {'channel': refs[0]['reference'], 'text': 'Message'}})
        assert result['approval_id']


def test_requirement_keys_replace_manual_revision_bookkeeping_for_results():
    task = work()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        service.tool_action(conn, task['bot_id'], attempt['session_id'], 'finish',
                            {'result': 'Research findings with limitations', 'results': [{'key': 'report', 'status': 'fulfilled'}]}, 'result', expected_work_revision=1)
    worker.settle(attempt['id'], {'status': 'completed'})
    with db.connection() as conn:
        item = work_items.list_items(conn)[0]
        assert item['status'] == 'completed'
        assert item['outcomes'][0]['evidence_strength'] == 'model_reported'


def test_correction_does_not_execute_a_helper_without_dependency_inputs():
    task = work(manager=True)
    with db.connection() as conn:
        first = service.delegate(conn, task, 'writer', 'Prepare evidence')['task_id']
        dependent = service.delegate(conn, task, 'reviewer', 'Review evidence', depends_on=[first])['task_id']
    interpret(message('Include uncertainty estimates'), {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'objective': 'Report including uncertainty'})
    work_controller.advance()
    assert row(task['id'])['status'] == 'queued'
    assert row(dependent)['status'] == 'blocked'


def test_owner_requirement_removal_is_versioned_and_retires_pending_drafts():
    task = work('Create an issue in owner/repo', requirements=[{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect'}])
    connection()
    with db.connection() as conn:
        result = connections.invoke(conn, task, {'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body'}})
        assert result['approval_id']
        outcome = outcomes.rows(conn, task['work_item_id'])[0]
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    response = client.post(f"/api/v1/outcomes/{outcome['id']}/drop", json={'expected_revision': outcome['revision']})
    assert response.status_code == 200, response.text
    client.close()
    with db.connection() as conn:
        item = work_items.list_items(conn)[0]
        assert item['revision'] == 2
        assert item['changes'][0]['kind'] == 'requirement_removed'
        assert conn.execute('SELECT status FROM connector_operations').fetchone()['status'] == 'cancelled'


def test_queued_edit_preserves_original_owner_message_and_waits_for_intake():
    task = work()
    client = TestClient(application.app, headers={'Authorization': 'Bearer ' + settings.owner_token})
    response = client.put(f"/api/v1/tasks/{task['id']}", json={'prompt': 'Include uncertainty estimates', 'expected_input_hash': service.digest(task['input'])})
    assert response.status_code == 200, response.text
    client.close()
    assert row(task['id'])['resume_after_intake']
    assert worker.claim_new() == []
    with db.connection() as conn:
        assert conn.execute('SELECT content FROM messages WHERE id=%s', (task['owner_message_id'],)).fetchone()['content'] == 'Research and publish a report'


def test_context_retrieval_can_read_earlier_public_messages_but_not_another_conversation():
    task = work()
    followup = message('Revise that earlier work')
    result = work_intake.read_context(followup, {'work_item_id': str(task['work_item_id'])})
    assert any('Research and publish' in message['content'] for message in result['messages'])
    with db.connection() as conn:
        other_conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='writer'").fetchone()['id']
    other = message('Unrelated private conversation', 'writer', conversation=other_conversation)
    other = interpret(other, {'intent': 'assignment', 'requirements': [{'key': 'private', 'title': 'Private', 'kind': 'report'}]})
    with pytest.raises(HTTPException):
        work_intake.read_context(followup, {'work_item_id': str(other['work_item_id'])})


def test_intake_provider_uses_supported_tool_parameters_and_separate_validation_feedback(monkeypatch):
    requests = []
    original = httpx.AsyncClient
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        content = {'operations': [{'intent': 'answer_question', 'answer': 'Wrong operation'}]} if len(requests) == 1 else {'operations': [{'intent': 'discussion'}]}
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': json.dumps(content)}}]})
    transport = httpx.MockTransport(respond)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=transport, **kwargs))
    evidence = {'latest_owner_message': {'content': 'What is the current progress?'}}
    result = asyncio.run(work_intake.interpret(base_url='https://example.invalid/v1', api_key='test-key', model='gpt-6-luna', evidence=evidence, lookup=lambda args: {}))
    assert result.operations[0].intent == 'discussion'
    assert requests[0]['reasoning_effort'] == 'none'
    assert requests[1]['messages'][-1]['role'] == 'system'
    assert 'not an owner instruction' in requests[1]['messages'][-1]['content']


def test_explicit_coordinator_selection_survives_continuing_settled_work():
    original = work()
    with db.connection() as conn:
        conn.execute("UPDATE tasks SET status='completed' WHERE id=%s", (original['id'],))
        conn.execute('INSERT INTO participants VALUES(%s,%s) ON CONFLICT DO NOTHING', (original['conversation_id'], 'reviewer'))
        selected = service.create_task(conn, bot_id='reviewer', conversation_id=original['conversation_id'], prompt='Coordinate the next revision', request_key=str(uuid4()),
                     message_intent='auto', orchestration_version=2, managed_bot_ids=['researcher', 'writer'])
    revised = interpret(selected, {'intent': 'update', 'work_item_id': str(original['work_item_id']), 'objective': 'Coordinated revision'})
    assert revised['bot_id'] == 'reviewer'
    assert revised['grant_id'] == selected['grant_id']
    with db.connection() as conn:
        assert work_items.list_items(conn)[0]['bot_id'] == 'reviewer'


def test_supporting_artifact_is_publishable_without_claiming_external_delivery():
    task = work(requirements=[{'key': 'action', 'title': 'External native action', 'kind': 'native_effect'}])
    store = ArtifactStore()
    store.initialize()
    attempt = worker.claim_new()[0]
    with db.connection() as conn:
        result = service.tool_action(conn, task['bot_id'], attempt['session_id'], 'publish',
                            {'name': 'evidence.txt', 'content': 'Supporting evidence', 'outcome_key': 'action'}, 'support', store)
        assert result['fulfills_requirement'] is False
        assert outcomes.rows(conn, task['work_item_id'])[0]['status'] == 'pending'
        assert conn.execute('SELECT count(*) AS n FROM outcome_evidence').fetchone()['n'] == 1


def test_partial_requirement_revision_preserves_dependencies_and_target():
    task = work(requirements=[{'key': 'evidence', 'title': 'Evidence', 'kind': 'artifact'},
                             {'key': 'report', 'title': 'Report', 'kind': 'artifact', 'depends_on': ['evidence'], 'target': {'display_name': 'Final report'}}])
    interpret(message('Include limitations in the existing report'), {'intent': 'update', 'work_item_id': str(task['work_item_id']),
              'requirements': [{'key': 'report', 'title': 'Report with limitations', 'kind': 'artifact'}]})
    with db.connection() as conn:
        report = next(o for o in outcomes.rows(conn, task['work_item_id']) if o['key'] == 'report')
        assert report['dependencies'] == ['evidence']
        assert report['target']['display_name'] == 'Final report'


def test_content_revision_preserves_authorization_for_unchanged_destination():
    task = work('Create an issue in owner/repo', requirements=[{'key': 'issue', 'title': 'Issue', 'kind': 'connector_effect', 'target': {'action': 'github.create_issue', 'resource': 'owner/repo'}}],
          effects=[{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Create an issue in owner/repo'}])
    connection()
    interpret(message('Use a shorter issue title'), {'intent': 'update', 'work_item_id': str(task['work_item_id']),
              'requirements': [{'key': 'issue', 'title': 'Issue with a shorter title', 'kind': 'connector_effect'}]})
    work_controller.advance()
    with db.connection() as conn:
        result = connections.invoke(conn, service.locked_task(conn, task['id']), {'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Short', 'body': 'Body'}})
        assert result['authorization'] == 'owner_request'


def test_converted_result_cannot_disagree_with_its_delivery_authorization():
    interpretation = work_intake.Interpretation.model_validate({'operations': [{'intent': 'update', 'work_item_id': '00000000-0000-4000-8000-000000000001',
        'requirements': [{'key': 'file', 'title': 'Hosted object', 'kind': 'connector_effect', 'target': {'action': 'drive.create'}}],
        'requested_effects': [{'outcome_key': 'delivery', 'action': 'drive.create', 'resource': 'root', 'instruction_quote': 'Use a hosted object'}]}]})
    evidence = {'background_context': {'work': [{'id': '00000000-0000-4000-8000-000000000001', 'outcomes': [{'key': 'file', 'kind': 'artifact'}, {'key': 'delivery', 'kind': 'connector_effect'}]}]}}
    with pytest.raises(ValueError, match='different requirements'):
        work_intake.validate_delivery_alignment(interpretation, evidence)


@pytest.mark.parametrize('case', ['duplicate', 'superseded', 'other_destination', 'split_revision'])
def test_conversion_checks_current_deliveries_without_blocking_distinct_results(case):
    work_id = '00000000-0000-4000-8000-000000000001'
    operation = {'intent': 'update', 'work_item_id': work_id,
        'requirements': [{'key': 'file', 'title': 'Hosted object', 'kind': 'connector_effect', 'target': {'action': 'drive.create', 'resource': 'root'}}],
        'requested_effects': [{'outcome_key': 'file', 'action': 'drive.create', 'resource': 'root', 'instruction_quote': 'Use a hosted object'}]}
    if case == 'superseded':
        operation['supersede_keys'] = ['delivery']
    operations = [operation]
    if case == 'split_revision':
        operations.append({'intent': 'update', 'work_item_id': work_id, 'supersede_keys': ['delivery']})
    evidence = {'background_context': {'work': [{'id': work_id, 'outcomes': [
        {'key': 'file', 'kind': 'artifact'},
        {'key': 'delivery', 'kind': 'connector_effect', 'status': 'pending', 'target': {'action': 'drive.create', 'resource': 'other-folder' if case == 'other_destination' else 'root'}}]}]}}
    interpretation = work_intake.Interpretation.model_validate({'operations': operations})
    if case == 'duplicate':
        with pytest.raises(ValueError, match='duplicates'):
            work_intake.validate_delivery_alignment(interpretation, evidence)
    else:
        work_intake.validate_delivery_alignment(interpretation, evidence)


def test_multiple_revisions_in_one_message_are_applied_together():
    task = work(requirements=[{'key': 'report', 'title': 'Report', 'kind': 'artifact'}, {'key': 'check', 'title': 'Check', 'kind': 'execution_check', 'required': False}])
    followup = message('Shorten the report and explain the optional check')
    interpret(followup,
        {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'requirements': [{'key': 'report', 'title': 'Short report', 'kind': 'artifact'}]},
        {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'requirements': [{'key': 'check', 'title': 'Explained check', 'kind': 'execution_check'}]})
    with db.connection() as conn:
        item = work_items.list_items(conn)[0]
        assert item['revision'] == 2
        values = {o['key']: o for o in item['outcomes']}
        assert values['report']['title'] == 'Short report'
        assert values['check']['title'] == 'Explained check'
        assert values['check']['required'] is False


def test_message_fanout_preserves_explicit_coordination_authority():
    task = message('Explain the approach, then coordinate a report', manager=True)
    interpret(task, {'intent': 'discussion'}, {'intent': 'assignment', 'requirements': [{'key': 'report', 'title': 'Coordinated report', 'kind': 'artifact'}]})
    with db.connection() as conn:
        derived = conn.execute('SELECT * FROM tasks WHERE request_key=%s', (f"intake:{task['id']}:1",)).fetchone()
        assert derived['grant_id'] == task['grant_id']
        assert derived['owner_message_id'] == row(task['id'])['owner_message_id']


def test_direction_arriving_at_settlement_resumes_without_a_reconciliation_tick():
    task = work()
    attempt = worker.claim_new()[0]
    interpret(message('Include the limitations'), {'intent': 'update', 'work_item_id': str(task['work_item_id']), 'objective': 'Report with limitations'})
    worker.settle(attempt['id'], {'status': 'completed', 'output': 'Original response'})
    assert row(task['id'])['status'] == 'queued'
    next_attempt = worker.claim_new()[0]
    assert 'Include the limitations' in next_attempt['input']


def test_explicit_owner_direction_admits_matching_existing_draft(monkeypatch):
    task = work('Draft an issue for review', requirements=[{'key': 'issue', 'title': 'Issue delivery', 'kind': 'connector_effect'}])
    connection()
    with db.connection() as conn:
        draft = connections.invoke(conn, task, {'action': 'github.create_issue', 'arguments': {'repo': 'owner/repo', 'title': 'Title', 'body': 'Body'}})
        assert draft['approval_id']
    interpret(message('Send the issue to owner/repo'), {'intent': 'update', 'work_item_id': str(task['work_item_id']),
              'requested_effects': [{'outcome_key': 'issue', 'action': 'github.create_issue', 'resource': 'owner/repo', 'instruction_quote': 'Send the issue to owner/repo'}]})
    work_controller.advance()
    with db.connection() as conn:
        operation = conn.execute('SELECT * FROM connector_operations').fetchone()
        assert operation['status'] == 'queued'
        assert operation['authorization_id']
        assert conn.execute('SELECT status FROM approvals').fetchone()['status'] == 'stale'
    calls = []
    monkeypatch.setattr(connections.providers, 'execute', lambda *args: calls.append(args) or {'id': 1})
    assert connections.drive_one()
    assert len(calls) == 1
