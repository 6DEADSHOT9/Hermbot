"""Managers propose; owners approve exact changes; the worker executes the durable outbox."""
from uuid import UUID, uuid4

from fastapi import HTTPException
from psycopg.types.json import Jsonb

from .execution_policy import review_expiry

from . import db, registry, service


def authority(conn, task, action, target=None, *, consume_creation=True):
    conn.execute("SELECT id FROM grants WHERE id=%s FOR UPDATE", (task.get("grant_id"),)).fetchone()
    grant = service.check_grant(conn, task, operation=action, manager=task["bot_id"])
    if target and target not in grant["targets"]:
        raise HTTPException(403, "Lifecycle target is outside the owner-issued grant")
    if action == "create" and consume_creation and grant["created_bots"] >= grant["max_created_bots"]:
        raise HTTPException(403, "This grant's bot creation budget is exhausted")
    return grant


def prepare(conn, task, args):
    action, target = args["action"], args.get("bot_id")
    if action != "create" and not target:
        raise HTTPException(422, "Select the bot affected by this lifecycle proposal")
    grant = authority(conn, task, action, target)
    if action == "create":
        if target:
            raise HTTPException(422, "The server assigns a new identity to a proposed teammate")
        target = "bot-" + uuid4().hex[:16]
        bot, revision, affected = None, None, []
    else:
        bot = registry.require_bot(conn, target)
        if bot["status"] in {"archived", "deleting"}:
            raise HTTPException(409, "The owner must restore this teammate before manager lifecycle changes")
        revision = bot["revision"]
        affected = registry.busy_tasks(conn, target)
    config = None
    if action in ("create", "configure"):
        if not args.get("configuration"):
            raise HTTPException(422, "Provide the complete proposed bot configuration")
        config = registry.validate_config(conn, args["configuration"], existing=bot)
        if config["memory_mb"] > grant["max_memory_mb"] or config["cpus"] > grant["max_cpus"]:
            raise HTTPException(403, "Proposed runtime exceeds the grant's resource limits")
    elif args.get("configuration"):
        raise HTTPException(422, "Runtime start/stop proposals cannot change configuration")
    if affected and not args.get("interrupt"):
        raise HTTPException(409, "Target is busy. Wait or explicitly propose interruption of the affected work")
    if any(t["status"] == "uncertain" for t in affected):
        raise HTTPException(409, "Reconcile uncertain execution before proposing lifecycle changes")
    provider_id = config["provider_id"] if config else bot["provider_id"]
    provider = conn.execute("SELECT id,name,revision,base_url FROM providers WHERE id=%s", (provider_id,)).fetchone()
    return db.jsonable_encoder({"action": action, "bot_id": target, "summary": args["summary"], "configuration": config,
        "before": registry.configuration(bot) if bot else None, "expected_revision": revision,
        "provider": provider, "resources": {"memory_mb": config["memory_mb"] if config else bot["memory_mb"], "cpus": config["cpus"] if config else bot["cpus"]},
        "interrupt": bool(args.get("interrupt")), "affected_task_ids": [str(t["id"]) for t in affected]})


def propose(conn, task, args):
    payload = prepare(conn, task, args)
    proposal_id, approval_id = uuid4(), uuid4()
    conn.execute("INSERT INTO approvals(id,task_id,kind,payload,payload_hash,expires_at) VALUES (%s,%s,'lifecycle',%s,%s,%s)", (approval_id, task["id"], Jsonb(payload), service.digest(payload), review_expiry(task)))
    conn.execute("INSERT INTO lifecycle_proposals(id,task_id,grant_id,approval_id,action,bot_id,payload,expected_revision) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (proposal_id, task["id"], task["grant_id"], approval_id, payload["action"], payload["bot_id"], Jsonb(payload), payload["expected_revision"]))
    conn.execute("UPDATE tasks SET status='waiting_approval' WHERE id=%s", (task["id"],))
    db.event(conn, "lifecycle.proposed", task, data={"proposal_id": str(proposal_id), "approval_id": str(approval_id), "action": payload["action"], "bot_id": payload["bot_id"]})
    return {"proposal_id": str(proposal_id), "approval_id": str(approval_id), "status": "waiting_approval", "instruction": "End your turn. The owner must approve the exact changes; you will be resumed only after rejection or runtime provisioning settles."}


def resume(conn, proposal, text):
    task = service.locked_task(conn, proposal["task_id"])
    if task["status"] in service.TERMINAL | service.STOPPING:
        return
    service.add_continuation(conn, task["id"], text)
    if not conn.execute("SELECT 1 FROM attempts WHERE task_id=%s AND status=ANY(%s)", (task["id"], list(service.ACTIVE_ATTEMPTS))).fetchone():
        conn.execute("UPDATE tasks SET status='queued' WHERE id=%s", (task["id"],))


def decide(conn, approval, task, decision):
    proposal = conn.execute("SELECT * FROM lifecycle_proposals WHERE approval_id=%s FOR UPDATE", (approval["id"],)).fetchone()
    if not proposal or proposal["status"] != "pending":
        raise HTTPException(409, "Proposal has already changed or settled")
    if decision == "deny":
        conn.execute("UPDATE lifecycle_proposals SET status='denied' WHERE id=%s", (proposal["id"],))
        resume(conn, proposal, f"Owner denied lifecycle proposal {proposal['id']}. No change was authorized or performed.")
        return
    payload = proposal["payload"]
    grant = authority(conn, task, proposal["action"], None if proposal["action"] == "create" else proposal["bot_id"])
    if proposal['action'] in ('create', 'configure'):
        config = payload['configuration']
        if config['memory_mb'] > grant['max_memory_mb'] or config['cpus'] > grant['max_cpus']:
            raise HTTPException(403, "Proposed runtime exceeds the grant's resource limits")
    provider = conn.execute("SELECT revision FROM providers WHERE id=%s", (UUID(payload["provider"]["id"]),)).fetchone()
    if not provider or provider["revision"] != payload["provider"]["revision"]:
        raise HTTPException(409, "The model provider changed. Request a fresh proposal")
    if proposal["action"] == "create":
        chat = conn.execute("SELECT kind FROM conversations WHERE id=%s FOR UPDATE", (task["conversation_id"],)).fetchone()
        if chat["kind"] == "group" and conn.execute("SELECT count(*) AS n FROM participants WHERE conversation_id=%s", (task["conversation_id"],)).fetchone()["n"] >= 6:
            raise HTTPException(409, "This group already has six teammates; edit membership before adding a specialist")
        config = registry.validate_config(conn, payload["configuration"])
        bot = registry.register(conn, config, proposal["bot_id"])
        conn.execute("UPDATE grants SET created_bots=created_bots+1,targets=targets||%s WHERE id=%s", (Jsonb([bot["id"]]), grant["id"]))
        conn.execute("INSERT INTO participants VALUES (%s,%s) ON CONFLICT DO NOTHING", (task["conversation_id"], bot["id"]))
    else:
        bot = registry.require_bot(conn, proposal["bot_id"], lock=True, nowait=True)
        if bot["revision"] != proposal["expected_revision"]:
            raise HTTPException(409, "Target configuration changed. Request a fresh proposal")
        affected = [str(t["id"]) for t in registry.busy_tasks(conn, bot["id"])]
        if affected != payload["affected_task_ids"]:
            raise HTTPException(409, "Affected work changed. Review a fresh proposal")
        registry.reserve_lifecycle(conn, bot, payload["interrupt"])
        if proposal["action"] == "configure":
            registry.apply_configuration(conn, bot["id"], registry.validate_config(conn, payload["configuration"], existing=bot))
        else:
            conn.execute("UPDATE bots SET desired_state=%s,revision=revision+1 WHERE id=%s", ("stopped" if proposal["action"] == "stop" else "running", bot["id"]))
        bot = registry.require_bot(conn, bot["id"])
    operation = registry.enqueue(conn, bot, proposal["action"], f"proposal:{proposal['id']}", proposal_id=proposal["id"])
    conn.execute("UPDATE lifecycle_proposals SET status='queued',operation_id=%s WHERE id=%s", (operation["id"], proposal["id"]))


def replace(conn, proposal_id, args):
    old = conn.execute("SELECT * FROM lifecycle_proposals WHERE id=%s", (proposal_id,)).fetchone()
    if not old:
        raise HTTPException(404, "Proposal not found")
    task = service.locked_task(conn, old["task_id"])
    old = conn.execute("SELECT * FROM lifecycle_proposals WHERE id=%s FOR UPDATE", (proposal_id,)).fetchone()
    if old["status"] != "pending":
        raise HTTPException(409, "Only a pending proposal can be edited")
    service.check_grant(conn, task)
    if task["status"] in service.TERMINAL | service.STOPPING:
        raise HTTPException(409, "This operation was stopped")
    conn.execute("UPDATE lifecycle_proposals SET status='superseded' WHERE id=%s", (proposal_id,))
    conn.execute("UPDATE approvals SET status='stale' WHERE id=%s", (old["approval_id"],))
    return propose(conn, task, args)
