"""The full authoritative scan gates every startup, reconciliation and recovery
before the store serves. Synthetic data, temporary databases and temporary
checkpoint directories only."""

from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest

from market_intelligence.evidence.enums import SubjectType
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store import store as store_module
from market_intelligence.evidence_store import verification
from market_intelligence.evidence_store.checkpoint import (
    read_checkpoint,
    sign_checkpoint,
    write_checkpoint,
)
from market_intelligence.evidence_store.contracts import StoreCheckpointContent
from market_intelligence.evidence_store.enums import (
    IntegrityFinding,
    StoreOperation,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.reads import audit_read_items
from market_intelligence.evidence_store.recovery import create_backup, restore_backup
from market_intelligence.evidence_store.severity import derive_conflict_severity
from market_intelligence.evidence_store.store import Txn, read_tip, verify_commit_chain, verify_row
from market_intelligence.evidence_store.store_io import connect, insert_row, select_rows
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx
from market_intelligence.tests.setup_card_fixtures import AS_OF
from market_intelligence.tests.test_evidence_store_bundles_cards import BUILDER, _card, _seed
from market_intelligence.tests.test_evidence_store_bundles_cards import _bundle as card_bundle
from market_intelligence.tests.test_evidence_store_bundles_cards import _env as card_env
from market_intelligence.tests.test_evidence_store_conflicts import _conflict
from market_intelligence.tests.test_evidence_store_integrity import _smuggle_restricted_item

SPY = "instrument:us_equity:SPY"


def _env(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    env.evidence_id, _ = fx.setup_registries(env)
    env.bar = f.bar_item()
    env.store.append_envelope(fx.envelope([env.bar], env.evidence_id))
    return env


def _stop_on_restart(env) -> IntegrityFinding:
    with pytest.raises(IntegrityStop) as excinfo:
        fx.reopen(env)
    assert env.store.state is StoreState.READ_REFUSED
    assert env.store.verified is None
    return excinfo.value.finding


def _smuggle(env, table: str, columns: dict) -> None:
    """Simulate corruption: a hash-consistent row written past every guard."""

    def build(txn: Txn) -> None:
        txn.add(table, columns)

    env.store.run_write(StoreWriterId.STORE_SERVICE, StoreOperation.RECORD_AUDIT_EVENTS, build)


def _unbind_a_row(env) -> None:
    """Insert a copy of an audit row with its own valid row hash but no commit
    that counts it: only the row-to-commit binding is broken."""
    with duckdb.connect(str(env.store.database_path)) as conn:
        row = select_rows(conn, "SELECT * FROM store_audit_events ORDER BY event_seq LIMIT 1")[0]
        top = conn.execute("SELECT max(event_seq) FROM store_audit_events").fetchone()[0]
        row.pop("row_sha256")
        row["event_seq"] = int(top) + 1
        insert_row(conn, "store_audit_events", row)


def _checkpoint_and_chain_are_valid(env) -> None:
    with connect(env.store.database_path, read_only=True) as conn:
        verify_commit_chain(conn)
        assert len(select_rows(conn, "SELECT * FROM store_instance")) == 1
    assert env.store.checkpoint_equals_tip()


def _event_types(env) -> list[str]:
    with connect(env.store.database_path, read_only=True) as conn:
        return [
            r["event_type"]
            for r in select_rows(
                conn, "SELECT event_type FROM store_audit_events ORDER BY event_seq"
            )
        ]


@pytest.fixture
def scans(monkeypatch):
    """Count full scans and the database tip each one saw."""
    seen: list[int] = []
    real = verification.verify_database

    def counting(path, **kwargs):
        report = real(path, **kwargs)
        seen.append(report.high_water_commit_seq)
        return report

    monkeypatch.setattr(verification, "verify_database", counting)
    return seen


# --- Startup -------------------------------------------------------------------------------------


def test_valid_startup_reaches_serving_with_a_verified_proof(tmp_path, scans):
    env = _env(tmp_path)
    scans.clear()  # the fixture's own initialization scan
    store = fx.reopen(env)
    assert store.state is StoreState.SERVING
    with connect(store.database_path, read_only=True) as conn:
        tip = read_tip(conn)
    assert store.verified is not None
    assert store.verified.high_water_commit_seq == tip.commit_seq
    assert store.verified.non_stop_findings == ()
    assert len(store.verified.report_sha256) == 64
    assert scans == [tip.commit_seq]


def test_corrupted_row_hash_blocks_startup(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_items SET producer_id = 'other_producer' WHERE item_id = ?",
            [env.bar.item_id],
        )
    assert _stop_on_restart(env) is IntegrityFinding.ROW_HASH_MISMATCH


def test_altered_canonical_record_blocks_startup(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_items SET record_json = record_json || ' ' WHERE item_id = ?",
            [env.bar.item_id],
        )
    assert _stop_on_restart(env) is IntegrityFinding.RECORD_HASH_MISMATCH


def test_broken_row_to_commit_binding_blocks_startup(tmp_path):
    env = _env(tmp_path)
    _unbind_a_row(env)
    with connect(env.store.database_path, read_only=True) as conn:
        for row in select_rows(conn, "SELECT * FROM store_audit_events"):
            verify_row("store_audit_events", row)  # every row hash is still valid
    assert _stop_on_restart(env) is IntegrityFinding.COMMIT_CHAIN_MISMATCH


def test_valid_checkpoint_and_commit_chain_do_not_hide_row_corruption(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE store_audit_events SET reason_code = 'altered' "
            "WHERE event_type = 'envelope_accepted'"
        )
    _checkpoint_and_chain_are_valid(env)  # everything the old startup checked passes
    assert _stop_on_restart(env) is IntegrityFinding.ROW_HASH_MISMATCH


def test_invalid_conflict_chain_blocks_startup(tmp_path):
    env = _env(tmp_path)
    other = f.bar_item(f.T0 - timedelta(minutes=5))
    env.store.append_envelope(fx.envelope([other], env.evidence_id, run_key="2" * 64))
    env.clock.set(f.T0 + timedelta(minutes=10))
    involved = [env.bar, other]
    root = _conflict(involved)
    env.store.append_conflict(root)
    second_root = _conflict(involved, evaluated=root.evaluated_as_of_utc + timedelta(seconds=1))
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
    derivation = derive_conflict_severity(second_root, involved, registry)
    _smuggle(
        env, "evidence_conflicts", rec.conflict_columns(second_root, derivation, env.evidence_id)
    )
    _checkpoint_and_chain_are_valid(env)
    assert _stop_on_restart(env) is IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY


def test_invalid_card_chain_blocks_startup(tmp_path):
    env = card_env(tmp_path)
    items = _seed(env)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = card_bundle(env, AS_OF)
    env.store.append_bundle(bundle, BUILDER)
    env.store.append_card(_card(env, bundle, items))
    second_root = _card(env, bundle, items, context_ids=[])
    _smuggle(env, "setup_cards", rec.card_columns(second_root, env.setup_id))
    _checkpoint_and_chain_are_valid(env)
    assert _stop_on_restart(env) is IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY


def test_missing_recorded_registry_version_blocks_startup(tmp_path):
    env = _env(tmp_path)
    orphan = f.bar_item(f.T0 - timedelta(minutes=30))
    _smuggle(env, "evidence_items", rec.item_columns(orphan, "evr1_" + "0" * 64))
    _checkpoint_and_chain_are_valid(env)
    assert _stop_on_restart(env) is IntegrityFinding.INVALID_REGISTRY_ACTIVATION


def test_persisted_holdout_violation_blocks_startup(tmp_path):
    env = _env(tmp_path)
    _smuggle_restricted_item(env)
    _checkpoint_and_chain_are_valid(env)
    assert _stop_on_restart(env) is IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION


def test_a_store_that_failed_its_scan_serves_nothing(tmp_path):
    env = _env(tmp_path)
    _unbind_a_row(env)
    _stop_on_restart(env)
    for attempt in (
        lambda: env.store.append_envelope(
            fx.envelope(
                [f.bar_item(f.T0 - timedelta(minutes=9))], env.evidence_id, run_key="9" * 64
            )
        ),
        lambda: audit_read_items(
            env.store,
            subject_id=SPY,
            subject_type=SubjectType.INSTRUMENT,
            first=f.SESSION,
            last=f.SESSION,
        ),
        env.store.reconcile,
    ):
        with pytest.raises(StoreRefusal) as excinfo:
            attempt()
        assert excinfo.value.reason == "store_read_refused"


def test_serving_requires_the_proof(tmp_path):
    env = _env(tmp_path)
    env.store.verified = None  # as if serving had been reached without a scan
    with pytest.raises(StoreRefusal) as excinfo:
        audit_read_items(
            env.store,
            subject_id=SPY,
            subject_type=SubjectType.INSTRUMENT,
            first=f.SESSION,
            last=f.SESSION,
        )
    assert excinfo.value.reason == "checkpoint_not_confirmed"


# --- Reconciliation ------------------------------------------------------------------------------


def _rewind_checkpoint(env) -> None:
    """The database is one commit ahead of the checkpoint, as after a crash
    between a commit and its checkpoint advancement."""
    checkpoint, _ = read_checkpoint(env.config)
    with connect(env.store.database_path, read_only=True) as conn:
        prior = select_rows(
            conn,
            "SELECT commit_seq, commit_digest FROM store_commits WHERE commit_seq = ?",
            [checkpoint.commit_seq - 1],
        )[0]
    write_checkpoint(
        env.config,
        sign_checkpoint(
            StoreCheckpointContent(
                store_instance_id=checkpoint.store_instance_id,
                commit_seq=prior["commit_seq"],
                commit_digest=prior["commit_digest"],
                created_at_utc=checkpoint.created_at_utc,
            ),
            env.config,
        ),
    )


def test_startup_reconciliation_scans_before_writing_and_before_serving(tmp_path, scans):
    env = _env(tmp_path)
    scans.clear()  # the fixture's own initialization scan
    _rewind_checkpoint(env)
    with connect(env.store.database_path, read_only=True) as conn:
        tip = read_tip(conn).commit_seq
    store = fx.reopen(env)
    assert store.state is StoreState.SERVING
    assert scans == [tip, tip + 1]  # before the reconciled event, then after it
    assert _event_types(env).count("checkpoint_reconciled") == 1
    assert store.verified.high_water_commit_seq == tip + 1


def test_reconciliation_cannot_resume_service_before_the_scan_succeeds(tmp_path):
    env = _env(tmp_path)
    _rewind_checkpoint(env)
    _unbind_a_row(env)
    before, _ = read_checkpoint(env.config)
    assert _stop_on_restart(env) is IntegrityFinding.COMMIT_CHAIN_MISMATCH
    assert "checkpoint_reconciled" not in _event_types(env)  # nothing was written
    after, _ = read_checkpoint(env.config)
    assert after == before  # the checkpoint was not advanced over the corruption


def test_operator_reconciliation_rescans_before_serving(tmp_path):
    env = _env(tmp_path)
    env.store.state = StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
    _unbind_a_row(env)
    with pytest.raises(IntegrityStop) as excinfo:
        env.store.reconcile()
    assert excinfo.value.finding is IntegrityFinding.COMMIT_CHAIN_MISMATCH
    assert env.store.state is StoreState.READ_REFUSED
    assert "checkpoint_reconciled" not in _event_types(env)


# --- Recovery ------------------------------------------------------------------------------------


def _interrupted_recovery(env, tmp_path, monkeypatch):
    """A verified restore whose checkpoint reissue failed: the recovery commit
    exists, the old checkpoint is still on disk."""
    backup = create_backup(env.store, tmp_path / "backup.duckdb")
    env.store.append_envelope(
        fx.envelope([f.bar_item(f.T0 - timedelta(minutes=5))], env.evidence_id, run_key="2" * 64)
    )
    real = store_module.write_checkpoint
    monkeypatch.setattr(
        store_module,
        "write_checkpoint",
        lambda *_: (_ for _ in ()).throw(StoreRefusal("checkpoint_write_failed")),
    )
    with pytest.raises(StoreRefusal):
        restore_backup(env.store, backup, authorization_ref=fx.AUTH)
    monkeypatch.setattr(store_module, "write_checkpoint", real)
    assert env.store.state is StoreState.RECOVERY_IN_PROGRESS


def test_recovery_restart_cannot_resume_before_the_scan_succeeds(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _interrupted_recovery(env, tmp_path, monkeypatch)
    superseded, _ = read_checkpoint(env.config)
    _unbind_a_row(env)
    assert _stop_on_restart(env) is IntegrityFinding.COMMIT_CHAIN_MISMATCH
    reissued, _ = read_checkpoint(env.config)
    assert reissued == superseded  # never reissued over an unverified store
    types = _event_types(env)
    assert types.count("store_restored") == 1 and types.count("checkpoint_reissue_required") == 1
    assert "checkpoint_reissued" not in types


def test_completed_recovery_is_rescanned_on_restart(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _interrupted_recovery(env, tmp_path, monkeypatch)
    with connect(env.store.database_path, read_only=True) as conn:
        tip = read_tip(conn)
    env.store._write_checkpoint_for(tip.commit_seq, tip.commit_digest)
    _unbind_a_row(env)
    assert _stop_on_restart(env) is IntegrityFinding.COMMIT_CHAIN_MISMATCH


def test_clean_recovery_restart_resumes_after_one_scan(tmp_path, monkeypatch, scans):
    env = _env(tmp_path)
    _interrupted_recovery(env, tmp_path, monkeypatch)
    scans.clear()
    store = fx.reopen(env)
    assert store.state is StoreState.SERVING and store.checkpoint_equals_tip()
    assert len(scans) == 1
    assert _event_types(env).count("checkpoint_reissue_required") == 1


# --- Once per startup; read-time validation stays ------------------------------------------------


def test_full_scan_runs_once_per_startup_not_per_query(tmp_path, scans):
    env = _env(tmp_path)
    scans.clear()  # the fixture's own initialization scan
    store = fx.reopen(env)
    assert len(scans) == 1
    for _ in range(3):
        found = audit_read_items(
            store,
            subject_id=SPY,
            subject_type=SubjectType.INSTRUMENT,
            first=f.SESSION,
            last=f.SESSION,
        )
        assert [i.item_id for i in found] == [env.bar.item_id]
        with store.read_connection() as conn:
            registry = store.load_registry(conn, env.evidence_id)
            store.records_as_of(conn, env.clock(), registry)
    assert len(scans) == 1
    fx.reopen(env)  # a new startup scans again
    assert len(scans) == 2


def test_read_time_validation_remains_active_after_startup(tmp_path, scans):
    env = _env(tmp_path)
    scans.clear()  # the fixture's own initialization scan
    store = fx.reopen(env)
    with duckdb.connect(str(store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_items SET record_json = record_json || ' ' WHERE item_id = ?",
            [env.bar.item_id],
        )
    with pytest.raises(IntegrityStop) as excinfo:
        audit_read_items(
            store,
            subject_id=SPY,
            subject_type=SubjectType.INSTRUMENT,
            first=f.SESSION,
            last=f.SESSION,
        )
    assert excinfo.value.finding is IntegrityFinding.RECORD_HASH_MISMATCH
    assert len(scans) == 1  # caught by the read itself, not by a rescan
