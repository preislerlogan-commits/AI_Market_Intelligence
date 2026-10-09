"""Write paths, duplicates, atomicity, ordering, holdout writes and append-only
enforcement. Synthetic data, temporary databases and state directories only."""

from __future__ import annotations

import ast
import re
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store import store as store_module
from market_intelligence.evidence_store.checkpoint import CheckpointConfig
from market_intelligence.evidence_store.enums import CheckpointMode, StoreState
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.store import EvidenceStore, visible_through
from market_intelligence.evidence_store.store_io import (
    PROJECTION_TABLES,
    REAL_DATABASE_PATH,
    connect,
    refuse_real_database_path,
    select_rows,
)
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx

PACKAGE = Path(store_module.__file__).parent


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


@pytest.fixture
def env(tmp_path):
    environment = fx.make_env(tmp_path, production=True)
    evidence_id, _ = fx.setup_registries(environment)
    environment.evidence_id = evidence_id
    return environment


def _rows(env, sql, params=()):
    with connect(env.store.database_path, read_only=True) as conn:
        return select_rows(conn, sql, params)


def _events(env):
    return [
        r["event_type"]
        for r in _rows(env, "SELECT event_type FROM store_audit_events ORDER BY event_seq")
    ]


# --- Migration and initialization ----------------------------------------------------------------


def test_migration_0010_creates_every_store_table(env):
    tables = {
        r["table_name"] for r in _rows(env, "SELECT table_name FROM information_schema.tables")
    }
    from market_intelligence.evidence_store.store_io import AUTHORITATIVE_TABLES

    assert set(AUTHORITATIVE_TABLES) <= tables
    assert set(PROJECTION_TABLES) <= tables


def test_migration_constraints_refuse_bad_values(env):
    with duckdb.connect(str(env.store.database_path)) as conn:
        with pytest.raises(duckdb.Error):
            conn.execute(
                "INSERT INTO store_commits VALUES (99, now(), 'trading_bot', "
                "'append_envelope', 1, ?, ?, ?)",
                ["a" * 64, "b" * 64, "c" * 64],
            )
        with pytest.raises(duckdb.Error):  # revision 1 must not supersede
            conn.execute(
                "INSERT INTO evidence_items VALUES ('evi1_' || repeat('0', "
                "64), 'evidence-envelope-1', "
                "'confirmed_fact', 'p', 'v', 's', 'x', 'cfg_none', now(), "
                "NULL, 1, 'evi1_x', TRUE, 'r', "
                "'{}', repeat('a', 64), 1, now(), repeat('a', 64))"
            )
        with pytest.raises(duckdb.Error):  # malformed ID
            conn.execute("INSERT INTO setup_cards (card_id) VALUES ('not-a-card')")


def test_initialization_writes_commit_one_and_its_checkpoint(env):
    commits = _rows(env, "SELECT commit_seq, operation FROM store_commits ORDER BY commit_seq")
    assert commits[0] == {"commit_seq": 1, "operation": "initialize_store"}
    assert env.store.checkpoint_equals_tip()
    assert len(_rows(env, "SELECT * FROM store_instance")) == 1


def test_real_database_path_is_refused():
    with pytest.raises(StoreRefusal):
        refuse_real_database_path(REAL_DATABASE_PATH)
    with pytest.raises(StoreRefusal):
        refuse_real_database_path(REPO_ROOT / "data")
    with pytest.raises(StoreRefusal):
        with connect(REAL_DATABASE_PATH, read_only=True):
            pass


def test_store_refuses_real_project_data_directory(tmp_path):
    settings = Settings(_env_file=None, project_data_path=REPO_ROOT / "data")
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(StoreRefusal) as excinfo:
        EvidenceStore(
            settings=settings,
            checkpoint=CheckpointConfig(state, CheckpointMode.OFFLINE_DEVELOPMENT_UNAUTHENTICATED),
            payload_models=fx.PAYLOAD_MODELS,
        )
    assert excinfo.value.reason == "real_database_refused"


# --- Envelope appends ----------------------------------------------------------------------------


def test_envelope_append_stores_items_header_and_projections(env):
    bars = [f.bar_item(), f.bar_item(f.T0 - timedelta(minutes=5))]
    envelope = fx.envelope(bars, env.evidence_id)
    env.store.append_envelope(envelope)
    items = _rows(env, "SELECT item_id, commit_seq FROM evidence_items ORDER BY item_id")
    assert [r["item_id"] for r in items] == sorted(b.item_id for b in bars)
    assert len({r["commit_seq"] for r in items}) == 1
    assert len(_rows(env, "SELECT * FROM evidence_envelope_items")) == 2
    assert _events(env)[-1] == "envelope_accepted"
    assert env.store.checkpoint_equals_tip()
    assert not verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS).findings


def test_identical_duplicate_is_idempotent(env):
    envelope = fx.envelope([f.bar_item()], env.evidence_id)
    env.store.append_envelope(envelope)
    first = _rows(env, "SELECT item_id, recorded_at_utc, commit_seq FROM evidence_items")
    env.clock.advance(minutes=1)
    env.store.append_envelope(envelope)
    assert _rows(env, "SELECT item_id, recorded_at_utc, commit_seq FROM evidence_items") == first
    assert _events(env)[-1] == "append_duplicate_ignored"
    # The same item in a different run is not re-stored either.
    env.store.append_envelope(fx.envelope([f.bar_item()], env.evidence_id, run_key="2" * 64))
    assert _rows(env, "SELECT item_id, recorded_at_utc, commit_seq FROM evidence_items") == first


def test_same_identity_with_different_content_is_an_integrity_stop(env):
    bar = f.bar_item()
    env.store.append_envelope(fx.envelope([bar], env.evidence_id))
    forged = rec.canonical_text({**bar.model_dump(mode="json"), "payload_schema_id": "forged.v1"})
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_items SET record_json = ? WHERE item_id = ?", [forged, bar.item_id]
        )
    with pytest.raises(IntegrityStop):
        env.store.append_envelope(fx.envelope([bar], env.evidence_id, run_key="3" * 64))
    assert env.store.state is StoreState.READ_REFUSED


def test_failed_write_leaves_nothing_behind(env, monkeypatch):
    before = _rows(env, "SELECT count(*) AS n FROM store_commits")[0]["n"]

    def explode(*_args, **_kwargs):
        raise RuntimeError("injected")

    monkeypatch.setattr(rec, "envelope_columns", explode)
    with pytest.raises(RuntimeError):
        env.store.append_envelope(fx.envelope([f.bar_item()], env.evidence_id))
    assert _rows(env, "SELECT count(*) AS n FROM store_commits")[0]["n"] == before
    assert _rows(env, "SELECT count(*) AS n FROM evidence_items")[0]["n"] == 0
    assert _rows(env, "SELECT count(*) AS n FROM evidence_item_subjects")[0]["n"] == 0
    assert env.store.state is StoreState.SERVING


def test_clock_regression_is_refused_and_equal_times_are_ordered_by_sequence(env):
    env.store.append_envelope(fx.envelope([f.bar_item()], env.evidence_id))
    same_time = env.clock()
    env.store.append_envelope(
        fx.envelope([f.bar_item(f.T0 - timedelta(minutes=5))], env.evidence_id, run_key="4" * 64)
    )
    commits = _rows(
        env, "SELECT commit_seq, committed_at_utc FROM store_commits ORDER BY commit_seq"
    )
    assert commits[-1]["committed_at_utc"] == commits[-2]["committed_at_utc"] == same_time
    with connect(env.store.database_path, read_only=True) as conn:
        # Time visibility includes every commit sharing time T: a sequence prefix.
        assert visible_through(conn, same_time) == commits[-1]["commit_seq"]
    env.clock.set(same_time - timedelta(microseconds=1))
    assert (
        _reason(
            env.store.append_envelope,
            fx.envelope(
                [f.bar_item(f.T0 - timedelta(minutes=10))], env.evidence_id, run_key="5" * 64
            ),
        )
        == "store_clock_regressed"
    )
    assert env.store.state is StoreState.SERVING


def test_unknown_payload_schema_and_producer_are_refused(env):
    bar = f.bar_item()
    foreign = fx.envelope([bar], "evr1_" + "9" * 64)
    assert _reason(env.store.append_envelope, foreign) == "registry_version_mismatch"
    assert env.store.state is StoreState.SERVING
    assert _events(env)[-1] == "envelope_refused"


def test_missing_parent_is_refused(env):
    bar = f.bar_item()
    calc = f.calc_item([bar])
    assert (
        _reason(env.store.append_envelope, fx.envelope([calc], env.evidence_id, producer=f.CALC))
        == "missing_parent"
    )


def test_lineage_sees_stored_grandparents_across_envelopes(env):
    """An item whose parent cites its own stored parents is validated against
    its whole stored ancestry instead of being refused as ``missing_parent``."""
    bar = f.bar_item()
    env.store.append_envelope(fx.envelope([bar], env.evidence_id, run_key="6" * 64))
    calc = f.calc_item([bar])
    env.store.append_envelope(
        fx.envelope([calc], env.evidence_id, producer=f.CALC, run_key="7" * 64)
    )
    inference = f.inference_item([calc])
    env.store.append_envelope(
        fx.envelope([inference], env.evidence_id, producer=f.AGENT, run_key="8" * 64)
    )
    ids = {r["item_id"] for r in _rows(env, "SELECT item_id FROM evidence_items")}
    assert {bar.item_id, calc.item_id, inference.item_id} <= ids


# --- Holdout -------------------------------------------------------------------------------------


def test_holdout_write_is_refused_before_persistence_without_any_identifier(env):
    restricted = f.bar_item(f.HOLDOUT_T0)
    assert (
        _reason(env.store.append_envelope, fx.envelope([restricted], env.evidence_id))
        == "holdout_restricted"
    )
    assert _rows(env, "SELECT count(*) AS n FROM evidence_items")[0]["n"] == 0
    event = _rows(env, "SELECT * FROM store_audit_events ORDER BY event_seq")[-1]
    assert event["event_type"] == "holdout_write_refused"
    assert event["subject_record_id"] is None
    assert restricted.item_id not in str(_rows(env, "SELECT * FROM store_audit_events"))
    # An ordinary refusal: the store keeps serving reads and writes.
    assert env.store.state is StoreState.SERVING
    env.store.append_envelope(fx.envelope([f.bar_item()], env.evidence_id))


# --- Static append-only enforcement --------------------------------------------------------------

_FORBIDDEN_SQL = re.compile(
    r"\b(UPDATE|DELETE|REPLACE|UPSERT|ON\s+CONFLICT|MERGE|TRUNCATE|DROP|ALTER)\b"
)
_AUTHORITATIVE_MODULES = (
    "store.py",
    "store_io.py",
    "records.py",
    "recovery.py",
    "registry_service.py",
    "reads.py",
    "verification.py",
    "checkpoint.py",
    "registry_files.py",
    "severity.py",
    "contracts.py",
)


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            found.append("".join(v.value for v in node.values if isinstance(v, ast.Constant)))
    return found


@pytest.mark.parametrize("module", _AUTHORITATIVE_MODULES)
def test_repositories_contain_no_rewriting_sql(module):
    for text in _string_constants(PACKAGE / module):
        sql_like = re.search(r"\b(SELECT|INSERT|FROM)\b", text)
        if sql_like:
            assert not _FORBIDDEN_SQL.search(text), (module, text[:60])


@pytest.mark.parametrize("module", _AUTHORITATIVE_MODULES)
def test_repositories_expose_no_update_or_delete_method(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    names = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert not {
        n for n in names if re.match(r"^(update|delete|upsert|replace|truncate|prune|purge)", n)
    }


def test_projection_rebuild_deletes_only_projection_tables():
    source = (PACKAGE / "projections.py").read_text(encoding="utf-8")
    deletes = {t for t in _string_constants(PACKAGE / "projections.py") if "DELETE" in t}
    assert deletes == {"DELETE FROM "}
    assert "for table in PROJECTION_TABLES:" in source
    from market_intelligence.evidence_store.store_io import AUTHORITATIVE_TABLES

    assert not set(AUTHORITATIVE_TABLES) & set(PROJECTION_TABLES)


def test_migration_defines_no_triggers_or_rewrite_statements():
    sql = (
        REPO_ROOT / "market_intelligence/storage/migrations/0010_create_evidence_card_store.sql"
    ).read_text(encoding="utf-8")
    code = "\n".join(line for line in sql.splitlines() if not line.strip().startswith("--"))
    statements = [s.strip() for s in code.split(";") if s.strip()]
    assert statements
    for statement in statements:
        assert statement.startswith(("CREATE TABLE", "CREATE INDEX")), statement[:40]
        assert "TRIGGER" not in statement.upper()
