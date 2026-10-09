"""The conflict-severity consumer boundary.

Severity is determined and frozen by the Evidence Store from the registry in
force at the conflict's evaluation: authorized machine-decision purposes,
matched requirement IDs, the required-item flag and the frozen rule version.
Consumers validate a stored conflict's structure and display its stored
severity; a consuming bundle's own ``required`` flags never re-decide it.
Synthetic data and temporary stores only."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path

import duckdb
import pytest

from market_intelligence.dashboard.adapter import present
from market_intelligence.dashboard.fixtures import build_scenarios
from market_intelligence.dashboard.validation import DashboardInputError
from market_intelligence.evidence.contracts import EvidenceConflictContent, seal_conflict
from market_intelligence.evidence.enums import ConflictSeverity, ConflictType
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.validation import validate_conflict, validate_conflict_record
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.enums import (
    IntegrityFinding,
    StoreOperation,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop
from market_intelligence.evidence_store.severity import derive_conflict_severity
from market_intelligence.evidence_store.store import Txn
from market_intelligence.evidence_store.verification import verify_database
from market_intelligence.integration import registry as R
from market_intelligence.integration import scenarios as S
from market_intelligence.integration.pipeline import ScenarioStore, read_back
from market_intelligence.integration.workspace import store_paths
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx
from market_intelligence.tests.test_evidence_store_conflicts import _conflict
from market_intelligence.tests.test_evidence_store_conflicts import _env as conflict_env

REPO = Path(R.__file__).resolve().parents[2]


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _stored_conflict_run(tmp_path, *, fixture_grant: bool, pair: tuple[int, int], severity):
    """One temporary store with one stored provider disagreement between two
    stored bars, read back and presented by the dashboard."""
    registry = R.make_registry(machine_decision_fixture=fixture_grant)
    store = ScenarioStore.create(store_paths(tmp_path, "case"), S.START, registry=registry)
    S._premarket(store)
    built = S._session(store, selector=S.dash.selector_result())
    a, b = (built.bars[i] for i in pair)
    store.at(S._t(15, 2, 20)).conflict(S.provider_disagreement(a, b, S._t(15, 2), severity))
    S._bundles_and_card(built, S.AS_OF)
    scenario = read_back(
        store, scenario_id="case", title="Case", summary="Synthetic case.", market="market"
    )
    return store, scenario, present(scenario), built


def _entry(bundle_input, item_id):
    return next(e for e in bundle_input.bundle.entries if e.item_id == item_id)


# --- Core: the record check never recomputes severity -----------------------------------------


def test_record_validation_keeps_structure_but_never_recomputes_severity():
    bars = [f.bar_item(), f.bar_item(f.T0 - timedelta(minutes=5))]
    items = {b.item_id: b for b in bars}
    registry = f.make_registry()
    informational = _conflict(bars, severity=ConflictSeverity.INFORMATIONAL)
    required = frozenset({bars[0].item_id})
    # The producer-side check derives severity from the required set given...
    assert (
        _reason(validate_conflict, informational, items, registry, required_item_ids=required)
        == "severity_mismatch"
    )
    # ...the consumer-side record check does not, and keeps every structural rule.
    validate_conflict_record(informational, items, registry)
    assert _reason(validate_conflict_record, informational, {}, registry) == "unknown_involved_item"
    early = _conflict(bars, evaluated=f.T0 - timedelta(hours=1))
    assert _reason(validate_conflict_record, early, items, registry) == "conflict_before_evidence"


# --- A dashboard bundle never re-decides stored severity --------------------------------------


def test_a_dashboard_bundle_requiring_an_item_does_not_make_its_conflict_critical(tmp_path):
    # Without the isolated fixture grant no purpose is machine-decision
    # authorized, so the store derives an informational provider disagreement.
    store, scenario, model, built = _stored_conflict_run(
        tmp_path, fixture_grant=False, pair=(0, -1), severity=ConflictSeverity.INFORMATIONAL
    )
    latest = built.bars[-1].item_id
    assert _entry(scenario.vwap.context, latest).required  # the dashboard bundle requires it
    (conflict,) = scenario.market.conflicts.values()
    assert conflict.severity is ConflictSeverity.INFORMATIONAL
    # The old consumer path would have re-decided it from the bundle's flags:
    assert (
        _reason(
            validate_conflict,
            conflict,
            scenario.market.items,
            scenario.registry,
            required_item_ids=frozenset({latest}),
        )
        == "severity_mismatch"
    )
    views = [c for c in model.market.conflicts]
    assert [v.severity for v in views] == ["informational"]
    assert model.market.strip.critical_conflicts == 0
    card = model.vwap_lane.cards[0]
    assert not any("critical" in b for b in card.blockers)


def test_a_stored_critical_conflict_stays_critical_when_not_bundle_required(tmp_path):
    # With the isolated fixture grant both early bars match a machine-decision
    # requirement, so the store froze the conflict as critical.
    store, scenario, model, built = _stored_conflict_run(
        tmp_path, fixture_grant=True, pair=(0, 1), severity=ConflictSeverity.CRITICAL
    )
    for bar in built.bars[:2]:
        assert not _entry(scenario.market, bar.item_id).required
    assert [v.severity for v in model.market.conflicts] == ["critical"]
    assert model.market.strip.critical_conflicts == 1
    assert model.market.blocking[0].startswith("Unresolved critical conflict")
    with store.store.read_connection() as conn:
        (row,) = conn.execute(
            "SELECT severity, involves_required_item FROM evidence_conflicts"
        ).fetchall()
    assert row == ("critical", True)  # the store's frozen derivation, unchanged


def test_a_critical_conflict_remains_a_manual_review_blocker(tmp_path):
    store, scenario = S.run_scenario(tmp_path, S.SCENARIOS[5])
    assert S.SCENARIOS[5].scenario_id == "unresolved_critical_conflict"
    model = present(scenario)
    card = model.vwap_lane.cards[0]
    assert card.readiness_label == "NOT READY FOR MANUAL REVIEW"
    assert any("unresolved_critical_conflict" in b for b in card.blockers)
    assert model.market.strip.critical_conflicts == 1


# --- Material and critical stay distinct --------------------------------------------------------


def test_material_and_critical_conflicts_render_distinctly():
    models = {sid: present(s) for sid, s in build_scenarios().items()}
    material = models["ready_for_manual_review"].market
    critical = models["unresolved_critical_conflict"].market
    assert {c.severity for c in material.conflicts} == {"material"}
    assert (material.strip.material_conflicts, material.strip.critical_conflicts) == (1, 0)
    assert "critical" in {c.severity for c in critical.conflicts}
    assert critical.strip.critical_conflicts == 1
    assert not any(b.startswith("Unresolved critical conflict") for b in material.blocking)
    st = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    st.cache_resource.clear()
    try:
        app = REPO / "market_intelligence" / "dashboard" / "app.py"
        at = AppTest.from_file(str(app), default_timeout=120).run()
        at.sidebar.selectbox[0].set_value("ready_for_manual_review").run()
        ready = " ".join(str(m.value) for m in at.markdown)
        at.sidebar.selectbox[0].set_value("unresolved_critical_conflict").run()
        blocked = " ".join(str(m.value) for m in at.markdown)
    finally:
        st.cache_resource.clear()
    assert "`CONFLICT` inference_vs_observation, severity material" in ready
    assert "`CRITICAL CONFLICT`" not in ready
    assert "`CRITICAL CONFLICT` provider_disagreement, severity critical" in blocked


# --- Consumers cannot alter severity -------------------------------------------------------------


def test_consumers_cannot_edit_downgrade_upgrade_or_replace_severity():
    scenario = build_scenarios()["unresolved_critical_conflict"]
    conflict_id, conflict = next(
        (cid, c)
        for cid, c in scenario.market.conflicts.items()
        if c.severity is ConflictSeverity.CRITICAL
    )
    downgraded = conflict.model_copy(update={"severity": ConflictSeverity.MATERIAL})
    edited = dataclasses.replace(
        scenario,
        market=dataclasses.replace(
            scenario.market, conflicts={**scenario.market.conflicts, conflict_id: downgraded}
        ),
    )
    with pytest.raises(DashboardInputError) as excinfo:
        present(edited)
    assert excinfo.value.reason == "conflict_invalid"  # its sealed identity no longer holds
    fields = {n: getattr(conflict, n) for n in EvidenceConflictContent.model_fields}
    replaced = seal_conflict(
        EvidenceConflictContent(**{**fields, "severity": ConflictSeverity.MATERIAL})
    )
    swapped = dataclasses.replace(
        scenario,
        market=dataclasses.replace(
            scenario.market,
            conflicts={
                **{k: v for k, v in scenario.market.conflicts.items() if k != conflict_id},
                replaced.conflict_id: replaced,
            },
        ),
    )
    with pytest.raises(DashboardInputError) as excinfo:
        present(swapped)
    assert excinfo.value.reason == "bundle_conflicts_mismatch"  # not the bundle's conflict
    view = next(c for c in present(scenario).market.conflicts if c.severity == "critical")
    with pytest.raises(dataclasses.FrozenInstanceError):
        view.severity = "material"  # type: ignore[misc]


# --- Store verification refuses a malformed stored severity -----------------------------------


def test_a_malformed_stored_severity_is_refused_by_store_verification(tmp_path):
    env = conflict_env(tmp_path)
    honest = _conflict(env.bars)  # informational under this registry
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
    derivation = derive_conflict_severity(honest, env.bars, registry)
    assert derivation.severity == "informational"
    inflated = _conflict(env.bars, severity=ConflictSeverity.CRITICAL)

    def smuggle(txn: Txn) -> None:  # hash-consistent, but not what the registry derives
        txn.add("evidence_conflicts", rec.conflict_columns(inflated, derivation, env.evidence_id))

    env.store.run_write(StoreWriterId.STORE_SERVICE, StoreOperation.RECORD_AUDIT_EVENTS, smuggle)
    report = verify_database(env.store.database_path, payload_models=fx.PAYLOAD_MODELS)
    assert IntegrityFinding.CONFLICT_SEVERITY_IRREPRODUCIBLE in {x.finding for x in report.findings}
    with pytest.raises(IntegrityStop):
        fx.reopen(env)  # the startup scan refuses to serve it
    with duckdb.connect(str(env.store.database_path), read_only=True) as conn:
        assert conn.execute("SELECT severity FROM evidence_conflicts").fetchone() == ("critical",)


def test_the_store_still_refuses_a_severity_its_derivation_does_not_give(tmp_path):
    env = conflict_env(tmp_path)
    from market_intelligence.evidence_store.errors import StoreRefusal

    with pytest.raises(StoreRefusal) as excinfo:
        env.store.append_conflict(_conflict(env.bars, severity=ConflictSeverity.CRITICAL))
    assert excinfo.value.reason == "conflict_severity_mismatch"


# --- No production machine-decision authorization ---------------------------------------------


def test_no_production_machine_decision_authorization_is_added():
    package = REPO / "market_intelligence"
    granting = sorted(
        str(p.relative_to(package)).replace("\\", "/")
        for p in package.rglob("*.py")
        if "tests" not in p.parts
        and "machine_decision_mode_allowed=True" in p.read_text(encoding="utf-8")
    )
    assert granting == ["integration/registry.py"]  # the isolated synthetic fixture only
    assert not (REPO / "registries").exists()
    assert R.REGISTRY_LABEL.startswith("synthetic-")
    without = R.make_registry(machine_decision_fixture=False)
    assert not any(g.machine_decision_mode_allowed for g in without.consumer_grants)
    assert ConflictType.PROVIDER_DISAGREEMENT  # vocabulary untouched
