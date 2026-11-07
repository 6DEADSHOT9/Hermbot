"""Owner lifecycle recreation plus a native browser cookie/form persistence proof."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

from phase3_inflight import api, root


def run(*args, **kwargs):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True, **kwargs)


def identity():
    return json.loads(
        run(
            "docker",
            "inspect",
            "hermbot-bot-researcher",
            "--format",
            '{"id":{{json .Id}},"mounts":{{json .Mounts}}}',
        ).stdout
    )


def verify():
    api("POST", "/api/v1/bots/researcher/computer/start")
    api("POST", "/api/v1/bots/researcher/computer/control", {"action": "takeover"})
    try:
        api("POST", "/api/v1/bots/researcher/computer/browser")
        # This local-only fixture is stored in the bot workspace; its process must restart.
        run(
            "docker",
            "exec",
            "--user",
            "10000",
            "-d",
            "hermbot-bot-researcher",
            "/opt/hermes/.venv/bin/python",
            "-m",
            "http.server",
            "8099",
            "--bind",
            "127.0.0.1",
            "--directory",
            "/workspace/phase3-computer-fixture",
        )
        run(
            "docker",
            "exec",
            "--user",
            "10000",
            "-i",
            "hermbot-bot-researcher",
            "/opt/hermes/.venv/bin/python",
            "-",
            "reload",
            input=(root / "tests/native_browser_probe.py").read_text(),
        )
    finally:
        api("POST", "/api/v1/bots/researcher/computer/control", {"action": "handback"})


def main():
    verify()
    before = identity()
    bot = next(b for b in api("GET", "/api/v1/state")["bots"] if b["id"] == "researcher")
    receipt = api("POST", "/api/v1/bots/researcher/start", {"expected_revision": bot["revision"]})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        operation = next(o for o in api("GET", "/api/v1/bot-operations") if o["id"] == receipt["id"])
        assert operation["status"] != "failed", "Browser persistence recreation failed"
        bot = next(b for b in api("GET", "/api/v1/state")["bots"] if b["id"] == "researcher")
        if operation["status"] == "completed" and bot["readiness"].get("compatible"):
            break
        time.sleep(1)
    else:
        raise AssertionError("Browser persistence recreation did not settle")
    after = identity()
    assert before["id"] != after["id"]

    def volumes(value):
        return sorted((m["Name"], m["Destination"]) for m in value["mounts"])

    assert volumes(before) == volumes(after)
    verify()
    proof = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "bot_id": "researcher",
        "operation_id": receipt["id"],
        "old_container_id": before["id"],
        "new_container_id": after["id"],
        "private_volumes": volumes(after),
        "private_profile_survived_recreation": True,
        "cleared_input_verified": True,
    }
    Path(root / "test-results/phase3-browser-persistence.json").write_text(json.dumps(proof, indent=2))
    print("Native browser profile and cleared input survived owner lifecycle recreation.")


if __name__ == "__main__":
    main()
