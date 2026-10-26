"""Deterministic explanations of one selected setup card and its bundle.

The assistant answers a fixed set of questions about the card the user has
open, from that card's own ``setup_detail`` bundle only:

- **Bounded retrieval.** It reads exactly the selected card, the bundle the
  card names and the items and conflicts that bundle lists. A selection
  carrying anything else, a card naming another bundle, or a handoff whose
  card or bundle ID differs from the selection is refused.
- **Granted, read-only, synthetic.** The registry the bundle was built under
  must be a synthetic one and must give the ``assistant`` consumer a
  read-only, non-machine-decision grant for ``setup_detail`` covering every
  kind in the bundle (``EVIDENCE_CONSUMER_RULES.md`` §3).
- **Cited, labelled statements.** Every statement cites evidence inside the
  bundle with the core ``EvidenceCitation`` (checked by
  ``validate_citations``) and is labelled fact, calculation, research
  result, inference, missing evidence or stale evidence.
- **Two reference types, never swapped.** An ``evidence_item`` reference is a
  core ``EvidenceCitation`` that must resolve inside the bundle. A required
  input with no item at all gets a ``missing_requirement`` reference instead,
  which must resolve to the bundle's own missing-requirement record and is
  allowed only on missing-evidence statements. A requirement is never
  presented as an evidence citation.
- **Inference kept apart.** Labelled inference is returned separately and is
  never presented as supporting or contradicting evidence.
- **Selector authority.** Contract answers repeat the Contract Selector's own
  outcome and returned sets, checked with ``validate_contract_view``; no
  rejected contract is named and nothing is re-ranked or reclassified.
- **Nothing invented.** Values are copied from payloads; there is no
  probability, confidence, price target, direction or recommendation. An
  unsupported question, or one the evidence cannot answer, says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from market_intelligence.contract_selection.contracts import ContractSelectorResult
from market_intelligence.evidence.bundle import _READ_PERMISSION
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.contracts import (
    EvidenceBundleManifest,
    EvidenceCitation,
    EvidenceConflict,
    EvidenceItem,
)
from market_intelligence.evidence.enums import (
    BundlePurpose,
    CitationRole,
    ConflictSeverity,
    ConsumerId,
    ConsumerPermission,
    EvidenceKind,
    MissingProducerReason,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_SELECTOR_PAYLOAD_ID,
    ContractView,
    validate_contract_view,
)
from market_intelligence.evidence.validation import validate_citations
from market_intelligence.setup_cards.contracts import SetupCard
from market_intelligence.setup_cards.definitions import SETUP_EVALUATION_PAYLOAD
from market_intelligence.setup_cards.enums import SelectorAvailability

SYNTHETIC_REGISTRY_PREFIX = "synthetic-"
NOT_ANSWERABLE = "Not answerable from this evidence."
SCOPE = (
    "Synthetic, offline and explanatory only. Deterministic templates over the selected "
    "card's own evidence bundle: no model, no live data, no forecast, probability, "
    "direction or advice, and no order capability. Any decision is manual."
)
_READ_ONLY = frozenset(
    {
        ConsumerPermission.READ_FACTS,
        ConsumerPermission.READ_CALCULATIONS,
        ConsumerPermission.READ_RESEARCH,
        ConsumerPermission.READ_INFERENCES,
        ConsumerPermission.READ_CONFLICTS,
    }
)


class QuestionId(StrEnum):
    QUALIFICATION = "qualification"
    SUPPORTING = "supporting"
    CONTRADICTING = "contradicting"
    MISSING_OR_STALE = "missing_or_stale"
    CONTRACT_STATUS = "contract_status"
    MARKET_AND_RESEARCH = "market_and_research"


QUESTIONS: Mapping[QuestionId, str] = {
    QuestionId.QUALIFICATION: "Why is this setup qualified or not qualified?",
    QuestionId.SUPPORTING: "What evidence supports it?",
    QuestionId.CONTRADICTING: "What evidence contradicts it?",
    QuestionId.MISSING_OR_STALE: "What information is missing or stale?",
    QuestionId.CONTRACT_STATUS: "Why is this contract eligible or research-only?",
    QuestionId.MARKET_AND_RESEARCH: "What does the current market or research status mean?",
}


class StatementLabel(StrEnum):
    FACT = "fact"
    CALCULATION = "calculation"
    RESEARCH_RESULT = "research_result"
    INFERENCE = "inference"
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"


_LABEL: Mapping[EvidenceKind, StatementLabel] = {
    EvidenceKind.CONFIRMED_FACT: StatementLabel.FACT,
    EvidenceKind.DETERMINISTIC_CALCULATION: StatementLabel.CALCULATION,
    EvidenceKind.HISTORICAL_RESEARCH_RESULT: StatementLabel.RESEARCH_RESULT,
    EvidenceKind.CURRENT_INFERENCE: StatementLabel.INFERENCE,
    EvidenceKind.MISSING_EVIDENCE: StatementLabel.MISSING_EVIDENCE,
    EvidenceKind.STALE_EVIDENCE: StatementLabel.STALE_EVIDENCE,
}


class ReferenceType(StrEnum):
    EVIDENCE_ITEM = "evidence_item"
    MISSING_REQUIREMENT = "missing_requirement"


class AssistantRefusal(Exception):
    """The selection cannot be explained. Carries a bounded token only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class MissingRequirementReference:
    """Not an evidence citation: a pointer to one record in the bundle's own
    ``missing_required_producers``, for a required input with no item."""

    bundle_id: str
    requirement_id: str
    producer_id: str
    reason: MissingProducerReason


@dataclass(frozen=True)
class Statement:
    """One labelled sentence with exactly one reference of its declared type:
    an evidence-item citation, or a missing-requirement reference (only on a
    missing-evidence statement)."""

    label: StatementLabel
    text: str
    reference_type: ReferenceType
    evidence_citation: EvidenceCitation | None = None
    missing_requirement: MissingRequirementReference | None = None

    def __post_init__(self) -> None:
        if self.reference_type is ReferenceType.EVIDENCE_ITEM:
            valid = (
                isinstance(self.evidence_citation, EvidenceCitation)
                and self.missing_requirement is None
            )
        elif self.reference_type is ReferenceType.MISSING_REQUIREMENT:
            valid = (
                isinstance(self.missing_requirement, MissingRequirementReference)
                and self.evidence_citation is None
                and self.label is StatementLabel.MISSING_EVIDENCE
            )
        else:
            valid = False
        if not valid:
            raise AssistantRefusal("reference_type_mismatch")


@dataclass(frozen=True)
class Answer:
    question_id: QuestionId | None
    question: str
    card_id: str
    bundle_id: str
    answerable: bool
    headline: str
    statements: tuple[Statement, ...]
    # Labelled inference only; never supporting or contradicting evidence.
    inference: tuple[Statement, ...] = ()
    scope: str = SCOPE


@dataclass(frozen=True)
class Selection:
    """The card open in the dashboard and exactly the bundle it names."""

    card: SetupCard
    bundle: EvidenceBundleManifest
    items: Mapping[str, EvidenceItem]
    conflicts: Mapping[str, EvidenceConflict]
    registry: EvidenceRegistry


# --- Selection checks ---------------------------------------------------------------------------


def check_selection(selection: Selection, *, card_id: str, bundle_id: str) -> None:
    """Refuse anything but the selected card, its own bundle and that
    bundle's evidence, under a synthetic, read-only assistant grant."""
    card, bundle, registry = selection.card, selection.bundle, selection.registry
    if not registry.registry_label.startswith(SYNTHETIC_REGISTRY_PREFIX):
        raise AssistantRefusal("assistant_synthetic_only")
    grant = registry.consumer_grant(ConsumerId.ASSISTANT)
    if grant is None or BundlePurpose.SETUP_DETAIL not in grant.purposes:
        raise AssistantRefusal("assistant_not_granted")
    if grant.machine_decision_mode_allowed or not set(grant.permissions) <= _READ_ONLY:
        raise AssistantRefusal("assistant_grant_not_read_only")
    if card.card_id != card_id or bundle.bundle_id != bundle_id:
        raise AssistantRefusal("handoff_mismatch")
    if card.decision_context_bundle_id != bundle.bundle_id:
        raise AssistantRefusal("card_bundle_mismatch")
    if bundle.purpose is not BundlePurpose.SETUP_DETAIL:
        raise AssistantRefusal("bundle_purpose_not_granted")
    if bundle.registry_version_id != compute_registry_version_id(registry):
        raise AssistantRefusal("bundle_registry_mismatch")
    entries = {e.item_id: e for e in bundle.entries}
    if not set(selection.items) <= set(entries) or not set(selection.conflicts) <= set(
        bundle.conflict_ids
    ):
        raise AssistantRefusal("evidence_outside_bundle")
    if set(selection.items) != set(entries) or set(selection.conflicts) != set(bundle.conflict_ids):
        raise AssistantRefusal("bundle_evidence_incomplete")
    for item_id, item in selection.items.items():
        if item.item_id != item_id or item.evidence_kind is not entries[item_id].evidence_kind:
            raise AssistantRefusal("evidence_outside_bundle")
        needed = _READ_PERMISSION[item.evidence_kind]
        if needed is not None and needed not in grant.permissions:
            raise AssistantRefusal("permission_not_granted")


# --- Statement building -------------------------------------------------------------------------


class _Builder:
    def __init__(self, selection: Selection) -> None:
        self.s = selection
        self.entries = {e.item_id: e for e in selection.bundle.entries}
        self.count = 0

    def cite(
        self,
        item_id: str,
        role: CitationRole,
        text: str,
        *,
        label: StatementLabel | None = None,
        conflict_ids: Sequence[str] = (),
    ) -> Statement:
        item = self.s.items[item_id]
        citation = EvidenceCitation(
            bundle_id=self.s.bundle.bundle_id,
            claim_index=self.count,
            cited_item_ids=[item_id],
            citation_role=role,
            cited_kinds=[item.evidence_kind],
            cited_conflict_ids=sorted(conflict_ids),
        )
        self.count += 1
        return Statement(
            label or _LABEL[item.evidence_kind],
            text,
            ReferenceType.EVIDENCE_ITEM,
            evidence_citation=citation,
        )

    def missing_requirement(self, requirement) -> Statement:
        reference = MissingRequirementReference(
            bundle_id=self.s.bundle.bundle_id,
            requirement_id=requirement.requirement_id,
            producer_id=requirement.producer_id,
            reason=requirement.reason,
        )
        return Statement(
            StatementLabel.MISSING_EVIDENCE,
            f"Required input `{requirement.requirement_id}` from `{requirement.producer_id}` is "
            f"missing ({_words(requirement.reason.value)}); the bundle holds no item for it.",
            ReferenceType.MISSING_REQUIREMENT,
            missing_requirement=reference,
        )

    def freshness(self, item_id: str) -> str:
        return self.entries[item_id].freshness.state.value

    def describe(self, item_id: str) -> str:
        item = self.s.items[item_id]
        return (
            f"{_kind(item.evidence_kind)} from `{item.provenance.producer_id}`: "
            f"{_payload_summary(item)} (freshness {self.freshness(item_id)})"
        )


def _words(token: str) -> str:
    return token.replace("_", " ")


def _kind(kind: EvidenceKind) -> str:
    return _words(_LABEL[kind].value).capitalize()


def _payload_summary(item: EvidenceItem) -> str:
    """Recorded values only, copied from the payload; nothing is derived."""
    payload = item.payload
    if item.payload_schema_id == CONTRACT_SELECTOR_PAYLOAD_ID:
        return f"Contract Selector result, outcome `{payload.get('status')}`"
    if item.payload_schema_id == SETUP_EVALUATION_PAYLOAD:
        return (
            f"setup evaluation, qualification `{payload.get('qualification')}`, "
            f"lifecycle `{payload.get('lifecycle_state')}`"
        )
    if item.research_reference is not None:
        ref = item.research_reference
        return f"study `{ref.study_id}`, recorded label `{ref.primary_label}`"
    parts = [
        f"{key} {value}"
        for key, value in sorted(payload.items())
        if isinstance(value, str | int | float | bool) or value is None
    ]
    text = "; ".join(parts) or "no displayable values"
    return text if len(text) <= 160 else text[:157] + "..."


# --- Answers ------------------------------------------------------------------------------------


def _missing_and_stale(b: _Builder, *, required_only: bool = False) -> list[Statement]:
    card = b.s.card
    statements: list[Statement] = []
    cited_missing: set[str] = set()
    for requirement in card.missing_requirements:
        notices = sorted(
            item_id
            for item_id, item in b.s.items.items()
            if item.evidence_kind is EvidenceKind.MISSING_EVIDENCE
            and item.provenance.producer_id == requirement.producer_id
        )
        if not notices:
            statements.append(b.missing_requirement(requirement))
        for item_id in notices:
            cited_missing.add(item_id)
            statements.append(
                b.cite(
                    item_id,
                    CitationRole.MISSING_EVIDENCE_NOTICE,
                    f"Required input `{requirement.requirement_id}` is missing "
                    f"({_words(requirement.reason.value)}); the producer recorded a "
                    "missing-evidence notice.",
                )
            )
    if not required_only:
        for item_id, item in sorted(b.s.items.items()):
            if item.evidence_kind is EvidenceKind.MISSING_EVIDENCE and item_id not in cited_missing:
                statements.append(
                    b.cite(
                        item_id,
                        CitationRole.MISSING_EVIDENCE_NOTICE,
                        f"Missing-evidence notice from `{item.provenance.producer_id}`.",
                    )
                )
    for note in card.non_current_entries:
        if required_only and not note.required:
            continue
        item = b.s.items[note.item_id]
        need = "required" if note.required else "cited"
        statements.append(
            b.cite(
                note.item_id,
                CitationRole.STALE_EVIDENCE_NOTICE,
                f"A {need} {_kind(item.evidence_kind).lower()} from "
                f"`{item.provenance.producer_id}` is `{note.state.value}` "
                f"({_words(note.reason.value)}) at the bundle's as-of time.",
                label=StatementLabel.STALE_EVIDENCE,
            )
        )
    for ambiguous in card.ambiguous_requirements:
        for item_id in ambiguous.competing_item_ids:
            statements.append(
                b.cite(
                    item_id,
                    CitationRole.CONTEXT,
                    f"Requirement `{ambiguous.requirement_id}` is ambiguous "
                    f"({_words(ambiguous.reason.value)}); this is one competing item, and no "
                    f"winner is chosen. {b.describe(item_id)}.",
                )
            )
    return statements


def _conflict_sides(b: _Builder, *, critical_only: bool = False) -> list[Statement]:
    statements = []
    for note in b.s.card.conflicts:
        if critical_only and note.severity is not ConflictSeverity.CRITICAL:
            continue
        for item_id in note.involved_item_ids:
            statements.append(
                b.cite(
                    item_id,
                    CitationRole.CONFLICT_NOTICE,
                    f"One side of a `{note.severity.value}` `{_words(note.conflict_type.value)}` "
                    f"conflict (status `{note.status.value}`; no winner is chosen): "
                    f"{b.describe(item_id)}.",
                    conflict_ids=[note.conflict_id],
                )
            )
    return statements


def _inference(b: _Builder) -> tuple[Statement, ...]:
    return tuple(
        b.cite(
            item_id,
            CitationRole.CONTEXT,
            f"Labelled inference, not supporting or contradicting evidence: {b.describe(item_id)}.",
        )
        for item_id, item in sorted(b.s.items.items())
        if item.evidence_kind is EvidenceKind.CURRENT_INFERENCE
    )


def _qualification(b: _Builder) -> tuple[bool, str, list[Statement]]:
    card = b.s.card
    if card.qualification_item_id is None or card.qualification_item_id not in b.s.items:
        return False, NOT_ANSWERABLE + " The card cites no qualification evaluation.", []
    item = b.s.items[card.qualification_item_id]
    lifecycle = f", lifecycle `{card.lifecycle_state.value}`" if card.lifecycle_state else ""
    reason = f", reason `{card.no_setup_reason.value}`" if card.no_setup_reason else ""
    statements = [
        b.cite(
            card.qualification_item_id,
            CitationRole.SUPPORTS_CLAIM,
            f"The synthetic setup evaluation by `{item.provenance.producer_id}` records "
            f"qualification `{card.qualification.value}`{lifecycle}{reason}.",
        )
    ]
    statements += _missing_and_stale(b, required_only=True)
    statements += _conflict_sides(b, critical_only=True)
    blockers = ", ".join(_words(x.value) for x in card.readiness_blockers)
    headline = (
        f"The card records qualification `{card.qualification.value}` and evidence readiness "
        f"`{card.evidence_readiness.value}`"
        + (f", blocked by: {blockers}." if blockers else "; no readiness blocker is recorded.")
    )
    return True, headline, statements


def _cited(
    b: _Builder, citations: Sequence[EvidenceCitation], role: CitationRole, title: str
) -> tuple[bool, str, list[Statement]]:
    ids = list(dict.fromkeys(i for c in citations for i in c.cited_item_ids))
    if any(b.s.items[i].evidence_kind is EvidenceKind.CURRENT_INFERENCE for i in ids):
        raise AssistantRefusal("inference_cited_as_evidence")
    if not ids:
        return False, f"{NOT_ANSWERABLE} The card cites no {title} evidence.", []
    statements = [b.cite(i, role, f"{b.describe(i)}.") for i in ids]
    return True, f"The card cites {len(ids)} {title} item(s).", statements


def _contracts(b: _Builder) -> tuple[bool, str, list[Statement]]:
    card = b.s.card
    section = card.contracts
    if card.selector_availability is not SelectorAvailability.AVAILABLE or section is None:
        state = _words(card.selector_availability.value)
        return (
            False,
            f"{NOT_ANSWERABLE} The Contract Selector result is {state} for this card, so no "
            "contract status can be explained.",
            [],
        )
    item = b.s.items.get(section.selector_result_item_id)
    if item is None or item.payload_schema_id != CONTRACT_SELECTOR_PAYLOAD_ID:
        raise AssistantRefusal("selector_result_not_in_bundle")
    result = ContractSelectorResult.model_validate_json(canonical_json_bytes(item.payload))
    try:
        # The assistant's view must repeat the selector's own sets exactly.
        validate_contract_view(
            result,
            ContractView(
                consumer_id=ConsumerId.ASSISTANT,
                eligible_contracts=list(section.eligible_contracts),
                research_only_contracts=list(section.research_only_contracts),
                rejection_counts=dict(section.rejection_counts),
            ),
        )
    except EvidenceValidationError as error:
        raise AssistantRefusal(error.reason) from None
    if result.status.value != section.selector_outcome.value:
        raise AssistantRefusal("selector_outcome_changed")
    sid = section.selector_result_item_id
    role = CitationRole.CONTEXT
    statements = [
        b.cite(
            sid,
            role,
            f"The deterministic Contract Selector records outcome `{result.status.value}` on "
            f"the `{result.feed.value}` feed (live OPRA: {str(result.feed_is_live_opra).lower()}).",
        )
    ]
    eligible = sorted(c.contract_symbol for c in result.eligible_contracts)
    research = sorted(c.contract_symbol for c in result.research_only_contracts)
    statements.append(
        b.cite(
            sid,
            role,
            "Eligible, as returned by the selector: "
            + (", ".join(f"`{s}`" for s in eligible) if eligible else "none")
            + ".",
        )
    )
    note = (
        " The feed is indicative, so these are research only and never operationally eligible."
        if result.feed.value == "indicative"
        else ""
    )
    statements.append(
        b.cite(
            sid,
            role,
            "Research only, not eligible, as returned by the selector: "
            + (", ".join(f"`{s}`" for s in research) if research else "none")
            + "."
            + note,
        )
    )
    counts = ", ".join(
        f"{_words(reason.value)} {count}"
        for reason, count in sorted(result.rejection_counts.items())
        if count
    )
    statements.append(
        b.cite(
            sid,
            role,
            "Rejected contracts are counted, never listed: " + (counts or "none rejected") + ".",
        )
    )
    headline = (
        "Contract status is the Contract Selector's alone. The assistant repeats it unchanged and "
        "does not rank, reclassify or add contracts."
    )
    return True, headline, statements


def _market_and_research(b: _Builder) -> tuple[bool, str, list[Statement]]:
    card = b.s.card
    statements = [
        b.cite(
            note.item_id,
            CitationRole.CONTEXT,
            f"Historical research, not live state: study `{note.study_id}` records label "
            f"`{note.primary_label}`, research status `{note.research_status.value}`, holdout "
            f"`{note.holdout_state.value}`; caveats: {', '.join(note.fixed_caveats)}.",
        )
        for note in card.research_references
    ]
    excluded = {card.qualification_item_id}
    if card.contracts is not None:
        excluded.add(card.contracts.selector_result_item_id)
    for item_id, item in sorted(b.s.items.items()):
        if item_id in excluded or item.evidence_kind not in (
            EvidenceKind.CONFIRMED_FACT,
            EvidenceKind.DETERMINISTIC_CALCULATION,
        ):
            continue
        statements.append(b.cite(item_id, CitationRole.CONTEXT, f"{b.describe(item_id)}."))
    if not statements:
        return False, f"{NOT_ANSWERABLE} The bundle holds no market or research evidence.", []
    as_of = b.s.bundle.as_of_utc.isoformat().replace("+00:00", "Z")
    headline = (
        f"Recorded state in the selected bundle as of {as_of}. Facts and calculations are "
        "observations and deterministic outputs; research results are historical and are not "
        "live market state. None of this is a forecast, direction or advice."
    )
    return True, headline, statements


def _contradicting(b: _Builder) -> tuple[bool, str, list[Statement]]:
    answerable, headline, statements = _cited(
        b, b.s.card.contradicting_citations, CitationRole.CONTRADICTS_CLAIM, "contradicting"
    )
    conflicts = _conflict_sides(b)
    if conflicts:
        count = len(b.s.card.conflicts)
        lead = headline if answerable else "The card cites no contradicting evidence."
        headline = f"{lead} {count} conflict(s) are shown with both sides."
        return True, headline, statements + conflicts
    return answerable, headline, statements


def _missing_or_stale(b: _Builder) -> tuple[bool, str, list[Statement]]:
    statements = _missing_and_stale(b)
    if not statements:
        return (
            True,
            "The selected bundle records no missing, stale, aging, unknown-freshness or "
            "ambiguous evidence for this card.",
            [],
        )
    headline = f"{len(statements)} missing, stale or ambiguous record(s) bear on this card."
    return True, headline, statements


def _supporting(b: _Builder) -> tuple[bool, str, list[Statement]]:
    return _cited(b, b.s.card.supporting_citations, CitationRole.SUPPORTS_CLAIM, "supporting")


_ANSWERS = {
    QuestionId.QUALIFICATION: _qualification,
    QuestionId.SUPPORTING: _supporting,
    QuestionId.CONTRADICTING: _contradicting,
    QuestionId.MISSING_OR_STALE: _missing_or_stale,
    QuestionId.CONTRACT_STATUS: _contracts,
    QuestionId.MARKET_AND_RESEARCH: _market_and_research,
}


def check_references(bundle: EvidenceBundleManifest, statements: Sequence[Statement]) -> None:
    """Resolve every reference by its own type: evidence-item citations
    inside the bundle, missing-requirement references to the bundle's own
    missing-requirement records. Anything else is refused."""
    records = {m.requirement_id: m for m in bundle.missing_required_producers}
    citations = []
    for statement in statements:
        if statement.reference_type is ReferenceType.EVIDENCE_ITEM:
            citations.append(statement.evidence_citation)
            continue
        reference = statement.missing_requirement
        record = records.get(reference.requirement_id)
        if (
            reference.bundle_id != bundle.bundle_id
            or record is None
            or (record.producer_id, record.reason) != (reference.producer_id, reference.reason)
        ):
            raise AssistantRefusal("missing_requirement_not_in_bundle")
    try:
        validate_citations(bundle, citations)
    except EvidenceValidationError as error:
        raise AssistantRefusal(error.reason) from None


def answer(selection: Selection, question: str, *, card_id: str, bundle_id: str) -> Answer:
    """Answer one bounded question about the selected card. Unsupported
    questions get a fixed not-answerable response; selection problems raise
    ``AssistantRefusal``."""
    check_selection(selection, card_id=card_id, bundle_id=bundle_id)
    try:
        question_id = QuestionId(question)
    except ValueError:
        return Answer(
            question_id=None,
            question=question[:200],
            card_id=card_id,
            bundle_id=bundle_id,
            answerable=False,
            headline=f"{NOT_ANSWERABLE} Only the listed questions are supported.",
            statements=(),
        )
    b = _Builder(selection)
    answerable, headline, statements = _ANSWERS[question_id](b)
    inference = (
        _inference(b)
        if question_id
        in (QuestionId.SUPPORTING, QuestionId.CONTRADICTING, QuestionId.MARKET_AND_RESEARCH)
        else ()
    )
    check_references(selection.bundle, [*statements, *inference])
    return Answer(
        question_id=question_id,
        question=QUESTIONS[question_id],
        card_id=card_id,
        bundle_id=bundle_id,
        answerable=answerable,
        headline=headline,
        statements=tuple(statements),
        inference=inference,
    )


def answer_all(selection: Selection, *, card_id: str, bundle_id: str) -> dict[QuestionId, Answer]:
    """Every supported question, for one selection."""
    return {q: answer(selection, q.value, card_id=card_id, bundle_id=bundle_id) for q in QuestionId}
