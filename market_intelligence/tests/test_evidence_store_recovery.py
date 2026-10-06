"""Exact backup restoration, ``checkpoint_reissue_required`` and restart behavior.
Temporary databases, backups and state directories only."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence_store import store as store_module
from market_intelligence.evidence_store.checkpoint import (
    read_checkpoint,
    sign_checkpoint,
    write_checkpoint,
)
from market_intelligence.evidence_store.contracts import StoreCheckpointContent
from market_intelligence.evidence_store.enums import IntegrityFinding, StoreState
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.recovery import create_backup, restore_backup
from market_intelligence.evidence_store.store_io import AUTHORITATIVE_TABLES, connect, select_rows
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx


def _env(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    env.evidence_id, _ = fx.setup_registries(env)
    return env


def _append(env, minutes, key):
    env.store.append_envelope(
        fx.envelope(
            [f.bar_item(f.T0 - timedelta(minutes=minutes))], env.evidence_id, run_key=key * 64
        )
    )


def _snapshot(path):
    with connect(path, read_only=True) as conn:
        return {
            table: select_rows(conn, f"SELECT * FROM {table} ORDER BY commit_seq")
            for table in AUTHORITATIVE_TABLES
        }


def _behind_checkpoint_scenario(tmp_path):
    env = _env(tmp_path)
    _append(env, 0, "1")
    backup = create_backup(env.store, tmp_path / "backup.duckdb")
    _append(env, 5, "2")  # lost after restoration
    _append(env, 10, "3")
    return env, backup


def _events(env):
    with connect(env.store.database_path, read_only=True) as conn:
        return select_rows(
            conn,
            "SELECT event_type, commit_seq, reference_commit_seq FROM "
            "store_audit_events ORDER BY event_seq",
        )


def test_restore_preserves_every_row_and_records_one_recovery_commit(tmp_path):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    old_checkpoint, _ = read_checkpoint(env.config)
    before = _snapshot(backup.backup_path)
    assert restore_backup(env.store, backup, authorization_ref=fx.AUTH) is StoreState.SERVING
    after = _snapshot(env.store.database_path)
    for table, rows in before.items():
        assert after[table][: len(rows)] == rows  # sequence, times, hashes, records: exact
    with connect(env.store.database_path, read_only=True) as conn:
        recovery = select_rows(conn, "SELECT * FROM store_recovery_events")[0]
        tip = select_rows(conn, "SELECT * FROM store_commits ORDER BY commit_seq DESC LIMIT 1")[0]
    assert recovery["commit_seq"] == tip["commit_seq"] == backup.high_water_commit_seq + 1
    assert recovery["superseded_checkpoint_commit_seq"] == old_checkpoint.commit_seq
    assert recovery["recovery_authorization_ref"] == fx.AUTH
    events = [e for e in _events(env) if e["commit_seq"] == tip["commit_seq"]]
    assert sorted(e["event_type"] for e in events) == [
        "checkpoint_reissue_required",
        "store_restored",
    ]
    assert all(e["reference_commit_seq"] == tip["commit_seq"] for e in events)
    assert "checkpoint_reissued" not in {e["event_type"] for e in _events(env)}
    assert env.store.checkpoint_equals_tip()
    assert not verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS).has_stop()


def test_lost_commits_are_never_recreated(tmp_path):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    with connect(env.store.database_path, read_only=True) as conn:
        lost = {
            r["commit_digest"]
            for r in select_rows(
                conn,
                "SELECT * FROM store_commits WHERE commit_seq > ?",
                [backup.high_water_commit_seq],
            )
        }
    restore_backup(env.store, backup, authorization_ref=fx.AUTH)
    with connect(env.store.database_path, read_only=True) as conn:
        digests = {r["commit_digest"] for r in select_rows(conn, "SELECT * FROM store_commits")}
    assert not lost & digests
    _append(env, 5, "2")  # producers may emit again: new knowledge at a new commit
    with connect(env.store.database_path, read_only=True) as conn:
        newest = select_rows(conn, "SELECT * FROM store_commits ORDER BY commit_seq DESC LIMIT 1")[
            0
        ]
    assert newest["committed_at_utc"] >= env.clock() - timedelta(seconds=1)


def test_failed_checkpoint_after_recovery_is_completed_on_restart(tmp_path, monkeypatch):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    real = store_module.write_checkpoint
    monkeypatch.setattr(
        store_module,
        "write_checkpoint",
        lambda *_: (_ for _ in ()).throw(StoreRefusal("checkpoint_write_failed")),
    )
    with pytest.raises(StoreRefusal) as excinfo:
        restore_backup(env.store, backup, authorization_ref=fx.AUTH)
    assert excinfo.value.reason == "workflow_incomplete"
    assert env.store.state is StoreState.RECOVERY_IN_PROGRESS
    with pytest.raises(StoreRefusal) as excinfo:  # no unrelated writes
        _append(env, 20, "4")
    assert excinfo.value.reason == "recovery_in_progress"
    monkeypatch.setattr(store_module, "write_checkpoint", real)
    store = fx.reopen(env)
    assert store.state is StoreState.SERVING and store.checkpoint_equals_tip()
    with connect(env.store.database_path, read_only=True) as conn:
        assert len(select_rows(conn, "SELECT * FROM store_recovery_events")) == 1
    assert sum(e["event_type"] == "checkpoint_reissue_required" for e in _events(env)) == 1


def test_existing_matching_checkpoint_completes_recovery_without_a_write(tmp_path, monkeypatch):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    real = store_module.write_checkpoint
    monkeypatch.setattr(
        store_module,
        "write_checkpoint",
        lambda *_: (_ for _ in ()).throw(StoreRefusal("checkpoint_write_failed")),
    )
    with pytest.raises(StoreRefusal):
        restore_backup(env.store, backup, authorization_ref=fx.AUTH)
    monkeypatch.setattr(store_module, "write_checkpoint", real)
    with connect(env.store.database_path, read_only=True) as conn:
        tip = select_rows(conn, "SELECT * FROM store_commits ORDER BY commit_seq DESC LIMIT 1")[0]
    env.store._write_checkpoint_for(tip["commit_seq"], tip["commit_digest"])
    store = fx.reopen(env)
    assert store.state is StoreState.SERVING
    with connect(env.store.database_path, read_only=True) as conn:
        assert (
            select_rows(conn, "SELECT max(commit_seq) AS n FROM store_commits")[0]["n"]
            == tip["commit_seq"]
        )


def test_checkpoint_matching_neither_side_of_recovery_is_an_integrity_stop(tmp_path, monkeypatch):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    monkeypatch.setattr(
        store_module,
        "write_checkpoint",
        lambda *_: (_ for _ in ()).throw(StoreRefusal("checkpoint_write_failed")),
    )
    with pytest.raises(StoreRefusal):
        restore_backup(env.store, backup, authorization_ref=fx.AUTH)
    monkeypatch.undo()
    checkpoint, _ = read_checkpoint(env.config)
    stray = sign_checkpoint(
        StoreCheckpointContent(
            store_instance_id=checkpoint.store_instance_id,
            commit_seq=2,
            commit_digest=checkpoint.commit_digest,
            created_at_utc=checkpoint.created_at_utc,
        ),
        env.config,
    )
    write_checkpoint(env.config, stray)
    with pytest.raises(IntegrityStop) as excinfo:
        fx.reopen(env)
    assert excinfo.value.finding is IntegrityFinding.CHECKPOINT_RECOVERY_MISMATCH
    assert env.store.state is StoreState.READ_REFUSED


def test_restore_requires_a_bounded_authorization_and_a_verified_backup(tmp_path):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    with pytest.raises(ValidationError):
        restore_backup(env.store, backup, authorization_ref="please-restore")
    backup.backup_path.write_bytes(backup.backup_path.read_bytes()[:-10] + b"0" * 10)
    with pytest.raises(StoreRefusal) as excinfo:
        restore_backup(env.store, backup, authorization_ref=fx.AUTH)
    assert excinfo.value.reason == "backup_not_verified"


def test_without_recovery_a_behind_database_stays_read_refused(tmp_path):
    env, backup = _behind_checkpoint_scenario(tmp_path)
    import shutil

    shutil.copyfile(backup.backup_path, env.store.database_path)
    with pytest.raises(IntegrityStop):
        fx.reopen(env)
    assert env.store.state is StoreState.READ_REFUSED
