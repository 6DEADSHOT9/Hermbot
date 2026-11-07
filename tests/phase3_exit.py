"""Real orchestration exit gate, including owner-assisted browser work and immutable outputs."""

import base64
import json
from pathlib import Path
import sys
import time

from .phase3_acceptance import save
from .phase2_acceptance import request
from .protocol_acceptance import state, wait

EVIDENCE = Path("/app/test-results/phase3-exit.json")


def main():
    if sys.argv[1] == "start":
        skill = "hermbot-phase3-orchestrate"
        current = request("GET", "/api/v1/bots/reviewer/knowledge/skills/" + skill)
        content = f"---\nname: {skill}\ndescription: Coordinate granted research and writing, collect owner reviews and publish versioned reports\n---\n\n# Reviewed orchestration procedure\n\nLoad the granted source artifact. Delegate source analysis to Scout with the exact artifact ID. Have Scout request owner secure input when needed and end its turn. Never approve a review yourself. Delegate synthesis to Quill only after Scout completes. Publish the final result with immutable versions and call hermbot_finish. Follow the current management grant; do not broaden authority.\n\nPHASE3_ORCHESTRATION_SKILL_USED\n"
        save("reviewer", "skill", skill, content, current["sha256"], True)
        chat = next(c["id"] for c in state()["conversations"] if c["bot_id"] == "reviewer")
        nonce = str(int(time.time()))
        asset = request(
            "POST",
            "/api/v1/library",
            {
                "name": f"phase3-exit-{nonce}.csv",
                "content_type": "text/csv",
                "content_base64": base64.b64encode(b"item,value\nA,12\nB,30\n").decode(),
                "conversation_id": chat,
            },
        )
        filename = f"phase3-orchestrated-{nonce}.md"
        scout = f'First load skill hermbot-phase3-report using skill_view. Read explicitly granted artifact {asset["id"]} using hermbot_read_artifact or its supplied native file path and calculate total. Then use browser_navigate to visit http://127.0.0.1:8099/ in your local native browser. Request hermbot_review kind input with summary "Owner assistance needed on local computer" and payload {{"input_type":"secure","bot_id":"researcher","instructions":"Type private test input into the focused password field and submit. Hand back the computer, then acknowledge this review."}}. End your turn. When the owner acknowledges completion, use browser_navigate on that same URL again. Only if the page says PHASE3_HUMAN_DONE and checked total 42, publish a short report artifact with total 42, PHASE3_HUMAN_DONE and PHASE3_SKILL_USED, and call hermbot_finish with those markers. Never read or print private input. Do not delegate, change knowledge or call browser_type.'
        prompt = f"Follow the enabled skill {skill}; first load it via skill_view. You manage ONLY Scout (researcher) and Quill (writer) for this exact operation. First publish {filename} content_type text/markdown with PHASE3_EXIT_DRAFT. Delegate to Scout with artifact_ids [{asset['id']}] and this exact work:\n{scout}\nEnd your turn after delegation. Wait for Scout to complete; required owner reviews will be resolved through the web app, you cannot approve them. Once Scout completes, delegate to Quill to read Scout's granted output artifact and synthesize a concise report with total 42 and markers PHASE3_HUMAN_DONE and PHASE3_SKILL_USED. Include the output artifact ID in artifact_ids; end your turn after delegating. When Quill completes, publish {filename} again in text/markdown containing total 42, PHASE3_EXIT_FINAL, PHASE3_HUMAN_DONE, PHASE3_SKILL_USED and PHASE3_ORCHESTRATION_SKILL_USED. This must be version 2 of your initial draft. Then hermbot_finish with the final report. No lifecycle proposals, external services, repeated reviews or extra assignments."
        task = request(
            "POST",
            "/api/v1/tasks",
            {
                "bot_id": "reviewer",
                "conversation_id": chat,
                "prompt": prompt,
                "managed_bot_ids": ["researcher", "writer"],
                "artifact_ids": [asset["id"]],
                "skill_refs": [skill],
            },
        )
        EVIDENCE.write_text(
            json.dumps(
                {
                    "root_task_id": task["id"],
                    "source_artifact_id": asset["id"],
                    "output_name": filename,
                    "conversation_id": chat,
                    "status": "in_progress",
                },
                indent=2,
            )
        )
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            child = next(
                (t for t in state()["tasks"] if t["root_id"] == task["id"] and t["bot_id"] == "researcher"),
                None,
            )
            if child and child["status"] == "waiting_approval":
                print("Real manager and Scout are waiting for owner computer assistance.", flush=True)
                return
            time.sleep(1)
        raise AssertionError("Scout did not request human assistance")
    evidence = json.loads(EVIDENCE.read_text())
    result = wait(evidence["root_task_id"], timeout=360)
    for marker in (
        "PHASE3_EXIT_FINAL",
        "PHASE3_HUMAN_DONE",
        "PHASE3_SKILL_USED",
        "PHASE3_ORCHESTRATION_SKILL_USED",
        "42",
    ):
        assert marker in result["output"], marker
    children = [t for t in state()["tasks"] if t["root_id"] == result["id"] and t["parent_id"]]
    assert {t["bot_id"] for t in children} == {"researcher", "writer"} and all(
        t["status"] == "completed" for t in children
    )
    outputs = request("GET", "/api/v1/library?q=" + evidence["output_name"])["items"]
    final = next(a for a in outputs if a["name"] == evidence["output_name"])
    detail = request("GET", "/api/v1/library/" + final["id"])
    assert final["version"] == 2 and len(detail["versions"]) == 2
    root = request("GET", "/api/v1/tasks/" + result["id"])
    scout = request("GET", "/api/v1/tasks/" + next(t["id"] for t in children if t["bot_id"] == "researcher"))
    assert any(e["kind"] == "tool.started" and e["data"].get("tool") == "skill_view" for e in root["events"])
    assert any(
        e["kind"] == "tool.started" and e["data"].get("tool") in ("browser_navigate", "browser_exec")
        for e in scout["events"]
    )
    assert "HERM_PHASE3_SECRET_CANARY_9V" not in json.dumps(state()) + json.dumps(root) + json.dumps(scout)
    evidence.update(
        status=result["status"],
        child_task_ids=[t["id"] for t in children],
        final_artifact_id=final["id"],
        versions=2,
        real_skill_and_browser_tools=True,
        secret_absent_from_product_records=True,
    )
    EVIDENCE.write_text(json.dumps(evidence, indent=2))
    print("Phase 3 real orchestration exit gate passed.", flush=True)


if __name__ == "__main__":
    main()
