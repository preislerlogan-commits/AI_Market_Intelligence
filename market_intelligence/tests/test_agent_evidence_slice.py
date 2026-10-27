"""Agent evidence through the offline vertical slice: synthetic source
evidence -> agent adapters -> Evidence Store -> reproduced bundle -> setup
card -> assistant and dashboard views built from read-back records.
Temporary stores and synthetic data only."""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import duckdb
import pytest

from market_intelligence.assistant import explain as A
from market_intelligence.dashboard.adapter import present
from market_intelligence.evidence.contracts import MISSING_EVIDENCE_PAYLOAD
from market_intelligence.evidence.enums import BundlePurpose, ConsumerId, EvidenceKind
from market_intelligence.evidence.validation import validate_citations
from market_intelligence.evidence_adapters import agents as ag
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.reads import read_card, reproduce_bundle
from market_intelligence.evidence_store.store_io import select_rows
from market_intelligence.integration import scenarios as S
from market_intelligence.integration.pipeline import ScenarioStore, read_back
from market_intelligence.integration.workspace import store_paths

SPEC = {s.scenario_id: s for s in S.SCENARIOS}
AGENTS = {ag.MARKET_EVIDENCE_PRODUCER, ag.MACRO_PRODUCER, ag.NEWS_PRODUCER}


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("agent_slice")
    return {
        sid: S.run_scenario(workspace, SPEC[sid])
        for sid in ("agent_interpretations", "ready_for_manual_review")
    }


def _stored_items(store: ScenarioStore) -> dict:
    with store.store.read_connection() as conn:
        rows = select_rows(conn, "SELECT record_json FROM evidence_items")
    items = [rec.parse_item(r["record_json"]) for r in rows]
    return {i.item_id: i for i in items}


def _read_back(store: ScenarioStore):
    spec = SPEC["agent_interpretations"]
    return read_back(
        store, scenario_id=spec.scenario_id, title=spec.title, summary=spec.summary, market="market"
    )


def _selection(scenario) -> A.Selection:
    record = scenario.vwap.history[-1]
    return A.Selection(
        record.card,
        record.source.bundle,
        record.source.items,
        record.source.conflicts,
        scenario.registry,
    )


def _ask(selection: A.Selection, question: A.QuestionId) -> A.Answer:
    return A.answer(
        selection,
        question.value,
        card_id=selection.card.card_id,
        bundle_id=selection.bundle.bundle_id,
    )


# --- Round trip, lineage and determinism -------------------------------------------------------


def test_agent_items_and_their_lineage_are_stored(runs):
    store, _ = runs["agent_interpretations"]
    items = _stored_items(store)
    agent = {i: item for i, item in items.items() if item.provenance.producer_id in AGENTS}
    kinds = sorted(
        (item.provenance.producer_id, item.evidence_kind.value) for item in agent.values()
    )
    assert kinds == [
        (ag.MACRO_PRODUCER, "current_inference"),
        (ag.MARKET_EVIDENCE_PRODUCER, "current_inference"),
        (ag.MARKET_EVIDENCE_PRODUCER, "current_inference"),
        (ag.NEWS_PRODUCER, "missing_evidence"),
    ]
    for item in agent.values():
        assert item.provenance.parent_evidence_ids  # every parent is itself stored
        assert set(item.provenance.parent_evidence_ids) <= set(items)
        assert item.machine_decision_eligible is False
    # The agents were given a stored bundle, which still rebuilds identically.
    agent_input = store.bundles["agent_input"]
    entries = {e.item_id for e in reproduce_bundle(store.store, agent_input).entries}
    assert all(set(i.provenance.parent_evidence_ids) <= entries for i in agent.values())


def test_a_restarted_process_reconstructs_the_same_views(runs):
    store, scenario = runs["agent_interpretations"]
    before = present(scenario)
    store.reopen()  # a fresh process: full startup verification
    after = present(_read_back(store))
    assert after == before
    for name, bundle_id in store.bundles.items():
        assert reproduce_bundle(store.store, bundle_id).bundle_id == bundle_id, name
    (card_id,) = store.cards
    assert read_card(store.store, card_id).card.card_id == card_id


def test_identities_are_deterministic(runs, tmp_path):
    store, _ = runs["agent_interpretations"]
    again, _ = S.run_scenario(tmp_path, SPEC["agent_interpretations"])
    assert set(_stored_items(again)) == set(_stored_items(store))
    assert again.bundles == store.bundles and again.cards == store.cards


# --- Assistant and dashboard from read-back records ---------------------------------------------


def test_assistant_answers_cite_only_the_stored_setup_bundle(runs):
    store, scenario = runs["agent_interpretations"]
    selection = _selection(scenario)
    assert selection.bundle.bundle_id == store.bundles["setup"]
    stored = reproduce_bundle(store.store, store.bundles["setup"])
    assert selection.bundle == stored
    for question in A.AGENT_QUESTIONS:
        answer = _ask(selection, question)
        assert answer.answerable
        validate_citations(
            stored,
            [s.evidence_citation for s in (*answer.statements, *answer.inference)],
        )
    model = present(scenario)
    (panel,) = model.assistant.values()
    assert panel.bundle_id == store.bundles["setup"] and panel.unavailable_reason is None


def test_agent_inference_is_shown_separately_and_the_abstention_as_missing_evidence(runs):
    _, scenario = runs["agent_interpretations"]
    selection = _selection(scenario)
    agent_ids = {i for i, item in selection.items.items() if item.provenance.producer_id in AGENTS}
    model = present(scenario)
    (detail,) = model.cards.values()
    for row in (
        *detail.supporting,
        *detail.contradicting,
        *detail.context,
        *detail.inference_context,
    ):
        assert row.item_id not in agent_ids  # the card never cites agent output
    for question in (A.QuestionId.MARKET_EVIDENCE, A.QuestionId.MACRO_CONDITIONS):
        answer = _ask(selection, question)
        assert answer.statements == ()
        assert {s.label for s in answer.inference} == {A.StatementLabel.INFERENCE}
    news = _ask(selection, A.QuestionId.NEWS_CONTEXT)
    assert news.inference == ()
    assert {s.label for s in news.statements} == {A.StatementLabel.MISSING_EVIDENCE}
    (abstention,) = [
        selection.items[i]
        for i in agent_ids
        if selection.items[i].provenance.producer_id == ag.NEWS_PRODUCER
    ]
    assert abstention.evidence_kind is EvidenceKind.MISSING_EVIDENCE
    assert abstention.payload_schema_id == MISSING_EVIDENCE_PAYLOAD
    assert abstention.payload["reason_code"] == "no_articles_returned"
    # It never satisfies a required input: no requirement matched it.
    entry = next(e for e in selection.bundle.entries if e.item_id == abstention.item_id)
    assert not entry.required


def test_setup_and_contract_results_are_unchanged(runs):
    _, agent_scenario = runs["agent_interpretations"]
    _, ready_scenario = runs["ready_for_manual_review"]
    card = agent_scenario.vwap.history[-1].card
    ready = ready_scenario.vwap.history[-1].card
    for field in (
        "qualification",
        "lifecycle_state",
        "evidence_readiness",
        "readiness_blockers",
        "selector_availability",
        "manual_review_ready",
    ):
        assert getattr(card, field) == getattr(ready, field), field
    assert card.contracts.selector_outcome is ready.contracts.selector_outcome
    assert card.contracts.eligible_contracts == ready.contracts.eligible_contracts
    assert card.contracts.research_only_contracts == ready.contracts.research_only_contracts
    assert card.contracts.rejection_counts == ready.contracts.rejection_counts


# --- Fail closed ----------------------------------------------------------------------------------


def test_agent_items_without_their_stored_parents_are_refused(runs, tmp_path):
    store, _ = runs["agent_interpretations"]
    macro = [
        i for i in _stored_items(store).values() if i.provenance.producer_id == ag.MACRO_PRODUCER
    ]
    fresh = ScenarioStore.create(store_paths(tmp_path, "fresh"), S.START)
    with pytest.raises(StoreRefusal) as excinfo:
        fresh.at(S._t(15, 3)).ingest(macro)
    assert excinfo.value.reason == "missing_parent"


@pytest.mark.parametrize(
    ("statement", "finding"),
    [
        (
            "UPDATE evidence_items SET record_json = record_json || ' ' WHERE item_id = ?",
            "record_hash_mismatch",
        ),
        ("DELETE FROM evidence_items WHERE item_id = ?", "commit_chain_mismatch"),
    ],
)
def test_tampered_or_missing_stored_parents_fail_closed_on_restart(tmp_path, statement, finding):
    store, _ = S.run_scenario(tmp_path, SPEC["agent_interpretations"])
    parent = store.named_items["original_observation"]  # the Macro Analyst's cited fact
    with duckdb.connect(str(store.store.database_path)) as conn:
        conn.execute(statement, [parent.item_id])
    with pytest.raises(IntegrityStop) as excinfo:
        store.reopen()
    assert excinfo.value.reason == finding


def test_no_provider_model_real_database_or_network_access(tmp_path, monkeypatch):
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
    _, scenario = S.run_scenario(tmp_path, SPEC["agent_interpretations"])
    present(scenario)
    grant = scenario.registry.consumer_grant(ConsumerId.ASSISTANT)
    assert not grant.machine_decision_mode_allowed
    assert grant.purposes == [BundlePurpose.SETUP_DETAIL]
