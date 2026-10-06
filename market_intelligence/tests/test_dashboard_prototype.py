"""The offline dashboard prototype's framework-free layers: synthetic
scenarios, fail-closed validation, view derivation, trust labels and
navigation. Synthetic data only; no database, provider, model or network."""

from __future__ import annotations

import ast
import dataclasses
import socket
from pathlib import Path

import duckdb
import pytest

from market_intelligence.contract_selection.contracts import ContractSelectorResult
from market_intelligence.dashboard import adapter, fixtures
from market_intelligence.dashboard import labels as L
from market_intelligence.dashboard import views as V
from market_intelligence.dashboard.adapter import present, validate_scenario
from market_intelligence.dashboard.fixtures import (
    HOLDOUT_T0,
    SCENARIO_ORDER,
    SYNTHETIC_DEFINITION,
    build_scenarios,
)
from market_intelligence.dashboard.navigation import (
    NavigationError,
    NavigationState,
    Page,
    go_to,
    open_card,
    open_contract_review,
    selected_card,
    switch_scenario,
)
from market_intelligence.dashboard.validation import DashboardInputError
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.enums import EvidenceKind
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence.selector_boundary import CONTRACT_SELECTOR_PAYLOAD_ID
from market_intelligence.setup_cards.contracts import FORBIDDEN_FIELD_FRAGMENTS
from market_intelligence.setup_cards.definitions import REGISTERED_SETUP_DEFINITIONS
from market_intelligence.setup_cards.enums import (
    CardKind,
    EvidenceReadiness,
    LaneQualificationAvailability,
    SelectorAvailability,
)

PACKAGE = Path(fixtures.__file__).parent
RESTRICTED_BAR = fixtures.bar_item(HOLDOUT_T0, close="611.11")


@pytest.fixture(scope="module")
def models() -> dict[str, V.DashboardModel]:
    return {sid: present(s) for sid, s in build_scenarios().items()}


def _only_card(model: V.DashboardModel) -> V.SetupDetailView:
    assert len(model.vwap_lane.cards) == 1
    return model.cards[model.vwap_lane.cards[0].card_id]


def _review(model: V.DashboardModel) -> V.ContractReviewView:
    return model.contract_reviews[model.vwap_lane.cards[0].card_id]


def _strings(value) -> list[str]:
    """Every string reachable from a view model."""
    if isinstance(value, str):
        return [value]
    if dataclasses.is_dataclass(value):
        return [s for f in dataclasses.fields(value) for s in _strings(getattr(value, f.name))]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(value, tuple | list):
        return [s for v in value for s in _strings(v)]
    return []


# --- Every required dashboard state ---------------------------------------------------------


def test_every_scenario_validates_and_presents(models):
    assert tuple(models) == SCENARIO_ORDER
    for model in models.values():
        assert model.market.strip.scope == L.SCOPE_STATEMENT
        assert model.market.strip.as_of and model.premarket.strip.as_of


def test_no_authorized_live_qualification(models):
    lane = models["no_authorized_live_qualification"].vwap_lane
    assert lane.availability is LaneQualificationAvailability.NOT_AUTHORIZED
    assert lane.cards == ()
    assert lane.text.startswith("Live qualification not authorized")
    assert REGISTERED_SETUP_DEFINITIONS == ()  # production table untouched


def test_trend_lane_is_never_researched(models):
    for model in models.values():
        lane = model.trend_lane
        assert lane.text == L.TREND_LANE_TEXT and lane.cards == ()
        # No card of either kind ever appears in the trend lane.
        assert all(d.summary.lane.value == "vwap_reversion" for d in model.cards.values())


def test_ready_for_manual_review_is_never_trade_readiness(models):
    detail = _only_card(models["ready_for_manual_review"])
    assert detail.summary.readiness is EvidenceReadiness.READY
    assert detail.summary.readiness_label == "READY FOR MANUAL REVIEW"
    assert L.forbidden_terms_in(detail.summary.readiness_label) == []
    assert detail.manual_decision.startswith("Decision support only.")


def test_no_qualified_setup_comes_from_an_authorized_not_qualified_conclusion(models):
    model = models["no_qualified_setup"]
    detail = _only_card(model)
    assert detail.summary.card_kind is CardKind.NO_QUALIFIED_SETUP
    assert detail.summary.qualification_label == "Qualification: not qualified"
    assert "criteria_not_met" in detail.summary.no_setup_text
    assert "valid result, not an error" in detail.summary.no_setup_text


@pytest.mark.parametrize(
    ("scenario", "blocker"),
    [
        ("stale_required_evidence", "stale_required_evidence"),
        ("missing_evidence", "missing_required_evidence"),
        ("ambiguous_evidence", "ambiguous_requirement"),
        ("ambiguous_clock_facts", "ambiguous_clock_facts"),
        ("unresolved_critical_conflict", "unresolved_critical_conflict"),
        ("holdout_restricted", "holdout_restricted"),
    ],
)
def test_blocked_states_are_named_on_card_and_market(models, scenario, blocker):
    model = models[scenario]
    detail = _only_card(model)
    assert detail.summary.readiness is EvidenceReadiness.BLOCKED
    assert detail.summary.readiness_label == "NOT READY FOR MANUAL REVIEW"
    assert any(blocker in b for b in detail.summary.blockers)
    assert any(blocker in b for b in model.market.strip.blockers)
    assert model.market.blocking  # blocking states render above the fold


def test_stale_evidence_stays_visible_with_its_age(models):
    market = models["stale_required_evidence"].market
    stale = [r for r in market.non_current if r.freshness.startswith("STALE")]
    assert stale and all("age" in r.freshness for r in stale)


def test_missing_and_ambiguous_evidence_are_listed(models):
    assert models["missing_evidence"].market.missing[0].requirement_id == "req.bars.spy"
    ambiguous = models["ambiguous_evidence"].market.ambiguous
    assert len(ambiguous) == 1 and len(ambiguous[0].competing_item_ids) >= 2


def test_ambiguous_clock_facts_reach_the_status_strip(models):
    assert "ambiguous_clock_facts" in models["ambiguous_clock_facts"].market.strip.clock


def test_critical_conflict_shows_both_sides(models):
    market = models["unresolved_critical_conflict"].market
    assert market.strip.critical_conflicts == 1
    assert market.blocking[0].startswith("Unresolved critical conflict")
    critical = [c for c in market.conflicts if c.severity == "critical"]
    assert len(critical) == 1 and len(critical[0].sides) == 2


def test_selector_unavailable_is_never_no_eligible_contracts(models):
    review = _review(models["selector_unavailable"])
    assert review.availability is SelectorAvailability.UNAVAILABLE
    assert review.availability_label == "Contract selector unavailable"
    assert review.outcome_label is None
    assert "no eligible" not in " ".join(_strings(review)).lower()


def test_no_eligible_contracts_shows_counts_only(models):
    review = _review(models["no_eligible_contracts"])
    assert review.outcome_label == "The selector returned no eligible contracts"
    assert review.eligible == () and review.research_only == ()
    assert dict(review.rejection_counts)["spread_too_wide"] == 3


def test_eligible_and_research_only_sets_stay_separate(models):
    eligible = _review(models["ready_for_manual_review"])
    research = _review(models["research_only_contracts"])
    assert eligible.eligible and not eligible.research_only
    assert research.research_only and not research.eligible
    assert research.indicative_warning and not eligible.indicative_warning


def test_outdated_and_superseded_cards(models):
    model = models["outdated_and_superseded"]
    assert len(model.vwap_lane.cards) == 1  # only the latest revision is listed
    latest = model.vwap_lane.cards[0]
    first, second = model.cards[latest.card_id].history
    assert first.display_status_label == "Superseded"
    assert second.display_status_label.startswith("Outdated view")
    assert model.cards[first.card_id].newer_card_id == second.card_id
    assert model.cards[second.card_id].newer_card_id is None


def test_holdout_restricted_content_never_appears(models):
    model = models["holdout_restricted"]
    assert model.market.strip.holdout_notice == L.HOLDOUT_NOTICE
    assert any(m.holdout_restricted for m in model.market.missing)
    text = " ".join(_strings(model))
    assert RESTRICTED_BAR.item_id not in text and "611.11" not in text
    for scenario in build_scenarios().values():
        for bundle_input in (scenario.market, scenario.premarket, scenario.vwap.context):
            assert RESTRICTED_BAR.item_id not in bundle_input.items
            for item in bundle_input.items.values():
                assert not is_holdout_restricted(item, scenario.registry)


# --- Selector status separation and inference handling ---------------------------------------


def test_contract_sets_are_exactly_the_selectors_own(models):
    for scenario_id, scenario in build_scenarios().items():
        for record in scenario.vwap.history:
            review = models[scenario_id].contract_reviews[record.card.card_id]
            selector = [
                i
                for i in record.source.items.values()
                if i.payload_schema_id == CONTRACT_SELECTOR_PAYLOAD_ID
            ]
            if review.availability is not SelectorAvailability.AVAILABLE:
                assert review.eligible == review.research_only == ()
                continue
            result = ContractSelectorResult.model_validate_json(
                canonical_json_bytes(selector[0].payload)
            )
            assert review.eligible == tuple(
                sorted(c.contract_symbol for c in result.eligible_contracts)
            )
            assert review.research_only == tuple(
                sorted(c.contract_symbol for c in result.research_only_contracts)
            )
            assert review.outcome_label == L.SELECTOR_OUTCOME_LABELS[result.status]
            assert dict(review.rejection_counts) == {
                r.value: n for r, n in result.rejection_counts.items()
            }
            assert not set(review.eligible) & set(review.research_only)


def test_inference_is_never_supporting_or_contradicting(models):
    seen_inference = False
    for model in models.values():
        for detail in model.cards.values():
            for row in (*detail.supporting, *detail.contradicting, *detail.context):
                assert row.kind is not EvidenceKind.CURRENT_INFERENCE
            assert all(r.kind_label == "INFERENCE" for r in detail.inference_context)
            seen_inference |= bool(detail.inference_context)
    assert seen_inference


def test_every_row_carries_kind_and_freshness_labels(models):
    for model in models.values():
        for view in (model.market, model.premarket):
            for row in view.rows:
                assert row.kind_label == L.KIND_LABELS[row.kind]
                assert row.freshness.split(" ")[0] in {
                    "CURRENT",
                    "AGING",
                    "STALE",
                    "TIMELESS",
                    "FRESHNESS",
                }
                assert "UTC" in row.effective_at


def test_research_stays_separate_from_live_state(models):
    for model in models.values():
        assert not model.market.rows_of(EvidenceKind.HISTORICAL_RESEARCH_RESULT)
        assert model.system.research[0].primary_label == "supported_for_further_shadow_research"


# --- Trust labels and forbidden wording ---------------------------------------------------------


def test_forbidden_wording_detector():
    for text in ("Buy now", "sell", "a TRADE", "high probability", "guaranteed move", "will rise"):
        assert L.forbidden_terms_in(text), text
    for text in (L.TREND_LANE_TEXT, L.MANUAL_DECISION, "trading decision", "center", "entry"):
        assert L.forbidden_terms_in(text) == [], text


def test_no_view_text_uses_forbidden_wording(models):
    for model in models.values():
        for text in _strings(model):
            assert L.forbidden_terms_in(text) == [], text


def test_label_module_texts_use_no_forbidden_wording():
    for name in dir(L):
        if not name.isupper():
            continue
        value = getattr(L, name)
        texts = _strings(value) if isinstance(value, dict | str) else []
        if name in {"FORBIDDEN_TERMS", "ALLOWED_PHRASES"}:
            continue
        for text in texts:
            assert L.forbidden_terms_in(text) == [], (name, text)


def test_views_define_no_execution_fields():
    for _, cls in vars(V).items():
        if dataclasses.is_dataclass(cls):
            for f in dataclasses.fields(cls):
                for fragment in FORBIDDEN_FIELD_FRAGMENTS:
                    assert not f.name.startswith(fragment) and f"_{fragment}" not in f.name, (
                        cls.__name__,
                        f.name,
                    )


# --- Fail-closed validation before rendering -------------------------------------------------


def _replace_market(scenario, **changes):
    market = dataclasses.replace(scenario.market, **changes)
    return dataclasses.replace(scenario, market=market)


def _reason(scenario) -> str:
    with pytest.raises(DashboardInputError) as excinfo:
        present(scenario)
    return excinfo.value.reason


@pytest.fixture
def ready():
    return build_scenarios()["ready_for_manual_review"]


def test_tampered_bundle_is_refused(ready):
    forged = ready.market.bundle.model_copy(update={"unresolved_material_conflict_count": 9})
    assert _reason(_replace_market(ready, bundle=forged)) == "bundle_invalid"


def test_bundle_for_another_purpose_is_refused(ready):
    assert (
        _reason(
            _replace_market(
                ready,
                bundle=ready.premarket.bundle,
                items=ready.premarket.items,
                conflicts=ready.premarket.conflicts,
            )
        )
        == "bundle_purpose_mismatch"
    )


def test_items_outside_the_bundle_are_refused(ready):
    extra = {**ready.market.items, RESTRICTED_BAR.item_id: RESTRICTED_BAR}
    assert _reason(_replace_market(ready, items=extra)) == "bundle_items_mismatch"
    fewer = dict(list(ready.market.items.items())[1:])
    assert _reason(_replace_market(ready, items=fewer)) == "bundle_items_mismatch"


def test_tampered_item_is_refused(ready):
    item_id, item = next(iter(ready.market.items.items()))
    forged = item.model_copy(
        update={"machine_decision_eligible": not item.machine_decision_eligible}
    )
    items = {**ready.market.items, item_id: forged}
    assert _reason(_replace_market(ready, items=items)) == "item_invalid"


def test_tampered_card_is_refused(ready):
    record = ready.vwap.history[0]
    forged = record.card.model_copy(update={"manual_review_ready": False})
    history = (dataclasses.replace(record, card=forged),)
    scenario = dataclasses.replace(ready, vwap=dataclasses.replace(ready.vwap, history=history))
    assert _reason(scenario) == "card_invalid"


def test_card_against_another_bundle_is_refused(ready):
    other = build_scenarios()["no_eligible_contracts"].vwap.history[0].source
    record = dataclasses.replace(ready.vwap.history[0], source=other)
    lane = dataclasses.replace(ready.vwap, history=(record,), context=other)
    assert _reason(dataclasses.replace(ready, vwap=lane)) == "card_bundle_mismatch"


def test_lane_context_must_be_the_latest_cards_bundle(ready):
    other = build_scenarios()["no_eligible_contracts"].vwap.context
    lane = dataclasses.replace(ready.vwap, context=other)
    assert _reason(dataclasses.replace(ready, vwap=lane)) == "lane_context_mismatch"


def test_production_setup_definitions_are_refused(ready):
    production = SYNTHETIC_DEFINITION.model_copy(update={"setup_definition_id": "live-vwap-def"})
    lane = dataclasses.replace(ready.vwap, definitions=(production,))
    assert _reason(dataclasses.replace(ready, vwap=lane)) == "non_synthetic_setup_definition"


def test_cards_need_a_definition(ready):
    lane = dataclasses.replace(ready.vwap, definitions=())
    assert _reason(dataclasses.replace(ready, vwap=lane)) == "card_without_definition"


def test_validation_runs_before_any_view_is_derived(ready, monkeypatch):
    calls = []
    monkeypatch.setattr(adapter, "bundle_view", lambda *a: calls.append(a))
    forged = ready.market.bundle.model_copy(update={"unresolved_material_conflict_count": 9})
    with pytest.raises(DashboardInputError):
        present(_replace_market(ready, bundle=forged))
    assert calls == []
    validate_scenario(ready)  # the untouched scenario still validates


# --- Determinism and isolation -------------------------------------------------------------------


def test_rendering_inputs_are_deterministic():
    first = {sid: present(s) for sid, s in build_scenarios().items()}
    build_scenarios.cache_clear()
    second = {sid: present(s) for sid, s in build_scenarios().items()}
    assert first == second
    ids = [c for m in second.values() for c in m.cards]
    assert len(ids) == len(set(ids)) == 13


_FORBIDDEN_IMPORTS = (
    "duckdb",
    "httpx",
    "requests",
    "socket",
    "urllib",
    "openai",
    "anthropic",
    "market_intelligence.data_connectors",
    "market_intelligence.storage",
    "market_intelligence.evidence_store",
    "market_intelligence.model_clients",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.news_pipeline",
    "market_intelligence.config",
)


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_package_imports_no_database_provider_model_or_network(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    for name in imported:
        assert not name.startswith(_FORBIDDEN_IMPORTS), (module, name)


def test_only_the_rendering_module_imports_streamlit():
    for path in PACKAGE.glob("*.py"):
        uses = "import streamlit" in path.read_text(encoding="utf-8")
        assert uses == (path.name == "app.py"), path.name


def test_no_database_or_network_use_at_runtime(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the prototype must not open a database or a connection")

    monkeypatch.setattr(duckdb, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    build_scenarios.cache_clear()
    try:
        for scenario in build_scenarios().values():
            present(scenario)
    finally:
        build_scenarios.cache_clear()


# --- Navigation ----------------------------------------------------------------------------------


def test_navigation_preserves_card_and_bundle_identity(models):
    model = models["ready_for_manual_review"]
    card = model.vwap_lane.cards[0]
    state = open_card(NavigationState("ready_for_manual_review"), card.card_id, card.bundle_id)
    assert state.page is Page.SETUP_DETAIL
    review = open_contract_review(state)
    back = go_to(review, Page.SETUP_DETAIL)
    for s in (state, review, back, go_to(back, Page.MARKET_OVERVIEW)):
        assert (s.card_id, s.bundle_id) == (card.card_id, card.bundle_id)
        assert selected_card(model, s) == card.card_id
    assert model.contract_reviews[card.card_id].bundle_id == card.bundle_id
    assert model.cards[card.card_id].strip.bundle_id == card.bundle_id


def test_navigation_never_swaps_bundles_silently(models):
    model = models["outdated_and_superseded"]
    first, second = model.cards[model.vwap_lane.cards[0].card_id].history
    wrong = NavigationState(
        "outdated_and_superseded", Page.SETUP_DETAIL, first.card_id, second.bundle_id
    )
    with pytest.raises(NavigationError) as excinfo:
        selected_card(model, wrong)
    assert excinfo.value.reason == "card_bundle_mismatch"
    with pytest.raises(NavigationError):
        open_contract_review(NavigationState("ready_for_manual_review"))
    with pytest.raises(NavigationError):
        NavigationState("x", Page.SETUP_DETAIL, card_id=first.card_id)


def test_switching_scenario_drops_the_card():
    state = NavigationState("a", Page.SETUP_DETAIL, "scd1_" + "0" * 64, "evb1_" + "0" * 64)
    switched = switch_scenario(state, "b")
    assert switched.card_id is None and switched.bundle_id is None
    assert switch_scenario(state, "a") is state
