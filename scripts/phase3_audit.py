"""Read-only Phase 3 closure audit. Credentials and desktop input never appear in output."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent.parent
results = root / "test-results"
CANARY = "HERM_PHASE3_SECRET_CANARY_9V"


def command(*args):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def load(name):
    return json.loads((results / name).read_text())


def main():
    token = next(
        line.split("=", 1)[1].strip().strip("\"'")
        for line in (root / ".env").read_text().splitlines()
        if line.startswith("OWNER_TOKEN=")
    )

    def get(path, binary=False):
        request = urllib.request.Request(
            "http://127.0.0.1:8780" + path, headers={"Authorization": "Bearer " + token}
        )
        with urllib.request.urlopen(request, timeout=80) as response:
            data = response.read()
        assert CANARY.encode() not in data, "Secure input appeared in a product response"
        return data if binary else json.loads(data)

    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    assert get("/health") == {"status": "ok", "phase": 3}
    state = get("/api/v1/state")
    assert not state["protocol_fixture"] and state["provider_configured"]
    suites = ET.parse(results / "backend.xml").getroot().findall("testsuite")
    count = sum(int(s.attrib["tests"]) for s in suites)
    assert count >= 85
    assert all(int(s.attrib["failures"]) == int(s.attrib["errors"]) == 0 for s in suites)
    names = (
        "phase3-library.json",
        "phase3-native.json",
        "phase3-knowledge.json",
        "phase3-computer.json",
        "phase3-previews.json",
        "phase3-review.json",
        "phase3-exit.json",
        "phase3-link-final.json",
        "phase3-inflight.json",
        "phase3-ui-fixes.json",
        "phase3-browser-persistence.json",
        "phase2-browser.json",
        "phase2-controls.json",
        "phase2-work-controls.json",
        "phase2-history.json",
        "phase2-skills.json",
        "phase2-lifecycle.json",
        "live-acceptance.json",
        "recovery-acceptance.json",
        "active-worker-recovery.json",
        "browser-reconnect.json",
        "browser-live-disconnect.json",
    )
    evidence = {name: load(name) for name in names}
    for proof in evidence.values():
        if isinstance(proof, dict) and "script_errors" in proof:
            assert proof["script_errors"] == []
    exit_proof = evidence["phase3-exit.json"]
    assert exit_proof["status"] == "completed" and exit_proof["versions"] == 2
    assert exit_proof["real_skill_and_browser_tools"] and exit_proof["secret_absent_from_product_records"]
    exit_ids = [exit_proof["root_task_id"], *exit_proof["child_task_ids"]]
    tasks = [get("/api/v1/tasks/" + identity) for identity in exit_ids]
    assert len(tasks) == 3 and all(t["status"] == "completed" for t in tasks)
    tools = {e["data"].get("tool") for t in tasks for e in t["events"] if e["kind"] == "tool.started"}
    assert "skill_view" in tools and tools & {"browser_exec", "browser_navigate"}
    source = get("/api/v1/library/" + exit_proof["source_artifact_id"])
    assert {g["task_id"] for g in source["task_access"]} & set(exit_ids)
    final = get("/api/v1/library/" + exit_proof["final_artifact_id"])
    assert len(final["versions"]) == 2 and {v["version"] for v in final["versions"]} == {1, 2}
    flight = evidence["phase3-inflight.json"]
    assert flight["real_native_model"] and flight["native_effect_count"] == 1
    assert flight["human_input_waited_for_inflight_tool"] and flight["native_turn_paused_and_resumed"]
    assert flight["takeover_wait_seconds"] >= 4
    assert get("/api/v1/tasks/" + flight["task_id"])["status"] == "completed"
    persistence = evidence["phase3-browser-persistence.json"]
    assert persistence["private_profile_survived_recreation"] and persistence["cleared_input_verified"]
    assert evidence["phase3-ui-fixes.json"]["stopped_desktop_handed_back"]
    assert evidence["phase3-ui-fixes.json"]["stale_knowledge_draft_suppressed"]
    assert evidence["phase3-ui-fixes.json"]["stale_file_attachment_suppressed"]
    assert len(evidence["phase3-native.json"]["checks"]) == 5
    assert evidence["phase3-library.json"]["version_count"] == 2
    assert all(p["download_verified"] for p in evidence["phase3-previews.json"])
    for name in (
        "phase3-library.json",
        "phase3-review.json",
        "phase3-knowledge.json",
        "phase2-skills.json",
        "browser-live-disconnect.json",
    ):
        proof = evidence[name]
        if "task_id" in proof:
            assert get("/api/v1/tasks/" + proof["task_id"])["status"] == "completed"
    for proof in evidence["phase3-native.json"]["checks"]:
        if "task_id" in proof:
            assert get("/api/v1/tasks/" + proof["task_id"])["status"] == "completed"
    # Revalidate retained Phase 2 exit receipts and bytes alongside freshly rerun regressions.
    phase2 = evidence["phase2-browser.json"]
    assert phase2["real_model"]
    for identity in (phase2["task_id"], phase2["child_task_id"]):
        assert get("/api/v1/tasks/" + identity)["status"] == "completed"
    for item in phase2["artifacts"]:
        assert hashlib.sha256(get("/api/v1/artifacts/" + item["id"], True)).hexdigest() == item["sha256"]
    lifecycle = evidence["phase2-lifecycle.json"]
    assert lifecycle["real_native_runtimes"] and len(lifecycle["checks"]) >= 5
    for proof in lifecycle["checks"]:
        for key in ("task_id", "thread_task_id"):
            if key in proof:
                assert get("/api/v1/tasks/" + proof[key])["status"] == "completed"
    work = evidence["phase2-work-controls.json"]
    original = get("/api/v1/tasks/" + work["original_task_id"])
    retry = get("/api/v1/tasks/" + work["retry_task_id"])
    assert original["status"] == "cancelled" and original["attempts"] == 0
    assert retry["status"] == "completed" and retry["id"] != original["id"]
    live = evidence["live-acceptance.json"]
    assert live["live_model_acceptance"] and len(live["checks"]) == 3
    for proof in live["checks"]:
        assert get("/api/v1/tasks/" + proof["task_id"])["status"] == "completed"
    recovery = evidence["recovery-acceptance.json"]
    assert recovery["live_model_acceptance"] and len(recovery["checks"]) == 5
    for proof in recovery["checks"]:
        assert get("/api/v1/tasks/" + proof["task_id"])["status"] in {"completed", "cancelled"}
    active = evidence["active-worker-recovery.json"]
    assert active["live_model_acceptance"] and active["attempts"] == 1
    recovered = get("/api/v1/tasks/" + active["task_id"])
    assert recovered["status"] == "completed" and len(recovered["executions"]) == 1
    assert any(
        e["kind"] == "tool.started" and e["data"].get("tool") == "terminal" for e in recovered["events"]
    )
    assert not [
        t for t in state["tasks"] if t["status"] not in {"completed", "failed", "interrupted", "cancelled"}
    ]
    assert not [
        o for o in get("/api/v1/bot-operations") if o["status"] in {"queued", "applying", "uncertain"}
    ]
    assert not [a for a in state["approvals"] if a["status"] == "pending"]
    artifacts, offset = [], 0
    while True:
        page = get(f"/api/v1/library?all_versions=true&offset={offset}")
        artifacts.extend(page["items"])
        if not page["has_more"]:
            break
        offset += 50
    for item in artifacts:
        data = get("/api/v1/artifacts/" + item["id"], True)
        assert len(data) == item["size"] and hashlib.sha256(data).hexdigest() == item["sha256"]
    ids = command(
        "docker",
        "ps",
        "-q",
        "--filter",
        "label=com.hermbot.project=hermbot",
        "--filter",
        "label=com.hermbot.managed=true",
        "--filter",
        "label=com.hermbot.kind=runtime",
    ).split()
    template = '{"name":{{json .Name}},"id":{{json .Id}},"image":{{json .Image}},"state":{{json .State.Status}},"health":{{json .State.Health.Status}},"labels":{{json .Config.Labels}},"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"memory":{{json .HostConfig.Memory}},"cpus":{{json .HostConfig.NanoCpus}}}'
    containers = [
        json.loads(line) for line in command("docker", "inspect", "--format", template, *ids).splitlines()
    ]
    assert len(containers) == len(state["bots"]) == 5
    image = command("docker", "image", "inspect", "hermbot-hermes:phase3", "--format", "{{.Id}}")
    mounts, knowledge_count = [], 0
    for container in containers:
        identity = container["labels"]["com.hermbot.bot-id"]
        bot = next(b for b in state["bots"] if b["id"] == identity)
        assert bot["status"] == "active" and bot["desired_state"] == "running"
        assert bot["revision"] == bot["applied_revision"] and bot["readiness"]["compatible"]
        assert (
            container["image"] == image
            and container["health"] == "healthy"
            and container["state"] == "running"
        )
        assert container["memory"] > 0 and container["cpus"] > 0 and len(container["networks"]) == 1
        assert {m["Destination"] for m in container["mounts"]} == {
            "/opt/data",
            "/workspace",
            "/browser-state",
        }
        assert all(m["Type"] == "volume" for m in container["mounts"])
        mounts.extend(m["Name"] for m in container["mounts"])
        assert not set(bot) & {"bot_token_encrypted", "bot_token_hash", "runtime_key_encrypted"}
        control = get(f"/api/v1/bots/{identity}/computer")
        assert control["lease"]["holder"] == "agent" and not control["admission_paused"]
        for version in get(f"/api/v1/bots/{identity}/knowledge")["versions"]:
            value = get(f"/api/v1/bots/{identity}/knowledge/versions/{version['id']}")
            assert hashlib.sha256(value["content"].encode()).hexdigest() == value["sha256"]
            knowledge_count += 1
    assert len(mounts) == len(set(mounts)) == 15
    assert command("docker", "inspect", "hermbot-worker-1", "--format", "{{.State.Paused}}") == "false"
    app_image = command("docker", "image", "inspect", "hermbot-app:phase1", "--format", "{{.Id}}")
    for container_name in ("hermbot-api-1", "hermbot-worker-1", "hermbot-supervisor-1"):
        assert command("docker", "inspect", container_name, "--format", "{{.Image}}") == app_image
    query = (
        "SELECT count(*) FROM (SELECT row_to_json(t)::text AS value FROM messages t UNION ALL SELECT row_to_json(t)::text FROM tasks t UNION ALL SELECT row_to_json(t)::text FROM attempts t UNION ALL SELECT row_to_json(t)::text FROM events t UNION ALL SELECT row_to_json(t)::text FROM approvals t UNION ALL SELECT row_to_json(t)::text FROM artifacts t UNION ALL SELECT row_to_json(t)::text FROM knowledge_versions t UNION ALL SELECT row_to_json(t)::text FROM knowledge_proposals t) records WHERE value LIKE '%"
        + CANARY
        + "%'"
    )
    assert (
        command(
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "hermbot",
            "-d",
            "hermbot",
            "-Atc",
            query,
        )
        == "0"
    )
    assert (
        command(
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "hermbot",
            "-d",
            "hermbot",
            "-Atc",
            "SELECT count(*) FROM knowledge_proposals WHERE status IN ('pending','queued','applying')",
        )
        == "0"
    )
    artifact = {
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "approved_scope": "local Phase 3; Linux staging/HTTPS deferred by owner; Phase 4 not started",
        "backend_tests": count,
        "runtime_count": len(containers),
        "runtime_image": image,
        "app_image": app_image,
        "isolated_private_volumes": len(mounts),
        "phase3_exit_task_id": exit_proof["root_task_id"],
        "immutable_artifact_downloads_verified": len(artifacts),
        "knowledge_versions_verified": knowledge_count,
        "secret_absent_from_product_records": True,
        "pending_operations": 0,
        "unsettled_tasks": 0,
        "pending_reviews": 0,
        "human_controls": 0,
        "evidence_files": list(evidence),
        "containers": containers,
    }
    (results / "phase3-completion-audit.json").write_text(json.dumps(artifact, indent=2))
    print(json.dumps({key: value for key, value in artifact.items() if key != "containers"}))


if __name__ == "__main__":
    main()
