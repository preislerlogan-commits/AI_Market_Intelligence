"""Tests for market_intelligence.orchestration.lock.

Every lock under test is pointed at an isolated tmp_path -- never the real
project data directory. Contention is simulated with two RunLock instances
in the same test process, never real concurrent processes.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from market_intelligence.orchestration.lock import (
    OrchestrationLockContentionError,
    OrchestrationLockError,
    RunLock,
    default_lock_path,
)


def test_acquire_creates_lock_file(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    lock = RunLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.exists()
    finally:
        lock.release()


def test_acquire_creates_parent_directory(tmp_path):
    lock_path = tmp_path / "cache" / "nested" / "orchestration.lock"
    lock = RunLock(lock_path)
    lock.acquire()
    try:
        assert lock_path.parent.exists()
    finally:
        lock.release()


def test_second_acquire_raises_contention_error(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    first = RunLock(lock_path)
    first.acquire()
    try:
        second = RunLock(lock_path)
        with pytest.raises(OrchestrationLockContentionError):
            second.acquire()
    finally:
        first.release()


def test_release_removes_lock_file(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    lock = RunLock(lock_path)
    lock.acquire()
    lock.release()
    assert not lock_path.exists()


def test_release_allows_reacquisition_by_a_new_lock_instance(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    first = RunLock(lock_path)
    first.acquire()
    first.release()

    second = RunLock(lock_path)
    second.acquire()  # must not raise
    second.release()


def test_release_without_acquire_is_a_no_op(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    lock = RunLock(lock_path)
    lock.release()  # never acquired -- must not raise or touch anything
    assert not lock_path.exists()


def test_release_never_removes_a_lock_it_did_not_acquire(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    first = RunLock(lock_path)
    first.acquire()
    try:
        second = RunLock(lock_path)
        with pytest.raises(OrchestrationLockContentionError):
            second.acquire()
        second.release()  # never acquired -- must not remove first's lock
        assert lock_path.exists()
    finally:
        first.release()


def test_release_never_touches_an_unrelated_file(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    unrelated = cache_dir / "unrelated.txt"
    unrelated.write_text("keep me", encoding="utf-8")

    lock_path = cache_dir / "orchestration.lock"
    lock = RunLock(lock_path)
    lock.acquire()
    lock.release()

    assert unrelated.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep me"


def test_context_manager_releases_lock_on_success(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    with RunLock(lock_path):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_context_manager_releases_lock_after_exception(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    with pytest.raises(RuntimeError):
        with RunLock(lock_path):
            assert lock_path.exists()
            raise RuntimeError("simulated job failure")
    assert not lock_path.exists()


def test_lock_content_never_raises_and_is_diagnostic_only(tmp_path):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    lock = RunLock(lock_path)
    lock.acquire()
    try:
        content = lock_path.read_text(encoding="utf-8")
        assert "pid=" in content
    finally:
        lock.release()


def test_directory_creation_failure_raises_sanitized_error(tmp_path, monkeypatch):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    secret_marker = "SECRET-MKDIR-DETAIL"

    def fake_mkdir(self, *args, **kwargs):
        raise OSError(f"boom {secret_marker}")

    monkeypatch.setattr(Path, "mkdir", fake_mkdir)

    lock = RunLock(lock_path)
    with pytest.raises(OrchestrationLockError) as exc_info:
        lock.acquire()
    assert secret_marker not in str(exc_info.value)


def test_write_failure_cleans_up_newly_created_lock_and_raises_sanitized_error(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    secret_marker = "SECRET-WRITE-DETAIL"

    def fake_write(fd, data):
        raise OSError(f"boom {secret_marker}")

    monkeypatch.setattr(os, "write", fake_write)

    lock = RunLock(lock_path)
    with pytest.raises(OrchestrationLockError) as exc_info:
        lock.acquire()

    assert secret_marker not in str(exc_info.value)
    assert not lock_path.exists()  # best-effort cleanup of the just-created lock
    assert not lock._acquired


def test_close_failure_after_successful_write_cleans_up_and_raises_sanitized_error(
    tmp_path, monkeypatch
):
    lock_path = tmp_path / "cache" / "orchestration.lock"
    secret_marker = "SECRET-CLOSE-DETAIL"
    real_close = os.close

    def fake_close(fd):
        # Actually release the descriptor (as most real close() failures --
        # e.g. a reported flush error -- still do) so cleanup can remove the
        # file; only the raised error is simulated.
        real_close(fd)
        raise OSError(f"boom {secret_marker}")

    monkeypatch.setattr(os, "close", fake_close)

    lock = RunLock(lock_path)
    with pytest.raises(OrchestrationLockError) as exc_info:
        lock.acquire()

    assert secret_marker not in str(exc_info.value)
    assert not lock_path.exists()
    assert not lock._acquired


def test_write_failure_does_not_block_a_subsequent_acquisition(tmp_path, monkeypatch):
    lock_path = tmp_path / "cache" / "orchestration.lock"

    def fake_write(fd, data):
        raise OSError("simulated write failure")

    monkeypatch.setattr(os, "write", fake_write)
    lock = RunLock(lock_path)
    with pytest.raises(OrchestrationLockError):
        lock.acquire()
    monkeypatch.undo()

    second = RunLock(lock_path)
    second.acquire()  # must not raise contention -- the failed lock was cleaned up
    second.release()


def test_default_lock_path_is_derived_only_from_settings_project_data_path(tmp_path):
    from market_intelligence.config.settings import Settings

    settings = Settings(project_data_path=tmp_path / "data", _env_file=tmp_path / "no.env")
    lock_path = default_lock_path(settings)
    assert (tmp_path / "data").resolve() in lock_path.resolve().parents
    assert lock_path.name == "orchestration.lock"
