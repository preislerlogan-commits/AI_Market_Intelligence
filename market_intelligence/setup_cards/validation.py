"""Bundle-dependent derivation and validation for ``setup-card-1``.

A card is valid only against the exact Evidence Bundle it names. The same
pure derivation functions are used by the builder (to fill the card) and by
``validate_setup_card`` (to re-derive and compare), so a card cannot carry a
value its bundle does not support.

Nothing here opens storage, a provider, a model or a database: the caller
supplies already-constructed, in-memory Evidence Envelope objects.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from pydantic import ValidationError

from market_intelligence.contract_selection.contracts import ContractSelectorResult
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.contracts import (
    EvidenceBundleEntry,
    EvidenceBundleManifest,
    EvidenceCitation,
    EvidenceConflict,
    EvidenceItem,
)
from market_intelligence.evidence.enums import (
    ClockHealthReason,
    ConflictSeverity,
    ConflictStatus,
    ConsumerId,
    EvidenceKind,
    FreshnessState,
    MissingProducerReason,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence.research_rules import validate_research_reference
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_SELECTOR_PAYLOAD_ID,
    CONTRACT_SELECTOR_PRODUCER_ID,
    ContractView,
    validate_contract_view,
)
from market_intelligence.evidence.validation import validate_citations
from market_intelligence.setup_cards.contracts import (
    CardConflictNote,
    CardContractSection,
    CardFreshnessNote,
    CardResearchNote,
    SetupCard,
)
from market_intelligence.setup_cards.definitions import (
    REGISTERED_SETUP_DEFINITIONS,
    SETUP_EVALUATION_PAYLOAD,
    SetupDefinition,
    SetupEvaluationPayload,
)
from market_intelligence.setup_cards.enums import (
    CardKind,
    EvidenceReadiness,
    Lane,
    LaneQualificationAvailability,
    ReadinessBlocker,
    SelectorAvailability,
)

READY_FRESHNESS = frozenset({FreshnessState.CURRENT, FreshnessState.TIMELESS})


def _fail(reason: str) -> EvidenceValidationError:
    return EvidenceValidationError(reason)


@dataclass(frozen=True)
class CardContext:
    """Everything a card may be derived from: one bundle, its items and its
    conflict records, the registry and the setup-definition table (empty in
    production)."""

    bundle: EvidenceBundleManifest
    items: Mapping[str, EvidenceItem]
    conflicts: Mapping[str, EvidenceConflict]
    registry: EvidenceRegistry
    setup_definitions: Sequence[SetupDefinition] = field(
        default_factory=lambda: REGISTERED_SETUP_DEFINITIONS
    )

    def __post_init__(self) -> None:
        entries = {e.item_id: e for e in self.bundle.entries}
        if not set(entries) <= set(self.items):
            raise _fail("bundle_items_not_supplied")
        for item_id, entry in entries.items():
            if self.items[item_id].evidence_kind is not entry.evidence_kind:
                raise _fail("bundle_item_kind_mismatch")
        if set(self.conflicts) != set(self.bundle.conflict_ids):
            raise _fail("bundle_conflicts_not_supplied")

    @property
    def entries(self) -> dict[str, EvidenceBundleEntry]:
        return {e.item_id: e for e in self.bundle.entries}

    def definition(self, setup_definition_id: str) -> SetupDefinition | None:
        return next(
            (d for d in self.setup_definitions if d.setup_definition_id == setup_definition_id),
            None,
        )


# --- Citations ---------------------------------------------------------------------------


def cited_ids(citations: Iterable[EvidenceCitation]) -> set[str]:
    return {item_id for citation in citations for item_id in citation.cited_item_ids}


def card_cited_ids(card: SetupCard) -> set[str]:
    return cited_ids(
        (*card.supporting_citations, *card.contradicting_citations, *card.context_citations)
    )


def relevant_ids(ctx: CardContext, cited: set[str]) -> set[str]:
    """Entries the card depends on: every cited entry and every required one."""
    return cited | {e.item_id for e in ctx.bundle.entries if e.required}


# --- Evidence lists ------------------------------------------------------------------------


def derive_non_current_entries(ctx: CardContext, cited: set[str]) -> list[CardFreshnessNote]:
    entries = ctx.entries
    notes = []
    for item_id in sorted(relevant_ids(ctx, cited)):
        entry = entries[item_id]
        state = entry.freshness.state
        if state in READY_FRESHNESS:
            continue
        notes.append(
            CardFreshnessNote(
                item_id=item_id,
                state=state,
                reason=entry.freshness.reason,
                required=entry.required,
            )
        )
    return notes


def _current_status(ctx: CardContext) -> dict[str, EvidenceConflict]:
    """Map every conflict ID to its chain's current status record."""
    superseded_by: dict[str, str] = {}
    for conflict in ctx.conflicts.values():
        if conflict.resolution is not None:
            superseded_by[conflict.resolution.supersedes_conflict_id] = conflict.conflict_id
    current: dict[str, EvidenceConflict] = {}
    for conflict_id in ctx.conflicts:
        tip = conflict_id
        seen = {tip}
        while tip in superseded_by and superseded_by[tip] in ctx.conflicts:
            tip = superseded_by[tip]
            if tip in seen:
                raise _fail("conflict_cycle")
            seen.add(tip)
        current[conflict_id] = ctx.conflicts[tip]
    return current


def derive_conflict_notes(ctx: CardContext, cited: set[str]) -> list[CardConflictNote]:
    relevant = relevant_ids(ctx, cited)
    current = _current_status(ctx)
    notes = []
    for conflict_id in sorted(ctx.conflicts):
        conflict = ctx.conflicts[conflict_id]
        if not set(conflict.involved_item_ids) & relevant:
            continue
        notes.append(
            CardConflictNote(
                conflict_id=conflict_id,
                conflict_type=conflict.conflict_type,
                severity=conflict.severity,
                status=current[conflict_id].status,
                involved_item_ids=list(conflict.involved_item_ids),
            )
        )
    return notes


def derive_research_notes(ctx: CardContext, cited: set[str]) -> list[CardResearchNote]:
    notes = []
    for item_id in sorted(cited):
        item = ctx.items[item_id]
        if item.evidence_kind is not EvidenceKind.HISTORICAL_RESEARCH_RESULT:
            continue
        reference = item.research_reference
        if reference is None:  # pragma: no cover - guaranteed by the item contract
            raise _fail("research_reference_missing")
        validate_research_reference(reference)
        notes.append(
            CardResearchNote(
                item_id=item_id,
                study_id=reference.study_id,
                result_kind=reference.result_kind,
                primary_label=reference.primary_label,
                secondary_labels=list(reference.secondary_labels),
                research_status=reference.research_status,
                holdout_state=reference.holdout_state,
                fixed_caveats=list(reference.fixed_caveats),
            )
        )
    return notes


def derive_observed_at(ctx: CardContext, cited: set[str]) -> datetime | None:
    entries = ctx.entries
    observed = [
        ctx.items[item_id].provenance.source_observed_at_utc
        for item_id in cited
        if entries[item_id].required
        and ctx.items[item_id].provenance.source_observed_at_utc is not None
    ]
    return min(observed) if observed else None


# --- Readiness ------------------------------------------------------------------------------


def derive_readiness(
    ctx: CardContext, cited: set[str]
) -> tuple[EvidenceReadiness, list[ReadinessBlocker]]:
    """Derived from the bundle's state only, never from ``machine_decision_ready``."""
    blockers: set[ReadinessBlocker] = set()
    for missing in ctx.bundle.missing_required_producers:
        if missing.reason is MissingProducerReason.HOLDOUT_RESTRICTED:
            blockers.add(ReadinessBlocker.HOLDOUT_RESTRICTED)
        elif missing.reason is MissingProducerReason.ONLY_STALE_ITEMS:
            blockers.add(ReadinessBlocker.STALE_REQUIRED_EVIDENCE)
        else:
            blockers.add(ReadinessBlocker.MISSING_REQUIRED_EVIDENCE)
    if ctx.bundle.ambiguous_requirements:
        blockers.add(ReadinessBlocker.AMBIGUOUS_REQUIREMENT)
    entries = ctx.entries
    relevant = relevant_ids(ctx, cited)
    for item_id in relevant:
        entry = entries[item_id]
        freshness = entry.freshness
        if freshness.clock_health_reason is ClockHealthReason.AMBIGUOUS_CLOCK_FACTS:
            blockers.add(ReadinessBlocker.AMBIGUOUS_CLOCK_FACTS)
        elif freshness.state is FreshnessState.UNKNOWN:
            blockers.add(ReadinessBlocker.UNKNOWN_FRESHNESS)
        elif freshness.state not in READY_FRESHNESS:
            blockers.add(ReadinessBlocker.STALE_REQUIRED_EVIDENCE)
        if entry.required and not entry.machine_decision_eligible:
            blockers.add(ReadinessBlocker.INELIGIBLE_REQUIRED_INPUT)
    current = _current_status(ctx)
    for conflict_id, conflict in ctx.conflicts.items():
        tip = current[conflict_id]
        if (
            set(conflict.involved_item_ids) & relevant
            and tip.status is ConflictStatus.UNRESOLVED
            and tip.severity is ConflictSeverity.CRITICAL
        ):
            blockers.add(ReadinessBlocker.UNRESOLVED_CRITICAL_CONFLICT)
    ordered = sorted(blockers)
    return (EvidenceReadiness.BLOCKED if ordered else EvidenceReadiness.READY), ordered


# --- Setup evaluation (lifecycle, qualification, expiry) ----------------------------------


def evaluation_payload(item: EvidenceItem) -> SetupEvaluationPayload:
    try:
        return SetupEvaluationPayload.model_validate_json(canonical_json_bytes(item.payload))
    except ValidationError:
        raise _fail("setup_evaluation_invalid") from None


def find_evaluations(ctx: CardContext, lane: Lane) -> list[EvidenceItem]:
    """Current (non-superseded) setup-evaluation items for a lane."""
    found = []
    for entry in ctx.bundle.entries:
        item = ctx.items[entry.item_id]
        if item.payload_schema_id != SETUP_EVALUATION_PAYLOAD or entry.superseded_as_of:
            continue
        if evaluation_payload(item).lane is lane:
            found.append(item)
    return found


def lane_conclusions(ctx: CardContext, lane: Lane) -> list[EvidenceItem]:
    """Current lane-level (no setup subject) evaluations for a lane."""
    return [
        item
        for item in find_evaluations(ctx, lane)
        if evaluation_payload(item).setup_subject_id is None
    ]


def usable_lane_conclusion(ctx: CardContext, lane: Lane) -> EvidenceItem | None:
    """The single valid lane-level ``not_qualified`` evaluation from the
    lane's registered producer, or ``None`` when it is missing, invalid,
    ambiguous (more than one) or from the wrong producer. Never picks one of
    several."""
    try:
        conclusions = lane_conclusions(ctx, lane)
        if len(conclusions) != 1:
            return None
        check_evaluation_item(ctx, conclusions[0])
    except EvidenceValidationError:
        return None
    return conclusions[0]


def lane_qualification(
    ctx: CardContext, lane: Lane, cited: Iterable[str] = ()
) -> tuple[LaneQualificationAvailability, list[ReadinessBlocker]]:
    """A dashboard availability state, outside the card contract, with the
    readiness blockers behind ``evidence_blocked`` (empty otherwise).

    ``not_authorized`` when the lane has no registered setup definition (the
    production state today); ``unavailable`` when a definition exists but no
    usable lane conclusion does (``usable_lane_conclusion``);
    ``evidence_blocked`` when that conclusion exists but readiness, derived
    over it and ``cited``, has blockers; ``available`` only otherwise. Only
    ``available`` can ever lead to a ``no_qualified_setup`` card.
    """
    if not any(d.lane is lane for d in ctx.setup_definitions):
        return LaneQualificationAvailability.NOT_AUTHORIZED, []
    conclusion = usable_lane_conclusion(ctx, lane)
    if conclusion is None:
        return LaneQualificationAvailability.UNAVAILABLE, []
    cited_set = {*cited, conclusion.item_id}
    if not cited_set <= set(ctx.entries):
        raise _fail("citation_outside_bundle")
    _, blockers = derive_readiness(ctx, cited_set)
    if blockers:
        return LaneQualificationAvailability.EVIDENCE_BLOCKED, blockers
    return LaneQualificationAvailability.AVAILABLE, []


def lane_qualification_availability(
    ctx: CardContext, lane: Lane, cited: Iterable[str] = ()
) -> LaneQualificationAvailability:
    """The availability state of ``lane_qualification`` alone."""
    return lane_qualification(ctx, lane, cited)[0]


def check_evaluation_item(ctx: CardContext, item: EvidenceItem) -> SetupEvaluationPayload:
    """The item must be a deterministic calculation from the registered
    setup-definition producer. No definition is registered in production."""
    payload = evaluation_payload(item)
    definition = ctx.definition(payload.setup_definition_id)
    if definition is None:
        raise _fail("setup_definition_not_registered")
    if definition.lane is not payload.lane:
        raise _fail("setup_definition_lane_mismatch")
    if item.evidence_kind is not EvidenceKind.DETERMINISTIC_CALCULATION:
        raise _fail("qualification_requires_deterministic_calculation")
    if item.provenance.producer_id != definition.evaluation_producer_id:
        raise _fail("qualification_from_unregistered_producer")
    return payload


# --- Contract selector -------------------------------------------------------------------


def find_selector_result(ctx: CardContext) -> EvidenceItem | None:
    """The single valid selector result in the bundle, or ``None``.

    A result is valid only if it comes from the deterministic selector, is the
    latest revision, and is current or timeless. Zero or several valid results
    mean the selector is ``unavailable``, never ``no_eligible_contracts``.
    """
    valid = []
    for entry in ctx.bundle.entries:
        item = ctx.items[entry.item_id]
        if (
            item.provenance.producer_id == CONTRACT_SELECTOR_PRODUCER_ID
            and item.payload_schema_id == CONTRACT_SELECTOR_PAYLOAD_ID
            and not entry.superseded_as_of
            and entry.freshness.state in READY_FRESHNESS
        ):
            valid.append(item)
    return valid[0] if len(valid) == 1 else None


def derive_contract_section(item: EvidenceItem) -> CardContractSection:
    result = ContractSelectorResult.model_validate_json(canonical_json_bytes(item.payload))
    eligible = sorted(c.contract_symbol for c in result.eligible_contracts)
    research = sorted(c.contract_symbol for c in result.research_only_contracts)
    counts = dict(result.rejection_counts)
    # Exact matching through the envelope's permanent selector boundary.
    validate_contract_view(
        result,
        ContractView(
            consumer_id=ConsumerId.DASHBOARD,
            eligible_contracts=eligible,
            research_only_contracts=research,
            rejection_counts=counts,
        ),
    )
    return CardContractSection(
        selector_result_item_id=item.item_id,
        selector_outcome=result.status,
        feed=result.feed.value,
        eligible_contracts=eligible,
        research_only_contracts=research,
        rejection_counts=counts,
    )


# --- Full validation -----------------------------------------------------------------------


def validate_setup_card(card: SetupCard, ctx: CardContext) -> None:
    """Re-derive every bundle-dependent value and refuse any mismatch."""
    bundle = ctx.bundle
    if card.decision_context_bundle_id != bundle.bundle_id:
        raise _fail("card_bundle_mismatch")
    if card.registry_version_id != bundle.registry_version_id:
        raise _fail("card_registry_mismatch")
    if card.effective_at_utc != bundle.as_of_utc:
        raise _fail("card_effective_time_mismatch")

    citations = [*card.supporting_citations, *card.contradicting_citations, *card.context_citations]
    validate_citations(bundle, citations)  # resolve inside the bundle; kinds match
    cited = card_cited_ids(card)
    for item_id in cited:
        if is_holdout_restricted(ctx.items[item_id], ctx.registry):
            raise _fail("holdout_restricted_citation")

    if list(card.missing_requirements) != list(bundle.missing_required_producers):
        raise _fail("missing_requirements_mismatch")
    if list(card.ambiguous_requirements) != list(bundle.ambiguous_requirements):
        raise _fail("ambiguous_requirements_mismatch")
    if card.non_current_entries != derive_non_current_entries(ctx, cited):
        raise _fail("non_current_entries_mismatch")
    if card.conflicts != derive_conflict_notes(ctx, cited):
        raise _fail("conflicts_mismatch")
    if card.research_references != derive_research_notes(ctx, cited):
        raise _fail("research_references_mismatch")
    if card.observed_at_utc != derive_observed_at(ctx, cited):
        raise _fail("observed_at_mismatch")
    readiness, blockers = derive_readiness(ctx, cited)
    if card.evidence_readiness is not readiness or list(card.readiness_blockers) != blockers:
        raise _fail("readiness_mismatch")

    _validate_evaluation(card, ctx, cited)
    _validate_selector(card, ctx, cited)


def _validate_evaluation(card: SetupCard, ctx: CardContext, cited: set[str]) -> None:
    evaluations = [i for i in find_evaluations(ctx, card.lane) if i.item_id in cited]
    if card.card_kind is CardKind.SETUP:
        if len(evaluations) != 1:
            raise _fail("setup_card_needs_one_cited_evaluation")
        payload = check_evaluation_item(ctx, evaluations[0])
        if (
            payload.setup_subject_id != card.setup_subject_id
            or payload.setup_definition_id != card.setup_definition_id
            or payload.lifecycle_state is not card.lifecycle_state
            or payload.qualification is not card.qualification
            or payload.expires_at_utc != card.expires_at_utc
        ):
            raise _fail("card_disagrees_with_evaluation")
        expected_item = evaluations[0].item_id if card.qualification_item_id else None
        if card.qualification_item_id != expected_item:
            raise _fail("qualification_item_mismatch")
        return
    # no_qualified_setup: exactly one cited, registered, deterministic
    # lane-level not_qualified conclusion, whose reason the card repeats.
    lane_level = lane_conclusions(ctx, card.lane)
    if len(lane_level) != 1:
        raise _fail("no_setup_card_needs_one_lane_conclusion")
    conclusion = lane_level[0]
    if conclusion.item_id not in cited:
        raise _fail("uncited_evaluation_conclusion")
    if card.qualification_item_id != conclusion.item_id:
        raise _fail("qualification_item_mismatch")
    payload = check_evaluation_item(ctx, conclusion)
    if (
        payload.qualification is not card.qualification
        or payload.no_setup_reason is not card.no_setup_reason
        or payload.expires_at_utc != card.expires_at_utc
    ):
        raise _fail("card_disagrees_with_evaluation")


def _validate_selector(card: SetupCard, ctx: CardContext, cited: set[str]) -> None:
    valid = find_selector_result(ctx)
    availability = card.selector_availability
    if availability is SelectorAvailability.AVAILABLE:
        if valid is None or card.contracts is None:
            raise _fail("selector_result_not_valid")
        if card.contracts.selector_result_item_id != valid.item_id or valid.item_id not in cited:
            raise _fail("selector_result_not_cited")
        if card.contracts != derive_contract_section(valid):
            raise _fail("contracts_do_not_match_selector")
    elif availability is SelectorAvailability.UNAVAILABLE and valid is not None:
        # A valid result exists, so the selector is not unavailable.
        raise _fail("selector_wrongly_unavailable")
