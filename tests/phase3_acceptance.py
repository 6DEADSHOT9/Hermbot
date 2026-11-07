"""Phase 3 acceptance against actual isolated Hermes runtimes and the owner's configured model."""

import hashlib
import json
from pathlib import Path

from .phase2_acceptance import request, operation, ready, bot
from .protocol_acceptance import state, wait

OUTPUT = Path("/app/test-results/phase3-native.json")
checks = []


def record(name, **details):
    checks.append({"check": name, **details})
    OUTPUT.write_text(json.dumps({"real_native_runtimes": True, "checks": checks}, indent=2))
    print(name, flush=True)


def configure(identity, tools):
    item = bot(identity)
    keys = [
        "name",
        "role",
        "color",
        "avatar",
        "provider_id",
        "model_name",
        "capabilities",
        "enabled_tools",
        "enabled_skills",
        "memory_mb",
        "cpus",
    ]
    config = {k: item[k] for k in keys}
    config["enabled_tools"] = tools
    result = request("PUT", "/api/v1/bots/" + identity, {**config, "expected_revision": item["revision"]})
    operation(result["operation"]["id"])
    ready(identity)


def save(identity, kind, name, content, expected_hash, enable=None):
    body = {"content": content, "expected_hash": expected_hash}
    if enable is not None:
        body["enable"] = enable
    result = request("PUT", f"/api/v1/bots/{identity}/knowledge/{kind}/{name}", body)
    operation(result["operation"]["id"])
    ready(identity)
    return result


def main():
    assert not state()["protocol_fixture"] and state()["provider_configured"]
    identity = "researcher"
    configure(identity, ["file", "terminal", "skills", "memory", "browser", "computer_use"])
    knowledge = request("GET", f"/api/v1/bots/{identity}/knowledge")
    old = next(m for m in knowledge["memory"] if m["name"] == "memory")
    # Retain pre-existing native entries; add and remove only this acceptance note.
    content = old["content"] + "\nPHASE3_MEMORY_PROOF: Verify sums against the granted source file."
    saved = save(identity, "memory", "memory", content, old["sha256"])
    assert (
        next(
            m for m in request("GET", f"/api/v1/bots/{identity}/knowledge")["memory"] if m["name"] == "memory"
        )["content"]
        == content
    )
    restored = save(identity, "memory", "memory", old["content"], saved["version"]["sha256"])
    assert restored["version"]["version"] == saved["version"]["version"] + 1
    record(
        "native memory inspection, edit, removal and immutable version history",
        bot_id=identity,
        version_ids=[saved["version"]["id"], restored["version"]["id"]],
    )
    name = "hermbot-phase3-report"
    current = request("GET", f"/api/v1/bots/{identity}/knowledge/skills/{name}")
    skill = f"---\nname: {name}\ndescription: Analyze granted CSV data and return reviewed versioned reports\n---\n\n# Procedure\n\n1. Use read_file or hermbot_read_artifact on the explicitly granted CSV.\n2. Compute the sum and check the data.\n3. If human assistance is required, ask hermbot_review kind input with input_type secure and bot_id. End the turn. Never read or repeat the secret.\n4. Return a durable report with hermbot_publish and finish.\n\nPHASE3_SKILL_USED: include this marker in completed reports using this procedure.\n"
    saved = save(identity, "skill", name, skill, current["sha256"], True)
    assert name in bot(identity)["enabled_skills"]
    newer = save(
        identity,
        "skill",
        name,
        skill + "\nReview the artifact provenance before publication.\n",
        saved["version"]["sha256"],
        True,
    )
    record(
        "native skill creation, versioning, explicit enable and runtime refresh",
        bot_id=identity,
        skill=name,
        versions=[saved["version"]["version"], newer["version"]["version"]],
    )
    screen = request("POST", f"/api/v1/bots/{identity}/computer/start", {})
    assert screen["desktop"]["running"] and screen["lease"]["holder"] == "agent"
    assert request("GET", f"/api/v1/bots/{identity}/computer/thumbnail")["image"].startswith("data:image/")
    record(
        "real native isolated desktop start and thumbnail",
        bot_id=identity,
        geometry=screen["desktop"].get("geometry"),
    )
    chat = next(c["id"] for c in state()["conversations"] if c["bot_id"] == identity)
    task = request(
        "POST",
        "/api/v1/tasks",
        {
            "bot_id": identity,
            "conversation_id": chat,
            "skill_refs": [name],
            "prompt": f'First call skill_view with name {name} to load the enabled skill. Then propose a reviewed improvement to your user memory by calling hermbot_review with kind memory and payload name user, content exactly "PHASE3_REVIEWED_MEMORY: Prefer source checks before summaries." and summary "Review proposed source-checking preference". End your turn after the request. When resumed with a saved knowledge result, finish with hermbot_finish result PHASE3_LEARNING_SAVED. Do not apply the memory yourself, access other files, delegate or request another review.',
        },
    )
    wait(task["id"], statuses=("waiting_approval",), timeout=240)
    review = next(a for a in state()["approvals"] if a["task_id"] == task["id"] and a["status"] == "pending")
    assert review["kind"] == "memory"
    before = request("GET", f"/api/v1/bots/{identity}/knowledge")
    assert (
        next(m for m in before["memory"] if m["name"] == "user")["content"]
        != review["payload"]["action"]["content"]
    )
    old_user = next(m for m in before["memory"] if m["name"] == "user")
    # Owner edits the model's draft before approving; old review is definitively invalid.
    action = {
        k: v
        for k, v in review["payload"]["action"].items()
        if k not in ("expected_hash", "expected_revision")
    }
    action["content"] = "PHASE3_REVIEWED_MEMORY: Check source values before producing summaries."
    updated = request(
        "PUT",
        "/api/v1/approvals/" + review["id"],
        {
            "expected_payload_hash": review["payload_hash"],
            "summary": "Owner-edited source-checking preference",
            "payload": action,
        },
    )
    from .protocol_acceptance import client

    stale = client.post(
        "/api/v1/approvals/" + review["id"],
        json={"decision": "once", "expected_payload_hash": review["payload_hash"]},
    )
    assert stale.status_code == 409
    fresh = next(a for a in state()["approvals"] if a["id"] == updated["approval_id"])
    request(
        "POST",
        "/api/v1/approvals/" + fresh["id"],
        {"decision": "once", "expected_payload_hash": fresh["payload_hash"]},
    )
    result = wait(task["id"], timeout=240)
    assert "PHASE3_LEARNING_SAVED" in result["output"]
    assert (
        next(
            m for m in request("GET", f"/api/v1/bots/{identity}/knowledge")["memory"] if m["name"] == "user"
        )["content"]
        == action["content"]
    )
    detail = request("GET", "/api/v1/tasks/" + task["id"])
    assert any(
        e["kind"] == "tool.started" and e["data"].get("tool") == "skill_view" for e in detail["events"]
    )
    record(
        "actual native skill use, editable model learning review, stale rejection and post-refresh continuation",
        task_id=task["id"],
        old_approval_id=review["id"],
        approval_id=fresh["id"],
    )
    save(
        identity,
        "memory",
        "user",
        old_user["content"],
        hashlib.sha256(action["content"].encode()).hexdigest(),
    )
    draft = request("GET", "/api/v1/tasks/" + task["id"] + "/skill-draft")
    workflow = save(identity, "skill", draft["name"], draft["content"], draft["expected_hash"], False)
    record(
        "completed workflow saved as a reviewed reusable skill draft",
        task_id=task["id"],
        workflow_name=draft["name"],
        version_id=workflow["version"]["id"],
    )


if __name__ == "__main__":
    main()
