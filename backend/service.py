"""Durable coordination. Models propose; authenticated runtime context supplies identity."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from .db import event, message
from .settings import settings

TERMINAL = {"completed", "failed", "cancelled", "interrupted", "uncertain"}
STOPPING = {'cancelling', 'pausing_budget', 'paused_budget'}
# The one definition of an answered teammate message (alias m): it has a reply that wasn't released. A reply that was
# dropped, given up or marked Done is not an answer, for the scheduler and bookkeeping here as for a waiting task.
ANSWERED = "EXISTS(SELECT 1 FROM teammate_messages reply WHERE reply.reply_to=m.id AND reply.status<>'cancelled')"
# The one definition of a teammate message that is still open work (alias m), for recurring-work bookkeeping: pending,
# an original, unanswered, and either unlinked or linked to a reply task that has ended (the scheduler will retry or
# release it). One linked to a live reply task is covered by that task.
OPEN_TEAMMATE_MESSAGE = ("m.status='pending' AND m.reply_to IS NULL AND (m.response_task_id IS NULL OR EXISTS(SELECT 1 FROM tasks handler "
                         "WHERE handler.id=m.response_task_id AND handler.status IN (" + ','.join(f"'{status}'" for status in sorted(TERMINAL)) + "))) "
                         "AND NOT " + ANSWERED)
ACTIVE_ATTEMPTS = ("dispatching", "running", "waiting_for_approval", "stopping", "uncertain", "external")


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def fail(status, detail):
    raise HTTPException(status, detail)


# A task's next run reads `continuation` once, then dispatch clears it. Everything queued for that run
# is only ever added, never replaced, so a later message (an approval, a release by dependencies, a
# handback) can't erase an earlier one, such as Continue's instruction never to repeat an external effect.
APPEND_CONTINUATION = "continuation=concat_ws(E'\\n',NULLIF(continuation,''),%s::text)"
# Every run of work that has run before starts with this, whatever restarted it.
RESUME_RULE = ("This task has run before in this conversation. Before acting, check your session history and the current results. "
               "Continue only what is unfinished, and never repeat an external action that already succeeded or whose outcome is uncertain.")


def add_continuation(conn, task_id, text):
    conn.execute(f"UPDATE tasks SET {APPEND_CONTINUATION} WHERE id=%s", (text, task_id))


def locked_task(conn, task_id):
    row = conn.execute("SELECT root_id,COALESCE(management_work_item_id,work_item_id) AS work_item_id FROM tasks WHERE id=%s", (task_id,)).fetchone()
    if not row:
        fail(404, "Task not found")
    if row['work_item_id']:
        conn.execute('SELECT id FROM work_items WHERE id=%s FOR UPDATE', (row['work_item_id'],))
    conn.execute("SELECT id FROM tasks WHERE id=%s FOR UPDATE", (row["root_id"],)).fetchone()
    return conn.execute("SELECT * FROM tasks WHERE id=%s FOR UPDATE", (task_id,)).fetchone()


def require_known_connector_outcomes(conn, root_id):
    if conn.execute("SELECT 1 FROM work_outcomes o JOIN tasks t ON t.work_item_id=o.work_item_id WHERE t.root_id=%s AND o.status='uncertain'", (root_id,)).fetchone():
        fail(409, 'Reconcile the uncertain external result before continuing this work')
    if conn.execute("SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.root_id=%s AND o.status IN ('dispatching','uncertain','unresolved')", (root_id,)).fetchone():
        fail(409, "Verify the previous application outcome before retrying or reassigning this work")


def active_task(conn, bot_id, session_id):
    attempt = conn.execute(
        "SELECT * FROM attempts WHERE bot_id=%s AND session_id=%s AND status=ANY(%s) ORDER BY created_at DESC LIMIT 1",
        (bot_id, session_id, list(ACTIVE_ATTEMPTS)),
    ).fetchone()
    if not attempt:
        fail(403, "No admitted task for this runtime session")
    task = locked_task(conn, attempt["task_id"])
    from .execution_policy import STOPPING, expired
    if task["status"] in TERMINAL | STOPPING or expired(task):
        fail(409, "Task is stopped or expired")
    check_grant(conn, task)
    return task


def active_grant(conn, grant_id, *, lock=False):
    """The grant if it is neither revoked nor expired. A lock holds off a concurrent revocation."""
    grant = conn.execute("SELECT * FROM grants WHERE id=%s" + (" FOR SHARE" if lock else ""), (grant_id,)).fetchone()
    return grant if grant and not grant["revoked_at"] and grant["expires_at"] > now() else None


def present_bots(conn, bot_ids):
    """Those of these bots that still exist and are neither archived nor being deleted."""
    return conn.execute("SELECT id,name FROM bots WHERE id=ANY(%s) AND status NOT IN ('archived','deleting','deleted')", (list(bot_ids),)).fetchall()


def handoffs_used(conn, root):
    """Handoffs used by every run of this work: its first run and each retry of it share one budget."""
    origin = root.get("retried_from") or root["id"]
    return conn.execute("SELECT COALESCE(sum(handoffs),0) AS n FROM tasks WHERE (id=%s OR retried_from=%s) AND parent_id IS NULL",
                        (origin, origin)).fetchone()["n"]


def admit_work(conn, bot_id, conversation_id):
    """Whether this bot can take new work in this conversation now; returns the bot."""
    from . import registry
    conversation = conn.execute("SELECT archived_at FROM conversations WHERE id=%s FOR SHARE", (conversation_id,)).fetchone()
    if not conversation:
        fail(404, "Conversation not found")
    if conversation['archived_at']:
        fail(409, "Restore this conversation before assigning new work")
    bot = registry.admit_bot(conn, bot_id)
    if bot["status"] != "active" or bot["desired_state"] != "running":
        fail(409, "Start this bot and wait for provisioning before assigning work")
    if not conn.execute("SELECT 1 FROM participants WHERE conversation_id=%s AND bot_id=%s", (conversation_id, bot_id)).fetchone():
        fail(403, "Bot is not a participant in this conversation")
    return bot


def check_grant(conn, task, *, target=None, operation=None, manager=None, lock_scope=True):
    from .recurring_management import scope
    frozen = scope(conn, task)
    if frozen and str(task.get('grant_id')) != frozen['grant_id']:
        fail(403, 'This occurrence requires its recorded management permission')
    if not task.get("grant_id"):
        if operation:
            fail(403, "This action requires a user-issued management grant")
        return None
    grant = active_grant(conn, task["grant_id"], lock=bool(frozen and lock_scope))
    if not grant:
        fail(403, "Management authority has expired or been revoked")
    if frozen:
        if grant['manager_id'] != frozen['manager_id'] or task['bot_id'] not in frozen['targets'] + [frozen['manager_id']] or task['bot_id'] not in grant['targets'] + [grant['manager_id']]:
            fail(403, 'This teammate is outside the saved occurrence scope')
        grant = {**grant, 'targets': sorted(set(grant['targets']) & set(frozen['targets'])),
                 'operations': sorted(set(grant['operations']) & set(frozen['operations']))}
    if target and target not in grant["targets"] + [grant["manager_id"]]:
        fail(403, "Target bot is outside the management grant")
    if operation and (operation not in grant["operations"] or manager != grant["manager_id"]):
        fail(403, "Manager is not authorized for this operation")
    return grant


def create_task(conn, *, bot_id, conversation_id, prompt, request_key, context="chat", managed_bot_ids=None,
                management_operations=None, grant_id=None, attachment_ids=None, artifact_ids=None, reply_to_message_id=None, skill_refs=None, mention_bot_ids=None,
                message_intent='assignment', work_item_id=None, deadline=None, orchestration_version=0, owner_message_id=None, coordination_version=0,
                inherited_grant_id=None):
    from . import registry
    fingerprint = digest({"bot_id": bot_id, "conversation_id": str(conversation_id), "prompt": prompt,
                          "context": context, "managed_bot_ids": managed_bot_ids, "management_operations": management_operations,
                          "grant_id": str(grant_id or inherited_grant_id or "") or None, "attachment_ids": list(map(str, attachment_ids or [])),
                          "reply_to_message_id": reply_to_message_id, "skill_refs": skill_refs or [], "mention_bot_ids": mention_bot_ids or [], "artifact_ids": list(map(str, artifact_ids or []))})
    if message_intent != 'assignment' or work_item_id:
        fingerprint = digest({'submission': fingerprint, 'intent': message_intent, 'work_item_id': str(work_item_id) if work_item_id else None})
    if deadline is not None:
        fingerprint = digest({'submission': fingerprint, 'deadline': deadline})
    # One lock covers identical request keys, including concurrent first submissions.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (request_key,))
    existing = conn.execute("SELECT * FROM tasks WHERE request_key=%s", (request_key,)).fetchone()
    if existing:
        if existing["request_hash"] != fingerprint:
            fail(409, "Idempotency key already identifies different work")
        return existing
    if deadline is not None and (deadline.tzinfo is None or deadline <= now()):
        fail(422, 'Use a future owner deadline with a timezone')
    bot = admit_work(conn, bot_id, conversation_id)
    task_id = uuid4()
    from .library import visible_in_conversation
    for artifact_id in artifact_ids or []:
        if not visible_in_conversation(conn, artifact_id, conversation_id):
            fail(403, 'Share this file version with the conversation before assigning it')
    for attachment_id in attachment_ids or []:
        if not conn.execute("SELECT 1 FROM attachments WHERE id=%s AND conversation_id=%s", (attachment_id, conversation_id)).fetchone():
            fail(403, "Attachment is not available in this conversation")
    if set(skill_refs or []) - set(bot["enabled_skills"]):
        fail(403, "Skill reference is not enabled for this bot")
    if reply_to_message_id:
        root_message = conn.execute("SELECT * FROM messages WHERE id=%s AND conversation_id=%s", (reply_to_message_id, conversation_id)).fetchone()
        if not root_message:
            fail(404, "Thread message does not belong to this conversation")
        reply_to_message_id = root_message["reply_to_message_id"] or root_message["id"]
    for mentioned in set(mention_bot_ids or []):
        if not conn.execute("SELECT 1 FROM participants WHERE conversation_id=%s AND bot_id=%s", (conversation_id, mentioned)).fetchone():
            fail(403, "Mentioned bot is not in this conversation")
    if grant_id or inherited_grant_id:
        # A standing grant is chosen by the owner; a task grant is inherited only by a retry of its own work,
        # never copied, so revoking it reaches every run of that work and its limits still apply.
        if (grant_id and inherited_grant_id) or managed_bot_ids is not None or management_operations is not None:
            fail(422, "Select a standing grant or create a task grant, not both")
        kind = "standing" if grant_id else "task"
        grant_id = grant_id or inherited_grant_id
        grant = active_grant(conn, grant_id)
        if not grant or grant["kind"] != kind or grant["manager_id"] != bot_id:
            fail(403, "Choose an active standing grant belonging to this manager" if kind == "standing"
                 else "This work's permission to manage teammates is no longer active")
        if kind == "task" and len(present_bots(conn, grant["targets"])) < len(set(grant["targets"])):
            fail(409, "A teammate this work managed is archived or no longer exists. Send the request again with Coordinate team.")
    if management_operations is not None and managed_bot_ids is None:
        fail(422, "Operations must belong to an explicitly scoped management request")
    if managed_bot_ids is not None:
        if not managed_bot_ids or any(peer == bot_id for peer in managed_bot_ids):
            fail(422, "Choose other registered bots for this management request")
        for peer in managed_bot_ids:
            registry.require_bot(conn, peer)
        grant_id = uuid4()
        conn.execute(
            "INSERT INTO grants(id,manager_id,targets,operations,max_handoffs,expires_at,root_id,max_created_bots) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (grant_id, bot_id, Jsonb(sorted(set(managed_bot_ids))), Jsonb(management_operations or ["delegate", "inspect", "steer", "cancel", "reassign"]),
             settings.max_handoffs, min(deadline, now() + timedelta(hours=24)) if deadline else now() + timedelta(hours=24), task_id, 1 if management_operations and "create" in management_operations else 0),
        )
    task = conn.execute(
        "INSERT INTO tasks(id,bot_id,conversation_id,root_id,grant_id,input,status,context,deadline,request_key,request_hash) "
        "VALUES (%s,%s,%s,%s,%s,%s,'queued',%s,%s,%s,%s) RETURNING *",
        (task_id, bot_id, conversation_id, task_id, grant_id, prompt, context,
         deadline, request_key, fingerprint),
    ).fetchone()
    conn.execute('UPDATE tasks SET execution_policy_version=1,active_budget_seconds=%s WHERE id=%s', (registry.workspace(conn)['active_budget_seconds'], task_id))
    conn.execute("UPDATE tasks SET attachment_ids=%s,reply_to_message_id=%s,skill_refs=%s WHERE id=%s", (Jsonb(list(map(str, attachment_ids or []))), reply_to_message_id, Jsonb(skill_refs or []), task_id))
    task = locked_task(conn, task_id)
    conn.execute('UPDATE tasks SET library_artifact_ids=%s WHERE id=%s', (Jsonb(list(map(str, artifact_ids or []))), task_id))
    for artifact_id in artifact_ids or []:
        conn.execute('INSERT INTO artifact_access(artifact_id,task_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (artifact_id, task_id))
    if owner_message_id is None:
        message(conn, task, "owner", prompt, f"request:{task_id}", "user")
        owner_message_id = conn.execute('SELECT id FROM messages WHERE source_id=%s', (f'request:{task_id}',)).fetchone()['id']
    conn.execute('UPDATE tasks SET orchestration_version=%s,owner_message_id=%s,intake_status=%s WHERE id=%s',
                 (orchestration_version, owner_message_id, 'pending' if orchestration_version == 2 and message_intent == 'auto' else 'legacy', task_id))
    conn.execute('UPDATE tasks SET coordination_version=%s WHERE id=%s', (coordination_version, task_id))
    conn.execute("UPDATE messages SET mentions=%s WHERE source_id=%s", (Jsonb(mention_bot_ids or []), f"request:{task_id}"))
    from . import work_items
    if message_intent == 'assignment' and not work_item_id:
        work_items.new_item(conn, task)
    else:
        conn.execute("UPDATE tasks SET message_intent='auto' WHERE id=%s", (task_id,))
        task = locked_task(conn, task_id)
        if message_intent != 'auto':
            work_items.route(conn, task, {'intent': message_intent, 'work_item_id': work_item_id})
        elif work_item_id:
            fail(422, 'Choose update or status when referencing existing work explicitly')
    task = locked_task(conn, task_id)
    event(conn, "task.queued", task, data={"manager": managed_bot_ids is not None or inherited_grant_id is not None})
    return task


def delegate(conn, task, bot_id, prompt, depends_on=None, artifact_ids=None, *, attachment_ids=None, skill_refs=None, expected_output='', outcome_ids=None, responsibility='contribution', required=True, replaces_task_id=None):
    from . import registry
    bot = registry.admit_bot(conn, bot_id)
    if bot["status"] != "active" or bot["desired_state"] != "running":
        fail(409, "This teammate is not running; wait for provisioning or ask the owner to start it")
    if bot_id == task["bot_id"]:
        fail(422, "Do your own work directly; self-delegation is not allowed")
    delegation_grant = check_grant(conn, task, target=bot_id)
    if delegation_grant and "delegate" not in delegation_grant["operations"]:
        fail(403, "Delegation is not permitted by this management grant")
    if task["depth"] >= settings.max_depth:
        fail(409, "Delegation depth limit reached")
    ancestors = conn.execute(
        "WITH RECURSIVE chain AS (SELECT id,parent_id,bot_id FROM tasks WHERE id=%s UNION ALL "
        "SELECT t.id,t.parent_id,t.bot_id FROM tasks t JOIN chain c ON t.id=c.parent_id) SELECT bot_id FROM chain",
        (task["id"],),
    ).fetchall()
    if bot_id in {ancestor["bot_id"] for ancestor in ancestors}:
        fail(409, "Delegation would create a circular bot wait")
    root = conn.execute("SELECT * FROM tasks WHERE id=%s", (task["root_id"],)).fetchone()
    grant = check_grant(conn, task)
    # A task grant's handoff budget covers its work, including every retry of it, not each run afresh. Only one run
    # of that work is ever active (a retry waits for the old run to settle), and its root is locked, so no extra lock.
    used = handoffs_used(conn, root) if grant and grant["kind"] == "task" else root["handoffs"]
    if used >= min(settings.max_handoffs, grant["max_handoffs"] if grant else settings.max_handoffs):
        fail(409, "Task handoff budget exhausted")
    from . import outcomes
    if task.get('orchestration_version') == 2 and not outcome_ids:
        outcome_ids = [o['id'] for o in outcomes.assigned(conn, task) if o['status'] not in ('fulfilled', 'cancelled')]
    outcomes.require_handoff(conn, task, outcome_ids)
    depends_on = depends_on or []
    for dependency_id in depends_on:
        dependency = conn.execute("SELECT * FROM tasks WHERE id=%s", (dependency_id,)).fetchone()
        if not dependency or dependency["parent_id"] != task["id"]:
            fail(403, "Dependencies must be existing sibling assignments you own")
    artifacts = []
    for artifact_id in artifact_ids or []:
        if not artifact_visible(conn, artifact_id, task):
            fail(403, "Artifact has not been granted to this task")
        artifacts.append(artifact_id)
    shared_attachments = list(dict.fromkeys(str(a) for a in attachment_ids or []))
    if set(shared_attachments) - set(task['attachment_ids']):
        fail(403, 'Share only attachments explicitly granted to this task')
    for identity in shared_attachments:
        if not conn.execute('SELECT 1 FROM attachments WHERE id=%s AND conversation_id=%s', (identity, task['conversation_id'])).fetchone():
            fail(403, 'Attachment does not belong to this conversation')
    if skill_refs:
        from .capabilities import manifest, requirements
        missing = requirements(manifest(conn, bot_id, task, peer=True), skills=skill_refs)
        if missing:
            plan = {**task['execution_plan'], 'handoff_blockers': missing}
            conn.execute('UPDATE tasks SET execution_plan=%s WHERE id=%s', (Jsonb(plan), task['id']))
            event(conn, 'work.handoff_blocked', task, data={'bot_id': bot_id, 'blockers': missing})
            return {'delivery': 'blocked', 'status': 'needs_access', 'blockers': missing,
                    'instruction': 'No assignment was created. Choose a capable teammate or available procedure, or explain the exact missing access to the owner. Do not repeat this handoff.'}
    objective = conn.execute('SELECT title,objective FROM work_items WHERE id=%s', (task['work_item_id'],)).fetchone()
    handoff = {'source_task_id': str(task['id']), 'work_item_id': str(task['work_item_id']) if task['work_item_id'] else None,
               'objective': objective['objective'][:2000] if objective else task['input'][:2000],
               'expected_output': expected_output, 'artifact_ids': [str(a) for a in artifacts],
               'attachment_ids': shared_attachments}
    task_id = uuid4()
    child = conn.execute(
        "INSERT INTO tasks(id,bot_id,conversation_id,parent_id,root_id,grant_id,input,status,depth,context,deadline) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (task_id, bot_id, task["conversation_id"], task["id"], task["root_id"], task["grant_id"], prompt,
         "blocked" if depends_on else "queued", task["depth"] + 1, task["context"], task["deadline"]),
    ).fetchone()
    conn.execute("UPDATE tasks SET reply_to_message_id=%s WHERE id=%s", (task.get("reply_to_message_id"), task_id))
    conn.execute('UPDATE tasks SET attachment_ids=%s,skill_refs=%s,handoff_context=%s WHERE id=%s',
                 (Jsonb(shared_attachments), Jsonb(list(dict.fromkeys(skill_refs or []))), Jsonb(handoff), task_id))
    conn.execute('UPDATE tasks SET work_item_id=%s,message_intent=%s WHERE id=%s', (task['work_item_id'], task['message_intent'], task_id))
    from .recurring_management import inherit
    child = inherit(conn, conn.execute('SELECT * FROM tasks WHERE id=%s', (task_id,)).fetchone())
    conn.execute('UPDATE tasks SET execution_policy_version=%s,active_budget_seconds=%s WHERE id=%s', (task['execution_policy_version'], task['active_budget_seconds'], task_id))
    conn.execute("UPDATE tasks SET orchestration_version=%s,work_revision=%s,owner_message_id=%s,intake_status='applied' WHERE id=%s",
                 (task.get('orchestration_version', 0), task.get('work_revision', 1), task.get('owner_message_id'), task_id))
    conn.execute('UPDATE tasks SET coordination_version=%s WHERE id=%s', (task.get('coordination_version', 0), task_id))
    outcomes.share(conn, task, task_id, outcome_ids or [])
    if task.get('coordination_version') == 1:
        from .result_acceptance import assign
        assign(conn, task, task_id, outcome_ids or [], responsibility=responsibility, required=required, expected_output=expected_output, replaces_task_id=replaces_task_id)
    for dependency in depends_on:
        conn.execute("INSERT INTO dependencies VALUES (%s,%s)", (task_id, dependency))
    for artifact_id in artifacts:
        conn.execute("INSERT INTO artifact_access VALUES (%s,%s) ON CONFLICT DO NOTHING", (artifact_id, task_id))
    conn.execute("UPDATE tasks SET handoffs=handoffs+1 WHERE id=%s", (task["root_id"],))
    if task['execution_plan'].get('handoff_blockers'):
        conn.execute('UPDATE tasks SET execution_plan=execution_plan-\'handoff_blockers\' WHERE id=%s', (task['id'],))
    if task.get('coordination_version') != 1:
        conn.execute("UPDATE tasks SET status='waiting_children',updated_at=now() WHERE id=%s", (task["id"],))
    event(conn, "task.delegated", child, data={"parent_id": str(task["id"]), "depends_on": list(map(str, depends_on)), 'handoff': handoff})
    return {"task_id": str(task_id), "status": child["status"], "delivery": "accepted", "instruction":
            ("Assignment queued. Continue independent work. Use hermbot_wait for required assignment results when ready to combine them. Contributions cannot fulfill the shared deliverable; its accountable owner reviews and submits the final result." if task.get('coordination_version') == 1 else
             "The task is queued, not finished. You may assign other branches now. End this turn; you will be resumed with results. Do not poll or repeat the handoff.")}


def artifact_visible(conn, artifact_id, task):
    artifact = conn.execute("SELECT * FROM artifacts WHERE id=%s", (artifact_id,)).fetchone()
    if not artifact:
        return None
    if artifact["task_id"] == task["id"] or conn.execute(
        "SELECT 1 FROM artifact_access WHERE artifact_id=%s AND task_id=%s", (artifact_id, task["id"]),
    ).fetchone():
        return artifact
    return None


def inspect_task(conn, caller, target_id, *, operation='inspect'):
    from .recurring_management import scope
    if scope(conn, caller) and operation not in check_grant(conn, caller)['operations']:
        fail(403, 'Operation is outside this occurrence permission')
    target = conn.execute("SELECT * FROM tasks WHERE id=%s", (target_id,)).fetchone()
    if not target or target["root_id"] != caller["root_id"]:
        fail(403, "That task is outside this operation")
    # A task can inspect its own descendants; a grant does not expose unrelated history.
    descendants = conn.execute(
        "WITH RECURSIVE tree AS (SELECT id FROM tasks WHERE id=%s UNION ALL "
        "SELECT t.id FROM tasks t JOIN tree p ON t.parent_id=p.id) SELECT id FROM tree", (caller["id"],),
    ).fetchall()
    if target["id"] not in {row["id"] for row in descendants}:
        fail(403, "Only owned assignments are inspectable")
    return {key: target[key] for key in ("id", "bot_id", "status", "output", "error", "parent_id")}


def request_review(conn, task, summary, payload, kind="review"):
    if kind in ('review', 'input') and {'connection_id', 'action', 'arguments'}.issubset(payload):
        fail(422, 'External action drafts require kind connector; a generic review cannot authorize a connected-app change')
    if kind == 'input' and payload.get('input_type') != 'secure':
        from . import inputs
        from .models import AskArgs
        try:
            question = AskArgs(prompt=payload.get('prompt') or summary,
                               answer_type=payload.get('answer_type', 'text'), choices=payload.get('choices', []),
                               outcome_id=payload.get('outcome_id'))
        except ValidationError:
            fail(422, 'Information requests need a valid prompt, answer type and choices')
        return inputs.ask(conn, task, question.model_dump())
    if conn.execute("SELECT 1 FROM input_requests WHERE task_id=%s AND status='pending' AND expires_at>now()", (task['id'],)).fetchone():
        fail(409, 'End this turn and wait for the requested answer')
    if conn.execute("SELECT 1 FROM connector_operations WHERE task_id=%s AND status IN ('pending','queued','dispatching','uncertain')", (task['id'],)).fetchone():
        fail(409, 'End this turn and wait for the existing external operation receipt')
    if conn.execute("SELECT 1 FROM approvals WHERE task_id=%s AND status='pending'", (task['id'],)).fetchone():
        fail(409, 'End this turn and wait for the existing owner review')
    if kind in ('skill', 'memory'):
        from .knowledge import proposal_payload
        payload = proposal_payload(conn, task, kind, payload)
    if kind == 'connector':
        from .connections import prepare_review
        payload = prepare_review(conn, task, payload)
    if kind == 'input' and payload.get('input_type') == 'secure':
        if set(payload) - {'input_type', 'bot_id', 'instructions'} or payload.get('bot_id', task['bot_id']) != task['bot_id']:
            fail(422, 'Secure input requests contain only destination metadata for the requesting bot')
        payload = {**payload, 'bot_id': task['bot_id']}
    approval_id = uuid4()
    from .execution_policy import review_expiry
    approval = conn.execute(
        "INSERT INTO approvals(id,task_id,kind,payload,payload_hash,expires_at) VALUES (%s,%s,%s,%s,%s,%s) RETURNING *",
        (approval_id, task["id"], kind, Jsonb({"summary": summary, "action": payload}), digest(payload), review_expiry(task)),
    ).fetchone()
    if kind in ('skill', 'memory'):
        from .knowledge import record_proposal
        record_proposal(conn, task, approval_id, payload)
    if kind == 'connector':
        from .connections import record_review
        record_review(conn, task, approval_id, payload)
    conn.execute("UPDATE tasks SET status='waiting_approval',updated_at=now() WHERE id=%s", (task["id"],))
    event(conn, "approval.requested", task, data={"approval_id": str(approval_id), "summary": summary})
    return {"approval_id": str(approval["id"]), "status": "waiting_approval",
            "instruction": "End this turn. Only the owner can resolve this review; you will be resumed afterward."}


def reconcile_requests(conn, task):
    """Caller holds the task lock; closing a prompt never records an owner decision."""
    closed = task['status'] in TERMINAL | {'cancelling'}
    reviews = conn.execute("""UPDATE approvals a SET status=CASE WHEN %s THEN 'cancelled'
        WHEN a.expires_at<=now() THEN 'expired' ELSE 'cancelled' END
        WHERE a.task_id=%s AND a.status='pending' AND (%s OR a.expires_at<=now()
        OR (a.kind='runtime' AND NOT EXISTS(SELECT 1 FROM attempts r WHERE r.id=a.attempt_id AND r.status=ANY(%s))))
        RETURNING a.id,a.status""", (closed, task['id'], closed, list(ACTIVE_ATTEMPTS) + ['reserved'])).fetchall()
    questions = conn.execute("""UPDATE input_requests SET status=CASE WHEN %s THEN 'cancelled' ELSE 'expired' END
        WHERE task_id=%s AND status='pending' AND (%s OR expires_at<=now()) RETURNING id,status""",
        (closed, task['id'], closed)).fetchall()
    ids = [r['id'] for r in reviews]
    if ids:
        conn.execute("UPDATE lifecycle_proposals SET status='cancelled' WHERE approval_id=ANY(%s) AND status='pending'", (ids,))
        conn.execute("UPDATE knowledge_proposals SET status='cancelled',updated_at=now() WHERE approval_id=ANY(%s) AND status='pending'", (ids,))
        conn.execute("UPDATE connector_operations SET status='cancelled',updated_at=now() WHERE approval_id=ANY(%s) AND status='pending'", (ids,))
    for kind, records in (('approval', reviews), ('input', questions)):
        for record in records:
            event(conn, kind + '.closed', task, data={'id': str(record['id']), 'status': record['status']})
    if not closed and any(r['status'] == 'expired' for r in reviews + questions):
        cancel(conn, task, 'An unanswered question or review expired; work stopped without an answer or approval', kind='review_expired')


def cancel(conn, task, reason="Stopped by owner", *, kind=None):
    task = locked_task(conn, task["id"])
    descendants = conn.execute(
        "WITH RECURSIVE tree AS (SELECT id FROM tasks WHERE id=%s UNION ALL "
        "SELECT t.id FROM tasks t JOIN tree p ON t.parent_id=p.id) SELECT id FROM tree", (task["id"],),
    ).fetchall()
    from .recurring_management import scope, tasks as occurrence_tasks
    frozen = scope(conn, task)
    if frozen and task['id'] == task['root_id'] and task['bot_id'] == frozen['manager_id']:
        descendants = occurrence_tasks(conn, task)
        from .coordination import drop_pending
        drop_pending(conn, task.get('management_work_item_id') or task['work_item_id'], f'The recurring work it belonged to was stopped: {reason}')
    for row in descendants:
        target = conn.execute("SELECT * FROM tasks WHERE id=%s", (row["id"],)).fetchone()
        if target["status"] in TERMINAL and target["status"] != "uncertain":
            continue
        # Runtime settlement must not overwrite an owner stop or the original cause.
        if target['status'] == 'cancelling' and target['stop_reason']:
            continue
        active = conn.execute("SELECT id,status FROM attempts WHERE task_id=%s AND status=ANY(%s)",
                              (row["id"], list(ACTIVE_ATTEMPTS) + ["reserved"])).fetchall()
        if any(attempt["status"] == "uncertain" for attempt in active):
            state = "uncertain"
        else:
            state = "cancelling" if active else "cancelled"
            conn.execute("UPDATE attempts SET status='stopping' WHERE task_id=%s AND status=ANY(%s)",
                         (row["id"], [status for status in ACTIVE_ATTEMPTS if status != "uncertain"] + ["reserved"]))
        stop_kind = kind or ('owner' if reason == 'Stopped by owner' else 'execution')
        conn.execute("UPDATE tasks SET status=%s,error=%s,stop_kind=%s,stop_reason=%s,updated_at=now() WHERE id=%s", (state, reason, stop_kind, reason, row["id"]))
        conn.execute("UPDATE controls SET status='discarded',updated_at=now() WHERE task_id=%s AND source='monitor' AND status='pending'", (row['id'],))
        conn.execute("UPDATE approvals SET status='cancelled' WHERE task_id=%s AND status='pending'", (row["id"],))
        conn.execute("UPDATE input_requests SET status='cancelled' WHERE task_id=%s AND status='pending'", (row['id'],))
        conn.execute("UPDATE lifecycle_proposals SET status='cancelled' WHERE task_id=%s AND status='pending'", (row["id"],))
        conn.execute("UPDATE knowledge_proposals SET status='cancelled',updated_at=now() WHERE task_id=%s AND status IN ('pending','queued')", (row['id'],))
        conn.execute("UPDATE connector_operations SET status='cancelled',updated_at=now() WHERE task_id=%s AND status IN ('pending','queued')", (row['id'],))
        event(conn, "task.stop_requested", target, data={"reason": reason, "status": state})


def reassign(conn, caller, target_id, bot_id):
    target = inspect_task(conn, caller, target_id, operation='reassign')
    check_grant(conn, caller, target=bot_id, operation="reassign", manager=caller["bot_id"])
    if target["status"] not in {"failed", "interrupted", "cancelled"}:
        fail(409, "Reconcile and settle the previous attempt before reassignment")
    old = locked_task(conn, target_id)
    require_known_connector_outcomes(conn, old['root_id'])
    if old["parent_id"] != caller["id"]:
        fail(403, "Only directly owned assignments can be reassigned")
    dependencies = [row["depends_on"] for row in conn.execute("SELECT depends_on FROM dependencies WHERE task_id=%s", (target_id,))]
    artifact_ids = [row["artifact_id"] for row in conn.execute("SELECT artifact_id FROM artifact_access WHERE task_id=%s", (target_id,))]
    replacement = delegate(conn, caller, bot_id, old["input"], dependencies, artifact_ids,
                           attachment_ids=old['attachment_ids'], skill_refs=old['skill_refs'],
                           expected_output=old['handoff_context'].get('expected_output', ''),
                           outcome_ids=[r['outcome_id'] for r in conn.execute('SELECT outcome_id FROM task_outcomes WHERE task_id=%s', (old['id'],))])
    if replacement.get('delivery') == 'blocked':
        return replacement
    if caller.get('coordination_version') == 1:
        conn.execute('UPDATE work_outcomes SET owner_task_id=%s WHERE owner_task_id=%s AND status NOT IN (\'fulfilled\',\'cancelled\')', (UUID(replacement['task_id']), old['id']))
        conn.execute("UPDATE result_contributions SET required=false WHERE task_id=%s", (old['id'],))
        conn.execute('DELETE FROM result_contributions c USING work_outcomes o WHERE c.outcome_id=o.id AND c.task_id=%s AND o.owner_task_id=c.task_id', (UUID(replacement['task_id']),))
        conn.execute('UPDATE coordination_waits SET dependency_id=%s WHERE dependency_id=%s', (UUID(replacement['task_id']), old['id']))
    # Pending dependent assignments now wait on the replacement, not the failed attempt.
    conn.execute(
        "UPDATE dependencies SET depends_on=%s WHERE depends_on=%s AND task_id IN (SELECT id FROM tasks WHERE status='blocked')",
        (UUID(replacement["task_id"]), target_id),
    )
    event(conn, "task.reassigned", caller, data={"previous_task_id": str(target_id), "replacement_task_id": replacement["task_id"]})
    return replacement


def tool_request_hash(operation, args):
    """Keep receipt identity compatible when optional tool arguments are absent."""
    receipt_args = {k: v for k, v in args.items() if k not in ('outcomes', 'results', 'outcome_ids', 'outcome_id', 'input_request_id', 'answer') or v}
    if operation in ('delegate', 'message'):
        # Preserve the identity of historical calls with no new handoff fields.
        receipt_args = {k: v for k, v in receipt_args.items() if k not in ('attachment_ids', 'skill_refs', 'expected_output', 'outcome_keys') or v}
    receipt_args = {k: v for k, v in receipt_args.items() if not (k == 'responsibility' and v == 'contribution') and not (k == 'required' and v is True) and not (k in ('reply_to','replaces_task_id') and v is None)}
    receipt_args = {k: v for k, v in receipt_args.items() if k not in ('outcome_key', 'connection') or v}
    return digest({"operation": operation, "args": receipt_args})


def tool_action(conn, bot_id, session_id, operation, args, receipt_id, artifact_store=None, *, expected_work_revision=None):
    task = active_task(conn, bot_id, session_id)
    key = f"{bot_id}:{task['id']}:{receipt_id}"
    fingerprint = tool_request_hash(operation, args)
    previous = conn.execute("SELECT * FROM tool_receipts WHERE id=%s", (key,)).fetchone()
    if previous:
        if previous["request_hash"] != fingerprint:
            fail(409, "Tool receipt already identifies different arguments")
        return previous["response"]
    if task.get('orchestration_version') == 2 and task['work_item_id'] and expected_work_revision is not None:
        current_revision = conn.execute('SELECT revision FROM work_items WHERE id=%s', (task['work_item_id'],)).fetchone()['revision']
        if expected_work_revision != current_revision:
            fail(409, {'code': 'work_revision_changed', 'message': 'The request changed after this action was planned.', 'recovery': 'Read the current work context and plan the operation again.'})
    if task['status'] == 'waiting_input' and operation not in ('finish', 'ask', 'status', 'grants'):
        fail(409, 'End this turn and wait for the answer continuation before executing more work')
    from . import work_items
    if task['message_intent'] == 'auto' and operation != 'route_message':
        fail(409, 'Call hermbot_route_message first: discussion, assignment, update or status. Unclear intent stays discussion.')
    if operation in ('delegate', 'wait', 'publish', 'publish_file', 'propose', 'reassign', 'steer', 'cancel', 'review', 'ask') or (operation == 'message' and task.get('coordination_version') != 1):
        work_items.require_work(task)
    if operation == 'connector':
        from .connector_catalog import validate
        from .connections import resolve_connection
        connection = resolve_connection(conn, task, args)
        args = {**args, 'connection_id': connection['id']}
        from .tool_references import resolve_arguments
        if validate(connection, args['action'], resolve_arguments(conn, task, connection, args['arguments']))[1]:
            work_items.require_work(task)
    if operation == 'route_message':
        result = work_items.route(conn, task, args)
        from .capabilities import manifest
        result['capabilities'] = manifest(conn, bot_id, locked_task(conn, task['id']))
    elif operation == 'prepare':
        from .capabilities import prepare
        result = prepare(conn, task, args)
    elif operation == "teammates":
        grant = check_grant(conn, task)
        rows = conn.execute("SELECT id,name,role,capabilities,desired_state,status FROM bots WHERE id<>%s AND status IN ('active','provisioning') ORDER BY id", (bot_id,)).fetchall()
        from .capabilities import manifest
        peers = [row for row in rows if not grant or row["id"] in grant["targets"]]
        for peer in peers:
            view = manifest(conn, peer['id'], task, peer=True)
            peer['execution_capabilities'] = {key: view[key] for key in ('running', 'inventory_known', 'native_tools', 'skills', 'connections', 'blocked_tools', 'connector_restricted')}
        result = {"teammates": peers}
    elif operation == 'message' and task.get('coordination_version') == 1:
        from .coordination import send
        result = send(conn, task, args)
    elif operation == 'wait':
        from .coordination import wait
        if task.get('coordination_version') != 1:
            fail(409, 'Explicit waits are available for newly coordinated work')
        result = wait(conn, task, args)
    elif operation in {"delegate", "message"}:
        if args.get('outcome_keys'):
            from . import outcomes
            args = {**args, 'outcome_ids': [outcomes.resolve(conn, task, key=key) for key in args['outcome_keys']]}
        result = delegate(conn, task, args["bot_id"], args["prompt"], args.get("depends_on"), args.get("artifact_ids"),
                          attachment_ids=args.get('attachment_ids'), skill_refs=args.get('skill_refs'), expected_output=args.get('expected_output', ''), outcome_ids=args.get('outcome_ids'), responsibility=args.get('responsibility', 'contribution'), required=args.get('required', True), replaces_task_id=args.get('replaces_task_id'))
        if operation == "message":
            result["message_type"] = "informational"
    elif operation == "status":
        if task.get('orchestration_version') == 2:
            target = conn.execute('SELECT id,bot_id,status,parent_id,work_item_id FROM tasks WHERE id=%s AND conversation_id=%s', (args['task_id'], task['conversation_id'])).fetchone()
            if not target:
                fail(403, 'This assignment is outside the shared conversation')
            result = target
        else:
            result = inspect_task(conn, task, args["task_id"])
    elif operation == 'work_status':
        from . import outcomes
        items = work_items.list_items(conn, task['conversation_id'])
        if args.get('work_item_id'):
            work_items.require_item(conn, args['work_item_id'], task['conversation_id'])
            items = [item for item in items if str(item['id']) == str(args['work_item_id'])]
        result = {'work': [{'id': str(item['id']), 'title': item['title'], 'objective': item['objective'],
                  'revision': item['revision'], 'status': item['status'], 'outcomes': outcomes.context(conn, {**task, 'work_item_id': item['id'], 'parent_id': None}),
                  'assignments': [{k: run[k] for k in ('id', 'bot_id', 'status', 'parent_id')} for run in item['executions']],
                  'outputs': item['outputs']} for item in items], 'instruction': 'Shared work status is evidence; it grants no management, private-file or account access.'}
    elif operation == "review":
        result = request_review(conn, task, args["summary"], args.get("payload", {}), args.get("kind", "review"))
    elif operation == 'ask':
        from . import inputs
        result = inputs.ask(conn, task, args)
    elif operation in ('publish', 'publish_file'):
        if args.get('previous_id') and not artifact_visible(conn, args['previous_id'], task):
            fail(403, 'Previous version is not available to this task')
        identity = args.get('outcome_id')
        if task.get('orchestration_version') == 2 or args.get('outcome_key'):
            from . import outcomes
            identity = outcomes.resolve(conn, task, identity, args.get('outcome_key'), optional=True)
        if identity:
            from . import outcomes
            outcome = outcomes.require(conn, task, identity)
            if task.get('orchestration_version') != 2 and (outcome['kind'] not in ('artifact', 'report') or not outcomes.dependencies_ready(conn, outcome)):
                fail(409, 'Publish against an artifact/report outcome whose dependencies are ready')
        if operation == 'publish_file':
            result = artifact_store.publish_file(conn, task, args['path'], args.get('name', ''), args['content_type'], key, args.get('previous_id'))
        else:
            result = artifact_store.publish(conn, task, args["name"], args["content"], args.get("content_type", "text/plain"), key, args.get('previous_id'))
        if identity:
            if task.get('orchestration_version') == 2 and (outcome['kind'] not in ('artifact', 'report') or not outcomes.dependencies_ready(conn, outcome)):
                outcomes.supporting_evidence(conn, outcome, task['id'], 'artifact', result['artifact_id'], 'artifact_exists')
                result.update(supporting_result=outcome['key'], fulfills_requirement=False,
                              instruction='This artifact is published as supporting evidence. It does not prove an external action occurred. Report actual result statuses by requirement key when finishing.')
            elif outcome['kind'] not in ('artifact', 'report') or not outcomes.dependencies_ready(conn, outcome):
                fail(409, 'Publish against an artifact/report outcome whose dependencies are ready')
            else:
                fulfilled = outcomes.record(conn, outcome, task['id'], 'artifact', result['artifact_id'], 'artifact_exists')
                result.update(supporting_result=outcome['key'], fulfills_requirement=fulfilled)
                if not fulfilled:
                    result['instruction'] = 'Published as evidence. Submit the result with hermbot_finish; the accountable owner reviews required contributions and the current requirement.'
    elif operation == "read_artifact":
        artifact = artifact_visible(conn, args["artifact_id"], task)
        if not artifact:
            fail(403, "Artifact is not available to this task")
        from .library import TEXT_TYPES
        result = {"artifact_id": str(artifact["id"]), "name": artifact["name"], 'content_type': artifact['content_type'], 'version': artifact['version']}
        if artifact['content_type'] in TEXT_TYPES:
            text = artifact_store.read_bytes(artifact).decode('utf-8')
            result['content'] = text[:200_000]
            result['truncated'] = len(text) > 200_000
            result['path'] = f"/workspace/attachments/{artifact['id']}/{artifact['name']}"
        else:
            result['path'] = f"/workspace/attachments/{artifact['id']}/{artifact['name']}"
            result['instruction'] = 'Read the granted file in this private workspace with the appropriate native file/skill tools.'
    elif operation == "reassign":
        result = reassign(conn, task, args["task_id"], args["bot_id"])
    elif operation in {"steer", "cancel"}:
        inspect_task(conn, task, args["task_id"], operation=operation)
        check_grant(conn, task, operation=operation, manager=bot_id)
        target = locked_task(conn, args["task_id"])
        if operation == "cancel":
            cancel(conn, target, "Stopped by authorized manager")
        else:
            steer(conn, target, args["prompt"], source='manager')
        result = {"task_id": str(target["id"]), "accepted": True}
    elif operation in ('connectors', 'connector'):
        from .connections import accessible, invoke
        result = {'connections': accessible(conn, bot_id)} if operation == 'connectors' else invoke(conn, task, args, artifact_store)
    elif operation == "finish":
        from . import outcomes
        if task.get('orchestration_version') != 2 and task['message_intent'] in ('assignment', 'update') and task['status'] == 'running' and task['execution_plan'].get('ready'):
            from .capabilities import manifest
            if manifest(conn, bot_id, task)['preparation_required']:
                fail(409, 'Preparation expired for this continuation or access changed. Call hermbot_prepare again, then finish using the existing receipts. Do not repeat completed external actions.')
        claims = args.get('outcomes', [])
        if args.get('results'):
            current = {o['key']: o for o in outcomes.assigned(conn, task)}
            for result in args['results']:
                outcome = current.get(result['key'])
                if not outcome:
                    fail(422, 'Choose a result key from current work context')
                if outcome['status'] == 'fulfilled':
                    continue
                claims.append({k: v for k, v in result.items() if k != 'key'} | {'outcome_id': outcome['id'], 'expected_revision': outcome['assigned_revision']})
        outcomes.validate_claims(conn, {**task, 'pending_result': args['result']}, claims)
        conn.execute("UPDATE tasks SET pending_result=%s,pending_outcomes=%s WHERE id=%s", (args["result"], Jsonb(outcomes.serialized(claims)), task["id"]))
        result = {"accepted": True, "instruction": "End this turn. Your result will be settled after execution exits."}
    elif operation in ("propose", "grants", "proposals"):
        from . import lifecycle
        if operation == "propose":
            result = lifecycle.propose(conn, task, args)
        elif operation == "grants":
            grant = check_grant(conn, task)
            result = {"grant": grant, "instruction": "Only the owner creates or extends authority. Lifecycle proposals require explicit approval."}
        else:
            result = {"proposals": conn.execute("SELECT id,action,bot_id,status,payload,operation_id FROM lifecycle_proposals WHERE task_id=%s ORDER BY created_at", (task["id"],)).fetchall()}
    else:
        fail(403, "Unsupported operation")
    result = json.loads(json.dumps(result, default=str))
    conn.execute("INSERT INTO tool_receipts(id,bot_id,task_id,operation,request_hash,response) VALUES (%s,%s,%s,%s,%s,%s)",
                 (key, bot_id, task["id"], operation, fingerprint, Jsonb(result)))
    event(conn, "tool.receipt", task, data={"operation": operation, "receipt_id": receipt_id})
    return result


def steer(conn, task, prompt, *, source='owner'):
    if task["status"] in TERMINAL | STOPPING:
        fail(409, "A settled or stopped task cannot be steered")
    conn.execute("UPDATE controls SET status='discarded',updated_at=now() WHERE task_id=%s AND source='monitor' AND status='pending'", (task['id'],))
    event(conn, "task.steer_requested", task, data={"prompt": prompt})
    if source == 'owner' and task.get('orchestration_version') == 2 and task['work_item_id']:
        intake = create_task(conn, bot_id=task['bot_id'], conversation_id=task['conversation_id'], prompt=prompt,
                             request_key='owner-direction:' + str(uuid4()), context=task['context'],
                             message_intent='auto', orchestration_version=2, coordination_version=task.get('coordination_version', 0))
        conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s',
                     (Jsonb({'owner_work_target': str(task['work_item_id'])}), intake['id']))
    else:
        conn.execute("INSERT INTO controls(id,task_id,input,source) VALUES (%s,%s,%s,%s)", (uuid4(), task["id"], ('Owner direction: ' if source == 'owner' else 'Authorized manager direction: ') + prompt, source))
