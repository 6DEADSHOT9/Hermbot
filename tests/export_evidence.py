"""Export non-secret execution receipts for the completed Phase 1 demonstrations."""
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path

from backend import db
from .protocol_acceptance import state

output = Path("/app/test-results")
protocol_roots = [
    ("named delegation, continuation, artifact and duplicate submission", "c040d788-efa0-4521-8128-a0c56f659cfc"),
    ("group manager, two bots, dependency and combined result", "c85c988c-aaeb-412b-ab1a-876b30add65b"),
    ("owner review and denied continuation", "989798c2-8fca-4892-a6b9-38f3f4c8cb7e"),
    ("native run stop settles after executor exits", "9cc39e3e-8a91-4249-8fcc-3eb490e5cd06"),
    ("native one-shot scheduled collaboration", "f3f93c87-8e3f-4d8b-9be9-45a09f150c88"),
]


def main():
    db.pool.open(wait=True)
    with db.connection() as conn:
        protocol = []
        for name, task_id in protocol_roots:
            task = conn.execute("SELECT id,status,attempts,handoffs FROM tasks WHERE id=%s", (task_id,)).fetchone()
            assert task and task["status"] in ("completed", "cancelled")
            protocol.append({"check": name, "task_id": str(task["id"]), **{k: task[k] for k in ("status", "attempts", "handoffs")}})
        (output / "protocol-acceptance.json").write_text(json.dumps({"model": "deterministic protocol fixture", "live_model_acceptance": False, "exported_from_durable_receipts": True, "checks": protocol}, indent=2))
        roots = set()
        for name in ("live-acceptance.json", "recovery-acceptance.json", "active-worker-recovery.json", "native-approval.json", "browser-live-disconnect.json"):
            report = json.loads((output / name).read_text())
            roots.update(check["task_id"] for check in report.get("checks", [report]))
        receipts = []
        for root in sorted(roots):
            tasks = conn.execute("SELECT id,bot_id,parent_id,status,attempts,handoffs FROM tasks WHERE root_id=%s ORDER BY created_at", (root,)).fetchall()
            ids = [t["id"] for t in tasks]
            attempts = conn.execute("SELECT id,task_id,bot_id,number,status,runtime_run_id,created_at,updated_at FROM attempts WHERE task_id=ANY(%s) ORDER BY created_at", (ids,)).fetchall()
            events = conn.execute("SELECT kind,count(*) AS count FROM events WHERE task_id=ANY(%s) GROUP BY kind", (ids,)).fetchall()
            terminal = conn.execute("SELECT kind,created_at FROM events WHERE task_id=ANY(%s) AND kind IN ('tool.started','tool.completed') AND data->>'tool'='terminal' ORDER BY created_at", (ids,)).fetchall()
            operations = conn.execute("SELECT operation,count(*) AS count FROM tool_receipts WHERE task_id=ANY(%s) GROUP BY operation", (ids,)).fetchall()
            artifact_count = conn.execute("SELECT count(*) AS count FROM artifacts WHERE task_id=ANY(%s)", (ids,)).fetchone()["count"]
            receipts.append({"root_id": root, "tasks": tasks, "executions": attempts, "event_counts": {e["kind"]: e["count"] for e in events}, "terminal_event_times": terminal, "tool_receipts": {e["operation"]: e["count"] for e in operations}, "artifact_count": artifact_count})
    snapshot = state()
    summary = {"generated_at": datetime.now(timezone.utc).isoformat(), "provider_configured": snapshot["provider_configured"], "protocol_fixture": snapshot["protocol_fixture"], "bots": [{"id": b["id"], "compatible": b["readiness"].get("compatible"), "plugin_ready": b["readiness"].get("plugin_ready")} for b in snapshot["bots"]], "task_status_counts": dict(Counter(t["status"] for t in snapshot["tasks"])), "pending_approvals": sum(a["status"] == "pending" for a in snapshot["approvals"]), "live_receipts": receipts}
    (output / "execution-evidence.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({k: v for k, v in summary.items() if k != "live_receipts"}), flush=True)
    db.pool.close()


if __name__ == "__main__":
    main()
