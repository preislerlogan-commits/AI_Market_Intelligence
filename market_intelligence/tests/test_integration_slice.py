"""The offline Evidence-to-Dashboard vertical slice, end to end.

Every scenario runs through the real adapters, a temporary Evidence Store,
point-in-time bundles, the synthetic setup evaluation, setup cards and the
dashboard's presentation adapter over records read back from storage.
Temporary directories and synthetic data only."""

from __future__ import annotations

import ast
import json
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.contract_selection.contracts import ContractSelectorResult
from market_intelligence.dashboard.adapter import present
from market_intelligence.dashboard.views import DashboardModel
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.enums import EvidenceKind
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence.selector_boundary import CONTRACT_SELECTOR_PAYLOAD_ID
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.reads import read_card, reproduce_bundle
from market_intelligence.evidence_store.store_io import select_rows
from market_intelligence.integration import demo, scenarios
from market_intelligence.integration import registry as R
from market_intelligence.integration.pipeline import ScenarioStore, read_back
from market_intelligence.integration.scenarios import SCENARIOS, run_scenario
from market_intelligence.integration.workspace import (
    SliceRefusal,
    offline_settings,
    require_offline_settings,
    require_temporary_parent,
    temporary_workspace,
)
from market_intelligence.setup_cards.validation import validate_setup_card

PACKAGE = Path(scenarios.__file__).parent
SPEC = {s.scenario_id: s for s in SCENARIOS}


@dataclass
class Run:
    store: ScenarioStore
    model: DashboardModel


@pytest.fixture(scope="module")
def runs(tmp_path_factory) -> dict[str, Run]:
    workspace = tmp_path_factory.mktemp("slice")
    out = {}
    for spec in SCENARIOS:
        store, scenario = run_scenario(workspace, spec)
        out[spec.scenario_id] = Run(store, present(scenario))
    return out


def _items(run: Run) -> list:
    with run.store.store.read_connection() as conn:
        rows = select_rows(conn, "SELECT record_json FROM evidence_items")
    return [rec.parse_item(r["record_json"]) for r in rows]


def _cards(run: Run) -> list:
    return [read_card(run.store.store, c).card for c in run.store.cards]


# --- The complete path ---------------------------------------------------------------------


def test_every_scenario_passes_every_layer(runs):
    assert tuple(runs) == scenarios.SCENARIO_IDS
    for scenario_id, run in runs.items():
        producers = {i.provenance.producer_id for i in _items(run)}
        assert {"alpaca_market_bars", "system_clock_health", R.RESEARCH, R.EVALUATOR} <= producers
        assert {"premarket", "market", "setup"} <= set(run.store.bundles)
        assert len(run.store.cards) == 1
        model = run.model
        assert model.scenario_id == scenario_id
        assert model.premarket.strip.bundle_id == run.store.bundles["premarket"]


@pytest.mark.parametrize(
    ("scenario_id", "kind", "blocker", "selector"),
    [
        ("ready_for_manual_review", "setup", None, "Selector outcome: eligible"),
        ("no_qualified_setup", "no_qualified_setup", None, None),
        ("missing_evidence", "setup", "missing_required_evidence", "Selector outcome: eligible"),
        ("stale_evidence", "setup", "stale_required_evidence", None),
        ("ambiguous_evidence", "setup", "ambiguous_requirement", "Selector outcome: eligible"),
        (
            "unresolved_critical_conflict",
            "setup",
            "unresolved_critical_conflict",
            "Selector outcome: eligible",
        ),
        ("selector_unavailable", "setup", None, None),
        (
            "research_only_contracts",
            "setup",
            None,
            "Selector outcome: research only (not eligible)",
        ),
        ("holdout_refused", "setup", None, "Selector outcome: eligible"),
        ("point_in_time_correction", "setup", None, "Selector outcome: eligible"),
    ],
)
def test_each_scenario_reaches_its_state(runs, scenario_id, kind, blocker, selector):
    model = runs[scenario_id].model
    card = model.vwap_lane.cards[0]
    assert card.card_kind.value == kind
    if blocker is None:
        assert card.readiness_label == "READY FOR MANUAL REVIEW"
    else:
        assert any(blocker in b for b in card.blockers)
    review = model.contract_reviews[card.card_id]
    assert review.outcome_label == selector
    if scenario_id in ("selector_unavailable", "stale_evidence"):
        assert review.availability_label == "Contract selector unavailable"


# --- Storage reconstructs the same bundle and card ---------------------------------------------


def test_stored_records_rebuild_to_the_same_bundles_and_cards(runs):
    for run in runs.values():
        store = run.store.store
        for bundle_id in run.store.bundles.values():
            assert reproduce_bundle(store, bundle_id).bundle_id == bundle_id
        for card in _cards(run):
            with store.read_connection() as conn:
                row = store.load_bundle(conn, card.decision_context_bundle_id)
                ctx = store.card_context(
                    conn, rec.parse_bundle(row["record_json"]), R.SETUP_REGISTRY_ID
                )
            validate_setup_card(card, ctx)


def test_dashboard_views_match_the_stored_bundles_and_cards(runs):
    for run in runs.values():
        (card,) = _cards(run)
        detail = run.model.cards[card.card_id]
        assert detail.strip.bundle_id == card.decision_context_bundle_id
        assert detail.summary.readiness is card.evidence_readiness
        review = run.model.contract_reviews[card.card_id]
        assert review.bundle_id == card.decision_context_bundle_id
        if card.contracts is not None:
            assert review.eligible == tuple(card.contracts.eligible_contracts)
            assert review.research_only == tuple(card.contracts.research_only_contracts)


def test_a_restarted_process_reconstructs_the_same_views(runs):
    run = runs["ready_for_manual_review"]
    spec = SPEC["ready_for_manual_review"]
    reopened = run.store.reopen()  # a new store object with full startup verification
    assert reopened.verified is not None
    again = read_back(
        run.store,
        scenario_id=spec.scenario_id,
        title=spec.title,
        summary=spec.summary,
        market="market",
    )
    assert present(again) == run.model


# --- Point-in-time correction --------------------------------------------------------------------


def test_a_later_correction_leaves_the_earlier_bundle_unchanged(runs):
    run = runs["point_in_time_correction"]
    store = run.store.store
    earlier, later = run.store.bundles["market"], run.store.bundles["market_corrected"]
    original = run.store.named_items["original_observation"]
    assert reproduce_bundle(store, earlier).bundle_id == earlier  # still rebuilds exactly
    with store.read_connection() as conn:
        b1 = rec.parse_bundle(store.load_bundle(conn, earlier)["record_json"])
        b2 = rec.parse_bundle(store.load_bundle(conn, later)["record_json"])
    corrected = [i for i in _items(run) if i.revision.supersedes_item_id == original.item_id]
    assert len(corrected) == 1 and corrected[0].revision.revision_reason.value == "source_revision"
    assert original.item_id in {e.item_id for e in b1.entries}
    assert corrected[0].item_id not in {e.item_id for e in b1.entries}
    assert corrected[0].item_id in {e.item_id for e in b2.entries}
    assert original.item_id not in {e.item_id for e in b2.entries}  # superseded
    assert run.model.market.strip.bundle_id == later
    assert run.model.cards[run.store.cards[0]].strip.bundle_id == run.store.bundles["setup"]


# --- Holdout, selector status and inference --------------------------------------------------


def test_holdout_spy_price_evidence_is_refused_before_storage(runs):
    run = runs["holdout_refused"]
    assert run.store.refusals == ["holdout_restricted"]
    for any_run in runs.values():
        with any_run.store.store.read_connection() as conn:
            hits = conn.execute(
                "SELECT count(*) FROM evidence_items WHERE record_json LIKE '%2026-10-14%'"
            ).fetchone()[0]
        assert hits == 0
        assert not any(is_holdout_restricted(i, any_run.store.registry) for i in _items(any_run))


def test_selector_statuses_are_preserved_exactly(runs):
    for run in runs.values():
        (card,) = _cards(run)
        if card.contracts is None:
            continue
        stored = next(i for i in _items(run) if i.item_id == card.contracts.selector_result_item_id)
        assert stored.payload_schema_id == CONTRACT_SELECTOR_PAYLOAD_ID
        result = ContractSelectorResult.model_validate_json(canonical_json_bytes(stored.payload))
        assert card.contracts.selector_outcome is result.status
        assert card.contracts.eligible_contracts == sorted(
            c.contract_symbol for c in result.eligible_contracts
        )
        assert card.contracts.research_only_contracts == sorted(
            c.contract_symbol for c in result.research_only_contracts
        )
        assert not set(card.contracts.eligible_contracts) & set(
            card.contracts.research_only_contracts
        )


def test_inference_is_never_promoted(runs):
    seen = False
    for run in runs.values():
        items = {i.item_id: i for i in _items(run)}
        for item in items.values():
            if item.evidence_kind in (
                EvidenceKind.CONFIRMED_FACT,
                EvidenceKind.DETERMINISTIC_CALCULATION,
            ):
                stack = list(item.provenance.parent_evidence_ids)
                while stack:
                    parent = items[stack.pop()]
                    assert parent.evidence_kind is not EvidenceKind.CURRENT_INFERENCE
                    stack.extend(parent.provenance.parent_evidence_ids)
        for card in _cards(run):
            for citation in (*card.supporting_citations, *card.contradicting_citations):
                assert EvidenceKind.CURRENT_INFERENCE not in citation.cited_kinds
        for detail in run.model.cards.values():
            seen |= bool(detail.inference_context)
            assert all(
                r.kind is not EvidenceKind.CURRENT_INFERENCE
                for r in (*detail.supporting, *detail.contradicting)
            )
    assert seen  # a labelled inference really is shown, as context only


# --- Determinism -------------------------------------------------------------------------------


def test_identical_runs_give_identical_identities_and_views(tmp_path):
    spec = SPEC["ready_for_manual_review"]
    first_store, first = run_scenario(tmp_path / "a", spec)
    second_store, second = run_scenario(tmp_path / "b", spec)
    assert first_store.bundles == second_store.bundles
    assert first_store.cards == second_store.cards
    assert {i.item_id for i in _items(Run(first_store, None))} == {
        i.item_id for i in _items(Run(second_store, None))
    }
    assert present(first) == present(second)


# --- Boundaries --------------------------------------------------------------------------------


def test_workspaces_are_temporary_and_removed(tmp_path):
    result = demo.build_slice(parent=tmp_path, specs=[SPEC["no_qualified_setup"]])
    assert list(tmp_path.iterdir()) == []  # no store, checkpoint, key or registry left
    assert isinstance(result.models["no_qualified_setup"], DashboardModel)
    with temporary_workspace(tmp_path) as workspace:
        assert workspace.exists()
    assert not workspace.exists()


@pytest.mark.parametrize(
    ("location", "reason"),
    [
        (REPO_ROOT, "repository_directory_refused"),
        (REPO_ROOT / "data", "repository_directory_refused"),
        (REPO_ROOT / "market_intelligence", "repository_directory_refused"),
        (Path.home() / "not-temporary", "non_temporary_directory_refused"),
    ],
)
def test_real_and_non_temporary_locations_are_refused(location, reason):
    with pytest.raises(SliceRefusal) as excinfo:
        require_temporary_parent(location)
    assert excinfo.value.reason == reason
    with pytest.raises(SliceRefusal):
        demo.build_slice(parent=location, specs=[])


def test_live_credentials_and_the_real_data_path_are_refused(tmp_path, monkeypatch):
    from pydantic import SecretStr

    live = Settings(_env_file=None, project_data_path=tmp_path, alpaca_api_key=SecretStr("x" * 20))
    with pytest.raises(SliceRefusal) as excinfo:
        require_offline_settings(live)
    assert excinfo.value.reason == "live_credentials_refused"
    with pytest.raises(SliceRefusal):
        offline_settings(REPO_ROOT / "data")
    monkeypatch.setenv("ALPACA_API_KEY", "x" * 20)  # the environment cannot inject one
    assert offline_settings(tmp_path).alpaca_api_key is None


def test_no_database_provider_model_or_network_access(tmp_path, monkeypatch):
    real_connect = duckdb.connect
    temp_root = str(Path(tempfile.gettempdir()).resolve())

    def temporary_only(database=":memory:", *args, **kwargs):
        assert str(Path(database).resolve()).startswith(temp_root), "non-temporary database"
        return real_connect(database, *args, **kwargs)

    def refuse(*args, **kwargs):
        raise AssertionError("the slice must not open a connection")

    monkeypatch.setattr(duckdb, "connect", temporary_only)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    store, scenario = run_scenario(tmp_path, SPEC["missing_evidence"])
    present(scenario)


_FORBIDDEN = (
    "httpx",
    "requests",
    "socket",
    "urllib",
    "openai",
    "anthropic",
    "duckdb",
    "market_intelligence.model_clients",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.storage",
    "market_intelligence.news_pipeline",
)
_CONNECTOR_DATACLASSES = {
    "market_intelligence.data_connectors.alpaca_bars": {"Bar"},
    "market_intelligence.data_connectors.fred_macro_data": {
        "FredObservation",
        "FredSeriesMetadata",
    },
}


@pytest.mark.parametrize("module", sorted(p.name for p in PACKAGE.glob("*.py")))
def test_package_imports_no_provider_model_or_network_code(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
            if node.module.startswith("market_intelligence.data_connectors"):
                # Only the connectors' validated source dataclasses, never a client.
                allowed = _CONNECTOR_DATACLASSES.get(node.module, set())
                assert {a.name for a in node.names} <= allowed, (module, node.module)
                continue
        for name in names:
            assert not any(name == f or name.startswith(f + ".") for f in _FORBIDDEN), (
                module,
                name,
            )


def test_slice_creates_no_production_registry_or_definition():
    from market_intelligence.setup_cards.definitions import REGISTERED_SETUP_DEFINITIONS

    assert REGISTERED_SETUP_DEFINITIONS == ()
    assert not (REPO_ROOT / "registries").exists()
    assert R.SETUP_REGISTRY.definitions == []
    for definitions in R.SYNTHETIC_DEFINITIONS.values():
        assert all(d.setup_definition_id.startswith("synthetic-only-") for d in definitions)


def test_the_dashboard_renders_the_read_back_scenarios(runs, monkeypatch):
    st = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    result = demo.SliceResult(
        order=scenarios.SCENARIO_IDS,
        titles={s.scenario_id: s.title for s in SCENARIOS},
        models={k: v.model for k, v in runs.items()},
        summaries={},
    )
    monkeypatch.setattr(demo, "build_slice", lambda: result)
    st.cache_resource.clear()
    try:
        at = AppTest.from_file(str(PACKAGE / "dashboard_app.py"), default_timeout=120).run()
        assert not at.exception and not at.error
        text = " ".join(str(m.value) for m in (*at.markdown, *at.caption, *at.info))
        assert runs["ready_for_manual_review"].store.bundles["market"][-8:] in text
    finally:
        st.cache_resource.clear()


def test_demo_summary_lists_every_scenario(runs):
    result = demo.SliceResult(
        order=scenarios.SCENARIO_IDS,
        titles={s.scenario_id: s.title for s in SCENARIOS},
        models={k: v.model for k, v in runs.items()},
        summaries={
            k: demo.ScenarioSummary(
                k, dict(v.store.bundles), tuple(v.store.cards), tuple(v.store.refusals)
            )
            for k, v in runs.items()
        },
    )
    lines = demo._summary_lines(result)
    assert len(lines) == 1 + len(SCENARIOS)
    assert any("refused before storage: holdout_restricted" in line for line in lines)
    assert json.dumps(lines)  # plain text only
