"""Install the pinned upstream native computer driver; verify its release asset digest."""

import hashlib
import io
from pathlib import Path
import platform
import tarfile
from urllib.request import urlopen

VERSION = "0.34.0"
ASSETS = {
    "aarch64": ("linux-arm64", "9db8b9084add57eb97be8164367b24b6be54ed4f3dc01213e64b72d7fc09fddb"),
    "x86_64": ("linux-x86_64", "629ac96eff829d4dfd5cf221f3f2165c2d813aed91e5efb7b20777a741cd70a7"),
}
label, expected = ASSETS[platform.machine()]
url = f"https://github.com/trycua/cua/releases/download/cua-driver-rs-v{VERSION}/cua-driver-rs-{VERSION}-{label}-binary.tar.gz"
with urlopen(url, timeout=90) as response:
    data = response.read(100_000_001)
if len(data) > 100_000_000 or hashlib.sha256(data).hexdigest() != expected:
    raise RuntimeError("Native computer driver release checksum does not match the pinned asset")
target = Path("/opt/hermbot/cua")
target.mkdir(parents=True, exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
    members = archive.getmembers()
    if sum(m.size for m in members) > 250_000_000 or any(not (m.isfile() or m.isdir()) for m in members):
        raise RuntimeError("Unexpected native driver release contents")
    archive.extractall(target, filter="data")
(target / "cua-driver").chmod(0o755)
print("Installed pinned cua-driver " + VERSION + " with verified release SHA-256")
