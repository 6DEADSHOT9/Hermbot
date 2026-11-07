"""Drive explicit local acceptance signals; touch only validated Hermbot resources."""

import json
from pathlib import Path
import subprocess
import time

root = Path(__file__).resolve().parent.parent
output = root / "test-results"

RESTORE = """
import httpx
from fastapi.encoders import jsonable_encoder
from uuid import uuid4
from backend import db, registry
from backend.secrets import decrypt
from backend.settings import settings
db.pool.open(wait=True)
with db.connection() as conn:
    bot = registry.require_bot(conn, BOT_ID)
    provider = conn.execute("SELECT * FROM providers WHERE id=%s", (bot['provider_id'],)).fetchone()
    timezone = registry.workspace(conn)['timezone']
    assert bot['status'] == 'active' and bot['desired_state'] == 'running'
    assert not conn.execute("SELECT 1 FROM computer_controls WHERE bot_id=%s AND holder='human'", (BOT_ID,)).fetchone()
spec = dict(bot_id=BOT_ID, operation_id=str(uuid4()), revision=bot['revision'],
    configuration=registry.configuration(bot), model_base_url=provider['base_url'],
    model_api_key=decrypt(provider['secret_encrypted']), bot_token=decrypt(bot['bot_token_encrypted']),
    runtime_key=decrypt(bot['runtime_key_encrypted']), timezone=timezone, desired_state='running')
with httpx.Client(timeout=110) as client:
    response = client.post('http://supervisor:8001/operations/ensure',
        headers={'Authorization':'Bearer '+settings.supervisor_token}, json=jsonable_encoder(spec))
    response.raise_for_status()
assert response.json()['state'] == 'running'
db.pool.close()
"""


def run(*args, **kwargs):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True, **kwargs)


def main():
    # Finite acceptance-only driver, started immediately before the corresponding test.
    started = time.time()
    handled = set()
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        for name in ("recovery-signal.json", "phase2-work-signal.json", "phase3-ui-signal.json"):
            path = output / name
            if not path.exists() or path.stat().st_mtime < started:
                continue
            raw = path.read_text()
            signature = (name, path.stat().st_mtime_ns, raw)
            if signature in handled:
                continue
            signal = json.loads(raw)
            action = signal["action"]
            if action in ("pause-worker", "resume-worker", "restart-worker"):
                verb = {"pause-worker": "pause", "resume-worker": "unpause", "restart-worker": "restart"}[
                    action
                ]
                run("docker", "compose", verb, "worker")
            elif action in ("recreate", "stop-native-desktop"):
                identity = signal["bot_id"]
                assert identity in {"researcher", "writer"}
                container = "hermbot-bot-" + identity
                labels = json.loads(
                    run("docker", "inspect", container, "--format", "{{json .Config.Labels}}").stdout
                )
                assert labels["com.hermbot.project"] == "hermbot"
                assert labels["com.hermbot.managed"] == "true"
                assert labels["com.hermbot.kind"] == "runtime" and labels["com.hermbot.bot-id"] == identity
                if action == "stop-native-desktop":
                    run(
                        "docker",
                        "exec",
                        "--user",
                        "10000",
                        container,
                        "/opt/hermes/.venv/bin/python",
                        "-c",
                        "from tools.bot_desktop import runtime; runtime.stop()",
                    )
                else:
                    # Failure injection preserves all three private volumes. The supervisor
                    # restores the server-owned configuration without printing credentials.
                    run("docker", "stop", container)
                    run("docker", "rm", container)
                    run(
                        "docker",
                        "compose",
                        "exec",
                        "-T",
                        "worker",
                        "python",
                        "-",
                        input="BOT_ID=" + repr(identity) + "\n" + RESTORE,
                    )
            else:
                raise AssertionError("Unrecognized acceptance action")
            if name == "recovery-signal.json":
                assert signal["name"] in {"review", "worker", "delegation", "active-worker"}
                receipt = "recovery-" + signal["name"] + "-done"
            elif name == "phase2-work-signal.json":
                assert action in {"pause-worker", "resume-worker"}
                receipt = "phase2-" + action + "-done"
            else:
                receipt = "phase3-stop-desktop-done"
            (output / receipt).write_text("completed\n")
            handled.add(signature)
            print(json.dumps({"acceptance_action_completed": action, "signal": name}), flush=True)
        time.sleep(0.5)


if __name__ == "__main__":
    main()
