"""Chrome DevTools protocol helpers shared by the adapter and the private owner session."""
import fcntl
import json
import os
import subprocess
import threading
import time

import httpx
from websockets.sync.client import connect

COOKIE_FIELDS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")
MAX_COOKIES, MAX_PAGES = 5000, 10


def cookies(items):
    """Settable cookie fields only, bounded. Session cookies carry no expiry."""
    result = []
    for item in items[:MAX_COOKIES]:
        if not (isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("domain"), str)):
            continue
        cookie = {key: item[key] for key in COOKIE_FIELDS if key in item}
        if item.get("session") or not isinstance(cookie.get("expires"), (int, float)) or cookie["expires"] < 0:
            cookie.pop("expires", None)
        result.append(cookie)
    return result


def pages(targets):
    return [target["url"] for target in targets if target.get("type") == "page"
            and str(target.get("url", "")).startswith(("http://", "https://")) and len(target["url"]) <= 2048][:MAX_PAGES]


class Pipe:
    """The protocol over --remote-debugging-pipe: NUL-separated JSON on the browser's fds 3 and 4."""

    def __init__(self, write_fd, read_fd):
        self.write_fd, self.read_fd, self.buffer, self.seq = write_fd, read_fd, b"", 0
        self.lock = threading.Lock()

    def call(self, method, **params):
        with self.lock:
            self.seq += 1
            os.write(self.write_fd, json.dumps({"id": self.seq, "method": method, "params": params}).encode() + b"\0")
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                while b"\0" not in self.buffer:
                    chunk = os.read(self.read_fd, 1 << 16)
                    if not chunk:
                        raise RuntimeError("The browser closed")
                    self.buffer += chunk
                message, self.buffer = self.buffer.split(b"\0", 1)
                reply = json.loads(message)
                if reply.get("id") == self.seq:
                    if "error" in reply:
                        raise RuntimeError(reply["error"].get("message", "Browser command failed"))
                    return reply.get("result", {})
            raise RuntimeError("The browser did not answer")

    def close(self):
        for fd in (self.write_fd, self.read_fd):
            try:
                os.close(fd)
            except OSError:
                pass


class Socket:
    """The protocol over a running browser's local debugging port."""

    def __init__(self, port):
        version = httpx.get(f"http://127.0.0.1:{port}/json/version", timeout=5, trust_env=False).json()
        self.connection, self.seq = connect(version["webSocketDebuggerUrl"], max_size=64 << 20, open_timeout=5), 0

    def call(self, method, **params):
        self.seq += 1
        self.connection.send(json.dumps({"id": self.seq, "method": method, "params": params}))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            reply = json.loads(self.connection.recv(timeout=max(0.1, deadline - time.monotonic())))
            if reply.get("id") == self.seq:
                if "error" in reply:
                    raise RuntimeError(reply["error"].get("message", "Browser command failed"))
                return reply.get("result", {})
        raise RuntimeError("The browser did not answer")

    def close(self):
        self.connection.close()


def launch(argv, environment):
    """Start Chromium with a private protocol pipe; nothing else can reach it."""
    to_browser, from_browser = os.pipe(), os.pipe()

    def descriptors():
        # Copy both ends clear of 3 and 4 first so neither placement overwrites the other.
        incoming, outgoing = fcntl.fcntl(to_browser[0], fcntl.F_DUPFD, 10), fcntl.fcntl(from_browser[1], fcntl.F_DUPFD, 10)
        os.dup2(incoming, 3)
        os.dup2(outgoing, 4)
    process = subprocess.Popen([*argv, "--remote-debugging-pipe"], preexec_fn=descriptors, pass_fds=(3, 4), env=environment,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(to_browser[0])
    os.close(from_browser[1])
    return process, Pipe(to_browser[1], from_browser[0])
