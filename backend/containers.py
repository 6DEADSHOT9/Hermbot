"""Sole Docker boundary: resource names, images, mounts and commands are server-owned."""
import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
from pathlib import Path
from collections import defaultdict

from fastapi import Depends, FastAPI, Header, HTTPException
import httpx
from pydantic import Field

from .models import BotConfiguration, BotId, StrictModel
from .settings import BOT_IDS, settings

RUNTIME_IMAGE = os.environ.get("HERMBOT_RUNTIME_IMAGE", "hermbot-hermes:phase4")
# Python the supervisor runs in a runtime runs as root. Isolated mode keeps the working directory,
# PYTHON* variables and user site-packages off its import path, and runtimes start in `/`, so a
# module the agent plants in a directory it owns is never imported.
RUNTIME_PYTHON = ["/opt/hermes/.venv/bin/python", "-I"]
RUNTIME_SECURITY = {
    'ReadonlyRootfs': True,
    'SecurityOpt': ['no-new-privileges:true'],
    'CapDrop': ['ALL'],
    # Bootstrap prepares private volumes, starts its children as the Hermes UID and stays
    # their root parent, so it needs KILL to forward stops; those children hold no capabilities.
    # No mount, network-administration or process-inspection capabilities are granted.
    'CapAdd': ['CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'KILL', 'SETGID', 'SETUID'],
    'Tmpfs': {'/tmp': 'rw,nosuid,nodev,size=512m,mode=1777',
              '/run': 'rw,nosuid,nodev,noexec,size=32m,mode=0755',
              '/opt/data/plugins': 'rw,nosuid,nodev,noexec,size=4m,mode=0755'},
}
# Agent processes run as `hermes` and must never reach the in-container control ports, even
# with a leaked key: the gateway's approvals and the adapter's owner operations. Rules live in
# the runtime's network namespace, so they are installed after every start; its bootstrap
# refuses to start the agent until this fence refuses its own probe.
FENCE = """set -eu
for tool in iptables ip6tables; do
  if [ "$tool" = ip6tables ] && [ "$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null || echo 1)" = 1 ]; then continue; fi
  $tool -w -N HERMBOT_FENCE 2>/dev/null || $tool -w -F HERMBOT_FENCE
  $tool -w -A HERMBOT_FENCE -p tcp -m owner --uid-owner hermes -m addrtype --dst-type LOCAL -m multiport --dports 8642,8643 -j REJECT --reject-with tcp-reset
  $tool -w -C OUTPUT -j HERMBOT_FENCE 2>/dev/null || $tool -w -I OUTPUT 1 -j HERMBOT_FENCE
done
"""
FENCED = {}
STAGING_DIRECTORY = '/run/hermbot-staging'
STAGING_INIT = """import os,stat
parent=os.open('/run',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    try: os.mkdir('hermbot-staging',mode=0o700,dir_fd=parent)
    except FileExistsError: pass
    directory=os.open('hermbot-staging',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
    try:
        info=os.fstat(directory)
        assert info.st_uid==0 and stat.S_ISDIR(info.st_mode) and info.st_mode & 0o077==0
    finally: os.close(directory)
finally: os.close(parent)
"""
STAGING_WRITE = """import base64,os,sys
directory=os.open('/run/hermbot-staging',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
    flags=os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|(os.O_TRUNC if sys.argv[3]=='first' else os.O_APPEND)
    fd=os.open(sys.argv[1],flags,0o600,dir_fd=directory)
    try:
        data=memoryview(base64.b64decode(sys.argv[2],validate=True))
        while data: data=data[os.write(fd,data):]
    finally: os.close(fd)
finally: os.close(directory)
"""
LOCKS = defaultdict(threading.Lock)
LABELS = {"com.hermbot.project": "hermbot", "com.hermbot.managed": "true"}


def authorize(authorization: str | None = Header(default=None)):
    supplied = (authorization or "").removeprefix("Bearer ")
    if not supplied or not hmac.compare_digest(supplied, settings.supervisor_token):
        raise HTTPException(401, "Supervisor authentication required")


app = FastAPI(title="Hermbot supervisor", docs_url=None, redoc_url=None)


def docker_request(method, path, **kwargs):
    with httpx.Client(transport=httpx.HTTPTransport(uds="/var/run/docker.sock"), timeout=90) as client:
        response = client.request(method, "http://docker/v1.45" + path, **kwargs)
        if response.status_code >= 400:
            raise HTTPException(response.status_code, f"Docker operation failed (HTTP {response.status_code}); check resource state")
        return response.json() if response.content else {}


def optional_get(path):
    try:
        return docker_request("GET", path)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return None


def filter_containers(labels, all=True):
    return docker_request("GET", "/containers/json", params={"all": str(all).lower(), "filters": json.dumps({"label": labels})})


def identity(bot_id):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,47}", bot_id):
        raise HTTPException(422, "Invalid bot identity")
    return bot_id


def find_runtime(bot_id, required=True):
    identity(bot_id)
    rows = filter_containers(["com.hermbot.project=hermbot", "com.hermbot.managed=true", f"com.hermbot.bot-id={bot_id}", "com.hermbot.kind=runtime"])
    if not rows and bot_id in BOT_IDS:
        rows = filter_containers(["com.docker.compose.project=hermbot", f"com.docker.compose.service=hermes-{bot_id}"])
    if len(rows) > 1:
        raise HTTPException(409, "Multiple containers claim this bot; reconcile before proceeding")
    if not rows and required:
        raise HTTPException(404, "Bot runtime is missing")
    return rows[0] if rows else None


def resource_names(bot_id):
    identity(bot_id)
    prefix = f"hermbot_{bot_id}" if bot_id in BOT_IDS else f"hermbot_bot-{bot_id}"
    network = f"hermbot_{bot_id}-net" if bot_id in BOT_IDS else f"hermbot_bot-{bot_id}-net"
    return network, {part: f"{prefix}-{part}" for part in ("data", "workspace", "browser")}


def owns_resource(resource, bot_id, name):
    labels = resource.get("Labels") or {}
    return (labels.get("com.hermbot.project") == "hermbot" and labels.get("com.hermbot.bot-id") == bot_id) or (
        bot_id in BOT_IDS and labels.get("com.docker.compose.project") == "hermbot" and name in resource_names(bot_id)[1].values())


def ensure_resources(bot_id):
    network_name, volumes = resource_names(bot_id)
    labels = {**LABELS, "com.hermbot.bot-id": bot_id}
    for name in volumes.values():
        volume = optional_get(f"/volumes/{name}")
        if volume and not owns_resource(volume, bot_id, name):
            raise HTTPException(409, "A required volume is owned by another project")
        if not volume:
            docker_request("POST", "/volumes/create", json={"Name": name, "Labels": labels})
    network = optional_get(f"/networks/{network_name}")
    if not network:
        docker_request("POST", "/networks/create", json={"Name": network_name, "Driver": "bridge", "Labels": labels})
    elif not ((network.get("Labels") or {}).get("com.hermbot.bot-id") == bot_id and (network.get("Labels") or {}).get("com.hermbot.project") == "hermbot"):
        if bot_id not in BOT_IDS or (network.get("Labels") or {}).get("com.docker.compose.project") != "hermbot":
            raise HTTPException(409, "The required network is owned by another project")
    for service in ("api", "worker"):
        for container in filter_containers(["com.docker.compose.project=hermbot", f"com.docker.compose.service={service}"]):
            detail = docker_request("GET", f"/containers/{container['Id']}/json")
            if network_name not in detail["NetworkSettings"]["Networks"]:
                docker_request("POST", f"/networks/{network_name}/connect", json={"Container": container["Id"], "EndpointConfig": {"Aliases": [service]}})
    return network_name, volumes


class RuntimeSpec(StrictModel):
    bot_id: BotId
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    revision: int = Field(ge=1)
    configuration: BotConfiguration
    model_base_url: str
    model_api_key: str
    model_auth_kind: str = Field(default='api_key', pattern=r'^(api_key|chatgpt_oauth|codex_oauth)$')
    runtime_key: str = Field(min_length=32)
    adapter_key: str = Field(min_length=32)
    bot_token: str = Field(min_length=32)
    timezone: str = "UTC"
    desired_state: str = Field(default="running", pattern=r"^(running|stopped)$")
    source_bot_id: BotId | None = None
    source_was_running: bool = False
    copy_options: dict[str, bool] = Field(default_factory=dict)


def container_stop(container):
    if container and container["State"] in {"running", "restarting"}:
        docker_request("POST", f"/containers/{container['Id']}/stop", params={"t": 15})


def remove_container(container):
    container_stop(container)
    if container:
        docker_request("DELETE", f"/containers/{container['Id']}")


def fence(container_id, bot_id):
    started = docker_request("GET", f"/containers/{container_id}/json")["State"]["StartedAt"]
    helper = docker_request("POST", "/containers/create", json={
        "Image": RUNTIME_IMAGE, "User": "0:0", "Entrypoint": ["/bin/sh", "-c", FENCE],
        "Labels": {**LABELS, "com.hermbot.bot-id": bot_id, "com.hermbot.kind": "fence-helper"},
        "HostConfig": {"NetworkMode": f"container:{container_id}", "CapDrop": ["ALL"], "CapAdd": ["NET_ADMIN", "NET_RAW"],
                       "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges:true"], "Tmpfs": {"/run": "rw,size=1m"},
                       "Memory": 64 * 1024 * 1024}})["Id"]
    try:
        docker_request("POST", f"/containers/{helper}/start")
        if docker_request("POST", f"/containers/{helper}/wait", params={"condition": "not-running"})["StatusCode"] != 0:
            raise HTTPException(502, "The runtime's control-port fence could not be installed; the teammate stays paused")
    finally:
        docker_request("DELETE", f"/containers/{helper}", params={"force": "true"})
    FENCED[container_id] = started


def runtime_ready(container_id):
    deadline = time.monotonic() + 75
    while time.monotonic() < deadline:
        state = docker_request("GET", f"/containers/{container_id}/json")["State"]
        if state.get("Health", {}).get("Status") == "healthy":
            return
        if state["Status"] in {"exited", "dead", "restarting"} or state.get("Health", {}).get("Status") == "unhealthy":
            raise HTTPException(502, "The runtime failed to start; private volumes are preserved for recovery")
        time.sleep(0.5)
    raise HTTPException(502, "Runtime health did not become ready; inspect resources and retry provisioning")


def clone_state(spec, target_volumes):
    if not spec.source_bot_id or spec.source_bot_id == spec.bot_id:
        raise HTTPException(422, "Clone source must be another bot")
    if set(spec.copy_options) - {"memory", "skills", "workspace", "history"}:
        raise HTTPException(422, "Unknown clone state selection")
    source = find_runtime(spec.source_bot_id, required=False)
    _, source_volumes = resource_names(spec.source_bot_id)
    for name in source_volumes.values():
        volume = optional_get(f"/volumes/{name}")
        if not volume or not owns_resource(volume, spec.source_bot_id, name):
            raise HTTPException(409, "Clone source storage is missing or unowned")
    container_stop(source)
    helper_name = "hermbot-clone-" + spec.operation_id
    try:
        helper = optional_get(f"/containers/{helper_name}/json")
        if helper:
            labels = helper.get("Config", {}).get("Labels", {})
            if any(labels.get(key) != value for key, value in {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "clone-helper"}.items()):
                raise HTTPException(409, "Clone helper identity mismatch")
            if helper["State"]["Status"] == "exited" and helper["State"]["ExitCode"] != 0:
                remove_container({"Id": helper["Id"], "State": "exited"})
                helper = None
        if not helper:
            binds = [f"{source_volumes['data']}:/source-data:ro", f"{source_volumes['workspace']}:/source-workspace:ro", f"{target_volumes['data']}:/target-data:rw", f"{target_volumes['workspace']}:/target-workspace:rw"]
            helper_id = docker_request("POST", "/containers/create", params={"name": helper_name}, json={"Image": RUNTIME_IMAGE, "User": "0:0", "WorkingDir": "/", "Entrypoint": [*RUNTIME_PYTHON, "/opt/hermbot/copy_state.py"], "Cmd": [json.dumps(spec.copy_options)], "Labels": {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "clone-helper"}, "HostConfig": {"Binds": binds, "NetworkMode": "none", "Memory": 512 * 1024 * 1024, "NanoCpus": 500_000_000}})["Id"]
            docker_request("POST", f"/containers/{helper_id}/start")
        else:
            labels = helper.get("Config", {}).get("Labels", {})
            if any(labels.get(key) != value for key, value in {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "clone-helper"}.items()):
                raise HTTPException(409, "Clone helper identity mismatch")
            helper_id = helper["Id"]
            if helper["State"]["Status"] == "created":
                docker_request("POST", f"/containers/{helper_id}/start")
        result = docker_request("POST", f"/containers/{helper_id}/wait", params={"condition": "not-running"})
        if result["StatusCode"] != 0:
            raise HTTPException(502, "Selected state could not be copied; source storage is preserved")
    finally:
        if source and spec.source_was_running:
            detail = docker_request("GET", f"/containers/{source['Id']}/json")
            if not detail["State"]["Running"]:
                docker_request("POST", f"/containers/{source['Id']}/start")


def ensure(spec, clone=False):
    image_id = docker_request("GET", f"/images/{RUNTIME_IMAGE}/json")["Id"]
    network, volumes = ensure_resources(spec.bot_id)
    fingerprint = hashlib.sha256(json.dumps({**spec.model_dump(mode="json"), "image": image_id,
                                           'security': RUNTIME_SECURITY}, sort_keys=True).encode()).hexdigest()
    container = find_runtime(spec.bot_id, required=False)
    if container and (container.get("Labels") or {}).get("com.hermbot.configuration") == fingerprint:
        if spec.desired_state == "running" and container["State"] != "running":
            docker_request("POST", f"/containers/{container['Id']}/start")
        elif spec.desired_state == "stopped":
            container_stop(container)
        if spec.desired_state == "running":
            fence(container["Id"], spec.bot_id)
            runtime_ready(container["Id"])
        return {"container_id": container["Id"][:12], "revision": spec.revision, "state": spec.desired_state}
    if clone:
        clone_state(spec, volumes)
    remove_container(container)
    config = spec.configuration
    environment = {"MODEL_NAME": config.model_name or "unconfigured", "MODEL_BASE_URL": spec.model_base_url, "MODEL_API_KEY": spec.model_api_key,
        "MODEL_AUTH_KIND": spec.model_auth_kind,
        "API_SERVER_KEY": spec.runtime_key, "HERMBOT_ADAPTER_KEY": spec.adapter_key, "HERMBOT_BOT_TOKEN": spec.bot_token, "HERMBOT_API_URL": "http://api:8000", "BOT_ID": spec.bot_id,
        "BOT_ROLE": f"You are {config.name}. {config.role}", "HERMES_ENABLED_TOOLS": json.dumps(config.enabled_tools), "HERMES_ENABLED_SKILLS": json.dumps(config.enabled_skills), "HERMES_TIMEZONE": spec.timezone}
    result = docker_request("POST", "/containers/create", params={"name": f"hermbot-bot-{spec.bot_id}"}, json={"Image": RUNTIME_IMAGE,
        "Env": [f"{key}={value}" for key, value in environment.items()], "WorkingDir": "/",
        "Labels": {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "runtime", "com.hermbot.configuration": fingerprint, "com.hermbot.revision": str(spec.revision)},
        "HostConfig": {**RUNTIME_SECURITY, "Binds": [f"{volumes['data']}:/opt/data", f"{volumes['workspace']}:/workspace", f"{volumes['browser']}:/browser-state"], "Init": True,
            "Memory": config.memory_mb * 1024 * 1024, "NanoCpus": int(config.cpus * 1_000_000_000), "RestartPolicy": {"Name": "unless-stopped"}},
        "NetworkingConfig": {"EndpointsConfig": {network: {"Aliases": [f"hermbot-bot-{spec.bot_id}", f"hermes-{spec.bot_id}"]}}},
        "Healthcheck": {"Test": ["CMD", *RUNTIME_PYTHON, "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8642/health',timeout=3); urllib.request.urlopen('http://127.0.0.1:8643/health',timeout=3)"], "Interval": 10_000_000_000, "Timeout": 5_000_000_000, "StartPeriod": 40_000_000_000, "Retries": 12}})
    if spec.desired_state == "running":
        docker_request("POST", f"/containers/{result['Id']}/start")
        fence(result["Id"], spec.bot_id)
        runtime_ready(result["Id"])
    return {"container_id": result["Id"][:12], "revision": spec.revision, "state": spec.desired_state}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/runtimes", dependencies=[Depends(authorize)])
def runtimes():
    rows = filter_containers(["com.hermbot.project=hermbot", "com.hermbot.kind=runtime"])
    return {"runtimes": [{"bot_id": row["Labels"]["com.hermbot.bot-id"], "container_id": row["Id"][:12], "state": row["State"], "status": row["Status"]} for row in rows]}


@app.post("/networks/{bot_id}/reconcile", dependencies=[Depends(authorize)])
def reconcile_networks(bot_id: BotId):
    runtime = find_runtime(bot_id, required=False)
    if not runtime:
        raise HTTPException(404, "Runtime is missing")
    ensure_resources(bot_id)
    if runtime["State"] == "exited":
        docker_request("POST", f"/containers/{runtime['Id']}/start")
    # Docker restarts a crashed runtime in a fresh network namespace without the fence.
    state = docker_request("GET", f"/containers/{runtime['Id']}/json")["State"]
    if state["Running"] and FENCED.get(runtime["Id"]) != state["StartedAt"]:
        fence(runtime["Id"], bot_id)
    return {"connected": True}


@app.post("/operations/{action}", dependencies=[Depends(authorize)])
def operate(action: str, spec: RuntimeSpec):
    if action not in ("create", "configure", "start", "stop", "archive", "restore", "delete", "clone", "ensure"):
        raise HTTPException(404, "Unknown lifecycle operation")
    with LOCKS[spec.bot_id]:
        receipts = Path("/var/lib/hermbot-supervisor/receipts")
        receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_file = receipts / (spec.operation_id + ".json")
        request_hash = hashlib.sha256(json.dumps({"action": action, "body": spec.model_dump(mode="json"), "image": RUNTIME_IMAGE}, sort_keys=True).encode()).hexdigest()
        if receipt_file.exists():
            previous = json.loads(receipt_file.read_text())
            if previous["request_hash"] != request_hash:
                raise HTTPException(409, "Operation identity already has a different immutable payload")
            cleanup_clone(action, spec)
            return previous["result"]
        result = execute_operation(action, spec)
        temporary = receipt_file.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump({"request_hash": request_hash, "result": result}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, receipt_file)
        directory = os.open(receipts, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        cleanup_clone(action, spec)
        return result


def cleanup_clone(action, spec):
    helper = optional_get(f"/containers/hermbot-clone-{spec.operation_id}/json") if action == "clone" else None
    if helper:
        labels = helper.get("Config", {}).get("Labels", {})
        if any(labels.get(key) != value for key, value in {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "clone-helper"}.items()):
            raise HTTPException(409, "Refusing to remove an unowned clone helper")
        remove_container({"Id": helper["Id"], "State": helper["State"]["Status"]})


def execute_operation(action, spec):
    if action in ("stop", "archive"):
        container_stop(find_runtime(spec.bot_id, required=False))
        return {"state": "stopped", "revision": spec.revision}
    if action == "delete":
        remove_container(find_runtime(spec.bot_id, required=False))
        for helper in filter_containers(["com.hermbot.project=hermbot", "com.hermbot.managed=true", f"com.hermbot.bot-id={spec.bot_id}", "com.hermbot.kind=clone-helper"]):
            remove_container(helper)
        network, volumes = resource_names(spec.bot_id)
        for name in volumes.values():
            volume = optional_get(f"/volumes/{name}")
            if volume:
                if not owns_resource(volume, spec.bot_id, name):
                    raise HTTPException(409, "Refusing to delete another project's volume")
                docker_request("DELETE", f"/volumes/{name}")
        if spec.bot_id not in BOT_IDS:
            item = optional_get(f"/networks/{network}")
            if item:
                if any((item.get("Labels") or {}).get(key) != value for key, value in {**LABELS, "com.hermbot.bot-id": spec.bot_id}.items()):
                    raise HTTPException(409, "Refusing to delete another bot's network")
                for cid in (item.get("Containers") or {}):
                    docker_request("POST", f"/networks/{network}/disconnect", json={"Container": cid})
                docker_request("DELETE", f"/networks/{network}")
        return {"state": "deleted", "revision": spec.revision}
    return ensure(spec, clone=action == "clone")

class AttachmentSpec(StrictModel):
    bot_id: BotId
    attachment_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    name: str = Field(min_length=1, max_length=160, pattern=r"^[^/\\\x00-\x1f]+$")
    content_base64: str = Field(max_length=14_000_000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@app.post("/attachments", dependencies=[Depends(authorize)])
def transfer_attachment(body: AttachmentSpec):
    from .library import filename
    filename(body.name)
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "Invalid attachment encoding")
    if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != body.sha256:
        raise HTTPException(422, "Attachment size or checksum mismatch")
    container = find_runtime(body.bot_id)
    relative = f"attachments/{body.attachment_id}/{body.name}"
    staging_name = f"upload-{body.attachment_id}.json"
    staging = json.dumps(body.model_dump()).encode()
    with LOCKS[body.bot_id]:
        # Staging is separate from immutable runtime code, root-only and not
        # writable by the agent. The server owns both commands and directory.
        runtime_command(container['Id'], [*RUNTIME_PYTHON, '-c', STAGING_INIT])
        # Docker's archive API rejects tmpfs destinations with a read-only root.
        # Bounded argv chunks avoid that API and Linux's per-argument size limit.
        # Data is a separate base64 argument, never interpolated into executable code.
        for offset in range(0, len(staging), 60_000):
            runtime_command(container['Id'], [*RUNTIME_PYTHON, '-c', STAGING_WRITE,
                staging_name, base64.b64encode(staging[offset:offset + 60_000]).decode(),
                'first' if offset == 0 else 'append'])
        runtime_command(container['Id'], [*RUNTIME_PYTHON, '/opt/hermbot/write_attachment.py',
                                         STAGING_DIRECTORY + '/' + staging_name])
    return {"path": "/workspace/" + relative}


def runtime_command(container_id, command):
    execution = docker_request('POST', f'/containers/{container_id}/exec',
                               json={'User': '0:0', 'WorkingDir': '/', 'Cmd': command})['Id']
    docker_request('POST', f'/exec/{execution}/start', json={'Detach': True, 'Tty': False})
    for _ in range(100):
        receipt = docker_request('GET', f'/exec/{execution}/json')
        if not receipt['Running']:
            if receipt['ExitCode'] != 0:
                raise HTTPException(409, 'Attachment path is unsafe or unwritable; inspect the private workspace')
            return
        time.sleep(0.05)
    raise HTTPException(504, 'Attachment installation has not settled')
