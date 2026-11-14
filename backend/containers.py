"""Sole Docker boundary: resource names, images, mounts and commands are server-owned."""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

from fastapi import Depends, FastAPI, Header, HTTPException
import httpx
from pydantic import Field, StrictBool, StrictInt

from .models import BotConfiguration, BotId, StrictModel
from . import limits
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
MACHINE_CACHE = {}


def rotated_logs(driver='json-file', size=None, files=None):
    if driver not in ('json-file', 'local'):
        return {}
    return {'LogConfig': {'Type': driver, 'Config': {
        'max-size': f'{size or limits.LOG_SIZE_MB}m', 'max-file': str(files or limits.LOG_FILES)}}}


def helper_limits(memory_mb):
    return {'PidsLimit': limits.HELPER_PIDS_LIMIT, 'MemorySwap': memory_mb * 1024 * 1024,
            'OomScoreAdj': limits.BOT_OOM_SCORE_ADJ,
            **rotated_logs(MACHINE_CACHE.get('facts', {}).get('logging_driver', ''))}


def read_machine():
    with LOCKS['machine']:
        if MACHINE_CACHE.get('at') is not None and time.monotonic() - MACHINE_CACHE['at'] < 60:
            return MACHINE_CACHE['facts']
        info = docker_request('GET', '/info')
        facts = {'cpus': info.get('NCPU'), 'memory_bytes': info.get('MemTotal'),
                 'operating_system': info.get('OperatingSystem', ''), 'cgroup_version': info.get('CgroupVersion'),
                 'memory_limit': bool(info.get('MemoryLimit')), 'swap_limit': bool(info.get('SwapLimit')),
                 'pids_limit': bool(info.get('PidsLimit')), 'logging_driver': info.get('LoggingDriver', ''),
                 'rootless': any('rootless' in x for x in info.get('SecurityOptions') or []),
                 'observed_at': datetime.now(timezone.utc).isoformat(), 'warnings': []}
        MACHINE_CACHE['facts'] = facts
        for key, path in {'threads_max': '/proc/sys/kernel/threads-max', 'file_max': '/proc/sys/fs/file-max',
                          'nr_open': '/proc/sys/fs/nr_open'}.items():
            try:
                facts[key] = int(Path(path).read_text().strip())
            except (OSError, ValueError):
                facts[key] = None
        facts['memory_pressure'] = Path('/proc/pressure/memory').exists()
        try:
            stat = os.statvfs('/var/lib/hermbot-supervisor')
            facts.update(disk_total_bytes=stat.f_blocks * stat.f_frsize,
                         disk_free_bytes=stat.f_bavail * stat.f_frsize,
                         total_inodes=stat.f_files, free_inodes=stat.f_favail)
        except OSError:
            facts['warnings'].append('Docker volume filesystem capacity is unavailable.')
        helper = None
        try:
            # pid_max in the supervisor's PID namespace is not the host's ceiling.
            helper = docker_request('POST', '/containers/create', json={
                'Image': RUNTIME_IMAGE, 'Entrypoint': ['/bin/cat', '/proc/sys/kernel/pid_max'], 'Tty': True,
                'Labels': {**LABELS, 'com.hermbot.kind': 'machine-helper'},
                'HostConfig': {'NetworkMode': 'none', 'PidMode': 'host', 'ReadonlyRootfs': True,
                               'CapDrop': ['ALL'], 'SecurityOpt': ['no-new-privileges:true'],
                               'Memory': 32 * 1024 * 1024, **helper_limits(32)}})['Id']
            docker_request('POST', f'/containers/{helper}/start')
            result = docker_request('POST', f'/containers/{helper}/wait', params={'condition': 'not-running'})
            if result['StatusCode'] != 0:
                raise ValueError('Machine helper failed')
            with httpx.Client(transport=httpx.HTTPTransport(uds='/var/run/docker.sock'), timeout=10) as client:
                response = client.get(f'http://docker/v1.45/containers/{helper}/logs', params={'stdout': 1, 'tail': 1})
                response.raise_for_status()
                facts['pid_max'] = int(response.text.strip())
        except (HTTPException, httpx.HTTPError, ValueError, KeyError):
            facts['pid_max'] = None
            facts['warnings'].append('Host PID ceiling unavailable; using the thread ceiling only.')
        finally:
            if helper:
                try:
                    docker_request('DELETE', f'/containers/{helper}', params={'force': True})
                except (HTTPException, httpx.HTTPError):
                    facts['warnings'].append('Machine helper cleanup needs retry; no bot was changed.')
        if 'docker desktop' in facts['operating_system'].lower():
            facts['warnings'].append('Docker reports its virtual machine, not the physical host disk or RAM.')
        facts['core_services'] = {}
        try:
            for name in ('postgres', 'storage'):
                rows = filter_containers(['com.docker.compose.project=hermbot', f'com.docker.compose.service={name}'])
                if len(rows) == 1:
                    host = docker_request('GET', f'/containers/{rows[0]["Id"]}/json')['HostConfig']
                    facts['core_services'][name] = {key: host.get(key) for key in ('OomScoreAdj', 'MemoryReservation', 'LogConfig')}
        except (HTTPException, httpx.HTTPError, KeyError):
            facts['warnings'].append('Core service settings could not be inspected; restart status is unverified.')
        MACHINE_CACHE.update(at=time.monotonic(), facts=facts)
        return facts


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


class RuntimeLimits(StrictModel):
    pids_limit: StrictInt = Field(ge=limits.PIDS_MIN)
    nofile_hard: StrictInt = Field(ge=limits.NOFILE_SOFT)
    allow_swap: StrictBool
    log_size_mb: StrictInt = Field(ge=limits.LOG_SIZE_HARD[0], le=limits.LOG_SIZE_HARD[1])
    log_files: StrictInt = Field(ge=limits.LOG_FILES_HARD[0], le=limits.LOG_FILES_HARD[1])


class RuntimeSpec(StrictModel):
    bot_id: BotId
    operation_id: str = Field(pattern=r"^[0-9a-f-]{36}$")
    revision: int = Field(ge=1)
    configuration: BotConfiguration
    model_base_url: str
    model_api_key: str
    model_auth_kind: str = Field(default='api_key', pattern=r'^(api_key|chatgpt_oauth|codex_oauth)$')
    # The real provider's URL, never its key: the runtime only uses it to pick Hermes' API style.
    model_provider_url: str = Field(default='', max_length=2048)
    runtime_key: str = Field(min_length=32)
    adapter_key: str = Field(min_length=32)
    bot_token: str = Field(min_length=32)
    timezone: str = "UTC"
    desired_state: str = Field(default="running", pattern=r"^(running|stopped)$")
    source_bot_id: BotId | None = None
    source_was_running: bool = False
    copy_options: dict[str, bool] = Field(default_factory=dict)
    limits: RuntimeLimits | None = None


def operation_body(spec):
    # Receipts written before limits existed must retain their exact serialized hash.
    return spec.model_dump(mode='json', exclude={'limits'} if spec.limits is None else set())


def resource_host(spec):
    if spec.limits is None:
        return {}
    value = spec.limits
    memory = spec.configuration.memory_mb * 1024 * 1024
    driver = read_machine().get('logging_driver', '')
    return {'PidsLimit': value.pids_limit,
            'MemorySwap': int(memory * (1 + limits.SWAP_ALLOWANCE)) if value.allow_swap else memory,
            'Ulimits': [{'Name': 'nofile', 'Soft': limits.NOFILE_SOFT, 'Hard': value.nofile_hard}],
            'OomScoreAdj': limits.BOT_OOM_SCORE_ADJ,
            **rotated_logs(driver, value.log_size_mb, value.log_files)}


def inspect_limits(container_id, spec):
    if spec.limits is None:
        return {}
    host = docker_request('GET', f'/containers/{container_id}/json').get('HostConfig', {})
    return {'limits_observed': {key: host.get(key) for key in
                               ('PidsLimit', 'Memory', 'MemorySwap', 'Ulimits', 'LogConfig', 'OomScoreAdj')}}


def container_stop(container):
    if container and container["State"] == "paused":
        # A frozen container cannot handle its stop signal; thaw it so it shuts down cleanly.
        docker_request("POST", f"/containers/{container['Id']}/unpause")
    if container and container["State"] in {"running", "restarting", "paused"}:
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
                       "Memory": 64 * 1024 * 1024, **helper_limits(64)}})["Id"]
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
            helper_id = docker_request("POST", "/containers/create", params={"name": helper_name}, json={"Image": RUNTIME_IMAGE, "User": "0:0", "WorkingDir": "/", "Entrypoint": [*RUNTIME_PYTHON, "/opt/hermbot/copy_state.py"], "Cmd": [json.dumps(spec.copy_options)], "Labels": {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "clone-helper"}, "HostConfig": {"Binds": binds, "NetworkMode": "none", "Memory": 512 * 1024 * 1024, "NanoCpus": 500_000_000, **helper_limits(512)}})["Id"]
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
    fingerprint = hashlib.sha256(json.dumps({**operation_body(spec), "image": image_id,
                                           'security': RUNTIME_SECURITY}, sort_keys=True).encode()).hexdigest()
    container = find_runtime(spec.bot_id, required=False)
    if container and (container.get("Labels") or {}).get("com.hermbot.configuration") == fingerprint:
        if spec.desired_state == "running" and container["State"] == "paused":
            docker_request("POST", f"/containers/{container['Id']}/unpause")
        elif spec.desired_state == "running" and container["State"] != "running":
            docker_request("POST", f"/containers/{container['Id']}/start")
        elif spec.desired_state == "stopped":
            container_stop(container)
        if spec.desired_state == "running":
            fence(container["Id"], spec.bot_id)
            runtime_ready(container["Id"])
        return {"container_id": container["Id"][:12], "revision": spec.revision, "state": spec.desired_state,
                **inspect_limits(container['Id'], spec)}
    host_limits = resource_host(spec)
    if clone:
        clone_state(spec, volumes)
    remove_container(container)
    config = spec.configuration
    environment = {"MODEL_NAME": config.model_name or "unconfigured", "MODEL_BASE_URL": spec.model_base_url, "MODEL_API_KEY": spec.model_api_key,
        "MODEL_AUTH_KIND": spec.model_auth_kind, "MODEL_PROVIDER_URL": spec.model_provider_url,
        "API_SERVER_KEY": spec.runtime_key, "HERMBOT_ADAPTER_KEY": spec.adapter_key, "HERMBOT_BOT_TOKEN": spec.bot_token, "HERMBOT_API_URL": "http://api:8000", "BOT_ID": spec.bot_id,
        "BOT_ROLE": f"You are {config.name}. {config.role}", "HERMES_ENABLED_TOOLS": json.dumps(config.enabled_tools), "HERMES_ENABLED_SKILLS": json.dumps(config.enabled_skills), "HERMES_TIMEZONE": spec.timezone}
    result = docker_request("POST", "/containers/create", params={"name": f"hermbot-bot-{spec.bot_id}"}, json={"Image": RUNTIME_IMAGE,
        "Env": [f"{key}={value}" for key, value in environment.items()], "WorkingDir": "/",
        "Labels": {**LABELS, "com.hermbot.bot-id": spec.bot_id, "com.hermbot.kind": "runtime", "com.hermbot.configuration": fingerprint, "com.hermbot.revision": str(spec.revision)},
        "HostConfig": {**RUNTIME_SECURITY, "Binds": [f"{volumes['data']}:/opt/data", f"{volumes['workspace']}:/workspace", f"{volumes['browser']}:/browser-state"], "Init": True,
            "Memory": config.memory_mb * 1024 * 1024, "NanoCpus": int(config.cpus * 1_000_000_000), "RestartPolicy": {"Name": "unless-stopped"}, **host_limits},
        "NetworkingConfig": {"EndpointsConfig": {network: {"Aliases": [f"hermbot-bot-{spec.bot_id}", f"hermes-{spec.bot_id}"]}}},
        "Healthcheck": {"Test": ["CMD", *RUNTIME_PYTHON, "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8642/health',timeout=3); urllib.request.urlopen('http://127.0.0.1:8643/health',timeout=3)"], "Interval": 10_000_000_000, "Timeout": 5_000_000_000, "StartPeriod": 40_000_000_000, "Retries": 12}})
    if spec.desired_state == "running":
        docker_request("POST", f"/containers/{result['Id']}/start")
        fence(result["Id"], spec.bot_id)
        runtime_ready(result["Id"])
    return {"container_id": result["Id"][:12], "revision": spec.revision, "state": spec.desired_state,
            **inspect_limits(result['Id'], spec)}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/runtimes", dependencies=[Depends(authorize)])
def runtimes():
    rows = filter_containers(["com.hermbot.project=hermbot", "com.hermbot.kind=runtime"])
    return {"runtimes": [{"bot_id": row["Labels"]["com.hermbot.bot-id"], "container_id": row["Id"][:12], "state": row["State"], "status": row["Status"]} for row in rows]}


@app.get('/machine', dependencies=[Depends(authorize)])
def machine():
    return read_machine()


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


# During a takeover the owner gets a fresh screen and browser in a separate, short-lived
# container: an unprivileged user, no capabilities, and a network only the API joins. The bot's
# runtime has no route to it, so what the owner types there never reaches the bot.
OWNER_SESSION_SECURITY = {
    'ReadonlyRootfs': True,
    'SecurityOpt': ['no-new-privileges:true'],
    'CapDrop': ['ALL'],
    'Tmpfs': {'/tmp': 'rw,nosuid,nodev,size=768m,mode=1777'},
    'ShmSize': 256 * 1024 * 1024,
    'Memory': 1536 * 1024 * 1024,
    'NanoCpus': 1_500_000_000,
    'PidsLimit': 512,
    'MemorySwap': 1536 * 1024 * 1024,
    'Ulimits': [{'Name': 'nofile', 'Soft': limits.NOFILE_SOFT, 'Hard': limits.NOFILE_DEFAULT}],
    'OomScoreAdj': limits.BOT_OOM_SCORE_ADJ,
}


class OwnerSessionSpec(StrictModel):
    geometry: str = Field(default="1440x900", pattern=r"^[1-9][0-9]{2,3}x[1-9][0-9]{2,3}$")


def owner_session_labels(bot_id):
    return {**LABELS, "com.hermbot.bot-id": bot_id, "com.hermbot.kind": "owner-session"}


def remove_owner_session(bot_id):
    name = "hermbot-owner-" + bot_id
    for kind in ("container", "network"):
        # Read the network only after its session container is gone, so its members are current.
        resource = optional_get(f"/containers/{name}/json" if kind == "container" else f"/networks/{name}")
        if not resource:
            continue
        labels = (resource.get("Config") or resource).get("Labels") or {}
        if any(labels.get(key) != value for key, value in owner_session_labels(bot_id).items()):
            raise HTTPException(409, "Refusing to remove an unowned owner-session resource")
        if kind == "container":
            docker_request("DELETE", f"/containers/{resource['Id']}", params={"force": "true"})
        else:
            for endpoint in resource.get("Containers") or {}:
                docker_request("POST", f"/networks/{name}/disconnect", json={"Container": endpoint, "Force": True})
            docker_request("DELETE", f"/networks/{name}")


@app.post("/owner-sessions/{bot_id}", dependencies=[Depends(authorize)])
def start_owner_session(bot_id: BotId, spec: OwnerSessionSpec):
    name, labels, token = "hermbot-owner-" + bot_id, owner_session_labels(bot_id), secrets.token_urlsafe(32)
    log_config = rotated_logs(read_machine().get('logging_driver', ''))
    with LOCKS["owner-session:" + bot_id]:
        remove_owner_session(bot_id)
        docker_request("POST", "/networks/create", json={"Name": name, "Driver": "bridge", "Labels": labels})
        for api in filter_containers(["com.docker.compose.project=hermbot", "com.docker.compose.service=api"]):
            docker_request("POST", f"/networks/{name}/connect", json={"Container": api["Id"]})
        created = docker_request("POST", "/containers/create", params={"name": name}, json={
            "Image": RUNTIME_IMAGE, "User": "10001:10001", "WorkingDir": "/",
            "Entrypoint": [*RUNTIME_PYTHON, "-u", "/opt/hermbot/owner_session.py"],
            "Env": [f"OWNER_SESSION_TOKEN={token}", f"OWNER_SESSION_GEOMETRY={spec.geometry}"],
            "Labels": labels, "HostConfig": {**OWNER_SESSION_SECURITY, "NetworkMode": name,
                                             **log_config}})
        docker_request("POST", f"/containers/{created['Id']}/start")
    return {"url": f"http://{name}:8650", "token": token}


@app.delete("/owner-sessions/{bot_id}", dependencies=[Depends(authorize)])
def stop_owner_session(bot_id: BotId):
    with LOCKS["owner-session:" + bot_id]:
        remove_owner_session(bot_id)
    return {"removed": True}


# While the owner uses a private browser the bot's runtime is frozen: every process in it stops at
# once, including ones the agent started, and none can run until it is resumed. Only Hermbot's own
# runtime for this bot can be paused; both calls are idempotent and report the verified state.
def set_runtime_paused(bot_id, paused):
    with LOCKS[bot_id]:
        runtime = find_runtime(bot_id, required=paused)
        if not runtime:
            return {"paused": False, "changed": False}
        state = docker_request("GET", f"/containers/{runtime['Id']}/json")["State"]
        changed = state["Paused"] != paused
        if paused and changed:
            if not state["Running"]:
                raise HTTPException(409, "The runtime is not running")
            docker_request("POST", f"/containers/{runtime['Id']}/pause")
        elif changed:
            docker_request("POST", f"/containers/{runtime['Id']}/unpause")
        if docker_request("GET", f"/containers/{runtime['Id']}/json")["State"]["Paused"] != paused:
            raise HTTPException(502, "The runtime did not " + ("pause" if paused else "resume"))
    return {"paused": paused, "changed": changed}


@app.post("/runtimes/{bot_id}/pause", dependencies=[Depends(authorize)])
def pause_runtime(bot_id: BotId):
    return set_runtime_paused(bot_id, True)


@app.post("/runtimes/{bot_id}/resume", dependencies=[Depends(authorize)])
def resume_runtime(bot_id: BotId):
    return set_runtime_paused(bot_id, False)


@app.post("/operations/{action}", dependencies=[Depends(authorize)])
def operate(action: str, spec: RuntimeSpec):
    if action not in ("create", "configure", "start", "stop", "archive", "restore", "delete", "clone", "ensure"):
        raise HTTPException(404, "Unknown lifecycle operation")
    with LOCKS[spec.bot_id]:
        receipts = Path("/var/lib/hermbot-supervisor/receipts")
        receipts.mkdir(parents=True, exist_ok=True, mode=0o700)
        receipt_file = receipts / (spec.operation_id + ".json")
        request_hash = hashlib.sha256(json.dumps({"action": action, "body": operation_body(spec), "image": RUNTIME_IMAGE}, sort_keys=True).encode()).hexdigest()
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
        remove_owner_session(spec.bot_id)
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
