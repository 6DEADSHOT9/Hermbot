"""Native skill catalog and selected skill invocation, with group mention metadata."""
import json
from pathlib import Path

from .phase2_acceptance import action, operation, ready, request
from .protocol_acceptance import state, wait


def main():
    catalog = request("GET", "/api/v1/bots/researcher/skills")["data"]
    assert any(s["name"] == "hermes-agent" for s in catalog)
    existing = next((b for b in state()["bots"] if b["name"] == "Phase 2 skills proof" and b["status"] == "error"), None)
    if existing:
        identity = existing["id"]
        failed = next(o for o in request("GET", "/api/v1/bot-operations") if o["bot_id"] == identity and o["status"] == "failed")
        request("POST", f"/api/v1/bot-operations/{failed['id']}/retry")
        operation(failed["id"])
    else:
        created = request("POST", "/api/v1/bots", {"name": "Phase 2 skills proof", "role": "Consult the selected native Hermes skill and report concise local evidence.", "enabled_tools": ["file", "terminal", "skills"], "enabled_skills": ["hermes-agent"], "memory_mb": 1024, "cpus": 1})
        identity = created["bot"]["id"]
        operation(created["operation"]["id"])
    ready(identity)
    group = request("POST", "/api/v1/groups", {"title": "Phase 2 skill and mention proof", "bot_ids": [identity, "researcher", "writer"]})
    request("PUT", f"/api/v1/groups/{group['id']}", {"title": "Phase 2 skill and mention proof", "bot_ids": [identity, "researcher"]})
    task = request("POST", "/api/v1/tasks", {"bot_id": identity, "conversation_id": group["id"], "skill_refs": ["hermes-agent"], "mention_bot_ids": ["researcher"], "prompt": "@Scout: this mention is context only. Your first tool call must be skill_view for hermes-agent. Read that selected skill through the native skill tool, summarize one fact about the local Hermes agent from it, then publish a text artifact containing SKILL_REFERENCE_PROOF and that fact, and finish. Do not delegate, contact external services, or change settings."})
    completed = wait(task["id"], timeout=240)
    tools = [e["data"].get("tool") for e in request("GET", "/api/v1/tasks/" + task["id"])["events"] if e["kind"] == "tool.started"]
    assert "skill_view" in tools, "Expected actual native skill consultation"
    chat = request("GET", f"/api/v1/conversations/{group['id']}")
    assert next(m for m in chat["messages"] if m["task_id"] == task["id"] and m["role"] == "user")["mentions"] == ["researcher"]
    artifacts = [a for a in state()["artifacts"] if a["task_id"] == task["id"]]
    assert artifacts and "SKILL_REFERENCE_PROOF" in completed["output"]
    action(identity, "delete")
    evidence = {"check": "native skill catalog, enabled skill reference and actual skill_view execution; mention metadata and group membership edit", "installed_skills": len(catalog), "task_id": task["id"], "native_tools": tools, "deleted_test_bot_id": identity, "status": completed["status"]}
    Path("/app/test-results/phase2-skills.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
