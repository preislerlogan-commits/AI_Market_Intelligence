"""Registry registration and activation, including history-wide price-declaration
immutability and the unresolved-conflict activation guard. Synthetic only."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from market_intelligence.evidence.contracts import EvidenceConflictContent, seal_conflict
from market_intelligence.evidence.enums import (
    BundlePurpose,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    DetectionMethod,
)
from market_intelligence.evidence.registry import PayloadSchemaEntry, SelectionRule
from market_intelligence.evidence_store.enums import ActivationReason, RegistryKind
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.evidence_store.registry_service import (
    ActivationRequest,
    execute_activation,
    execute_registration,
    plan_activation,
    plan_registration,
)
from market_intelligence.evidence_store.severity import authorized_machine_decision_purposes
from market_intelligence.evidence_store.store_io import connect, select_rows
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx

EVID = RegistryKind.EVIDENCE_REGISTRY


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _request(version, reason=ActivationReason.VERSION_UPGRADE, effective=None, kind=EVID):
    return ActivationRequest(kind, version, reason, effective, fx.AUTH)


def _activations(env):
    with connect(env.store.database_path, read_only=True) as conn:
        return select_rows(conn, "SELECT * FROM registry_activations ORDER BY commit_seq")


def _in_force(env, at):
    with env.store.read_connection() as conn:
        return env.store.in_force(conn, EVID, at)


@pytest.fixture
def env(tmp_path):
    return fx.make_env(tmp_path, production=True)


# --- Registration --------------------------------------------------------------------------------


def test_registration_needs_its_confirmation_token_and_is_idempotent(env):
    path = fx.write_registry_file(env.registries, EVID, fx.no_machine_decision_registry())
    plan = plan_registration(
        env.store,
        EVID,
        path,
        source_commit_sha=fx.SOURCE_COMMIT,
        registries_root=env.registries,
        source_verifier=fx.committed_bytes_verifier(env.registries),
    )
    dry = plan.dry_run()
    assert set(dry) == {
        "operation",
        "registry_kind",
        "registry_version_id",
        "registry_label",
        "source_path",
        "source_file_sha256",
        "source_commit_sha",
        "confirmation_token",
    }
    assert (
        _reason(execute_registration, env.store, plan, "cfm_wrong") == "confirmation_token_mismatch"
    )
    execute_registration(env.store, plan, plan.confirmation_token)
    execute_registration(env.store, plan, plan.confirmation_token)  # idempotent
    with connect(env.store.database_path, read_only=True) as conn:
        assert len(select_rows(conn, "SELECT * FROM registry_versions")) == 1
    # Registering never activates.
    assert _activations(env) == []


def test_uncommitted_file_and_reused_label_are_refused(env):
    path = fx.write_registry_file(env.registries, EVID, fx.no_machine_decision_registry())
    assert (
        _reason(
            plan_registration,
            env.store,
            EVID,
            path,
            source_commit_sha=fx.SOURCE_COMMIT,
            registries_root=env.registries,
            source_verifier=lambda *_: b"different bytes",
        )
        == "registry_not_committed"
    )
    fx.register(env, EVID, fx.no_machine_decision_registry())
    changed = fx.no_machine_decision_registry(uncertainty_codes=["indicative_feed"])
    assert _reason(fx.register, env, EVID, changed) == "registry_label_reused"


def test_payload_schema_without_code_model_is_refused(env):
    base = fx.no_machine_decision_registry()
    extra = sorted(
        [
            *base.payload_schemas,
            PayloadSchemaEntry(payload_schema_id="unmodelled.v1", spy_price_content=False),
        ],
        key=lambda p: p.payload_schema_id,
    )
    registry = fx.no_machine_decision_registry(payload_schemas=extra)
    assert _reason(fx.register, env, EVID, registry) == "payload_model_missing"


def test_selector_authority_cannot_be_transferred_by_a_file(env):
    path = fx.write_registry_file(env.registries, EVID, fx.no_machine_decision_registry())
    data = json.loads(path.read_bytes())
    for producer in data["registry"]["producers"]:
        if producer["producer_id"] == f.CALC:
            producer["emission_rules"][0]["payload_schema_id"] = "contract_selector_result.v1"
    path.write_bytes(
        (json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()
    )
    assert (
        _reason(
            plan_registration,
            env.store,
            EVID,
            path,
            source_commit_sha=fx.SOURCE_COMMIT,
            registries_root=env.registries,
            source_verifier=fx.committed_bytes_verifier(env.registries),
        )
        == "registry_schema_invalid"
    )


# --- Activation rules ----------------------------------------------------------------------------


def test_activation_reasons_rollback_and_in_force_selection(env):
    first = fx.register(env, EVID, fx.no_machine_decision_registry())
    assert _reason(
        execute_activation, env.store, plan_activation(env.store, _request(first)), "x"
    ) == ("confirmation_token_mismatch")
    plan = plan_activation(env.store, _request(first))
    assert plan.evaluation.refusal == "activation_reason_mismatch"
    fx.activate(env, EVID, first)
    second = fx.register(
        env, EVID, fx.no_machine_decision_registry(registry_label="synthetic-test-2")
    )
    env.clock.advance(minutes=1)
    assert plan_activation(env.store, _request(first)).evaluation.refusal == "activation_no_change"
    assert plan_activation(
        env.store, _request(second, ActivationReason.ROLLBACK)
    ).evaluation.refusal == ("activation_reason_mismatch")
    fx.activate(env, EVID, second, reason=ActivationReason.VERSION_UPGRADE)
    assert _in_force(env, env.clock()) == second
    env.clock.advance(minutes=1)
    assert (
        plan_activation(env.store, _request(first)).evaluation.refusal
        == "activation_reason_mismatch"
    )
    fx.activate(env, EVID, first, reason=ActivationReason.ROLLBACK)
    rows = _activations(env)
    assert [r["activation_reason"] for r in rows] == [
        "initial_activation",
        "version_upgrade",
        "rollback",
    ]
    assert rows[2]["supersedes_activation_id"] == rows[1]["activation_id"]
    assert _in_force(env, env.clock()) == first
    # History is never rewritten: past instants keep their answer.
    assert _in_force(env, env.clock() - timedelta(minutes=1)) == second


def test_backdated_future_and_duplicate_effective_times(env):
    first = fx.register(env, EVID, fx.no_machine_decision_registry())
    backdated = plan_activation(
        env.store,
        _request(first, ActivationReason.INITIAL_ACTIVATION, env.clock() - timedelta(seconds=1)),
    )
    assert backdated.evaluation.refusal == "activation_backdated"
    future_time = env.clock() + timedelta(hours=1)
    fx.activate(env, EVID, first, effective=future_time)
    assert _in_force(env, env.clock()) is None
    assert _in_force(env, future_time) == first
    second = fx.register(
        env, EVID, fx.no_machine_decision_registry(registry_label="synthetic-test-2")
    )
    same_time = plan_activation(env.store, _request(second, effective=future_time))
    assert same_time.evaluation.refusal == "activation_effective_time_taken"


def test_stale_plan_cannot_execute(env):
    first = fx.register(env, EVID, fx.no_machine_decision_registry())
    fx.activate(env, EVID, first)
    second = fx.register(
        env, EVID, fx.no_machine_decision_registry(registry_label="synthetic-test-2")
    )
    third = fx.register(
        env, EVID, fx.no_machine_decision_registry(registry_label="synthetic-test-3")
    )
    env.clock.advance(minutes=1)
    stale = plan_activation(env.store, _request(third))
    fx.activate(env, EVID, second, reason=ActivationReason.VERSION_UPGRADE)
    env.clock.advance(minutes=1)
    assert (
        _reason(execute_activation, env.store, stale, stale.confirmation_token)
        == "confirmation_token_mismatch"
    )


def test_setup_definition_registry_activation_stays_empty(env):
    version = fx.register(env, RegistryKind.SETUP_DEFINITION_REGISTRY, fx.EMPTY_SETUP_REGISTRY)
    fx.activate(env, RegistryKind.SETUP_DEFINITION_REGISTRY, version)
    with env.store.read_connection() as conn:
        assert (
            env.store.in_force(conn, RegistryKind.SETUP_DEFINITION_REGISTRY, env.clock()) == version
        )
        assert env.store.load_registry(conn, version).definitions == []


# --- Price-declaration immutability (complete history) -------------------------------------------


def _with_declaration(label, value, *, drop=False):
    base = fx.no_machine_decision_registry()
    schemas = [
        PayloadSchemaEntry(payload_schema_id=s.payload_schema_id, spy_price_content=value)
        if s.payload_schema_id == "synthetic_inference.v1"
        else s
        for s in base.payload_schemas
        if not (drop and s.payload_schema_id == "synthetic_inference.v1")
    ]
    producers = [p for p in base.producers if not (drop and p.producer_id == f.AGENT)]
    return fx.no_machine_decision_registry(
        registry_label=label, payload_schemas=schemas, producers=producers
    )


def test_price_declaration_cannot_change_against_any_prior_activation(env):
    first = fx.register(env, EVID, _with_declaration("v-true", True))
    fx.activate(env, EVID, first)
    gap = fx.register(env, EVID, _with_declaration("v-gap", True, drop=True))
    env.clock.advance(minutes=1)
    fx.activate(env, EVID, gap, reason=ActivationReason.VERSION_UPGRADE)
    weakened = fx.register(env, EVID, _with_declaration("v-false", False))
    env.clock.advance(minutes=1)
    # The version in force lacks the schema; the history still forbids true -> false.
    assert (
        plan_activation(env.store, _request(weakened)).evaluation.refusal
        == "holdout_declaration_weakened"
    )


def test_false_to_true_needs_a_new_schema_id(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    first = fx.register(env, EVID, _with_declaration("v-false", False))
    fx.activate(env, EVID, first)
    flipped = fx.register(env, EVID, _with_declaration("v-true", True))
    env.clock.advance(minutes=1)
    assert (
        plan_activation(env.store, _request(flipped)).evaluation.refusal
        == "holdout_declaration_changed"
    )


def test_dropping_a_schema_that_stored_evidence_uses_is_refused(env):
    first = fx.register(env, EVID, fx.no_machine_decision_registry())
    fx.activate(env, EVID, first)
    bar = f.bar_item()
    env.store.append_envelope(fx.envelope([bar], first))
    inference = f.inference_item([bar])
    env.store.append_envelope(fx.envelope([inference], first, producer=f.AGENT, run_key="7" * 64))
    dropped = fx.register(env, EVID, _with_declaration("v-dropped", False, drop=True))
    env.clock.advance(minutes=1)
    assert (
        plan_activation(env.store, _request(dropped)).evaluation.refusal
        == "registry_drops_referenced_schema"
    )


# --- Unresolved-conflict activation guard --------------------------------------------------------


def _conflict(items, severity, ctype=ConflictType.PROVIDER_DISAGREEMENT, evaluated=None):
    return seal_conflict(
        EvidenceConflictContent(
            conflict_type=ctype,
            involved_item_ids=sorted(i.item_id for i in items),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=f.DETECTOR,
            detector_version="1.0.0",
            severity=severity,
            status=ConflictStatus.UNRESOLVED,
            evaluated_as_of_utc=evaluated,
            detected_at_utc=evaluated + timedelta(seconds=1),
        )
    )


def _seeded(env, registry):
    version = fx.register(env, EVID, registry)
    fx.activate(env, EVID, version)
    bars = [f.bar_item(), f.bar_item(f.T0 - timedelta(minutes=5))]
    env.store.append_envelope(fx.envelope(bars, version))
    env.clock.set(f.T0 + timedelta(minutes=10))
    return version, bars


def test_current_registry_shape_grants_no_machine_decision_mode():
    assert authorized_machine_decision_purposes(fx.no_machine_decision_registry()) == []


def test_first_machine_decision_authorization_with_no_unresolved_conflicts(env):
    _seeded(env, fx.no_machine_decision_registry())
    md = fx.register(env, EVID, fx.machine_decision_registry(registry_label="synthetic-md"))
    plan = plan_activation(env.store, _request(md))
    assert plan.evaluation.machine_decision_triggered
    assert plan.evaluation.affected_unresolved_conflicts == 0
    assert plan.evaluation.refusal is None
    execute_activation(env.store, plan, plan.confirmation_token)
    row = _activations(env)[-1]
    assert row["effective_from_utc"] == row["activated_at_utc"]  # immediate


def test_authorization_refused_when_an_unresolved_conflict_would_become_required(env):
    _, bars = _seeded(env, fx.no_machine_decision_registry())
    conflict = _conflict(
        bars, ConflictSeverity.INFORMATIONAL, evaluated=f.T0 + timedelta(seconds=30)
    )
    env.store.append_conflict(conflict)
    with connect(env.store.database_path, read_only=True) as conn:
        before = select_rows(conn, "SELECT * FROM evidence_conflicts")
    md = fx.register(env, EVID, fx.machine_decision_registry(registry_label="synthetic-md"))
    plan = plan_activation(env.store, _request(md))
    assert plan.evaluation.refusal == "unresolved_conflict_requires_contract_amendment"
    assert plan.evaluation.affected_unresolved_conflicts == 1
    dry = json.dumps(plan.dry_run())
    assert conflict.conflict_id not in dry and bars[0].item_id not in dry
    assert _reason(execute_activation, env.store, plan, plan.confirmation_token) == (
        "unresolved_conflict_requires_contract_amendment"
    )
    with connect(env.store.database_path, read_only=True) as conn:
        assert select_rows(conn, "SELECT * FROM evidence_conflicts") == before
        events = [
            r["event_type"] for r in select_rows(conn, "SELECT event_type FROM store_audit_events")
        ]
    assert events[-1] == "registry_activation_refused"


def test_requirement_change_refused_when_it_changes_matched_requirements(env):
    _, bars = _seeded(env, fx.machine_decision_registry())
    env.store.append_conflict(
        _conflict(bars, ConflictSeverity.CRITICAL, evaluated=f.T0 + timedelta(seconds=30))
    )
    base = fx.machine_decision_registry()
    rules = [
        SelectionRule(
            selection_rule_id=r.selection_rule_id,
            purpose=r.purpose,
            requirements=[q for q in r.requirements if q.requirement_id != "req.bars.spy"]
            if r.purpose is BundlePurpose.LIVE_MARKET_STATE
            else r.requirements,
            max_window_seconds=r.max_window_seconds,
        )
        for r in base.selection_rules
    ]
    changed = fx.register(
        env,
        EVID,
        fx.machine_decision_registry(registry_label="synthetic-md-2", selection_rules=rules),
    )
    assert plan_activation(env.store, _request(changed)).evaluation.refusal == (
        "unresolved_conflict_requires_contract_amendment"
    )


def test_consumer_grant_change_refused_when_it_changes_severity(env):
    _, bars = _seeded(env, fx.machine_decision_registry())
    env.store.append_conflict(
        _conflict(bars, ConflictSeverity.CRITICAL, evaluated=f.T0 + timedelta(seconds=30))
    )
    revoked = fx.register(
        env, EVID, fx.no_machine_decision_registry(registry_label="synthetic-no-md")
    )
    plan = plan_activation(env.store, _request(revoked))
    assert plan.evaluation.machine_decision_triggered
    assert plan.evaluation.refusal == "unresolved_conflict_requires_contract_amendment"


def test_activation_allowed_when_unresolved_derivations_are_unchanged(env):
    _, bars = _seeded(env, fx.no_machine_decision_registry())
    claims = [f.inference_item([bars[0]]), f.inference_item([bars[1]])]
    env.store.append_envelope(
        fx.envelope(
            claims, _activations(env)[0]["registry_version_id"], producer=f.AGENT, run_key="6" * 64
        )
    )
    conflict = _conflict(
        claims,
        ConflictSeverity.INFORMATIONAL,
        ConflictType.CROSS_DOMAIN_INFERENCE_DISAGREEMENT,
        evaluated=f.T0 + timedelta(seconds=30),
    )
    env.store.append_conflict(conflict)
    md = fx.register(env, EVID, fx.machine_decision_registry(registry_label="synthetic-md"))
    plan = plan_activation(env.store, _request(md))
    assert plan.evaluation.machine_decision_triggered and plan.evaluation.refusal is None
    execute_activation(env.store, plan, plan.confirmation_token)


def test_future_dated_machine_decision_activation_is_refused(env):
    _seeded(env, fx.no_machine_decision_registry())
    md = fx.register(env, EVID, fx.machine_decision_registry(registry_label="synthetic-md"))
    plan = plan_activation(env.store, _request(md, effective=env.clock() + timedelta(hours=1)))
    assert plan.evaluation.refusal == "machine_decision_activation_must_take_effect_immediately"


def test_dry_run_lists_grant_changes_for_human_review(env):
    _seeded(env, fx.no_machine_decision_registry())
    md = fx.register(env, EVID, fx.machine_decision_registry(registry_label="synthetic-md"))
    assert plan_activation(env.store, _request(md)).dry_run()["grant_changes"] == ["setup_ranker"]
