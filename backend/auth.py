"""Owner bootstrap token plus revocable, expiring opaque browser sessions."""
from datetime import timedelta
import hmac
import os
import secrets

from fastapi import HTTPException

from . import db, service
from .secrets import credential_hash
from .settings import settings


def verify(authorization=None, cookie=None):
    bearer = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if bearer and hmac.compare_digest(bearer, settings.owner_token):
        return "owner"
    if cookie:
        with db.connection() as conn:
            session = conn.execute("SELECT 1 FROM owner_sessions WHERE token_hash=%s AND revoked_at IS NULL AND expires_at>now()", (credential_hash(cookie),)).fetchone()
        if session:
            return "owner"
    raise HTTPException(401, "Enter your private owner token to open this workspace")


def login(response):
    token = secrets.token_urlsafe(32)
    expiry = service.now() + timedelta(days=1)
    with db.connection() as conn:
        conn.execute("INSERT INTO owner_sessions(token_hash,expires_at) VALUES (%s,%s)", (credential_hash(token), expiry))
    response.set_cookie("hermbot_session", token, httponly=True, secure=os.environ.get("PUBLIC_URL", "").startswith("https://"), samesite="strict", max_age=86400, expires=expiry)


def logout(response, cookie, device=None):
    if cookie:
        with db.connection() as conn:
            conn.execute("UPDATE owner_sessions SET revoked_at=now() WHERE token_hash=%s", (credential_hash(cookie),))
    if device:
        with db.connection() as conn:
            conn.execute("UPDATE push_subscriptions SET enabled=false,status='signed_out',secret_encrypted=NULL WHERE device_hash=%s", (credential_hash(device),))
    response.delete_cookie("hermbot_device")
    response.delete_cookie("hermbot_session")
