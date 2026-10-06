"""Strict, frozen storage-layer contracts
(docs/EVIDENCE_CARD_STORAGE_DESIGN.md §4.1, §5; docs/REGISTRY_LOADING_DESIGN.md §6, §13).

These wrap, and never change, the ``evidence-envelope-1`` and
``setup-card-1`` records. Every wrapper field lives outside every record
identity. Identities defined here:

- ``rga1_`` registry activations (SHA-256 of the activation content without
  its own ID and the operational ``activated_at_utc``);
- commit digests (SHA-256 of the canonical commit content, chained);
- row hashes (SHA-256 of the canonical JSON of every other column of a row).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

from market_intelligence.evidence.canonical import canonical_sha256, prefixed_id
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    CommitSha,
    ConfigIdentity,
    Sha256Hex,
    Token,
    UtcTimestamp,
    VersionLabel,
    format_utc,
)
from market_intelligence.evidence_store.enums import (
    ActivationReason,
    CheckpointAuthAlgorithm,
    RecoveryKind,
    RegistryKind,
    StoreAuditEventType,
    StoreOperation,
    StoreWriterId,
)

ACTIVATION_PREFIX = "rga1_"
SETUP_REGISTRY_PREFIX = "sdr1_"
CHECKPOINT_SCHEMA_VERSION = "store-checkpoint-1"

# Syntax only (no real authorization record is created by this code):
# ``project-state:<item-number>@<40-character-commit-sha>``.
AuthorizationRef = Annotated[
    str, StringConstraints(pattern=r"^project-state:[1-9][0-9]{0,4}@[0-9a-f]{40}$")
]
StoreInstanceId = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
KeyId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
ActivationId = Annotated[str, StringConstraints(pattern=r"^rga1_[0-9a-f]{64}$")]
AnyRegistryVersionId = Annotated[str, StringConstraints(pattern=r"^(evr1|sdr1)_[0-9a-f]{64}$")]
SetupRegistryVersionId = Annotated[str, StringConstraints(pattern=r"^sdr1_[0-9a-f]{64}$")]
RegistrySourcePath = Annotated[
    str,
    StringConstraints(
        pattern=r"^registries/(evidence|setup_definitions)/[a-z0-9][a-z0-9._-]{0,63}\.json$"
    ),
]


def canonical_column_value(value: Any) -> Any:
    """The single encoding used to hash stored column values."""
    if isinstance(value, datetime):
        return format_utc(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def compute_row_sha256(columns: dict[str, Any]) -> str:
    """Row hash over every column except ``row_sha256`` itself."""
    payload = {
        name: canonical_column_value(value)
        for name, value in columns.items()
        if name != "row_sha256"
    }
    return canonical_sha256(payload)


def compute_rows_sha256(row_hashes: list[str]) -> str:
    return canonical_sha256("\n".join(sorted(row_hashes)))


# --- Store identity and commits ------------------------------------------------------------------


class StoreInstance(BaseModel):
    """§5.11: the database identity, written once in commit 1."""

    model_config = STRICT_FROZEN

    store_instance_id: StoreInstanceId
    initialized_at_utc: UtcTimestamp


class StoreCommitContent(BaseModel):
    model_config = STRICT_FROZEN

    commit_seq: Annotated[int, Field(ge=1)]
    committed_at_utc: UtcTimestamp
    writer_id: StoreWriterId
    operation: StoreOperation
    row_count: Annotated[int, Field(ge=1)]
    rows_sha256: Sha256Hex
    prev_commit_digest: Sha256Hex | None

    @model_validator(mode="after")
    def _check_commit(self) -> StoreCommitContent:
        if (self.commit_seq == 1) != (self.prev_commit_digest is None):
            raise ValueError("prev_commit_digest is null iff commit_seq is 1")
        return self


def compute_commit_digest(content: StoreCommitContent) -> str:
    dump = content.model_dump(mode="json")
    return canonical_sha256({name: dump[name] for name in StoreCommitContent.model_fields})


class StoreCommit(StoreCommitContent):
    commit_digest: Sha256Hex

    @model_validator(mode="after")
    def _check_digest(self) -> StoreCommit:
        if self.commit_digest != compute_commit_digest(self):
            raise ValueError("commit_digest does not match the recomputed digest")
        return self


def seal_commit(content: StoreCommitContent) -> StoreCommit:
    fields = {name: getattr(content, name) for name in StoreCommitContent.model_fields}
    return StoreCommit(**fields, commit_digest=compute_commit_digest(content))


# --- Builder identity (bundle wrapper) -----------------------------------------------------------


class BundleBuilderIdentity(BaseModel):
    """Authoritative wrapper facts that support reproduction; outside ``evb1_``."""

    model_config = STRICT_FROZEN

    builder_id: Literal["evidence_bundle_builder"] = "evidence_bundle_builder"
    builder_version: VersionLabel
    builder_code_commit_sha: CommitSha
    builder_configuration_identity: ConfigIdentity


# --- Conflict severity wrapper -------------------------------------------------------------------


class ConflictSeverityDerivation(BaseModel):
    """§6.3.1: every input and output of the frozen severity derivation."""

    model_config = STRICT_FROZEN

    evaluated_as_of_utc: UtcTimestamp
    severity_registry_version_id: Annotated[str, StringConstraints(pattern=r"^evr1_[0-9a-f]{64}$")]
    matched_requirement_ids: Annotated[list[str], Field(max_length=512)]
    involves_required_item: bool
    severity: str
    severity_rule_version: Literal["conflict-severity-rules-1"]

    @model_validator(mode="after")
    def _check_derivation(self) -> ConflictSeverityDerivation:
        if self.matched_requirement_ids != sorted(set(self.matched_requirement_ids)):
            raise ValueError("matched requirement IDs must be sorted and unique")
        if self.involves_required_item != bool(self.matched_requirement_ids):
            raise ValueError("involves_required_item equals a non-empty match set")
        return self


# --- Registry records ----------------------------------------------------------------------------


class RegistryVersionRecord(BaseModel):
    """Registry design §6.1: registered, reviewed content."""

    model_config = STRICT_FROZEN

    registry_version_id: AnyRegistryVersionId
    registry_kind: RegistryKind
    registry_label: VersionLabel
    file_format: Literal["evidence-registry-file-1", "setup-definition-registry-file-1"]
    content_json: str
    content_sha256: Sha256Hex
    source_path: RegistrySourcePath
    source_file_sha256: Sha256Hex
    source_commit_sha: CommitSha

    @model_validator(mode="after")
    def _check_record(self) -> RegistryVersionRecord:
        evidence = self.registry_kind is RegistryKind.EVIDENCE_REGISTRY
        prefix = "evr1_" if evidence else SETUP_REGISTRY_PREFIX
        if not self.registry_version_id.startswith(prefix):
            raise ValueError("registry version prefix must match its kind")
        if self.registry_version_id[5:] != self.content_sha256:
            raise ValueError("content hash must equal the identity")
        expected_format = (
            "evidence-registry-file-1" if evidence else "setup-definition-registry-file-1"
        )
        if self.file_format != expected_format:
            raise ValueError("file format must match the registry kind")
        folder = "evidence" if evidence else "setup_definitions"
        if self.source_path != f"registries/{folder}/{self.registry_label}.json":
            raise ValueError("source path must be registries/<kind>/<registry_label>.json")
        return self


class RegistryActivationContent(BaseModel):
    """Registry design §6.2. ``activated_at_utc`` is operational (the commit
    time) and excluded from the ``rga1_`` identity."""

    model_config = STRICT_FROZEN

    registry_kind: RegistryKind
    registry_version_id: AnyRegistryVersionId
    effective_from_utc: UtcTimestamp
    activation_reason: ActivationReason
    supersedes_activation_id: ActivationId | None
    authorization_ref: AuthorizationRef

    @model_validator(mode="after")
    def _check_activation(self) -> RegistryActivationContent:
        initial = self.activation_reason is ActivationReason.INITIAL_ACTIVATION
        if initial != (self.supersedes_activation_id is None):
            raise ValueError("only an initial activation has no predecessor")
        prefix = "evr1_" if self.registry_kind is RegistryKind.EVIDENCE_REGISTRY else "sdr1_"
        if not self.registry_version_id.startswith(prefix):
            raise ValueError("activated version prefix must match its kind")
        return self


def compute_activation_id(content: RegistryActivationContent) -> str:
    dump = content.model_dump(mode="json")
    return prefixed_id(
        ACTIVATION_PREFIX, {name: dump[name] for name in RegistryActivationContent.model_fields}
    )


class RegistryActivation(RegistryActivationContent):
    activation_id: ActivationId
    activated_at_utc: UtcTimestamp

    @model_validator(mode="after")
    def _check_identity(self) -> RegistryActivation:
        if self.activation_id != compute_activation_id(self):
            raise ValueError("activation_id does not match the recomputed identity")
        if self.effective_from_utc < self.activated_at_utc:
            raise ValueError("an activation can never be backdated")
        return self


def seal_activation(
    content: RegistryActivationContent, activated_at_utc: datetime
) -> RegistryActivation:
    fields = {name: getattr(content, name) for name in RegistryActivationContent.model_fields}
    return RegistryActivation(
        **fields,
        activation_id=compute_activation_id(content),
        activated_at_utc=activated_at_utc,
    )


# --- Recovery, key rotation and audit ------------------------------------------------------------


class RecoveryRecord(BaseModel):
    """§5.10. Preserves the superseded checkpoint's bounded metadata."""

    model_config = STRICT_FROZEN

    recovery_kind: RecoveryKind
    restored_high_water_commit_seq: Annotated[int, Field(ge=1)]
    restored_high_water_commit_digest: Sha256Hex
    backup_file_sha256: Sha256Hex
    backup_verification_sha256: Sha256Hex
    replaced_high_water_commit_seq: Annotated[int, Field(ge=1)] | None
    replaced_high_water_commit_digest: Sha256Hex | None
    superseded_checkpoint_commit_seq: Annotated[int, Field(ge=1)]
    superseded_checkpoint_commit_digest: Sha256Hex
    superseded_checkpoint_created_at_utc: UtcTimestamp
    superseded_checkpoint_sha256: Sha256Hex
    recovery_authorization_ref: AuthorizationRef

    @model_validator(mode="after")
    def _check_recovery(self) -> RecoveryRecord:
        if (self.replaced_high_water_commit_seq is None) != (
            self.replaced_high_water_commit_digest is None
        ):
            raise ValueError("replaced high-water sequence and digest go together")
        return self


class CheckpointKeyRotation(BaseModel):
    """§5.12: key IDs only, never key material."""

    model_config = STRICT_FROZEN

    previous_key_id: KeyId
    new_key_id: KeyId
    algorithm: CheckpointAuthAlgorithm
    authorization_ref: AuthorizationRef

    @model_validator(mode="after")
    def _check_rotation(self) -> CheckpointKeyRotation:
        if self.new_key_id == self.previous_key_id:
            raise ValueError("a rotation must change the key ID")
        return self


class AuditEvent(BaseModel):
    """§5.8: bounded; no free text, payload, SQL, path or exception text."""

    model_config = STRICT_FROZEN

    event_type: StoreAuditEventType
    actor: Token
    subject_record_id: Annotated[str, StringConstraints(max_length=80)] | None = None
    reason_code: Token
    reference_commit_seq: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def _check_event(self) -> AuditEvent:
        holdout = self.event_type in (
            StoreAuditEventType.HOLDOUT_WRITE_REFUSED,
            StoreAuditEventType.HOLDOUT_READ_REFUSED,
        )
        if holdout and self.subject_record_id is not None:
            raise ValueError("holdout events never carry a record ID")
        return self


# --- Anti-rollback checkpoint --------------------------------------------------------------------


class CheckpointAuthentication(BaseModel):
    model_config = STRICT_FROZEN

    algorithm: str
    key_id: KeyId
    mac: Sha256Hex


class StoreCheckpointContent(BaseModel):
    """§10.6. No evidence, record ID, path or key material."""

    model_config = STRICT_FROZEN

    checkpoint_schema_version: Literal["store-checkpoint-1"] = CHECKPOINT_SCHEMA_VERSION
    store_instance_id: StoreInstanceId
    commit_seq: Annotated[int, Field(ge=1)]
    commit_digest: Sha256Hex
    created_at_utc: UtcTimestamp


class StoreCheckpoint(StoreCheckpointContent):
    authentication: CheckpointAuthentication | None


def checkpoint_mac_payload(content: StoreCheckpointContent, algorithm: str, key_id: str) -> bytes:
    """The canonical bytes an HMAC covers: every field except the MAC."""
    from market_intelligence.evidence.canonical import canonical_json_bytes

    fields = {name: getattr(content, name) for name in StoreCheckpointContent.model_fields}
    dump = StoreCheckpointContent(**fields).model_dump(mode="json")
    dump["authentication"] = {"algorithm": algorithm, "key_id": key_id}
    return canonical_json_bytes(dump)
