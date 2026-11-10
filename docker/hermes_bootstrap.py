"""Initialize the pinned single-bot runtime without mounting any other bot's state.

The bootstrap stays a root parent. It prepares state, writes the owner-controlled configuration as root,
starts the private adapter, confirms the port fence that keeps agent processes away from both control
ports, starts the Hermes gateway, and exits when either child does so that the container restarts.
"""

import os
import json
import importlib
from pathlib import Path
import shutil
import signal
import socket
import sys
import subprocess
import pwd
import time

import yaml

# Isolated mode ignores PYTHONPATH; the Hermbot plugin that Hermes loads below imports from here.
sys.path.insert(0, "/opt/hermbot")
os.environ["HOME"] = os.environ.get("HERMES_HOME", "/opt/data")
if os.getuid() != 0:
    raise SystemExit("The runtime bootstrap must start as root to separate the agent from its controls")
account = pwd.getpwnam("hermes")
# Only the adapter receives its credential; no agent process ever inherits it.
adapter_key = os.environ.pop("HERMBOT_ADAPTER_KEY", "")
# Isolated mode and a root-owned working directory keep agent-planted modules off the adapter's
# import path; --app-dir supplies its own modules.
ADAPTER = ["/opt/hermes/.venv/bin/python", "-I", "-u", "-m", "uvicorn", "runtime_api:app", "--app-dir", "/opt/hermbot",
           "--host", "0.0.0.0", "--port", "8643", "--no-access-log"]
# The gateway and the agent's tools never need these; the adapter needs none of the agent's keys.
AGENT_ONLY = ("API_SERVER_KEY", "MODEL_API_KEY", "OPENAI_API_KEY", "HERMBOT_BOT_TOKEN")

# The supervisor mounts this directory as a root-owned tmpfs. Install the managed
# extension before dropping privileges; native tools cannot replace its files or
# shadow it with another user plugin. Private memory, skills and workspace stay writable.
plugin_home = Path(os.environ["HOME"]) / "plugins" / "hermbot"
shutil.copytree("/opt/hermbot/plugin", plugin_home, dirs_exist_ok=True)
for path in plugin_home.rglob('*'):
    os.chown(path, 0, 0)
    path.chmod(0o555 if path.is_dir() else 0o444)
os.chown(plugin_home, 0, 0)
plugin_home.chmod(0o555)
plugin_home.parent.chmod(0o555)

browser_home = Path("/browser-state")
browser_home.mkdir(exist_ok=True)
os.chown(browser_home, account.pw_uid, account.pw_gid)
runtime_home = Path("/tmp/hermbot-runtime")
runtime_home.mkdir(mode=0o700, exist_ok=True)
os.chown(runtime_home, account.pw_uid, account.pw_gid)
os.chmod(runtime_home, 0o700)
os.environ["XDG_RUNTIME_DIR"] = str(runtime_home)

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
home.mkdir(parents=True, exist_ok=True)
# The agent creates and replaces its own files here, but the sticky root-owned directory stops
# it from replacing owner-controlled configuration that Hermes reloads on every change.
home_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
os.fchown(home_fd, 0, account.pw_gid)
os.fchmod(home_fd, 0o1775)
os.close(home_fd)


def agent_identity():
    os.setgroups([])
    os.setresgid(account.pw_gid, account.pw_gid, account.pw_gid)
    os.setresuid(account.pw_uid, account.pw_uid, account.pw_uid)


def write_owned(name, text, mode):
    """Atomically replace a top-level HERMES_HOME file as root without following agent-made links."""
    directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = f".{name}.hermbot-{os.getpid()}"
    try:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=directory)
        with os.fdopen(fd, "w") as handle:
            os.fchown(handle.fileno(), 0, 0)
            os.fchmod(handle.fileno(), mode)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        os.close(directory)


def connects(host, port):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def agent_reaches_adapter():
    """Probe with the agent's identity; the fence must refuse the adapter's live port."""
    probe = os.fork()
    if probe == 0:
        try:
            agent_identity()
            os._exit(0 if connects("127.0.0.1", 8643) else 1)
        except BaseException:
            os._exit(2)
    _, status = os.waitpid(probe, 0)
    return os.waitstatus_to_exitcode(status) != 1


def supervise(preparer, reader):
    with os.fdopen(reader, "rb") as stream:
        prepared = stream.read().decode()
    _, status = os.waitpid(preparer, 0)
    if os.waitstatus_to_exitcode(status) != 0 or not prepared:
        raise SystemExit("Runtime preparation failed")
    config = yaml.safe_load(prepared)
    write_owned("config.yaml", prepared, 0o644)
    # Hermes loads this file at startup; an empty root-owned copy keeps agent edits out of it.
    write_owned(".env", "", 0o444)
    environment = dict(os.environ)
    environment.update({"OPENAI_API_KEY": os.environ.get("MODEL_API_KEY") or "not-configured",
                        "OPENAI_BASE_URL": config["model"]["base_url"], "API_SERVER_ENABLED": "true",
                        "API_SERVER_HOST": "0.0.0.0", "API_SERVER_PORT": "8642",
                        "AGENT_BROWSER_PROFILE": "/browser-state/profile", "AGENT_BROWSER_EXECUTABLE_PATH": "/usr/bin/chromium"})
    if os.environ.get("MODEL_AUTH_KIND") not in ("chatgpt_oauth", "codex_oauth"):
        # Hermes reads the relay token from OPENAI_API_KEY, which it strips from tool environments.
        environment.pop("MODEL_API_KEY", None)
    environment.pop("MODEL_PROVIDER_URL", None)  # Only preparation needs it, to pick the API style.
    adapter_environment = {key: value for key, value in environment.items() if key not in AGENT_ONLY}
    adapter_environment["HERMBOT_ADAPTER_KEY"] = adapter_key
    adapter = subprocess.Popen(ADAPTER, env=adapter_environment, cwd="/", preexec_fn=agent_identity)
    children = [adapter]
    requested = []

    def stop(signum, _frame):
        requested.append(signum)
        for child in children:
            if child.poll() is None:
                child.send_signal(signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    deadline = time.monotonic() + 60
    while not connects("127.0.0.1", 8643):
        if adapter.poll() is not None or time.monotonic() > deadline:
            raise SystemExit("The private adapter did not start")
        time.sleep(0.2)
    waiting = False
    # Fail closed: until the supervisor installs the fence, the agent never starts.
    while agent_reaches_adapter():
        if adapter.poll() is not None:
            raise SystemExit("The private adapter stopped")
        if not waiting:
            print("Waiting for the control-port fence before starting the agent", flush=True)
            waiting = True
        time.sleep(1)
    children.append(subprocess.Popen(["/opt/hermes/.venv/bin/hermes", "gateway", "run"],
                                     env=environment, cwd="/workspace", preexec_fn=agent_identity))
    # Either child ending ends the runtime, so a stopped adapter can never be impersonated.
    finished = os.wait()[0]
    for child in children:
        if child.pid != finished and child.poll() is None:
            child.terminate()
    for child in children:
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            child.kill()
        except ChildProcessError:
            pass
    raise SystemExit(0 if requested else 1)


reader, writer = os.pipe()
preparer = os.fork()
if preparer:
    os.close(writer)
    supervise(preparer, reader)
os.close(reader)
agent_identity()

# The supervisor has stopped the previous container before this bootstrap. Chromium's
# singleton markers contain the old hostname and otherwise block a persistent profile
# after recreation. Unlink only these ephemeral markers through verified directory fds.
browser_fd = os.open("/browser-state", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    try:
        profile_fd = os.open("profile", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=browser_fd)
    except FileNotFoundError:
        profile_fd = None
    if profile_fd is not None:
        try:
            for marker in ("SingletonLock", "SingletonCookie", "SingletonSocket", "DevToolsActivePort"):
                try:
                    os.unlink(marker, dir_fd=profile_fd)
                except FileNotFoundError:
                    pass
        finally:
            os.close(profile_fd)
finally:
    os.close(browser_fd)
config_path = home / "config.yaml"
config = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
config = config or {}
config["model"] = {
    "default": os.environ.get("MODEL_NAME") or "unconfigured",
    "provider": "custom",
    "base_url": os.environ.get("MODEL_BASE_URL") or "http://127.0.0.1:1/v1",
}
MODEL_ROUTES = ("hermbot-chatgpt", "hermbot-relay")


def apply_model_route(config, route, api_mode, key_env):
    """Send the main model and every auxiliary task through one Hermbot route, and drop unused routes.

    Every configured provider is reached through Hermbot's relay, which holds the provider's credential;
    this runtime only ever holds its own relay token. Auxiliary tasks are pinned to the same route and
    model, so none of them can fall back to another provider or ask the relay for an unassigned model.
    """
    for name in MODEL_ROUTES:
        if name == route:
            continue
        config.get('providers', {}).pop(name, None)
        for task, values in list(config.get('auxiliary', {}).items()):
            if isinstance(values, dict) and values.get('provider') == 'custom:' + name:
                config['auxiliary'].pop(task)
    if not route:
        return
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    model = config['model']
    model['provider'] = 'custom:' + route
    config['providers'] = {**config.get('providers', {}), route: {
        'base_url': model['base_url'], 'api_mode': api_mode, 'key_env': key_env,
    }}
    config['fallback_providers'] = []
    auxiliary = config.setdefault('auxiliary', {})
    for task, defaults in DEFAULT_CONFIG['auxiliary'].items():
        if isinstance(defaults, dict) and 'provider' in defaults:
            auxiliary[task] = {**auxiliary.get(task, {}), 'provider': 'custom:' + route,
                               'model': model['default'], 'base_url': model['base_url'],
                               'api_mode': api_mode, 'api_key': '', 'fallback_chain': [], 'prefer_fast_model': False}


if os.environ.get('MODEL_AUTH_KIND') in ('chatgpt_oauth', 'codex_oauth'):
    apply_model_route(config, 'hermbot-chatgpt', 'codex_responses', 'MODEL_API_KEY')
elif os.environ.get('MODEL_PROVIDER_URL'):
    # Speak the API style Hermes itself would choose for the real provider, so routing through the
    # relay changes nothing the provider sees (official OpenAI hosts need the Responses API).
    from hermes_cli.runtime_provider import _detect_api_mode_for_url
    apply_model_route(config, 'hermbot-relay', _detect_api_mode_for_url(os.environ['MODEL_PROVIDER_URL']) or 'chat_completions',
                      'OPENAI_API_KEY')
else:
    apply_model_route(config, None, None, None)
toolsets = list(
    dict.fromkeys(json.loads(os.environ.get("HERMES_ENABLED_TOOLS", '["file","terminal"]')) + ["hermbot"])
)
config["toolsets"] = toolsets
config["platform_toolsets"] = {"api_server": toolsets, "cron": toolsets}
config["terminal"] = {"backend": "local", "cwd": "/workspace", "timeout": 60}
config["plugins"] = {"enabled": ["hermbot"]}
config["tools"] = {"tool_search": {"enabled": "off"}}
# The API key reaches Hermes only through its environment, never this readable file.
config["gateway"] = {
    "api_server": {"max_concurrent_runs": 1},
    "platforms": {
        "api_server": {"enabled": True, "host": "0.0.0.0", "port": 8642}
    },
}
config["approvals"] = {"unattended_mode": "deny", "cron_mode": "deny", "single_query_mode": "deny"}
# Earlier versions let the agent edit this file; drop sections it could have used to weaken approvals.
config.pop("security", None)
config.pop("hooks", None)
# Hermes requires a positive iteration ceiling. The application enforces the owner-visible
# active budget; this large native ceiling removes the old 24-turn cutoff without slicing runs.
config["agent"] = {**config.get('agent', {}), "max_turns": 1_000_000}
config["timezone"] = os.environ.get("HERMES_TIMEZONE", "UTC")
# In this revision, backend=off disables the optional Browser Use CLI replacement,
# leaving Hermes's built-in browser tools advertised by the product inventory.
config["browser"] = {**config.get("browser", {}), "backend": "off", "headed": True}
desktop = config.get("bot_desktop", {})
config["bot_desktop"] = {**desktop, "auto_start": True, "geometry": "1280x800",
                         "min_free_memory_mb": desktop.get("min_free_memory_mb", 1024)}
sys.path.insert(0, "/opt/hermes")
importlib.import_module("tools.skills_sync").sync_skills(quiet=True)
selected_skills = json.loads(os.environ.get("HERMES_ENABLED_SKILLS", "[]"))
# In this pinned Hermes version, skip_disabled=True means include disabled skills.
installed_skills = importlib.import_module("tools.skills_tool")._find_all_skills(skip_disabled=True)
missing_skills = set(selected_skills) - {skill["name"] for skill in installed_skills}
if missing_skills:
    raise ValueError("Enabled skill files are missing: " + ", ".join(sorted(missing_skills)))
config["skills"] = {
    "disabled": [skill["name"] for skill in installed_skills if skill["name"] not in selected_skills]
}
(home / "SOUL.md").write_text(
    os.environ.get("BOT_ROLE", "Be a helpful persistent teammate.")
    + "\nUse Hermbot tools to collaborate. Finish work with hermbot_finish.\n"
    + (
        "Enabled skills: " + ", ".join(selected_skills) + ". Use skill_view to consult them when relevant.\n"
        if selected_skills
        else ""
    )
)
# The root parent writes the configuration; this unprivileged preparer only computes it.
with os.fdopen(writer, "w") as handle:
    handle.write(yaml.safe_dump(config, sort_keys=False))
os._exit(0)
