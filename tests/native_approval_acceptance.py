"""Deny a harmless, nonexistent-path command through Hermes's native approval bridge."""
import json
from pathlib import Path
import time

from .live_acceptance import submit
from .protocol_acceptance import client, state, wait


def main():
    assert not state().get("protocol_fixture")
    task = submit("Live proof · native approval bridge", "writer", "This is a local native-approval test. The owner will DENY the command and no file should be removed. Call the terminal tool with exactly this command once: `rm -rf /workspace/hermbot-approval-proof-never-created`\nDo not include any other paths or arguments. Do not run a substitute command or use hermbot_review; we are verifying the terminal's native approval bridge. After the owner denies it, do not retry the command. Publish native-approval-proof.txt saying the command was denied and did not run, then call hermbot_finish and end.")
    deadline = time.monotonic() + 150
    approval = None
    while time.monotonic() < deadline:
        snapshot = state()
        approval = next((a for a in snapshot["approvals"] if a["task_id"] == task["id"] and a["kind"] == "runtime" and a["status"] == "pending"), None)
        if approval:
            break
        item = next(t for t in snapshot["tasks"] if t["id"] == task["id"])
        assert item["status"] not in ("completed", "failed", "cancelled", "uncertain"), "Runtime did not expose the expected native approval"
        time.sleep(0.5)
    assert approval and approval["payload"].get("request_id"), "Expected an exact native request identity"
    result = client.post("/api/v1/approvals/" + approval["id"], json={"decision": "deny", "expected_payload_hash": approval["payload_hash"]})
    result.raise_for_status()
    completed = wait(task["id"], timeout=180)
    evidence = {"live_model_acceptance": True, "check": "native approval request, exact request ID, owner denial and resumed execution", "task_id": completed["id"], "approval_id": approval["id"], "request_id": approval["payload"]["request_id"], "status": completed["status"]}
    Path("/app/test-results/native-approval.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
