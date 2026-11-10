"""Owner registry and immutable lifecycle outbox. No runtime credentials in projections."""
from datetime import timedelta
import os
import re
import secrets
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from psycopg.types.json import Jsonb
from psycopg.errors import LockNotAvailable

from . import db, service
from .secrets import credential_hash, decrypt, encrypt
from .settings import settings
from .model_protocol import BROKERED_AUTH, CODEX_URL

BOT_COLUMNS = "id,name,role,color,avatar,capabilities,status,desired_state,revision,applied_revision,runtime_error,provider_id,model_name,enabled_tools,enabled_skills,memory_mb,cpus,readiness,checked_at"
PROVIDER_COLUMNS = "id,name,base_url,models,default_model,revision,from_environment,auth_kind,transport,connection_status,account_label,account_plan,plan_enabled,discovered_models,verified_models"
CONFIG_FIELDS = ("name", "role", "color", "avatar", "provider_id", "model_name", "capabilities", "enabled_tools", "enabled_skills", "memory_mb", "cpus")
# Raise when every existing runtime must be recreated (1: runtimes start in `/` with isolated Python;
# 2: the adapter no longer records the screen; 3: the adapter exchanges cookies with private takeover sessions;
# 4: every provider is reached through the model relay, so no runtime holds a provider key).
RUNTIME_LAYOUT = 4


def require_bot(conn, bot_id, *, lock=False, include_deleted=False, nowait=False):
    try:
        bot = conn.execute("SELECT * FROM bots WHERE id=%s" + (" FOR UPDATE" + (" NOWAIT" if nowait else "") if lock else ""), (bot_id,)).fetchone()
    except LockNotAvailable:
        raise HTTPException(409, "A concurrent runtime change is in progress; reload and retry this review")
    if not bot or (bot["status"] == "deleted" and not include_deleted):
        raise HTTPException(404, "Bot not found")
    return bot


def public_bot(conn, bot_id):
    return conn.execute(f"SELECT {BOT_COLUMNS} FROM bots WHERE id=%s", (bot_id,)).fetchone()


def admit_bot(conn, bot_id):
    try:
        bot = conn.execute("SELECT * FROM bots WHERE id=%s FOR SHARE NOWAIT", (bot_id,)).fetchone()
    except LockNotAvailable:
        raise HTTPException(409, "A runtime lifecycle change is in progress; wait before assigning new work")
    if not bot or bot["status"] == "deleted":
        raise HTTPException(404, "Bot not found")
    if conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (bot_id,)).fetchone():
        raise HTTPException(409, 'Hand back this computer before assigning bot work')
    return bot


def configuration(bot):
    return {key: bot[key] for key in CONFIG_FIELDS}


def workspace(conn):
    return conn.execute("SELECT * FROM workspace_settings WHERE singleton=true").fetchone()


def advanced_settings(conn):
    from .models import AdvancedSettings
    current = workspace(conn)
    history = conn.execute(
        "SELECT data,created_at FROM events WHERE kind='settings.advanced_changed' ORDER BY id DESC LIMIT 10").fetchall()
    return {"values": {name: current[name] for name in AdvancedSettings.model_fields}, "defaults": AdvancedSettings().model_dump(),
            "history": [{**row["data"], "changed_at": row["created_at"]} for row in history]}


def save_advanced_settings(conn, body):
    """Apply advanced settings, recording each change so the owner can see what changed and when."""
    conn.execute("SELECT pg_advisory_xact_lock(887832102)")
    current = workspace(conn)
    for name, value in body.model_dump().items():
        if current[name] != value:
            conn.execute(f"UPDATE workspace_settings SET {name}=%s", (value,))
            db.event(conn, "settings.advanced_changed", data={"setting": name, "from": current[name], "to": value})
    return advanced_settings(conn)


def provider_name(conn, kind, label='', provider_id=None):
    conn.execute('SELECT pg_advisory_xact_lock(887832103)')
    prefix = 'Codex' if kind == 'codex_oauth' else 'ChatGPT'
    identity = ' '.join(''.join(c for c in label if c.isprintable()).split())
    base = f'{prefix} · {identity}' if '@' in identity else f'{prefix} connection'
    base = base[:80]
    names = {row['name'] for row in conn.execute('SELECT name FROM providers WHERE %s::uuid IS NULL OR id<>%s', (provider_id, provider_id))}
    name, number = base, 2
    while name in names:
        suffix = f' ({number})'
        name = base[:80-len(suffix)] + suffix
        number += 1
    return name


def bootstrap(conn):
    conn.execute('SELECT pg_advisory_xact_lock(887832103)')
    for provider in conn.execute("SELECT id,auth_kind,account_label FROM providers WHERE NOT name_is_custom AND auth_kind IN ('codex_oauth','chatgpt_oauth') ORDER BY id").fetchall():
        conn.execute('UPDATE providers SET name=%s WHERE id=%s', (provider_name(conn, provider['auth_kind'], provider['account_label'], provider['id']), provider['id']))
    if settings.provider_configured:
        provider = conn.execute("SELECT * FROM providers WHERE from_environment=true OR name='Environment provider' ORDER BY from_environment DESC LIMIT 1").fetchone()
        env_key = os.environ.get("MODEL_API_KEY", "")
        if not provider and not workspace(conn)["environment_imported"]:
            provider = conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,secret_encrypted,from_environment) VALUES (%s,'Environment provider',%s,%s,%s,%s,true) RETURNING *", (uuid4(), settings.model_base_url, Jsonb([settings.model_name]), settings.model_name, encrypt(env_key))).fetchone()
        elif provider and provider["from_environment"] and (provider["base_url"] != settings.model_base_url or provider["default_model"] != settings.model_name or decrypt(provider["secret_encrypted"]) != env_key):
            provider = conn.execute("UPDATE providers SET base_url=%s,models=%s,default_model=%s,secret_encrypted=%s,revision=revision+1 WHERE id=%s RETURNING *", (settings.model_base_url, Jsonb([settings.model_name]), settings.model_name, encrypt(env_key), provider["id"])).fetchone()
            conn.execute("UPDATE bots SET model_name=%s,revision=revision+1 WHERE provider_id=%s AND status<>'deleted'", (settings.model_name, provider["id"]))
        if provider:
            conn.execute("UPDATE workspace_settings SET default_provider_id=%s WHERE default_provider_id IS NULL", (provider["id"],))
            conn.execute("UPDATE bots SET provider_id=%s,model_name=%s WHERE provider_id IS NULL AND status<>'deleted'", (provider["id"], provider["default_model"]))
        conn.execute("UPDATE workspace_settings SET environment_imported=true")
    for bot in conn.execute("SELECT id FROM bots WHERE bot_token_hash IS NULL AND status<>'deleted'").fetchall():
        token = settings.bot_token(bot["id"]) or secrets.token_urlsafe(32)
        key = settings.runtime_key(bot["id"]) or secrets.token_urlsafe(32)
        conn.execute("UPDATE bots SET bot_token_encrypted=%s,bot_token_hash=%s,runtime_key_encrypted=%s WHERE id=%s", (encrypt(token), credential_hash(token), encrypt(key), bot["id"]))
    # Runtimes once shared one key between the adapter and the Hermes API. Give each bot its own
    # adapter key once; the revision bump reprovisions the runtime so it starts with that key.
    for bot in conn.execute("SELECT id FROM bots WHERE adapter_key_encrypted IS NULL AND status<>'deleted'").fetchall():
        conn.execute("UPDATE bots SET adapter_key_encrypted=%s,revision=revision+1,adapter_key_revision=revision+1 WHERE id=%s",
                     (encrypt(secrets.token_urlsafe(32)), bot["id"]))
    conn.execute("UPDATE bots SET runtime_layout=%s,revision=revision+1 WHERE runtime_layout<%s AND status<>'deleted'",
                 (RUNTIME_LAYOUT, RUNTIME_LAYOUT))


def validate_config(conn, value):
    from .models import BotConfiguration
    value = BotConfiguration.model_validate(value).model_dump()
    value["name"], value["role"] = value["name"].strip(), value["role"].strip()
    if not value["name"] or not value["role"]:
        raise HTTPException(422, "Give the teammate a name and a role")
    config = workspace(conn)
    value["provider_id"] = value["provider_id"] or config["default_provider_id"]
    provider = conn.execute("SELECT * FROM providers WHERE id=%s", (value["provider_id"],)).fetchone()
    if not provider:
        raise HTTPException(422, "Configure a model provider in Settings first")
    value["model_name"] = value["model_name"] or provider["default_model"]
    if value["model_name"] not in provider["models"]:
        raise HTTPException(422, "Choose a permitted model for this provider")
    if provider['auth_kind'] in BROKERED_AUTH and (provider['connection_status'] != 'connected' or not provider['plan_enabled'] or value['model_name'] not in provider['verified_models']):
        raise HTTPException(422, 'Connect ChatGPT and test this model in Providers before assigning it')
    if set(value["enabled_tools"]) - set(config["permitted_tools"]):
        raise HTTPException(403, "These tools are not permitted by workspace settings")
    if any(not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_/-]{0,119}", skill) or ".." in skill for skill in value["enabled_skills"]):
        raise HTTPException(422, "Use valid skill names without relative path segments")
    if value["enabled_skills"] and "skills" not in value["enabled_tools"]:
        raise HTTPException(422, "Enable the skills tool to use selected skills")
    value["enabled_tools"] = sorted(set(value["enabled_tools"]))
    value["capabilities"] = sorted(set(value["capabilities"]))
    return value


def apply_configuration(conn, bot_id, config):
    fields, args = [], []
    for key in CONFIG_FIELDS:
        fields.append(f"{key}=%s")
        args.append(Jsonb(config[key]) if isinstance(config[key], list) else config[key])
    conn.execute("UPDATE bots SET " + ",".join(fields) + ",revision=revision+1 WHERE id=%s", (*args, bot_id))
    conn.execute("UPDATE conversations SET title=%s WHERE bot_id=%s AND kind='dm'", (config["name"], bot_id))


def register(conn, config, bot_id=None):
    conn.execute("SELECT pg_advisory_xact_lock(887832102)")
    count = conn.execute("SELECT count(*) AS n FROM bots WHERE status<>'deleted'").fetchone()["n"]
    if count >= workspace(conn)["max_bots"]:
        raise HTTPException(409, "Workspace teammate limit reached")
    bot_id = bot_id or "bot-" + uuid4().hex[:16]
    token, runtime_key, adapter_key = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    conn.execute("INSERT INTO bots(id,name,role,color,capabilities,runtime_url,status,bot_token_encrypted,bot_token_hash,runtime_key_encrypted,adapter_key_encrypted,runtime_layout) VALUES (%s,%s,%s,%s,%s,%s,'provisioning',%s,%s,%s,%s,%s)", (bot_id, config["name"], config["role"], config["color"], Jsonb(config["capabilities"]), f"http://hermbot-bot-{bot_id}:8642", encrypt(token), credential_hash(token), encrypt(runtime_key), encrypt(adapter_key), RUNTIME_LAYOUT))
    apply_configuration(conn, bot_id, config)
    conversation_id = uuid4()
    conn.execute("INSERT INTO conversations(id,title,kind,bot_id) VALUES (%s,%s,'dm',%s)", (conversation_id, config["name"], bot_id))
    conn.execute("INSERT INTO participants VALUES (%s,%s)", (conversation_id, bot_id))
    db.event(conn, "bot.created", conversation_id=conversation_id, bot_id=bot_id, data={"name": config["name"]})
    return require_bot(conn, bot_id)


def busy_tasks(conn, bot_id):
    return conn.execute("SELECT id,status,root_id FROM tasks WHERE bot_id=%s AND (status<>ALL(%s) OR EXISTS(SELECT 1 FROM attempts a WHERE a.task_id=tasks.id AND a.status=ANY(%s))) ORDER BY created_at", (bot_id, list(service.TERMINAL), list(service.ACTIVE_ATTEMPTS) + ["reserved"])).fetchall()


def reserve_lifecycle(conn, bot, interrupt=False):
    if conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (bot['id'],)).fetchone():
        raise HTTPException(409, 'Hand back computer control before changing this runtime')
    if conn.execute("SELECT 1 FROM bot_operations WHERE (bot_id=%s OR payload->>'source_bot_id'=%s) AND status IN ('queued','applying','uncertain')", (bot["id"], bot["id"])).fetchone():
        raise HTTPException(409, "A lifecycle operation is already pending for this bot")
    tasks = busy_tasks(conn, bot["id"])
    if any(t["status"] == "uncertain" for t in tasks):
        raise HTTPException(409, "Reconcile uncertain work before changing this runtime")
    if tasks and not interrupt:
        raise HTTPException(409, "This bot has work. Wait for it to settle or explicitly stop the affected work")
    for task in tasks:
        service.cancel(conn, service.locked_task(conn, task["id"]), "Owner-authorized runtime lifecycle operation")
    return tasks


def pending_operation(conn, bot_id):
    return conn.execute("SELECT 1 FROM bot_operations WHERE (bot_id=%s OR payload->>'source_bot_id'=%s) AND status IN ('queued','applying','uncertain')", (bot_id, bot_id)).fetchone()


def enqueue(conn, bot, action, key, payload=None, *, proposal_id=None):
    payload = payload or {}
    provider = conn.execute("SELECT * FROM providers WHERE id=%s", (bot["provider_id"],)).fetchone()
    if provider:
        # Every runtime reaches its provider through Hermbot's relay with a token of its own, valid only
        # for this revision; the provider's key stays here and never enters the runtime or this payload.
        inference_key = secrets.token_urlsafe(48)
        conn.execute('UPDATE bots SET inference_key_encrypted=%s,inference_key_hash=%s,inference_revision=revision WHERE id=%s',
                     (encrypt(inference_key), credential_hash(inference_key), bot['id']))
        provider_snapshot = {k: provider[k] for k in ('id', 'base_url', 'default_model', 'revision', 'auth_kind')}
        provider_snapshot['inference_key_encrypted'] = encrypt(inference_key)
    else:
        conn.execute('UPDATE bots SET inference_key_encrypted=NULL,inference_key_hash=NULL,inference_revision=NULL WHERE id=%s', (bot['id'],))
        provider_snapshot = None
    payload = {**payload, "configuration": configuration(bot), "revision": bot["revision"], "provider": provider_snapshot,
               "timezone": workspace(conn)["timezone"], "desired_state": bot["desired_state"], "previous_status": bot["status"],
               "bot_token_encrypted": bot["bot_token_encrypted"], "runtime_key_encrypted": bot["runtime_key_encrypted"],
               "adapter_key_encrypted": bot["adapter_key_encrypted"]}
    operation_id = uuid4()
    conn.execute("INSERT INTO bot_operations(id,bot_id,action,payload,request_key,request_hash,proposal_id) VALUES (%s,%s,%s,%s,%s,%s,%s)", (operation_id, bot["id"], action, Jsonb(db.jsonable_encoder(payload)), key, service.digest({"action": action, "payload": payload}), proposal_id))
    conn.execute("UPDATE bots SET status=%s,runtime_error=NULL,readiness='{}' WHERE id=%s", ("deleting" if action == "delete" else "provisioning", bot["id"]))
    db.event(conn, "bot.operation.queued", bot_id=bot["id"], data={"operation_id": str(operation_id), "action": action})
    return {"id": operation_id, "bot_id": bot["id"], "action": action, "status": "queued"}


def owner_operation(conn, bot_id, action, key, body):
    bot = require_bot(conn, bot_id, lock=True)
    if bot["revision"] != body.expected_revision:
        raise HTTPException(409, "This bot changed. Reload its configuration before applying changes")
    reserve_lifecycle(conn, bot, body.interrupt)
    if action == "delete" and body.confirmation != bot["name"]:
        raise HTTPException(422, "Type the bot's exact name to delete its private data and workspace")
    if action == "restore" and bot["status"] != "archived":
        raise HTTPException(409, "Only archived bots can be restored")
    if bot["status"] == "archived" and action in {"start", "stop"}:
        raise HTTPException(409, "Restore this archived teammate before controlling its runtime")
    desired = "stopped" if action in ("stop", "archive", "delete") else "running"
    conn.execute("UPDATE bots SET desired_state=%s,revision=revision+1 WHERE id=%s", (desired, bot_id))
    return enqueue(conn, require_bot(conn, bot_id), action, key)


def mutation(conn, key, request, work):
    if not key or not 1 <= len(key) <= 255 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise HTTPException(422, "Use an idempotency key with 1–255 visible ASCII characters")
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("owner:" + key,))
    fingerprint = service.digest(request)
    previous = conn.execute("SELECT * FROM owner_receipts WHERE key=%s", (key,)).fetchone()
    if previous:
        if previous["request_hash"] != fingerprint:
            raise HTTPException(409, "Request ID already identifies a different action")
        return previous["response"]
    result = db.jsonable_encoder(work())
    conn.execute("INSERT INTO owner_receipts VALUES (%s,%s,%s,now())", (key, fingerprint, Jsonb(result)))
    return result


def save_provider(conn, body, provider_id=None):
    conn.execute('SELECT pg_advisory_xact_lock(887832103)')
    if not body.name.strip():
        raise HTTPException(422, "Give the provider a name")
    url = urlparse(body.base_url)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise HTTPException(422, "Use an HTTP(S) provider URL without credentials, queries or fragments")
    if body.default_model not in body.models or any(not model.strip() or len(model) > 200 for model in body.models):
        raise HTTPException(422, "List the permitted models and choose a default from that list")
    if conn.execute("SELECT 1 FROM providers WHERE name=%s AND (%s::uuid IS NULL OR id<>%s)", (body.name.strip(), provider_id, provider_id)).fetchone():
        raise HTTPException(409, "A provider already uses this name")
    provider = conn.execute("SELECT * FROM providers WHERE id=%s FOR UPDATE", (provider_id,)).fetchone() if provider_id else None
    if provider_id and not provider:
        raise HTTPException(404, "Provider not found")
    if body.auth_kind in BROKERED_AUTH:
        if not provider or provider['auth_kind'] != body.auth_kind or body.api_key or body.base_url.rstrip('/') != (CODEX_URL if body.auth_kind == 'codex_oauth' else 'https://api.openai.com/v1') or set(body.models) - set(provider['discovered_models']):
            raise HTTPException(422, 'Use a connected ChatGPT account and its discovered models')
        if provider['connection_status'] != 'connected' or not provider['plan_enabled']:
            raise HTTPException(409, 'Finish connecting this account before editing its settings')
    elif provider and provider['auth_kind'] != 'api_key':
        raise HTTPException(422, 'The authentication type of an existing provider cannot change')
    if provider and provider["revision"] != body.expected_revision:
        raise HTTPException(409, "Provider changed. Reload before editing it")
    if provider:
        bots = conn.execute("SELECT * FROM bots WHERE provider_id=%s AND status<>'deleted' ORDER BY id FOR UPDATE", (provider_id,)).fetchall()
        for bot in bots:
            reserve_lifecycle(conn, bot)
            if bot["model_name"] not in body.models:
                raise HTTPException(409, "A teammate still uses a model you are removing")
        secret = encrypt(body.api_key) if body.api_key else provider["secret_encrypted"]
        conn.execute("UPDATE providers SET name=%s,name_is_custom=name_is_custom OR %s,base_url=%s,models=%s,default_model=%s,secret_encrypted=%s,revision=revision+1,from_environment=false WHERE id=%s", (body.name.strip(), body.name.strip() != provider['name'], body.base_url.rstrip("/"), Jsonb(sorted(set(body.models))), body.default_model, secret, provider_id))
        for bot in bots:
            conn.execute("UPDATE bots SET revision=revision+1 WHERE id=%s", (bot["id"],))
            enqueue(conn, require_bot(conn, bot["id"]), "configure", f"provider:{provider_id}:{provider['revision']+1}:{bot['id']}")
    else:
        if not body.api_key:
            raise HTTPException(422, "Enter an API key for the new provider")
        provider_id = uuid4()
        conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,secret_encrypted) VALUES (%s,%s,%s,%s,%s,%s)", (provider_id, body.name.strip(), body.base_url.rstrip("/"), Jsonb(sorted(set(body.models))), body.default_model, encrypt(body.api_key)))
        conn.execute("UPDATE workspace_settings SET default_provider_id=%s WHERE default_provider_id IS NULL", (provider_id,))
    return conn.execute(f"SELECT {PROVIDER_COLUMNS} FROM providers WHERE id=%s", (provider_id,)).fetchone()


def save_workspace(conn, body):
    conn.execute("SELECT pg_advisory_xact_lock(887832102)")
    current = workspace(conn)
    policy_fields = ('active_budget_seconds', 'decision_provider_id', 'decision_model', 'decision_mode', 'decision_actions')
    body = body.model_copy(update={name: current[name] for name in policy_fields if name not in body.model_fields_set})
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Choose an IANA timezone such as Asia/Kolkata")
    if body.default_provider_id and not conn.execute("SELECT 1 FROM providers WHERE id=%s", (body.default_provider_id,)).fetchone():
        raise HTTPException(422, "Default provider does not exist")
    if body.decision_mode != 'disabled':
        provider = conn.execute('SELECT models,auth_kind,verified_models FROM providers WHERE id=%s', (body.decision_provider_id,)).fetchone()
        if not provider or body.decision_model not in provider['models']:
            raise HTTPException(422, 'Select a permitted decision model and its API provider')
        if (provider['auth_kind'] in BROKERED_AUTH) != (body.decision_mode == 'responses'):
            raise HTTPException(422, 'ChatGPT plan usage supports structured Responses assessments; Decisions requires an API provider')
        if provider['auth_kind'] in BROKERED_AUTH and body.decision_model not in provider['verified_models']:
            raise HTTPException(422, 'Test this assessment model in Providers first')
    elif body.decision_provider_id and not conn.execute('SELECT 1 FROM providers WHERE id=%s', (body.decision_provider_id,)).fetchone():
        raise HTTPException(422, 'Decision provider does not exist')
    count = conn.execute("SELECT count(*) AS n FROM bots WHERE status<>'deleted'").fetchone()["n"]
    if count > body.max_bots:
        raise HTTPException(409, "The teammate limit is below the number of existing teammates")
    used = conn.execute("SELECT id,enabled_tools FROM bots WHERE status<>'deleted'").fetchall()
    if any(set(b["enabled_tools"]) - set(body.permitted_tools) for b in used):
        raise HTTPException(409, "Disable these tools on existing bots before removing workspace permission")
    timezone_changed = workspace(conn)["timezone"] != body.timezone
    bots = conn.execute("SELECT * FROM bots WHERE status<>'deleted' ORDER BY id FOR UPDATE").fetchall() if timezone_changed else []
    for bot in bots:
        reserve_lifecycle(conn, bot)
    conn.execute("UPDATE workspace_settings SET timezone=%s,default_provider_id=%s,permitted_tools=%s,max_bots=%s,max_active=%s", (body.timezone, body.default_provider_id, Jsonb(body.permitted_tools), body.max_bots, body.max_active))
    conn.execute('UPDATE workspace_settings SET active_budget_seconds=%s,decision_provider_id=%s,decision_model=%s,decision_mode=%s,decision_actions=%s,execution_policy_revision=execution_policy_revision+1',
                 (body.active_budget_seconds, body.decision_provider_id, body.decision_model, body.decision_mode, body.decision_actions))
    if body.external_action_mode is not None:
        conn.execute('UPDATE workspace_settings SET external_action_mode=%s', (body.external_action_mode,))
    for bot in bots:
        conn.execute("UPDATE bots SET revision=revision+1 WHERE id=%s", (bot["id"],))
        enqueue(conn, require_bot(conn, bot["id"]), "configure", f"timezone:{body.timezone}:{bot['id']}:{bot['revision']+1}")
    db.event(conn, "workspace.updated")
    return workspace(conn)


def create_grant(conn, body):
    require_bot(conn, body.manager_id)
    for target in set(body.targets):
        require_bot(conn, target)
        if target == body.manager_id:
            raise HTTPException(422, "Choose other bots as management targets")
    grant_id = uuid4()
    return conn.execute("INSERT INTO grants(id,manager_id,targets,operations,max_handoffs,expires_at,kind,max_created_bots,max_memory_mb,max_cpus) VALUES (%s,%s,%s,%s,%s,%s,'standing',%s,%s,%s) RETURNING *", (grant_id, body.manager_id, Jsonb(sorted(set(body.targets))), Jsonb(sorted(set(body.operations))), settings.max_handoffs, service.now() + timedelta(hours=body.duration_hours), body.max_created_bots, body.max_memory_mb, body.max_cpus)).fetchone()
