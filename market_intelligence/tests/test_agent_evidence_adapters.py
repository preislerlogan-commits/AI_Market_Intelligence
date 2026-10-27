"""Offline Evidence Envelope adapters for the Market Evidence Agent, News
Analyst and Macro Analyst: valid inference items, citations and lineage,
abstention, malformed-output refusal, inference boundaries, and the
assistant/dashboard display. Synthetic reports and the dashboard prototype's
synthetic registry only; no model, provider or database."""

from __future__ import annotations

import copy
import dataclasses
from datetime import timedelta
from typing import get_args

import pytest

from market_intelligence.agents import macro_analyst, market_evidence_agent, news_analyst
from market_intelligence.assistant import explain as A
from market_intelligence.dashboard import fixtures as fx
from market_intelligence.dashboard import labels as L
from market_intelligence.dashboard.adapter import present
from market_intelligence.evidence.contracts import MISSING_EVIDENCE_PAYLOAD, EvidenceItem
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    DataQuality,
    DirectionalContent,
    EvidenceKind,
    InferenceSupport,
    ProductionPath,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.registry import BundleRequirement
from market_intelligence.evidence.validation import validate_item
from market_intelligence.evidence_adapters import agents as ag
from market_intelligence.evidence_adapters import payloads as P

REGISTRY = fx.make_registry()


def _sources() -> dict[str, EvidenceItem]:
    base = fx._setup_items()
    return {
        "bar": base["bar"],
        "calc": base["calc"],
        "macro": fx.macro_fact_item(),
        "news": fx.news_missing_item(REGISTRY),
        "inference": base["inference"],
    }


SOURCES = _sources()
SOURCE_BUNDLE = fx._build(
    REGISTRY, BundlePurpose.LIVE_MARKET_STATE, fx.T0 + timedelta(seconds=20), list(SOURCES.values())
)
KEYS = {
    ag.MARKET_EVIDENCE_PRODUCER: {"bar:latest": "bar", "calc:vwap_distance": "calc"},
    ag.MACRO_PRODUCER: {"fred:CPIAUCSL:2026-12-01": "macro"},
    ag.NEWS_PRODUCER: {"news:window": "news"},
}
ADAPT = {
    ag.MARKET_EVIDENCE_PRODUCER: (ag.adapt_market_evidence_report, fx.MARKET_REPORT),
    ag.MACRO_PRODUCER: (ag.adapt_macro_report, fx.MACRO_REPORT),
    ag.NEWS_PRODUCER: (ag.adapt_news_report, fx.NEWS_REPORT),
}


def _source(agent: str, *, keys=None, model: bool = True, **changes) -> ag.AgentSourceContext:
    fields = dict(
        bundle=SOURCE_BUNDLE.bundle,
        items=SOURCE_BUNDLE.items,
        citation_keys={k: SOURCES[v].item_id for k, v in (keys or KEYS[agent]).items()},
        agent_generated_at_utc=fx.AGENT_RUN_AT,
        model_run=fx._model_ref() if model else None,
    )
    fields.update(changes)
    return ag.AgentSourceContext(**fields)


def _run(agent: str, report=None, *, source=None, registry=REGISTRY, configuration=None):
    adapt, default = ADAPT[agent]
    # A completed report came from a model call; these synthetic abstentions did not.
    model = default["status"] == "completed"
    return adapt(
        default if report is None else report,
        source=source or _source(agent, model=model),
        context=fx._adapter_context(registry, configuration or ag.AGENT_CONFIGURATIONS[agent]),
    )


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _changed(report: dict, **changes) -> dict:
    out = copy.deepcopy(report)
    out.update(changes)
    return out


# --- Valid outputs ------------------------------------------------------------------------------


def test_a_completed_report_becomes_one_labelled_inference_item_per_observation():
    items = _run(ag.MARKET_EVIDENCE_PRODUCER)
    assert len(items) == 2
    report = fx.MARKET_REPORT
    for index, item in enumerate(sorted(items, key=lambda i: i.payload["claim_index"])):
        observation = report["observations"][index]
        assert item.evidence_kind is EvidenceKind.CURRENT_INFERENCE
        assert item.payload_schema_id == P.MARKET_EVIDENCE_OBSERVATION
        assert item.provenance.producer_id == ag.MARKET_EVIDENCE_PRODUCER
        assert item.provenance.producer_version == "1.0.0"  # from the registry
        assert (
            item.provenance.configuration_identity
            == ag.AGENT_CONFIGURATIONS[ag.MARKET_EVIDENCE_PRODUCER]
        )
        assert item.provenance.production_path is ProductionPath.MODEL_GENERATED
        assert item.effective_at_utc == fx.AGENT_RUN_AT
        assert item.payload["agent_generated_at_utc"].startswith("2027-01-12T15:00:30")
        assert item.payload["statement"] == observation["statement"]
        assert item.payload["cited_evidence_keys"] == observation["evidence_ids"]
        expected = sorted(
            {
                SOURCES[KEYS[ag.MARKET_EVIDENCE_PRODUCER][k]].item_id
                for k in observation["evidence_ids"]
            }
        )
        assert item.provenance.parent_evidence_ids == expected
        assert item.inference_basis.input_evidence_ids == expected
        # Non-directional, never machine-decision eligible, never self-graded.
        assert item.payload["non_directional"] is True
        assert item.payload["directional_assessment"] == "not_performed"
        assert item.payload["trade_recommendation"] == "not_performed"
        assert item.inference_basis.directional_content is DirectionalContent.NONE
        assert item.machine_decision_eligible is False
        assert item.payload["model_reported_evidence_quality"] == "limited"
        assert item.quality.data_quality is DataQuality.NOT_ASSESSED
        assert item.quality.inference_support is InferenceSupport.NOT_ASSESSED
        assert item.quality.calibrated_probability is None
        assert EvidenceItem.model_validate_json(item.model_dump_json()) == item
        validate_item(item, REGISTRY, payload_models=fx.PAYLOAD_MODELS)
    assert [i.item_id for i in _run(ag.MARKET_EVIDENCE_PRODUCER)] == [i.item_id for i in items]


def test_the_agents_own_validated_report_objects_are_accepted():
    market = market_evidence_agent.MarketEvidenceReport(**fx.MARKET_REPORT)
    macro = macro_analyst.MacroAnalystReport(**fx.MACRO_REPORT)
    news = news_analyst.NewsAnalystReport(**fx.NEWS_REPORT)
    for agent, report in (
        (ag.MARKET_EVIDENCE_PRODUCER, market),
        (ag.MACRO_PRODUCER, macro),
        (ag.NEWS_PRODUCER, news),
    ):
        assert [i.item_id for i in _run(agent, report)] == [i.item_id for i in _run(agent)]
    macro_item = _run(ag.MACRO_PRODUCER)[0]
    assert macro_item.payload_schema_id == P.MACRO_CLAIM
    assert macro_item.payload["series_id"] == "CPIAUCSL"


def test_payload_vocabularies_track_the_agents_own_literals():
    fields = P.MarketEvidenceObservation.model_fields
    assert set(get_args(fields["category"].annotation)) == set(
        get_args(market_evidence_agent.ObservationCategory)
    )
    news = P.NewsEventClaim.model_fields
    assert set(get_args(news["event_type"].annotation)) == set(get_args(news_analyst.EventType))
    assert set(get_args(news["relevance"].annotation)) == set(get_args(news_analyst.Relevance))
    macro = P.MacroClaimInference.model_fields
    assert set(get_args(macro["economic_category"].annotation)) == set(
        get_args(macro_analyst.EconomicCategory)
    )


def test_completed_claims_are_inference_and_abstentions_are_missing_evidence():
    for agent in (ag.MARKET_EVIDENCE_PRODUCER, ag.MACRO_PRODUCER):
        assert {i.evidence_kind for i in _run(agent)} == {EvidenceKind.CURRENT_INFERENCE}
    (item,) = _run(ag.NEWS_PRODUCER)  # the agent produced no interpretation
    assert item.evidence_kind is EvidenceKind.MISSING_EVIDENCE
    assert item.payload_schema_id == MISSING_EVIDENCE_PAYLOAD
    assert item.inference_basis is None and item.machine_decision_eligible is False
    assert item.availability.state is AvailabilityState.UNAVAILABLE
    # The absence record only: agent, reason, expected subject and query, as-of
    # time. No conclusion, direction, probability or value.
    assert item.payload == {
        "checked_at_utc": "2027-01-12T15:00:30.000000Z",
        "expected_component": None,
        "expected_producer_id": ag.NEWS_PRODUCER,
        "expected_subject_id": "instrument:us_equity:SPY",
        "query_sha256": item.payload["query_sha256"],
        "reason_code": "news_missing",
    }
    assert item.display_text == [
        "Abstained: no interpretation was produced. Reasons: news_missing.",
        "Limitation: " + fx.NEWS_REPORT["limitations"][0],
    ]
    assert item.provenance.parent_evidence_ids == [SOURCES["news"].item_id]
    assert not any(r.reference_type == "model_run" for r in item.provenance.source_references)
    unregistered = _changed(fx.NEWS_REPORT, abstained_reasons=["made_up_reason"])
    assert _reason(_run, ag.NEWS_PRODUCER, unregistered) == "unknown_reason_code"


def test_a_post_model_abstention_is_refused_rather_than_losing_its_model_run():
    # Missing evidence cannot carry the model-run reference: fail closed.
    after_model = _source(ag.NEWS_PRODUCER, model=True)
    assert _reason(_run, ag.NEWS_PRODUCER, source=after_model) == (
        "post_model_abstention_not_representable"
    )


def test_an_abstention_can_never_satisfy_a_required_input():
    with pytest.raises(ValueError):
        BundleRequirement(
            requirement_id="req.news.spy",
            producer_id=ag.NEWS_PRODUCER,
            producer_versions=["1.0.0"],
            evidence_kind=EvidenceKind.MISSING_EVIDENCE,
            payload_schema_id=MISSING_EVIDENCE_PAYLOAD,
            primary_subject_id="instrument:us_equity:SPY",
            configuration_identities=[ag.AGENT_CONFIGURATIONS[ag.NEWS_PRODUCER]],
        )


def test_registered_abstention_reasons_are_the_agents_own():
    for agent, module in (
        (ag.MARKET_EVIDENCE_PRODUCER, market_evidence_agent),
        (ag.NEWS_PRODUCER, news_analyst),
        (ag.MACRO_PRODUCER, macro_analyst),
    ):
        own = {
            value
            for name, value in vars(module).items()
            if name.startswith(("PREFLIGHT_REASON_", "ABSTAIN_REASON_"))
        }
        assert set(fx.AGENT_ABSTENTION_REASONS[agent]) == own, agent


# --- Refusals: citations, lineage, malformed output, permissions --------------------------------


def test_uncited_claims_missing_parents_and_outside_evidence_are_refused():
    agent = ag.MARKET_EVIDENCE_PRODUCER
    uncited = copy.deepcopy(fx.MARKET_REPORT)
    uncited["observations"][0]["evidence_ids"] = []
    assert _reason(_run, agent, uncited) == "uncited_claim"
    unknown_key = copy.deepcopy(fx.MARKET_REPORT)
    unknown_key["observations"][0]["evidence_ids"] = ["bar:unsupplied"]
    assert _reason(_run, agent, unknown_key) == "missing_parent"
    outside = {"bar:latest": fx.bar_item(fx.T0 - timedelta(hours=2)).item_id}
    source = _source(agent, citation_keys={**_source(agent).citation_keys, **outside})
    assert _reason(_run, agent, source=source) == "evidence_outside_bundle"
    inference_parent = _source(
        agent, keys={"bar:latest": "inference", "calc:vwap_distance": "calc"}
    )
    assert _reason(_run, agent, source=inference_parent) == "parent_kind_not_allowed"
    late = _source(agent, agent_generated_at_utc=fx.T0 - timedelta(minutes=5))
    assert _reason(_run, agent, source=late) == "parent_generated_later"


@pytest.mark.parametrize(
    "change",
    [
        {"unexpected_field": "x"},
        {"directional_assessment": "bullish"},
        {"trade_recommendation": "buy"},
        {"status": "completed", "observations": []},
        {"status": "abstained"},  # abstained, yet with observations and no reasons
        {"evidence_quality": "insufficient"},
        {
            "observations": [
                {"category": "sentiment", "statement": "x", "evidence_ids": ["bar:latest"]}
            ]
        },
    ],
)
def test_malformed_output_is_refused(change):
    assert _reason(_run, ag.MARKET_EVIDENCE_PRODUCER, _changed(fx.MARKET_REPORT, **change)) == (
        "malformed_output"
    )
    assert _reason(_run, ag.MARKET_EVIDENCE_PRODUCER, "not a report") == "malformed_output"


def test_directional_text_is_refused_by_the_shared_policy():
    report = copy.deepcopy(fx.MARKET_REPORT)
    report["observations"][0]["statement"] = "SPY will rise from here."
    assert _reason(_run, ag.MARKET_EVIDENCE_PRODUCER, report) == "directional_content"
    macro = copy.deepcopy(fx.MACRO_REPORT)
    macro["macro_claims"][0]["conditional_mechanism"] = "This is bullish for equities."
    assert _reason(_run, ag.MACRO_PRODUCER, macro) == "directional_content"


def test_unknown_configuration_producer_and_widened_permissions_are_refused():
    agent = ag.MACRO_PRODUCER
    assert _reason(_run, agent, configuration="cfg1_" + "0" * 64) == "unknown_configuration"
    without = REGISTRY.model_copy(
        update={"producers": [p for p in REGISTRY.producers if p.producer_id != agent]}
    )
    assert _reason(_run, agent, registry=without) == "unknown_producer"
    widened = REGISTRY.model_copy(
        update={
            "producers": [
                p.model_copy(
                    update={
                        "emission_rules": [
                            r.model_copy(update={"machine_decision_eligible": True})
                            for r in p.emission_rules
                        ]
                    }
                )
                if p.producer_id == agent
                else p
                for p in REGISTRY.producers
            ]
        }
    )
    assert _reason(_run, agent, registry=widened) == "agent_permissions_invalid"
    no_model = _source(agent, model=False)
    assert _reason(_run, agent, source=no_model) == "model_run_missing"


def test_agent_output_can_never_be_a_fact_calculation_or_research_result():
    for agent in ADAPT:
        producer = REGISTRY.producer(agent)
        assert {r.evidence_kind for r in producer.emission_rules} == {
            EvidenceKind.CURRENT_INFERENCE,
            EvidenceKind.MISSING_EVIDENCE,
        }
        assert not any(r.machine_decision_eligible for r in producer.emission_rules)
    assert REGISTRY.inference_input_authorizations == []


# --- Assistant and dashboard ---------------------------------------------------------------------


def _selection(scenario_id: str = "agent_interpretations") -> A.Selection:
    scenario = fx.build_scenarios()[scenario_id]
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


def test_agent_questions_answer_only_with_labelled_cited_inference():
    selection = _selection()
    for question in (A.QuestionId.MARKET_EVIDENCE, A.QuestionId.MACRO_CONDITIONS):
        answer = _ask(selection, question)
        assert answer.answerable and answer.statements == ()
        assert answer.inference
        for statement in answer.inference:
            assert statement.label is A.StatementLabel.INFERENCE
            (cited,) = statement.evidence_citation.cited_item_ids
            assert selection.items[cited].evidence_kind is EvidenceKind.CURRENT_INFERENCE
        assert L.forbidden_terms_in(" ".join(s.text for s in answer.inference)) == []
    # The abstention is shown as missing evidence, never as inference.
    news = _ask(selection, A.QuestionId.NEWS_CONTEXT)
    assert "produced no interpretation" in news.headline and news.inference == ()
    assert {s.label for s in news.statements} == {A.StatementLabel.MISSING_EVIDENCE}
    assert "Reasons: news_missing" in news.statements[0].text


def test_interpretation_basis_cites_the_underlying_evidence_by_its_own_kind():
    selection = _selection()
    answer = _ask(selection, A.QuestionId.INTERPRETATION_BASIS)
    cited = {s.evidence_citation.cited_item_ids[0]: s.label for s in answer.statements}
    assert cited == {
        SOURCES["bar"].item_id: A.StatementLabel.FACT,
        SOURCES["calc"].item_id: A.StatementLabel.CALCULATION,
        fx.macro_fact_item().item_id: A.StatementLabel.FACT,
        fx.news_missing_item(REGISTRY).item_id: A.StatementLabel.MISSING_EVIDENCE,
        _run(ag.NEWS_PRODUCER)[0].item_id: A.StatementLabel.MISSING_EVIDENCE,
    }
    assert all(s.label is A.StatementLabel.INFERENCE for s in answer.inference)
    assert any("limitation" in s.text for s in answer.inference)


def test_agent_outputs_cannot_qualify_a_setup_or_change_contract_status():
    selection = _selection()
    ready = _selection("ready_for_manual_review")
    agent_ids = {i for i, item in selection.items.items() if item.provenance.producer_id in ADAPT}
    assert len(agent_ids) == 4
    card = selection.card
    for citation in (*card.supporting_citations, *card.contradicting_citations):
        assert not agent_ids & set(citation.cited_item_ids)
    assert card.qualification is ready.card.qualification
    assert card.evidence_readiness is ready.card.evidence_readiness
    assert card.contracts.selector_outcome is ready.card.contracts.selector_outcome
    assert card.contracts.eligible_contracts == ready.card.contracts.eligible_contracts
    supporting = _ask(selection, A.QuestionId.SUPPORTING)
    assert not agent_ids & {s.evidence_citation.cited_item_ids[0] for s in supporting.statements}


def test_without_agent_output_the_questions_are_not_answerable():
    selection = _selection("ready_for_manual_review")
    for question in A.AGENT_QUESTIONS:
        answer = _ask(selection, question)
        assert not answer.answerable and answer.headline.startswith(A.NOT_ANSWERABLE)


def test_the_dashboard_presents_the_agent_scenario():
    model = present(fx.build_scenarios()["agent_interpretations"])
    (panel,) = model.assistant.values()
    assert panel.unavailable_reason is None
    news = panel.answers[A.QuestionId.NEWS_CONTEXT.value]
    assert news.inference == () and all(s.label == "missing_evidence" for s in news.statements)
    assert dataclasses.asdict(model.contract_reviews[panel.card_id])["eligible"]
