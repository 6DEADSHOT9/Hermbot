"""Cross-process tool admission and computer takeover, owned by Hermbot.

Shared holders represent active tool invocations, including tools that mutate data.
Only the owner adapter drains them exclusively. This is not a tool scheduling lock.
"""

from contextlib import contextmanager
import fcntl
import os
import stat
import time


class AdmissionClosed(RuntimeError):
    pass


class GateUnavailable(RuntimeError):
    pass


class OwnershipChanged(RuntimeError):
    pass


class DrainTimeout(TimeoutError):
    pass


@contextmanager
def descriptor(home):
    try:
        directory_fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open('hermbot-tool-gate.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        raise GateUnavailable('Tool admission lock is unsafe or inaccessible') from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise GateUnavailable('Tool admission requires a private regular lock file')
        # Each invocation owns an independent descriptor; subprocess exec must not inherit it.
        os.set_inheritable(fd, False)
        yield fd
    finally:
        os.close(fd)


@contextmanager
def admit(home, human_holds):
    if human_holds():
        raise AdmissionClosed('Computer takeover is pending or active')
    with descriptor(home) as fd:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            if human_holds():
                raise AdmissionClosed('Computer takeover is pending or active') from None
            raise GateUnavailable('Tool admission is temporarily unavailable') from None
        if human_holds():
            raise AdmissionClosed('Computer takeover is pending or active')
        # Never upgrade this lock or request takeover from an admitted tool callback.
        yield


@contextmanager
def drain(home, owns_control, *, timeout=70):
    deadline = time.monotonic() + timeout
    with descriptor(home) as fd:
        while True:
            if not owns_control():
                raise OwnershipChanged('Computer ownership changed during handover')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise DrainTimeout('Admitted tools have not settled') from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        if not owns_control():
            raise OwnershipChanged('Computer ownership changed during handover')
        yield
