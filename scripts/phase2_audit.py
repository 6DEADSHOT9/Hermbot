"""Read-only completion audit; Docker projections deliberately exclude container environment."""
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request
import xml.etree.ElementTree as ET

root = Path(__file__).resolve().parent.parent
results = root / "test-results"


def command(*args):
    return subprocess.run(args, cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def load(name):
    return json.loads((results / name).read_text())


def main():
    token = next(line.split("=", 1)[1].strip().strip("\"'") for line in (root / ".env").read_text().splitlines() if line.startswith("OWNER_TOKEN="))
    def get(path, binary=False):
        request = urllib.request.Request("http://127.0.0.1:8780" + path, headers={"Authorization": "Bearer " + token})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read()
        return data if binary else json.loads(data)
    state = get("/api/v1/state")
    assert not state["protocol_fixture"] and state["provider_configured"]
    assert (root / ".env").stat().st_mode & 0o777 == 0o600
    suites = ET.parse(results / "backend.xml").getroot().findall("testsuite")
    test_count = sum(int(s.attrib["tests"]) for s in suites)
    assert test_count >= 51 and all(int(s.attrib["failures"]) == int(s.attrib["errors"]) == 0 for s in suites)
    evidence = {name: load(name) for name in ("phase2-browser.json", "phase2-controls.json", "phase2-work-controls.json", "phase2-history.json", "phase2-skills.json", "phase2-lifecycle.json", "live-acceptance.json", "active-worker-recovery.json", "browser-reconnect.json")}
    for name in ("phase2-browser.json", "phase2-controls.json", "phase2-work-controls.json", "phase2-history.json", "browser-reconnect.json"):
        assert evidence[name]["script_errors"] == []
    web = evidence["phase2-browser.json"]
    assert web["real_model"] and get("/api/v1/tasks/" + web["task_id"])["status"] == "completed"
    assert get("/api/v1/tasks/" + web["child_task_id"])["status"] == "completed"
    for artifact in web["artifacts"]:
        data = get("/api/v1/artifacts/" + artifact["id"], binary=True)
        assert hashlib.sha256(data).hexdigest() == artifact["sha256"]
    control = evidence["phase2-work-controls.json"]
    original = get("/api/v1/tasks/" + control["original_task_id"])
    retry = get("/api/v1/tasks/" + control["retry_task_id"])
    assert original["status"] == "cancelled" and original["attempts"] == 0
    assert retry["status"] == "completed" and original["id"] != retry["id"]
    skills = evidence["phase2-skills.json"]
    assert skills["installed_skills"] >= 20 and "skill_view" in skills["native_tools"]
    assert get("/api/v1/tasks/" + skills["task_id"])["status"] == "completed"
    assert evidence["phase2-lifecycle.json"]["real_native_runtimes"]
    assert len(evidence["phase2-lifecycle.json"]["checks"]) >= 6
    live = evidence["live-acceptance.json"]
    assert live["live_model_acceptance"] and len(live["checks"]) == 3
    assert all(get("/api/v1/tasks/" + item["task_id"])["status"] == "completed" for item in live["checks"])
    recovery = evidence["active-worker-recovery.json"]
    assert recovery["live_model_acceptance"] and recovery["attempts"] == 1
    recovered = get("/api/v1/tasks/" + recovery["task_id"])
    assert recovered["status"] == "completed" and len(recovered["executions"]) == 1
    assert any(e["kind"] == "tool.started" and e["data"].get("tool") == "terminal" for e in recovered["events"])
    assert not [t for t in state["tasks"] if t["status"] not in {"completed", "failed", "interrupted", "cancelled"}]
    assert not [o for o in get("/api/v1/bot-operations") if o["status"] in {"queued", "applying", "uncertain"}]
    ids = command("docker", "ps", "-q", "--filter", "label=com.hermbot.project=hermbot", "--filter", "label=com.hermbot.managed=true", "--filter", "label=com.hermbot.kind=runtime").split()
    template = '{"name":{{json .Name}},"id":{{json .Id}},"image":{{json .Image}},"state":{{json .State.Status}},"health":{{json .State.Health.Status}},"labels":{{json .Config.Labels}},"mounts":{{json .Mounts}},"networks":{{json .NetworkSettings.Networks}},"memory":{{json .HostConfig.Memory}},"cpus":{{json .HostConfig.NanoCpus}}}'
    containers = [json.loads(line) for line in command("docker", "inspect", "--format", template, *ids).splitlines()]
    assert len(containers) == len(state["bots"]) == 5
    image = command("docker", "image", "inspect", "hermbot-hermes:phase2", "--format", "{{.Id}}")
    mounts = []
    for container in containers:
        identity = container["labels"]["com.hermbot.bot-id"]
        bot = next(b for b in state["bots"] if b["id"] == identity)
        assert bot["status"] == "active" and bot["revision"] == bot["applied_revision"] and bot["readiness"]["compatible"]
        assert container["image"] == image and container["health"] == "healthy" and container["state"] == "running"
        assert container["memory"] > 0 and container["cpus"] > 0 and len(container["networks"]) == 1
        assert set(m["Destination"] for m in container["mounts"]) == {"/opt/data", "/workspace", "/browser-state"}
        assert all(m["Type"] == "volume" for m in container["mounts"])
        mounts.extend(m["Name"] for m in container["mounts"])
        if identity in {"researcher", "writer", "reviewer"}:
            assert next(m["Name"] for m in container["mounts"] if m["Destination"] == "/opt/data") == f"hermbot_{identity}-data"
        assert not set(bot) & {"bot_token_encrypted", "bot_token_hash", "runtime_key_encrypted"}
    assert len(mounts) == len(set(mounts)) == 15
    assert command("docker", "inspect", "hermbot-worker-1", "--format", "{{.State.Paused}}") == "false"
    artifact = {"approved_scope": "local Phase 2; Linux staging/HTTPS deferred by owner", "backend_tests": test_count, "runtime_count": len(containers), "runtime_image": image, "isolated_private_volumes": len(mounts), "phase2_exit_task_id": web["task_id"], "evidence_files": list(evidence), "pending_operations": 0, "unsettled_tasks": 0, "containers": containers}
    (results / "phase2-completion-audit.json").write_text(json.dumps(artifact, indent=2))
    print(json.dumps({key: value for key, value in artifact.items() if key != "containers"}))


if __name__ == "__main__":
    main()
