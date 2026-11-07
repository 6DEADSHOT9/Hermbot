"""Real model/tool handoff test on the local machine; secrets stay in memory."""

import json
from pathlib import Path
import subprocess
import time
import urllib.request
from uuid import uuid4

root = Path(__file__).resolve().parent.parent
owner_token = next(
    line.split("=", 1)[1].strip().strip("\"'")
    for line in (root / ".env").read_text().splitlines()
    if line.startswith("OWNER_TOKEN=")
)


def api(method, path, body=None):
    request = urllib.request.Request(
        "http://127.0.0.1:8780" + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + owner_token,
            "Content-Type": "application/json",
            "Idempotency-Key": str(uuid4()),
        },
    )
    with urllib.request.urlopen(request, timeout=80) as response:
        return json.load(response)


def main():
    snapshot = api("GET", "/api/v1/state")
    chat = next(c["id"] for c in snapshot["conversations"] if c["bot_id"] == "researcher")
    nonce = str(int(time.time()))
    marker = f"/workspace/phase3-inflight-{nonce}.started"
    counter = f"/workspace/phase3-inflight-{nonce}.count"
    command = f"python -c \"from pathlib import Path; import time; p=Path('{counter}'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); Path('{marker}').write_text('started'); time.sleep(8); print('PHASE3_INFLIGHT_FINISHED')\""
    task = api(
        "POST",
        "/api/v1/tasks",
        {
            "bot_id": "researcher",
            "conversation_id": chat,
            "prompt": f"On this first turn, your first tool must be terminal with this exact foreground command and timeout at least 15 seconds: {command}. Do not background it. After it returns, publish phase3-inflight-{nonce}.txt containing PHASE3_INFLIGHT_DONE, then hermbot_finish with PHASE3_INFLIGHT_DONE. If the owner takes over, end the turn and wait for handback. On resume after handback, use the existing terminal result, do NOT run terminal or the command again, and publish and finish only. Do not delegate, change settings or request reviews.",
        },
    )
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        exists = (
            subprocess.run(
                ["docker", "exec", "hermbot-bot-researcher", "test", "-f", marker], capture_output=True
            ).returncode
            == 0
        )
        if exists:
            break
        time.sleep(0.2)
    else:
        raise AssertionError("Real native terminal did not enter the foreground operation")
    started = time.monotonic()
    control = api("POST", "/api/v1/bots/researcher/computer/control", {"action": "takeover"})
    delay = time.monotonic() - started
    assert delay > 4 and control["input_ready"] and control["lease"]["holder"] == "human"
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        status = api("GET", "/api/v1/tasks/" + task["id"])
        if status["status"] == "waiting_computer":
            break
        time.sleep(0.5)
    else:
        raise AssertionError("The interrupted turn did not persist a computer handback wait")
    api("POST", "/api/v1/bots/researcher/computer/control", {"action": "handback"})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status = api("GET", "/api/v1/tasks/" + task["id"])
        if status["status"] == "completed":
            break
        time.sleep(0.5)
    assert status["status"] == "completed" and "PHASE3_INFLIGHT_DONE" in status["output"]
    count = subprocess.run(
        ["docker", "exec", "hermbot-bot-researcher", "cat", counter],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert count == "1"
    receipt = {
        "real_native_model": True,
        "task_id": task["id"],
        "status": status["status"],
        "takeover_wait_seconds": round(delay, 3),
        "native_effect_count": int(count),
        "human_input_waited_for_inflight_tool": True,
        "native_turn_paused_and_resumed": True,
    }
    (root / "test-results" / "phase3-inflight.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
