"""Mapping between sealed records and stored rows.

Each ``*_columns`` function returns the authoritative columns of one record
**except** ``commit_seq``, ``recorded_at_utc`` and ``row_sha256``, which the
commit machinery adds. Copied key columns are always derived from the record
itself; ``key_columns_agree`` re-derives them for verification. Each
``parse_*`` function re-parses ``record_json`` strictly, which re-checks the
record's own identity (the sealed models refuse an ID that does not match).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from market_intelligence.evidence.canonical import ENVELOPE_PREFIX, canonical_sha256, prefixed_id
from market_intelligence.evidence.contracts import (
    EvidenceBundleManifest,
    EvidenceConflict,
    EvidenceEnvelope,
    EvidenceItem,
    envelope_identity_payload,
)
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence_store.contracts import (
    BundleBuilderIdentity,
    CheckpointKeyRotation,
    ConflictSeverityDerivation,
    RecoveryRecord,
    RegistryActivation,
    RegistryVersionRecord,
    StoreInstance,
)
from market_intelligence.evidence_store.enums import IntegrityFinding
from market_intelligence.evidence_store.errors import IntegrityStop
from market_intelligence.evidence_store.store_io import canonical_text, sha256_text
from market_intelligence.setup_cards.contracts import SetupCard
from market_intelligence.setup_cards.supersession import card_identity_key


def _record(model_dump: dict[str, Any]) -> dict[str, Any]:
    text = canonical_text(model_dump)
    return {"record_json": text, "record_sha256": sha256_text(text)}


def _parse(model: type[BaseModel], text: str) -> Any:
    try:
        return model.model_validate_json(text)
    except ValidationError:
        raise IntegrityStop(IntegrityFinding.IDENTITY_MISMATCH) from None


# --- Store instance ------------------------------------------------------------------------------


def instance_columns(instance: StoreInstance) -> dict[str, Any]:
    return {
        "store_instance_id": instance.store_instance_id,
        "initialized_at_utc": instance.initialized_at_utc,
        **_record(instance.model_dump(mode="json")),
    }


# --- Evidence items ------------------------------------------------------------------------------


def item_columns(item: EvidenceItem, validated_registry_version_id: str) -> dict[str, Any]:
    prov = item.provenance
    return {
        "item_id": item.item_id,
        "schema_version": item.schema_version,
        "evidence_kind": item.evidence_kind.value,
        "producer_id": prov.producer_id,
        "producer_version": prov.producer_version,
        "payload_schema_id": item.payload_schema_id,
        "primary_subject_id": item.subjects[0].subject_id,
        "configuration_identity": prov.configuration_identity,
        "effective_at_utc": item.effective_at_utc,
        "source_observed_at_utc": prov.source_observed_at_utc,
        "revision_number": item.revision.revision_number,
        "supersedes_item_id": item.revision.supersedes_item_id,
        "machine_decision_eligible": item.machine_decision_eligible,
        "validated_registry_version_id": validated_registry_version_id,
        **_record(item.model_dump(mode="json")),
    }


def parse_item(text: str) -> EvidenceItem:
    return _parse(EvidenceItem, text)


def item_projection_rows(item: EvidenceItem, commit_seq: int) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = [
        (
            "evidence_item_subjects",
            {
                "item_id": item.item_id,
                "position": position,
                "subject_type": subject.subject_type.value,
                "subject_id": subject.subject_id,
            },
        )
        for position, subject in enumerate(item.subjects)
    ]
    rows += [
        ("evidence_item_parents", {"item_id": item.item_id, "parent_item_id": parent})
        for parent in item.provenance.parent_evidence_ids
    ]
    if item.revision.supersedes_item_id is not None:
        rows.append(
            (
                "evidence_item_revisions",
                {
                    "predecessor_item_id": item.revision.supersedes_item_id,
                    "successor_item_id": item.item_id,
                    "successor_commit_seq": commit_seq,
                },
            )
        )
    return rows


# --- Envelopes -----------------------------------------------------------------------------------


def envelope_header(envelope: EvidenceEnvelope) -> dict[str, Any]:
    header = envelope_identity_payload(envelope)
    header["envelope_id"] = envelope.envelope_id
    header["emitted_at_utc"] = envelope.model_dump(mode="json")["emitted_at_utc"]
    return header


def envelope_columns(envelope: EvidenceEnvelope) -> dict[str, Any]:
    return {
        "envelope_id": envelope.envelope_id,
        "producer_id": envelope.producer_id,
        "producer_version": envelope.producer_version,
        "producer_run_key": envelope.producer_run_key,
        "run_as_of_utc": envelope.run_as_of_utc,
        "registry_version_id": envelope.registry_version_id,
        "availability_state": envelope.availability.state.value,
        "item_count": len(envelope.items),
        **_record(envelope_header(envelope)),
    }


def verify_envelope_header(text: str) -> dict[str, Any]:
    import json

    header = json.loads(text)
    identity = {k: v for k, v in header.items() if k not in ("envelope_id", "emitted_at_utc")}
    if prefixed_id(ENVELOPE_PREFIX, identity) != header.get("envelope_id"):
        raise IntegrityStop(IntegrityFinding.IDENTITY_MISMATCH)
    return header


# --- Conflicts -----------------------------------------------------------------------------------


def conflict_chain_key(conflict: EvidenceConflict) -> str:
    return canonical_sha256(
        {
            "conflict_type": conflict.conflict_type.value,
            "involved_item_ids": list(conflict.involved_item_ids),
        }
    )


def conflict_columns(
    conflict: EvidenceConflict,
    derivation: ConflictSeverityDerivation,
    validated_registry_version_id: str,
) -> dict[str, Any]:
    supersedes = conflict.resolution.supersedes_conflict_id if conflict.resolution else None
    return {
        "conflict_id": conflict.conflict_id,
        "conflict_chain_key": conflict_chain_key(conflict),
        "conflict_type": conflict.conflict_type.value,
        "severity": conflict.severity.value,
        "status": conflict.status.value,
        "detection_method": conflict.detection_method.value,
        "detector_producer_id": conflict.detector_producer_id,
        "detector_version": conflict.detector_version,
        "evaluated_as_of_utc": conflict.evaluated_as_of_utc,
        "supersedes_conflict_id": supersedes,
        "severity_registry_version_id": derivation.severity_registry_version_id,
        "matched_requirement_ids": canonical_text(derivation.matched_requirement_ids),
        "involves_required_item": derivation.involves_required_item,
        "severity_rule_version": derivation.severity_rule_version,
        "validated_registry_version_id": validated_registry_version_id,
        **_record(conflict.model_dump(mode="json")),
    }


def parse_conflict(text: str) -> EvidenceConflict:
    return _parse(EvidenceConflict, text)


def stored_derivation(row: dict[str, Any]) -> ConflictSeverityDerivation:
    import json

    return ConflictSeverityDerivation(
        evaluated_as_of_utc=row["evaluated_as_of_utc"],
        severity_registry_version_id=row["severity_registry_version_id"],
        matched_requirement_ids=json.loads(row["matched_requirement_ids"]),
        involves_required_item=row["involves_required_item"],
        severity=row["severity"],
        severity_rule_version=row["severity_rule_version"],
    )


# --- Bundles -------------------------------------------------------------------------------------


def bundle_columns(
    bundle: EvidenceBundleManifest, builder: BundleBuilderIdentity, visible_through: int
) -> dict[str, Any]:
    return {
        "bundle_id": bundle.bundle_id,
        "purpose": bundle.purpose.value,
        "as_of_utc": bundle.as_of_utc,
        "registry_version_id": bundle.registry_version_id,
        "selection_rule_id": bundle.selection_rule_id,
        "consumer_id": bundle.consumer_context.consumer_id.value,
        "machine_decision_ready": bundle.machine_decision_ready,
        "entry_count": len(bundle.entries),
        "builder_id": builder.builder_id,
        "builder_version": builder.builder_version,
        "builder_code_commit_sha": builder.builder_code_commit_sha,
        "builder_configuration_identity": builder.builder_configuration_identity,
        "visible_through_commit_seq": visible_through,
        **_record(bundle.model_dump(mode="json")),
    }


def parse_bundle(text: str) -> EvidenceBundleManifest:
    return _parse(EvidenceBundleManifest, text)


def bundle_projection_rows(
    bundle: EvidenceBundleManifest, registry: EvidenceRegistry, items: dict[str, EvidenceItem]
) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []
    for entry in bundle.entries:
        rows.append(
            (
                "evidence_bundle_entries",
                {
                    "bundle_id": bundle.bundle_id,
                    "item_id": entry.item_id,
                    "evidence_kind": entry.evidence_kind.value,
                    "producer_id": entry.producer_id,
                    "required": entry.required,
                    "freshness_state": entry.freshness.state.value,
                    "freshness_reason": entry.freshness.reason.value,
                    "age_seconds": entry.freshness.age_seconds,
                    "clock_health_reason": entry.freshness.clock_health_reason.value,
                    "availability_state": entry.availability_state.value,
                    "machine_decision_eligible": entry.machine_decision_eligible,
                    "superseded_as_of": entry.superseded_as_of,
                    "latest_revision_item_id": entry.latest_revision_item_id,
                },
            )
        )
    rule = registry.selection_rule(bundle.purpose)
    missing = {m.requirement_id: m for m in bundle.missing_required_producers}
    ambiguous = {a.requirement_id: a for a in bundle.ambiguous_requirements}
    required = [items[e.item_id] for e in bundle.entries if e.required and e.item_id in items]
    for requirement in rule.requirements if rule else []:
        rid = requirement.requirement_id
        if rid in missing:
            outcome = ("missing", missing[rid].reason.value, None, None, 0)
        elif rid in ambiguous:
            group = ambiguous[rid]
            outcome = ("ambiguous", None, group.reason.value, None, len(group.competing_item_ids))
        else:
            matches = [i.item_id for i in required if requirement.matches(i)]
            satisfying = matches[0] if len(matches) == 1 else None
            outcome = ("satisfied", None, None, satisfying, len(matches))
        rows.append(
            (
                "evidence_bundle_requirement_outcomes",
                {
                    "bundle_id": bundle.bundle_id,
                    "requirement_id": rid,
                    "outcome": outcome[0],
                    "missing_reason": outcome[1],
                    "ambiguity_reason": outcome[2],
                    "satisfying_item_id": outcome[3],
                    "competing_item_count": outcome[4],
                },
            )
        )
    return rows


# --- Cards ---------------------------------------------------------------------------------------


def card_chain_key(card: SetupCard) -> str:
    return canonical_sha256(list(card_identity_key(card)))


def card_columns(card: SetupCard, setup_definition_registry_version_id: str) -> dict[str, Any]:
    return {
        "card_id": card.card_id,
        "schema_version": card.schema_version,
        "card_kind": card.card_kind.value,
        "lane": card.lane.value,
        "chain_key_sha256": card_chain_key(card),
        "setup_subject_id": card.setup_subject_id,
        "session_date": card.session_date,
        "revision_number": card.card_revision.revision_number,
        "supersedes_card_id": card.card_revision.supersedes_card_id,
        "revision_reason": card.card_revision.revision_reason.value,
        "decision_context_bundle_id": card.decision_context_bundle_id,
        "registry_version_id": card.registry_version_id,
        "setup_definition_registry_version_id": setup_definition_registry_version_id,
        "effective_at_utc": card.effective_at_utc,
        **_record(card.model_dump(mode="json")),
    }


def chain_columns(card: SetupCard) -> dict[str, Any]:
    key = list(card_identity_key(card))
    return {
        "chain_key_sha256": card_chain_key(card),
        "card_kind": card.card_kind.value,
        "lane": card.lane.value,
        "setup_subject_id": card.setup_subject_id,
        "session_date": card.session_date if card.setup_subject_id is None else None,
        "root_card_id": card.card_id,
        **_record({"chain_key": key, "root_card_id": card.card_id}),
    }


def parse_card(text: str) -> SetupCard:
    return _parse(SetupCard, text)


def parse_chain(text: str) -> dict[str, Any]:
    import json

    chain = json.loads(text)
    if set(chain) != {"chain_key", "root_card_id"} or not isinstance(chain["chain_key"], list):
        raise IntegrityStop(IntegrityFinding.IDENTITY_MISMATCH)
    return chain


# --- Registry, recovery, rotation ----------------------------------------------------------------


def registry_version_columns(record: RegistryVersionRecord) -> dict[str, Any]:
    return {
        "registry_version_id": record.registry_version_id,
        "registry_kind": record.registry_kind.value,
        "registry_label": record.registry_label,
        "file_format": record.file_format,
        "content_sha256": record.content_sha256,
        "source_path": record.source_path,
        "source_file_sha256": record.source_file_sha256,
        "source_commit_sha": record.source_commit_sha,
        **_record(record.model_dump(mode="json")),
    }


def parse_registry_version(text: str) -> RegistryVersionRecord:
    return _parse(RegistryVersionRecord, text)


def activation_columns(activation: RegistryActivation) -> dict[str, Any]:
    return {
        "activation_id": activation.activation_id,
        "registry_kind": activation.registry_kind.value,
        "registry_version_id": activation.registry_version_id,
        "effective_from_utc": activation.effective_from_utc,
        "activation_reason": activation.activation_reason.value,
        "supersedes_activation_id": activation.supersedes_activation_id,
        "authorization_ref": activation.authorization_ref,
        "activated_at_utc": activation.activated_at_utc,
        **_record(activation.model_dump(mode="json")),
    }


def parse_activation(text: str) -> RegistryActivation:
    return _parse(RegistryActivation, text)


def recovery_columns(seq: int, record: RecoveryRecord) -> dict[str, Any]:
    return {
        "recovery_seq": seq,
        "recovery_kind": record.recovery_kind.value,
        "restored_high_water_commit_seq": record.restored_high_water_commit_seq,
        "restored_high_water_commit_digest": record.restored_high_water_commit_digest,
        "backup_file_sha256": record.backup_file_sha256,
        "superseded_checkpoint_commit_seq": record.superseded_checkpoint_commit_seq,
        "superseded_checkpoint_commit_digest": record.superseded_checkpoint_commit_digest,
        "superseded_checkpoint_sha256": record.superseded_checkpoint_sha256,
        "recovery_authorization_ref": record.recovery_authorization_ref,
        **_record(record.model_dump(mode="json")),
    }


def parse_recovery(text: str) -> RecoveryRecord:
    return _parse(RecoveryRecord, text)


def rotation_columns(seq: int, rotation: CheckpointKeyRotation) -> dict[str, Any]:
    return {
        "rotation_seq": seq,
        "previous_key_id": rotation.previous_key_id,
        "new_key_id": rotation.new_key_id,
        "algorithm": rotation.algorithm.value,
        "authorization_ref": rotation.authorization_ref,
        **_record(rotation.model_dump(mode="json")),
    }


def parse_rotation(text: str) -> CheckpointKeyRotation:
    return _parse(CheckpointKeyRotation, text)


# Which parse function and identity column belong to each record table.
RECORD_TABLES: dict[str, tuple[str, Any]] = {
    "store_instance": ("store_instance_id", lambda t: _parse(StoreInstance, t)),
    "evidence_items": ("item_id", parse_item),
    "evidence_envelopes": ("envelope_id", verify_envelope_header),
    "evidence_conflicts": ("conflict_id", parse_conflict),
    "evidence_bundles": ("bundle_id", parse_bundle),
    "setup_cards": ("card_id", parse_card),
    "setup_card_chains": ("chain_key_sha256", parse_chain),
    "registry_versions": ("registry_version_id", parse_registry_version),
    "registry_activations": ("activation_id", parse_activation),
    "store_recovery_events": ("recovery_seq", parse_recovery),
    "store_checkpoint_key_rotations": ("rotation_seq", parse_rotation),
}
