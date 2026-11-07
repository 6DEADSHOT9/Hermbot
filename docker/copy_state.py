"""Copy explicitly selected native state into a new bot's distinct volumes."""
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

options = json.loads(sys.argv[1])
for mount in ("/target-data", "/target-workspace"):
    shutil.chown(mount, user="hermes", group="hermes")


def copy(source, destination):
    if source.is_symlink() or not source.exists():
        return
    if source.is_dir():
        destination.mkdir(parents=True, exist_ok=True)
        for child in source.iterdir():
            if child.name.lower() in {".env", "auth.json", "credentials.json", "config.yaml", "plugins", "token", "tokens"}:
                continue
            copy(child, destination / child.name)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    shutil.chown(destination, user="hermes", group="hermes")


for name in ("memory", "skills"):
    if options.get(name):
        for directory in (("memories", "memory") if name == "memory" else ("skills",)):
            copy(Path("/source-data") / directory, Path("/target-data") / directory)
if options.get("workspace"):
    copy(Path("/source-workspace"), Path("/target-workspace"))
if options.get("history"):
    source_db = Path("/source-data/state.db")
    if source_db.exists() and not source_db.is_symlink():
        # SQLite may need to create a WAL index even for a read-only connection.
        # The paused source remains read-only; build its consistent snapshot in scratch storage.
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "state.db"
            shutil.copy2(source_db, snapshot)
            wal = source_db.with_name("state.db-wal")
            if wal.exists() and not wal.is_symlink():
                shutil.copy2(wal, snapshot.with_name("state.db-wal"))
            with sqlite3.connect(snapshot) as source:
                with sqlite3.connect("/target-data/state.db") as target:
                    source.backup(target)
        shutil.chown("/target-data/state.db", user="hermes", group="hermes")
    copy(Path("/source-data/sessions"), Path("/target-data/sessions"))
