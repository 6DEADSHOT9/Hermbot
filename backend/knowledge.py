"""Owner knowledge edits with native compare-and-swap, immutable versions and runtime refresh."""

import hashlib
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import Field
from psycopg.types.json import Jsonb

from .execution_policy import expired

from . import db, native, registry, service
from .models import BotId, StrictModel


class KnowledgeRequest(StrictModel):
    content: str = Field(max_length=100_000)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    enable: bool | None = None
    source_task_id: UUID | None = None


class SkillEnableRequest(StrictModel):
    enabled: bool
    expected_revision: int = Field(ge=1)


def validate_name(kind, name):
    if kind == "memory" and name not in ("memory", "user"):
        raise HTTPException(422, "Choose memory or user profile")
    if kind == "skill" and (
        not isinstance(name, str) or not re.fullmatch(r"hermbot-[a-z0-9][a-z0-9-]{0,70}", name)
    ):
        raise HTTPException(
            422,
            "Owner-managed skill names must start with hermbot- and use lowercase letters, numbers and hyphens",
        )
    if kind not in ("memory", "skill"):
        raise HTTPException(422, "Choose memory or skill")


def guard(conn, bot_id, *, review_task_id=None):
    bot = registry.require_bot(conn, bot_id, lock=True, nowait=True)
    if registry.pending_operation(conn, bot_id):
        raise HTTPException(409, "Wait for runtime provisioning before editing knowledge")
    busy = registry.busy_tasks(conn, bot_id)
    if (
        any(t["id"] != review_task_id for t in busy)
        or conn.execute(
            "SELECT 1 FROM attempts WHERE bot_id=%s AND status=ANY(%s)",
            (bot_id, list(service.ACTIVE_ATTEMPTS) + ["reserved"]),
        ).fetchone()
    ):
        raise HTTPException(
            409, "Wait for native execution and queued work to settle before changing knowledge"
        )
    if conn.execute(
        "SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (bot_id,)
    ).fetchone():
        raise HTTPException(409, "Hand back this computer before editing its knowledge")
    if bot["status"] != "active" or bot["desired_state"] != "running":
        raise HTTPException(409, "Start this teammate before editing its native knowledge")
    return bot


def apply(conn, bot, kind, name, body, key):
    validate_name(kind, name)
    if body.source_task_id:
        source = conn.execute("SELECT * FROM tasks WHERE id=%s", (body.source_task_id,)).fetchone()
        if not source or source["status"] != "completed":
            raise HTTPException(409, "Save workflows only from completed work")
    if kind == "memory" and body.enable is not None:
        raise HTTPException(422, "Memory drafts cannot change tool permissions")
    if kind == "skill" and body.enable and "skills" not in registry.workspace(conn)["permitted_tools"]:
        raise HTTPException(403, "The workspace has not permitted the skills tool")
    config = registry.configuration(bot)
    if kind == "skill":
        if body.enable is True and body.content:
            config["enabled_tools"] = sorted(set(config["enabled_tools"] + ["skills"]))
            config["enabled_skills"] = sorted(set(config["enabled_skills"] + [name]))
        if body.enable is False or not body.content:
            config["enabled_skills"] = [s for s in config["enabled_skills"] if s != name]
    validated = registry.validate_config(conn, config)
    result = native.request(
        bot["id"],
        "PUT",
        ("/memory/" if kind == "memory" else "/skills/") + name,
        json={"content": body.content, "expected_hash": body.expected_hash},
    )
    head = conn.execute(
        "SELECT * FROM knowledge_versions WHERE bot_id=%s AND kind=%s AND name=%s ORDER BY version DESC LIMIT 1",
        (bot["id"], kind, name),
    ).fetchone()
    content_hash = hashlib.sha256(body.content.encode()).hexdigest()
    if not head or head["sha256"] != content_hash:
        head = conn.execute(
            "INSERT INTO knowledge_versions(id,bot_id,kind,name,version,content,sha256,source_task_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                uuid4(),
                bot["id"],
                kind,
                name,
                head["version"] + 1 if head else 1,
                body.content,
                content_hash,
                body.source_task_id,
            ),
        ).fetchone()
    registry.apply_configuration(conn, bot["id"], validated)
    operation = registry.enqueue(conn, registry.require_bot(conn, bot["id"]), "configure", key)
    db.event(
        conn,
        "knowledge.saved",
        bot_id=bot["id"],
        data={"kind": kind, "name": name, "version": head["version"]},
    )
    return {"version": head, "native": result, "operation": operation}


def proposal_payload(conn, task, kind, payload):
    allowed = {"bot_id", "name", "content", "enable"}
    if set(payload) - allowed or not isinstance(payload.get("content"), str):
        raise HTTPException(422, "Knowledge proposals need exact name/content and optional bot_id/enable")
    bot_id = payload.get("bot_id") or task["bot_id"]
    if not isinstance(bot_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", bot_id):
        raise HTTPException(422, "Use a valid target bot ID")
    if bot_id != task["bot_id"]:
        service.check_grant(conn, task, target=bot_id, operation="configure", manager=task["bot_id"])
    bot = registry.require_bot(conn, bot_id)
    name = payload.get("name", "")
    validate_name(kind, name)
    content = payload["content"]
    if len(content) > 100_000 or (kind == "memory" and "enable" in payload):
        raise HTTPException(422, "Invalid knowledge proposal content or permissions")
    if "enable" in payload and not isinstance(payload["enable"], bool):
        raise HTTPException(422, "Skill enable must be true or false")
    if registry.pending_operation(conn, bot_id):
        raise HTTPException(409, "Wait for runtime provisioning before proposing knowledge")
    current = native.request(bot_id, "GET", "/memory" if kind == "memory" else "/skills/" + name)
    if kind == "memory":
        current = next(v for v in current["targets"] if v["name"] == name)
        if len(content) > current["limit"]:
            raise HTTPException(413, "Memory draft exceeds the native character limit")
    return {
        **payload,
        "bot_id": bot_id,
        "name": name,
        "expected_hash": current["sha256"],
        "expected_revision": bot["revision"],
    }


def record_proposal(conn, task, approval_id, payload):
    conn.execute(
        "INSERT INTO knowledge_proposals(id,task_id,approval_id,bot_id,payload) VALUES(%s,%s,%s,%s,%s)",
        (uuid4(), task["id"], approval_id, payload["bot_id"], Jsonb(payload)),
    )


def decide(conn, approval, task, decision):
    status = "queued" if decision == "once" else ("cancelled" if decision == "cancel" else "denied")
    conn.execute(
        "UPDATE knowledge_proposals SET status=%s,updated_at=now() WHERE approval_id=%s",
        (status, approval["id"]),
    )
    if decision != "once":
        resume(
            conn,
            task,
            "Owner declined this knowledge change. Do not apply it or re-propose it without new owner directions.",
        )


def resume(conn, task, guidance):
    conn.execute("UPDATE tasks SET continuation=%s WHERE id=%s", (guidance, task["id"]))
    if not conn.execute(
        "SELECT 1 FROM attempts WHERE task_id=%s AND status=ANY(%s)",
        (task["id"], list(service.ACTIVE_ATTEMPTS)),
    ).fetchone():
        conn.execute(
            "UPDATE tasks SET status='queued' WHERE id=%s AND status='waiting_approval'", (task["id"],)
        )


def drive_proposals():
    # A transaction holds the root, proposal and target bot while admitting the exact approved
    # change. Native compare-and-swap makes a lost commit replay safe. Runtime refresh is a
    # separate durable operation; task continuation waits for its definitive receipt.
    with db.connection() as conn:
        ids = conn.execute(
            "SELECT id,task_id FROM knowledge_proposals WHERE status IN ('queued','applying') ORDER BY created_at"
        ).fetchall()
    for row in ids:
        with db.connection() as conn:
            task = service.locked_task(conn, row["task_id"])
            proposal = conn.execute(
                "SELECT * FROM knowledge_proposals WHERE id=%s FOR UPDATE", (row["id"],)
            ).fetchone()
            if proposal["status"] not in ("queued", "applying"):
                continue
            approval = conn.execute(
                "SELECT * FROM approvals WHERE id=%s", (proposal["approval_id"],)
            ).fetchone()
            try:
                if (
                    task["status"] in service.TERMINAL | service.STOPPING
                    or expired(task)
                    or approval["decision"] != "once"
                    or approval["status"] != "decided"
                ):
                    raise HTTPException(409, "Approved work was stopped or expired before application")
                service.check_grant(conn, task)
                if proposal["bot_id"] != task["bot_id"]:
                    service.check_grant(
                        conn, task, target=proposal["bot_id"], operation="configure", manager=task["bot_id"]
                    )
                payload = proposal["payload"]
                operation_id = payload.get("operation_id")
                if operation_id:
                    operation = conn.execute(
                        "SELECT * FROM bot_operations WHERE id=%s", (operation_id,)
                    ).fetchone()
                    if operation["status"] in ("queued", "applying"):
                        continue
                    if operation["status"] != "completed":
                        raise HTTPException(
                            409,
                            "Native knowledge was saved but runtime refresh failed; inspect the bot operation before continuing",
                        )
                    conn.execute(
                        "UPDATE knowledge_proposals SET status='completed',updated_at=now() WHERE id=%s",
                        (row["id"],),
                    )
                    resume(
                        conn,
                        task,
                        "Owner-approved knowledge was saved and the native runtime refreshed. Continue the original work. Saved change: "
                        + str(
                            {
                                "bot_id": proposal["bot_id"],
                                "kind": approval["kind"],
                                "name": payload["name"],
                                "content_hash": hashlib.sha256(payload["content"].encode()).hexdigest(),
                            }
                        ),
                    )
                    db.event(conn, "knowledge.proposal.completed", task, data={"proposal_id": str(row["id"])})
                    continue
                # Do not mutate an active native run or concurrent queued task. Retain queued
                # status instead of reclassifying a temporary conflict as permanent failure.
                try:
                    bot = guard(conn, proposal["bot_id"], review_task_id=task["id"])
                except HTTPException as exc:
                    if exc.status_code == 409:
                        continue
                    raise
                if bot["revision"] != payload["expected_revision"]:
                    raise HTTPException(409, "Target bot changed after this review; create a fresh proposal")
                result = apply(
                    conn,
                    bot,
                    approval["kind"],
                    payload["name"],
                    KnowledgeRequest(
                        content=payload["content"],
                        expected_hash=payload["expected_hash"],
                        enable=payload.get("enable"),
                    ),
                    "knowledge-proposal:" + str(row["id"]),
                )
                payload = {**payload, "operation_id": str(result["operation"]["id"])}
                conn.execute(
                    "UPDATE knowledge_proposals SET status='applying',payload=%s,updated_at=now() WHERE id=%s",
                    (Jsonb(payload), row["id"]),
                )
            except HTTPException as exc:
                if exc.status_code == 503:
                    continue  # Same frozen CAS, no invented retry scope.
                conn.execute(
                    "UPDATE knowledge_proposals SET status='failed',error=%s,updated_at=now() WHERE id=%s",
                    (str(exc.detail), row["id"]),
                )
                if task["status"] not in service.TERMINAL | service.STOPPING:
                    resume(
                        conn,
                        task,
                        "Owner-approved knowledge could not be applied: "
                        + str(exc.detail)
                        + ". Do not apply it yourself; report this failure.",
                    )
                db.event(
                    conn,
                    "knowledge.proposal.failed",
                    task,
                    data={"proposal_id": str(row["id"]), "error": str(exc.detail)},
                )


def routes(owner):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get("/api/v1/bots/{bot_id}/knowledge")
    def inspect(bot_id: BotId):
        memory = native.request(bot_id, "GET", "/memory")
        skills = native.request(bot_id, "GET", "/skills")
        with db.connection() as conn:
            bot = registry.require_bot(conn, bot_id)
            versions = conn.execute(
                "SELECT id,kind,name,version,sha256,source_task_id,created_at FROM knowledge_versions WHERE bot_id=%s ORDER BY created_at DESC",
                (bot_id,),
            ).fetchall()
        return {
            "memory": memory["targets"],
            "skills": [{**s, "enabled": s["name"] in bot["enabled_skills"]} for s in skills["data"]],
            "versions": versions,
        }

    @router.get("/api/v1/bots/{bot_id}/knowledge/skills/{name}")
    def skill(bot_id: BotId, name: str):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", name):
            raise HTTPException(422, "Use a valid installed skill name")
        return native.request(bot_id, "GET", "/skills/" + name)

    @router.post("/api/v1/bots/{bot_id}/knowledge/skills/{name}/enabled", status_code=202)
    def enable(
        bot_id: BotId, name: str, body: SkillEnableRequest, key: str = Header(alias="Idempotency-Key")
    ):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", name):
            raise HTTPException(422, "Use a valid installed skill name")
        with db.connection() as conn:

            def work():
                bot = guard(conn, bot_id)
                if bot["revision"] != body.expected_revision:
                    raise HTTPException(409, "This bot changed; reload before changing its skill selection")
                installed = native.request(bot_id, "GET", "/skills/" + name)
                if body.enabled and not installed["exists"]:
                    raise HTTPException(404, "Install this skill before enabling it")
                config = registry.configuration(bot)
                if body.enabled:
                    config["enabled_tools"] = sorted(set(config["enabled_tools"] + ["skills"]))
                    config["enabled_skills"] = sorted(set(config["enabled_skills"] + [name]))
                else:
                    config["enabled_skills"] = [s for s in config["enabled_skills"] if s != name]
                registry.apply_configuration(conn, bot_id, registry.validate_config(conn, config))
                return {
                    "operation": registry.enqueue(conn, registry.require_bot(conn, bot_id), "configure", key)
                }

            return registry.mutation(
                conn,
                key,
                {"action": "skill-enabled", "bot_id": bot_id, "name": name, "body": body.model_dump()},
                work,
            )

    @router.put("/api/v1/bots/{bot_id}/knowledge/{kind}/{name}", status_code=202)
    def save(
        bot_id: BotId,
        kind: str,
        name: str,
        body: KnowledgeRequest,
        key: str = Header(alias="Idempotency-Key"),
    ):
        validate_name(kind, name)
        with db.connection() as conn:
            return registry.mutation(
                conn,
                key,
                {
                    "action": "knowledge",
                    "bot_id": bot_id,
                    "kind": kind,
                    "name": name,
                    "body": body.model_dump(),
                },
                lambda: apply(conn, guard(conn, bot_id), kind, name, body, key),
            )

    @router.get("/api/v1/bots/{bot_id}/knowledge/versions/{version_id}")
    def version(bot_id: BotId, version_id: UUID):
        with db.connection() as conn:
            item = conn.execute(
                "SELECT * FROM knowledge_versions WHERE id=%s AND bot_id=%s", (version_id, bot_id)
            ).fetchone()
            if not item:
                raise HTTPException(404, "Knowledge version not found")
            return item

    @router.get("/api/v1/tasks/{task_id}/skill-draft")
    def workflow(task_id: UUID):
        with db.connection() as conn:
            task = service.locked_task(conn, task_id)
            if task["status"] != "completed":
                raise HTTPException(409, "This work has not completed")
            steps = conn.execute(
                "SELECT bot_id,input,output FROM tasks WHERE root_id=%s ORDER BY depth,created_at",
                (task["root_id"],),
            ).fetchall()
        name = "hermbot-workflow-" + str(task_id)[:8]
        content = (
            f"---\nname: {name}\ndescription: Reusable reviewed workflow from completed Hermbot work\n---\n\n# Workflow draft\n\nReview and generalize the saved work before enabling this skill. Replace one-time data, private details and hard-coded artifact IDs with parameters. Teammate delegation always requires current owner authority.\n\n## Requested outcome\n\n"
            + task["input"][:10_000]
            + "\n\n## Observed steps\n\n"
            + "\n\n".join(
                f"### {i + 1}. {s['bot_id']}\n\n{s['input'][:2000]}\n\nObserved result:\n{(s['output'] or '')[:3000]}"
                for i, s in enumerate(steps)
            )
            + "\n"
        )
        return {
            "name": name,
            "content": content[:100_000],
            "source_task_id": task_id,
            "expected_hash": hashlib.sha256(b"").hexdigest(),
            "enable": False,
        }

    return router
