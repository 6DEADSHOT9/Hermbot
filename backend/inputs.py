"""Ordinary human answers are distinct from exact-action approval decisions."""
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from psycopg.types.json import Jsonb

from .execution_policy import review_expiry

from . import db, outcomes, service
from .models import AnswerRequest


def ask(conn, task, args):
    if args['answer_type'] in ('choice', 'confirmation') and (len(set(args['choices'])) < 2 or len(set(args['choices'])) != len(args['choices'])):
        raise HTTPException(422, 'Provide at least two distinct answer choices')
    if args['answer_type'] == 'text' and args['choices']:
        raise HTTPException(422, 'Text questions do not contain choices')
    outcome = outcomes.require(conn, task, args['outcome_id']) if args.get('outcome_id') else None
    payload = {'prompt': args['prompt'].strip(), 'answer_type': args['answer_type'], 'choices': args['choices'],
               'outcome_id': str(outcome['id']) if outcome else None, 'revision': outcome['revision'] if outcome else None}
    if not payload['prompt']:
        raise HTTPException(422, 'A question cannot be empty')
    hashed = service.digest(payload)
    pending = conn.execute("SELECT * FROM input_requests WHERE task_id=%s AND status='pending' AND expires_at>now()", (task['id'],)).fetchone()
    if pending:
        if pending['question_hash'] == hashed:
            return {'input_request_id': str(pending['id']), 'status': 'waiting_input', 'instruction': 'End this turn and wait for the actual answer.'}
        raise HTTPException(409, 'Wait for the existing question before asking another')
    if conn.execute("SELECT 1 FROM approvals WHERE task_id=%s AND status='pending' UNION ALL SELECT 1 FROM connector_operations WHERE task_id=%s AND status IN ('queued','dispatching','uncertain')", (task['id'], task['id'])).fetchone():
        raise HTTPException(409, 'Resolve the existing review or external action first')
    identity = uuid4()
    conn.execute('INSERT INTO input_requests(id,task_id,work_item_id,outcome_id,outcome_revision,prompt,answer_type,choices,question_hash,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                 (identity, task['id'], task['work_item_id'], outcome['id'] if outcome else None, outcome['revision'] if outcome else None, payload['prompt'], args['answer_type'], Jsonb(args['choices']), hashed, review_expiry(task)))
    conn.execute("UPDATE tasks SET status='waiting_input',updated_at=now() WHERE id=%s", (task['id'],))
    db.event(conn, 'input.requested', task, data={'input_request_id': str(identity)})
    return {'input_request_id': str(identity), 'status': 'waiting_input', 'instruction': 'End this turn. The owner must answer; approval decisions do not contain answers.'}


def answer(conn, identity, body, *, source='owner', task=None):
    question = conn.execute('SELECT * FROM input_requests WHERE id=%s', (identity,)).fetchone()
    if not question:
        raise HTTPException(404, 'Question not found')
    origin = service.locked_task(conn, question['task_id'])
    question = conn.execute('SELECT * FROM input_requests WHERE id=%s FOR UPDATE', (identity,)).fetchone()
    text = body['answer'].strip()
    if body['expected_question_hash'] != question['question_hash']:
        raise HTTPException(409, 'The question changed; reload it before answering')
    if not text or (question['answer_type'] != 'text' and text not in question['choices']):
        raise HTTPException(422, 'Provide a nonempty answer matching the requested type')
    if question['status'] == 'answered':
        if question['answer'] == text:
            return {'status': 'answered', 'input_request_id': str(identity)}
        raise HTTPException(409, 'This question already has a different answer')
    if question['status'] != 'pending' or question['expires_at'] <= service.now() or origin['status'] in service.TERMINAL | service.STOPPING:
        raise HTTPException(409, 'This question expired or its work was stopped')
    if task and (task['conversation_id'] != origin['conversation_id'] or not conn.execute("SELECT 1 FROM messages WHERE task_id=%s AND role='user'", (task['id'],)).fetchone()):
        raise HTTPException(403, 'An answer must come from the owner in this conversation')
    service.check_grant(conn, origin)
    if question['outcome_id']:
        outcomes.require(conn, origin, question['outcome_id'], question['outcome_revision'])
    conn.execute("UPDATE input_requests SET status='answered',answer=%s,answer_source=%s,answered_at=now() WHERE id=%s", (text, source, identity))
    continuation = 'Authenticated owner answer follows. Continue the current work using its recorded requirements and authorization. Application policy is separate from the owner answer.\n' + json.dumps({'question': question['prompt'], 'answer': text, 'outcome_id': str(question['outcome_id']) if question['outcome_id'] else None})
    # Append rather than erase existing owner directions; settlement handles a still-ending run.
    conn.execute("UPDATE tasks SET continuation=concat_ws(E'\\n',NULLIF(continuation,''),%s::text),status=CASE WHEN EXISTS(SELECT 1 FROM attempts WHERE task_id=%s AND status=ANY(%s)) THEN status ELSE 'queued' END,updated_at=now() WHERE id=%s", (continuation, origin['id'], list(service.ACTIVE_ATTEMPTS), origin['id']))
    if source == 'owner' and origin.get('orchestration_version') == 2:
        intake = service.create_task(conn, bot_id=origin['bot_id'], conversation_id=origin['conversation_id'],
                     prompt=text, request_key='question-answer:' + str(identity), context=origin['context'],
                     message_intent='auto', orchestration_version=2, reply_to_message_id=origin['reply_to_message_id'])
        conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s', (Jsonb({'owner_answer_to': str(identity), 'owner_work_target': str(origin['work_item_id']) if origin['work_item_id'] else None}), intake['id']))
        conn.execute('UPDATE tasks SET resume_after_intake=%s WHERE id=%s', (intake['id'], origin['id']))
        conn.execute('UPDATE input_requests SET answer_message_id=%s WHERE id=%s', (intake['owner_message_id'], identity))
    elif task:
        from .work_controller import owner_message
        conn.execute('UPDATE input_requests SET answer_message_id=%s WHERE id=%s', (owner_message(conn, task)['id'], identity))
    db.event(conn, 'input.answered', origin, data={'input_request_id': str(identity)})
    return {'status': 'answered', 'input_request_id': str(identity)}


def context(conn, task):
    return conn.execute('SELECT id,prompt,answer_type,choices,status,answer,outcome_id,question_hash FROM input_requests WHERE task_id=%s ORDER BY created_at DESC LIMIT 20', (task['id'],)).fetchall()


def pending(conn, conversation_id=None):
    values = conn.execute("""SELECT q.* FROM input_requests q JOIN tasks t ON t.id=q.task_id
        WHERE q.status='pending' AND q.expires_at>now() AND t.status<>ALL(%s)
        AND (t.deadline IS NULL OR t.deadline>now())
        AND (%s::uuid IS NULL OR t.conversation_id=%s) ORDER BY q.created_at""",
        (list(service.TERMINAL | {'cancelling'}), conversation_id, conversation_id)).fetchall()
    from .owner_requests import valid
    return [q for q in values if valid(conn,q,conn.execute('SELECT * FROM tasks WHERE id=%s',(q['task_id'],)).fetchone())]


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.post('/api/v1/input-requests/{identity}/answer')
    def respond(identity: str, body: AnswerRequest):
        from uuid import UUID
        try:
            identity = UUID(identity)
        except ValueError:
            raise HTTPException(422, 'Invalid question ID')
        with db.connection() as conn:
            return answer(conn, identity, body.model_dump())

    return router
