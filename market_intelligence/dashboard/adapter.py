"""The narrow presentation adapter: validated contracts in, frozen views out.

``present`` validates every bundle and card of a scenario first
(``validation.py``) and only then derives views. It never creates evidence,
never chooses a winner among competing items, never edits a card, and never
re-labels a selector status: readiness, lane availability, freshness,
conflicts and contract sets are all taken from the Evidence Envelope and
``setup-card-1`` derivations themselves. Any inconsistency raises
``DashboardInputError``; nothing is partially rendered.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from market_intelligence.dashboard import labels as L
from market_intelligence.dashboard.fixtures import (
    PAYLOAD_MODELS,
    BundleInput,
    CardRecord,
    LaneInput,
    Scenario,
)
from market_intelligence.dashboard.validation import (
    DashboardInputError,
    check_definitions,
    validate_bundle,
    validate_card,
    validate_history,
)
from market_intelligence.dashboard.views import (
    AmbiguityView,
    BundleView,
    CardSummary,
    ConflictView,
    ContractReviewView,
    DashboardModel,
    EvidenceRow,
    FreshnessNoteView,
    LaneView,
    MissingView,
    ResearchNoteView,
    SetupDetailView,
    StatusStrip,
    SystemStatusView,
)
from market_intelligence.evidence.contracts import (
    AmbiguousRequirement,
    EvidenceConflict,
    EvidenceItem,
    MissingProducer,
)
from market_intelligence.evidence.enums import (
    BundlePurpose,
    ClockHealthReason,
    ConflictSeverity,
    ConflictStatus,
    EvidenceKind,
    MissingProducerReason,
)
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence.selector_boundary import CONTRACT_SELECTOR_PAYLOAD_ID
from market_intelligence.setup_cards.contracts import SetupCard
from market_intelligence.setup_cards.definitions import SETUP_EVALUATION_PAYLOAD
from market_intelligence.setup_cards.enums import (
    CardKind,
    EvidenceReadiness,
    Lane,
    LaneQualificationAvailability,
)
from market_intelligence.setup_cards.supersession import display_status
from market_intelligence.setup_cards.validation import (
    CardContext,
    card_cited_ids,
    derive_readiness,
    lane_qualification,
)

# Static authorization text from the reviewed documents (not evidence).
AUTHORIZATION: tuple[tuple[str, str], ...] = (
    ("Offline synthetic dashboard prototype", "Authorized: local, read-only, synthetic only"),
    ("Real database and migration 0010", "Not authorized"),
    ("Live providers (Alpaca, FRED) and model calls", "Not authorized"),
    ("Registry activation and producer adapters", "Not authorized"),
    ("Live setup definitions and live qualification", "Not authorized (definition table empty)"),
    ("Trend-day research and detection", "Not researched; not authorized"),
    ("Assistant retrieval and explanation", "Not authorized (needs a registry grant)"),
    ("Setup and contract ranking; scenario relations", "Not authorized"),
    ("Notifications and SMS", "Not authorized"),
    ("Machine-decision mode", "Not authorized"),
    ("Order entry, brokerage, position sizing, execution", "Never: execution is manual"),
)
RESEARCH_STAGES: tuple[tuple[str, str], ...] = (
    ("SPY VWAP reversion: confirmation label", "supported_for_further_shadow_research"),
    ("SPY VWAP reversion: holdout", "sealed"),
    ("SPY VWAP reversion: shadow research Stage 2", "design_complete_test_pending"),
    ("SPY VWAP reversion: shadow research Stage 3", "not authorized"),
    ("Trend continuation", "not researched"),
)


# --- Validation of a whole scenario ---------------------------------------------------------


def _validate_input(
    bundle_input: BundleInput, registry: EvidenceRegistry, purpose, payload_models=PAYLOAD_MODELS
) -> None:
    validate_bundle(
        bundle_input.bundle,
        bundle_input.items,
        bundle_input.conflicts,
        registry=registry,
        payload_models=payload_models,
        purpose=purpose,
    )


def _context(bundle_input: BundleInput, registry: EvidenceRegistry, definitions=()) -> CardContext:
    return CardContext(
        bundle=bundle_input.bundle,
        items=bundle_input.items,
        conflicts=bundle_input.conflicts,
        registry=registry,
        setup_definitions=tuple(definitions),
    )


def validate_scenario(scenario: Scenario) -> None:
    """Every bundle and card, before anything is derived."""
    registry = scenario.registry
    models = scenario.payload_models
    _validate_input(scenario.market, registry, BundlePurpose.LIVE_MARKET_STATE, models)
    _validate_input(scenario.premarket, registry, BundlePurpose.PREMARKET_BRIEFING, models)
    lane = scenario.vwap
    check_definitions(lane.definitions)
    _validate_input(lane.context, registry, BundlePurpose.SETUP_DETAIL, models)
    if lane.history and not lane.definitions:
        raise DashboardInputError("card_without_definition")
    for record in lane.history:
        _validate_input(record.source, registry, BundlePurpose.SETUP_DETAIL, models)
        if record.card.lane is not Lane.VWAP_REVERSION:
            raise DashboardInputError("card_lane_mismatch")
        validate_card(record.card, _context(record.source, registry, lane.definitions))
    if lane.history:
        validate_history([r.card for r in lane.history])
        if lane.history[-1].source.bundle.bundle_id != lane.context.bundle.bundle_id:
            raise DashboardInputError("lane_context_mismatch")


# --- Rows, conflicts and the status strip ---------------------------------------------------


def _summary(item: EvidenceItem) -> str:
    payload = item.payload
    if item.payload_schema_id == CONTRACT_SELECTOR_PAYLOAD_ID:
        return "Deterministic contract selector result (see Contract Review)"
    if item.payload_schema_id == SETUP_EVALUATION_PAYLOAD:
        lifecycle = payload.get("lifecycle_state")
        qualification = payload.get("qualification")
        return f"Synthetic setup evaluation: lifecycle {lifecycle}, qualification {qualification}"
    if item.research_reference is not None:
        return f"Recorded research label {item.research_reference.primary_label}"
    parts = [
        f"{key} {value}"
        for key, value in sorted(payload.items())
        if isinstance(value, str | int | float | bool) or value is None
    ]
    text = "; ".join(parts) or "(no displayable payload)"
    return text if len(text) <= 120 else text[:117] + "..."


def _current_conflicts(conflicts: Mapping[str, EvidenceConflict]) -> list[EvidenceConflict]:
    superseded = {
        c.resolution.supersedes_conflict_id for c in conflicts.values() if c.resolution is not None
    }
    return [c for cid, c in sorted(conflicts.items()) if cid not in superseded]


def _rows(bundle_input: BundleInput) -> dict[str, EvidenceRow]:
    in_conflict: dict[str, list[str]] = defaultdict(list)
    for conflict in _current_conflicts(bundle_input.conflicts):
        if conflict.status is ConflictStatus.UNRESOLVED:
            for item_id in conflict.involved_item_ids:
                in_conflict[item_id].append(conflict.conflict_id)
    rows = {}
    for entry in bundle_input.bundle.entries:
        item = bundle_input.items[entry.item_id]
        fresh = entry.freshness
        rows[entry.item_id] = EvidenceRow(
            item_id=entry.item_id,
            short_id=L.short_id(entry.item_id),
            kind=entry.evidence_kind,
            kind_label=L.kind_label(entry.evidence_kind),
            producer_id=entry.producer_id,
            required=entry.required,
            freshness_state=fresh.state,
            freshness=L.freshness_label(fresh.state, fresh.reason.value, fresh.age_seconds),
            effective_at=L.format_time(item.effective_at_utc),
            observed_at=L.format_time(item.provenance.source_observed_at_utc),
            summary=_summary(item),
            superseded=entry.superseded_as_of,
            conflict_ids=tuple(sorted(in_conflict.get(entry.item_id, []))),
        )
    return rows


def _conflict_view(conflict: EvidenceConflict, rows: Mapping[str, EvidenceRow]) -> ConflictView:
    return ConflictView(
        conflict_id=conflict.conflict_id,
        short_id=L.short_id(conflict.conflict_id),
        conflict_type=conflict.conflict_type.value,
        severity=conflict.severity.value,
        status=conflict.status.value,
        sides=tuple(rows[i] for i in conflict.involved_item_ids if i in rows),
    )


def _missing(missing: Sequence[MissingProducer]) -> tuple[MissingView, ...]:
    return tuple(
        MissingView(
            requirement_id=m.requirement_id,
            producer_id=m.producer_id,
            reason=L.humanize(m.reason.value),
            holdout_restricted=m.reason is MissingProducerReason.HOLDOUT_RESTRICTED,
        )
        for m in missing
    )


def _ambiguous(ambiguous: Sequence[AmbiguousRequirement]) -> tuple[AmbiguityView, ...]:
    return tuple(
        AmbiguityView(
            requirement_id=a.requirement_id,
            reason=L.humanize(a.reason.value),
            competing_item_ids=tuple(a.competing_item_ids),
            matching_conflict_ids=tuple(a.matching_unresolved_conflict_ids),
        )
        for a in ambiguous
    )


def _clock(bundle_input: BundleInput) -> str:
    reasons = {e.freshness.clock_health_reason for e in bundle_input.bundle.entries}
    reasons.discard(ClockHealthReason.NOT_EVALUATED)
    if not reasons:
        return L.clock_label(ClockHealthReason.NOT_EVALUATED)
    return "; ".join(L.clock_label(r) for r in sorted(reasons))


def _strip(
    bundle_input: BundleInput, readiness: EvidenceReadiness, blockers: Sequence[str]
) -> StatusStrip:
    bundle = bundle_input.bundle
    current = [
        c
        for c in _current_conflicts(bundle_input.conflicts)
        if c.status is ConflictStatus.UNRESOLVED
    ]
    holdout = any(
        m.reason is MissingProducerReason.HOLDOUT_RESTRICTED
        for m in bundle.missing_required_producers
    )
    rows = _rows(bundle_input)
    return StatusStrip(
        bundle_id=bundle.bundle_id,
        bundle_short_id=L.short_id(bundle.bundle_id),
        purpose=bundle.purpose.value,
        as_of=L.format_time(bundle.as_of_utc),
        clock=_clock(bundle_input),
        readiness=readiness,
        readiness_label=L.READINESS_LABELS[readiness],
        blockers=tuple(L.humanize(b) for b in blockers),
        missing_count=len(bundle.missing_required_producers),
        non_current_count=sum(
            1 for r in rows.values() if r.freshness_state.value not in ("current", "timeless")
        ),
        material_conflicts=sum(1 for c in current if c.severity is ConflictSeverity.MATERIAL),
        critical_conflicts=sum(1 for c in current if c.severity is ConflictSeverity.CRITICAL),
        registry_short_id=L.short_id(bundle.registry_version_id),
        holdout_notice=L.HOLDOUT_NOTICE if holdout else None,
        scope=L.SCOPE_STATEMENT,
    )


def _blocking(strip: StatusStrip) -> tuple[str, ...]:
    lines = list(strip.blockers)
    if strip.critical_conflicts:
        lines.insert(
            0, f"Unresolved critical conflict ({strip.critical_conflicts}): both sides shown"
        )
    if strip.holdout_notice:
        lines.append(strip.holdout_notice)
    return tuple(lines)


def bundle_view(bundle_input: BundleInput, registry: EvidenceRegistry) -> BundleView:
    """A page-level view of one validated bundle. Readiness uses the card
    contract's own derivation (never ``machine_decision_ready``)."""
    ctx = _context(bundle_input, registry)
    readiness, blockers = derive_readiness(ctx, set())
    strip = _strip(bundle_input, readiness, [b.value for b in blockers])
    rows = _rows(bundle_input)
    current = _current_conflicts(bundle_input.conflicts)
    return BundleView(
        strip=strip,
        rows=tuple(rows.values()),
        conflicts=tuple(_conflict_view(c, rows) for c in current),
        missing=_missing(bundle_input.bundle.missing_required_producers),
        ambiguous=_ambiguous(bundle_input.bundle.ambiguous_requirements),
        blocking=_blocking(strip),
    )


# --- Cards and lanes ---------------------------------------------------------------------------


def _card_summary(card: SetupCard, history: Sequence[SetupCard], now) -> CardSummary:
    status = display_status(card, history=history, now=now, view_max_age_seconds=None)
    return CardSummary(
        card_id=card.card_id,
        short_id=L.short_id(card.card_id),
        card_kind=card.card_kind,
        lane=card.lane,
        lifecycle_label=L.LIFECYCLE_LABELS[card.lifecycle_state] if card.lifecycle_state else None,
        qualification_label=L.QUALIFICATION_LABELS[card.qualification],
        readiness=card.evidence_readiness,
        readiness_label=L.READINESS_LABELS[card.evidence_readiness],
        blockers=tuple(L.humanize(b.value) for b in card.readiness_blockers),
        display_status_label=L.DISPLAY_STATUS_LABELS[status],
        bundle_id=card.decision_context_bundle_id,
        as_of=L.format_time(card.effective_at_utc),
        revision_number=card.card_revision.revision_number,
        synthetic_definition=True,
        no_setup_text=(
            L.NO_QUALIFIED_SETUP_TEXT + f" Reason: {L.humanize(card.no_setup_reason.value)}"
            if card.card_kind is CardKind.NO_QUALIFIED_SETUP and card.no_setup_reason
            else None
        ),
    )


def _cited_rows(citations, rows: Mapping[str, EvidenceRow]) -> tuple[EvidenceRow, ...]:
    ids = [i for c in citations for i in c.cited_item_ids]
    return tuple(rows[i] for i in dict.fromkeys(ids))


def _detail(record: CardRecord, history: Sequence[SetupCard], now) -> SetupDetailView:
    card, source = record.card, record.source
    rows = _rows(source)
    strip = _strip(source, card.evidence_readiness, [b.value for b in card.readiness_blockers])
    context = _cited_rows(card.context_citations, rows)
    conflicts = {c.conflict_id: c for c in _current_conflicts(source.conflicts)}
    successor = next(
        (c.card_id for c in history if c.card_revision.supersedes_card_id == card.card_id), None
    )
    research = tuple(
        ResearchNoteView(
            item_id=r.item_id,
            study_id=r.study_id,
            result_kind=r.result_kind.value,
            primary_label=r.primary_label,
            secondary_labels=tuple(r.secondary_labels),
            research_status=r.research_status.value,
            holdout_state=r.holdout_state.value,
            caveats=tuple(r.fixed_caveats),
        )
        for r in card.research_references
    )
    provenance = (
        ("Card ID", card.card_id),
        ("Evidence bundle", card.decision_context_bundle_id),
        ("Registry version", card.registry_version_id),
        ("Setup definition", card.setup_definition_id or "none (lane-level conclusion)"),
        ("Card effective at", L.format_time(card.effective_at_utc)),
        ("Evidence observed at", L.format_time(card.observed_at_utc)),
        (
            "Card builder",
            f"{card.provenance.card_builder_id} {card.provenance.card_builder_version}",
        ),
        ("Builder commit", card.provenance.code_commit_sha),
        (
            "Revision",
            f"{card.card_revision.revision_number} ({card.card_revision.revision_reason})",
        ),
    )
    return SetupDetailView(
        summary=_card_summary(card, history, now),
        strip=strip,
        blocking=_blocking(strip),
        supporting=_cited_rows(card.supporting_citations, rows),
        contradicting=_cited_rows(card.contradicting_citations, rows),
        context=tuple(r for r in context if r.kind is not EvidenceKind.CURRENT_INFERENCE),
        inference_context=tuple(r for r in context if r.kind is EvidenceKind.CURRENT_INFERENCE),
        missing=_missing(card.missing_requirements),
        ambiguous=_ambiguous(card.ambiguous_requirements),
        non_current=tuple(
            FreshnessNoteView(
                item_id=n.item_id,
                state=L.FRESHNESS_LABELS[n.state],
                reason=L.humanize(n.reason.value),
                required=n.required,
            )
            for n in card.non_current_entries
        ),
        research=research,
        conflicts=tuple(
            _conflict_view(conflicts[n.conflict_id], rows)
            for n in card.conflicts
            if n.conflict_id in conflicts
        ),
        provenance=provenance,
        history=tuple(_card_summary(c, history, now) for c in history),
        newer_card_id=successor,
        selector_availability=card.selector_availability,
        manual_decision=card.manual_decision_statement,
    )


def _contract_review(record: CardRecord) -> ContractReviewView:
    card = record.card
    section = card.contracts
    selector_as_of = selector_freshness = None
    if section is not None:
        entry = next(
            e for e in record.source.bundle.entries if e.item_id == section.selector_result_item_id
        )
        item = record.source.items[entry.item_id]
        selector_as_of = L.format_time(item.provenance.source_observed_at_utc)
        fresh = entry.freshness
        selector_freshness = L.freshness_label(fresh.state, fresh.reason.value, fresh.age_seconds)
    return ContractReviewView(
        card_id=card.card_id,
        bundle_id=card.decision_context_bundle_id,
        availability=card.selector_availability,
        availability_label=L.SELECTOR_AVAILABILITY_LABELS[card.selector_availability],
        outcome_label=L.SELECTOR_OUTCOME_LABELS[section.selector_outcome] if section else None,
        feed=section.feed.value if section else None,
        selector_as_of=selector_as_of,
        selector_freshness=selector_freshness,
        # Copied exactly as the selector returned them; never merged.
        eligible=tuple(section.eligible_contracts) if section else (),
        research_only=tuple(section.research_only_contracts) if section else (),
        rejection_counts=tuple(
            sorted((reason.value, count) for reason, count in section.rejection_counts.items())
        )
        if section
        else (),
        indicative_warning=bool(section and section.feed.value == "indicative"),
        manual_decision=card.manual_decision_statement,
    )


def _vwap_lane(lane: LaneInput, registry: EvidenceRegistry, summaries) -> LaneView:
    title = "VWAP reversion"
    if not lane.definitions:
        return LaneView(
            Lane.VWAP_REVERSION,
            title,
            LaneQualificationAvailability.NOT_AUTHORIZED,
            L.VWAP_LANE_TEXT[LaneQualificationAvailability.NOT_AUTHORIZED],
            (),
            (),
        )
    if lane.history:
        latest = lane.history[-1].card
        if latest.card_kind is CardKind.NO_QUALIFIED_SETUP:
            ctx = _context(lane.history[-1].source, registry, lane.definitions)
            availability, _ = lane_qualification(ctx, latest.lane, card_cited_ids(latest))
            if availability is not LaneQualificationAvailability.AVAILABLE:
                raise DashboardInputError("no_setup_card_lane_not_available")
        return LaneView(
            Lane.VWAP_REVERSION,
            title,
            None,
            L.SYNTHETIC_DEFINITION_NOTE,
            (),
            (summaries[latest.card_id],),
        )
    availability, blockers = lane_qualification(
        _context(lane.context, registry, lane.definitions), Lane.VWAP_REVERSION
    )
    if availability is LaneQualificationAvailability.AVAILABLE:
        raise DashboardInputError("lane_conclusion_without_card")
    return LaneView(
        Lane.VWAP_REVERSION,
        title,
        availability,
        L.VWAP_LANE_TEXT[availability],
        tuple(L.humanize(b.value) for b in blockers),
        (),
    )


def _trend_lane(lane: LaneInput, registry: EvidenceRegistry) -> LaneView:
    availability, _ = lane_qualification(
        _context(lane.context, registry, lane.definitions), Lane.TREND_CONTINUATION
    )
    if availability is not LaneQualificationAvailability.NOT_AUTHORIZED:
        raise DashboardInputError("trend_lane_unexpected_state")
    return LaneView(
        Lane.TREND_CONTINUATION, "Trend continuation", availability, L.TREND_LANE_TEXT, (), ()
    )


def _system(scenario: Scenario, market: BundleView, premarket: BundleInput) -> SystemStatusView:
    registry = scenario.registry
    research = tuple(
        ResearchNoteView(
            item_id=item.item_id,
            study_id=ref.study_id,
            result_kind=ref.result_kind.value,
            primary_label=ref.primary_label,
            secondary_labels=tuple(ref.secondary_labels),
            research_status=ref.research_status.value,
            holdout_state=ref.holdout_state.value,
            caveats=tuple(ref.fixed_caveats),
        )
        for item in premarket.items.values()
        if (ref := item.research_reference) is not None
    )

    def window(policy: Any) -> str:
        if policy.timeless:
            return "timeless"
        if policy.current_until_seconds is None:
            return "live thresholds unset"
        return (
            f"current {policy.current_until_seconds} s, stale after {policy.stale_after_seconds} s"
        )

    return SystemStatusView(
        research=research,
        research_stages=RESEARCH_STAGES,
        producers=tuple(
            (p.producer_id, p.producer_type.value, p.implementation_status.value)
            for p in registry.producers
        ),
        registry_version_id=premarket.bundle.registry_version_id,
        registry_label=registry.registry_label,
        clock=market.strip.clock,
        freshness_policies=tuple((p.policy_id, window(p)) for p in registry.freshness_policies),
        holdout_guard=(
            "Active. SPY price evidence dated 2026-09-23 through 2026-12-04 is withheld from "
            "every bundle until the holdout is recorded."
        ),
        authorization=AUTHORIZATION,
    )


# --- The whole scenario --------------------------------------------------------------------------


def present(scenario: Scenario) -> DashboardModel:
    """Validate everything in ``scenario``, then derive its views."""
    validate_scenario(scenario)
    registry = scenario.registry
    market = bundle_view(scenario.market, registry)
    lane = scenario.vwap
    history = [r.card for r in lane.history]
    now = lane.context.bundle.as_of_utc  # deterministic: never the wall clock
    summaries = {c.card_id: _card_summary(c, history, now) for c in history}
    return DashboardModel(
        scenario_id=scenario.scenario_id,
        title=scenario.title,
        summary=scenario.summary,
        market=market,
        premarket=bundle_view(scenario.premarket, registry),
        vwap_lane=_vwap_lane(lane, registry, summaries),
        trend_lane=_trend_lane(lane, registry),
        cards={r.card.card_id: _detail(r, history, now) for r in lane.history},
        contract_reviews={r.card.card_id: _contract_review(r) for r in lane.history},
        system=_system(scenario, market, scenario.premarket),
    )
