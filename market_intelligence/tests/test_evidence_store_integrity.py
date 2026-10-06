"""Integrity verification, stop versus ordinary refusal, holdout read cases,
projection rebuild, sanitization and the offline boundary. Synthetic only."""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import REPO_ROOT
from market_intelligence.evidence.enums import SubjectType
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store import store as store_module
from market_intelligence.evidence_store.enums import (
    IntegrityFinding,
    StoreAuditEventType,
    StoreOperation,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.projections import rebuild_projections
from market_intelligence.evidence_store.reads import audit_read_items
from market_intelligence.evidence_store.store import Txn
from market_intelligence.evidence_store.store_io import connect, select_rows
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx

PACKAGE = Path(store_module.__file__).parent
SPY = "instrument:us_equity:SPY"


def _env(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    env.evidence_id, _ = fx.setup_registries(env)
    env.bar = f.bar_item()
    env.store.append_envelope(fx.envelope([env.bar], env.evidence_id))
    return env


def _verify(env):
    return verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS)


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


# --- Verification findings -----------------------------------------------------------------------


def test_clean_store_verifies_without_findings(tmp_path):
    report = _verify(_env(tmp_path))
    assert report.findings == []
    assert len(report.report_sha256()) == 64


def test_edited_record_is_detected(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_items SET record_json = record_json || ' ' WHERE item_id = ?",
            [env.bar.item_id],
        )
    assert IntegrityFinding.RECORD_HASH_MISMATCH in {x.finding for x in _verify(env).findings}


def test_deleted_row_breaks_the_commit_row_set(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute("DELETE FROM store_audit_events WHERE event_type = 'envelope_accepted'")
    report = _verify(env)
    assert report.has_stop()
    assert IntegrityFinding.COMMIT_CHAIN_MISMATCH in {x.finding for x in report.findings}


def test_edited_key_column_is_reported(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_items SET producer_id = 'other_producer' WHERE item_id = ?",
            [env.bar.item_id],
        )
    tokens = _verify(env).tokens()
    assert "row_hash_mismatch" in tokens


def test_projection_mismatch_is_not_a_stop_and_rebuilds(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute("DELETE FROM evidence_item_subjects")
    report = _verify(env)
    assert report.tokens() == {"projection_mismatch"} and not report.has_stop()
    rebuild_projections(env.store)
    assert _verify(env).findings == []
    assert env.store.state is StoreState.SERVING


# --- Holdout: persisted violation versus refusals ------------------------------------------------


def _smuggle_restricted_item(env):
    """Simulate corruption: a restricted item written past every guard."""
    restricted = f.bar_item(f.HOLDOUT_T0)

    def build(txn: Txn) -> None:
        txn.add("evidence_items", rec.item_columns(restricted, env.evidence_id))
        for table, columns in rec.item_projection_rows(restricted, txn.commit_seq):
            txn.project(table, columns)

    env.store.run_write(StoreWriterId.STORE_SERVICE, StoreOperation.RECORD_AUDIT_EVENTS, build)
    return restricted


def test_persisted_holdout_violation_is_an_integrity_stop(tmp_path):
    env = _env(tmp_path)
    _smuggle_restricted_item(env)
    assert IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION in {
        x.finding for x in _verify(env).findings
    }
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
        with pytest.raises(IntegrityStop) as excinfo:
            env.store.records_as_of(conn, env.clock(), registry)
    assert excinfo.value.finding is IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION
    assert env.store.state is StoreState.READ_REFUSED
    assert (
        _reason(
            env.store.append_envelope,
            fx.envelope(
                [f.bar_item(f.T0 - timedelta(minutes=5))], env.evidence_id, run_key="2" * 64
            ),
        )
        == "store_read_refused"
    )


def test_holdout_read_refusal_exposes_nothing_and_keeps_serving(tmp_path):
    env = _env(tmp_path)
    assert (
        _reason(
            audit_read_items,
            env.store,
            subject_id=SPY,
            subject_type=SubjectType.INSTRUMENT,
            first=date(2026, 10, 1),
            last=date(2026, 10, 31),
        )
        == "holdout_restricted"
    )
    with connect(env.store.database_path, read_only=True) as conn:
        event = select_rows(
            conn, "SELECT * FROM store_audit_events ORDER BY event_seq DESC LIMIT 1"
        )[0]
    assert event["event_type"] == StoreAuditEventType.HOLDOUT_READ_REFUSED.value
    assert event["subject_record_id"] is None
    assert env.store.state is StoreState.SERVING
    found = audit_read_items(
        env.store,
        subject_id=SPY,
        subject_type=SubjectType.INSTRUMENT,
        first=f.SESSION,
        last=f.SESSION,
    )
    assert [i.item_id for i in found] == [env.bar.item_id]


# --- Ordinary refusals never stop the store ------------------------------------------------------


def test_ordinary_refusals_leave_the_store_serving(tmp_path):
    env = _env(tmp_path)
    assert (
        _reason(
            env.store.append_envelope,
            fx.envelope([f.bar_item(f.HOLDOUT_T0)], env.evidence_id, run_key="3" * 64),
        )
        == "holdout_restricted"
    )
    assert (
        _reason(
            env.store.append_envelope,
            fx.envelope([f.bar_item()], "evr1_" + "0" * 64, run_key="4" * 64),
        )
        == "registry_version_mismatch"
    )
    assert env.store.state is StoreState.SERVING
    assert _verify(env).findings == []


def test_refusals_are_bounded_tokens(tmp_path):
    env = _env(tmp_path)
    with pytest.raises(StoreRefusal) as excinfo:
        env.store.append_envelope(fx.envelope([f.bar_item()], "evr1_" + "0" * 64, run_key="5" * 64))
    text = str(excinfo.value)
    assert text == "registry_version_mismatch"
    assert str(env.store.database_path) not in text and "SELECT" not in text


# --- Offline boundary ----------------------------------------------------------------------------

_FORBIDDEN_IMPORTS = (
    "httpx",
    "requests",
    "urllib.request",
    "socket",
    "openai",
    "anthropic",
    "market_intelligence.data_connectors",
    "market_intelligence.agents",
    "market_intelligence.llm",
    "market_intelligence.dashboard",
)


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_package_has_no_network_provider_model_or_consumer_imports(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith(_FORBIDDEN_IMPORTS), (module, name)


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_package_has_no_execution_or_notification_surface(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.ClassDef)}
    for name in names:
        lowered = name.lower()
        for fragment in (
            "order",
            "broker",
            "execute_trade",
            "position_size",
            "notify",
            "sms",
            "rank",
        ):
            assert fragment not in lowered, (module, name)


def test_no_real_registry_or_checkpoint_exists_in_the_repository():
    assert not (REPO_ROOT / "registries").exists()
    assert not list(REPO_ROOT.glob("**/store_checkpoint.json"))
