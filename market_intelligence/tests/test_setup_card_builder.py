"""Builder and bundle-bound validation for ``setup-card-1``
(docs/SETUP_CARD_CONTRACT.md §4). Synthetic, in-memory data only.

The synthetic setup definition passed to these contexts exercises the future
contract; production's setup-definition table is empty, which the tests also
prove.
"""

from __future__ import annotations

import ast
import inspect
from datetime import timedelta

import pytest

from market_intelligence.evidence.bundle import RecordedConflict
from market_intelligence.evidence.contracts import (
    EvidenceConflictContent,
    seal_conflict,
)
from market_intelligence.evidence.enums import (
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    DetectionMethod,
    EvidenceKind,
    FreshnessState,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.setup_cards import validation as card_validation
from market_intelligence.setup_cards.builder import (
    NoQualifiedSetupResult,
    build_no_qualified_setup_card,
    build_setup_card,
)
from market_intelligence.setup_cards.contracts import SetupCardContent, seal_card
from market_intelligence.setup_cards.enums import (
    CardKind,
    EvidenceReadiness,
    Lane,
    LaneQualificationAvailability,
    NoSetupReason,
    ReadinessBlocker,
    SelectorAvailability,
    SelectorOutcome,
    SetupLifecycle,
    SetupQualification,
)
from market_intelligence.setup_cards.validation import CardContext, validate_setup_card
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import setup_card_fixtures as s


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _setup_card(ctx, items, **overrides):
    kwargs = dict(
        card_kind=CardKind.SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=s.PROVENANCE,
        setup_subject_id=s.SETUP_SUBJECT,
        supporting_ids=[items["bar"].item_id],
        context_ids=[items["research"].item_id],
    )
    kwargs.update(overrides)
    return build_setup_card(ctx, **kwargs)


def _no_setup_card(ctx, reason=None, lane=Lane.VWAP_REVERSION, **overrides):
    return build_setup_card(
        ctx,
        card_kind=CardKind.NO_QUALIFIED_SETUP,
        lane=lane,
        provenance=s.PROVENANCE,
        no_setup_reason=reason,
        **overrides,
    )


def _no_setup_result(ctx, lane=Lane.VWAP_REVERSION, **overrides):
    return build_no_qualified_setup_card(ctx, lane=lane, provenance=s.PROVENANCE, **overrides)


def _assert_no_card(result, availability):
    assert isinstance(result, NoQualifiedSetupResult)
    assert result.availability is availability
    assert result.card is None


def _tampered(card, **changes):
    fields = {name: getattr(card, name) for name in SetupCardContent.model_fields}
    fields.update(changes)
    return seal_card(SetupCardContent(**fields))


# --- A synthetic qualified card (future contract behavior) ------------------------------


def test_synthetic_qualified_card_is_built_and_valid():
    items = s.core_items()
    ctx = s.card_context(*items.values(), f.selector_item())
    card = _setup_card(ctx, items, selector_requested=True)
    assert card.qualification is SetupQualification.QUALIFIED
    assert card.lifecycle_state is SetupLifecycle.EVALUATION_COMPLETE
    assert card.qualification_item_id == items["evaluation"].item_id
    assert card.setup_definition_id == s.SYNTHETIC_DEFINITION_ID
    assert card.manual_review_ready and card.evidence_readiness is EvidenceReadiness.READY
    assert card.decision_context_bundle_id == ctx.bundle.bundle_id
    assert card.effective_at_utc == ctx.bundle.as_of_utc
    validate_setup_card(card, ctx)


def test_empty_production_definition_table_blocks_both_card_kinds():
    items = s.core_items()
    conclusion = s.lane_conclusion()
    ctx = s.card_context(
        *items.values(), conclusion, definitions=card_validation.REGISTERED_SETUP_DEFINITIONS
    )
    assert card_validation.REGISTERED_SETUP_DEFINITIONS == ()
    assert card_validation.lane_qualification_availability(ctx, Lane.VWAP_REVERSION) is (
        LaneQualificationAvailability.NOT_AUTHORIZED
    )
    # No setup card ...
    assert _reason(_setup_card, ctx, items) == "setup_definition_not_registered"
    # ... and no no_qualified_setup card: an absent authority is not a
    # negative conclusion.
    assert _reason(_no_setup_card, ctx) == "lane_qualification_not_available"
    result = _no_setup_result(ctx)
    _assert_no_card(result, LaneQualificationAvailability.NOT_AUTHORIZED)
    assert result.readiness_blockers == ()


def test_qualification_requires_a_registered_deterministic_producer():
    items = s.core_items()
    impostor = s.evaluation_item(producer_id=f.CALC)
    items["evaluation"] = impostor
    ctx = s.card_context(*items.values())
    assert _reason(_setup_card, ctx, items) == "qualification_from_unregistered_producer"


def test_lifecycle_and_qualification_come_only_from_the_cited_evaluation():
    items = s.core_items()
    items["evaluation"] = s.evaluation_item(
        lifecycle=SetupLifecycle.DEVELOPING, qualification=SetupQualification.NOT_EVALUATED
    )
    ctx = s.card_context(*items.values())
    card = _setup_card(ctx, items)
    assert card.lifecycle_state is SetupLifecycle.DEVELOPING
    assert card.qualification is SetupQualification.NOT_EVALUATED
    assert card.qualification_item_id is None
    assert items["evaluation"].item_id in {
        i for c in card.context_citations for i in c.cited_item_ids
    }
    forged = _tampered(card, lifecycle_state=SetupLifecycle.OBSERVED)
    assert _reason(validate_setup_card, forged, ctx) == "card_disagrees_with_evaluation"


# --- no_qualified_setup ------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason", [NoSetupReason.CRITERIA_NOT_MET, NoSetupReason.NO_CANDIDATE_EVALUATED]
)
def test_cited_synthetic_not_qualified_conclusion_produces_a_card(reason):
    items = s.core_items()
    conclusion = s.lane_conclusion(reason=reason)
    ctx = s.card_context(items["bar"], items["research"], items["clock"], conclusion)
    result = _no_setup_result(ctx)
    assert result.availability is LaneQualificationAvailability.AVAILABLE
    assert result.readiness_blockers == ()
    card = result.card
    assert card is not None
    assert card.card_kind is CardKind.NO_QUALIFIED_SETUP
    assert card.qualification is SetupQualification.NOT_QUALIFIED
    assert card.no_setup_reason is reason
    assert card.qualification_item_id == conclusion.item_id
    assert card.selector_availability is SelectorAvailability.NOT_REQUESTED
    assert card.contracts is None
    assert card.manual_review_ready  # a valid, neutral result can be presented
    assert card.evidence_readiness is EvidenceReadiness.READY
    assert card.readiness_blockers == []
    validate_setup_card(card, ctx)


def test_no_qualified_setup_with_not_evaluated_is_refused():
    with pytest.raises(ValueError):
        s.evaluation_item(
            setup_subject_id=None,
            lifecycle=SetupLifecycle.EVALUATION_COMPLETE,
            qualification=SetupQualification.NOT_EVALUATED,
            no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
        )
    items = s.core_items()
    ctx = s.card_context(items["bar"], items["research"], items["clock"], s.lane_conclusion())
    card = _no_setup_card(ctx)
    with pytest.raises(ValueError):
        _tampered(card, qualification=SetupQualification.NOT_EVALUATED, qualification_item_id=None)


def test_lane_conclusion_needs_a_completed_evaluation():
    for lifecycle in (SetupLifecycle.OBSERVED, SetupLifecycle.DEVELOPING):
        with pytest.raises(ValueError):
            s.lane_conclusion(lifecycle=lifecycle)
    for lifecycle in (SetupLifecycle.INVALIDATED, SetupLifecycle.EXPIRED):
        assert s.lane_conclusion(lifecycle=lifecycle)


def test_no_qualified_setup_without_a_registered_definition_is_refused():
    items = s.core_items()
    conclusion = s.lane_conclusion()
    ctx = s.card_context(items["bar"], items["research"], items["clock"], conclusion)
    card = _no_setup_card(ctx)
    unregistered = s.card_context(
        items["bar"], items["research"], items["clock"], conclusion, definitions=()
    )
    assert _reason(_no_setup_card, unregistered) == "lane_qualification_not_available"
    assert _reason(validate_setup_card, card, unregistered) == "setup_definition_not_registered"


def test_no_qualified_setup_without_a_cited_lane_evaluation_is_refused():
    items = s.core_items()
    ctx = s.card_context(*items.values())  # a setup-level evaluation, no lane conclusion
    assert card_validation.lane_qualification_availability(ctx, Lane.VWAP_REVERSION) is (
        LaneQualificationAvailability.UNAVAILABLE
    )
    assert _reason(_no_setup_card, ctx) == "lane_qualification_not_available"
    _assert_no_card(_no_setup_result(ctx), LaneQualificationAvailability.UNAVAILABLE)
    concluded = s.card_context(items["bar"], items["research"], items["clock"], s.lane_conclusion())
    card = _no_setup_card(concluded)
    uncited = _tampered(card, context_citations=[])
    assert _reason(validate_setup_card, uncited, concluded) == "uncited_evaluation_conclusion"


def test_lane_conclusion_from_an_unregistered_producer_is_refused():
    items = s.core_items()
    impostor = s.lane_conclusion(producer_id=f.CALC)
    ctx = s.card_context(items["bar"], items["research"], items["clock"], impostor)
    assert card_validation.lane_qualification_availability(ctx, Lane.VWAP_REVERSION) is (
        LaneQualificationAvailability.UNAVAILABLE
    )
    _assert_no_card(_no_setup_result(ctx), LaneQualificationAvailability.UNAVAILABLE)


def test_two_lane_conclusions_are_unavailable_never_resolved_by_picking_one():
    items = s.core_items()
    first = s.lane_conclusion(reason=NoSetupReason.CRITERIA_NOT_MET)
    second = s.lane_conclusion(reason=NoSetupReason.NO_CANDIDATE_EVALUATED)
    ctx = s.card_context(items["bar"], items["research"], items["clock"], first, second)
    _assert_no_card(_no_setup_result(ctx), LaneQualificationAvailability.UNAVAILABLE)
    assert _reason(_no_setup_card, ctx) == "lane_qualification_not_available"


def test_reason_cannot_contradict_the_cited_evaluation():
    items = s.core_items()
    conclusion = s.lane_conclusion(reason=NoSetupReason.CRITERIA_NOT_MET)
    ctx = s.card_context(items["bar"], items["research"], items["clock"], conclusion)
    assert (
        _reason(_no_setup_card, ctx, reason=NoSetupReason.NO_CANDIDATE_EVALUATED)
        == "reason_contradicts_evaluation"
    )
    card = _no_setup_card(ctx)
    flipped = _tampered(card, no_setup_reason=NoSetupReason.NO_CANDIDATE_EVALUATED)
    assert _reason(validate_setup_card, flipped, ctx) == "card_disagrees_with_evaluation"


RETIRED_REASONS = ["lane_not_authorized", "lane_not_researched", "evidence_not_ready"]


def test_no_setup_reason_has_only_the_two_conclusions():
    assert {r.value for r in NoSetupReason} == {"criteria_not_met", "no_candidate_evaluated"}


@pytest.mark.parametrize("reason", RETIRED_REASONS)
def test_retired_reasons_are_unknown_values(reason):
    with pytest.raises(ValueError):
        NoSetupReason(reason)
    with pytest.raises(ValueError):
        s.lane_conclusion(reason=reason)
    items = s.core_items()
    ctx = s.card_context(items["bar"], items["research"], items["clock"], s.lane_conclusion())
    card = _no_setup_card(ctx)
    with pytest.raises(ValueError):
        _tampered(card, no_setup_reason=reason)


def test_unavailable_qualification_returns_no_card_not_a_false_negative():
    items = s.core_items()
    # A definition is registered, but its producer left no evaluation.
    ctx = s.card_context(items["bar"], items["research"], items["clock"])
    assert card_validation.lane_qualification_availability(ctx, Lane.VWAP_REVERSION) is (
        LaneQualificationAvailability.UNAVAILABLE
    )
    _assert_no_card(_no_setup_result(ctx), LaneQualificationAvailability.UNAVAILABLE)
    assert _reason(_no_setup_card, ctx) == "lane_qualification_not_available"


def test_trend_lane_cannot_produce_no_qualified_setup():
    items = s.core_items()
    ctx = s.card_context(items["bar"], items["research"], items["clock"], s.lane_conclusion())
    assert card_validation.lane_qualification_availability(ctx, Lane.TREND_CONTINUATION) is (
        LaneQualificationAvailability.NOT_AUTHORIZED
    )
    _assert_no_card(
        _no_setup_result(ctx, lane=Lane.TREND_CONTINUATION),
        LaneQualificationAvailability.NOT_AUTHORIZED,
    )
    assert _reason(_no_setup_card, ctx, lane=Lane.TREND_CONTINUATION) == (
        "lane_qualification_not_available"
    )
    card = _no_setup_card(ctx)
    with pytest.raises(ValueError):
        _tampered(card, lane=Lane.TREND_CONTINUATION)


# --- Blocked evidence is no conclusion: no no_qualified_setup card -------------------------


def _critical_conflict(items):
    twin_time = f.bar_item(f.T0 - timedelta(minutes=5))
    conflict = seal_conflict(
        EvidenceConflictContent(
            conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
            involved_item_ids=sorted([items["bar"].item_id, twin_time.item_id]),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=f.DETECTOR,
            detector_version="1.0.0",
            severity=ConflictSeverity.CRITICAL,
            status=ConflictStatus.UNRESOLVED,
            evaluated_as_of_utc=f.T0 + timedelta(seconds=30),
            detected_at_utc=f.T0 + timedelta(seconds=31),
        )
    )
    recorded = RecordedConflict(conflict=conflict, recorded_at_utc=f.T0 + timedelta(seconds=40))
    return twin_time, recorded


def _blocked_no_setup_case(case):
    """A cited, valid ``not_qualified`` lane conclusion beside unready evidence."""
    items = s.core_items()
    bar, research, clock = items["bar"], items["research"], items["clock"]
    conclusion = s.lane_conclusion()
    cite = [bar.item_id]
    if case == "stale_bar":
        later = f.T0 + timedelta(minutes=20)
        ctx = s.card_context(bar, research, conclusion, f.clock_item(later), as_of=later)
        blocker = ReadinessBlocker.STALE_REQUIRED_EVIDENCE
    elif case == "missing_bar":
        ctx = s.card_context(research, clock, conclusion)
        cite, blocker = [], ReadinessBlocker.MISSING_REQUIRED_EVIDENCE
    elif case == "ambiguous_requirement":
        ctx = s.card_context(bar, research, clock, conclusion, f.bar_item(close="500.2"))
        blocker = ReadinessBlocker.AMBIGUOUS_REQUIREMENT
    elif case == "ambiguous_clock_facts":
        other_clock = f.clock_item(f.T0, offset_ms=5000)
        ctx = s.card_context(bar, research, clock, conclusion, other_clock)
        blocker = ReadinessBlocker.AMBIGUOUS_CLOCK_FACTS
    elif case == "critical_conflict":
        twin_time, recorded = _critical_conflict(items)
        ctx = s.card_context(bar, research, clock, conclusion, twin_time, conflicts=[recorded])
        blocker = ReadinessBlocker.UNRESOLVED_CRITICAL_CONFLICT
    else:  # holdout_restricted_bar
        ctx = s.card_context(f.bar_item(f.HOLDOUT_T0), research, clock, conclusion)
        cite, blocker = [], ReadinessBlocker.HOLDOUT_RESTRICTED
    return ctx, cite, blocker


BLOCKED_CASES = [
    "stale_bar",
    "missing_bar",
    "ambiguous_requirement",
    "ambiguous_clock_facts",
    "critical_conflict",
    "holdout_restricted_bar",
]


@pytest.mark.parametrize("case", BLOCKED_CASES)
def test_blocked_evidence_returns_evidence_blocked_and_no_card(case):
    ctx, cite, blocker = _blocked_no_setup_case(case)
    # The conclusion itself is valid and from the registered producer ...
    assert card_validation.usable_lane_conclusion(ctx, Lane.VWAP_REVERSION) is not None
    # ... but the evidence is not ready, so there is no conclusion to present.
    result = _no_setup_result(ctx, supporting_ids=cite)
    _assert_no_card(result, LaneQualificationAvailability.EVIDENCE_BLOCKED)
    assert blocker in result.readiness_blockers
    assert list(result.readiness_blockers) == sorted(result.readiness_blockers)
    # The lower-level builder refuses before building anything.
    assert _reason(_no_setup_card, ctx, supporting_ids=cite) == (
        "no_setup_conclusion_evidence_blocked"
    )


@pytest.mark.parametrize("case", BLOCKED_CASES)
@pytest.mark.parametrize("reason", list(NoSetupReason))
def test_blocked_evidence_never_becomes_a_no_setup_reason(case, reason):
    ctx, cite, _ = _blocked_no_setup_case(case)
    # Even when the caller asserts a valid reason, blocked evidence yields no card.
    assert _reason(_no_setup_card, ctx, reason=reason, supporting_ids=cite) == (
        "no_setup_conclusion_evidence_blocked"
    )
    result = _no_setup_result(ctx, supporting_ids=cite)
    assert result.card is None
    assert not hasattr(result, "no_setup_reason")
    # A ready card relabelled as blocked is refused by the contract itself.
    items = s.core_items()
    ready_ctx = s.card_context(
        items["bar"], items["research"], items["clock"], s.lane_conclusion(reason=reason)
    )
    card = _no_setup_result(ready_ctx).card
    assert card is not None and card.no_setup_reason is reason
    with pytest.raises(ValueError):
        _tampered(
            card,
            evidence_readiness=EvidenceReadiness.BLOCKED,
            readiness_blockers=[ReadinessBlocker.STALE_REQUIRED_EVIDENCE],
            manual_review_ready=False,
        )


def test_no_setup_result_invariants():
    items = s.core_items()
    ctx = s.card_context(items["bar"], items["research"], items["clock"], s.lane_conclusion())
    card = _no_setup_result(ctx).card
    a = LaneQualificationAvailability
    with pytest.raises(EvidenceValidationError):
        NoQualifiedSetupResult(a.AVAILABLE)  # available needs a card
    for state in (a.NOT_AUTHORIZED, a.UNAVAILABLE, a.EVIDENCE_BLOCKED):
        with pytest.raises(EvidenceValidationError):
            NoQualifiedSetupResult(
                state,
                card=card,
                readiness_blockers=(ReadinessBlocker.STALE_REQUIRED_EVIDENCE,),
            )
    with pytest.raises(EvidenceValidationError):
        NoQualifiedSetupResult(a.EVIDENCE_BLOCKED)  # blocked needs its blockers
    with pytest.raises(EvidenceValidationError):
        NoQualifiedSetupResult(
            a.UNAVAILABLE, readiness_blockers=(ReadinessBlocker.STALE_REQUIRED_EVIDENCE,)
        )
    setup_items = s.core_items()
    setup_card = _setup_card(s.card_context(*setup_items.values()), setup_items)
    with pytest.raises(EvidenceValidationError):
        NoQualifiedSetupResult(a.AVAILABLE, card=setup_card)


def test_setup_cards_still_represent_developing_and_blocked_observations():
    items = s.core_items()
    items["evaluation"] = s.evaluation_item(
        lifecycle=SetupLifecycle.DEVELOPING, qualification=SetupQualification.NOT_EVALUATED
    )
    later = f.T0 + timedelta(minutes=20)
    ctx = s.card_context(
        items["bar"], items["research"], items["evaluation"], f.clock_item(later), as_of=later
    )
    card = _setup_card(ctx, items)
    assert card.card_kind is CardKind.SETUP
    assert card.lifecycle_state is SetupLifecycle.DEVELOPING
    assert card.qualification is SetupQualification.NOT_EVALUATED
    assert card.evidence_readiness is EvidenceReadiness.BLOCKED
    assert ReadinessBlocker.STALE_REQUIRED_EVIDENCE in card.readiness_blockers
    assert not card.manual_review_ready
    validate_setup_card(card, ctx)


# --- Readiness blockers (derived from the bundle state) ---------------------------------


def test_stale_required_evidence_blocks():
    items = s.core_items()
    later = f.T0 + timedelta(minutes=20)
    ctx = s.card_context(
        items["bar"], items["research"], items["evaluation"], f.clock_item(later), as_of=later
    )
    card = _setup_card(ctx, items)
    assert ReadinessBlocker.STALE_REQUIRED_EVIDENCE in card.readiness_blockers
    assert not card.manual_review_ready
    stale_ids = {n.item_id for n in card.non_current_entries if n.state is FreshnessState.STALE}
    assert items["bar"].item_id in stale_ids


def test_missing_required_evidence_blocks():
    items = s.core_items()
    ctx = s.card_context(items["research"], items["clock"], items["evaluation"])
    card = _setup_card(ctx, items, supporting_ids=[])
    assert ReadinessBlocker.MISSING_REQUIRED_EVIDENCE in card.readiness_blockers
    assert card.missing_requirements == list(ctx.bundle.missing_required_producers)


def test_unknown_freshness_blocks():
    items = s.core_items()
    ctx = s.card_context(items["bar"], items["research"], items["evaluation"])
    card = _setup_card(ctx, items)
    assert ReadinessBlocker.UNKNOWN_FRESHNESS in card.readiness_blockers


def test_ambiguous_clock_facts_block():
    items = s.core_items()
    other_clock = f.clock_item(f.T0, offset_ms=5000)
    ctx = s.card_context(*items.values(), other_clock)
    card = _setup_card(ctx, items)
    assert ReadinessBlocker.AMBIGUOUS_CLOCK_FACTS in card.readiness_blockers


def test_ambiguous_requirement_blocks():
    items = s.core_items()
    twin = f.bar_item(close="500.2")
    ctx = s.card_context(*items.values(), twin)
    card = _setup_card(ctx, items)
    assert ReadinessBlocker.AMBIGUOUS_REQUIREMENT in card.readiness_blockers
    assert card.ambiguous_requirements == list(ctx.bundle.ambiguous_requirements)


def test_unresolved_critical_conflict_blocks():
    items = s.core_items()
    twin_time = f.bar_item(f.T0 - timedelta(minutes=5))
    conflict = seal_conflict(
        EvidenceConflictContent(
            conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
            involved_item_ids=sorted([items["bar"].item_id, twin_time.item_id]),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=f.DETECTOR,
            detector_version="1.0.0",
            severity=ConflictSeverity.CRITICAL,
            status=ConflictStatus.UNRESOLVED,
            evaluated_as_of_utc=f.T0 + timedelta(seconds=30),
            detected_at_utc=f.T0 + timedelta(seconds=31),
        )
    )
    ctx = s.card_context(
        *items.values(),
        twin_time,
        conflicts=[
            RecordedConflict(conflict=conflict, recorded_at_utc=f.T0 + timedelta(seconds=40))
        ],
    )
    card = _setup_card(ctx, items)
    assert ReadinessBlocker.UNRESOLVED_CRITICAL_CONFLICT in card.readiness_blockers
    assert [n.conflict_id for n in card.conflicts] == [conflict.conflict_id]
    assert card.conflicts[0].status is ConflictStatus.UNRESOLVED
    hidden = _tampered(card, conflicts=[])
    assert _reason(validate_setup_card, hidden, ctx) == "conflicts_mismatch"


def test_holdout_restriction_blocks():
    items = s.core_items()
    restricted_bar = f.bar_item(f.HOLDOUT_T0)
    ctx = s.card_context(restricted_bar, items["research"], items["clock"], items["evaluation"])
    card = _setup_card(ctx, items, supporting_ids=[])
    assert ReadinessBlocker.HOLDOUT_RESTRICTED in card.readiness_blockers
    assert restricted_bar.item_id not in {e.item_id for e in ctx.bundle.entries}


def test_no_card_inside_the_holdout_window():
    at = f.HOLDOUT_T0
    conclusion = s.lane_conclusion(at=at)
    ctx = s.card_context(
        f.bar_item(at),
        f.research_item(),
        f.clock_item(at),
        conclusion,
        as_of=at + timedelta(minutes=1),
        recorded_at=at,
    )
    # The price-bearing evaluation is itself restricted, so qualification is
    # unavailable and no card (of either kind) is produced.
    _assert_no_card(_no_setup_result(ctx), LaneQualificationAvailability.UNAVAILABLE)


def test_manual_review_ready_is_not_derived_from_machine_decision_ready():
    source = inspect.getsource(card_validation)
    tree = ast.parse(source)
    names = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "machine_decision_ready" not in names


def test_not_qualified_card_can_be_ready_for_manual_review():
    items = s.core_items()
    items["evaluation"] = s.evaluation_item(qualification=SetupQualification.NOT_QUALIFIED)
    ctx = s.card_context(*items.values())
    card = _setup_card(ctx, items)
    assert card.qualification is SetupQualification.NOT_QUALIFIED
    assert card.manual_review_ready


# --- Citations and kinds -------------------------------------------------------------------


def test_inference_only_as_labelled_context():
    items = s.core_items()
    inference = f.inference_item([items["bar"]], f.T0 + timedelta(seconds=30))
    ctx = s.card_context(*items.values(), inference)
    assert (
        _reason(_setup_card, ctx, items, supporting_ids=[items["bar"].item_id, inference.item_id])
        == "inference_only_as_context"
    )
    card = _setup_card(ctx, items, context_ids=[items["research"].item_id, inference.item_id])
    kinds = {k for c in card.context_citations for k in c.cited_kinds}
    assert EvidenceKind.CURRENT_INFERENCE in kinds
    assert card.qualification_item_id != inference.item_id


def test_citation_outside_the_bundle_is_refused():
    items = s.core_items()
    ctx = s.card_context(*items.values())
    stranger = f.bar_item(f.T0 + timedelta(hours=3))
    assert (
        _reason(_setup_card, ctx, items, supporting_ids=[stranger.item_id])
        == "citation_outside_bundle"
    )


def test_card_from_another_bundle_is_refused():
    items = s.core_items()
    ctx = s.card_context(*items.values())
    card = _setup_card(ctx, items)
    other_ctx = s.card_context(*items.values(), as_of=s.AS_OF + timedelta(seconds=10))
    assert _reason(validate_setup_card, card, other_ctx) == "card_bundle_mismatch"


def test_context_must_supply_every_bundle_item_and_conflict():
    items = s.core_items()
    ctx = s.card_context(*items.values())
    with pytest.raises(EvidenceValidationError):
        CardContext(
            bundle=ctx.bundle,
            items={items["bar"].item_id: items["bar"]},
            conflicts={},
            registry=ctx.registry,
        )


@pytest.mark.parametrize(
    "field",
    [
        "missing_requirements",
        "non_current_entries",
        "research_references",
        "observed_at_utc",
        "readiness",
    ],
)
def test_derived_lists_cannot_be_edited(field):
    items = s.core_items()
    later = f.T0 + timedelta(minutes=20)
    ctx = s.card_context(
        items["bar"], items["research"], items["evaluation"], f.clock_item(later), as_of=later
    )
    card = _setup_card(ctx, items)
    changes = {
        "missing_requirements": {"missing_requirements": []},
        "non_current_entries": {"non_current_entries": []},
        "research_references": {"research_references": []},
        "observed_at_utc": {"observed_at_utc": None},
        "readiness": {
            "evidence_readiness": EvidenceReadiness.READY,
            "readiness_blockers": [],
            "manual_review_ready": True,
        },
    }[field]
    tampered = _tampered(card, **changes)
    assert tampered != card
    with pytest.raises(EvidenceValidationError):
        validate_setup_card(tampered, ctx)


def test_research_reference_is_copied_with_its_caveats():
    items = s.core_items()
    ctx = s.card_context(*items.values())
    card = _setup_card(ctx, items)
    note = card.research_references[0]
    reference = items["research"].research_reference
    assert note.primary_label == reference.primary_label
    assert note.fixed_caveats == list(reference.fixed_caveats)
    assert note.holdout_state is reference.holdout_state


def test_research_item_missing_a_fixed_caveat_is_refused():
    items = s.core_items()
    items["research"] = f.research_item(
        f.confirmation_reference(fixed_caveats=["research_result_not_validation"])
    )
    ctx = s.card_context(*items.values())
    assert _reason(_setup_card, ctx, items) == "research_fixed_caveat_missing"


# --- Selector availability and outcome ---------------------------------------------------


def test_available_selector_copies_the_exact_opra_result():
    items = s.core_items()
    selector = f.selector_item()
    ctx = s.card_context(*items.values(), selector)
    card = _setup_card(ctx, items, selector_requested=True)
    assert card.selector_availability is SelectorAvailability.AVAILABLE
    assert card.contracts.selector_outcome is SelectorOutcome.ELIGIBLE
    assert card.contracts.eligible_contracts == sorted([f.ELIGIBLE_A, f.ELIGIBLE_B])
    assert card.contracts.research_only_contracts == []
    assert f.REJECTED_WIDE not in card.contracts.eligible_contracts
    assert card.contracts.rejection_counts == dict(f.selector_result().rejection_counts)


def test_research_only_result_stays_separate():
    items = s.core_items()
    selector = f.selector_item(f.selector_result(feed="indicative"))
    ctx = s.card_context(*items.values(), selector)
    card = _setup_card(ctx, items, selector_requested=True)
    assert card.contracts.selector_outcome is SelectorOutcome.RESEARCH_ONLY
    assert card.contracts.eligible_contracts == []
    assert card.contracts.research_only_contracts == sorted([f.ELIGIBLE_A, f.ELIGIBLE_B])


def test_missing_selector_is_unavailable_not_no_eligible_contracts():
    items = s.core_items()
    ctx = s.card_context(*items.values())
    card = _setup_card(ctx, items, selector_requested=True)
    assert card.selector_availability is SelectorAvailability.UNAVAILABLE
    assert card.contracts is None


def test_stale_selector_result_is_unavailable():
    items = s.core_items()
    later = f.T0 + timedelta(minutes=20)
    ctx = s.card_context(
        items["bar"],
        items["research"],
        items["evaluation"],
        f.selector_item(),
        f.clock_item(later),
        as_of=later,
    )
    card = _setup_card(ctx, items, selector_requested=True)
    assert card.selector_availability is SelectorAvailability.UNAVAILABLE


def test_not_requested_selector_carries_no_outcome():
    items = s.core_items()
    ctx = s.card_context(*items.values(), f.selector_item())
    card = _setup_card(ctx, items)
    assert card.selector_availability is SelectorAvailability.NOT_REQUESTED
    assert card.contracts is None


def test_rejected_contracts_cannot_be_added_to_the_card():
    items = s.core_items()
    ctx = s.card_context(*items.values(), f.selector_item())
    card = _setup_card(ctx, items, selector_requested=True)
    widened = card.contracts.model_copy(
        update={"eligible_contracts": sorted([*card.contracts.eligible_contracts, f.REJECTED_WIDE])}
    )
    tampered = _tampered(card, contracts=widened)
    assert _reason(validate_setup_card, tampered, ctx) == "contracts_do_not_match_selector"


def test_unavailable_is_refused_when_a_valid_result_exists():
    items = s.core_items()
    ctx = s.card_context(*items.values(), f.selector_item())
    card = _setup_card(ctx, items, selector_requested=True)
    tampered = _tampered(
        card, selector_availability=SelectorAvailability.UNAVAILABLE, contracts=None
    )
    # The selector item is still cited in context; availability alone is wrong.
    assert _reason(validate_setup_card, tampered, ctx) == "selector_wrongly_unavailable"


def test_no_qualified_setup_card_ignores_a_selector_request():
    items = s.core_items()
    ctx = s.card_context(
        items["bar"], items["research"], items["clock"], s.lane_conclusion(), f.selector_item()
    )
    card = _no_setup_card(ctx, selector_requested=True)
    assert card.selector_availability is SelectorAvailability.NOT_REQUESTED
    assert card.contracts is None


# --- Holdout ----------------------------------------------------------------------------------


def test_restricted_item_can_never_be_cited():
    items = s.core_items()
    ctx = s.card_context(*items.values())
    card = _setup_card(ctx, items)
    restricted = f.bar_item(f.HOLDOUT_T0)
    patched = CardContext(
        bundle=ctx.bundle,
        items={**ctx.items, items["bar"].item_id: restricted},
        conflicts=ctx.conflicts,
        registry=ctx.registry,
        setup_definitions=ctx.setup_definitions,
    )
    assert _reason(validate_setup_card, card, patched) == "holdout_restricted_citation"
