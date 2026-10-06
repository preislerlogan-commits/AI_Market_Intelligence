"""Startup finding classification (implementation-review decision): a
key-column or bundle-reproduction mismatch is an integrity stop; a projection
mismatch is rebuilt and reverified before the store may serve. Synthetic data,
temporary databases and temporary checkpoint directories only."""

from __future__ import annotations

import threading
from datetime import timedelta
from types import SimpleNamespace

import duckdb
import pytest

from market_intelligence.evidence.bundle import build_bundle
from market_intelligence.evidence.enums import BundlePurpose, SubjectType
from market_intelligence.evidence_store import projections
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.contracts import BundleBuilderIdentity
from market_intelligence.evidence_store.enums import (
    IntegrityFinding,
    StoreOperation,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.projections import (
    rebuild_projection_tables,
    rebuild_projections,
)
from market_intelligence.evidence_store.reads import audit_read_items
from market_intelligence.evidence_store.store import EvidenceStore, Txn, VerifiedStartup
from market_intelligence.evidence_store.store_io import (
    AUTHORITATIVE_TABLES,
    PROJECTION_TABLES,
    connect,
    select_rows,
)
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx
from market_intelligence.tests.test_evidence_store_startup_verification import _rewind_checkpoint

SPY = "instrument:us_equity:SPY"
BUILDER = BundleBuilderIdentity(
    builder_version="1.0.0",
    builder_code_commit_sha="d" * 40,
    builder_configuration_identity="cfg_none",
)


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


def _read(store):
    return audit_read_items(
        store,
        subject_id=SPY,
        subject_type=SubjectType.INSTRUMENT,
        first=f.SESSION,
        last=f.SESSION,
    )


def _authoritative(env):
    with connect(env.store.database_path, read_only=True) as conn:
        return {
            table: select_rows(conn, f"SELECT * FROM {table} ORDER BY commit_seq")
            for table in AUTHORITATIVE_TABLES
        }


def _drop_projection(env) -> None:
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute("DELETE FROM evidence_item_subjects")


@pytest.fixture
def rebuilds(monkeypatch):
    """Record each startup rebuild and what the store looked like during it."""
    calls: list[dict] = []
    real = projections.rebuild_projection_tables

    def spy(store, tables):
        try:
            _read(store)
            read = "served"
        except StoreRefusal as refusal:
            read = refusal.reason
        calls.append(
            {
                "tables": tuple(tables),
                "state": store.state,
                "verified": store.verified,
                "read": read,
            }
        )
        return real(store, tables)

    monkeypatch.setattr(projections, "rebuild_projection_tables", spy)
    return calls


# --- Key-column mismatch: a stop ------------------------------------------------------------------


def _smuggle_misfiled_item(env):
    """A hash-consistent row whose copied key column disagrees with its record."""
    item = f.bar_item(f.T0 - timedelta(minutes=30))

    def build(txn: Txn) -> None:
        columns = rec.item_columns(item, env.evidence_id)
        columns["producer_id"] = "other_producer"
        txn.add("evidence_items", columns)
        for table, values in rec.item_projection_rows(item, txn.commit_seq):
            txn.project(table, values)

    env.store.run_write(StoreWriterId.STORE_SERVICE, StoreOperation.RECORD_AUDIT_EVENTS, build)


def test_key_column_mismatch_blocks_startup(tmp_path, rebuilds):
    env = _env(tmp_path)
    _smuggle_misfiled_item(env)
    assert _stop_on_restart(env) is IntegrityFinding.KEY_COLUMN_MISMATCH
    assert rebuilds == []  # a stop is never routed to projection repair


def test_key_column_mismatch_is_not_cleared_by_projection_rebuilding(tmp_path):
    env = _env(tmp_path)
    _smuggle_misfiled_item(env)
    _stop_on_restart(env)
    assert _read_refused(rebuild_projections, env.store)
    assert _read_refused(rebuild_projection_tables, env.store, PROJECTION_TABLES)
    # Even a rebuild of every projection on an unopened store leaves the row wrong.
    rebuild_projection_tables(fx.reopen(env, open_store=False), PROJECTION_TABLES)
    assert _stop_on_restart(env) is IntegrityFinding.KEY_COLUMN_MISMATCH


def _read_refused(fn, *args) -> bool:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args)
    return excinfo.value.reason == "store_read_refused"


# --- Bundle-reproduction mismatch: a stop ---------------------------------------------------------


def _store_bundle(env):
    env.clock.set(f.T0 + timedelta(minutes=5))
    as_of = f.T0 + timedelta(minutes=1)
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
        items, conflicts, _ = env.store.records_as_of(conn, as_of, registry)
    q = f.query()
    bundle = build_bundle(
        registry=registry,
        purpose=BundlePurpose.LIVE_MARKET_STATE,
        as_of=as_of,
        consumer_context=f.context(q),
        query=q,
        recorded_items=items,
        recorded_conflicts=conflicts,
        built_at=as_of + timedelta(seconds=1),
    )
    env.store.append_bundle(bundle, BUILDER)
    return bundle


def test_bundle_reproduction_mismatch_blocks_startup(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _store_bundle(env)
    real = EvidenceStore.rebuild_bundle

    def different_builder(self, conn, bundle):
        _, n_t, extra = real(self, conn, bundle)
        return SimpleNamespace(bundle_id="evb1_" + "0" * 64), n_t, extra

    monkeypatch.setattr(EvidenceStore, "rebuild_bundle", different_builder)
    assert _stop_on_restart(env) is IntegrityFinding.BUNDLE_REPRODUCTION_MISMATCH


def test_an_unavailable_historical_builder_does_not_permit_serving(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _store_bundle(env)

    def unavailable(self, conn, bundle):
        raise StoreRefusal("bundle_not_reproducible")

    monkeypatch.setattr(EvidenceStore, "rebuild_bundle", unavailable)
    assert _stop_on_restart(env) is IntegrityFinding.BUNDLE_REPRODUCTION_MISMATCH
    monkeypatch.undo()  # the historical builder is available again
    store = fx.reopen(env)  # a separate startup, after review
    assert store.state is StoreState.SERVING
    assert store.verified.non_stop_findings == ()


# --- Projection mismatch: rebuilt and reverified before serving -----------------------------------


def test_projection_mismatch_blocks_service_while_rebuilding(tmp_path, rebuilds):
    env = _env(tmp_path)
    _drop_projection(env)
    fx.reopen(env)
    assert len(rebuilds) == 1
    during = rebuilds[0]
    assert during["state"] is not StoreState.SERVING
    assert during["verified"] is None
    assert during["read"] != "served"


def test_successful_rebuild_and_reverification_permit_startup(tmp_path, rebuilds):
    env = _env(tmp_path)
    _drop_projection(env)
    store = fx.reopen(env)
    assert store.state is StoreState.SERVING
    assert rebuilds[0]["tables"] == ("evidence_item_subjects",)  # only the affected one
    assert [i.item_id for i in _read(store)] == [env.bar.item_id]
    assert verify_database(store.database_path, payload_models=fx.PAYLOAD_MODELS).findings == []


def test_proof_records_the_repaired_projection(tmp_path):
    env = _env(tmp_path)
    _drop_projection(env)
    proof = fx.reopen(env).verified
    assert proof.non_stop_findings == ("projection_mismatch",)
    assert proof.rebuilt_projections == ("evidence_item_subjects",)
    clean = verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS)
    assert proof.report_sha256 == clean.report_sha256()  # the reverification, not the first scan


def test_failed_projection_rebuild_leaves_the_store_read_refused(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _drop_projection(env)

    def failing(store, tables):
        raise duckdb.IOException("synthetic write failure")

    monkeypatch.setattr(projections, "rebuild_projection_tables", failing)
    assert _stop_on_restart(env) is IntegrityFinding.PROJECTION_MISMATCH


def test_failed_projection_reverification_leaves_the_store_read_refused(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _drop_projection(env)
    monkeypatch.setattr(projections, "rebuild_projection_tables", lambda store, tables: None)
    assert _stop_on_restart(env) is IntegrityFinding.PROJECTION_MISMATCH


def test_projection_recovery_never_updates_or_deletes_an_authoritative_row(tmp_path):
    env = _env(tmp_path)
    _drop_projection(env)
    before = _authoritative(env)
    fx.reopen(env)
    assert _authoritative(env) == before  # every row, hash and commit unchanged


def test_proof_cannot_carry_an_unresolved_finding():
    for findings, rebuilt in (
        (("key_column_mismatch",), ()),
        (("bundle_reproduction_mismatch",), ()),
        (("projection_mismatch",), ()),  # not shown to be rebuilt
        ((), ("evidence_item_subjects",)),
    ):
        with pytest.raises(ValueError):
            VerifiedStartup(1, "0" * 64, findings, rebuilt)


# --- The re-entrant writer lock belongs to one thread -------------------------------------------


def test_another_thread_cannot_enter_the_held_writer_lock(tmp_path):
    env = _env(tmp_path)
    store = env.store
    outcome: list[str] = []

    def intruder():
        try:
            with store.writer_lock():  # the same store instance, another thread
                outcome.append("entered")
        except StoreRefusal as refusal:
            outcome.append(refusal.reason)

    with store.writer_lock():
        thread = threading.Thread(target=intruder)
        thread.start()
        thread.join()
        assert outcome == ["store_writer_locked"]
        assert store._lock_depth == 1 and store._lock_owner == threading.get_ident()
        assert store._lock_path.exists()
    assert not store._lock_path.exists()


def test_nested_lock_keeps_the_file_until_the_outermost_exit_even_on_errors(tmp_path):
    store = _env(tmp_path).store
    with store.writer_lock():
        with pytest.raises(RuntimeError):
            with store.writer_lock():
                assert store._lock_depth == 2
                raise RuntimeError("inner failure")
        assert store._lock_depth == 1 and store._lock_path.exists()
    assert store._lock_depth == 0 and store._lock_owner is None
    assert not store._lock_path.exists()
    with pytest.raises(RuntimeError):
        with store.writer_lock():
            raise RuntimeError("outer failure")
    assert not store._lock_path.exists()
    with store.writer_lock():  # released cleanly: acquirable again
        pass


def test_projection_repair_nested_in_startup_reconciliation(tmp_path, rebuilds):
    env = _env(tmp_path)
    _rewind_checkpoint(env)
    _drop_projection(env)
    store = fx.reopen(env)  # reconciliation holds the lock while it repairs
    assert store.state is StoreState.SERVING
    assert rebuilds and rebuilds[0]["tables"] == ("evidence_item_subjects",)
    assert store.verified.rebuilt_projections == ("evidence_item_subjects",)
    assert not store._lock_path.exists()
