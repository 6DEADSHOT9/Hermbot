"""Read only native Python source; omit virtual environments and all private runtime state."""
import hashlib
import json
import os
from pathlib import Path

root = Path('/opt/hermes')
digest = hashlib.sha256()
count = 0
for directory, dirs, files in os.walk(root):
    dirs[:] = sorted(d for d in dirs if d not in ('.venv', '.git', '__pycache__', 'node_modules'))
    for name in sorted(files):
        path = Path(directory, name)
        if path.suffix == '.py' and not path.is_symlink():
            digest.update(str(path.relative_to(root)).encode() + b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).digest())
            count += 1
print(json.dumps({'python_files': count, 'sha256': digest.hexdigest()}))
