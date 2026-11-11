"""Durable, versioned owner attention. Projection never advances the underlying work."""

from uuid import uuid4

from psycopg.types.json import Jsonb

from . import db, service, work_items
from .owner_requests import valid

PROBLEMS = {"uncertain", "unresolved", "needs_attention", "paused_budget", "failed", "interrupted"}
SOURCE_TABLES = {
    "routine": "routine_definitions",
    "trigger": "event_trigger_definitions",
    "source": "event_sources",
    "connection": "connections",
}
PUBLIC_COLUMNS = "id,subject_type,subject_id,purpose,category,version,read_version,dismissed_version,active,needs_owner,title,summary,source_status,refs,expires_at,created_at,updated_at"


def dirty(conn, kind, identity):
    conn.execute("SELECT attention_dirty(%s,%s)", (kind, identity))


def materialize(
    conn,
    kind,
    identity,
    purpose,
    *,
    category,
    title,
    summary,
    status,
    refs,
    needs_owner=False,
    expires_at=None,
    fingerprint=None,
    initial=False,
):
    fingerprint = service.digest(
        fingerprint
        or {"title": title, "summary": summary, "status": status, "needs_owner": needs_owner, "refs": refs}
    )
    old = conn.execute(
        "SELECT * FROM attention_items WHERE subject_type=%s AND subject_id=%s AND purpose=%s",
        (kind, identity, purpose),
    ).fetchone()
    if old and old["fingerprint"] == fingerprint and old["active"]:
        if old["pruned_at"]:
            return old
        if (
            old["refs"] != refs
            or old["title"] != title[:180]
            or old["summary"] != summary[:700]
            or old["expires_at"] != expires_at
        ):
            return conn.execute(
                "UPDATE attention_items SET refs=%s,title=%s,summary=%s,expires_at=%s WHERE id=%s RETURNING *",
                (Jsonb(refs), title[:180], summary[:700], expires_at, old["id"]),
            ).fetchone()
        return old
    version = old["version"] + 1 if old else 1
    item = conn.execute(
        """INSERT INTO attention_items(id,subject_type,subject_id,purpose,category,fingerprint,
        version,read_version,active,needs_owner,title,summary,source_status,refs,expires_at)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(subject_type,subject_id,purpose) DO UPDATE SET fingerprint=excluded.fingerprint,
        version=attention_items.version+1,active=true,needs_owner=excluded.needs_owner,title=excluded.title,
        summary=excluded.summary,source_status=excluded.source_status,refs=excluded.refs,
        expires_at=excluded.expires_at,updated_at=now(),pruned_at=NULL RETURNING *""",
        (
            old["id"] if old else uuid4(),
            kind,
            identity,
            purpose,
            category,
            fingerprint,
            version,
            version if initial and not needs_owner else 0,
            needs_owner,
            title[:180],
            summary[:700],
            status,
            Jsonb(refs),
            expires_at,
        ),
    ).fetchone()
    if not initial:
        from .notifications import enqueue

        enqueue(conn, item)
    db.event(conn, "attention.changed", data={"id": str(item["id"]), "version": item["version"]})
    return item


def closure_status(conn, kind, identity, purpose):
    if kind != "task" or not purpose.startswith(("questions:", "reviews:")):
        return "resolved"
    task = conn.execute("SELECT * FROM tasks WHERE id=%s", (identity,)).fetchone()
    table = "input_requests" if purpose.startswith("questions:") else "approvals"
    request = conn.execute(
        f"SELECT status,expires_at FROM {table} WHERE id=%s", (purpose.split(":")[1],)
    ).fetchone()
    if request and request["status"] != "pending":
        return request["status"]
    if request and request["expires_at"] <= service.now():
        return "expired"
    if task and task["status"] in service.TERMINAL | service.STOPPING:
        return task["status"]
    if task:
        chat = conn.execute(
            "SELECT archived_at FROM conversations WHERE id=%s", (task["conversation_id"],)
        ).fetchone()
        if chat and chat["archived_at"]:
            return "archived"
    return "no_longer_actionable"


def close_missing(conn, kind, identity, purposes):
    rows = conn.execute(
        "SELECT id,purpose FROM attention_items WHERE subject_type=%s AND subject_id=%s AND active AND purpose<>ALL(%s)",
        (kind, identity, list(purposes)),
    ).fetchall()
    for row in rows:
        status = closure_status(conn, kind, identity, row["purpose"])
        conn.execute(
            "UPDATE attention_items SET active=false,needs_owner=false,source_status=%s,updated_at=now() WHERE id=%s AND active",
            (status, row["id"]),
        )
    if rows:
        db.event(conn, "attention.changed", data={"resolved": len(rows)})


def task_projection(conn, identity, *, initial=False):
    task = conn.execute("SELECT * FROM tasks WHERE id=%s", (identity,)).fetchone()
    if not task:
        close_missing(conn, "task", identity, [])
        return
    refs = {
        "task_id": str(identity),
        "conversation_id": str(task["conversation_id"]),
        "work_item_id": str(task.get("management_work_item_id") or task["work_item_id"])
        if task.get("management_work_item_id") or task["work_item_id"]
        else None,
        "thread_id": task["reply_to_message_id"],
        "bot_id": task["bot_id"],
        "bot_name": conn.execute("SELECT name FROM bots WHERE id=%s", (task["bot_id"],)).fetchone()["name"],
    }
    current = []
    for table, category, name in [
        ("input_requests", "questions", "Question"),
        ("approvals", "reviews", "Review"),
    ]:
        requests = conn.execute(
            f"SELECT * FROM {table} WHERE task_id=%s AND status='pending'", (identity,)
        ).fetchall()
        for request in requests:
            purpose = category + ":" + str(request["id"])
            if not valid(conn, request, task, review=table == "approvals"):
                continue
            current.append(purpose)
            expiry = min(request["expires_at"], task["deadline"] or request["expires_at"])
            if task.get("grant_id"):
                grant = conn.execute(
                    "SELECT expires_at FROM grants WHERE id=%s", (task["grant_id"],)
                ).fetchone()
                if grant:
                    expiry = min(expiry, grant["expires_at"])
            text = (
                request["prompt"]
                if table == "input_requests"
                else {
                    "runtime": "Review a proposed computer or command action.",
                    "connector": "Review a connected app action.",
                    "lifecycle": "Review teammate changes.",
                    "skill": "Review a proposed skill.",
                    "memory": "Review proposed memory.",
                    "input": "Provide secure input using the existing review.",
                }.get(request["kind"], "Inspect the proposed action before deciding.")
            )
            materialize(
                conn,
                "task",
                identity,
                purpose,
                category=category,
                title=f"{name} needs your response",
                summary=text,
                status="pending",
                needs_owner=True,
                expires_at=expiry,
                refs=refs | {"request_id": str(request["id"])},
                initial=initial,
                fingerprint={
                    "hash": request.get("question_hash", request.get("payload_hash")),
                    "status": "pending",
                },
            )
    canonical = task.get("management_work_item_id") or task["work_item_id"]
    if canonical:
        project(conn, "work", canonical)
    elif not task["parent_id"] and task["message_intent"] in ("assignment", "update"):
        if task["status"] in PROBLEMS:
            current.append("problem")
            materialize(
                conn,
                "task",
                identity,
                "problem",
                category="problems",
                title="Work needs attention",
                summary="Inspect the current work and recovery controls.",
                status=task["status"],
                refs=refs,
                needs_owner=True,
                initial=initial,
            )
        elif task["status"] == "completed":
            current.append("result")
            materialize(
                conn,
                "task",
                identity,
                "result",
                category="results",
                title="Reply available",
                summary="The teammate finished this reply.",
                status="completed",
                refs=refs,
                fingerprint={"output": service.digest(task.get("output") or "")},
                initial=initial,
            )
    if task["id"] == task["root_id"]:
        # A message given up by this work stops needing the owner once the work is handled.
        for message in conn.execute(
            "SELECT m.id FROM teammate_messages m JOIN tasks t ON t.id=m.source_task_id WHERE t.root_id=%s AND m.released='failed' AND m.owner_action IS NULL",
            (identity,),
        ).fetchall():
            project(conn, "message", message["id"])
    close_missing(conn, "task", identity, current)


def work_projection(conn, identity, *, initial=False):
    items = work_items.list_items(conn, identities=[identity], canonical=True)
    current = []
    if items:
        item = items[0]
        chat = conn.execute(
            "SELECT archived_at FROM conversations WHERE id=%s", (item["conversation_id"],)
        ).fetchone()
        task = item["current_task"]
        if chat and not chat["archived_at"] and not item["hidden_at"] and task:
            refs = {
                "work_item_id": str(identity),
                "conversation_id": str(item["conversation_id"]),
                "task_id": str(task["id"]),
                "bot_id": item["bot_id"],
                "thread_id": task["reply_to_message_id"],
            }
            refs["bot_name"] = conn.execute(
                "SELECT name FROM bots WHERE id=%s", (item["bot_id"],)
            ).fetchone()["name"]
            state = item["status"]
            if task["status"] not in service.TERMINAL:
                from fastapi import HTTPException

                try:
                    service.check_grant(conn, task, lock_scope=False)
                except HTTPException:
                    state = "needs_attention"
            # Accepted requirements alone do not imply all relevant execution/effects have settled.
            runs = conn.execute(
                """SELECT id,status FROM tasks WHERE COALESCE(management_work_item_id,work_item_id)=%s
                AND (message_intent IN ('assignment','update') OR management_work_item_id IS NOT NULL OR execution_kind='peer_message')""",
                (identity,),
            ).fetchall()
            unsettled = any(t["status"] not in service.TERMINAL for t in runs)
            effect = conn.execute(
                """SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id
                WHERE COALESCE(t.management_work_item_id,t.work_item_id)=%s
                AND o.status IN ('pending','queued','dispatching','uncertain','unresolved') LIMIT 1""",
                (identity,),
            ).fetchone()
            attempts = conn.execute(
                """SELECT 1 FROM attempts a JOIN tasks t ON t.id=a.task_id
                WHERE COALESCE(t.management_work_item_id,t.work_item_id)=%s AND a.status=ANY(%s) LIMIT 1""",
                (identity, list(service.ACTIVE_ATTEMPTS) + ["reserved"]),
            ).fetchone()
            if state == "completed" and not unsettled and not effect and not attempts:
                progress = item.get("progress", {})
                current.append("result")
                title = "Results ready" if item["outcomes_version"] else "Reply available"
                summary = (
                    f"{item['title']} · {progress.get('fulfilled', 0)} of {progress.get('required', 0)} required results ready"
                    if item["outcomes_version"]
                    else item["title"]
                )
                materialize(
                    conn,
                    "work",
                    identity,
                    "result",
                    category="results",
                    title=title,
                    summary=summary,
                    status=state,
                    refs=refs,
                    initial=initial,
                    fingerprint={
                        "revision": item["revision"],
                        "outcomes": [
                            (str(o["id"]), o["revision"], o["status"], o["evidence_strength"])
                            for o in item.get("outcomes", [])
                        ],
                        "outputs": [str(a["id"]) for a in item["outputs"]],
                    },
                )
            elif state in PROBLEMS:
                current.append("problem")
                materialize(
                    conn,
                    "work",
                    identity,
                    "problem",
                    category="problems",
                    title="Work needs attention",
                    summary=item["title"]
                    + " · "
                    + {
                        "paused_budget": "Active work budget exhausted",
                        "uncertain": "An execution or external outcome needs reconciliation",
                    }.get(state, "Inspect the required results and recovery controls"),
                    status=state,
                    needs_owner=True,
                    refs=refs,
                    initial=initial,
                )
    close_missing(conn, "work", identity, current)


def source_projection(conn, kind, identity, *, initial=False):
    table = SOURCE_TABLES[kind]
    row = conn.execute(f"SELECT * FROM {table} WHERE id=%s", (identity,)).fetchone()
    current = []
    if row:
        status = row.get("state", row.get("status"))
        automatic = bool(row.get("error")) and status not in ("archived", "draft", "finished", "disconnected")
        if kind == "source":
            automatic = row["enabled"] and status in (
                "needs_access",
                "needs_resync",
                "needs_inspection",
                "error",
            )
        if kind == "connection" and status == "revoked":
            automatic = False
        if kind == "source" and row.get("connection_id"):
            upstream = conn.execute(
                "SELECT status FROM connections WHERE id=%s", (row["connection_id"],)
            ).fetchone()
            if upstream and upstream["status"] == "needs_reconnect":
                automatic = False
        if kind == "trigger":
            upstream = conn.execute(
                "SELECT status,enabled FROM event_sources WHERE id=%s", (row["source_id"],)
            ).fetchone()
            if (
                upstream
                and upstream["enabled"]
                and upstream["status"] in ("needs_access", "needs_resync", "needs_inspection")
            ):
                automatic = False
        chat = (
            conn.execute(
                "SELECT archived_at FROM conversations WHERE id=%s", (row["conversation_id"],)
            ).fetchone()
            if row.get("conversation_id")
            else None
        )
        if automatic and not (chat and chat["archived_at"]):
            name = row.get("name") or kind.capitalize()
            if kind in ("routine", "trigger"):
                revisions = "routine_revisions" if kind == "routine" else "event_trigger_revisions"
                definition = conn.execute(
                    f"SELECT definition->>'name' AS name FROM {revisions} WHERE {kind}_id=%s AND revision=%s",
                    (identity, row["revision"]),
                ).fetchone()
                if definition:
                    name = definition["name"] or name
            current.append("setup")
            # Error bodies can contain provider content; keep operational previews fixed.
            materialize(
                conn,
                kind,
                identity,
                "setup",
                category="problems",
                title=f"{name} needs attention",
                summary="Open setup or history to inspect access and recovery.",
                status=status,
                needs_owner=True,
                refs={
                    "settings": "connections" if kind == "connection" else "routines",
                    "source_id": str(identity),
                    "source_type": kind,
                },
                initial=initial,
                fingerprint={
                    "status": status,
                    "reason": service.digest(row.get("error") or ""),
                    "revision": row.get("revision"),
                },
            )
    close_missing(conn, kind, identity, current)


def message_projection(conn, identity, *, initial=False):
    """A teammate message given up after repeated failures is reported once, with fixed text (no error content)."""
    message = conn.execute("SELECT * FROM teammate_messages WHERE id=%s", (identity,)).fetchone()
    current = []
    if message and message["status"] == "cancelled" and message["released"] == "failed" and not message["owner_action"]:
        from .coordination import sender_work_open

        # "Needs you" only while the sender's work is in progress and resending can still help; once that work
        # finishes or is stopped, it is an update the owner can read or dismiss.
        actionable = sender_work_open(conn, message)
        old = conn.execute(
            "SELECT active,needs_owner FROM attention_items WHERE subject_type='message' AND subject_id=%s AND purpose='undeliverable'",
            (identity,),
        ).fetchone()
        demoted = bool(old and old["active"] and old["needs_owner"] and not actionable)
        names = {
            row["id"]: row["name"]
            for row in conn.execute(
                "SELECT id,name FROM bots WHERE id=ANY(%s)", ([message["sender"], message["recipient"]],)
            ).fetchall()
        }
        sender, recipient = names.get(message["sender"], "A teammate"), names.get(message["recipient"], "a teammate")
        current.append("undeliverable")
        materialize(
            conn,
            "message",
            identity,
            "undeliverable",
            category="problems",
            title=f"A message from {sender} to {recipient} couldn't be delivered",
            summary=(
                f"It failed {message['attempts']} times and was dropped. If {sender} was waiting for the answer, it was told. "
                + ("Resend it, or choose Done if you'll handle it yourself." if actionable else f"{sender}'s work has since finished, so this is for your information.")
            ),
            status="failed",
            needs_owner=actionable,
            refs={
                "task_id": str(message["source_task_id"]),
                "conversation_id": str(message["conversation_id"]),
                "work_item_id": str(message["work_item_id"]) if message["work_item_id"] else None,
                "bot_id": message["sender"],
                "bot_name": sender,
                "message_id": str(identity),
            },
            # Moving out of "Needs you" because the work is handled sends no second notification.
            initial=initial or demoted,
            fingerprint={"released": "failed", "since": str(message["last_error_at"]), "needs_owner": actionable},
        )
    close_missing(conn, "message", identity, current)


def project(conn, kind, identity):
    pref = conn.execute("SELECT initialized_at FROM notification_preferences WHERE owner='owner'").fetchone()
    if kind == "task":
        row = conn.execute("SELECT created_at FROM tasks WHERE id=%s", (identity,)).fetchone()
    elif kind == "work":
        row = conn.execute("SELECT created_at FROM work_items WHERE id=%s", (identity,)).fetchone()
    elif kind == "message":
        row = conn.execute("SELECT created_at FROM teammate_messages WHERE id=%s", (identity,)).fetchone()
    else:
        row = conn.execute(
            f"SELECT created_at FROM {SOURCE_TABLES[kind]} WHERE id=%s", (identity,)
        ).fetchone()
    initial = bool(
        row
        and row["created_at"] < pref["initialized_at"]
        and not conn.execute(
            "SELECT 1 FROM attention_items WHERE subject_type=%s AND subject_id=%s LIMIT 1", (kind, identity)
        ).fetchone()
    )
    if kind == "task":
        task_projection(conn, identity, initial=initial)
    elif kind == "work":
        work_projection(conn, identity, initial=initial)
    elif kind == "message":
        message_projection(conn, identity, initial=initial)
    else:
        source_projection(conn, kind, identity, initial=initial)


def drain(limit=50):
    with db.connection() as conn:
        candidates = conn.execute(
            "SELECT * FROM attention_pending WHERE due_at<=now() ORDER BY due_at,subject_id LIMIT %s",
            (limit,),
        ).fetchall()
    for row in candidates:
        try:
            with db.connection() as conn:
                if not conn.execute("SELECT pg_try_advisory_xact_lock(887832154)").fetchone()[
                    "pg_try_advisory_xact_lock"
                ]:
                    break
                project(conn, row["subject_type"], row["subject_id"])
                conn.execute(
                    "DELETE FROM attention_pending WHERE subject_type=%s AND subject_id=%s AND generation=%s",
                    (row["subject_type"], row["subject_id"], row["generation"]),
                )
                expiry = conn.execute(
                    "SELECT min(expires_at) AS at FROM attention_items WHERE subject_type=%s AND subject_id=%s AND active",
                    (row["subject_type"], row["subject_id"]),
                ).fetchone()["at"]
                if expiry:
                    conn.execute(
                        "INSERT INTO attention_pending(subject_type,subject_id,due_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                        (row["subject_type"], row["subject_id"], expiry),
                    )
        except Exception:
            # A poison subject cannot monopolize the head of the queue; never log private source data.
            with db.connection() as conn:
                conn.execute(
                    "UPDATE attention_pending SET failures=failures+1,due_at=now()+interval '30 seconds' WHERE subject_type=%s AND subject_id=%s AND generation=%s",
                    (row["subject_type"], row["subject_id"], row["generation"]),
                )
                conn.execute(
                    "UPDATE notification_preferences SET worker_error='An attention source could not be refreshed' WHERE owner='owner'"
                )
            raise
    return len(candidates)


def reconcile(conn, limit=100):
    """Level-based paging closes expiries and repairs hints without a sequence watermark."""
    pref = conn.execute(
        "SELECT reconcile_cursor FROM notification_preferences WHERE owner='owner'"
    ).fetchone()
    cursor = pref["reconcile_cursor"]
    kinds = [("task", "tasks"), ("work", "work_items"), *SOURCE_TABLES.items()]
    kind, table = kinds[int(cursor.get("index", 0)) % len(kinds)]
    scope = (
        "(status NOT IN ('completed','failed','cancelled','interrupted') OR updated_at>now()-interval '24 hours' OR EXISTS(SELECT 1 FROM attention_items a WHERE a.subject_type='task' AND a.subject_id=tasks.id AND a.active)) AND "
        if kind == "task"
        else ""
    )
    rows = conn.execute(
        f"SELECT id FROM {table} WHERE {scope}(%s::uuid IS NULL OR id>%s::uuid) ORDER BY id LIMIT %s",
        (cursor.get("id"), cursor.get("id"), limit),
    ).fetchall()
    for row in rows:
        # Do not reset a poison subject's backoff on every reconciliation pass.
        conn.execute(
            "INSERT INTO attention_pending(subject_type,subject_id) VALUES(%s,%s) ON CONFLICT DO NOTHING",
            (kind, row["id"]),
        )
    next_cursor = (
        {"index": int(cursor.get("index", 0)), "id": str(rows[-1]["id"])}
        if len(rows) == limit
        else {"index": (int(cursor.get("index", 0)) + 1) % len(kinds)}
    )
    conn.execute(
        """UPDATE notification_preferences SET reconcile_cursor=%s,heartbeat_at=now(),worker_error=CASE
        WHEN EXISTS(SELECT 1 FROM attention_pending WHERE failures>0) THEN 'An attention source could not be refreshed'
        WHEN EXISTS(SELECT 1 FROM push_subscriptions WHERE enabled AND status='server_key_error') THEN 'Push service rejected the server key or contact; check notification configuration'
        ELSE NULL END WHERE owner='owner'""",
        (Jsonb(next_cursor),),
    )


def maintenance():
    # Each family commits separately: delivery completion locks delivery before subscription.
    # Retention must never hold preference/subscription locks while waiting for that delivery.
    for query in (
        "UPDATE push_subscriptions SET enabled=false,status='expired',secret_encrypted=NULL WHERE enabled AND expires_at<=now()",
        "UPDATE notification_deliveries SET status='expired',updated_at=now() WHERE status IN ('pending','dispatching') AND expires_at<=now()",
        """UPDATE attention_items SET title='',summary='',refs='{}',pruned_at=now()
            WHERE id IN (SELECT id FROM attention_items WHERE (NOT active OR (NOT needs_owner AND read_version>=version)
            OR dismissed_version>=version) AND updated_at<now()-interval '90 days' AND pruned_at IS NULL
            ORDER BY updated_at LIMIT 100)""",
        "DELETE FROM notification_deliveries WHERE id IN (SELECT id FROM notification_deliveries WHERE status NOT IN ('pending','dispatching') AND updated_at<now()-interval '90 days' LIMIT 100)",
    ):
        with db.connection() as conn:
            conn.execute(query)


def page(conn, view="needs", before=None, limit=50):
    # One SQL statement gives list membership and badge counts the same MVCC snapshot.
    result = conn.execute(
        f"""WITH visible AS (SELECT {PUBLIC_COLUMNS},version>read_version AS unread
        FROM attention_items WHERE pruned_at IS NULL), selected AS (
        SELECT * FROM visible WHERE CASE %s WHEN 'needs' THEN active AND needs_owner
        WHEN 'updates' THEN active AND NOT needs_owner AND unread AND dismissed_version<version
        ELSE NOT(active AND needs_owner) AND (NOT active OR NOT unread OR dismissed_version>=version) END
        AND (%s::uuid IS NULL OR (updated_at,id)<(SELECT updated_at,id FROM attention_items WHERE id=%s))
        ORDER BY updated_at DESC,id DESC LIMIT %s)
        SELECT COALESCE((SELECT jsonb_agg(to_jsonb(s) ORDER BY updated_at DESC,id DESC) FROM selected s),'[]') AS items,
        (SELECT count(*) FROM visible WHERE active AND version>read_version AND dismissed_version<version) AS unread,
        (SELECT count(*) FROM visible WHERE active AND needs_owner) AS needs_owner""",
        (view, before, before, limit + 1),
    ).fetchone()
    items = result["items"]
    return {
        **result,
        "items": items[:limit],
        "next": str(items[limit - 1]["id"]) if len(items) > limit else None,
    }


def acknowledge(conn, pairs, action):
    for pair in pairs:
        item = conn.execute("SELECT * FROM attention_items WHERE id=%s FOR UPDATE", (pair.id,)).fetchone()
        if not item:
            continue
        if pair.version != item["version"]:
            continue
        if action == "dismiss" and item["active"] and item["needs_owner"]:
            continue
        if action == "unread":
            conn.execute(
                "UPDATE attention_items SET read_version=LEAST(read_version,%s-1),dismissed_version=0 WHERE id=%s",
                (pair.version, pair.id),
            )
        else:
            conn.execute(
                "UPDATE attention_items SET read_version=%s,read_at=now() WHERE id=%s",
                (pair.version, pair.id),
            )
            if action == "dismiss":
                conn.execute(
                    "UPDATE attention_items SET dismissed_version=%s,dismissed_at=now() WHERE id=%s",
                    (pair.version, pair.id),
                )
    db.event(conn, "attention.read")
