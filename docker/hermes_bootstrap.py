"""Initialize the pinned single-bot runtime without mounting any other bot's state."""

import os
import json
import importlib
from pathlib import Path
import shutil
import sys
import subprocess
import pwd

import yaml

os.environ["HOME"] = os.environ.get("HERMES_HOME", "/opt/data")

# The supervisor mounts this directory as a root-owned tmpfs. Install the managed
# extension before dropping privileges; native tools cannot replace its files or
# shadow it with another user plugin. Private memory, skills and workspace stay writable.
plugin_home = Path(os.environ["HOME"]) / "plugins" / "hermbot"
shutil.copytree("/opt/hermbot/plugin", plugin_home, dirs_exist_ok=True)
if os.getuid() == 0:
    for path in plugin_home.rglob('*'):
        os.chown(path, 0, 0)
        path.chmod(0o555 if path.is_dir() else 0o444)
    os.chown(plugin_home, 0, 0)
    plugin_home.chmod(0o555)
    plugin_home.parent.chmod(0o555)

if os.getuid() == 0:
    account = pwd.getpwnam("hermes")
    browser_home = Path("/browser-state")
    browser_home.mkdir(exist_ok=True)
    os.chown(browser_home, account.pw_uid, account.pw_gid)
    runtime_home = Path("/tmp/hermbot-runtime")
    runtime_home.mkdir(mode=0o700, exist_ok=True)
    os.chown(runtime_home, account.pw_uid, account.pw_gid)
    os.chmod(runtime_home, 0o700)
    os.environ["XDG_RUNTIME_DIR"] = str(runtime_home)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)

home = Path(os.environ.get("HERMES_HOME", "/opt/data"))
home.mkdir(parents=True, exist_ok=True)
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
if os.environ.get('MODEL_AUTH_KIND') in ('chatgpt_oauth', 'codex_oauth'):
    config['model']['provider'] = 'custom:hermbot-chatgpt'
    config['providers'] = {**config.get('providers', {}), 'hermbot-chatgpt': {
        'base_url': config['model']['base_url'], 'api_mode': 'codex_responses', 'key_env': 'MODEL_API_KEY',
    }}
    from hermes_cli.config_defaults import DEFAULT_CONFIG
    config['fallback_providers'] = []
    auxiliary = config.setdefault('auxiliary', {})
    for task, defaults in DEFAULT_CONFIG['auxiliary'].items():
        if isinstance(defaults, dict) and 'provider' in defaults:
            auxiliary[task] = {**auxiliary.get(task, {}), 'provider': 'custom:hermbot-chatgpt',
                               'model': config['model']['default'], 'base_url': config['model']['base_url'],
                               'api_mode': 'codex_responses', 'api_key': '', 'fallback_chain': [], 'prefer_fast_model': False}
else:
    # Switching back to an API key must also stop auxiliary requests using the old broker route.
    config.get('providers', {}).pop('hermbot-chatgpt', None)
    for task, values in list(config.get('auxiliary', {}).items()):
        if isinstance(values, dict) and values.get('provider') == 'custom:hermbot-chatgpt':
            config['auxiliary'].pop(task)
toolsets = list(
    dict.fromkeys(json.loads(os.environ.get("HERMES_ENABLED_TOOLS", '["file","terminal"]')) + ["hermbot"])
)
config["toolsets"] = toolsets
config["platform_toolsets"] = {"api_server": toolsets, "cron": toolsets}
config["terminal"] = {"backend": "local", "cwd": "/workspace", "timeout": 60}
config["plugins"] = {"enabled": ["hermbot"]}
config["tools"] = {"tool_search": {"enabled": "off"}}
config["gateway"] = {
    "api_server": {"max_concurrent_runs": 1},
    "platforms": {
        "api_server": {"enabled": True, "host": "0.0.0.0", "port": 8642, "key": os.environ["API_SERVER_KEY"]}
    },
}
config["approvals"] = {"unattended_mode": "deny", "cron_mode": "deny", "single_query_mode": "deny"}
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
config_path.write_text(yaml.safe_dump(config, sort_keys=False))
(home / "SOUL.md").write_text(
    os.environ.get("BOT_ROLE", "Be a helpful persistent teammate.")
    + "\nUse Hermbot tools to collaborate. Finish work with hermbot_finish.\n"
    + (
        "Enabled skills: " + ", ".join(selected_skills) + ". Use skill_view to consult them when relevant.\n"
        if selected_skills
        else ""
    )
)
os.environ["OPENAI_API_KEY"] = os.environ.get("MODEL_API_KEY") or "not-configured"
os.environ["OPENAI_BASE_URL"] = config["model"]["base_url"]
os.environ["API_SERVER_ENABLED"] = "true"
os.environ["API_SERVER_HOST"] = "0.0.0.0"
os.environ["API_SERVER_PORT"] = "8642"
os.environ["AGENT_BROWSER_PROFILE"] = "/browser-state/profile"
os.environ["AGENT_BROWSER_EXECUTABLE_PATH"] = "/usr/bin/chromium"
subprocess.Popen(
    [
        "/opt/hermes/.venv/bin/python",
        "-m",
        "uvicorn",
        "runtime_api:app",
        "--app-dir",
        "/opt/hermbot",
        "--host",
        "0.0.0.0",
        "--port",
        "8643",
        "--no-access-log",
    ],
    env=os.environ.copy(),
)
os.execv("/opt/hermes/.venv/bin/hermes", ["hermes", "gateway", "run"])
