"""Bounded vocabularies for the offline evidence and setup-card store
(docs/EVIDENCE_CARD_STORAGE_DESIGN.md §4.2; docs/REGISTRY_LOADING_DESIGN.md §13).

Every value that can change behavior is a ``StrEnum`` member, never an open
string.
"""

from __future__ import annotations

from enum import StrEnum


class StoreRecordKind(StrEnum):
    EVIDENCE_ITEM = "evidence_item"
    EVIDENCE_ENVELOPE = "evidence_envelope"
    EVIDENCE_CONFLICT = "evidence_conflict"
    EVIDENCE_BUNDLE = "evidence_bundle"
    SETUP_CARD = "setup_card"
    REGISTRY_VERSION = "registry_version"
    REGISTRY_ACTIVATION = "registry_activation"
    RECOVERY_EVENT = "recovery_event"


class StoreWriterId(StrEnum):
    EVIDENCE_INGEST = "evidence_ingest"
    CONFLICT_DETECTOR = "conflict_detector"
    MANUAL_CONFLICT_REVIEW = "manual_conflict_review"
    BUNDLE_BUILDER = "bundle_builder"
    CARD_BUILDER = "card_builder"
    REGISTRY_OPERATOR = "registry_operator"
    RECOVERY_OPERATOR = "recovery_operator"
    CHECKPOINT_OPERATOR = "checkpoint_operator"
    STORE_SERVICE = "store_service"


class StoreOperation(StrEnum):
    INITIALIZE_STORE = "initialize_store"
    APPEND_ENVELOPE = "append_envelope"
    APPEND_CONFLICT = "append_conflict"
    APPEND_BUNDLE = "append_bundle"
    APPEND_CARD = "append_card"
    REGISTER_REGISTRY_VERSION = "register_registry_version"
    APPEND_ACTIVATION = "append_activation"
    RECORD_RECOVERY = "record_recovery"
    ROTATE_CHECKPOINT_KEY = "rotate_checkpoint_key"
    RECORD_AUDIT_EVENTS = "record_audit_events"


class StoreState(StrEnum):
    SERVING = "serving"
    CHECKPOINT_RECONCILIATION_REQUIRED = "checkpoint_reconciliation_required"
    CHECKPOINT_KEY_ROTATION_IN_PROGRESS = "checkpoint_key_rotation_in_progress"
    RECOVERY_IN_PROGRESS = "recovery_in_progress"
    NOT_SERVICEABLE = "not_serviceable"
    READ_REFUSED = "read_refused"


class CheckpointMode(StrEnum):
    PRODUCTION_AUTHENTICATED = "production_authenticated"
    OFFLINE_DEVELOPMENT_UNAUTHENTICATED = "offline_development_unauthenticated"


class CheckpointAuthAlgorithm(StrEnum):
    HMAC_SHA256 = "hmac_sha256"


class SeverityRuleVersion(StrEnum):
    """``conflict-severity-rules-1`` is the fixed table of design §I.2,
    implemented by ``evidence.validation.expected_severity``."""

    CONFLICT_SEVERITY_RULES_1 = "conflict-severity-rules-1"


class RegistryKind(StrEnum):
    EVIDENCE_REGISTRY = "evidence_registry"
    SETUP_DEFINITION_REGISTRY = "setup_definition_registry"


class ActivationReason(StrEnum):
    INITIAL_ACTIVATION = "initial_activation"
    VERSION_UPGRADE = "version_upgrade"
    ROLLBACK = "rollback"


class RecoveryKind(StrEnum):
    BACKUP_RESTORATION = "backup_restoration"


class StoreAuditEventType(StrEnum):
    # Evidence Envelope design §O events.
    ENVELOPE_ACCEPTED = "envelope_accepted"
    ENVELOPE_REFUSED = "envelope_refused"
    APPEND_DUPLICATE_IGNORED = "append_duplicate_ignored"
    BUNDLE_BUILT = "bundle_built"
    BUNDLE_REFUSED = "bundle_refused"
    CONFLICT_RECORDED = "conflict_recorded"
    CONSUMER_ACCESS = "consumer_access"
    CONSUMER_EMIT = "consumer_emit"
    # Storage additions.
    STORE_INITIALIZED = "store_initialized"
    CONFLICT_REFUSED = "conflict_refused"
    CARD_RECORDED = "card_recorded"
    CARD_REFUSED = "card_refused"
    REGISTRY_VERSION_REGISTERED = "registry_version_registered"
    REGISTRY_ACTIVATED = "registry_activated"
    REGISTRY_ACTIVATION_REFUSED = "registry_activation_refused"
    HOLDOUT_WRITE_REFUSED = "holdout_write_refused"
    HOLDOUT_READ_REFUSED = "holdout_read_refused"
    INTEGRITY_STOP = "integrity_stop"
    STORE_RESTORED = "store_restored"
    STORE_OPEN_REFUSED = "store_open_refused"
    CHECKPOINT_REISSUE_REQUIRED = "checkpoint_reissue_required"
    CHECKPOINT_RECONCILIATION_ENTERED = "checkpoint_reconciliation_entered"
    CHECKPOINT_RECONCILED = "checkpoint_reconciled"
    CHECKPOINT_KEY_ROTATED = "checkpoint_key_rotated"


class IntegrityFinding(StrEnum):
    IDENTITY_MISMATCH = "identity_mismatch"
    RECORD_HASH_MISMATCH = "record_hash_mismatch"
    ROW_HASH_MISMATCH = "row_hash_mismatch"
    COMMIT_CHAIN_MISMATCH = "commit_chain_mismatch"
    COMMIT_SEQUENCE_GAP = "commit_sequence_gap"
    COMMIT_TIME_REGRESSION = "commit_time_regression"
    IMPOSSIBLE_DUPLICATE_IDENTITY = "impossible_duplicate_identity"
    PERSISTED_HOLDOUT_VIOLATION = "persisted_holdout_violation"
    INVALID_REGISTRY_ACTIVATION = "invalid_registry_activation"
    UNKNOWN_RECORD_SCHEMA = "unknown_record_schema"
    AUTHORITATIVE_CHAIN_AMBIGUITY = "authoritative_chain_ambiguity"
    MISSING_PARENT = "missing_parent"
    CARD_BUNDLE_MISSING = "card_bundle_missing"
    CARD_REGISTRY_VERSION_MISSING = "card_registry_version_missing"
    CHECKPOINT_MISSING = "checkpoint_missing"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    CHECKPOINT_AUTHENTICATION_FAILED = "checkpoint_authentication_failed"
    CHECKPOINT_ALGORITHM_UNSUPPORTED = "checkpoint_algorithm_unsupported"
    CHECKPOINT_STORE_MISMATCH = "checkpoint_store_mismatch"
    DATABASE_BEHIND_CHECKPOINT = "database_behind_checkpoint"
    CHECKPOINT_COMMIT_MISMATCH = "checkpoint_commit_mismatch"
    CHECKPOINT_RECOVERY_MISMATCH = "checkpoint_recovery_mismatch"
    CONFLICT_SEVERITY_IRREPRODUCIBLE = "conflict_severity_irreproducible"
    # Stops: a copied key column can omit or misfile records and cannot be
    # repaired in an insert-only table; an unreproducible bundle cannot be
    # proven to match the facts and builder that produced it.
    KEY_COLUMN_MISMATCH = "key_column_mismatch"
    BUNDLE_REPRODUCTION_MISMATCH = "bundle_reproduction_mismatch"
    # Not a stop only because a projection is rebuildable: startup rebuilds
    # and reverifies it before serving, and fails closed if it cannot.
    PROJECTION_MISMATCH = "projection_mismatch"


NON_STOP_FINDINGS = frozenset({IntegrityFinding.PROJECTION_MISMATCH})
STOP_FINDINGS = frozenset(IntegrityFinding) - NON_STOP_FINDINGS
