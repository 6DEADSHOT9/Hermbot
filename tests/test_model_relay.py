"""API-key providers are reached only through Hermbot's relay; a runtime never holds the provider key."""
import ast
import asyncio
import json
from pathlib import Path
import sys
import types
from uuid import uuid4

from fastapi.testclient import TestClient
import httpx
import pytest

from backend import app as application, db, model_relay, provisioning, registry
from backend.models import BotConfiguration
from backend.secrets import decrypt
from backend.settings import settings

pytestmark = pytest.mark.integration
REAL_KEY = "sk-real-provider-key-for-relay-tests"
BASE_URL = "https://provider.example/v1"


@pytest.fixture
def owner_client():
    client = TestClient(application.app, headers={"Authorization": "Bearer " + settings.owner_token})
    yield client
    client.close()


@pytest.fixture
def upstream(monkeypatch):
    """The provider, as the relay sees it. Each test sets `reply`; every request is recorded."""
    class Upstream:
        requests, reply = [], None
    fake = Upstream()
    fake.requests = []

    async def handler(request):
        fake.requests.append(request)
        if fake.reply is None:
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"event: done\ndata: {}\n\n")
        return fake.reply(request)
    monkeypatch.setattr(model_relay, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return fake


@pytest.fixture
def relay_bot(owner_client):
    body = {"name": "Relay provider " + uuid4().hex[:6], "base_url": BASE_URL, "models": ["model-a", "model-b"], "default_model": "model-a", "api_key": REAL_KEY}
    provider = owner_client.post("/api/v1/providers", json=body, headers={"Idempotency-Key": str(uuid4())}).json()
    with db.connection() as conn:
        config = registry.validate_config(conn, BotConfiguration(name="Relay fixture", role="Read local fixtures", provider_id=provider["id"], model_name="model-a").model_dump())
        bot = registry.register(conn, config)
        queued = registry.enqueue(conn, bot, "create", str(uuid4()))
        operation = conn.execute("SELECT * FROM bot_operations WHERE id=%s", (queued["id"],)).fetchone()
        conn.execute("UPDATE bots SET status='active',desired_state='running' WHERE id=%s", (bot["id"],))
    token = decrypt(operation["payload"]["provider"]["inference_key_encrypted"])
    return {"bot": bot, "provider": provider, "operation": operation, "token": token}


def call(client, token, path="responses", body=None, headers=None, method="POST"):
    request = {"model": "model-a", "input": [{"role": "user", "content": "hello"}], "stream": True} if body is None else body
    headers = {"Authorization": "Bearer " + token, **(headers or {})}
    if method == "GET":
        return client.get("/internal/inference/v1/" + path, headers=headers)
    return client.post("/internal/inference/v1/" + path, content=json.dumps(request), headers=headers)


def test_the_operation_payload_carries_a_relay_token_and_never_the_provider_key(relay_bot):
    payload = relay_bot["operation"]["payload"]
    assert "secret_encrypted" not in payload["provider"] and REAL_KEY not in json.dumps(payload)
    with db.connection() as conn:
        stored = registry.require_bot(conn, relay_bot["bot"]["id"])
    from backend.secrets import credential_hash
    assert credential_hash(relay_bot["token"]) == stored["inference_key_hash"] and stored["inference_revision"] == stored["revision"]


def test_the_runtime_gets_the_relay_url_and_token_and_the_real_url_only_for_its_api_style(relay_bot, monkeypatch):
    sent = []

    async def supervisor(method, path, **kwargs):
        sent.append(kwargs["json"])
        return {"state": "running", "revision": kwargs["json"]["revision"]}
    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    asyncio.run(provisioning.drive(relay_bot["operation"]))
    spec = sent[0]
    assert spec["model_base_url"] == provisioning.RELAY_URL and spec["model_api_key"] == relay_bot["token"]
    assert spec["model_auth_kind"] == "api_key" and spec["model_provider_url"] == BASE_URL
    assert REAL_KEY not in json.dumps(spec)


def test_an_operation_queued_with_a_provider_key_is_refused_not_applied(relay_bot, monkeypatch):
    async def supervisor(*args, **kwargs):
        pytest.fail("A provider key must never reach the supervisor")
    monkeypatch.setattr(provisioning, "supervisor", supervisor)
    with db.connection() as conn:
        provider = conn.execute("SELECT * FROM providers WHERE id=%s", (relay_bot["provider"]["id"],)).fetchone()
        legacy = {**relay_bot["operation"]["payload"], "provider": {"id": str(provider["id"]), **{k: provider[k] for k in ("base_url", "default_model", "revision", "secret_encrypted")}}}
        conn.execute("UPDATE bot_operations SET payload=%s WHERE id=%s", (json.dumps(legacy), relay_bot["operation"]["id"]))
        operation = conn.execute("SELECT * FROM bot_operations WHERE id=%s", (relay_bot["operation"]["id"],)).fetchone()
    asyncio.run(provisioning.drive(operation))
    with db.connection() as conn:
        failed = conn.execute("SELECT status,error FROM bot_operations WHERE id=%s", (operation["id"],)).fetchone()
    assert failed["status"] == "failed" and "predates" in failed["error"]


def test_a_compressed_provider_reply_reaches_the_runtime_decoded(owner_client, relay_bot, upstream):
    import gzip
    upstream.reply = lambda request: httpx.Response(200, headers={"content-type": "application/json", "content-encoding": "gzip"},
                                                    content=gzip.compress(b'{"output": "plain"}'))
    response = call(owner_client, relay_bot["token"], body={"model": "model-a", "input": []})
    assert response.json() == {"output": "plain"} and "content-encoding" not in response.headers


def test_the_relay_sends_the_request_to_the_providers_own_url_with_the_real_key(owner_client, relay_bot, upstream):
    body = {"model": "model-a", "input": [{"role": "user", "content": "hello"}], "stream": True, "reasoning": {"effort": "medium"}}
    raw = json.dumps(body)
    response = owner_client.post("/internal/inference/v1/responses", content=raw, headers={
        "Authorization": "Bearer " + relay_bot["token"], "x-api-key": "planted", "Cookie": "session=planted", "OpenAI-Organization": "planted",
        "Accept": "text/event-stream"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert response.text == "event: done\ndata: {}\n\n", "The stream comes back as the provider sent it"
    sent = upstream.requests[0]
    assert str(sent.url) == BASE_URL + "/responses", "The runtime cannot choose the destination"
    assert sent.headers["authorization"] == "Bearer " + REAL_KEY
    assert not {"x-api-key", "cookie", "openai-organization"} & set(sent.headers.keys()), "Only content headers are forwarded"
    assert sent.content == raw.encode(), "The body is forwarded byte for byte"


def test_chat_completions_and_anthropic_messages_use_each_providers_auth_format(owner_client, relay_bot, upstream):
    assert call(owner_client, relay_bot["token"], "chat/completions").status_code == 200
    assert str(upstream.requests[-1].url) == BASE_URL + "/chat/completions" and upstream.requests[-1].headers["authorization"] == "Bearer " + REAL_KEY
    assert call(owner_client, relay_bot["token"], "messages", headers={"anthropic-version": "2023-06-01"}).status_code == 200
    sent = upstream.requests[-1]
    assert sent.headers["x-api-key"] == REAL_KEY and "authorization" not in sent.headers and sent.headers["anthropic-version"] == "2023-06-01"


def test_only_a_valid_current_token_of_a_running_bot_is_admitted(owner_client, relay_bot, upstream):
    assert call(owner_client, "").status_code == 401
    assert call(owner_client, "not-a-relay-token").status_code == 401
    assert call(owner_client, relay_bot["token"], headers={"Authorization": "Basic " + relay_bot["token"]}).status_code == 401
    with db.connection() as conn:
        conn.execute("UPDATE bots SET desired_state='stopped' WHERE id=%s", (relay_bot["bot"]["id"],))
    assert call(owner_client, relay_bot["token"]).status_code == 401, "A stopped bot's token is dead"
    with db.connection() as conn:
        conn.execute("UPDATE bots SET desired_state='running',revision=revision+1 WHERE id=%s", (relay_bot["bot"]["id"],))
    assert call(owner_client, relay_bot["token"]).status_code == 401, "A reconfigured bot's old token is dead"
    assert upstream.requests == []


def test_only_the_assigned_model_is_relayed(owner_client, relay_bot, upstream):
    response = call(owner_client, relay_bot["token"], body={"model": "model-b", "input": []})
    assert response.status_code == 403 and upstream.requests == []
    assert call(owner_client, relay_bot["token"], body=["not", "an", "object"]).status_code == 422


def test_only_model_routes_exist(owner_client, relay_bot, upstream):
    for path in ("embeddings", "audio/speech", "images/generations", "files"):
        assert call(owner_client, relay_bot["token"], path).status_code in (404, 405), path
    assert upstream.requests == []


def test_a_provider_rejection_never_echoes_the_providers_error(owner_client, relay_bot, upstream):
    upstream.reply = lambda request: httpx.Response(401, json={"error": {"message": "Incorrect API key provided: " + REAL_KEY[:12] + "****"}})
    response = call(owner_client, relay_bot["token"])
    assert response.status_code == 401 and REAL_KEY[:12] not in response.text
    assert response.json()["error"]["code"] == "provider_key_rejected"


def test_an_unreachable_provider_is_a_retryable_gateway_error(owner_client, relay_bot, upstream):
    def unreachable(request):
        raise httpx.ConnectError("provider down")
    upstream.reply = unreachable
    assert call(owner_client, relay_bot["token"]).status_code == 502


def test_models_lists_only_the_assigned_model(owner_client, relay_bot, upstream):
    upstream.reply = lambda request: httpx.Response(200, json={"object": "list", "data": [{"id": "model-a", "context_length": 400000}, {"id": "other-model"}]})
    response = call(owner_client, relay_bot["token"], "models", method="GET")
    assert response.status_code == 200 and response.json()["data"] == [{"id": "model-a", "context_length": 400000}]
    assert upstream.requests[-1].method == "GET" and str(upstream.requests[-1].url) == BASE_URL + "/models"


def test_a_stopping_execution_gets_no_further_inference(owner_client, relay_bot, upstream):
    with db.connection() as conn:  # The fixture bot has no conversation of its own, so borrow one.
        conversation = conn.execute("SELECT id FROM conversations WHERE bot_id='researcher'").fetchone()["id"]
        from backend import service
        task = service.create_task(conn, bot_id="researcher", conversation_id=conversation, prompt="Stop me", request_key=str(uuid4()))
        conn.execute("UPDATE tasks SET status='cancelling',bot_id=%s WHERE id=%s", (relay_bot["bot"]["id"], task["id"]))
        conn.execute("INSERT INTO attempts(id,task_id,bot_id,number,session_id,status,idempotency_key,input) VALUES(%s,%s,%s,1,'s','running',%s,'x')",
                     (uuid4(), task["id"], relay_bot["bot"]["id"], str(uuid4())))
    assert call(owner_client, relay_bot["token"]).status_code == 409 and upstream.requests == []


def test_a_stream_ends_when_the_bot_stops_mid_reply(owner_client, relay_bot, upstream, monkeypatch):
    monkeypatch.setattr(model_relay, "ADMISSION_SECONDS", 0)
    bot_id = relay_bot["bot"]["id"]

    async def chunks():
        yield b"data: first\n\n"
        with db.connection() as conn:
            conn.execute("UPDATE bots SET desired_state='stopped' WHERE id=%s", (bot_id,))
        yield b"data: second\n\n"
        yield b"data: third\n\n"
    upstream.reply = lambda request: httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())
    response = call(owner_client, relay_bot["token"])
    assert response.text == "data: first\n\n", "Nothing more is relayed once the bot stops"


def test_subscription_bots_keep_their_own_route(owner_client, relay_bot, upstream):
    from psycopg.types.json import Jsonb
    with db.connection() as conn:
        subscription = conn.execute("INSERT INTO providers(id,name,base_url,models,default_model,auth_kind,transport,connection_status,plan_enabled,verified_models,discovered_models) "
                                    "VALUES(%s,%s,'https://chatgpt.com/backend-api/codex',%s,'model-a','codex_oauth','responses','connected',true,%s,%s) RETURNING id",
                                    (uuid4(), "Relay subscription " + uuid4().hex[:6], Jsonb(["model-a"]), Jsonb(["model-a"]), Jsonb(["model-a"]))).fetchone()
        conn.execute("UPDATE bots SET provider_id=%s WHERE id=%s", (subscription["id"], relay_bot["bot"]["id"]))
    assert call(owner_client, relay_bot["token"], "chat/completions").status_code == 404
    assert call(owner_client, relay_bot["token"], "models", method="GET").status_code == 404
    assert upstream.requests == []


def bootstrap_route():
    """The bootstrap's model-route function, run without starting a runtime."""
    tree = ast.parse(Path("docker/hermes_bootstrap.py").read_text())
    nodes = [node for node in tree.body if (isinstance(node, ast.FunctionDef) and node.name == "apply_model_route")
             or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "MODEL_ROUTES" for t in node.targets))]
    scope = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), "<bootstrap-route>", "exec"), scope)
    return scope["apply_model_route"]


@pytest.fixture
def hermes_defaults(monkeypatch):
    defaults = types.ModuleType("hermes_cli.config_defaults")
    defaults.DEFAULT_CONFIG = {"auxiliary": {"vision": {"provider": "auto"}, "title_generation": {"provider": "auto"}, "notes": "not a task"}}
    monkeypatch.setitem(sys.modules, "hermes_cli", types.ModuleType("hermes_cli"))
    monkeypatch.setitem(sys.modules, "hermes_cli.config_defaults", defaults)


def test_bootstrap_routes_the_model_and_every_auxiliary_task_through_the_relay(hermes_defaults):
    apply_model_route = bootstrap_route()
    config = {"model": {"default": "model-a", "provider": "custom", "base_url": provisioning.RELAY_URL},
              "providers": {"hermbot-chatgpt": {}, "other": {}}, "auxiliary": {"vision": {"provider": "custom:hermbot-chatgpt"}, "other": {"provider": "other"}}}
    apply_model_route(config, "hermbot-relay", "codex_responses", "OPENAI_API_KEY")
    assert config["model"]["provider"] == "custom:hermbot-relay"
    assert config["providers"] == {"other": {}, "hermbot-relay": {"base_url": provisioning.RELAY_URL, "api_mode": "codex_responses", "key_env": "OPENAI_API_KEY"}}
    assert {task: values["provider"] for task, values in config["auxiliary"].items()} == {
        "vision": "custom:hermbot-relay", "title_generation": "custom:hermbot-relay", "other": "other"}
    assert config["auxiliary"]["vision"]["model"] == "model-a" and config["fallback_providers"] == []


def test_bootstrap_without_a_provider_removes_every_hermbot_route(hermes_defaults):
    apply_model_route = bootstrap_route()
    config = {"model": {"default": "unconfigured", "provider": "custom", "base_url": "http://127.0.0.1:1/v1"},
              "providers": {"hermbot-chatgpt": {}, "hermbot-relay": {}, "other": {}},
              "auxiliary": {"vision": {"provider": "custom:hermbot-chatgpt"}, "compression": {"provider": "custom:hermbot-relay"}, "other": {"provider": "other"}}}
    apply_model_route(config, None, None, None)
    assert config["providers"] == {"other": {}} and config["auxiliary"] == {"other": {"provider": "other"}}
    assert config["model"]["provider"] == "custom"
