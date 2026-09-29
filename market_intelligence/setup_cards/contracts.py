"""Strict, immutable ``setup-card-1`` contracts (docs/SETUP_CARD_CONTRACT.md §3-§6).

A setup card is an evidence-backed **presentation object**. It is not
evidence, a trading signal, a recommendation or an order: it has no order,
quantity, position-size, price-target, brokerage or execution field, and the
module refuses to define one.

This module holds the card's intrinsic rules. Rules that need the card's
Evidence Bundle (citation resolution, evidence-list consistency, readiness
derivation, selector matching, research validation) live in
``validation.py``.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from market_intelligence.contract_selection.contracts import RejectionReason
from market_intelligence.evidence.canonical import canonical_json_bytes, prefixed_id, remove_paths
from market_intelligence.evidence.contracts import (
    SUBJECT_PATTERNS,
    AmbiguousRequirement,
    EvidenceCitation,
    MissingProducer,
)
from market_intelligence.evidence.enums import (
    CitationRole,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    EvidenceKind,
    Feed,
    FreshnessReason,
    FreshnessState,
    HoldoutState,
    ResearchStatus,
    ResultKind,
    SubjectType,
)
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    BundleId,
    CommitSha,
    ConfigIdentity,
    ConflictId,
    ItemId,
    RegistryVersionId,
    SessionDate,
    Token,
    UtcTimestamp,
    VersionLabel,
)
from market_intelligence.setup_cards.definitions import lifecycle_qualification_compatible
from market_intelligence.setup_cards.enums import (
    QUALIFICATION_ITEM_REQUIRED,
    CardKind,
    CardRevisionReason,
    EvidenceReadiness,
    Lane,
    NoSetupReason,
    ReadinessBlocker,
    SelectorAvailability,
    SelectorOutcome,
    SetupLifecycle,
    SetupQualification,
)

CARD_SCHEMA_VERSION = "setup-card-1"
CARD_PREFIX = "scd1_"
SPY_SUBJECT = "instrument:us_equity:SPY"
MANUAL_DECISION_STATEMENT = (
    "Decision support only. You make every trading decision manually. No order is placed."
)
MAX_CITATIONS = 32

_EASTERN = ZoneInfo("America/New_York")
_OSI = Annotated[str, Field(pattern=r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")]

# Field-name fragments that would indicate an execution surface (§4.7).
FORBIDDEN_FIELD_FRAGMENTS = (
    "order",
    "quantity",
    "qty",
    "position_size",
    "size",
    "stop",
    "target",
    "entry",
    "exit",
    "broker",
    "account",
    "execut",
    "trade",
)


def _sorted_unique(values: list[Any], what: str) -> None:
    keys = [
        canonical_json_bytes(v.model_dump(mode="json") if isinstance(v, BaseModel) else v)
        for v in values
    ]
    if len(set(keys)) != len(keys) or keys != sorted(keys):
        raise ValueError(f"{what} must be sorted and unique")


# --- Nested contracts -----------------------------------------------------------------


class CardFreshnessNote(BaseModel):
    model_config = STRICT_FROZEN

    item_id: ItemId
    state: Literal[FreshnessState.AGING, FreshnessState.STALE, FreshnessState.UNKNOWN]
    reason: FreshnessReason
    required: bool


class CardConflictNote(BaseModel):
    model_config = STRICT_FROZEN

    conflict_id: ConflictId
    conflict_type: ConflictType
    severity: ConflictSeverity
    status: ConflictStatus  # the current status record's
    involved_item_ids: Annotated[list[ItemId], Field(min_length=2, max_length=16)]


class CardResearchNote(BaseModel):
    model_config = STRICT_FROZEN

    item_id: ItemId
    study_id: Token
    result_kind: ResultKind
    primary_label: Token
    secondary_labels: Annotated[list[Token], Field(max_length=8)]
    research_status: ResearchStatus
    holdout_state: HoldoutState
    fixed_caveats: Annotated[list[Token], Field(min_length=1, max_length=16)]


class CardContractSection(BaseModel):
    """Present only when selector availability is ``available``. The outcome
    is exactly one of the selector's own four values."""

    model_config = STRICT_FROZEN

    selector_result_item_id: ItemId
    selector_outcome: SelectorOutcome
    feed: Literal[Feed.OPRA, Feed.INDICATIVE]
    eligible_contracts: Annotated[list[_OSI], Field(max_length=5000)]
    research_only_contracts: Annotated[list[_OSI], Field(max_length=5000)]
    rejection_counts: dict[RejectionReason, int]

    @model_validator(mode="after")
    def _check_sets(self) -> CardContractSection:
        for values in (self.eligible_contracts, self.research_only_contracts):
            if values != sorted(set(values)):
                raise ValueError("returned contract lists must be sorted and unique")
        if set(self.eligible_contracts) & set(self.research_only_contracts):
            raise ValueError("eligible and research-only contracts never overlap")
        return self


class CardRevision(BaseModel):
    model_config = STRICT_FROZEN

    revision_number: Annotated[int, Field(ge=1, le=10_000)]
    supersedes_card_id: Annotated[str, Field(pattern=r"^scd1_[0-9a-f]{64}$")] | None = None
    revision_reason: CardRevisionReason

    @model_validator(mode="after")
    def _check_revision(self) -> CardRevision:
        original = self.revision_number == 1
        if original != (self.supersedes_card_id is None):
            raise ValueError("supersedes_card_id is null iff revision_number is 1")
        if original != (self.revision_reason is CardRevisionReason.ORIGINAL):
            raise ValueError("revision_reason is original iff revision_number is 1")
        return self


ORIGINAL_CARD_REVISION = CardRevision(
    revision_number=1, revision_reason=CardRevisionReason.ORIGINAL
)


class CardProvenance(BaseModel):
    """``generated_at_utc`` is operational and excluded from the card identity."""

    model_config = STRICT_FROZEN

    card_builder_id: VersionLabel
    card_builder_version: VersionLabel
    code_commit_sha: CommitSha
    code_tree_clean: bool
    configuration_identity: ConfigIdentity
    generated_at_utc: UtcTimestamp


# --- The card ------------------------------------------------------------------------------

_Citations = Annotated[list[EvidenceCitation], Field(max_length=MAX_CITATIONS)]


class SetupCardContent(BaseModel):
    """Every substantive field of a card (everything but its ID)."""

    model_config = STRICT_FROZEN

    schema_version: Literal["setup-card-1"] = CARD_SCHEMA_VERSION
    card_kind: CardKind
    underlying: Literal["instrument:us_equity:SPY"] = SPY_SUBJECT
    lane: Lane
    setup_subject_id: Annotated[str, Field(max_length=160)] | None = None
    setup_definition_id: VersionLabel | None = None
    session_date: SessionDate
    effective_at_utc: UtcTimestamp
    observed_at_utc: UtcTimestamp | None = None
    decision_context_bundle_id: BundleId
    registry_version_id: RegistryVersionId
    evidence_readiness: EvidenceReadiness
    readiness_blockers: Annotated[list[ReadinessBlocker], Field(max_length=8)]
    qualification: SetupQualification
    qualification_item_id: ItemId | None = None
    lifecycle_state: SetupLifecycle | None = None
    no_setup_reason: NoSetupReason | None = None
    # Reserved and must stay empty in this version: no ranking rubric,
    # inference-input authorization or scenario definition exists.
    deterministic_rank: None = None
    interpretation: None = None
    supporting_citations: _Citations = Field(default_factory=list)
    contradicting_citations: _Citations = Field(default_factory=list)
    context_citations: _Citations = Field(default_factory=list)
    missing_requirements: Annotated[list[MissingProducer], Field(max_length=32)]
    ambiguous_requirements: Annotated[list[AmbiguousRequirement], Field(max_length=32)]
    non_current_entries: Annotated[list[CardFreshnessNote], Field(max_length=2000)]
    conflicts: Annotated[list[CardConflictNote], Field(max_length=256)]
    research_references: Annotated[list[CardResearchNote], Field(max_length=32)]
    scenario_relations: Annotated[list[Token], Field(max_length=0)] = Field(default_factory=list)
    selector_availability: SelectorAvailability
    contracts: CardContractSection | None = None
    manual_decision_statement: Literal[
        "Decision support only. You make every trading decision manually. No order is placed."
    ] = MANUAL_DECISION_STATEMENT
    expires_at_utc: UtcTimestamp | None = None
    manual_review_ready: bool
    card_revision: CardRevision
    provenance: CardProvenance

    @model_validator(mode="after")
    def _check_card(self) -> SetupCardContent:
        self._check_kind()
        self._check_states()
        self._check_citations()
        self._check_lists()
        self._check_selector()
        if self.effective_at_utc.astimezone(_EASTERN).date() != self.session_date:
            raise ValueError("session_date must be the New York date of effective_at_utc")
        if self.observed_at_utc is not None and self.observed_at_utc > self.effective_at_utc:
            raise ValueError("observed_at_utc cannot follow effective_at_utc")
        return self

    def _check_kind(self) -> None:
        setup = self.card_kind is CardKind.SETUP
        if setup != (self.setup_subject_id is not None):
            raise ValueError("setup_subject_id is required iff the card is a setup card")
        if setup != (self.setup_definition_id is not None):
            raise ValueError("setup_definition_id is required iff the card is a setup card")
        if setup != (self.lifecycle_state is not None):
            raise ValueError("lifecycle_state is required iff the card is a setup card")
        if setup == (self.no_setup_reason is not None):
            raise ValueError("no_setup_reason is required iff the card is no_qualified_setup")
        if self.setup_subject_id is not None:
            if not SUBJECT_PATTERNS[SubjectType.SETUP_CANDIDATE].fullmatch(self.setup_subject_id):
                raise ValueError("setup_subject_id must be a canonical setup subject")
            if self.setup_subject_id.split(":")[1] != self.lane.value:
                raise ValueError("the setup subject's lane must equal the card lane")
            if self.setup_subject_id.split(":")[3] != "SPY":
                raise ValueError("the setup subject must be for SPY")
        if not setup and self.selector_availability is not SelectorAvailability.NOT_REQUESTED:
            raise ValueError("a no_qualified_setup card never requests the selector")

    def _check_states(self) -> None:
        q = self.qualification
        if (q in QUALIFICATION_ITEM_REQUIRED) != (self.qualification_item_id is not None):
            raise ValueError("qualification_item_id is required iff qualified or not_qualified")
        if self.lane is Lane.TREND_CONTINUATION and q is not SetupQualification.NOT_EVALUATED:
            raise ValueError("the trend lane has no research or definition: not_evaluated only")
        if self.card_kind is CardKind.SETUP and not lifecycle_qualification_compatible(
            self.lifecycle_state, q
        ):
            raise ValueError("lifecycle and qualification are incompatible")
        if self.card_kind is CardKind.NO_QUALIFIED_SETUP:
            # A no_qualified_setup card only presents a cited, authorized
            # deterministic not_qualified conclusion. "No authorized
            # conclusion" (not_evaluated) never becomes a card.
            if q is not SetupQualification.NOT_QUALIFIED:
                raise ValueError("a no_qualified_setup card needs a not_qualified conclusion")
            # Blocked evidence is no conclusion: it never becomes this card.
            if self.evidence_readiness is not EvidenceReadiness.READY:
                raise ValueError("a no_qualified_setup card needs ready evidence")
        blocked = self.evidence_readiness is EvidenceReadiness.BLOCKED
        if blocked != bool(self.readiness_blockers):
            raise ValueError("readiness_blockers are non-empty iff readiness is blocked")
        if self.manual_review_ready != (self.evidence_readiness is EvidenceReadiness.READY):
            raise ValueError("manual_review_ready must equal evidence_readiness = ready")

    def _check_citations(self) -> None:
        for citations, role in (
            (self.supporting_citations, CitationRole.SUPPORTS_CLAIM),
            (self.contradicting_citations, CitationRole.CONTRADICTS_CLAIM),
            (self.context_citations, CitationRole.CONTEXT),
        ):
            for citation in citations:
                if citation.citation_role is not role:
                    raise ValueError("a citation list may hold only its own role")
                if citation.bundle_id != self.decision_context_bundle_id:
                    raise ValueError("every citation must resolve inside the card's bundle")
        for citation in (*self.supporting_citations, *self.contradicting_citations):
            if EvidenceKind.CURRENT_INFERENCE in citation.cited_kinds:
                raise ValueError("inference may appear only as labelled context")
        indexes = [
            c.claim_index
            for c in (
                *self.supporting_citations,
                *self.contradicting_citations,
                *self.context_citations,
            )
        ]
        if len(set(indexes)) != len(indexes):
            raise ValueError("claim indexes must be unique across the card")

    def _check_lists(self) -> None:
        _sorted_unique(self.readiness_blockers, "readiness_blockers")
        _sorted_unique([n.item_id for n in self.non_current_entries], "non_current_entries")
        _sorted_unique([n.conflict_id for n in self.conflicts], "conflicts")
        _sorted_unique([n.item_id for n in self.research_references], "research_references")

    def _check_selector(self) -> None:
        available = self.selector_availability is SelectorAvailability.AVAILABLE
        if available != (self.contracts is not None):
            raise ValueError("a contract section is present iff the selector is available")


CARD_EXCLUDED_PATHS = (("provenance", "generated_at_utc"),)


def card_identity_payload(card: SetupCardContent) -> dict[str, Any]:
    dump = card.model_dump(mode="json")
    dump.pop("card_id", None)
    return remove_paths(dump, CARD_EXCLUDED_PATHS)


def compute_card_id(card: SetupCardContent) -> str:
    return prefixed_id(CARD_PREFIX, card_identity_payload(card))


class SetupCard(SetupCardContent):
    """A sealed, immutable card: ``card_id`` must equal the recomputed identity."""

    card_id: Annotated[str, Field(pattern=r"^scd1_[0-9a-f]{64}$")]

    @model_validator(mode="after")
    def _check_identity(self) -> SetupCard:
        if self.card_id != compute_card_id(self):
            raise ValueError("card_id does not match the recomputed identity")
        if self.card_revision.supersedes_card_id == self.card_id:
            raise ValueError("a card cannot supersede itself")
        return self


def seal_card(content: SetupCardContent) -> SetupCard:
    fields = {name: getattr(content, name) for name in SetupCardContent.model_fields}
    return SetupCard(**fields, card_id=compute_card_id(content))


def _field_names(model: type[BaseModel]) -> set[str]:
    names: set[str] = set()
    for name, field in model.model_fields.items():
        names.add(name)
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            names |= _field_names(annotation)
    return names


def execution_field_names() -> set[str]:
    """Card field names that look like an execution surface (must be empty)."""
    allowed = {"card_revision", "revision_reason", "session_date"}
    found: set[str] = set()
    for model in (
        SetupCard,
        CardContractSection,
        CardRevision,
        CardProvenance,
        CardFreshnessNote,
        CardConflictNote,
        CardResearchNote,
    ):
        for name in _field_names(model):
            if name in allowed:
                continue
            if any(re.search(rf"(^|_){fragment}", name) for fragment in FORBIDDEN_FIELD_FRAGMENTS):
                found.add(name)
    return found
