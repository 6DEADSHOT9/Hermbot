"""Native one-shot cron collaboration against the deterministic fixture stack."""
from datetime import datetime, timedelta, timezone
import json
import time
from uuid import uuid4

from .protocol_acceptance import client, record, state, wait


def main():
    deadline = time.monotonic() + 60
    while any(t["status"] in ("running", "queued", "waiting_children") for t in state()["tasks"]):
        assert time.monotonic() < deadline, "Existing tasks must settle before reserving a bot"
        time.sleep(1)
    chat = next(c["id"] for c in state()["conversations"] if c["bot_id"] == "researcher")
    payload = {"bot_id": "researcher", "conversation_id": chat, "prompt": "[POC:research] Native scheduled collaboration: ask Quill to return an artifact.", "scheduled_at": (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()}
    key = str(uuid4())
    response = client.post("/api/v1/routines", json=payload, headers={"Idempotency-Key": key})
    response.raise_for_status()
    routine = response.json()
    print(json.dumps({"routine_id": routine["id"], "status": routine["status"], "task_id": routine["task_id"]}), flush=True)
    duplicate = client.post("/api/v1/routines", json=payload, headers={"Idempotency-Key": key})
    duplicate.raise_for_status()
    assert duplicate.json()["id"] == routine["id"]
    task = wait(routine["task_id"], timeout=150)
    assert task["context"] == "routine" and task["handoffs"] == 1 and task["attempts"] == 2
    record("native scheduled admission, handoff, artifact and API continuation", task)


if __name__ == "__main__":
    main()
