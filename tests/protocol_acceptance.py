"""Run explicitly against the opt-in fixture stack; prints only non-secret receipts."""
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx

base = os.environ.get("HERMBOT_TEST_URL", "http://api:8000")
client = httpx.Client(base_url=base, headers={"Authorization": "Bearer " + os.environ["OWNER_TOKEN"]}, timeout=20)
evidence = []


def state():
    response = client.get("/api/v1/state")
    response.raise_for_status()
    return response.json()


def send(bot, prompt, *, conversation=None, managed=False):
    chat = conversation or next(c["id"] for c in state()["conversations"] if c["bot_id"] == bot)
    payload = {"bot_id": bot, "conversation_id": chat, "prompt": prompt}
    if managed:
        payload["managed_bot_ids"] = [b for b in ("researcher", "writer", "reviewer") if b != bot]
    key = str(uuid4())
    response = client.post("/api/v1/tasks", json=payload, headers={"Idempotency-Key": key})
    response.raise_for_status()
    duplicate = client.post("/api/v1/tasks", json=payload, headers={"Idempotency-Key": key})
    duplicate.raise_for_status()
    assert response.json()["id"] == duplicate.json()["id"]
    return response.json()


def wait(task_id, statuses=("completed",), timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        item = next(t for t in state()["tasks"] if t["id"] == task_id)
        if item["status"] in statuses:
            return item
        if item["status"] in ("failed", "uncertain", "cancelled", "interrupted"):
            raise AssertionError(f"Task {task_id}: {item['status']}: {item['error']}")
        time.sleep(0.5)
    raise AssertionError(f"Task {task_id} did not reach {statuses}; last status {item['status']}")


def record(name, task):
    evidence.append({"check": name, "task_id": task["id"], "status": task["status"], "attempts": task["attempts"], "handoffs": task["handoffs"]})
    print(json.dumps(evidence[-1]), flush=True)


def main():
    initial = state()
    assert initial["provider_configured"]
    for bot in initial["bots"]:
        assert bot["readiness"].get("compatible") and bot["readiness"].get("plugin_ready"), bot["id"]
    research = wait(send("researcher", "[POC:research] Research and explicitly delegate report writing to Quill.")["id"])
    assert research["handoffs"] == 1 and research["attempts"] == 2
    assets = [a for a in state()["artifacts"] if any(t["id"] == a["task_id"] and t["root_id"] == research["id"] for t in state()["tasks"])]
    assert assets
    assert "Protocol fixture report" in client.get("/api/v1/artifacts/" + assets[0]["id"]).text
    record("named delegation, continuation, artifact and duplicate submission", research)
    group = client.post("/api/v1/groups", json={"title": "Phase 1 protocol acceptance", "bot_ids": ["researcher", "writer", "reviewer"]})
    group.raise_for_status()
    manager = wait(send("reviewer", "[POC:manager] Coordinate research, then writing using a dependency.", conversation=group.json()["id"], managed=True)["id"])
    assert manager["handoffs"] == 2 and manager["attempts"] == 2
    children = [t for t in state()["tasks"] if t["parent_id"] == manager["id"]]
    assert len(children) == 2 and all(t["status"] == "completed" for t in children)
    record("group manager, two bots, dependency and combined result", manager)
    review = wait(send("writer", "[POC:review] Ask the owner to review the fixture report.")["id"], ("waiting_approval",))
    pending = next(a for a in state()["approvals"] if a["task_id"] == review["id"])
    decision = client.post("/api/v1/approvals/" + pending["id"], json={"decision": "deny", "expected_payload_hash": pending["payload_hash"]})
    decision.raise_for_status()
    record("owner review and denied continuation", wait(review["id"]))
    stopped = send("researcher", "[POC:slow] Wait while the owner tests stop.")
    wait(stopped["id"], ("running",))
    result = client.post("/api/v1/tasks/" + stopped["id"] + "/stop")
    result.raise_for_status()
    record("native run stop settles after executor exits", wait(stopped["id"], ("cancelled",)))
    Path("/app/test-results/protocol-acceptance.json").write_text(json.dumps({"model": "deterministic protocol fixture", "live_model_acceptance": False, "checks": evidence}, indent=2))


if __name__ == "__main__":
    main()
