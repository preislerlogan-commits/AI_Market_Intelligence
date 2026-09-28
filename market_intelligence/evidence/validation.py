"""Registry-dependent and lineage validation (design §B, §C.2, §C.3, §I, §M, §O).

Contract-intrinsic rules are enforced by the Pydantic models themselves.
The functions here add the rules that need the registry or other records:

- ``validate_item``: producer, version, kind, subject, payload schema,
  eligibility derivation, vocabularies, empty authorization tables, the
  selector boundary, research rules, and the holdout guard;
- ``validate_lineage``: parents, taint (no fact, calculation or research
  result descends from inference), holdout taint, and revisions;
- ``validate_envelope``: the run and every item in it;
- ``validate_conflict``: severity table, timing, and resolution semantics
  (a resolution supersedes only the prior conflict-status record);
- ``validate_citations``: citations resolve only inside their bundle.

Every refusal raises ``EvidenceValidationError`` with a fixed reason token.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ValidationError

from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.contracts import (
    EvidenceBundleManifest,
    EvidenceCitation,
    EvidenceConflict,
    EvidenceEnvelope,
    EvidenceItem,
    EvidenceItemContent,
)
from market_intelligence.evidence.enums import (
    ConflictSeverity,
    ConflictType,
    EvidenceKind,
    ImplementationStatus,
    ProducerType,
    ResolutionKind,
    RevisionReason,
)
from market_intelligence.evidence.errors import EvidenceValidationError, HoldoutRestrictedError
from market_intelligence.evidence.holdout import enforce_holdout_guard, is_holdout_restricted
from market_intelligence.evidence.payloads import (
    CORE_PAYLOAD_MODELS,
    MissingEvidencePayload,
    StaleEvidencePayload,
)
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence.research_rules import validate_research_reference
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_CLASSIFICATION_PAYLOADS,
    CONTRACT_SELECTOR_PRODUCER_ID,
)

PayloadModels = Mapping[str, type[BaseModel]]

_NO_INFERENCE_ANCESTOR = frozenset(
    {
        EvidenceKind.CONFIRMED_FACT,
        EvidenceKind.DETERMINISTIC_CALCULATION,
        EvidenceKind.HISTORICAL_RESEARCH_RESULT,
    }
)


def _fail(reason: str) -> EvidenceValidationError:
    return EvidenceValidationError(reason)


def derive_machine_decision_eligible(
    item: EvidenceItemContent, registry: EvidenceRegistry
) -> bool:
    """§B.3 derivation. ``current_inference`` is default-deny: the registry's
    inference-input authorization table is structurally empty."""
    producer = registry.producer(item.provenance.producer_id)
    if producer is None:
        return False
    rule = producer.rule_for(item.evidence_kind, item.payload_schema_id)
    if rule is None or not rule.machine_decision_eligible:
        return False
    if not item.provenance.code_tree_clean:
        return False
    if item.evidence_kind is EvidenceKind.CURRENT_INFERENCE:
        return bool(registry.inference_input_authorizations)
    return True


def _validate_payload(item: EvidenceItemContent, payload_models: PayloadModels) -> BaseModel:
    model = payload_models.get(item.payload_schema_id)
    if model is None:
        raise _fail("unknown_payload_schema")
    raw = canonical_json_bytes(item.payload)
    try:
        parsed = model.model_validate_json(raw)
    except ValidationError:
        raise _fail("payload_invalid") from None
    if canonical_json_bytes(parsed.model_dump(mode="json")) != raw:
        raise _fail("payload_not_canonical")
    return parsed


def validate_item(
    item: EvidenceItemContent,
    registry: EvidenceRegistry,
    *,
    payload_models: PayloadModels = CORE_PAYLOAD_MODELS,
) -> None:
    prov = item.provenance
    producer = registry.producer(prov.producer_id)
    if producer is None:
        raise _fail("unknown_producer")
    if prov.producer_version not in producer.producer_versions:
        raise _fail("unknown_producer_version")
    if prov.producer_type is not producer.producer_type:
        raise _fail("producer_type_mismatch")
    if producer.implementation_status is not ImplementationStatus.IMPLEMENTED:
        raise _fail("producer_not_implemented")
    if prov.production_path not in producer.production_paths:
        raise _fail("production_path_not_allowed")

    if registry.payload_schema(item.payload_schema_id) is None:
        # Refused before any other payload or holdout evaluation.
        raise _fail("unknown_payload_schema")
    rule = producer.rule_for(item.evidence_kind, item.payload_schema_id)
    if rule is None:
        raise _fail("kind_or_payload_not_allowed")
    if item.freshness_policy_id != rule.freshness_policy_id:
        raise _fail("freshness_policy_mismatch")
    if any(s.subject_type not in producer.allowed_subject_types for s in item.subjects):
        raise _fail("subject_not_allowed")

    if (
        item.payload_schema_id in CONTRACT_CLASSIFICATION_PAYLOADS
        and prov.producer_id != CONTRACT_SELECTOR_PRODUCER_ID
    ):
        raise _fail("contract_classification_forbidden")
    parsed = _validate_payload(item, payload_models)

    if item.machine_decision_eligible != derive_machine_decision_eligible(item, registry):
        raise _fail("eligibility_mismatch")

    availability = item.availability
    reason = availability.reason_code
    if reason is not None and reason not in producer.reason_codes:
        raise _fail("unknown_reason_code")
    if (
        availability.error_category is not None
        and availability.error_category not in producer.error_categories
    ):
        raise _fail("unknown_error_category")
    if not set(availability.missing_components) <= set(registry.component_tokens):
        raise _fail("unknown_component_token")
    if not set(item.quality.uncertainty_codes) <= set(registry.uncertainty_codes):
        raise _fail("unknown_uncertainty_code")
    if item.inference_basis is not None and not set(
        item.inference_basis.limitation_codes
    ) <= set(registry.limitation_codes):
        raise _fail("unknown_limitation_code")

    if item.quality.strength_rubric_id is not None:
        raise _fail("unregistered_rubric")
    if item.quality.calibrated_probability is not None:
        raise _fail("unauthorized_probability")
    if item.scenario_relations:
        raise _fail("scenario_relation_unauthorized")

    if item.research_reference is not None:
        validate_research_reference(item.research_reference)

    if isinstance(parsed, MissingEvidencePayload):
        if parsed.checked_at_utc != item.effective_at_utc or (
            prov.source_observed_at_utc != item.effective_at_utc
        ):
            raise _fail("status_time_mismatch")
        if parsed.reason_code != availability.reason_code:
            raise _fail("status_reason_mismatch")
    if isinstance(parsed, StaleEvidencePayload):
        if parsed.status_as_of_utc != item.effective_at_utc:
            raise _fail("status_time_mismatch")

    enforce_holdout_guard(item, registry)


def _ancestors(
    item: EvidenceItemContent, known: Mapping[str, EvidenceItem]
) -> Iterable[EvidenceItem]:
    seen: set[str] = set()
    stack = list(item.provenance.parent_evidence_ids)
    while stack:
        parent_id = stack.pop()
        if parent_id in seen:
            continue
        seen.add(parent_id)
        parent = known.get(parent_id)
        if parent is None:
            raise _fail("missing_parent")
        yield parent
        stack.extend(parent.provenance.parent_evidence_ids)


def validate_lineage(
    item: EvidenceItemContent,
    known: Mapping[str, EvidenceItem],
    registry: EvidenceRegistry,
) -> None:
    """Parents must already be known; taint and revision rules (§C.3, §M)."""
    for parent_id in item.provenance.parent_evidence_ids:
        parent = known.get(parent_id)
        if parent is None:
            raise _fail("missing_parent")
        if parent.provenance.generated_at_utc > item.provenance.generated_at_utc:
            raise _fail("parent_generated_later")
        if (
            item.evidence_kind is EvidenceKind.CONFIRMED_FACT
            and parent.evidence_kind is not EvidenceKind.CONFIRMED_FACT
        ):
            raise _fail("lineage_violation")
    for ancestor in _ancestors(item, known):
        if (
            item.evidence_kind in _NO_INFERENCE_ANCESTOR
            and ancestor.evidence_kind is EvidenceKind.CURRENT_INFERENCE
        ):
            raise _fail("lineage_violation")
        if is_holdout_restricted(ancestor, registry):
            raise HoldoutRestrictedError()

    revision = item.revision
    if revision.supersedes_item_id is not None:
        predecessor = known.get(revision.supersedes_item_id)
        if predecessor is None:
            raise _fail("missing_predecessor")
        if (
            predecessor.provenance.producer_id != item.provenance.producer_id
            or predecessor.evidence_kind is not item.evidence_kind
            or predecessor.subjects[0] != item.subjects[0]
        ):
            raise _fail("revision_mismatch")
        if revision.revision_number != predecessor.revision.revision_number + 1:
            raise _fail("revision_number_mismatch")
        if (
            revision.revision_reason is RevisionReason.ANALYTICAL_REINTERPRETATION
            and item.evidence_kind is not EvidenceKind.CURRENT_INFERENCE
        ):
            raise _fail("reinterpretation_only_for_inference")
        if revision.revision_reason is RevisionReason.PRODUCER_CORRECTION and (
            predecessor.provenance.producer_version == item.provenance.producer_version
            and predecessor.provenance.code_commit_sha == item.provenance.code_commit_sha
        ):
            raise _fail("correction_requires_new_producer_build")


def validate_envelope(
    envelope: EvidenceEnvelope,
    registry: EvidenceRegistry,
    *,
    known_items: Mapping[str, EvidenceItem],
    payload_models: PayloadModels = CORE_PAYLOAD_MODELS,
) -> None:
    if envelope.registry_version_id != compute_registry_version_id(registry):
        raise _fail("registry_version_mismatch")
    producer = registry.producer(envelope.producer_id)
    if producer is None:
        raise _fail("unknown_producer")
    if producer.implementation_status is not ImplementationStatus.IMPLEMENTED:
        raise _fail("producer_not_implemented")
    if envelope.producer_version not in producer.producer_versions:
        raise _fail("unknown_producer_version")
    reason = envelope.availability.reason_code
    if reason is not None and reason not in producer.reason_codes:
        raise _fail("unknown_reason_code")
    known = dict(known_items)
    for item in envelope.items:
        validate_item(item, registry, payload_models=payload_models)
        known[item.item_id] = item
    for item in envelope.items:
        validate_lineage(item, known, registry)


def expected_severity(
    conflict_type: ConflictType, involves_required_item: bool
) -> ConflictSeverity:
    """The fixed initial severity table (design §I.2); never set by a model."""
    if involves_required_item and conflict_type in (
        ConflictType.PROVIDER_DISAGREEMENT,
        ConflictType.FRESHNESS_MISMATCH,
    ):
        return ConflictSeverity.CRITICAL
    if involves_required_item or conflict_type in (
        ConflictType.INFERENCE_VS_OBSERVATION,
        ConflictType.CURRENT_VS_RESEARCH_CONTEXT,
    ):
        return ConflictSeverity.MATERIAL
    return ConflictSeverity.INFORMATIONAL


def validate_conflict(
    conflict: EvidenceConflict,
    items: Mapping[str, EvidenceItem],
    registry: EvidenceRegistry,
    *,
    required_item_ids: frozenset[str] = frozenset(),
    prior: EvidenceConflict | None = None,
) -> None:
    """Conflicts are winnerless; a resolution supersedes only the prior
    conflict-status record and never touches the evidence (design §I.3)."""
    detector = registry.producer(conflict.detector_producer_id)
    if detector is None or conflict.detector_version not in detector.producer_versions:
        raise _fail("unknown_conflict_detector")
    if detector.producer_type is not ProducerType.CONFLICT_DETECTOR:
        raise _fail("not_a_conflict_detector")
    involved = []
    for item_id in conflict.involved_item_ids:
        item = items.get(item_id)
        if item is None:
            raise _fail("unknown_involved_item")
        involved.append(item)
    if conflict.evaluated_as_of_utc < max(i.effective_at_utc for i in involved):
        raise _fail("conflict_before_evidence")
    involves_required = bool(set(conflict.involved_item_ids) & required_item_ids)
    if conflict.severity is not expected_severity(conflict.conflict_type, involves_required):
        raise _fail("severity_mismatch")

    resolution = conflict.resolution
    if (resolution is None) != (prior is None):
        raise _fail("resolution_prior_mismatch")
    if resolution is None or prior is None:
        return
    if resolution.supersedes_conflict_id != prior.conflict_id:
        raise _fail("resolution_must_supersede_prior_status")
    if (
        prior.conflict_type is not conflict.conflict_type
        or prior.involved_item_ids != conflict.involved_item_ids
    ):
        raise _fail("resolution_changes_conflict")
    if conflict.evaluated_as_of_utc < prior.evaluated_as_of_utc:
        raise _fail("resolution_before_prior_status")
    for evidence_id in resolution.resolution_evidence_ids:
        cited = items.get(evidence_id)
        if cited is None:
            raise _fail("unknown_resolution_evidence")
        if resolution.resolution_kind is ResolutionKind.UNDERLYING_ITEM_REVISED and (
            cited.revision.supersedes_item_id not in conflict.involved_item_ids
        ):
            raise _fail("cited_item_is_not_a_revision_of_involved_evidence")


def validate_citations(
    bundle: EvidenceBundleManifest, citations: Iterable[EvidenceCitation]
) -> None:
    entries = {entry.item_id: entry for entry in bundle.entries}
    for citation in citations:
        if citation.bundle_id != bundle.bundle_id:
            raise _fail("citation_outside_bundle")
        if any(item_id not in entries for item_id in citation.cited_item_ids):
            raise _fail("citation_outside_bundle")
        kinds = sorted({entries[i].evidence_kind for i in citation.cited_item_ids})
        if list(citation.cited_kinds) != kinds:
            raise _fail("citation_kind_mismatch")
        if not set(citation.cited_conflict_ids) <= set(bundle.conflict_ids):
            raise _fail("citation_outside_bundle")
