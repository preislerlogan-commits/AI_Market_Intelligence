"""Intrinsic ``setup-card-1`` contract rules (docs/SETUP_CARD_CONTRACT.md §3-§5).
Synthetic data only."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.contract_selection.contracts import SelectorStatus
from market_intelligence.evidence.contracts import EvidenceCitation
from market_intelligence.evidence.enums import CitationRole, EvidenceKind
from market_intelligence.setup_cards import enums as card_enums
from market_intelligence.setup_cards.builder import build_setup_card
from market_intelligence.setup_cards.contracts import (
    MANUAL_DECISION_STATEMENT,
    CardContractSection,
    CardRevision,
    SetupCard,
    SetupCardContent,
    compute_card_id,
    execution_field_names,
    seal_card,
)
from market_intelligence.setup_cards.definitions import (
    REGISTERED_SETUP_DEFINITIONS,
    SetupDefinition,
    SetupEvaluationPayload,
)
from market_intelligence.setup_cards.enums import (
    CardKind,
    CardRevisionReason,
    EvidenceReadiness,
    Lane,
    NoSetupReason,
    ReadinessBlocker,
    SelectorAvailability,
    SetupLifecycle,
    SetupQualification,
)
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import setup_card_fixtures as s


def _qualified_card():
    items = s.core_items()
    ctx = s.card_context(*items.values(), f.selector_item())
    return build_setup_card(
        ctx,
        card_kind=CardKind.SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=s.PROVENANCE,
        setup_subject_id=s.SETUP_SUBJECT,
        supporting_ids=[items["bar"].item_id],
        context_ids=[items["research"].item_id],
        selector_requested=True,
    )


def _content(card, **changes):
    fields = {name: getattr(card, name) for name in SetupCardContent.model_fields}
    fields.update(changes)
    return SetupCardContent(**fields)


CARD = _qualified_card()


# --- Vocabularies ----------------------------------------------------------------------


def test_seven_separate_vocabularies_exist_and_are_distinct():
    vocabularies = [
        card_enums.EvidenceReadiness,
        card_enums.SetupQualification,
        card_enums.SetupLifecycle,
        card_enums.DisplayStatus,
        card_enums.SelectorOutcome,
        card_enums.SelectorAvailability,
        card_enums.CardResearchStatus,
    ]
    assert len({v.__name__ for v in vocabularies}) == 7
    assert "qualified" not in {m.value for m in SetupLifecycle}
    assert {m.value for m in SetupLifecycle} == {
        "observed",
        "developing",
        "evaluation_complete",
        "invalidated",
        "expired",
    }


def test_selector_outcome_is_exactly_the_selectors_four_values():
    assert card_enums.SelectorOutcome is SelectorStatus
    assert {m.value for m in SelectorStatus} == {
        "eligible",
        "no_eligible_contracts",
        "research_only",
        "indeterminate",
    }
    availability = {m.value for m in SelectorAvailability}
    assert availability == {"not_requested", "available", "unavailable"}
    assert not availability & {m.value for m in SelectorStatus}


# --- Presentation object, never an order ------------------------------------------------


def test_card_has_no_execution_surface():
    assert execution_field_names() == set()
    for forbidden in ("order", "quantity", "position_size", "broker_account", "execute"):
        with pytest.raises(ValidationError):
            _content(CARD, **{forbidden: "x"})


def test_manual_decision_statement_is_fixed():
    assert CARD.manual_decision_statement == MANUAL_DECISION_STATEMENT
    with pytest.raises(ValidationError):
        _content(CARD, manual_decision_statement="Buy now.")


def test_rank_interpretation_and_scenarios_must_stay_empty():
    with pytest.raises(ValidationError):
        _content(CARD, deterministic_rank={"position": 1})
    with pytest.raises(ValidationError):
        _content(CARD, interpretation={"note": "x"})
    with pytest.raises(ValidationError):
        _content(CARD, scenario_relations=["up"])


def test_cards_are_immutable_and_strict():
    with pytest.raises(ValidationError):
        CARD.qualification = SetupQualification.NOT_QUALIFIED
    with pytest.raises(ValidationError):
        _content(CARD, lane="vwap_reversion")  # a string, not the enum


# --- Identity -------------------------------------------------------------------------------


def test_identity_is_content_addressed_and_excludes_generation_time():
    assert CARD.card_id.startswith("scd1_")
    later = _content(
        CARD,
        provenance=CARD.provenance.model_copy(
            update={"generated_at_utc": CARD.provenance.generated_at_utc + timedelta(hours=1)}
        ),
    )
    assert compute_card_id(later) == CARD.card_id
    assert compute_card_id(_content(CARD, lane=Lane.VWAP_REVERSION)) == CARD.card_id


def test_forged_card_id_is_refused():
    fields = {n: getattr(CARD, n) for n in SetupCardContent.model_fields}
    with pytest.raises(ValidationError):
        SetupCard(**fields, card_id="scd1_" + "0" * 64)


def test_card_round_trips_through_json():
    again = SetupCard.model_validate_json(CARD.model_dump_json())
    assert again == CARD
    assert json.loads(CARD.model_dump_json())["schema_version"] == "setup-card-1"


# --- Kind rules ---------------------------------------------------------------------------


def test_setup_card_requires_subject_definition_and_lifecycle():
    with pytest.raises(ValidationError):
        _content(CARD, setup_subject_id=None)
    with pytest.raises(ValidationError):
        _content(CARD, lifecycle_state=None)
    with pytest.raises(ValidationError):
        _content(CARD, no_setup_reason=NoSetupReason.CRITERIA_NOT_MET)


def test_setup_subject_lane_must_match():
    with pytest.raises(ValidationError):
        _content(
            CARD,
            setup_subject_id="setup:trend_continuation:synthetic_only_def:SPY:20270112T150000Z",
        )


def test_no_qualified_setup_never_requests_the_selector():
    with pytest.raises(ValidationError):
        _content(
            CARD,
            card_kind=CardKind.NO_QUALIFIED_SETUP,
            setup_subject_id=None,
            setup_definition_id=None,
            lifecycle_state=None,
            no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
            qualification=SetupQualification.NOT_QUALIFIED,
        )


# --- Lifecycle and qualification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("lifecycle", "qualification", "ok"),
    [
        (SetupLifecycle.OBSERVED, SetupQualification.NOT_EVALUATED, True),
        (SetupLifecycle.DEVELOPING, SetupQualification.NOT_EVALUATED, True),
        (SetupLifecycle.OBSERVED, SetupQualification.QUALIFIED, False),
        (SetupLifecycle.DEVELOPING, SetupQualification.NOT_QUALIFIED, False),
        (SetupLifecycle.EVALUATION_COMPLETE, SetupQualification.QUALIFIED, True),
        (SetupLifecycle.EVALUATION_COMPLETE, SetupQualification.NOT_QUALIFIED, True),
        (SetupLifecycle.EVALUATION_COMPLETE, SetupQualification.INDETERMINATE, True),
        (SetupLifecycle.EVALUATION_COMPLETE, SetupQualification.NOT_EVALUATED, False),
        (SetupLifecycle.INVALIDATED, SetupQualification.QUALIFIED, True),
        (SetupLifecycle.EXPIRED, SetupQualification.NOT_EVALUATED, True),
    ],
)
def test_lifecycle_never_implies_qualification_but_must_be_consistent(lifecycle, qualification, ok):
    def build():
        return SetupEvaluationPayload(
            setup_definition_id=s.SYNTHETIC_DEFINITION_ID,
            lane=Lane.VWAP_REVERSION,
            setup_subject_id=s.SETUP_SUBJECT,
            lifecycle_state=lifecycle,
            qualification=qualification,
        )

    if ok:
        assert build()
    else:
        with pytest.raises(ValidationError):
            build()


def test_qualification_item_required_iff_qualified_or_not_qualified():
    with pytest.raises(ValidationError):
        _content(CARD, qualification_item_id=None)
    with pytest.raises(ValidationError):
        _content(
            CARD,
            qualification=SetupQualification.INDETERMINATE,
        )


def test_trend_lane_is_never_evaluated():
    with pytest.raises(ValidationError):
        _content(
            CARD,
            card_kind=CardKind.NO_QUALIFIED_SETUP,
            lane=Lane.TREND_CONTINUATION,
            setup_subject_id=None,
            setup_definition_id=None,
            lifecycle_state=None,
            no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
            qualification=SetupQualification.NOT_QUALIFIED,
            selector_availability=SelectorAvailability.NOT_REQUESTED,
            contracts=None,
        )


def test_no_live_setup_definition_is_registered():
    assert REGISTERED_SETUP_DEFINITIONS == ()
    with pytest.raises(ValidationError):
        SetupDefinition(
            setup_definition_id="trend-def",
            lane=Lane.TREND_CONTINUATION,
            evaluation_producer_id="synthetic_x",
        )
    with pytest.raises(ValidationError):
        SetupDefinition(
            setup_definition_id="x-def",
            lane=Lane.VWAP_REVERSION,
            evaluation_producer_id="synthetic_x",
            inference_allowed=True,
        )


# --- Readiness --------------------------------------------------------------------------------


def test_manual_review_ready_equals_evidence_readiness():
    with pytest.raises(ValidationError):
        _content(CARD, manual_review_ready=False)
    with pytest.raises(ValidationError):
        _content(CARD, evidence_readiness=EvidenceReadiness.BLOCKED)
    with pytest.raises(ValidationError):
        _content(
            CARD,
            readiness_blockers=[ReadinessBlocker.STALE_REQUIRED_EVIDENCE],
        )


def test_no_qualified_setup_card_needs_a_not_qualified_conclusion():
    base = dict(
        card_kind=CardKind.NO_QUALIFIED_SETUP,
        setup_subject_id=None,
        setup_definition_id=None,
        lifecycle_state=None,
        selector_availability=SelectorAvailability.NOT_REQUESTED,
        contracts=None,
    )
    # not_evaluated means no authorized conclusion exists: never a card.
    with pytest.raises(ValidationError):
        _content(
            CARD,
            **base,
            qualification=SetupQualification.NOT_EVALUATED,
            qualification_item_id=None,
            no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
        )
    # Retired availability reasons are unknown values, refused outright.
    for reason in ("evidence_not_ready", "lane_not_authorized", "lane_not_researched"):
        with pytest.raises(ValidationError):
            _content(
                CARD,
                **base,
                qualification=SetupQualification.NOT_QUALIFIED,
                no_setup_reason=reason,
            )
    # Blocked evidence is no conclusion: a blocked no_qualified_setup card is
    # refused by the contract itself.
    with pytest.raises(ValidationError):
        _content(
            CARD,
            **base,
            qualification=SetupQualification.NOT_QUALIFIED,
            no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
            evidence_readiness=EvidenceReadiness.BLOCKED,
            readiness_blockers=[ReadinessBlocker.STALE_REQUIRED_EVIDENCE],
            manual_review_ready=False,
        )
    assert _content(
        CARD,
        **base,
        qualification=SetupQualification.NOT_QUALIFIED,
        no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
    )


# --- Citations and selector ---------------------------------------------------------------


def test_inference_cannot_be_supporting_or_contradicting_evidence():
    citation = CARD.supporting_citations[0]
    with_inference = citation.model_copy(
        update={"cited_kinds": sorted([*citation.cited_kinds, EvidenceKind.CURRENT_INFERENCE])}
    )
    with pytest.raises(ValidationError):
        _content(CARD, supporting_citations=[with_inference])


def test_citation_lists_hold_only_their_role_and_bundle():
    citation = CARD.supporting_citations[0]
    with pytest.raises(ValidationError):
        _content(CARD, contradicting_citations=[citation.model_copy(update={"claim_index": 50})])
    other_bundle = EvidenceCitation(
        bundle_id="evb1_" + "0" * 64,
        claim_index=60,
        cited_item_ids=citation.cited_item_ids,
        citation_role=CitationRole.SUPPORTS_CLAIM,
        cited_kinds=citation.cited_kinds,
    )
    with pytest.raises(ValidationError):
        _content(CARD, supporting_citations=[citation, other_bundle])


def test_contract_section_present_iff_selector_available():
    with pytest.raises(ValidationError):
        _content(CARD, selector_availability=SelectorAvailability.UNAVAILABLE)
    with pytest.raises(ValidationError):
        _content(CARD, contracts=None)


def test_contract_section_keeps_eligible_and_research_only_apart():
    section = CARD.contracts
    with pytest.raises(ValidationError):
        CardContractSection(
            **{
                **section.model_dump(),
                "selector_outcome": section.selector_outcome,
                "feed": section.feed,
                "rejection_counts": section.rejection_counts,
                "research_only_contracts": [section.eligible_contracts[0]],
            }
        )


def test_revision_contract_rules():
    with pytest.raises(ValidationError):
        CardRevision(revision_number=2, revision_reason=CardRevisionReason.NEWER_BUNDLE)
    with pytest.raises(ValidationError):
        CardRevision(
            revision_number=1,
            supersedes_card_id=CARD.card_id,
            revision_reason=CardRevisionReason.ORIGINAL,
        )


def test_seal_card_is_reproducible():
    assert seal_card(_content(CARD)).card_id == CARD.card_id
