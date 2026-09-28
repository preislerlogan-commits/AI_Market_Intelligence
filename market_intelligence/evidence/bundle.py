"""Deterministic, point-in-time bundle construction and readiness (design §N).

``build_bundle`` is a pure function over in-memory records. It opens no
storage: the caller supplies each item and conflict with the time it was
recorded (``recorded_at_utc``), which defines "known at time T" (design
§L.4). Items recorded after ``as_of_utc`` are invisible, so later corrections
never change an earlier bundle.

The builder:

- is the only component that evaluates freshness;
- never includes SPY price evidence inside the holdout window, and reports a
  requirement whose evidence was blocked as ``holdout_restricted``;
- includes the latest revision by default and marks superseded entries;
- satisfies each registered requirement only with an item matching its full
  requirement identity (producer and version, kind, payload schema, primary
  subject and configuration), and runs these checks regardless of query
  filters, so filters can never hide absence;
- chooses a requirement's item only by substantive ordering (effective time,
  observed time, revision number) and revision-chain resolution. Distinct
  items still tied, or branched revisions, are reported as an ambiguous
  requirement with every competing ID (sorted for audit, never used to pick a
  winner), and the bundle is not machine-decision ready. The builder links a
  matching unresolved conflict if one is recorded, but never creates one;
- never includes a non-selector item about an option contract that the
  selector did not return (except for ``audit_export``); rejected contracts
  are never materialized;
- picks no winner among conflicts, and never creates or resolves one.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from pydantic import BaseModel

from market_intelligence.contract_selection.contracts import ContractSelectorResult
from market_intelligence.evidence.canonical import canonical_json_bytes, canonical_sha256
from market_intelligence.evidence.contracts import (
    AmbiguousRequirement,
    EvidenceBundleEntry,
    EvidenceBundleManifest,
    EvidenceBundleManifestContent,
    EvidenceConflict,
    EvidenceConsumerContext,
    EvidenceFreshness,
    EvidenceItem,
    EvidenceQuery,
    MissingProducer,
    seal_bundle,
)
from market_intelligence.evidence.enums import (
    AmbiguityReason,
    BundlePurpose,
    ClockHealthReason,
    ConflictSeverity,
    ConflictStatus,
    ConsumerId,
    ConsumerPermission,
    EvidenceKind,
    FreshnessState,
    ImplementationStatus,
    MissingProducerReason,
    SubjectType,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.freshness import assess_clock, evaluate_freshness
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence.primitives import STRICT_FROZEN, UtcTimestamp
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_SELECTOR_PAYLOAD_ID,
    CONTRACT_SELECTOR_PRODUCER_ID,
    ReturnedContractSets,
)

READY_STATES = frozenset({FreshnessState.CURRENT, FreshnessState.TIMELESS})

_READ_PERMISSION: dict[EvidenceKind, ConsumerPermission | None] = {
    EvidenceKind.CONFIRMED_FACT: ConsumerPermission.READ_FACTS,
    EvidenceKind.DETERMINISTIC_CALCULATION: ConsumerPermission.READ_CALCULATIONS,
    EvidenceKind.HISTORICAL_RESEARCH_RESULT: ConsumerPermission.READ_RESEARCH,
    EvidenceKind.CURRENT_INFERENCE: ConsumerPermission.READ_INFERENCES,
    # Absence and staleness notices are always visible (design §N.2).
    EvidenceKind.MISSING_EVIDENCE: None,
    EvidenceKind.STALE_EVIDENCE: None,
}


class RecordedItem(BaseModel):
    """An item together with the instant it was recorded (storage metadata,
    outside the item's identity)."""

    model_config = STRICT_FROZEN

    item: EvidenceItem
    recorded_at_utc: UtcTimestamp


class RecordedConflict(BaseModel):
    model_config = STRICT_FROZEN

    conflict: EvidenceConflict
    recorded_at_utc: UtcTimestamp


def query_request_sha256(query: EvidenceQuery) -> str:
    return canonical_sha256(query.model_dump(mode="json"))


def _fail(reason: str) -> EvidenceValidationError:
    return EvidenceValidationError(reason)


def _check_consumer(
    registry: EvidenceRegistry,
    purpose: BundlePurpose,
    context: EvidenceConsumerContext,
    query: EvidenceQuery,
) -> None:
    grant = registry.consumer_grant(context.consumer_id)
    if grant is None:
        raise _fail("consumer_not_registered")
    if context.purpose is not purpose or purpose not in grant.purposes:
        raise _fail("purpose_not_granted")
    if not set(context.permissions) <= set(grant.permissions):
        raise _fail("permission_not_granted")
    if context.machine_decision_mode and not grant.machine_decision_mode_allowed:
        raise _fail("machine_decision_mode_not_granted")
    if context.request_sha256 != query_request_sha256(query):
        raise _fail("request_hash_mismatch")
    if query.include_superseded and ConsumerPermission.READ_SUPERSEDED not in context.permissions:
        raise _fail("superseded_read_not_permitted")


def _known_items(records: Iterable[RecordedItem], as_of: datetime) -> dict[str, RecordedItem]:
    known: dict[str, RecordedItem] = {}
    for record in records:
        if record.recorded_at_utc > as_of:
            continue
        existing = known.get(record.item.item_id)
        # A repeated append of the same ID is idempotent: keep the first.
        if existing is None or record.recorded_at_utc < existing.recorded_at_utc:
            known[record.item.item_id] = record
    return known


def _successors(items: Iterable[EvidenceItem]) -> dict[str, list[str]]:
    successors: dict[str, list[str]] = {}
    for item in items:
        predecessor = item.revision.supersedes_item_id
        if predecessor is not None:
            successors.setdefault(predecessor, []).append(item.item_id)
    return successors


def _latest_revision(item_id: str, successors: dict[str, list[str]]) -> str | None:
    """The unique latest revision, or ``None`` for a tip or a branched chain
    (branches are a ``revision_disagreement``; the builder never picks one)."""
    current = item_id
    seen = {current}
    while True:
        nexts = successors.get(current, [])
        if not nexts:
            return None if current == item_id else current
        if len(nexts) > 1:
            return None
        current = nexts[0]
        if current in seen:
            raise _fail("revision_cycle")
        seen.add(current)


def requirement_rank_key(item: EvidenceItem) -> tuple[datetime, datetime, int]:
    """Substantive ordering among items satisfying one requirement: latest
    effective time, then latest source-observed time, then highest revision
    number. The item ID is deliberately absent: it never decides a winner."""
    observed = item.provenance.source_observed_at_utc or item.effective_at_utc
    return (item.effective_at_utc, observed, item.revision.revision_number)


def _revision_root(item: EvidenceItem, by_id: dict[str, EvidenceItem]) -> str:
    """The earliest known ancestor of an item's revision chain."""
    current = item
    seen = {current.item_id}
    while current.revision.supersedes_item_id in by_id:
        current = by_id[current.revision.supersedes_item_id]
        if current.item_id in seen:
            raise _fail("revision_cycle")
        seen.add(current.item_id)
    return current.item_id


def _select_for_requirement(
    candidates: list[EvidenceItem], by_id: dict[str, EvidenceItem]
) -> tuple[EvidenceItem | None, AmbiguityReason | None, list[EvidenceItem]]:
    """Pick the single winner, or report ambiguity with every competitor.

    Candidates are already the latest known revision of their chains (a
    superseded item never qualifies), so a linear revision chain yields its
    unique latest item. After the substantive ordering, the remaining items
    plus any revision-branch siblings form the competing set:

    - one item: it is selected;
    - items sharing a revision root: ``branched_revisions``;
    - otherwise several distinct items: ``competing_observations``.
    """
    top_rank = max(requirement_rank_key(i) for i in candidates)
    top = [i for i in candidates if requirement_rank_key(i) == top_rank]
    roots = {i.item_id: _revision_root(i, by_id) for i in candidates}
    top_roots = {roots[i.item_id] for i in top}
    group = [i for i in candidates if roots[i.item_id] in top_roots]
    if len(group) == 1:
        return group[0], None, []
    group_roots = [roots[i.item_id] for i in group]
    reason = (
        AmbiguityReason.BRANCHED_REVISIONS
        if len(set(group_roots)) < len(group_roots)
        else AmbiguityReason.COMPETING_OBSERVATIONS
    )
    return None, reason, sorted(group, key=lambda i: i.item_id)


def _matches_query(item: EvidenceItem, query: EvidenceQuery) -> bool:
    if query.producer_ids and item.provenance.producer_id not in query.producer_ids:
        return False
    if query.evidence_kinds and item.evidence_kind not in query.evidence_kinds:
        return False
    subject_ids = {s.subject_id for s in item.subjects}
    if query.subject_ids and not subject_ids & set(query.subject_ids):
        return False
    subject_types = {s.subject_type for s in item.subjects}
    if query.subject_types and not subject_types & set(query.subject_types):
        return False
    return query.effective_from_utc <= item.effective_at_utc <= query.effective_to_utc


def _readable(item: EvidenceItem, context: EvidenceConsumerContext) -> bool:
    permission = _READ_PERMISSION[item.evidence_kind]
    return permission is None or permission in context.permissions


def _returned_contracts(items: Iterable[EvidenceItem]) -> frozenset[str]:
    returned: set[str] = set()
    for item in items:
        if (
            item.provenance.producer_id == CONTRACT_SELECTOR_PRODUCER_ID
            and item.payload_schema_id == CONTRACT_SELECTOR_PAYLOAD_ID
        ):
            result = ContractSelectorResult.model_validate_json(canonical_json_bytes(item.payload))
            returned |= ReturnedContractSets.from_result(result).returned
    return frozenset(returned)


def _mentions_unreturned_contract(item: EvidenceItem, returned: frozenset[str]) -> bool:
    if item.provenance.producer_id == CONTRACT_SELECTOR_PRODUCER_ID:
        return False
    for subject in item.subjects:
        if subject.subject_type is SubjectType.OPTION_CONTRACT:
            if subject.subject_id.removeprefix("option:osi:") not in returned:
                return True
    return False


def build_bundle(
    *,
    registry: EvidenceRegistry,
    purpose: BundlePurpose,
    as_of: datetime,
    consumer_context: EvidenceConsumerContext,
    query: EvidenceQuery,
    recorded_items: Sequence[RecordedItem],
    recorded_conflicts: Sequence[RecordedConflict] = (),
    built_at: datetime,
) -> EvidenceBundleManifest:
    _check_consumer(registry, purpose, consumer_context, query)
    rule = registry.selection_rule(purpose)
    if rule is None:
        raise _fail("no_selection_rule")
    window = query.effective_to_utc - query.effective_from_utc
    if window.total_seconds() > rule.max_window_seconds:
        raise _fail("query_window_too_large")

    known = _known_items(recorded_items, as_of)
    all_known = [record.item for record in known.values()]
    restricted = [item for item in all_known if is_holdout_restricted(item, registry)]
    restricted_ids = {item.item_id for item in restricted}
    visible = [item for item in all_known if item.item_id not in restricted_ids]
    successors = _successors(visible)
    visible_ids = {item.item_id for item in visible}
    superseded = {item_id for item_id in successors if item_id in visible_ids}
    machine_mode = consumer_context.machine_decision_mode
    audit = consumer_context.consumer_id is ConsumerId.AUDIT_EXPORT

    def admissible(item: EvidenceItem) -> bool:
        if item.item_id in superseded and not query.include_superseded:
            return False
        if machine_mode and not item.machine_decision_eligible:
            return False
        return _readable(item, consumer_context)

    selected = {i.item_id: i for i in visible if admissible(i) and _matches_query(i, query)}

    # Requirement checks ignore query filters (filters never hide absence).
    # Each requirement is satisfied only by an item matching its full
    # registered identity. Exactly one item may win; if several distinct items
    # remain after the substantive ordering and revision-chain resolution,
    # the requirement is ambiguous and nothing wins.
    by_id = {item.item_id: item for item in visible}
    chosen: dict[str, EvidenceItem] = {}
    competing: dict[str, tuple[AmbiguityReason, list[EvidenceItem]]] = {}
    for requirement in rule.requirements:
        candidates = [
            i
            for i in visible
            if requirement.matches(i)
            and i.item_id not in superseded
            and admissible(i)
            and i.effective_at_utc <= as_of
        ]
        if not candidates:
            continue
        winner, reason, group = _select_for_requirement(candidates, by_id)
        if winner is not None:
            chosen[requirement.requirement_id] = winner
        elif reason is not None:
            competing[requirement.requirement_id] = (reason, group)
    required_items = {item.item_id: item for item in chosen.values()}
    for _, group in competing.values():
        required_items.update({item.item_id: item for item in group})
    selected.update(required_items)

    if not audit:
        returned = _returned_contracts(selected.values())
        selected = {
            item_id: item
            for item_id, item in selected.items()
            if not _mentions_unreturned_contract(item, returned)
        }
        required_items = {k: v for k, v in required_items.items() if k in selected}

    if len(selected) > query.max_entries:
        raise _fail("bundle_exceeds_max_entries")

    clock = assess_clock(visible, registry.clock_policy, as_of)
    freshness: dict[str, EvidenceFreshness] = {}
    for item_id, item in selected.items():
        policy = registry.freshness_policy(item.freshness_policy_id)
        if policy is None:
            raise _fail("unknown_freshness_policy")
        freshness[item_id] = evaluate_freshness(item, policy, as_of=as_of, clock=clock)

    missing: list[MissingProducer] = []
    for requirement in rule.requirements:

        def unmet(reason: MissingProducerReason, requirement=requirement) -> MissingProducer:
            return MissingProducer(
                requirement_id=requirement.requirement_id,
                producer_id=requirement.producer_id,
                reason=reason,
            )

        if requirement.requirement_id in competing:
            continue
        item = chosen.get(requirement.requirement_id)
        if item is not None and item.item_id in selected:
            if freshness[item.item_id].state not in READY_STATES:
                missing.append(unmet(MissingProducerReason.ONLY_STALE_ITEMS))
            continue
        producer = registry.producer(requirement.producer_id)
        status_items = [i for i in visible if requirement.concerns_status_item(i)]
        implemented = (
            producer is not None
            and producer.implementation_status is ImplementationStatus.IMPLEMENTED
        )
        if not implemented:
            reason = MissingProducerReason.PRODUCER_NOT_IMPLEMENTED
        elif any(requirement.matches(i) for i in restricted):
            reason = MissingProducerReason.HOLDOUT_RESTRICTED
        elif any(i.evidence_kind is EvidenceKind.STALE_EVIDENCE for i in status_items):
            reason = MissingProducerReason.ONLY_STALE_ITEMS
        elif any(i.evidence_kind is EvidenceKind.MISSING_EVIDENCE for i in status_items):
            reason = MissingProducerReason.PRODUCER_UNAVAILABLE
        else:
            reason = MissingProducerReason.NO_ITEM_RECORDED
        missing.append(unmet(reason))

    conflicts = [
        record.conflict
        for record in recorded_conflicts
        if record.recorded_at_utc <= as_of
        and set(record.conflict.involved_item_ids) & set(selected)
    ]
    superseded_conflicts = {
        c.resolution.supersedes_conflict_id for c in conflicts if c.resolution is not None
    }
    current_status = [c for c in conflicts if c.conflict_id not in superseded_conflicts]
    unresolved = [c for c in current_status if c.status is ConflictStatus.UNRESOLVED]
    material_count = sum(
        1
        for c in unresolved
        if c.severity in (ConflictSeverity.MATERIAL, ConflictSeverity.CRITICAL)
    )
    critical_open = any(c.severity is ConflictSeverity.CRITICAL for c in unresolved)

    ambiguous = [
        AmbiguousRequirement(
            requirement_id=requirement_id,
            reason=reason,
            competing_item_ids=[item.item_id for item in group],
            matching_unresolved_conflict_ids=sorted(
                c.conflict_id
                for c in unresolved
                if {item.item_id for item in group} <= set(c.involved_item_ids)
            ),
        )
        for requirement_id, (reason, group) in sorted(competing.items())
    ]

    entries = [
        EvidenceBundleEntry(
            item_id=item_id,
            evidence_kind=item.evidence_kind,
            producer_id=item.provenance.producer_id,
            required=item_id in required_items,
            freshness=freshness[item_id],
            availability_state=item.availability.state,
            machine_decision_eligible=item.machine_decision_eligible,
            superseded_as_of=item_id in superseded,
            latest_revision_item_id=_latest_revision(item_id, successors),
        )
        for item_id, item in sorted(selected.items())
    ]
    ready = (
        not missing
        and not ambiguous
        and clock.reason is not ClockHealthReason.AMBIGUOUS_CLOCK_FACTS
        and all(
            e.machine_decision_eligible and e.freshness.state in READY_STATES
            for e in entries
            if e.required
        )
        and not critical_open
    )

    return seal_bundle(
        EvidenceBundleManifestContent(
            purpose=purpose,
            as_of_utc=as_of,
            registry_version_id=compute_registry_version_id(registry),
            selection_rule_id=rule.selection_rule_id,
            query=query,
            consumer_context=consumer_context,
            entries=entries,
            missing_required_producers=sorted(missing, key=lambda m: m.requirement_id),
            ambiguous_requirements=ambiguous,
            conflict_ids=sorted({c.conflict_id for c in conflicts}),
            unresolved_material_conflict_count=material_count,
            machine_decision_ready=ready,
            built_at_utc=built_at,
        )
    )
