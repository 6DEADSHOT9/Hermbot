"""Authenticated private adapter transport; native keys never cross the owner projection."""

from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
import httpx

from . import db, registry
from .secrets import decrypt


def target(bot_id):
    with db.connection() as conn:
        bot = registry.require_bot(conn, bot_id)
    parsed = urlsplit(bot["runtime_url"])
    # Until a runtime is reprovisioned with its own adapter key, it still holds the shared one.
    applied = bot["adapter_key_encrypted"] and bot["applied_revision"] >= bot["adapter_key_revision"]
    return urlunsplit((parsed.scheme, f"{parsed.hostname}:8643", "", "", "")), {
        "Authorization": "Bearer " + decrypt(bot["adapter_key_encrypted"] if applied else bot["runtime_key_encrypted"])
    }


def request(bot_id, method, path, **kwargs):
    url, headers = target(bot_id)
    try:
        with httpx.Client(timeout=httpx.Timeout(80, connect=4)) as client:
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
