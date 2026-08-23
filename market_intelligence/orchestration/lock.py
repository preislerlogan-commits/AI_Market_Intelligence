"""A conservative, cross-platform local run lock for orchestration execution.

Design (fail-closed, deliberately simple):

DuckDB itself already prevents two processes from writing to the same
database file at once, but that only surfaces the conflict deep inside a
specific job's storage call, after earlier jobs in a run may already have
made partial progress, and via a raw DuckDB exception not guaranteed to be
safe to report at the orchestration boundary. This lock exists to fail
fast and cleanly, before any job runs at all.

Reliably determining whether the process that created an existing lock
file is still alive is not something this project's existing dependencies
(``duckdb``, ``pandas``, ``pyarrow``, ``httpx``, ``pydantic``,
``pydantic-settings``, ``python-dotenv``) can do in a cross-platform-correct
way -- there is no ``psutil`` here, and ``os.kill(pid, 0)`` is not a safe,
uniform liveness probe across POSIX and Windows (on Windows it does not
mean the same thing it does on POSIX). Rather than guess -- e.g. breaking a
lock merely because it looks "stale" by age, which risks two processes
writing at once if the original process is merely slow -- this lock
**fails closed**: it never breaks or removes an existing lock file
automatically, under any condition, regardless of age. A lock file left
behind by a crashed process must be removed manually by the operator, once
they have independently confirmed no orchestration process is actually
still running. This is a documented limitation, not an oversight -- see
``docs/INGESTION_ORCHESTRATION.md``.

Acquisition is atomic (``os.O_CREAT | os.O_EXCL``, which every supported
platform honors as an atomic "create only if it does not already exist"),
so there is no separate check-then-create race window. The lock file lives
at ``Settings.project_data_path / "cache" / "orchestration.lock"`` --
always derived from ``Settings``, never from a caller-supplied path -- and
only this exact, fixed filename is ever created or removed; no other file
is ever touched.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.config.settings import Settings

LOCK_FILENAME = "orchestration.lock"


class OrchestrationLockError(RuntimeError):
    """Sanitized lock failure. Never includes a raw OS exception message or path internals."""


class OrchestrationLockContentionError(OrchestrationLockError):
    """Raised when another process already holds the run lock."""


def default_lock_path(settings: Settings) -> Path:
    """The fixed lock file location, always derived from ``Settings.project_data_path``."""
    return settings.project_data_path / "cache" / LOCK_FILENAME


class RunLock:
    """A conservative, fail-closed, atomically-acquired local run lock.

    Use as a context manager: ``with RunLock(path): ...``. Only the
    instance that successfully acquired the lock will ever remove the lock
    file, and only that fixed lock file -- never any other file in the
    directory -- is ever created or removed by this class.
    """

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path
        self._acquired = False

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self) -> None:
        """Atomically create the lock file. Raises a sanitized error on any failure.

        Raises ``OrchestrationLockContentionError`` if the lock file
        already exists (another process holds it), or
        ``OrchestrationLockError`` for any other failure (e.g. the parent
        directory could not be created). Never breaks or removes an
        existing lock file.
        """
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OrchestrationLockError(
                f"Failed to prepare the lock directory: {type(exc).__name__}."
            ) from None

        content = f"pid={os.getpid()} acquired_at_utc={datetime.now(UTC).isoformat()}\n"
        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise OrchestrationLockContentionError(
                "Another orchestration run already holds the run lock."
            ) from None
        except OSError as exc:
            raise OrchestrationLockError(
                f"Failed to acquire the run lock: {type(exc).__name__}."
            ) from None

        try:
            os.write(fd, content.encode("utf-8"))
        except OSError as exc:
            raise OrchestrationLockError(
                f"Failed to write the run lock: {type(exc).__name__}."
            ) from None
        finally:
            os.close(fd)

        self._acquired = True

    def release(self) -> None:
        """Remove the lock file, but only if this instance actually acquired it.

        Never raises: a failure to remove the lock is sanitized and
        silently tolerated (the lock will then need manual removal -- see
        the module docstring's documented limitation), so a release
        failure can never mask or replace a job's own result.
        """
        if not self._acquired:
            return
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass
        finally:
            self._acquired = False

    def __enter__(self) -> RunLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()
