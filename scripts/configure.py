"""Create private local configuration. Never print credential values."""
import os
import base64
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
destination = root / ".env"
if destination.exists():
    raise SystemExit(".env already exists; edit it to configure the model. Existing secrets were preserved.")
values = {
    key: secrets.token_urlsafe(32)
    for key in (
        "OWNER_TOKEN", "INTERNAL_TOKEN", "SUPERVISOR_TOKEN", "RESEARCHER_TOKEN", "WRITER_TOKEN",
        "REVIEWER_TOKEN", "RESEARCHER_API_KEY", "WRITER_API_KEY", "REVIEWER_API_KEY",
        "POSTGRES_PASSWORD", "STORAGE_PASSWORD", "CONFIG_ENCRYPTION_KEY",
    )
}
values.update(MODEL_NAME="", MODEL_BASE_URL="", MODEL_API_KEY="")
values["CONFIG_ENCRYPTION_KEY"] = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(descriptor, "w") as stream:
    stream.write("# Private local configuration. Do not commit or paste this file.\n")
    stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
print("Created .env with separate owner, service, bot, and storage credentials.")
print("Set MODEL_NAME, MODEL_BASE_URL, and MODEL_API_KEY for real model runs.")
