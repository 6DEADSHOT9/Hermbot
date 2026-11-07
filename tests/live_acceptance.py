"""Owner-authorized real-model Phase 1 checks; no synthetic model responses."""
import json
from pathlib import Path
import time
from uuid import uuid4

from .protocol_acceptance import client, state, wait

checks = []


def submit(title, bot, prompt, manager=False):
    group = client.post("/api/v1/groups", json={"title": title, "bot_ids": ["researcher", "writer", "reviewer"]})
    group.raise_for_status()
    payload = {"bot_id": bot, "conversation_id": group.json()["id"], "prompt": prompt}
    if manager:
        payload["managed_bot_ids"] = [b for b in ("researcher", "writer", "reviewer") if b != bot]
    key = str(uuid4())
    result = client.post("/api/v1/tasks", json=payload, headers={"Idempotency-Key": key})
    result.raise_for_status()
    duplicate = client.post("/api/v1/tasks", json=payload, headers={"Idempotency-Key": key})
    duplicate.raise_for_status()
    assert result.json()["id"] == duplicate.json()["id"]
    print(json.dumps({"check": title, "task_id": result.json()["id"], "status": "submitted"}), flush=True)
    return result.json()


def verify(task, title, expected_handoffs=1, expected_text=None):
    assert task["handoffs"] >= expected_handoffs, "Expected actual collaboration"
    snapshot = state()
    children = [t for t in snapshot["tasks"] if t["root_id"] == task["id"] and t["parent_id"]]
    assert children and all(t["status"] == "completed" for t in children)
    assets = [a for a in snapshot["artifacts"] if any(t["id"] == a["task_id"] and t["root_id"] == task["id"] for t in snapshot["tasks"])]
    assert assets, "Expected a durable artifact"
    contents = "\n".join(client.get("/api/v1/artifacts/" + a["id"]).text for a in assets)
    assert "[Protocol fixture]" not in contents + (task["output"] or "")
    if expected_text:
        assert expected_text in contents + (task["output"] or "")
    checks.append({"check": title, "task_id": task["id"], "status": task["status"], "attempts": task["attempts"], "handoffs": task["handoffs"], "artifacts": [{"id": a["id"], "name": a["name"], "sha256": a["sha256"]} for a in assets]})
    Path("/app/test-results/live-acceptance.json").write_text(json.dumps({"live_model_acceptance": True, "checks": checks}, indent=2))
    print(json.dumps(checks[-1]), flush=True)


def main():
    assert state()["provider_configured"] and not state().get("protocol_fixture")
    deadline = time.monotonic() + 90
    while not all(b["readiness"].get("compatible") for b in state()["bots"]):
        assert time.monotonic() < deadline, "Runtimes did not become compatible"
        time.sleep(1)
    research = submit("Live proof · research and writing", "researcher", "Use only this supplied source brief: Trial A recruited 120 people and reported a 12% improvement; Trial B recruited 80 people and reported 8%; neither has long-term follow-up. Investigate what the brief supports and its limitations. Explicitly delegate the writing of a short evidence report to Quill (writer), ask for a durable markdown artifact, end your turn while waiting, then combine its result. Do not browse or invent additional sources. Keep the report under 250 words.")
    verify(wait(research["id"], timeout=300), "Research and named writing delegation")
    data = submit("Live proof · data and autonomous handoff", "writer", "Analyze this supplied CSV: item,units,price\nA,3,12\nB,4,7\nC,2,15\nChoose an appropriate teammate autonomously to verify the arithmetic using local code in their own workspace. Delegate that verification, yield until the result returns, then publish a CSV artifact with each line revenue and the grand total, and give a concise conclusion. Teammates receive only the data you explicitly send. Do not contact external services.")
    verify(wait(data["id"], timeout=300), "Data processing and autonomous teammate choice", expected_text="94")
    manager = submit("Live proof · manager and dependent assignments", "reviewer", "Coordinate your two permitted teammates for this code review. First assign Quill (writer) to implement a Python function average(values) that returns None for an empty list and the arithmetic mean otherwise, and publish the code as an artifact. Then assign Scout (researcher) to review that implementation and run meaningful tests in Scout's own workspace. Declare the review dependency with depends_on so it waits for the implementation; use the returned assignment UUID. The backend grants dependency artifacts automatically. Each teammate must publish its result and finish. End your turn after scheduling both assignments. When resumed, inspect outcomes and publish a short final review artifact with the implementation artifact ID and test evidence. Do not install packages or use external services.", manager=True)
    verify(wait(manager["id"], timeout=420), "Manager, code review, dependency and two isolated bots", expected_handoffs=2)


if __name__ == "__main__":
    main()
