"""Install an attachment using directory descriptors; never follow workspace symlinks."""

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import pwd
import sys
from uuid import uuid4


def install(root, body, owner=None):
    name = body["name"]
    if (
        not re.fullmatch(r"[0-9a-f-]{36}", body["attachment_id"])
        or not name
        or len(name) > 160
        or name in (".", "..")
        or Path(name).name != name
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise ValueError("Invalid attachment identity")
    data = base64.b64decode(body["content_base64"], validate=True)
    if len(data) > 10_000_000 or hashlib.sha256(data).hexdigest() != body["sha256"]:
        raise ValueError("Attachment integrity mismatch")
    descriptors = []
    temporary = None
    try:
        current = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(current)
        for part in ("attachments", body["attachment_id"]):
            try:
                os.mkdir(part, 0o755, dir_fd=current)
            except FileExistsError:
                pass
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(current)
            if owner is not None:
                os.fchown(current, owner, owner)
        temporary = ".upload-" + uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=current)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            if owner is not None:
                os.fchown(stream.fileno(), owner, owner)
        os.replace(temporary, body["name"], src_dir_fd=current, dst_dir_fd=current)
        temporary = None
    finally:
        if temporary and descriptors:
            os.unlink(temporary, dir_fd=descriptors[-1])
        for descriptor in reversed(descriptors):
            os.close(descriptor)


if __name__ == "__main__":
    staging = Path(sys.argv[1])
    try:
        install("/workspace", json.loads(staging.read_text()), owner=pwd.getpwnam("hermes").pw_uid)
    finally:
        staging.unlink(missing_ok=True)
