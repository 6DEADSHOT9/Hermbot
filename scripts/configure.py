"""Create private local configuration. Never print credential values."""
import os
import base64
from pathlib import Path
import secrets
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
sys.path.insert(0, str(root))


def rootless_docker():
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .SecurityOptions}}"],
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "name=rootless" in result.stdout.lower()


def oom_settings(rootless):
    from backend.limits import BOT_OOM_SCORE_ADJ, CORE_OOM_SCORE_ADJ, DATA_OOM_SCORE_ADJ
    return {
        "CORE_OOM_SCORE_ADJ": 0 if rootless and CORE_OOM_SCORE_ADJ < 0 else CORE_OOM_SCORE_ADJ,
        "DATA_OOM_SCORE_ADJ": 0 if rootless and DATA_OOM_SCORE_ADJ < 0 else DATA_OOM_SCORE_ADJ,
        "BOT_OOM_SCORE_ADJ": BOT_OOM_SCORE_ADJ,
    }


rootless = rootless_docker()
settings = oom_settings(rootless)
if destination.exists():
    existing = destination.read_text()
    pairs = [line.split("=", 1) for line in existing.splitlines() if "=" in line and not line.startswith("#")]
    names = {key for key, _ in pairs}
    missing = [f"{key}={value}" for key, value in settings.items() if key not in names]
    if rootless:
        current = dict(pairs)
        missing.extend(f"{key}=0" for key in ("CORE_OOM_SCORE_ADJ", "DATA_OOM_SCORE_ADJ")
                       if key in current and current[key].lstrip().startswith("-"))
    if missing:
        with destination.open("a") as stream:
            stream.write("\n# Container kill-order settings\n" + "\n".join(missing) + "\n")
        os.chmod(destination, 0o600)
    print("Configuration is present; existing credentials and settings were preserved.")
    raise SystemExit(0)
values = {
    key: secrets.token_urlsafe(32)
    for key in (
        "OWNER_TOKEN", "INTERNAL_TOKEN", "SUPERVISOR_TOKEN", "RESEARCHER_TOKEN", "WRITER_TOKEN",
        "REVIEWER_TOKEN", "RESEARCHER_API_KEY", "WRITER_API_KEY", "REVIEWER_API_KEY",
        "POSTGRES_PASSWORD", "STORAGE_PASSWORD", "CONFIG_ENCRYPTION_KEY",
    )
}
values.update(MODEL_NAME="", MODEL_BASE_URL="", MODEL_API_KEY="")
values.update(settings)
values["CONFIG_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(descriptor, "w") as stream:
    stream.write("# Private local configuration. Do not commit or paste this file.\n")
    stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Created .env with separate owner, service, bot, and storage credentials.")
print("Set MODEL_NAME, MODEL_BASE_URL, and MODEL_API_KEY for real model runs.")
