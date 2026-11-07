"""Local-only acceptance fixture setup/probes inside a bot; never prints entered input."""

import asyncio
import json
from pathlib import Path
import sys
import urllib.request

from websockets.asyncio.client import connect


async def main():
    for _ in range(40):
        try:
            port = int(Path("/browser-state/profile/DevToolsActivePort").read_text().splitlines()[0])
            pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
            break
        except (OSError, ValueError):
            await asyncio.sleep(0.5)
    else:
        raise AssertionError("Native browser did not expose its local CDP receipt")
    target = next(
        (p for p in pages if p["type"] == "page" and "127.0.0.1:8099" in p["url"]),
        next(p for p in pages if p["type"] == "page"),
    )
    async with connect(target["webSocketDebuggerUrl"], proxy=None) as ws:

        async def command(name, params):
            await ws.send(json.dumps({"id": 1, "method": name, "params": params}))
            while True:
                result = json.loads(await ws.recv())
                if result.get("id") == 1:
                    assert "error" not in result
                    return result.get("result", {})

        if sys.argv[1] == "prepare":
            await command(
                "Network.deleteCookies", {"name": "phase3_human_done", "url": "http://127.0.0.1:8099/"}
            )
            await command("Page.navigate", {"url": "http://127.0.0.1:8099/"})
            await asyncio.sleep(1)
            await command("Page.bringToFront", {})
            await command("Runtime.evaluate", {"expression": "document.getElementById('private').focus()"})
        else:
            if sys.argv[1] == "reload":
                await command("Page.navigate", {"url": "http://127.0.0.1:8099/"})
                await asyncio.sleep(1)
            result = await command(
                "Runtime.evaluate",
                {
                    "expression": "document.cookie.includes('phase3_human_done=42') && document.body.innerText.includes('PHASE3_HUMAN_DONE') && !document.getElementById('private')",
                    "returnByValue": True,
                },
            )
            assert result["result"].get("value") is True, "Local form did not receive and clear secure input"
            print("Local form received and cleared secure input; browser persistence cookie present.")


asyncio.run(main())
