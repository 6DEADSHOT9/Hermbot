"""Phase 2 owner APIs; bots reach only the separately authenticated collaboration boundary."""
import base64
import binascii
from datetime import timedelta
import hashlib
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from psycopg.types.json import Jsonb

from . import db, lifecycle, registry, resource_settings, service
from .settings import settings
from .models import (AttachmentRequest, BotConfiguration, BotId, BotUpdate, CloneRequest, EditRequest,
                     GrantRequest, GroupRequest, LifecycleRequest, ProposalArgs, ProviderRequest,
                     ReactionRequest, RetryRequest, WorkspaceRequest, AdvancedSettings, MessageResolution, LimitsUpdate)

ATTACHMENT_TYPES = {"text/plain", "text/markdown", "text/csv", "application/json", "text/x-python", "application/pdf", "image/png", "image/jpeg", "image/webp"}


def routes(owner, get_store):
    router = APIRouter(dependencies=[Depends(owner)])

    @router.get("/api/v1/settings")
    def get_settings():
        from .model_auth import relay
        with db.connection() as conn:
            return {"workspace": registry.workspace(conn), "model_broker_online": relay.socket is not None, "providers": conn.execute(f"SELECT {registry.PROVIDER_COLUMNS},auth_kind='api_key' AS key_configured FROM providers ORDER BY created_at").fetchall()}

    @router.put("/api/v1/settings")
    def put_settings(body: WorkspaceRequest):
        with db.connection() as conn:
            return registry.save_workspace(conn, body)

    @router.get('/api/v1/settings/limits')
    def get_limits():
        with db.connection() as conn:
            return resource_settings.read(conn)

    @router.put('/api/v1/settings/limits')
    def save_limits(body: LimitsUpdate, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, body.model_dump(), lambda: resource_settings.save(conn, body))

    @router.get('/api/v1/bots/{bot_id}/limits')
    def get_bot_limits(bot_id: BotId):
        with db.connection() as conn:
            return resource_settings.read(conn, bot_id)

    @router.put('/api/v1/bots/{bot_id}/limits')
    def save_bot_limits(bot_id: BotId, body: LimitsUpdate, key: str = Header(alias='Idempotency-Key')):
        with db.connection() as conn:
            return registry.mutation(conn, key, {'bot_id': bot_id, **body.model_dump()},
                                     lambda: resource_settings.save(conn, body, bot_id))

    @router.get("/api/v1/settings/advanced")
    def get_advanced_settings():
        with db.connection() as conn:
            return registry.advanced_settings(conn)

    @router.put("/api/v1/settings/advanced")
    def put_advanced_settings(body: AdvancedSettings):
        with db.connection() as conn:
            return registry.save_advanced_settings(conn, body)

    @router.post("/api/v1/settings/advanced/reset")
    def reset_advanced_settings():
        with db.connection() as conn:
            return registry.save_advanced_settings(conn, AdvancedSettings())

    @router.post("/api/v1/providers", status_code=201)
    def create_provider(body: ProviderRequest, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            return registry.mutation(conn, key, {"action": "create-provider", "body": body.model_dump()}, lambda: registry.save_provider(conn, body))

    @router.put("/api/v1/providers/{provider_id}")
    def update_provider(provider_id: UUID, body: ProviderRequest, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            return registry.mutation(conn, key, {"action": "update-provider", "id": provider_id, "body": body.model_dump()}, lambda: registry.save_provider(conn, body, provider_id))

    @router.delete("/api/v1/providers/{provider_id}")
    def delete_provider(provider_id: UUID):
        with db.connection() as conn:
            conn.execute('SELECT id FROM providers WHERE id=%s FOR UPDATE', (provider_id,)).fetchone()
            if conn.execute("SELECT 1 FROM providers WHERE id=%s AND auth_kind IN ('chatgpt_oauth','codex_oauth') AND connection_status<>'disconnected'", (provider_id,)).fetchone():
                raise HTTPException(409, 'Disconnect this ChatGPT account before removing it')
            workspace = registry.workspace(conn)
            if conn.execute("SELECT 1 FROM bots WHERE provider_id=%s AND status<>'deleted'", (provider_id,)).fetchone() or provider_id in (workspace['default_provider_id'], workspace['decision_provider_id']):
                raise HTTPException(409, "Switch the workspace default, decision provider and all affected bots before removing this provider")
            conn.execute("UPDATE bots SET provider_id=NULL WHERE provider_id=%s AND status='deleted'", (provider_id,))
            if not conn.execute("DELETE FROM providers WHERE id=%s RETURNING id", (provider_id,)).fetchone():
                raise HTTPException(404, "Provider not found")
        return {"deleted": True}

    @router.get("/api/v1/bots")
    def list_bots(include_archived: bool = False):
        with db.connection() as conn:
            return conn.execute(f"SELECT {registry.BOT_COLUMNS} FROM bots WHERE status<>'deleted' AND (%s OR status<>'archived') ORDER BY id", (include_archived,)).fetchall()

    @router.post("/api/v1/bots", status_code=202)
    def create_bot(body: BotConfiguration, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            def work():
                bot = registry.register(conn, registry.validate_config(conn, body.model_dump(exclude_unset=True)))
                operation = registry.enqueue(conn, bot, "create", key)
                return {"bot": registry.public_bot(conn, bot["id"]), "operation": operation}
            return registry.mutation(conn, key, {"action": "create-bot", "body": body.model_dump()}, work,
                                     resource_fields=sorted(body.model_fields_set & {'memory_mb', 'cpus'}))

    @router.put("/api/v1/bots/{bot_id}", status_code=202)
    def update_bot(bot_id: BotId, body: BotUpdate, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            def work():
                bot = registry.require_bot(conn, bot_id, lock=True)
                if bot["revision"] != body.expected_revision:
                    raise HTTPException(409, "This bot changed. Reload before editing")
                registry.reserve_lifecycle(conn, bot, body.interrupt)
                config = registry.validate_config(conn, body.model_dump(exclude_unset=True, exclude={"expected_revision", "interrupt"}), existing=bot)
                registry.apply_configuration(conn, bot_id, config)
                operation = registry.enqueue(conn, registry.require_bot(conn, bot_id), "configure", key)
                return {"bot": registry.public_bot(conn, bot_id), "operation": operation}
            return registry.mutation(conn, key, {"action": "update-bot", "id": bot_id, "body": body.model_dump()}, work,
                                     resource_fields=sorted(body.model_fields_set & {'memory_mb', 'cpus'}))

    @router.post("/api/v1/bots/{bot_id}/clone", status_code=202)
    def clone_bot(bot_id: BotId, body: CloneRequest, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            def work():
                conn.execute("SELECT pg_advisory_xact_lock(887832102)")
                source = registry.require_bot(conn, bot_id, lock=True)
                if source["revision"] != body.expected_revision:
                    raise HTTPException(409, "Clone source changed. Reload it first")
                registry.reserve_lifecycle(conn, source)
                bot = registry.register(conn, registry.validate_config(conn, body.configuration.model_dump(exclude_unset=True)))
                options = {name: getattr(body, "copy_" + name) for name in ("memory", "skills", "workspace", "history")}
                operation = registry.enqueue(conn, bot, "clone", key, {"source_bot_id": bot_id, "source_revision": source["revision"], "source_was_running": source["desired_state"] == "running", "copy_options": options})
                return {"bot": registry.public_bot(conn, bot["id"]), "operation": operation}
            return registry.mutation(conn, key, {"action": "clone-bot", "id": bot_id, "body": body.model_dump()}, work,
                                     resource_fields=sorted(body.configuration.model_fields_set & {'memory_mb', 'cpus'}))

    @router.post("/api/v1/bots/{bot_id}/{action}", status_code=202)
    def bot_action(bot_id: BotId, action: str, body: LifecycleRequest, key: str = Header(alias="Idempotency-Key")):
        if action not in {"start", "stop", "archive", "restore", "delete"}:
            raise HTTPException(404, "Unknown bot action")
        with db.connection() as conn:
            return registry.mutation(conn, key, {"action": action, "bot_id": bot_id, "body": body.model_dump()}, lambda: registry.owner_operation(conn, bot_id, action, key, body))

    @router.get("/api/v1/bots/{bot_id}/impact")
    def impact(bot_id: BotId):
        with db.connection() as conn:
            bot = registry.require_bot(conn, bot_id)
            return {"bot_id": bot_id, "revision": bot["revision"], "affected_tasks": registry.busy_tasks(conn, bot_id)}

    @router.get("/api/v1/bots/{bot_id}/skills")
    def skills(bot_id: BotId):
        from . import native
        with db.connection() as conn:
            registry.require_bot(conn, bot_id)
        try:
            return native.request(bot_id, 'GET', '/skills')
        except Exception:
            raise HTTPException(503, "Start the bot and wait for it to become ready before inspecting installed skills")

    @router.get("/api/v1/bot-operations")
    def operations():
        with db.connection() as conn:
            return conn.execute("SELECT id,bot_id,action,status,error,result,created_at,updated_at FROM bot_operations ORDER BY created_at DESC LIMIT 100").fetchall()

    @router.post("/api/v1/bot-operations/{operation_id}/retry")
    def retry_operation(operation_id: UUID):
        with db.connection() as conn:
            op = conn.execute("SELECT * FROM bot_operations WHERE id=%s FOR UPDATE", (operation_id,)).fetchone()
            if not op or op["status"] != "failed":
                raise HTTPException(409, "Only a settled failed operation can be retried")
            bot = registry.require_bot(conn, op["bot_id"], lock=True)
            if bot["revision"] != op["payload"]["revision"] or registry.busy_tasks(conn, bot["id"]):
                raise HTTPException(409, "Bot state changed or work is unsettled; review a new operation")
            registry.reserve_lifecycle(conn, bot)
            if op["payload"].get("source_bot_id"):
                source = registry.require_bot(conn, op["payload"]["source_bot_id"], lock=True)
                if source["revision"] != op["payload"].get("source_revision", source["revision"]):
                    raise HTTPException(409, "Clone source configuration changed; review a new clone")
                registry.reserve_lifecycle(conn, source)
            if op["proposal_id"]:
                proposal = conn.execute("SELECT * FROM lifecycle_proposals WHERE id=%s", (op["proposal_id"],)).fetchone()
                task = service.locked_task(conn, proposal["task_id"])
                if task["status"] in service.TERMINAL | service.STOPPING:
                    raise HTTPException(409, "The manager operation has settled; apply a new owner lifecycle action instead")
                lifecycle.authority(conn, task, proposal["action"], None if proposal["action"] == "create" else bot["id"], consume_creation=False)
            conn.execute("UPDATE bot_operations SET status='queued',error=NULL,worker_id=NULL,lease_until=NULL WHERE id=%s", (operation_id,))
            conn.execute("UPDATE bots SET status='provisioning',runtime_error=NULL WHERE id=%s", (bot["id"],))
        return {"id": operation_id, "status": "queued"}

    @router.post("/api/v1/grants", status_code=201)
    def create_grant(body: GrantRequest, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            return registry.mutation(conn, key, {"action": "grant", "body": body.model_dump()}, lambda: registry.create_grant(conn, body))

    @router.get("/api/v1/grants")
    def list_grants():
        with db.connection() as conn:
            return conn.execute("SELECT * FROM grants ORDER BY expires_at DESC LIMIT 100").fetchall()

    @router.put("/api/v1/proposals/{proposal_id}")
    def edit_proposal(proposal_id: UUID, body: ProposalArgs, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            return registry.mutation(conn, key, {"action": "edit-proposal", "id": proposal_id, "body": body.model_dump()},
                                     lambda: lifecycle.replace(conn, proposal_id, body.model_dump(exclude_unset=True)),
                                     resource_fields=sorted((body.configuration.model_fields_set if body.configuration else set()) & {'memory_mb', 'cpus'}))

    @router.put("/api/v1/groups/{conversation_id}")
    def edit_group(conversation_id: UUID, body: GroupRequest):
        with db.connection() as conn:
            chat = conn.execute("SELECT * FROM conversations WHERE id=%s FOR UPDATE", (conversation_id,)).fetchone()
            if not chat or chat["kind"] != "group" or chat['archived_at']:
                raise HTTPException(404, "Group not found")
            for bot_id in set(body.bot_ids):
                registry.require_bot(conn, bot_id)
            if len(set(body.bot_ids)) < 2:
                raise HTTPException(422, "Choose at least two different teammates")
            old = {r["bot_id"] for r in conn.execute("SELECT bot_id FROM participants WHERE conversation_id=%s", (conversation_id,)).fetchall()}
            removed = old - set(body.bot_ids)
            if removed and conn.execute("SELECT 1 FROM tasks WHERE conversation_id=%s AND bot_id=ANY(%s) AND (status<>ALL(%s) OR status='uncertain' OR EXISTS(SELECT 1 FROM attempts WHERE attempts.task_id=tasks.id AND attempts.status=ANY(%s)))", (conversation_id, list(removed), list(service.TERMINAL), list(service.ACTIVE_ATTEMPTS) + ["reserved"])).fetchone():
                raise HTTPException(409, "Stop or settle a teammate's group work before removing it")
            conn.execute("UPDATE conversations SET title=%s WHERE id=%s", (body.title.strip(), conversation_id))
            conn.execute("DELETE FROM participants WHERE conversation_id=%s", (conversation_id,))
            for bot_id in set(body.bot_ids):
                conn.execute("INSERT INTO participants VALUES (%s,%s)", (conversation_id, bot_id))
            if removed:
                from . import triggers
                triggers.pause_conversation(conn, conversation_id, list(removed))
            db.event(conn, "group.updated", conversation_id=conversation_id)
        return {"id": conversation_id, "updated": True}

    def archive_conversation(conversation_id, group_only=False):
        with db.connection() as conn:
            chat = conn.execute('SELECT * FROM conversations WHERE id=%s FOR UPDATE', (conversation_id,)).fetchone()
            if not chat or (group_only and chat['kind'] != 'group'):
                raise HTTPException(404, 'Conversation not found')
            if chat['kind'] == 'dm':
                bot = registry.require_bot(conn, chat['bot_id'], include_deleted=True)
                if bot['status'] not in {'archived', 'deleted'}:
                    raise HTTPException(409, 'Archive or delete this teammate before removing its saved conversation')
            if conn.execute("SELECT 1 FROM tasks t WHERE t.conversation_id=%s AND (t.status<>ALL(%s) OR EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=t.id AND a.status=ANY(%s)))", (conversation_id, list(service.TERMINAL), list(service.ACTIVE_ATTEMPTS) + ['reserved'])).fetchone():
                raise HTTPException(409, 'Stop or settle all conversation work before removing it')
            if conn.execute("SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.conversation_id=%s AND o.status=ANY(%s)", (conversation_id, ['pending', 'queued', 'dispatching', 'uncertain'])).fetchone():
                raise HTTPException(409, 'Settle pending application changes before removing this conversation')
            conn.execute('UPDATE conversations SET archived_at=COALESCE(archived_at,now()) WHERE id=%s', (conversation_id,))
            from . import triggers
            triggers.pause_conversation(conn, conversation_id)
            conn.execute("UPDATE routine_definitions SET state='paused',next_run_at=NULL,error='Destination conversation archived; explicitly resume after restoring it',updated_at=now() WHERE conversation_id=%s AND state='active'", (conversation_id,))
            db.event(conn, 'conversation.archived', conversation_id=conversation_id)
        return {'id': conversation_id, 'archived': True}

    @router.delete('/api/v1/groups/{conversation_id}')
    def remove_group(conversation_id: UUID):
        return archive_conversation(conversation_id, group_only=True)

    @router.delete('/api/v1/conversations/{conversation_id}')
    def remove_conversation(conversation_id: UUID):
        return archive_conversation(conversation_id)

    @router.post('/api/v1/conversations/{conversation_id}/restore')
    def restore_conversation(conversation_id: UUID):
        with db.connection() as conn:
            if not conn.execute('UPDATE conversations SET archived_at=NULL WHERE id=%s RETURNING id', (conversation_id,)).fetchone():
                raise HTTPException(404, 'Conversation not found')
            db.event(conn, 'conversation.restored', conversation_id=conversation_id)
        return {'id': conversation_id, 'restored': True}

    @router.post("/api/v1/conversations/{conversation_id}/attachments", status_code=201)
    def upload(conversation_id: UUID, body: AttachmentRequest, key: str = Header(alias="Idempotency-Key")):
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,159}", body.name) or body.content_type not in ATTACHMENT_TYPES:
            raise HTTPException(422, "Use a plain filename and a supported text, PDF or image type")
        try:
            data = base64.b64decode(body.content_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(422, "Attachment must be valid base64")
        if not data or len(data) > 10_000_000:
            raise HTTPException(413, "Attachments must be between 1 byte and 10 MB")
        sha = hashlib.sha256(data).hexdigest()
        with db.connection() as conn:
            def work():
                if not conn.execute("SELECT 1 FROM conversations WHERE id=%s", (conversation_id,)).fetchone():
                    raise HTTPException(404, "Conversation not found")
                attachment_id = uuid4()
                object_key = f"attachments/{conversation_id}/{attachment_id}"
                store = get_store()
                store.client.put_object(Bucket=store.bucket, Key=object_key, Body=data, ContentType=body.content_type, Metadata={"sha256": sha})
                return conn.execute("INSERT INTO attachments(id,conversation_id,name,content_type,size,sha256,object_key) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,conversation_id,name,content_type,size,sha256", (attachment_id, conversation_id, body.name, body.content_type, len(data), sha, object_key)).fetchone()
            return registry.mutation(conn, key, {"action": "upload", "conversation": conversation_id, "name": body.name, "content_type": body.content_type, "sha": sha}, work)

    @router.get("/api/v1/attachments/{attachment_id}")
    def download_attachment(attachment_id: UUID):
        with db.connection() as conn:
            attachment = conn.execute("SELECT * FROM attachments WHERE id=%s", (attachment_id,)).fetchone()
        if not attachment:
            raise HTTPException(404, "Attachment not found")
        return Response(get_store().read_bytes(attachment), media_type=attachment["content_type"], headers={"Content-Disposition": f'attachment; filename="{attachment["name"]}"'})

    @router.post("/api/v1/messages/{message_id}/reactions")
    def react(message_id: int, body: ReactionRequest):
        with db.connection() as conn:
            if not conn.execute("SELECT 1 FROM messages WHERE id=%s", (message_id,)).fetchone():
                raise HTTPException(404, "Message not found")
            removed = conn.execute("DELETE FROM message_reactions WHERE message_id=%s AND emoji=%s RETURNING message_id", (message_id, body.emoji)).fetchone()
            if not removed:
                conn.execute("INSERT INTO message_reactions(message_id,emoji) VALUES (%s,%s)", (message_id, body.emoji))
        return {"reacted": not bool(removed)}

    @router.put("/api/v1/tasks/{task_id}")
    def edit_task(task_id: UUID, body: EditRequest):
        with db.connection() as conn:
            task = service.locked_task(conn, task_id)
            if task["status"] not in {"queued", "blocked"} or task["attempts"]:
                raise HTTPException(409, "Only work that has not started can be edited; use Redirect for active work")
            if service.digest(task["input"]) != body.expected_input_hash:
                raise HTTPException(409, "Instructions changed. Reload before editing")
            if task.get('orchestration_version') == 2 and task['work_item_id']:
                intake = service.create_task(conn, bot_id=task['bot_id'], conversation_id=task['conversation_id'],
                            prompt=body.prompt, request_key=f"edit:{task_id}:{body.expected_input_hash}:{service.digest(body.prompt)}",
                            context=task['context'], message_intent='auto', orchestration_version=2, coordination_version=task.get('coordination_version', 0))
                conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s', (Jsonb({'owner_work_target': str(task['work_item_id'])}), intake['id']))
                conn.execute('UPDATE tasks SET input=%s,resume_after_intake=%s,updated_at=now() WHERE id=%s', (body.prompt, intake['id'], task_id))
                db.event(conn, 'task.edit_recorded', task)
                return {'id': task_id, 'updated': True}
            conn.execute("UPDATE tasks SET input=%s,continuation=NULL,updated_at=now() WHERE id=%s", (body.prompt, task_id))
            conn.execute("UPDATE messages SET content=%s,edited_at=now() WHERE task_id=%s AND role='user' AND source_id=%s", (body.prompt, task_id, f"request:{task_id}"))
            db.event(conn, "task.edited", task)
        return {"id": task_id, "updated": True}

    def retry_management(conn, task, grant, renew):
        """A retry keeps its work's own grant. A revoked one is never restored; a task grant is renewed for 24 h on every
        retry, but only once the owner confirms it, so a retry never runs out of permission partway through."""
        def manager():
            row = conn.execute("SELECT name FROM bots WHERE id=%s", (task["bot_id"],)).fetchone()
            return row["name"] if row else "This teammate"
        if grant["revoked_at"] or (grant["kind"] == "standing" and grant["expires_at"] <= service.now()):
            lapsed = "was revoked" if grant["revoked_at"] else "has expired"
            again = "send the request again with Coordinate team" if grant["kind"] == "task" else "choose an active permission or send the request again with Coordinate team"
            raise HTTPException(409, f"{manager()}'s permission to manage teammates for this work {lapsed}. To grant it again, {again}.")
        if grant["kind"] != "task":
            return  # A standing grant keeps the duration the owner chose for it in Settings.
        if conn.execute("SELECT 1 FROM tasks WHERE root_id=%s AND status<>ALL(%s)", (task["root_id"], list(service.TERMINAL))).fetchone():
            raise HTTPException(409, "Wait for the entire old operation to settle before retrying")
        managed = service.present_bots(conn, grant["targets"])
        if len(managed) < len(set(grant["targets"])):
            raise HTTPException(409, "A teammate this work managed is archived or no longer exists. Send the request again with Coordinate team.")
        if service.handoffs_used(conn, task) >= min(settings.max_handoffs, grant["max_handoffs"]):
            raise HTTPException(409, "This work has used all its handoffs to teammates. To continue, send the request again with Coordinate team.")
        if not renew:
            names = ", ".join(sorted(row["name"] for row in managed))
            raise HTTPException(409, {"code": "renewal_required", "message": f"Retry and let {manager()} manage {names} for another 24 hours?"})
        expires = service.now() + timedelta(hours=24)
        conn.execute("UPDATE grants SET expires_at=%s WHERE id=%s", (expires, grant["id"]))
        db.event(conn, "grant.renewed", task, data={"grant_id": str(grant["id"]), "expires_at": expires.isoformat(),
                                                     "targets": grant["targets"], "operations": grant["operations"]})

    @router.post("/api/v1/tasks/{task_id}/retry", status_code=202)
    def retry_task(task_id: UUID, body: RetryRequest, key: str = Header(alias="Idempotency-Key")):
        with db.connection() as conn:
            def work():
                task = service.locked_task(conn, task_id)
                if task["status"] not in {"failed", "interrupted", "cancelled"}:
                    raise HTTPException(409, "Only settled failed, interrupted or stopped work can be retried")
                if task['work_item_id']:
                    item = conn.execute('SELECT outcomes_version FROM work_items WHERE id=%s FOR UPDATE', (task['work_item_id'],)).fetchone()
                    required = conn.execute("SELECT status FROM work_outcomes WHERE work_item_id=%s AND required AND status<>'cancelled'", (task['work_item_id'],)).fetchall()
                    if item['outcomes_version'] and required and all(outcome['status'] == 'fulfilled' for outcome in required):
                        raise HTTPException(409, 'All required results are already complete. Inspect the existing results instead of retrying this work.')
                service.require_known_connector_outcomes(conn, task['root_id'])
                if conn.execute("SELECT 1 FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.root_id=%s AND a.status=ANY(%s)", (task["root_id"], list(service.ACTIVE_ATTEMPTS) + ["reserved"])).fetchone():
                    raise HTTPException(409, "Wait for the entire old operation to settle before retrying")
                grant = conn.execute("SELECT * FROM grants WHERE id=%s", (task["grant_id"],)).fetchone() if task["grant_id"] else None
                managing = bool(grant and grant["manager_id"] == task["bot_id"])
                if managing and grant["kind"] == "task":
                    # Locked after the work, as revocation locks it, so a retry and a revoke can't interleave.
                    grant = conn.execute("SELECT * FROM grants WHERE id=%s FOR UPDATE", (grant["id"],)).fetchone()
                origin = task["retried_from"] or task["id"]
                # Only the latest run of a piece of work is retried: the checks above looked at this run alone.
                newer = conn.execute("SELECT status FROM tasks WHERE retried_from=%s AND id<>%s AND created_at>%s ORDER BY created_at DESC LIMIT 1",
                                     (origin, task["id"], task["created_at"])).fetchone()
                if newer and newer["status"] in ("pausing_budget", "paused_budget"):
                    raise HTTPException(409, "A retry of this work is stopped at its budget. Continue it instead.")
                if newer and newer["status"] not in service.TERMINAL:
                    raise HTTPException(409, "A retry of this work is already running. Use Redirect on it instead.")
                if newer:
                    raise HTTPException(409, "Retry the latest run of this work instead.")
                service.admit_work(conn, task["bot_id"], task["conversation_id"])  # Before asking the owner to renew anything.
                if managing:
                    retry_management(conn, task, grant, body.renew_management)
                replacement = service.create_task(conn, bot_id=task["bot_id"], conversation_id=task["conversation_id"], prompt=body.prompt or task["input"], request_key=key + ":task", context=task["context"],
                    inherited_grant_id=grant["id"] if managing and grant["kind"] == "task" else None,
                    grant_id=grant["id"] if managing and grant["kind"] == "standing" else None,
                    attachment_ids=[UUID(a) for a in task["attachment_ids"]], artifact_ids=[UUID(a) for a in task['library_artifact_ids']], reply_to_message_id=task["reply_to_message_id"], skill_refs=task["skill_refs"],
                    message_intent='auto' if task.get('orchestration_version') == 2 else 'update' if task['work_item_id'] else task['message_intent'],
                    work_item_id=None if task.get('orchestration_version') == 2 else task['work_item_id'], orchestration_version=task.get('orchestration_version', 0), coordination_version=task.get('coordination_version', 0))
                if task.get('orchestration_version') == 2 and task['work_item_id']:
                    conn.execute('UPDATE tasks SET handoff_context=%s WHERE id=%s', (Jsonb({'owner_work_target': str(task['work_item_id'])}), replacement['id']))
                conn.execute('UPDATE tasks SET retried_from=%s WHERE id=%s', (origin, replacement['id']))
                return replacement
            # Without a renewal, the fingerprint is the one used before renewals existed, so earlier receipts still match.
            request = body.model_dump(exclude=None if body.renew_management else {"renew_management"})
            return registry.mutation(conn, key, {"action": "retry-task", "id": task_id, "body": request}, work)

    @router.post('/api/v1/teammate-messages/{message_id}/resolve')
    def resolve_message(message_id: UUID, body: MessageResolution, key: str = Header(alias='Idempotency-Key')):
        from . import coordination
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'resolve-message', 'id': message_id, 'body': body.model_dump()},
                                     lambda: coordination.resolve_failed(conn, message_id, body.action, body.expected_revision))

    @router.post('/api/v1/tasks/{task_id}/continue')
    def continue_task(task_id: UUID, key: str = Header(alias='Idempotency-Key')):
        from .execution_policy import continue_work
        with db.connection() as conn:
            return registry.mutation(conn, key, {'action': 'continue-budget', 'task_id': str(task_id)}, lambda: continue_work(conn, service.locked_task(conn, task_id)))

    @router.get("/api/v1/owner-sessions")
    def sessions():
        with db.connection() as conn:
            return conn.execute("SELECT token_hash AS id,created_at,expires_at FROM owner_sessions WHERE revoked_at IS NULL AND expires_at>now() ORDER BY created_at DESC").fetchall()

    @router.delete("/api/v1/owner-sessions/{session_id}")
    def revoke_session(session_id: str):
        with db.connection() as conn:
            conn.execute("UPDATE owner_sessions SET revoked_at=now() WHERE token_hash=%s", (session_id,))
        return {"revoked": True}

    return router
