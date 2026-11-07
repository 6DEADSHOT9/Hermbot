"""Live recovery checks. The supervising agent performs explicitly signalled Docker actions."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from backend import db
from .live_acceptance import submit
from .protocol_acceptance import client, state, wait

output = Path("/app/test-results")
checks = []


def record(name, **details):
    checks.append({"check": name, **details})
    (output / "recovery-acceptance.json").write_text(json.dumps({"live_model_acceptance": True, "checks": checks}, indent=2))
    print(json.dumps(checks[-1]), flush=True)


def signal(action, name, **details):
    done = output / ("recovery-" + name + "-done")
    done.unlink(missing_ok=True)
    (output / "recovery-signal.json").write_text(json.dumps({"action": action, "name": name, **details}))
    print(json.dumps({"awaiting_external_action": action, "name": name, **details}), flush=True)
    deadline = time.monotonic() + 180
    while not done.exists():
        assert time.monotonic() < deadline, "Recovery action was not acknowledged"
        time.sleep(1)


def descendants(root):
    return [t for t in state()["tasks"] if t["root_id"] == root and t["parent_id"]]


def main():
    assert not state().get("protocol_fixture")
    db.pool.open(wait=True)
    review = submit("Live recovery · persisted owner review", "writer", "Call hermbot_review to ask the owner to allow publication of an exact local artifact. Summary: Publish the local recovery report. Payload: {artifact_name: recovery-report.md, content: Review survived runtime recreation}. End this turn after requesting review. Once the owner allows it, publish that exact local artifact with hermbot_publish and finish. This is a local artifact, not an external write.")
    wait(review["id"], ("waiting_approval",), timeout=180)
    deadline = time.monotonic() + 60
    while any(e["status"] in ("running", "dispatching") for e in client.get("/api/v1/tasks/" + review["id"]).json()["executions"]):
        assert time.monotonic() < deadline
        time.sleep(0.5)
    pending = next(a for a in state()["approvals"] if a["task_id"] == review["id"] and a["status"] == "pending")
    signal("recreate", "review", bot_id="writer", task_id=review["id"])
    restored = next(a for a in state()["approvals"] if a["id"] == pending["id"])
    assert restored["status"] == "pending" and restored["payload_hash"] == pending["payload_hash"]
    decision = client.post("/api/v1/approvals/" + pending["id"], json={"decision": "once", "expected_payload_hash": pending["payload_hash"]})
    decision.raise_for_status()
    result = wait(review["id"], timeout=180)
    record("pending owner review survives runtime recreation and resumes once", task_id=result["id"], approval_id=pending["id"], attempts=result["attempts"])
    worker_task = submit("Live recovery · worker restart", "writer", "Run terminal sleep 20, then publish worker-recovery.txt containing exactly ONE_NATIVE_ATTEMPT, call hermbot_finish with that result, and end. Do not delegate or request review; this local delay tests the worker's durable dispatch receipt.")
    wait(worker_task["id"], ("running",), timeout=120)
    signal("restart-worker", "worker", task_id=worker_task["id"])
    result = wait(worker_task["id"], timeout=180)
    assert result["attempts"] == 1
    record("worker restart preserves one native execution", task_id=result["id"], attempts=result["attempts"])
    manager = submit("Live recovery · interrupted delegation", "reviewer", "Coordinate two teammates to report the sum of 17 and 25. First delegate to Scout (researcher) with this exact instruction: If you are Scout, first run terminal sleep 45 before doing anything else; if you are Quill after a recovery reassignment, skip the delay. Calculate 17+25 and publish an evidence artifact, then finish. Next delegate to Quill (writer) to turn that evidence into a short report artifact, declaring depends_on the first assignment UUID. End your turn after both assignments. If an execution is interrupted or failed, inspect the child, confirm it is settled, and use hermbot_reassign to replace it with Quill. Do not create another independent assignment to retry it. The worker rewires the blocked writing dependency to the replacement. Combine the settled outputs into a final artifact and concise result.", manager=True)
    deadline = time.monotonic() + 150
    interrupted = None
    while time.monotonic() < deadline:
        children = descendants(manager["id"])
        target = next((t for t in children if t["bot_id"] == "researcher" and t["status"] == "running"), None)
        if target:
            with db.connection() as conn:
                started = conn.execute("SELECT 1 FROM events WHERE task_id=%s AND kind='tool.started' AND data->>'tool'='terminal'", (target["id"],)).fetchone()
            if started:
                interrupted = target
                break
        time.sleep(0.5)
    assert interrupted, "Expected the child to enter its controlled local delay"
    redirect = client.post("/api/v1/tasks/" + manager["id"] + "/steer", json={"prompt": "Include the marker RECOVERED_V1 in your final artifact and final result after recovery."})
    redirect.raise_for_status()
    signal("recreate", "delegation", bot_id="researcher", task_id=interrupted["id"], manager_id=manager["id"])
    result = wait(manager["id"], timeout=420)
    children = descendants(manager["id"])
    assert any(t["id"] == interrupted["id"] and t["status"] in ("interrupted", "cancelled", "failed") for t in children)
    assert result["handoffs"] == 3 and "RECOVERED_V1" in (result["output"] or "")
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM events WHERE task_id=%s AND kind='task.reassigned'", (manager["id"],)).fetchone()
        assert conn.execute("SELECT 1 FROM controls WHERE task_id=%s AND status='included'", (manager["id"],)).fetchone()
    record("interrupted native delegation, settled reassignment, rewired dependency and queued redirection", task_id=result["id"], interrupted_task_id=interrupted["id"], handoffs=result["handoffs"])
    cancelled = submit("Live recovery · cancel complete operation", "reviewer", "Assign one independent task to Scout and one to Quill, each to run terminal sleep 45, then publish a short local artifact. End your turn while those two tasks run. Do not finish the combined operation until both return; the owner will test stopping this entire operation.", manager=True)
    deadline = time.monotonic() + 150
    while sum(t["status"] == "running" for t in descendants(cancelled["id"])) < 2:
        assert time.monotonic() < deadline, "Expected two active child executions"
        time.sleep(0.5)
    client.post("/api/v1/tasks/" + cancelled["id"] + "/stop").raise_for_status()
    wait(cancelled["id"], ("cancelled",), timeout=120)
    deadline = time.monotonic() + 120
    while any(t["status"] not in ("cancelled", "completed", "failed", "interrupted") for t in descendants(cancelled["id"])):
        assert time.monotonic() < deadline, "Owned children did not settle after stop"
        time.sleep(0.5)
    assert all(t["status"] == "cancelled" for t in descendants(cancelled["id"]))
    record("owner stop cancels the complete active operation", task_id=cancelled["id"], child_count=len(descendants(cancelled["id"])))
    scheduled_group = client.post("/api/v1/groups", json={"title": "Live proof · native scheduled collaboration", "bot_ids": ["researcher", "writer"]})
    scheduled_group.raise_for_status()
    payload = {"bot_id": "researcher", "conversation_id": scheduled_group.json()["id"], "prompt": "Delegate a brief evidence note to Quill: the supplied measurement is 42, with no uncertainty bound given. Ask Quill to publish a markdown artifact identifying that limitation, then end your turn. When resumed, return the artifact ID and concise conclusion. Do not browse or use external services.", "scheduled_at": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}
    routine = client.post("/api/v1/routines", json=payload, headers={"Idempotency-Key": str(uuid4())})
    routine.raise_for_status()
    result = wait(routine.json()["task_id"], timeout=240)
    assert result["context"] == "routine" and result["handoffs"] == 1
    record("real-model native scheduled collaboration survives without a browser", routine_id=routine.json()["id"], task_id=result["id"], attempts=result["attempts"])
    db.pool.close()


if __name__ == "__main__":
    main()
