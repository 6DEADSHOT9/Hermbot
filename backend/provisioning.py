"""Durable, leased lifecycle operations; exact approved snapshots survive worker restart."""
import base64
import logging
from random import random
from uuid import uuid4

import httpx
from psycopg.types.json import Jsonb

from . import db, isolation, lifecycle, registry, service
from .secrets import decrypt
from .settings import settings
from .model_protocol import BROKERED_AUTH

logger = logging.getLogger("hermbot.provisioning")
WORKER_ID = str(uuid4())


async def supervisor(method, path, **kwargs):
    async with httpx.AsyncClient(timeout=100) as client:
        result = await client.request(method, "http://supervisor:8001" + path, headers={"Authorization": "Bearer " + settings.supervisor_token}, **kwargs)
        result.raise_for_status()
        return result.json()


def queue_initial():
    with db.connection() as conn:
        bots = conn.execute("SELECT * FROM bots WHERE status='active' AND applied_revision<revision ORDER BY id FOR UPDATE SKIP LOCKED").fetchall()
        for bot in bots:
            if not registry.busy_tasks(conn, bot["id"]) and not conn.execute("SELECT 1 FROM bot_operations WHERE bot_id=%s AND status IN ('queued','applying','uncertain')", (bot["id"],)).fetchone():
                registry.enqueue(conn, bot, "ensure", f"bootstrap:{bot['id']}:{bot['revision']}")


def claim():
    with db.connection() as conn:
        rows = conn.execute("UPDATE bot_operations SET worker_id=%s,lease_until=now()+interval '120 seconds' WHERE id IN (SELECT id FROM bot_operations WHERE status IN ('queued','applying') AND (worker_id=%s OR lease_until<now() OR lease_until IS NULL) ORDER BY created_at FOR UPDATE SKIP LOCKED) RETURNING *,(drive_not_before IS NULL OR drive_not_before<=now()) AS due", (WORKER_ID, WORKER_ID)).fetchall()
    return [row for row in rows if row["due"]]


def fail(operation_id, reason):
    """Record a failed operation; the owner can retry the same operation explicitly."""
    with db.connection() as conn:
        current = conn.execute("SELECT * FROM bot_operations WHERE id=%s FOR UPDATE", (operation_id,)).fetchone()
        if current["status"] not in ("queued", "applying"):
            return
        proposal = conn.execute("SELECT * FROM lifecycle_proposals WHERE id=%s", (current["proposal_id"],)).fetchone() if current["proposal_id"] else None
        conn.execute("UPDATE bot_operations SET status='failed',error=%s,updated_at=now() WHERE id=%s", (reason, current["id"]))
        conn.execute("UPDATE bots SET status='error',runtime_error=%s WHERE id=%s", (reason, current["bot_id"]))
        if proposal:
            conn.execute("UPDATE lifecycle_proposals SET status='failed' WHERE id=%s", (proposal["id"],))
            lifecycle.resume(conn, proposal, f"Approved lifecycle operation failed: {reason}. Report the blocker; no successful provisioning is implied.")
        db.event(conn, "bot.operation.failed", bot_id=current["bot_id"], data={"operation_id": str(current["id"]), "error": reason})


async def guarded_drive(operation):
    try:
        await drive(operation)
    except Exception as exc:
        if isolation.database_lost(exc):
            raise
        logger.exception("Lifecycle operation %s failed unexpectedly", operation["id"])
        counted = not isolation.unavailable(exc)
        with db.connection() as conn:
            row = conn.execute(
                "UPDATE bot_operations SET drive_failures=drive_failures+%s,last_drive_error=%s,drive_not_before=now()+make_interval(secs=>%s) WHERE id=%s RETURNING drive_failures",
                (int(counted), isolation.diagnostic(exc), isolation.backoff_seconds(operation["drive_failures"]) * (0.5 + random() / 2), operation["id"]),
            ).fetchone()
        if counted and row and row["drive_failures"] >= isolation.FAILURE_LIMIT:
            fail(operation["id"], f"Hermbot could not apply this operation: the same failure repeated {row['drive_failures']} times ({isolation.describe(exc)}). Check the runtime before retrying")
        return
    if operation["drive_failures"]:
        with db.connection() as conn:
            conn.execute("UPDATE bot_operations SET drive_failures=0,drive_not_before=NULL WHERE id=%s", (operation["id"],))


async def drive(operation):
    with db.connection() as conn:
        current = conn.execute("SELECT * FROM bot_operations WHERE id=%s FOR UPDATE", (operation["id"],)).fetchone()
        if current["status"] not in ("queued", "applying"):
            return
        payload = current["payload"]
        bot_ids = [current["bot_id"]] + ([payload["source_bot_id"]] if payload.get("source_bot_id") else [])
        if conn.execute("SELECT 1 FROM attempts WHERE bot_id=ANY(%s) AND status=ANY(%s)", (bot_ids, list(service.ACTIVE_ATTEMPTS) + ["reserved"])).fetchone():
            return
        proposal = conn.execute("SELECT * FROM lifecycle_proposals WHERE id=%s", (current["proposal_id"],)).fetchone() if current["proposal_id"] else None
        if proposal and current["status"] == "queued":
            task = service.locked_task(conn, proposal["task_id"])
            grant = conn.execute("SELECT * FROM grants WHERE id=%s", (proposal["grant_id"],)).fetchone()
            if task["status"] in service.TERMINAL | service.STOPPING or grant["revoked_at"] or grant["expires_at"] <= service.now():
                conn.execute("UPDATE bot_operations SET status='cancelled',error='Authorization ended before dispatch' WHERE id=%s", (current["id"],))
                conn.execute("UPDATE bots SET status='error',runtime_error='Approved operation cancelled before dispatch' WHERE id=%s", (current["bot_id"],))
                conn.execute("UPDATE lifecycle_proposals SET status='cancelled' WHERE id=%s", (proposal["id"],))
                return
        conn.execute("UPDATE bot_operations SET status='applying',updated_at=now() WHERE id=%s", (current["id"],))
        if proposal:
            conn.execute("UPDATE lifecycle_proposals SET status='applying' WHERE id=%s", (proposal["id"],))
    provider = payload.get("provider") or {}
    oauth = provider.get('auth_kind') in BROKERED_AUTH
    spec = {"bot_id": current["bot_id"], "operation_id": str(current["id"]), "revision": payload["revision"],
        "configuration": payload["configuration"], "model_base_url": 'http://api:8000/internal/inference/v1' if oauth else provider.get("base_url", "http://127.0.0.1:1/v1"),
        "model_api_key": decrypt(provider.get('inference_key_encrypted') if oauth else provider.get("secret_encrypted")), "model_auth_kind": provider['auth_kind'] if oauth else 'api_key', "bot_token": decrypt(payload["bot_token_encrypted"]),
        "runtime_key": decrypt(payload["runtime_key_encrypted"]), "timezone": payload["timezone"], "desired_state": payload["desired_state"],
        "source_bot_id": payload.get("source_bot_id"), "source_was_running": payload.get("source_was_running", False), "copy_options": payload.get("copy_options", {})}
    try:
        result = await supervisor("POST", f"/operations/{current['action']}", json=spec)
    except httpx.HTTPStatusError as exc:
        # The same operation remains available for explicit retry after deterministic failures.
        detail = exc.response.json().get("detail", "") if exc.response.headers.get("content-type", "").startswith("application/json") else ""
        reason = f"Runtime provisioning failed (HTTP {exc.response.status_code}): " + (detail[:500] if isinstance(detail, str) and detail else "check image availability and Docker resources")
        fail(current["id"], reason)
        return
    except httpx.HTTPError:
        logger.info("Lifecycle operation %s has no transport receipt; reconcile using the same operation", current["id"])
        return
    with db.connection() as conn:
        current = conn.execute("SELECT * FROM bot_operations WHERE id=%s FOR UPDATE", (current["id"],)).fetchone()
        if current["status"] == "completed":
            return
        conn.execute("UPDATE bot_operations SET status='completed',result=%s,updated_at=now() WHERE id=%s", (Jsonb(result), current["id"]))
        status = "deleted" if current["action"] == "delete" else ("archived" if current["action"] == "archive" else "active")
        if current["action"] in {"configure", "ensure"} and payload.get("previous_status") == "archived":
            status = "archived"
        conn.execute("UPDATE bots SET status=%s,applied_revision=%s,runtime_error=NULL,readiness='{}' WHERE id=%s", (status, payload["revision"], current["bot_id"]))
        if status == "deleted":
            conn.execute("UPDATE bots SET bot_token_hash=NULL,bot_token_encrypted=NULL,runtime_key_encrypted=NULL WHERE id=%s", (current["bot_id"],))
            conn.execute("DELETE FROM participants WHERE bot_id=%s", (current["bot_id"],))
        if proposal:
            conn.execute("UPDATE lifecycle_proposals SET status='completed' WHERE id=%s", (proposal["id"],))
            lifecycle.resume(conn, proposal, f"Owner-approved lifecycle proposal {proposal['id']} completed. Bot ID: {current['bot_id']}. Action: {current['action']}. Runtime state: {result['state']}. Use hermbot_teammates to discover the specialist, delegate permitted work and combine the result.")
        db.event(conn, "bot.operation.completed", bot_id=current["bot_id"], data={"operation_id": str(current["id"]), "action": current["action"], "state": result["state"]})


async def copy_attachments(attempt):
    from .artifacts import ArtifactStore
    with db.connection() as conn:
        task = conn.execute("SELECT attachment_ids FROM tasks WHERE id=%s", (attempt["task_id"],)).fetchone()
        rows = [conn.execute("SELECT * FROM attachments WHERE id=%s", (attachment_id,)).fetchone() for attachment_id in task["attachment_ids"]]
        rows += conn.execute('SELECT a.* FROM artifacts a JOIN artifact_access g ON g.artifact_id=a.id WHERE g.task_id=%s', (attempt['task_id'],)).fetchall()
    if not rows:
        return
    store = ArtifactStore()
    for attachment in rows:
        data = store.read_bytes(attachment)
        await supervisor("POST", "/attachments", json={"bot_id": attempt["bot_id"], "attachment_id": str(attachment["id"]), "name": attachment["name"], "sha256": attachment["sha256"], "content_base64": base64.b64encode(data).decode()})
