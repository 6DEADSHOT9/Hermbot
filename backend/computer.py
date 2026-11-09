"""Owner desktop control and authenticated WebSocket relay; persistent bot admission fence."""

import asyncio
import hashlib
import json
import re
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Cookie, HTTPException, WebSocket
import httpx
from pydantic import Field
from websockets.asyncio.client import connect

from . import auth, db, native, registry
from .models import BotId, StrictModel
from .secrets import credential_hash, decrypt, encrypt
from .settings import settings

COOKIE_TEXT = ("name", "value", "domain", "path")
MAX_COOKIES, MAX_PAGES = 5000, 10


class ControlRequest(StrictModel):
    action: str = Field(pattern=r"^(takeover|handback)$")
    force: bool = False


def viewer(authorization=None, cookie=None):
    auth.verify(authorization, cookie)
    return credential_hash("desktop:" + (cookie or authorization or ""))


def fence(conn, bot_id, holder, viewer_hash=None):
    conn.execute(
        "INSERT INTO computer_controls(bot_id,holder,viewer_hash) VALUES(%s,%s,%s) ON CONFLICT(bot_id) DO UPDATE SET holder=EXCLUDED.holder,viewer_hash=EXCLUDED.viewer_hash,updated_at=now()",
        (bot_id, holder, viewer_hash),
    )


def public_state(state, identity):
    state["mine"] = state["lease"].get("viewer_hash") == hashlib.sha256(identity.encode()).hexdigest()[:12]
    return state


# Takeover happens in a private owner session: a fresh screen and browser in a separate container
# the bot cannot reach. Only cookies cross between it and the bot's browser, never page code, storage
# or extensions, and the bot's cookie data is bounded before it enters the session.
def cookies(items):
    result = []
    for item in (items if isinstance(items, list) else [])[:MAX_COOKIES]:
        if not isinstance(item, dict) or not all(isinstance(item.get(key, "/"), str) for key in COOKIE_TEXT):
            continue
        cookie = {key: item[key] for key in COOKIE_TEXT if key in item}
        if not cookie.get("name") or not cookie.get("domain") or len(cookie.get("value", "")) + len(cookie["name"]) > 8192:
            continue
        cookie.update({key: item[key] for key in ("httpOnly", "secure") if isinstance(item.get(key), bool)})
        if item.get("sameSite") in ("Strict", "Lax", "None"):
            cookie["sameSite"] = item["sameSite"]
        if isinstance(item.get("expires"), (int, float)) and item["expires"] > 0 and not item.get("session"):
            cookie["expires"] = item["expires"]
        result.append(cookie)
    return result


def pages(urls):
    return [url for url in (urls if isinstance(urls, list) else []) if isinstance(url, str)
            and re.match(r"^https?://", url) and len(url) <= 2048][:MAX_PAGES]


def identity_of(cookie):
    return cookie["name"], cookie["domain"], cookie.get("path", "/")


def supervisor(method, path, **kwargs):
    with httpx.Client(timeout=100, trust_env=False) as client:
        response = client.request(method, "http://supervisor:8001" + path,
                                  headers={"Authorization": "Bearer " + settings.supervisor_token}, **kwargs)
    response.raise_for_status()
    return response.json()


def session_request(session, method, path, **kwargs):
    with httpx.Client(timeout=httpx.Timeout(60, connect=4), trust_env=False) as client:
        response = client.request(method, session["url"] + path, headers={"Authorization": "Bearer " + session["token"]}, **kwargs)
    response.raise_for_status()
    return response.json()


def private_session(conn, bot_id):
    row = conn.execute("SELECT * FROM computer_controls WHERE bot_id=%s", (bot_id,)).fetchone()
    if not row or not row["session_url"]:
        return None
    return {"url": row["session_url"], "token": decrypt(row["session_token_encrypted"]),
            "seed": json.loads(decrypt(row["session_seed_encrypted"])), "viewer_hash": row["viewer_hash"]}


def open_private_session(conn, bot_id, geometry):
    seed = native.request(bot_id, "GET", "/computer/browser-state")
    seed = {"cookies": cookies(seed.get("cookies")), "urls": pages(seed.get("urls"))}
    geometry = geometry if re.fullmatch(r"[1-9][0-9]{2,3}x[1-9][0-9]{2,3}", geometry or "") else "1440x900"
    session = supervisor("POST", f"/owner-sessions/{bot_id}", json={"geometry": geometry})
    deadline = time.monotonic() + 30
    while True:
        try:
            if session_request(session, "GET", "/health").get("ready"):
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise httpx.TimeoutException("The private browser did not start")
        time.sleep(0.5)
    session_request(session, "POST", "/start", json=seed)
    # Cookie values are not needed to remove a cookie later; keep only what identifies it.
    removable = [{key: cookie[key] for key in cookie if key != "value"} for cookie in seed["cookies"]]
    conn.execute("UPDATE computer_controls SET session_url=%s,session_token_encrypted=%s,session_seed_encrypted=%s WHERE bot_id=%s",
                 (session["url"], encrypt(session["token"]), encrypt(json.dumps(removable)), bot_id))


def abandon_takeover(bot_id, identity):
    """Return control to the bot after a failed private-session start, outside the failing transaction."""
    try:
        supervisor("DELETE", f"/owner-sessions/{bot_id}")
    except httpx.HTTPError:
        pass
    try:
        native.request(bot_id, "POST", "/computer/control", json={"action": "handback", "force": True, "viewer_id": identity})
    except HTTPException:
        pass
    with db.connection() as release:
        fence(release, bot_id, "agent")


def close_private_session(conn, bot_id):
    """End the session and carry its cookies to the bot. Returns how many cookies were carried, or None."""
    session = private_session(conn, bot_id)
    if not session:
        try:
            supervisor("DELETE", f"/owner-sessions/{bot_id}")  # A session left by an interrupted takeover.
        except httpx.HTTPError:
            pass
        return None
    try:
        final = cookies(session_request(session, "POST", "/finish").get("cookies"))
    except httpx.HTTPError:
        final = None
    try:
        supervisor("DELETE", f"/owner-sessions/{bot_id}")
    except httpx.HTTPError:
        raise HTTPException(502, "The private browser could not be closed; hand back again") from None
    conn.execute("UPDATE computer_controls SET session_url=NULL,session_token_encrypted=NULL,session_seed_encrypted=NULL WHERE bot_id=%s", (bot_id,))
    if final is None:
        return None
    kept = {identity_of(cookie) for cookie in final}
    removed = [cookie for cookie in session["seed"] if identity_of(cookie) not in kept]
    try:
        native.request(bot_id, "POST", "/computer/browser-cookies", json={"set": final, "delete": removed})
    except HTTPException:
        return None
    return len(final)


def upstream_view(bot_id, identity):
    """The owner holding control sees their private session; everyone else watches the bot's screen."""
    with db.connection() as conn:
        session = private_session(conn, bot_id)
        control = conn.execute("SELECT holder,viewer_hash FROM computer_controls WHERE bot_id=%s", (bot_id,)).fetchone()
    if session and session["viewer_hash"] == identity:
        return session["url"].replace("http://", "ws://") + "/view", {"Authorization": "Bearer " + session["token"]}
    if control and control["holder"] == "human" and control["viewer_hash"] == identity:
        # The owner in control types only into the private browser, never onto the bot's screen.
        raise HTTPException(409, "The private browser is still starting")
    url, headers = native.target(bot_id)
    return url.replace("http://", "ws://").replace("https://", "wss://") + "/computer/ws", {**headers, "X-Hermbot-Viewer": identity}


def routes(owner):
    router = APIRouter()

    @router.get("/api/v1/bots/{bot_id}/computer", dependencies=[Depends(owner)])
    def status(
        bot_id: BotId,
        authorization: str | None = Header(default=None),
        hermbot_session: str | None = Cookie(default=None),
    ):
        identity = viewer(authorization, hermbot_session)
        with db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("computer:" + bot_id,))
            state = native.request(bot_id, "GET", "/computer")
            if state["lease"]["holder"] == "human":
                fence(conn, bot_id, "human")
            blocked = conn.execute(
                "SELECT holder FROM computer_controls WHERE bot_id=%s", (bot_id,)
            ).fetchone()
            state["admission_paused"] = bool(blocked and blocked["holder"] == "human")
            state["private_session"] = bool(private_session(conn, bot_id))
            return public_state(state, identity)

    @router.post("/api/v1/bots/{bot_id}/computer/control", dependencies=[Depends(owner)])
    def control(
        bot_id: BotId,
        body: ControlRequest,
        authorization: str | None = Header(default=None),
        hermbot_session: str | None = Cookie(default=None),
    ):
        identity = viewer(authorization, hermbot_session)
        with db.connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("computer:" + bot_id,))
            if body.action == "takeover":
                # Commit admission fencing before waiting for an in-flight native tool. The
                # advisory lock spans that wait and serializes handback/status in other sessions.
                with db.connection() as admission:
                    bot = registry.require_bot(admission, bot_id, lock=True)
                    if (
                        bot["status"] != "active"
                        or bot["desired_state"] != "running"
                        or registry.pending_operation(admission, bot_id)
                    ):
                        raise HTTPException(409, "Wait for a healthy running runtime before takeover")
                    fence(admission, bot_id, "human", identity)
            carried = None
            if body.action == "handback":
                current = conn.execute("SELECT viewer_hash FROM computer_controls WHERE bot_id=%s", (bot_id,)).fetchone()
                if body.force or not current or current["viewer_hash"] in (None, identity):
                    # The bot resumes only after the owner's logins have reached its browser.
                    carried = close_private_session(conn, bot_id)
            state = native.request(
                bot_id,
                "POST",
                "/computer/control",
                json={"action": body.action, "force": body.force, "viewer_id": identity},
            )
            if body.action == "takeover" and state["lease"]["holder"] == "human" and not private_session(conn, bot_id):
                try:
                    open_private_session(conn, bot_id, state["desktop"].get("geometry"))
                except (httpx.HTTPError, HTTPException, KeyError, ValueError):
                    # Never fall back to the bot's own screen: what the owner types there is visible to the bot.
                    abandon_takeover(bot_id, identity)
                    raise HTTPException(502, "The private browser could not start, so control stayed with the bot. Try again.") from None
            if body.action == "handback" and state["lease"]["holder"] == "agent":
                fence(conn, bot_id, "agent")
            data = {"holder": state["lease"]["holder"]}
            if body.action == "handback":
                data["cookies_carried"] = carried
            db.event(conn, "computer.control", bot_id=bot_id, data=data)
            state["private_session"] = bool(private_session(conn, bot_id))
            return public_state(state, identity)

    @router.post("/api/v1/bots/{bot_id}/computer/{action}", dependencies=[Depends(owner)])
    def operation(
        bot_id: BotId,
        action: str,
        authorization: str | None = Header(default=None),
        hermbot_session: str | None = Cookie(default=None),
    ):
        if action not in ("start", "stop"):
            raise HTTPException(404, "Unknown computer action")
        with db.connection() as conn:
            # Keep desktop mutations in the same order as takeover and handback. In
            # particular, a stop admitted before takeover must finish before input
            # becomes ready, and browser launch must not race a handback.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("computer:" + bot_id,))
            return native.request(bot_id, "POST", "/computer/" + action, json={})

    @router.get("/api/v1/bots/{bot_id}/computer/thumbnail", dependencies=[Depends(owner)])
    def thumbnail(bot_id: BotId):
        return native.request(bot_id, "GET", "/computer/thumbnail")

    @router.websocket("/api/v1/bots/{bot_id}/computer/ws")
    async def stream(ws: WebSocket, bot_id: BotId):
        if urlsplit(ws.headers.get("origin", "")).netloc != ws.headers.get("host"):
            await ws.close(code=4403)
            return
        try:
            identity = await asyncio.to_thread(
                viewer, ws.headers.get("authorization"), ws.cookies.get("hermbot_session")
            )
            url, headers = await asyncio.to_thread(upstream_view, bot_id, identity)
        except HTTPException:
            await ws.close(code=4401)
            return
        await ws.accept()
        try:
            async with connect(
                url,
                additional_headers=headers,
                max_size=1_000_000,
                proxy=None,
            ) as upstream:

                async def send():
                    while True:
                        packet = await ws.receive()
                        if packet["type"] == "websocket.disconnect":
                            return
                        data = packet.get("bytes")
                        if data is None or len(data) > 1_000_000:
                            return
                        await upstream.send(data)

                async def receive():
                    async for data in upstream:
                        if not isinstance(data, bytes):
                            return
                        await ws.send_bytes(data)

                async def session_guard():
                    while True:
                        await asyncio.sleep(5)
                        await asyncio.to_thread(
                            auth.verify, ws.headers.get("authorization"), ws.cookies.get("hermbot_session")
                        )

                tasks = [
                    asyncio.create_task(send()),
                    asyncio.create_task(receive()),
                    asyncio.create_task(session_guard()),
                ]
                try:
                    _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass  # No credentials, input bytes or screenshots belong in product logs.
        finally:
            try:
                await ws.close()
            except RuntimeError:
                pass

    return router
