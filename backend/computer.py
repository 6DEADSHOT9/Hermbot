"""Owner desktop control and authenticated WebSocket relay; persistent bot admission fence."""

import asyncio
import hashlib
import json
import math
import re
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Cookie, HTTPException, WebSocket
import httpx
from psycopg.types.json import Jsonb
from pydantic import Field
from websockets.asyncio.client import connect

from . import auth, db, execution_policy, native, registry, service
from .models import BotId, StrictModel
from .secrets import credential_hash, decrypt, encrypt
from .settings import settings

COOKIE_TEXT = ("name", "value", "domain", "path")
MAX_COOKIES, MAX_PAGES = 5000, 10
# A reply is in progress while the bot has a turn the runtime has started or may have started.
MID_REPLY = """SELECT 1 FROM attempts WHERE bot_id=%s AND status IN ('dispatching','running','stopping')
    AND (runtime_run_id IS NOT NULL OR response ? 'start_requested_at') LIMIT 1"""
TAKEOVER_RESET = "paused_at=NULL,paused_state=NULL,unpaused=false,pause_failed=false,finishing_until=NULL,skip_wait=false"


class ControlRequest(StrictModel):
    action: str = Field(pattern=r"^(takeover|takeover_now|takeover_unpaused|handback)$")
    force: bool = False


def viewer(authorization=None, cookie=None):
    auth.verify(authorization, cookie)
    return credential_hash("desktop:" + (cookie or authorization or ""))


def lock(conn, bot_id):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("computer:" + bot_id,))


def control_row(conn, bot_id):
    return conn.execute("SELECT * FROM computer_controls WHERE bot_id=%s", (bot_id,)).fetchone()


def owns(row, identity):
    return bool(row and row["holder"] == "human" and row["viewer_hash"] == identity)


def fence(conn, bot_id, holder, viewer_hash=None):
    if holder == "agent":
        release(conn, bot_id)
        return
    conn.execute(
        "INSERT INTO computer_controls(bot_id,holder,viewer_hash) VALUES(%s,%s,%s) ON CONFLICT(bot_id) DO UPDATE SET holder=EXCLUDED.holder,viewer_hash=EXCLUDED.viewer_hash,updated_at=now()",
        (bot_id, holder, viewer_hash),
    )


def release(conn, bot_id):
    conn.execute(
        f"INSERT INTO computer_controls(bot_id,holder) VALUES(%s,'agent') ON CONFLICT(bot_id) DO UPDATE SET holder='agent',viewer_hash=NULL,{TAKEOVER_RESET},updated_at=now()",
        (bot_id,),
    )


def update_control(bot_id, **fields):
    """Commit takeover progress at once, so status polls and the worker see it while a takeover runs."""
    with db.connection() as conn:
        conn.execute("UPDATE computer_controls SET " + ",".join(name + "=%s" for name in fields) + ",updated_at=now() WHERE bot_id=%s",
                     (*fields.values(), bot_id))


def public_state(state, identity):
    state["mine"] = state["lease"].get("viewer_hash") == hashlib.sha256(identity.encode()).hexdigest()[:12]
    return state


def takeover_phase(row):
    """Where a takeover stands, for the pane: finishing a reply, pausing, paused, unpaused or pause failed."""
    if not row or row["holder"] != "human":
        return None
    if row["pause_failed"]:
        return {"phase": "pause_failed"}
    if row["paused_at"]:
        return {"phase": "paused"}
    if row["unpaused"]:
        return {"phase": "unpaused"}
    left = (row["finishing_until"] - service.now()).total_seconds() if row["finishing_until"] else 0
    if left > 0 and not row["skip_wait"]:
        return {"phase": "finishing", "seconds_left": math.ceil(left)}
    return {"phase": "pausing"}


def stored_status(row, identity):
    """A paused bot cannot answer, so its status is what it reported just before the pause."""
    state = {**row["paused_state"], "mine": row["viewer_hash"] == identity, "admission_paused": True,
             "private_session": bool(row["session_url"]), "takeover": takeover_phase(row)}
    state["lease"] = {**state.get("lease", {}), "holder": "human"}
    return state


def computer_status(bot_id, identity):
    with db.connection() as conn:
        registry.require_bot(conn, bot_id)
        row = control_row(conn, bot_id)
    if row and row["holder"] == "human" and row["paused_at"] and row["paused_state"]:
        return stored_status(row, identity)
    with db.connection() as conn:
        lock(conn, bot_id)
        row = control_row(conn, bot_id)
        if row and row["holder"] == "human" and row["paused_at"] and row["paused_state"]:
            return stored_status(row, identity)
        state = native.request(bot_id, "GET", "/computer")
        if state["lease"]["holder"] == "human":
            # Keep the admission fence without forgetting which owner holds control.
            conn.execute(
                "INSERT INTO computer_controls(bot_id,holder) VALUES(%s,'human') ON CONFLICT(bot_id) DO UPDATE SET holder='human',updated_at=now()",
                (bot_id,),
            )
        row = control_row(conn, bot_id)
        state["admission_paused"] = bool(row and row["holder"] == "human")
        state["private_session"] = bool(row and row["session_url"])
        state["takeover"] = takeover_phase(row)
        return public_state(state, identity)


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


def open_private_session(bot_id, geometry, seed):
    """Start the owner's private browser from the bot's pages and cookies, read before the bot paused."""
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
    update_control(bot_id, session_url=session["url"], session_token_encrypted=encrypt(session["token"]),
                   session_seed_encrypted=encrypt(json.dumps(removable)))


def stop_clocks(conn, bot_id):
    """Stop the active-time clock of the bot's running turns; it restarts when the worker next sees them run."""
    for attempt in conn.execute("SELECT id FROM attempts WHERE bot_id=%s AND accounted_at IS NOT NULL", (bot_id,)).fetchall():
        execution_policy.account(conn, attempt["id"], running=False)


def resume(bot_id):
    """Thaw the bot's runtime and wait until its adapter answers again."""
    if not supervisor("POST", f"/runtimes/{bot_id}/resume").get("changed"):
        return
    deadline = time.monotonic() + 20
    while True:
        try:
            native.request(bot_id, "GET", "/health", allow_paused=True, timeout=4)
            return
        except HTTPException:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.25)


def abandon_takeover(bot_id, identity):
    """Return control to the bot after a failed takeover, outside the failing transaction."""
    try:
        supervisor("DELETE", f"/owner-sessions/{bot_id}")
    except httpx.HTTPError:
        pass
    with db.connection() as conn:
        was_paused = native.paused(conn, bot_id)
    if was_paused:
        try:
            resume(bot_id)
        except (httpx.HTTPError, HTTPException):
            # Control stays with the owner, so a handback can resume the bot later.
            raise HTTPException(502, "The takeover failed and the bot could not be resumed. Hand back to try again.") from None
    try:
        native.request(bot_id, "POST", "/computer/control", json={"action": "handback", "force": True, "viewer_id": identity},
                       allow_paused=True)
    except HTTPException:
        pass
    with db.connection() as conn:
        release(conn, bot_id)


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
        native.request(bot_id, "POST", "/computer/browser-cookies", json={"set": final, "delete": removed}, allow_paused=True)
    except HTTPException:
        return None
    return len(final)


# A takeover runs in three steps, so the per-bot lock is never held while a reply finishes:
# 1. under the lock, fence admission and take the lease (the bot's tools now refuse to run);
# 2. without the lock, let a reply in progress finish, for up to the owner's chosen wait;
# 3. under the lock again, pause the bot's container and open the private browser.
# All takeover progress is committed in separate short transactions, never by the lock's own one.
def begin_takeover(bot_id, identity, force):
    """Step 1. Returns whether steps 2 and 3 are needed."""
    with db.connection() as conn:
        lock(conn, bot_id)
        row = control_row(conn, bot_id)
        if row and row["holder"] == "human" and row["paused_at"]:
            # Already paused for a private browser. Another session of the owner reclaims it here,
            # without calling the paused bot; its handback then releases the lease by force.
            if row["viewer_hash"] != identity:
                update_control(bot_id, viewer_hash=identity)
            return not row["session_url"]
        # Commit admission fencing before waiting for an in-flight native tool. The advisory
        # lock spans that wait and serializes handback and status in other sessions.
        with db.connection() as admission:
            bot = registry.require_bot(admission, bot_id, lock=True)
            if bot["status"] != "active" or bot["desired_state"] != "running" or registry.pending_operation(admission, bot_id):
                raise HTTPException(409, "Wait for a healthy running runtime before takeover")
            fence(admission, bot_id, "human", identity)
        state = native.request(bot_id, "POST", "/computer/control", json={"action": "takeover", "force": force, "viewer_id": identity})
        if state["lease"]["holder"] != "human" or (row and row["session_url"]):
            return False  # Confirming control of a private browser that is already open.
        wait = registry.workspace(conn)["takeover_reply_wait_seconds"]
        if wait and conn.execute(MID_REPLY, (bot_id,)).fetchone():
            with db.connection() as mark:
                mark.execute("UPDATE computer_controls SET finishing_until=now()+make_interval(secs=>%s),skip_wait=false,updated_at=now() WHERE bot_id=%s",
                             (wait, bot_id))
        return True


def wait_for_reply(bot_id, identity):
    """Step 2. The bot's next tool call is refused, so its turn ends soon; Take over now ends the wait."""
    while True:
        with db.connection() as conn:
            row = conn.execute("SELECT *,finishing_until>now() AS finishing FROM computer_controls WHERE bot_id=%s", (bot_id,)).fetchone()
            if not owns(row, identity):
                raise HTTPException(409, "Control changed while the bot was finishing its reply. Reload to see who is in control.")
            if not row["finishing"] or row["skip_wait"] or not conn.execute(MID_REPLY, (bot_id,)).fetchone():
                return
        time.sleep(0.5)


def pause_and_open(bot_id, identity, *, unpaused=False):
    """Step 3. Pause the bot, unless told not to, then open the owner's private browser."""
    with db.connection() as conn:
        lock(conn, bot_id)
        row = control_row(conn, bot_id)
        if not owns(row, identity):
            raise HTTPException(409, "Control changed during the takeover. Reload to see who is in control.")
        if row["session_url"]:
            return
        bot, workspace = registry.require_bot(conn, bot_id), registry.workspace(conn)
        if row["paused_at"]:
            # An interrupted takeover left the bot paused without a private browser: start this step over.
            resume(bot_id)
            update_control(bot_id, paused_at=None, paused_state=None)
        # Read the bot's pages and cookies while it can still answer.
        state = native.request(bot_id, "GET", "/computer")
        seed = native.request(bot_id, "GET", "/computer/browser-state")
        paused = False
        if not unpaused:
            # Record the pause before making it, so nothing calls the bot once it is frozen.
            with db.connection() as mark:
                mark.execute("UPDATE computer_controls SET paused_at=now(),paused_state=%s,pause_failed=false,finishing_until=NULL,skip_wait=false,"
                             "updated_at=now() WHERE bot_id=%s", (Jsonb(state), bot_id))
                stop_clocks(mark, bot_id)
            try:
                supervisor("POST", f"/runtimes/{bot_id}/pause")
                paused = True
            except httpx.HTTPError:
                try:
                    supervisor("POST", f"/runtimes/{bot_id}/resume")  # In case the pause took effect after all.
                except httpx.HTTPError:
                    pass  # The worker resumes a runtime left paused without a takeover.
                update_control(bot_id, paused_at=None, paused_state=None)
                mode = workspace["takeover_unpausable"]
                if mode == "ask":
                    update_control(bot_id, pause_failed=True)
                    record(bot_id, holder="human", pause_failed=True)
                    return
                if mode == "refuse":
                    abandon_takeover(bot_id, identity)
                    record(bot_id, holder="agent", pause_failed=True)
                    raise HTTPException(502, f"Couldn't pause {bot['name']}. Try again.") from None
        if not paused:
            update_control(bot_id, unpaused=True, pause_failed=False, finishing_until=None, skip_wait=False)
        try:
            open_private_session(bot_id, state["desktop"].get("geometry"), seed)
        except (httpx.HTTPError, HTTPException, KeyError, ValueError):
            # Never fall back to the bot's own screen: what the owner types there is visible to the bot.
            abandon_takeover(bot_id, identity)
            raise HTTPException(502, "The private browser could not start, so control stayed with the bot. Try again.") from None
        record(bot_id, holder="human", paused=paused)


def record(bot_id, **data):
    with db.connection() as conn:
        db.event(conn, "computer.control", bot_id=bot_id, data=data)


def hand_back(bot_id, identity, force):
    with db.connection() as conn:
        lock(conn, bot_id)
        row = control_row(conn, bot_id)
        was_paused = bool(row and row["paused_at"])
        authorized = force or not row or row["viewer_hash"] in (None, identity)
        if was_paused and not authorized:
            raise HTTPException(409, "Another session holds control; explicitly reclaim it before handing back")
        carried = None
        if authorized:
            # A forced handback always resumes, whatever the record says.
            if was_paused or force:
                try:
                    resume(bot_id)
                except (httpx.HTTPError, HTTPException):
                    if was_paused:
                        raise HTTPException(502, "The bot could not be resumed, so you still have control. Hand back again.") from None
            # The bot resumes work only after the owner's logins have reached its browser.
            carried = close_private_session(conn, bot_id)
        # While paused the lease was the database's to move between the owner's sessions, so release it by force.
        state = native.request(bot_id, "POST", "/computer/control",
                               json={"action": "handback", "force": force or was_paused, "viewer_id": identity}, allow_paused=True)
        if state["lease"]["holder"] == "agent":
            release(conn, bot_id)
        db.event(conn, "computer.control", bot_id=bot_id, data={"holder": state["lease"]["holder"], "cookies_carried": carried})


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
    # While the bot is paused its screen cannot be watched (native.PAUSED).
    url, headers = native.target(bot_id, allow_paused=False)
    # The bot's own screen is watch-only: this viewer never matches the lease, so the adapter drops all input.
    watcher = hashlib.sha256(("watch:" + identity).encode()).hexdigest()
    return url.replace("http://", "ws://").replace("https://", "wss://") + "/computer/ws", {**headers, "X-Hermbot-Viewer": watcher}


def routes(owner):
    router = APIRouter()

    @router.get("/api/v1/bots/{bot_id}/computer", dependencies=[Depends(owner)])
    def status(
        bot_id: BotId,
        authorization: str | None = Header(default=None),
        hermbot_session: str | None = Cookie(default=None),
    ):
        return computer_status(bot_id, viewer(authorization, hermbot_session))

    @router.post("/api/v1/bots/{bot_id}/computer/control", dependencies=[Depends(owner)])
    def control(
        bot_id: BotId,
        body: ControlRequest,
        authorization: str | None = Header(default=None),
        hermbot_session: str | None = Cookie(default=None),
    ):
        identity = viewer(authorization, hermbot_session)
        if body.action == "handback":
            hand_back(bot_id, identity, body.force)
        elif body.action == "takeover_now":
            with db.connection() as conn:
                conn.execute("UPDATE computer_controls SET skip_wait=true,updated_at=now() WHERE bot_id=%s AND holder='human' AND viewer_hash=%s "
                             "AND finishing_until IS NOT NULL", (bot_id, identity))
        elif body.action == "takeover_unpaused":
            with db.connection() as conn:
                row = control_row(conn, bot_id)
            if not owns(row, identity) or not row["pause_failed"]:
                raise HTTPException(409, "Taking over without pausing is offered only after a pause fails")
            pause_and_open(bot_id, identity, unpaused=True)
        elif begin_takeover(bot_id, identity, body.force):
            wait_for_reply(bot_id, identity)
            pause_and_open(bot_id, identity)
        return computer_status(bot_id, identity)

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
            lock(conn, bot_id)
            return native.request(bot_id, "POST", "/computer/" + action, json={})

    @router.get("/api/v1/bots/{bot_id}/computer/thumbnail", dependencies=[Depends(owner)])
    def thumbnail(bot_id: BotId):
        with db.connection() as conn:
            if native.paused(conn, bot_id):
                return {"image": None, "paused": True}
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
