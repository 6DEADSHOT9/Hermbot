"""Restart the worker while a real native terminal execution is active."""
import json
from pathlib import Path
import time

from backend import db
from .live_acceptance import submit
from .protocol_acceptance import wait
from .recovery_acceptance import signal


def main():
    db.pool.open(wait=True)
    task = submit("Live proof · active worker recovery", "writer", "Controlled native execution test. Your FIRST tool call must be the native terminal tool, command: python -c 'import time; time.sleep(45); print(\"ONE_ACTIVE_NATIVE_EXECUTION\")'. Actually execute this command, wait for its output, then publish active-worker-recovery.txt containing that output with hermbot_publish, and ONLY THEN call hermbot_finish. Do not claim execution without a real terminal tool call. Do not delegate, ask for review, or finish early. The owner will restart the worker while the command is sleeping.")
    deadline = time.monotonic() + 120
    while True:
        with db.connection() as conn:
            started = conn.execute("SELECT 1 FROM events WHERE task_id=%s AND kind='tool.started' AND data->>'tool'='terminal'", (task["id"],)).fetchone()
        if started:
            break
        assert time.monotonic() < deadline, "Expected the real terminal delay to start"
        time.sleep(0.5)
    signal("restart-worker", "active-worker", task_id=task["id"])
    completed = wait(task["id"], timeout=180)
    assert completed["attempts"] == 1
    with db.connection() as conn:
        attempt = conn.execute("SELECT id,runtime_run_id,status FROM attempts WHERE task_id=%s", (task["id"],)).fetchone()
    evidence = {"live_model_acceptance": True, "check": "worker restarted during terminal execution without duplicate dispatch", "task_id": task["id"], "attempt_id": str(attempt["id"]), "runtime_run_id": attempt["runtime_run_id"], "attempts": completed["attempts"], "status": completed["status"]}
    Path("/app/test-results/active-worker-recovery.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence), flush=True)
    db.pool.close()


if __name__ == "__main__":
    main()
