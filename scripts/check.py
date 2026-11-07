"""Run isolated backend checks without displaying service credentials."""
from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent.parent


def run(*args, **kwargs):
    return subprocess.run(args, cwd=root, check=True, text=True, **kwargs)


if __name__ == "__main__":
    (root / "test-results").mkdir(exist_ok=True)
    run("docker", "compose", "up", "-d", "postgres", "storage")
    databases = run("docker", "compose", "exec", "-T", "postgres", "psql", "-U", "hermbot", "-d", "hermbot", "-Atc", "SELECT datname FROM pg_database WHERE datname='hermbot_test'", capture_output=True).stdout
    if "hermbot_test" not in databases:
        run("docker", "compose", "exec", "-T", "postgres", "createdb", "-U", "hermbot", "hermbot_test")
    run("docker", "build", "-f", "backend/Tools.Dockerfile", "-t", "hermbot-tools:phase1", ".")
    common = ["docker", "run", "--rm", "--network", "hermbot_control", "--env-file", ".env", "-v", f"{root}:/app", "-w", "/app", "hermbot-tools:phase1"]
    run(*common, "ruff", "check", "backend", "model_broker", "plugins", "docker", "tests", "scripts")
    launcher = 'import os; from urllib.parse import quote; os.environ["DATABASE_URL"]="postgresql://hermbot:"+quote(os.environ["POSTGRES_PASSWORD"],safe="")+"@postgres/hermbot_test"; import pytest; raise SystemExit(pytest.main(["-q","--junitxml=/app/test-results/backend.xml"]))'
    run(*common, "python", "-c", launcher)
