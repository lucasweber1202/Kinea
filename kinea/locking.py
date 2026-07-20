"""Cross-process execution lock for a local SQLite collector."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class RunLockError(RuntimeError):
    """Raised when another collector owns the database lock."""


def _prepare_windows_lock_byte(handle) -> None:
    """Ensure every Windows process locks the same existing byte at offset zero."""
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


def _try_lock_windows(handle, msvcrt_module) -> bool:
    _prepare_windows_lock_byte(handle)
    try:
        msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _unlock_windows(handle, msvcrt_module) -> None:
    try:
        handle.seek(0)
        msvcrt_module.locking(handle.fileno(), msvcrt_module.LK_UNLCK, 1)
    except OSError:
        pass


def _try_lock(handle) -> bool:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except ImportError:  # pragma: no cover - Windows fallback
        import msvcrt

        return _try_lock_windows(handle, msvcrt)
    except BlockingIOError:
        return False


def _unlock(handle) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except ImportError:  # pragma: no cover - Windows fallback
        import msvcrt

        _unlock_windows(handle, msvcrt)


@contextmanager
def execution_lock(
    database: str | Path, *, timeout: float = 0.0
) -> Iterator[Path | None]:
    """Prevent concurrent CLI runs against the same SQLite file."""
    if str(database) == ":memory:":
        yield None
        return
    db_path = Path(database).expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = Path(f"{db_path}.lock")
    deadline = time.monotonic() + max(timeout, 0.0)
    with lock_path.open("a+b") as handle:
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise RunLockError(f"collector already running for {db_path}")
            time.sleep(min(0.1, max(deadline - time.monotonic(), 0.0)))
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"pid={os.getpid()}\n".encode())
            handle.flush()
            yield lock_path
        finally:
            _unlock(handle)
