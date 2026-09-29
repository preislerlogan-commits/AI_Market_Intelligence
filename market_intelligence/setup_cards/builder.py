"""A pure, synthetic-safe setup-card builder.

``build_setup_card`` reads only already-constructed, in-memory Evidence
Envelope objects (one bundle, its items, its conflict records and the
registry). It opens no storage, database, provider or model, and it never
invents a value: lifecycle, qualification and expiry come only from a cited
deterministic setup evaluation by a registered setup definition, contracts
come only from the cited deterministic selector result, and every list is
derived from the bundle.

In production the setup-definition table is empty, so the builder produces
**no card of either kind**. Without a registered setup definition and an
authorized deterministic evaluation, the correct product state is "live
qualification not authorized / unavailable", a dashboard availability state
outside the card contract (``LaneQualificationAvailability``), never a
``no_qualified_setup`` conclusion. Blocked evidence is likewise no conclusion:
``build_no_qualified_setup_card`` reports ``evidence_blocked`` and builds no
card, rather than building a blocked ``no_qualified_setup`` card.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from market_intelligence.evidence.contracts import EvidenceCitation
from market_intelligence.evidence.enums import CitationRole, EvidenceKind
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.setup_cards.contracts import (
    ORIGINAL_CARD_REVISION,
    CardProvenance,
    CardRevision,
    SetupCard,
    SetupCardContent,
    seal_card,
)
from market_intelligence.setup_cards.enums import (
    QUALIFICATION_ITEM_REQUIRED,
    CardKind,
    CardRevisionReason,
    Lane,
    LaneQualificationAvailability,
    NoSetupReason,
    ReadinessBlocker,
    SelectorAvailability,
    SetupQualification,
)
from market_intelligence.setup_cards.supersession import validate_supersession
from market_intelligence.setup_cards.validation import (
    CardContext,
    check_evaluation_item,
    derive_conflict_notes,
    derive_contract_section,
    derive_non_current_entries,
    derive_observed_at,
    derive_readiness,
    derive_research_notes,
    evaluation_payload,
    find_evaluations,
    find_selector_result,
    lane_qualification,
    usable_lane_conclusion,
    validate_setup_card,
)

MAX_IDS_PER_CITATION = 8
_EASTERN = ZoneInfo("America/New_York")


def _fail(reason: str) -> EvidenceValidationError:
    return EvidenceValidationError(reason)


def _citations(
    ctx: CardContext, ids: Sequence[str], role: CitationRole, start_index: int
) -> list[EvidenceCitation]:
    entries = ctx.entries
    ordered = sorted(set(ids))
    citations = []
    for offset in range(0, len(ordered), MAX_IDS_PER_CITATION):
        chunk = ordered[offset : offset + MAX_IDS_PER_CITATION]
        citations.append(
            EvidenceCitation(
                bundle_id=ctx.bundle.bundle_id,
                claim_index=start_index + len(citations),
                cited_item_ids=chunk,
                citation_role=role,
                cited_kinds=sorted({entries[i].evidence_kind for i in chunk}),
            )
        )
    return citations


def build_setup_card(
    ctx: CardContext,
    *,
    card_kind: CardKind,
    lane: Lane,
    provenance: CardProvenance,
    setup_subject_id: str | None = None,
    no_setup_reason: NoSetupReason | None = None,
    supporting_ids: Sequence[str] = (),
    contradicting_ids: Sequence[str] = (),
    context_ids: Sequence[str] = (),
    selector_requested: bool = False,
    prior_card: SetupCard | None = None,
    revision_reason: CardRevisionReason | None = None,
) -> SetupCard:
    entries = ctx.entries
    for item_id in (*supporting_ids, *contradicting_ids, *context_ids):
        if item_id not in entries:
            raise _fail("citation_outside_bundle")
    for item_id in (*supporting_ids, *contradicting_ids):
        if entries[item_id].evidence_kind is EvidenceKind.CURRENT_INFERENCE:
            raise _fail("inference_only_as_context")

    context = list(context_ids)
    lifecycle = expires = definition_id = qualification_item_id = None
    qualification = SetupQualification.NOT_EVALUATED

    if card_kind is CardKind.SETUP:
        if setup_subject_id is None:
            raise _fail("setup_subject_required")
        matches = [
            item
            for item in find_evaluations(ctx, lane)
            if evaluation_payload(item).setup_subject_id == setup_subject_id
        ]
        if len(matches) != 1:
            raise _fail("setup_card_needs_one_evaluation")
        payload = check_evaluation_item(ctx, matches[0])
        context.append(matches[0].item_id)
        lifecycle, qualification = payload.lifecycle_state, payload.qualification
        expires, definition_id = payload.expires_at_utc, payload.setup_definition_id
        if qualification in QUALIFICATION_ITEM_REQUIRED:
            qualification_item_id = matches[0].item_id
    else:
        # Checked before anything is built: an unauthorized, unavailable or
        # evidence-blocked lane has no conclusion, so no card is created.
        availability, _ = lane_qualification(
            ctx, lane, (*supporting_ids, *contradicting_ids, *context_ids)
        )
        if availability is LaneQualificationAvailability.EVIDENCE_BLOCKED:
            raise _fail("no_setup_conclusion_evidence_blocked")
        if availability is not LaneQualificationAvailability.AVAILABLE:
            raise _fail("lane_qualification_not_available")
        conclusion = usable_lane_conclusion(ctx, lane)
        if conclusion is None:  # unreachable once ``available``; fail closed
            raise _fail("lane_qualification_not_available")
        payload = check_evaluation_item(ctx, conclusion)
        if no_setup_reason is not None and no_setup_reason is not payload.no_setup_reason:
            raise _fail("reason_contradicts_evaluation")
        no_setup_reason = payload.no_setup_reason
        qualification = payload.qualification
        expires = payload.expires_at_utc
        context.append(conclusion.item_id)
        if qualification in QUALIFICATION_ITEM_REQUIRED:
            qualification_item_id = conclusion.item_id

    selector_availability = SelectorAvailability.NOT_REQUESTED
    contracts = None
    if card_kind is CardKind.SETUP and selector_requested:
        selector_item = find_selector_result(ctx)
        if selector_item is None:
            selector_availability = SelectorAvailability.UNAVAILABLE
        else:
            selector_availability = SelectorAvailability.AVAILABLE
            contracts = derive_contract_section(selector_item)
            context.append(selector_item.item_id)

    supporting = _citations(ctx, supporting_ids, CitationRole.SUPPORTS_CLAIM, 0)
    contradicting = _citations(
        ctx, contradicting_ids, CitationRole.CONTRADICTS_CLAIM, len(supporting)
    )
    context_citations = _citations(
        ctx, context, CitationRole.CONTEXT, len(supporting) + len(contradicting)
    )
    cited = {
        item_id
        for citation in (*supporting, *contradicting, *context_citations)
        for item_id in citation.cited_item_ids
    }
    readiness, blockers = derive_readiness(ctx, cited)

    if prior_card is None:
        revision = ORIGINAL_CARD_REVISION
    else:
        if revision_reason is None or revision_reason is CardRevisionReason.ORIGINAL:
            raise _fail("superseding_card_needs_a_reason")
        revision = CardRevision(
            revision_number=prior_card.card_revision.revision_number + 1,
            supersedes_card_id=prior_card.card_id,
            revision_reason=revision_reason,
        )

    bundle = ctx.bundle
    card = seal_card(
        SetupCardContent(
            card_kind=card_kind,
            lane=lane,
            setup_subject_id=setup_subject_id if card_kind is CardKind.SETUP else None,
            setup_definition_id=definition_id,
            session_date=bundle.as_of_utc.astimezone(_EASTERN).date(),
            effective_at_utc=bundle.as_of_utc,
            observed_at_utc=derive_observed_at(ctx, cited),
            decision_context_bundle_id=bundle.bundle_id,
            registry_version_id=bundle.registry_version_id,
            evidence_readiness=readiness,
            readiness_blockers=blockers,
            qualification=qualification,
            qualification_item_id=qualification_item_id,
            lifecycle_state=lifecycle,
            no_setup_reason=no_setup_reason,
            supporting_citations=supporting,
            contradicting_citations=contradicting,
            context_citations=context_citations,
            missing_requirements=list(bundle.missing_required_producers),
            ambiguous_requirements=list(bundle.ambiguous_requirements),
            non_current_entries=derive_non_current_entries(ctx, cited),
            conflicts=derive_conflict_notes(ctx, cited),
            research_references=derive_research_notes(ctx, cited),
            selector_availability=selector_availability,
            contracts=contracts,
            expires_at_utc=expires,
            manual_review_ready=not blockers,
            card_revision=revision,
            provenance=provenance,
        )
    )
    validate_setup_card(card, ctx)
    if prior_card is not None:
        validate_supersession(card, prior_card)
    return card


@dataclass(frozen=True)
class NoQualifiedSetupResult:
    """The bounded outcome of asking for a lane's ``no_qualified_setup``
    card. Outside the card contract: ``availability`` is a dashboard state.

    A card is present only when ``availability`` is ``available``; readiness
    blockers are present only when it is ``evidence_blocked``.
    """

    availability: LaneQualificationAvailability
    card: SetupCard | None = None
    readiness_blockers: tuple[ReadinessBlocker, ...] = ()

    def __post_init__(self) -> None:
        available = self.availability is LaneQualificationAvailability.AVAILABLE
        blocked = self.availability is LaneQualificationAvailability.EVIDENCE_BLOCKED
        if available != (self.card is not None):
            raise _fail("no_setup_result_card_iff_available")
        if blocked != bool(self.readiness_blockers):
            raise _fail("no_setup_result_blockers_iff_evidence_blocked")
        if self.card is not None and self.card.card_kind is not CardKind.NO_QUALIFIED_SETUP:
            raise _fail("no_setup_result_card_kind")


def build_no_qualified_setup_card(
    ctx: CardContext,
    *,
    lane: Lane,
    provenance: CardProvenance,
    supporting_ids: Sequence[str] = (),
    contradicting_ids: Sequence[str] = (),
    context_ids: Sequence[str] = (),
    prior_card: SetupCard | None = None,
    revision_reason: CardRevisionReason | None = None,
) -> NoQualifiedSetupResult:
    """Present a cited, authorized ``not_qualified`` lane conclusion with
    ready evidence, or explain why there is no card.

    No card (never a false negative) when the lane is ``not_authorized``,
    ``unavailable`` or ``evidence_blocked``; blocked evidence is never
    turned into ``criteria_not_met`` or ``no_candidate_evaluated``. The
    card's reason is taken from the cited evaluation.
    """
    availability, blockers = lane_qualification(
        ctx, lane, (*supporting_ids, *contradicting_ids, *context_ids)
    )
    if availability is not LaneQualificationAvailability.AVAILABLE:
        return NoQualifiedSetupResult(availability, readiness_blockers=tuple(blockers))
    card = build_setup_card(
        ctx,
        card_kind=CardKind.NO_QUALIFIED_SETUP,
        lane=lane,
        provenance=provenance,
        supporting_ids=supporting_ids,
        contradicting_ids=contradicting_ids,
        context_ids=context_ids,
        prior_card=prior_card,
        revision_reason=revision_reason,
    )
    return NoQualifiedSetupResult(availability, card=card)
