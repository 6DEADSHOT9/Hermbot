"""Model relay for API-key providers: a runtime's relay token comes in, the provider's key goes out.

The provider's key stays in this process and is never sent to a runtime. The destination is always the
provider's own URL, so a runtime can't redirect the key elsewhere. Only the request body and a few
content headers are forwarded, and responses stream back unbuffered. A runtime that disconnects ends
the provider request at once, so nobody pays for output nobody reads.
"""
import asyncio
import json
import time
from uuid import uuid4

import anyio
import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, service
from .model_protocol import MAX_BODY
from .secrets import decrypt

# Admission is re-checked this often during a long stream, off the event loop: checking on every
# streamed event costs a database query per token and stalls every other request (spike, 2026-11-10).
ADMISSION_SECONDS = 5
FORWARDED_HEADERS = ("accept", "anthropic-version", "anthropic-beta")
PATHS = ("responses", "chat/completions", "messages", "models")
_client = None


def client():
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False, follow_redirects=False,
                                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))
    return _client


async def raw_body(request):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise HTTPException(413, "Model request exceeds the size limit")
    return bytes(data)


def credential(row, path):
    key = decrypt(row["secret_encrypted"])
    if not key:
        raise HTTPException(409, "This teammate's model provider has no API key. Add one in Settings.")
    # Anthropic's Messages API authenticates with x-api-key; OpenAI-style APIs use a bearer token.
    return {"x-api-key": key} if path == "messages" else {"Authorization": "Bearer " + key}


def rejected(status):
    # The provider's own error text can echo part of the key, so it is never passed on.
    return JSONResponse({"error": {"message": "The model provider rejected this workspace's API key. Update the provider's key in Settings.",
                                   "type": "provider_authentication", "code": "provider_key_rejected"}}, status_code=status)


def active_task(bot_id):
    with db.connection() as conn:
        return conn.execute("SELECT t.* FROM tasks t JOIN attempts a ON a.task_id=t.id WHERE a.bot_id=%s AND a.status=ANY(%s) "
                            "ORDER BY a.created_at DESC LIMIT 1", (bot_id, list(service.ACTIVE_ATTEMPTS))).fetchone()


def record(task, state, request_id):
    if task:
        with db.connection() as conn:
            db.event(conn, "model.request." + state, task, data={"request_id": request_id})


async def forward(request, path, token, bot, row, admit):
    """Relay one admitted model request to the bot's API-key provider."""
    if path not in PATHS:
        raise HTTPException(404, "Not a relayed model route")
    raw = await raw_body(request) if request.method == "POST" else b""
    if request.method == "POST":
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "Invalid model request") from None
        if payload.get("model") != bot["model_name"]:
            raise HTTPException(403, "Use this teammate’s assigned model")
    headers = {"Content-Type": "application/json", **{name: request.headers[name] for name in FORWARDED_HEADERS if name in request.headers},
               **credential(row, path)}
    http = client()
    try:
        upstream = await http.send(http.build_request(request.method, row["base_url"].rstrip("/") + "/" + path, content=raw or None,
                                                      headers=headers), stream=True)
    except httpx.HTTPError:
        raise HTTPException(502, "The model provider is unreachable") from None
    if upstream.status_code in (401, 403):
        await upstream.aclose()
        return rejected(upstream.status_code)
    if path == "models":
        try:
            listed = json.loads(await upstream.aread())
        except ValueError:
            listed = {}
        finally:
            await upstream.aclose()
        models = listed.get("data") if isinstance(listed, dict) else None
        # Only the assigned model's metadata (its context window) is the runtime's business.
        return JSONResponse({"object": "list", "data": [item for item in models or [] if isinstance(item, dict) and item.get("id") == bot["model_name"]]},
                            status_code=upstream.status_code if upstream.status_code < 400 else 404)
    task = await asyncio.to_thread(active_task, bot["id"])
    request_id = str(uuid4())
    await asyncio.to_thread(record, task, "active", request_id)

    async def stream():
        checked = time.monotonic()
        try:
            # Decoded bytes: the provider may compress its reply, and no content encoding is passed on.
            async for chunk in upstream.aiter_bytes():
                if time.monotonic() - checked >= ADMISSION_SECONDS:
                    checked = time.monotonic()
                    try:
                        latest_bot, latest = await asyncio.to_thread(admit, token)
                    except HTTPException:
                        return  # Stopped, reconfigured or deleted: end the provider request too.
                    if latest_bot["revision"] != bot["revision"] or latest["revision"] != row["revision"]:
                        return
                yield chunk
        finally:
            # A disconnecting runtime cancels this generator; finish closing the provider request regardless.
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
                await asyncio.to_thread(record, task, "finished", request_id)
    return StreamingResponse(stream(), status_code=upstream.status_code, media_type=upstream.headers.get("content-type", "application/json"))
