"""Owner desktop control and authenticated WebSocket relay; persistent bot admission fence."""

import asyncio
import hashlib
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Cookie, HTTPException, WebSocket
from pydantic import Field
from websockets.asyncio.client import connect

from . import auth, db, native, registry
from .models import BotId, StrictModel
from .secrets import credential_hash


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
            state = native.request(
                bot_id,
                "POST",
                "/computer/control",
                json={"action": body.action, "force": body.force, "viewer_id": identity},
            )
            if body.action == "handback" and state["lease"]["holder"] == "agent":
                fence(conn, bot_id, "agent")
            db.event(conn, "computer.control", bot_id=bot_id, data={"holder": state["lease"]["holder"]})
            return public_state(state, identity)

    @router.post("/api/v1/bots/{bot_id}/computer/{action}", dependencies=[Depends(owner)])
    def operation(
        bot_id: BotId,
        action: str,
        authorization: str | None = Header(default=None),
        hermbot_session: str | None = Cookie(default=None),
    ):
        if action not in ("start", "stop", "browser"):
            raise HTTPException(404, "Unknown computer action")
        with db.connection() as conn:
            # Keep desktop mutations in the same order as takeover and handback. In
            # particular, a stop admitted before takeover must finish before input
            # becomes ready, and browser launch must not race a handback.
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("computer:" + bot_id,))
            return native.request(
                bot_id,
                "POST",
                "/computer/" + action,
                json={"viewer_id": viewer(authorization, hermbot_session)} if action == "browser" else {},
            )

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
            url, headers = await asyncio.to_thread(native.target, bot_id)
        except HTTPException:
            await ws.close(code=4401)
            return
        headers["X-Hermbot-Viewer"] = identity
        await ws.accept()
        try:
            async with connect(
                url.replace("http://", "ws://").replace("https://", "wss://") + "/computer/ws",
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
