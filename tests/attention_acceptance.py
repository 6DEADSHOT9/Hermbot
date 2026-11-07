"""Opt-in real local attention proof. Never answers a question or grants browser permission."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

from .recurring_management_acceptance import INSTRUCTIONS, OPS, request

PATH = Path("/app/test-results/phase54-live.json")


def save(proof):
    PATH.write_text(json.dumps(proof, indent=2, default=str))


def start():
    if PATH.exists():
        return status()
    snapshot = request("GET", "/api/v1/state")
    assert not any(
        t["status"] not in {"completed", "failed", "cancelled", "interrupted"} for t in snapshot["tasks"]
    ), "Existing owner work must settle first"
    marker = uuid4().hex[:12]
    proof = {
        "marker": marker,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "native_model": True,
        "external_mutations_requested": False,
        "owner_answer_simulated": False,
        "original_groups": [c["id"] for c in snapshot["conversations"] if c["kind"] == "group"],
        "original_active_routines": {
            r["id"]: hashlib.sha256(json.dumps(r["definition"], sort_keys=True).encode()).hexdigest()
            for r in request("GET", "/api/v1/routines")["data"]
            if r["state"] == "active"
        },
    }
    save(proof)
    group = request(
        "POST",
        "/api/v1/groups",
        {
            "title": "Phase 5.4 attention acceptance " + marker,
            "bot_ids": ["researcher", "writer", "reviewer"],
        },
    )
    proof["conversation_id"] = group["id"]
    save(proof)
    grant = request(
        "POST",
        "/api/v1/grants",
        {
            "manager_id": "reviewer",
            "targets": ["researcher", "writer"],
            "operations": OPS,
            "duration_hours": 24,
            "max_created_bots": 0,
        },
        "phase54-grant:" + marker,
    )
    proof["grant_id"] = grant["id"]
    save(proof)
    instructions = (
        "First call hermbot_ask with answer_type choice, prompt 'Continue the local Phase 5.4 notification demonstration?', choices ['Continue local test','Stop local test']. End the turn and wait for the actual owner answer. On Continue local test carry out the following instructions. On Stop local test stop. Never answer on behalf of the owner.\n"
        + INSTRUCTIONS.replace("Phase 5.3", "Phase 5.4")
    )
    definition = {
        "name": "Phase 5.4 attention manager " + marker,
        "bot_id": "reviewer",
        "conversation_id": group["id"],
        "instructions": instructions,
        "requirements": [
            {"key": "report", "title": "Reviewed attention demonstration: 42, 110 and 152", "kind": "report"}
        ],
        "management": {"grant_id": grant["id"], "targets": ["researcher", "writer"], "operations": OPS},
        "schedule": {
            "kind": "interval",
            "timezone": "Asia/Kolkata",
            "at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "interval_minutes": 60,
        },
        "active_budget_seconds": 900,
        "start_window_minutes": 15,
        "max_runs_per_day": 1,
        "enabled": False,
    }
    routine = request("POST", "/api/v1/routines", definition, "phase54-routine:" + marker)
    proof["routine_id"] = routine["id"]
    save(proof)
    run = request(
        "POST",
        f"/api/v1/routines/{routine['id']}/run",
        {"expected_revision": routine["revision"]},
        "phase54-run:" + marker,
    )
    proof["task_id"] = run["task_id"]
    proof["run_id"] = run["id"]
    save(proof)
    status()


def status():
    proof = json.loads(PATH.read_text())
    task = request("GET", "/api/v1/tasks/" + proof["task_id"])
    proof["status"] = task["status"]
    proof["attempts"] = [
        {k: a.get(k) for k in ("id", "session_id", "runtime_run_id", "status")} for a in task["executions"]
    ]
    questions = [
        q for q in request("GET", "/api/v1/state")["input_requests"] if q["task_id"] == proof["task_id"]
    ]
    proof["question_ids"] = [q["id"] for q in questions]
    inbox = request("GET", "/api/v1/attention?view=needs")
    proof["attention_ids"] = [i["id"] for i in inbox["items"] if i["refs"].get("task_id") == proof["task_id"]]
    proof["checked_at"] = datetime.now(timezone.utc).isoformat()
    save(proof)
    print(
        json.dumps(
            {
                k: proof.get(k)
                for k in (
                    "marker",
                    "status",
                    "conversation_id",
                    "task_id",
                    "question_ids",
                    "attention_ids",
                    "attempts",
                )
            }
        )
    )


def verify():
    import re
    from backend import db, attention

    status()
    proof = json.loads(PATH.read_text())
    db.pool.open(wait=True)
    try:
        with db.connection() as conn:
            manager = conn.execute("SELECT * FROM tasks WHERE id=%s", (proof["task_id"],)).fetchone()
            assert manager["status"] == "completed"
            assert all(number in manager["output"] for number in ("42", "110", "152"))
            children = conn.execute(
                "SELECT * FROM tasks WHERE parent_id=%s ORDER BY bot_id", (manager["id"],)
            ).fetchall()
            assert len(children) == 2 and {c["bot_id"] for c in children} == {"researcher", "writer"}
            for child in children:
                assert child["status"] == "completed" and child["grant_id"] == manager["grant_id"]
                assert child["management_work_item_id"] == manager["management_work_item_id"]
                terminal = conn.execute(
                    "SELECT data FROM events WHERE task_id=%s AND kind='tool.completed' AND data->>'tool'='terminal' AND data->>'error'='false'",
                    (child["id"],),
                ).fetchall()
                expected = "42" if child["bot_id"] == "researcher" else "110"
                assert any(
                    re.search(
                        r"(?<!\d)" + expected + r"(?!\d)", json.loads(e["data"]["preview"]).get("output", "")
                    )
                    for e in terminal
                )
            results = conn.execute(
                "SELECT * FROM work_outcomes WHERE work_item_id=%s AND required", (manager["work_item_id"],)
            ).fetchall()
            assert len(results) == 1 and results[0]["status"] == "fulfilled"
            contributions = conn.execute(
                "SELECT * FROM result_contributions WHERE outcome_id=%s", (results[0]["id"],)
            ).fetchall()
            assert len(contributions) == 2 and all(c["status"] == "accepted" for c in contributions)
            artifacts = conn.execute(
                "SELECT id,task_id FROM artifacts WHERE task_id=ANY(%s)", ([c["id"] for c in children],)
            ).fetchall()
            assert len(artifacts) == 2
            for artifact in artifacts:
                receipt = conn.execute(
                    "SELECT response FROM tool_receipts WHERE task_id=%s AND operation='read_artifact' AND response->>'artifact_id'=%s",
                    (manager["id"], str(artifact["id"])),
                ).fetchone()
                assert receipt and receipt["response"]["content"]
            attempts = conn.execute(
                "SELECT id,session_id FROM attempts WHERE task_id=%s ORDER BY number", (manager["id"],)
            ).fetchall()
            assert len(attempts) >= 2 and len({a["session_id"] for a in attempts}) == 1
            assert str(attempts[0]["id"]) == proof["before_final_restart"]["attempts"][0]["id"]
            question = conn.execute(
                "SELECT * FROM input_requests WHERE task_id=%s", (manager["id"],)
            ).fetchone()
            assert (
                question["status"] == "answered"
                and question["answer_source"] == "owner"
                and question["answer"] == "Continue local test"
            )
            attention.project(conn, "task", manager["id"])
            result = conn.execute(
                "SELECT id,version FROM attention_items WHERE subject_type='work' AND subject_id=%s AND purpose='result' AND active",
                (manager["work_item_id"],),
            ).fetchone()
            assert result
            assert not conn.execute(
                "SELECT 1 FROM attention_items WHERE subject_id=%s AND needs_owner AND active",
                (manager["id"],),
            ).fetchone()
            assert not conn.execute(
                "SELECT 1 FROM connector_operations o JOIN tasks t ON t.id=o.task_id WHERE t.conversation_id=%s",
                (proof["conversation_id"],),
            ).fetchone()
            proof["native_verified"] = {
                "accepted_contributions": [str(c["task_id"]) for c in contributions],
                "artifact_ids": [str(a["id"]) for a in artifacts],
                "session_id": attempts[0]["session_id"],
                "manager_attempts": len(attempts),
                "actual_owner_answer_verified": True,
                "restart_identity_verified": True,
                "result_attention_id": str(result["id"]),
                "result_version": result["version"],
                "native_terminal_verified": True,
                "manager_read_both_artifacts": True,
                "connected_provider_operations": 0,
            }
            proof["cleanup_pending"] = (
                "Retained this one completed demonstration until the owner desktop-push check."
            )
    finally:
        db.pool.close()
    save(proof)
    print(json.dumps(proof["native_verified"]))


def notify():
    from backend import db, notifications

    proof = json.loads(PATH.read_text())
    assert proof.get("native_verified"), "Verify the real native demonstration first"
    db.pool.open(wait=True)
    try:
        with db.connection() as conn:
            assert notifications.preferences(conn)["push_enabled"], "The owner must enable browser push first"
            item = conn.execute(
                "SELECT * FROM attention_items WHERE id=%s",
                (proof["native_verified"]["result_attention_id"],),
            ).fetchone()
            assert item and item["active"] and item["read_version"] < item["version"], (
                "The current result must still be unread"
            )
            notifications.enqueue(conn, item)
    finally:
        db.pool.close()
    print("Current result queued under actual browser opt-in and notification preferences.")


def cleanup():
    proof = json.loads(PATH.read_text())
    assert proof.get("native_verified") and proof.get("actual_os_push_verified"), (
        "Finish actual desktop delivery acceptance before cleanup"
    )
    row = next(r for r in request("GET", "/api/v1/routines")["data"] if r["id"] == proof["routine_id"])
    if row["state"] != "archived":
        request(
            "POST",
            f"/api/v1/routines/{row['id']}/archive",
            {"expected_revision": row["revision"]},
            "phase54-cleanup:" + proof["marker"],
        )
    request("DELETE", "/api/v1/groups/" + proof["conversation_id"])
    request("POST", "/api/v1/grants/" + proof["grant_id"] + "/revoke")
    snapshot = request("GET", "/api/v1/state")
    assert set(proof["original_groups"]).issubset(c["id"] for c in snapshot["conversations"])
    routines = request("GET", "/api/v1/routines")["data"]
    for identity, fingerprint in proof["original_active_routines"].items():
        row = next(r for r in routines if r["id"] == identity)
        assert (
            row["state"] == "active"
            and hashlib.sha256(json.dumps(row["definition"], sort_keys=True).encode()).hexdigest()
            == fingerprint
        )
    proof["cleanup_complete"] = True
    proof.pop("cleanup_pending", None)
    save(proof)
    print(
        "Only the verified Phase 5.4 fixture group/routine/grant were archived or revoked. Owner browser registrations and preferences preserved."
    )


if __name__ == "__main__":
    {"start": start, "status": status, "verify": verify, "notify": notify, "cleanup": cleanup}[
        sys.argv[1] if len(sys.argv) > 1 else "status"
    ]()
