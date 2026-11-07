"""Real native runtimes: dynamic lifecycle, explicit clone state and attachment/thread work."""
import base64
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from .protocol_acceptance import client, state, wait

checks = []


def request(method, path, body=None, key=None):
    result = client.request(method, path, json=body, headers={"Idempotency-Key": key or str(uuid4())})
    result.raise_for_status()
    return result.json()


def record(name, **receipt):
    checks.append({"check": name, **receipt})
    Path("/app/test-results/phase2-lifecycle.json").write_text(json.dumps({"real_native_runtimes": True, "checks": checks}, indent=2))
    print(json.dumps(checks[-1]), flush=True)


def bot(identity):
    return next(b for b in state()["bots"] if b["id"] == identity)


def operation(identity):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        item = next(o for o in request("GET", "/api/v1/bot-operations") if o["id"] == identity)
        if item["status"] == "completed":
            return item
        assert item["status"] != "failed", item["error"]
        time.sleep(1)
    raise AssertionError("Lifecycle operation did not settle")


def ready(identity):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        item = bot(identity)
        if item["readiness"].get("compatible"):
            return item
        time.sleep(1)
    raise AssertionError("New runtime did not become compatible")


def action(identity, name):
    item = bot(identity)
    result = request("POST", f"/api/v1/bots/{identity}/{name}", {"expected_revision": item["revision"], "confirmation": item["name"] if name == "delete" else None})
    operation(result["id"])
    return result


def main():
    assert state()["provider_configured"] and not state()["protocol_fixture"]
    config = {"name": "Phase 2 lifecycle proof", "role": "A concise local arithmetic assistant. Follow the owner's instructions precisely.", "avatar": "🧮", "enabled_tools": ["file", "terminal"], "memory_mb": 1024, "cpus": 1}
    resume = os.environ.get("HERMBOT_RESUME_PROOF")
    if resume:
        checks.extend(json.loads(Path("/app/test-results/phase2-lifecycle.json").read_text())["checks"])
        finish_lifecycle(resume, config, retry=True)
        return
    key = str(uuid4())
    created = request("POST", "/api/v1/bots", config, key)
    duplicate = request("POST", "/api/v1/bots", config, key)
    assert duplicate["bot"]["id"] == created["bot"]["id"]
    identity = created["bot"]["id"]
    operation(created["operation"]["id"])
    ready(identity)
    record("dynamic create and duplicate request", bot_id=identity)
    conversation = next(c["id"] for c in state()["conversations"] if c["bot_id"] == identity)
    attachment = request("POST", f"/api/v1/conversations/{conversation}/attachments", {"name": "numbers.csv", "content_type": "text/csv", "content_base64": base64.b64encode(b"item,value\nA,12\nB,30\n").decode()})
    task = request("POST", "/api/v1/tasks", {"bot_id": identity, "conversation_id": conversation, "attachment_ids": [attachment["id"]], "prompt": "Read the attached numbers.csv from the supplied workspace path using a native file or terminal tool. Compute the sum. Write /workspace/phase2-proof.txt containing exactly CLONE_PROOF_42 and publish a concise report artifact containing the total 42. Finish. Do not delegate."})
    result = wait(task["id"], timeout=240)
    tools = [e["data"].get("tool") for e in request("GET", "/api/v1/tasks/" + task["id"])["events"] if e["kind"] == "tool.started"]
    assert "read_file" in tools or "terminal" in tools
    assets = [a for a in state()["artifacts"] if a["task_id"] == task["id"]]
    assert assets and "42" in result["output"]
    message = next(m for m in request("GET", f"/api/v1/conversations/{conversation}")["messages"] if m["task_id"] == task["id"] and m["role"] == "user")
    request("POST", f"/api/v1/messages/{message['id']}/reactions", {"emoji": "✅"})
    thread = request("POST", "/api/v1/tasks", {"bot_id": identity, "conversation_id": conversation, "reply_to_message_id": message["id"], "prompt": "This is a reply thread. Publish a small text artifact with THREAD_PROOF_42 and finish. Do not delegate."})
    wait(thread["id"], timeout=240)
    record("native attachment read, durable output, reaction and separate reply thread", task_id=task["id"], thread_task_id=thread["id"], attachment_id=attachment["id"])
    finish_lifecycle(identity, config)


def finish_lifecycle(identity, config, retry=False):
    config["name"] = "Phase 2 clone proof"
    if retry:
        cloned = next(b["id"] for b in state()["bots"] if b["name"] == config["name"] and b["status"] == "error")
        pending = next(o for o in request("GET", "/api/v1/bot-operations") if o["bot_id"] == cloned and o["action"] == "clone" and o["status"] == "failed")
        request("POST", f"/api/v1/bot-operations/{pending['id']}/retry")
        operation(pending["id"])
        record("failed partial clone recovers using the same operation", operation_id=pending["id"], clone_bot_id=cloned)
    else:
        clone = request("POST", f"/api/v1/bots/{identity}/clone", {"expected_revision": bot(identity)["revision"], "configuration": config, "copy_workspace": True, "copy_history": True})
        cloned = clone["bot"]["id"]
        operation(clone["operation"]["id"])
    ready(cloned)
    ready(identity)
    clone_chat = next(c["id"] for c in state()["conversations"] if c["bot_id"] == cloned)
    check = request("POST", "/api/v1/tasks", {"bot_id": cloned, "conversation_id": clone_chat, "prompt": "Use a native file or terminal tool to read /workspace/phase2-proof.txt. If and only if it contains CLONE_PROOF_42 publish a text artifact containing CLONE_VERIFIED_42. Finish. Do not delegate."})
    result = wait(check["id"], timeout=240)
    assert any(e["kind"] == "tool.started" and e["data"].get("tool") in {"read_file", "terminal"} for e in request("GET", "/api/v1/tasks/" + check["id"])["events"])
    assert "CLONE_VERIFIED_42" in result["output"] or any("CLONE_VERIFIED_42" in client.get("/api/v1/artifacts/" + a["id"]).text for a in state()["artifacts"] if a["task_id"] == check["id"])
    record("explicit workspace/native history clone with independent runtime", source_bot_id=identity, clone_bot_id=cloned, task_id=check["id"])
    action(cloned, "archive")
    assert bot(cloned)["status"] == "archived"
    action(cloned, "restore")
    ready(cloned)
    action(cloned, "stop")
    assert bot(cloned)["desired_state"] == "stopped"
    action(cloned, "start")
    ready(cloned)
    record("archive, restore, stop and start preserve the teammate", bot_id=cloned)
    action(cloned, "delete")
    action(identity, "delete")
    assert not any(b["id"] in (identity, cloned) for b in state()["bots"])
    record("owner-confirmed deletion of the two acceptance bots", deleted_bot_ids=[identity, cloned])


if __name__ == "__main__":
    main()
