"""Cross-process execution lock for a local SQLite collector."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class RunLockError(RuntimeError):
    """Raised when another collector owns the database lock."""


def _try_lock(handle) -> bool:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except ImportError:  # pragma: no cover - Windows fallback
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    except BlockingIOError:
        return False


def _unlock(handle) -> None:
    try:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except ImportError:  # pragma: no cover - Windows fallback
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass


@contextmanager
def execution_lock(database: str | Path, *, timeout: float = 0.0) -> Iterator[Path | None]:
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
