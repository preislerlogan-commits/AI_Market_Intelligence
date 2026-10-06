"""Frozen presentation views for the offline dashboard prototype.

Plain, render-ready values derived from validated bundles and cards by
``adapter.py``. They are not a second evidence or setup model: every field is
copied or formatted from an Evidence Envelope or ``setup-card-1`` object, and
nothing here is persisted. No UI framework is imported.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from market_intelligence.evidence.enums import EvidenceKind, FreshnessState
from market_intelligence.setup_cards.enums import (
    CardKind,
    EvidenceReadiness,
    Lane,
    LaneQualificationAvailability,
    SelectorAvailability,
)


@dataclass(frozen=True)
class EvidenceRow:
    item_id: str
    short_id: str
    kind: EvidenceKind
    kind_label: str
    producer_id: str
    required: bool
    freshness_state: FreshnessState
    freshness: str
    effective_at: str
    observed_at: str
    summary: str
    superseded: bool
    conflict_ids: tuple[str, ...]


@dataclass(frozen=True)
class ConflictView:
    conflict_id: str
    short_id: str
    conflict_type: str
    severity: str
    status: str
    sides: tuple[EvidenceRow, ...]


@dataclass(frozen=True)
class MissingView:
    requirement_id: str
    producer_id: str
    reason: str
    holdout_restricted: bool


@dataclass(frozen=True)
class AmbiguityView:
    requirement_id: str
    reason: str
    competing_item_ids: tuple[str, ...]
    matching_conflict_ids: tuple[str, ...]


@dataclass(frozen=True)
class StatusStrip:
    bundle_id: str
    bundle_short_id: str
    purpose: str
    as_of: str
    clock: str
    readiness: EvidenceReadiness
    readiness_label: str
    blockers: tuple[str, ...]
    missing_count: int
    non_current_count: int
    material_conflicts: int
    critical_conflicts: int
    registry_short_id: str
    holdout_notice: str | None
    scope: str


@dataclass(frozen=True)
class BundleView:
    strip: StatusStrip
    rows: tuple[EvidenceRow, ...]
    conflicts: tuple[ConflictView, ...]
    missing: tuple[MissingView, ...]
    ambiguous: tuple[AmbiguityView, ...]
    blocking: tuple[str, ...]

    def rows_of(self, *kinds: EvidenceKind) -> tuple[EvidenceRow, ...]:
        return tuple(r for r in self.rows if r.kind in kinds)

    @property
    def non_current(self) -> tuple[EvidenceRow, ...]:
        ready = (FreshnessState.CURRENT, FreshnessState.TIMELESS)
        return tuple(r for r in self.rows if r.freshness_state not in ready)


@dataclass(frozen=True)
class ResearchNoteView:
    item_id: str
    study_id: str
    result_kind: str
    primary_label: str
    secondary_labels: tuple[str, ...]
    research_status: str
    holdout_state: str
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class FreshnessNoteView:
    item_id: str
    state: str
    reason: str
    required: bool


@dataclass(frozen=True)
class CardSummary:
    card_id: str
    short_id: str
    card_kind: CardKind
    lane: Lane
    lifecycle_label: str | None
    qualification_label: str
    readiness: EvidenceReadiness
    readiness_label: str
    blockers: tuple[str, ...]
    display_status_label: str
    bundle_id: str
    as_of: str
    revision_number: int
    synthetic_definition: bool
    no_setup_text: str | None


@dataclass(frozen=True)
class LaneView:
    lane: Lane
    title: str
    availability: LaneQualificationAvailability | None
    text: str
    blockers: tuple[str, ...]
    cards: tuple[CardSummary, ...]


@dataclass(frozen=True)
class SetupDetailView:
    summary: CardSummary
    strip: StatusStrip
    blocking: tuple[str, ...]
    supporting: tuple[EvidenceRow, ...]
    contradicting: tuple[EvidenceRow, ...]
    context: tuple[EvidenceRow, ...]
    inference_context: tuple[EvidenceRow, ...]
    missing: tuple[MissingView, ...]
    ambiguous: tuple[AmbiguityView, ...]
    non_current: tuple[FreshnessNoteView, ...]
    research: tuple[ResearchNoteView, ...]
    conflicts: tuple[ConflictView, ...]
    provenance: tuple[tuple[str, str], ...]
    history: tuple[CardSummary, ...]
    newer_card_id: str | None
    selector_availability: SelectorAvailability
    manual_decision: str


@dataclass(frozen=True)
class ContractReviewView:
    card_id: str
    bundle_id: str
    availability: SelectorAvailability
    availability_label: str
    outcome_label: str | None
    feed: str | None
    selector_as_of: str | None
    selector_freshness: str | None
    eligible: tuple[str, ...]
    research_only: tuple[str, ...]
    rejection_counts: tuple[tuple[str, int], ...]
    indicative_warning: bool
    manual_decision: str


@dataclass(frozen=True)
class SystemStatusView:
    research: tuple[ResearchNoteView, ...]
    research_stages: tuple[tuple[str, str], ...]
    producers: tuple[tuple[str, str, str], ...]
    registry_version_id: str
    registry_label: str
    clock: str
    freshness_policies: tuple[tuple[str, str], ...]
    holdout_guard: str
    authorization: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DashboardModel:
    """Everything one synthetic scenario renders, derived only after every
    bundle and card in it passed validation."""

    scenario_id: str
    title: str
    summary: str
    market: BundleView
    premarket: BundleView
    vwap_lane: LaneView
    trend_lane: LaneView
    cards: Mapping[str, SetupDetailView] = field(default_factory=dict)
    contract_reviews: Mapping[str, ContractReviewView] = field(default_factory=dict)
    system: SystemStatusView | None = None
