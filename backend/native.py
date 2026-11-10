"""Authenticated private adapter transport; native keys never cross the owner projection."""

from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
import httpx

from . import db, registry
from .secrets import decrypt

PAUSED = "This teammate is paused while the owner uses a private browser"


def paused(conn, bot_id):
    """While a takeover has the bot's container frozen, nothing may call it: a request would hang."""
    return bool(conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND paused_at IS NOT NULL", (bot_id,)).fetchone())


def target(bot_id, *, allow_paused=True):
    with db.connection() as conn:
        bot = registry.require_bot(conn, bot_id)
        if not allow_paused and paused(conn, bot_id):
            raise HTTPException(409, PAUSED)
    parsed = urlsplit(bot["runtime_url"])
    # Until a runtime is reprovisioned with its own adapter key, it still holds the shared one.
    applied = bot["adapter_key_encrypted"] and bot["applied_revision"] >= bot["adapter_key_revision"]
    return urlunsplit((parsed.scheme, f"{parsed.hostname}:8643", "", "", "")), {
        "Authorization": "Bearer " + decrypt(bot["adapter_key_encrypted"] if applied else bot["runtime_key_encrypted"])
    }


def request(bot_id, method, path, *, allow_paused=False, timeout=80, **kwargs):
    # Only the takeover and handback flow, which pauses and resumes the bot itself, may pass allow_paused.
    url, headers = target(bot_id, allow_paused=allow_paused)
    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=4)) as client:
            response = client.request(method, url + path, headers=headers, **kwargs)
        if response.status_code >= 400:
            detail = response.json().get("detail", "Native operation was refused")
            raise HTTPException(
                response.status_code, detail if isinstance(detail, str) else "Native operation was refused"
            )
        return response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(
            503, "This runtime adapter is unavailable. Start or upgrade the teammate and wait for readiness"
        )
