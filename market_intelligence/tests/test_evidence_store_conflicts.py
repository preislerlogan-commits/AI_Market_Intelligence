"""Accepted stricter storage requirements for conflicts: linear status chains and
the frozen registry-based severity derivation. Synthetic data only."""

from __future__ import annotations

import json
from datetime import timedelta

import duckdb
import pytest

from market_intelligence.evidence.contracts import (
    ConflictResolution,
    EvidenceConflictContent,
    seal_conflict,
)
from market_intelligence.evidence.enums import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    DetectionMethod,
    ResolutionKind,
)
from market_intelligence.evidence_store.enums import (
    ActivationReason,
    IntegrityFinding,
    RegistryKind,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.evidence_store.severity import (
    authorized_machine_decision_purposes,
    matched_requirement_ids,
)
from market_intelligence.evidence_store.store_io import connect, select_rows
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx

EVAL = f.T0 + timedelta(seconds=30)


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _conflict(
    items,
    *,
    severity=ConflictSeverity.INFORMATIONAL,
    status=ConflictStatus.UNRESOLVED,
    resolution=None,
    evaluated=EVAL,
    ctype=ConflictType.PROVIDER_DISAGREEMENT,
):
    return seal_conflict(
        EvidenceConflictContent(
            conflict_type=ctype,
            involved_item_ids=sorted(i.item_id for i in items),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=f.DETECTOR,
            detector_version="1.0.0",
            severity=severity,
            status=status,
            resolution=resolution,
            evaluated_as_of_utc=evaluated,
            detected_at_utc=evaluated + timedelta(seconds=1),
        )
    )


def _ack(
    prior, items, severity=ConflictSeverity.INFORMATIONAL, evaluated=EVAL + timedelta(seconds=5)
):
    return _conflict(
        items,
        severity=severity,
        status=ConflictStatus.ACKNOWLEDGED_UNRESOLVABLE,
        resolution=ConflictResolution(
            resolution_kind=ResolutionKind.MANUAL_ACKNOWLEDGEMENT,
            resolution_evidence_ids=[],
            supersedes_conflict_id=prior.conflict_id,
        ),
        evaluated=evaluated,
    )


def _env(tmp_path, registry=None):
    env = fx.make_env(tmp_path, production=True)
    env.evidence_id, _ = fx.setup_registries(env, registry)
    env.bars = [f.bar_item(), f.bar_item(f.T0 - timedelta(minutes=5))]
    env.store.append_envelope(fx.envelope(env.bars, env.evidence_id))
    env.clock.set(f.T0 + timedelta(minutes=10))
    return env


def _conflict_rows(env):
    with connect(env.store.database_path, read_only=True) as conn:
        return select_rows(conn, "SELECT * FROM evidence_conflicts ORDER BY commit_seq")


# --- Severity derivation -------------------------------------------------------------------------


def test_only_authorized_machine_decision_purposes_count():
    assert authorized_machine_decision_purposes(fx.no_machine_decision_registry()) == []
    purposes = authorized_machine_decision_purposes(fx.machine_decision_registry())
    assert [p.value for p in purposes] == ["live_market_state", "setup_detail"]
    bar = f.bar_item()
    assert matched_requirement_ids(fx.no_machine_decision_registry(), [bar]) == []
    assert matched_requirement_ids(fx.machine_decision_registry(), [bar]) == [
        "live_market_state:req.bars.spy",
        "setup_detail:req.bars.spy",
    ]


def test_today_no_conflict_is_required_and_severity_follows_type(tmp_path):
    env = _env(tmp_path)
    conflict = _conflict(env.bars)
    env.store.append_conflict(conflict)
    row = _conflict_rows(env)[0]
    assert row["involves_required_item"] is False
    assert json.loads(row["matched_requirement_ids"]) == []
    assert row["severity"] == "informational"
    assert row["severity_rule_version"] == "conflict-severity-rules-1"
    assert row["severity_registry_version_id"] == env.evidence_id
    # Bars stay stored and visible, unchanged.
    with connect(env.store.database_path, read_only=True) as conn:
        assert len(select_rows(conn, "SELECT * FROM evidence_items")) == 2


def test_stored_severity_must_equal_the_fixed_derivation(tmp_path):
    env = _env(tmp_path)
    wrong = _conflict(env.bars, severity=ConflictSeverity.CRITICAL)
    assert _reason(env.store.append_conflict, wrong) == "conflict_severity_mismatch"
    assert env.store.state is StoreState.SERVING


def test_machine_decision_registry_marks_required_items(tmp_path):
    env = _env(tmp_path, fx.machine_decision_registry())
    env.store.append_conflict(_conflict(env.bars, severity=ConflictSeverity.CRITICAL))
    row = _conflict_rows(env)[0]
    assert row["involves_required_item"] is True
    assert json.loads(row["matched_requirement_ids"]) == [
        "live_market_state:req.bars.spy",
        "setup_detail:req.bars.spy",
    ]
    report = verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS)
    assert not report.findings


def test_tampered_severity_wrapper_is_an_integrity_stop_on_verification(tmp_path):
    env = _env(tmp_path)
    conflict = _conflict(env.bars)
    env.store.append_conflict(conflict)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_conflicts SET matched_requirement_ids = ? WHERE conflict_id = ?",
            ['["live_market_state:req.bars.spy"]', conflict.conflict_id],
        )
    report = verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS)
    assert report.has_stop()
    assert IntegrityFinding.ROW_HASH_MISMATCH in {x.finding for x in report.findings}


def test_conflict_must_describe_the_past(tmp_path):
    env = _env(tmp_path)
    future = _conflict(env.bars, evaluated=env.clock() + timedelta(seconds=1))
    assert _reason(env.store.append_conflict, future) == "conflict_as_of_not_past"


def test_conflict_needs_stored_involved_items(tmp_path):
    env = _env(tmp_path)
    stranger = f.bar_item(f.T0 - timedelta(minutes=30))
    assert (
        _reason(env.store.append_conflict, _conflict([env.bars[0], stranger]))
        == "unknown_involved_item"
    )


# --- Linear chains -------------------------------------------------------------------------------


def test_identical_retry_is_idempotent_and_a_second_root_is_refused(tmp_path):
    env = _env(tmp_path)
    conflict = _conflict(env.bars)
    env.store.append_conflict(conflict)
    env.store.append_conflict(conflict)  # identical: no-op
    assert len(_conflict_rows(env)) == 1
    different = _conflict(env.bars, evaluated=EVAL + timedelta(seconds=1))
    assert _reason(env.store.append_conflict, different) == "conflict_chain_already_started"


def test_status_chain_is_linear(tmp_path):
    env = _env(tmp_path)
    root = _conflict(env.bars)
    env.store.append_conflict(root)
    first = _ack(root, env.bars)
    env.store.append_conflict(first)
    rows = _conflict_rows(env)
    assert rows[1]["supersedes_conflict_id"] == root.conflict_id
    # A second opinion must supersede the tip, never the original.
    branch = _ack(root, env.bars, evaluated=EVAL + timedelta(seconds=9))
    assert _reason(env.store.append_conflict, branch) == "conflict_status_not_tip"
    second = _ack(first, env.bars, evaluated=EVAL + timedelta(seconds=9))
    env.store.append_conflict(second)
    assert len(_conflict_rows(env)) == 3
    assert not verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS).findings


def test_manual_review_writer_may_only_acknowledge(tmp_path):
    env = _env(tmp_path)
    root = _conflict(env.bars)
    assert _reason(
        env.store.append_conflict, root, writer=StoreWriterId.MANUAL_CONFLICT_REVIEW
    ) == ("writer_not_permitted")
    env.store.append_conflict(root)
    env.store.append_conflict(_ack(root, env.bars), writer=StoreWriterId.MANUAL_CONFLICT_REVIEW)
    assert (
        _reason(env.store.append_conflict, root, writer=StoreWriterId.BUNDLE_BUILDER)
        == "writer_not_permitted"
    )


def test_stored_branch_is_detected_as_chain_ambiguity(tmp_path):
    env = _env(tmp_path)
    root = _conflict(env.bars)
    env.store.append_conflict(root)
    env.store.append_conflict(_ack(root, env.bars))
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_conflicts SET supersedes_conflict_id = NULL "
            "WHERE supersedes_conflict_id IS NOT NULL"
        )
    report = verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS)
    assert report.has_stop()


# --- Stale-at-birth ------------------------------------------------------------------------------


def test_unresolved_conflict_evaluated_before_a_registry_change_is_stale(tmp_path):
    env = _env(tmp_path)
    upgraded = fx.machine_decision_registry(registry_label="synthetic-test-md")
    upgraded_id = fx.register(env, RegistryKind.EVIDENCE_REGISTRY, upgraded)
    fx.activate(
        env, RegistryKind.EVIDENCE_REGISTRY, upgraded_id, reason=ActivationReason.VERSION_UPGRADE
    )
    env.clock.advance(minutes=1)
    # Evaluated under the first registry, recorded after the machine-decision change.
    stale = _conflict(env.bars)
    assert _reason(env.store.append_conflict, stale) == "conflict_evaluation_stale"
    fresh = _conflict(
        env.bars, severity=ConflictSeverity.CRITICAL, evaluated=env.clock() - timedelta(seconds=1)
    )
    env.store.append_conflict(fresh)
