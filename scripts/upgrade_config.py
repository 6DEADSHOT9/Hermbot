"""Add Phase 2 configuration without replacing or displaying existing secrets."""
import base64
import os
from pathlib import Path
import secrets

path = Path(__file__).resolve().parents[1] / ".env"
text = path.read_text()
names = {line.split("=", 1)[0] for line in text.splitlines() if "=" in line and not line.startswith("#")}
extra = []
if "CONFIG_ENCRYPTION_KEY" not in names:
    extra.append("CONFIG_ENCRYPTION_KEY=" + base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
if "PUBLIC_URL" not in names:
    extra.append("PUBLIC_URL=http://localhost:8780")
if extra:
    with path.open("a") as stream:
        stream.write("\n# Phase 2 private configuration\n" + "\n".join(extra) + "\n")
os.chmod(path, 0o600)
print("Phase 2 configuration is present; existing credentials were preserved.")
