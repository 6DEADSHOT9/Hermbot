"""Private owner session for computer takeover: a fresh screen and browser the bot never touched.

The supervisor runs this in its own short-lived container as an unprivileged user, on a network
only the API joins. The bot's container cannot reach it. Chromium is driven over an inherited pipe,
so no debugging port exists; the API seeds the bot's cookies and pages, then collects the cookies
when the owner hands back.
"""
import asyncio
import hmac
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, WebSocket
from pydantic import BaseModel, ConfigDict, Field
import uvicorn

sys.path.insert(0, "/opt/hermbot")  # Isolated mode ignores PYTHONPATH.
import browser_control  # noqa: E402

TOKEN = os.environ.pop("OWNER_SESSION_TOKEN", "")
GEOMETRY = os.environ.get("OWNER_SESSION_GEOMETRY", "1440x900")
if not re.fullmatch(r"[1-9][0-9]{2,3}x[1-9][0-9]{2,3}", GEOMETRY):
    GEOMETRY = "1440x900"
HOME = Path("/tmp/owner")
SOCKET = HOME / "rfb.sock"
DISPLAY = ":1"
ENVIRONMENT = {"HOME": str(HOME), "PATH": "/usr/bin:/bin", "DISPLAY": DISPLAY, "XAUTHORITY": str(HOME / "Xauthority")}
app = FastAPI(docs_url=None, redoc_url=None)
processes = {}


def authorize(authorization):
    if not TOKEN or not hmac.compare_digest(authorization or "", "Bearer " + TOKEN):
        raise HTTPException(401, "Unauthorized")


def start_screen():
    HOME.mkdir(mode=0o700, exist_ok=True)
    subprocess.run(["xauth", "-q", "-f", ENVIRONMENT["XAUTHORITY"], "add", DISPLAY, ".", os.urandom(16).hex()],
                   env=ENVIRONMENT, check=True, stderr=subprocess.DEVNULL)
    processes["screen"] = subprocess.Popen(
        ["Xvnc", DISPLAY, "-rfbunixpath", str(SOCKET), "-rfbunixmode", "0600", "-rfbport", "-1", "-SecurityTypes", "None",
         "-nolisten", "tcp", "-auth", ENVIRONMENT["XAUTHORITY"], "-geometry", GEOMETRY, "-depth", "24", "-AlwaysShared",
         "-desktop", "Hermbot private session"],
        env=ENVIRONMENT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if SOCKET.exists():
            break
        time.sleep(0.1)
    processes["windows"] = subprocess.Popen(["xfwm4", "--compositor=off"], env=ENVIRONMENT,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch_browser():
    width, height = GEOMETRY.split("x")
    processes["browser"], pipe = browser_control.launch(
        ["/usr/bin/chromium", f"--user-data-dir={HOME / 'profile'}", "--no-first-run", "--no-default-browser-check",
         "--password-store=basic", "--disable-sync", "--no-sandbox", "--test-type", "--disable-dev-shm-usage", "--start-maximized",
         f"--window-size={width},{height}", "--window-position=0,0", "about:blank"], ENVIRONMENT)
    return pipe


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cookies: list[dict] = Field(default_factory=list, max_length=browser_control.MAX_COOKIES)
    urls: list[str] = Field(default_factory=list, max_length=browser_control.MAX_PAGES)


@app.get("/health")
def health(authorization: str = Header(default="")):
    authorize(authorization)
    screen, browser = processes.get("screen"), processes.get("browser")
    return {"ready": bool(screen and screen.poll() is None and SOCKET.exists()), "browser": bool(browser and browser.poll() is None)}


@app.post("/start")
def start(seed: Seed, authorization: str = Header(default="")):
    authorize(authorization)
    if "pipe" in processes:
        raise HTTPException(409, "The private browser has already started")
    cookies = browser_control.cookies(seed.cookies)
    urls = [url for url in seed.urls if re.match(r"^https?://", url) and len(url) <= 2048]
    pipe = processes["pipe"] = launch_browser()
    if cookies:
        pipe.call("Storage.setCookies", cookies=cookies)
    blank = [target["targetId"] for target in pipe.call("Target.getTargets")["targetInfos"] if target["type"] == "page"]
    for url in urls:
        pipe.call("Target.createTarget", url=url)
    if urls:
        for target in blank:
            pipe.call("Target.closeTarget", targetId=target)
    return {"started": True, "cookies": len(cookies), "pages": len(urls)}


@app.post("/finish")
def finish(authorization: str = Header(default="")):
    authorize(authorization)
    pipe = processes.pop("pipe", None)
    if not pipe:
        return {"cookies": []}
    cookies = pipe.call("Storage.getCookies")["cookies"]
    try:
        pipe.call("Browser.close")
    except RuntimeError:
        pass
    return {"cookies": browser_control.cookies(cookies)}


@app.websocket("/view")
async def view(ws: WebSocket):
    try:
        authorize(ws.headers.get("authorization"))
        reader, writer = await asyncio.open_unix_connection(str(SOCKET))
    except (HTTPException, OSError):
        await ws.close(code=4401)
        return
    await ws.accept()

    async def receive():
        while True:
            packet = await ws.receive()
            data = packet.get("bytes")
            if packet["type"] == "websocket.disconnect" or data is None or len(data) > 1_000_000:
                return
            writer.write(data)
            await writer.drain()

    async def send():
        while data := await reader.read(1 << 16):
            await ws.send_bytes(data)

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        try:
            await ws.close()
        except RuntimeError:
            pass


if __name__ == "__main__":
    start_screen()
    uvicorn.run(app, host="0.0.0.0", port=8650, log_level="warning", access_log=False)
