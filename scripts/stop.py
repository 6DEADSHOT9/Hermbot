"""Stop only this application's managed runtimes and Compose services; preserve all volumes."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent


if __name__ == "__main__":
    identifiers = subprocess.run(["docker", "ps", "-q", "--filter", "label=com.hermbot.project=hermbot", "--filter", "label=com.hermbot.managed=true", "--filter", "label=com.hermbot.kind=runtime"], cwd=root, check=True, capture_output=True, text=True).stdout.split()
    # Prevent the worker's desired-state reconciliation from restarting bots during shutdown.
    subprocess.run(["docker", "compose", "stop", "worker"], cwd=root, check=True)
    if identifiers:
        subprocess.run(["docker", "stop", *identifiers], cwd=root, check=True)
    subprocess.run(["docker", "compose", "stop"], cwd=root, check=True)
