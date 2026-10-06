"""Backup, exact restoration, recovery completion and checkpoint key rotation
(docs/EVIDENCE_CARD_STORAGE_DESIGN.md §10.5, §10.6). Offline, temporary files only.

Recovery never re-appends, re-times or re-hashes an authoritative row: the
verified backup is restored byte for byte, then **one** recovery commit records
the recovery row, ``store_restored`` and ``checkpoint_reissue_required``. The
authenticated checkpoint equal to the database tip is the only proof that
reissuance completed; there is no ``checkpoint_reissued`` event.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.checkpoint import checkpoint_file_sha256, read_checkpoint
from market_intelligence.evidence_store.contracts import (
    CheckpointKeyRotation,
    RecoveryRecord,
)
from market_intelligence.evidence_store.enums import (
    CheckpointAuthAlgorithm,
    IntegrityFinding,
    RecoveryKind,
    StoreAuditEventType,
    StoreOperation,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.store import EvidenceStore, Txn, read_tip
from market_intelligence.evidence_store.store_io import (
    connect,
    refuse_real_database_path,
    select_one,
)
from market_intelligence.evidence_store.verification import verify_database


@dataclass(frozen=True)
class BackupManifest:
    backup_path: Path
    file_sha256: str
    high_water_commit_seq: int
    high_water_commit_digest: str
    store_instance_id: str


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wal_path(database_path: Path) -> Path:
    return database_path.with_name(database_path.name + ".wal")


def create_backup(store: EvidenceStore, destination: Path) -> BackupManifest:
    """A byte copy taken under the writer lock with no transaction open."""
    destination = refuse_real_database_path(destination)
    if destination.exists():
        raise StoreRefusal("backup_destination_exists")
    with store.writer_lock():
        if _wal_path(store.database_path).exists():
            raise StoreRefusal("database_wal_present")
        shutil.copyfile(store.database_path, destination)
    with connect(destination, read_only=True) as conn:
        tip = read_tip(conn)
        instance = select_one(conn, "SELECT store_instance_id FROM store_instance")
    if tip is None or instance is None:
        raise StoreRefusal("backup_empty")
    return BackupManifest(
        backup_path=destination,
        file_sha256=_file_sha256(destination),
        high_water_commit_seq=tip.commit_seq,
        high_water_commit_digest=tip.commit_digest,
        store_instance_id=instance["store_instance_id"],
    )


def verify_backup(store: EvidenceStore, manifest: BackupManifest) -> str:
    """Full verification of the copy; returns the verification report hash."""
    refuse_real_database_path(manifest.backup_path)  # before any byte is read
    if _file_sha256(manifest.backup_path) != manifest.file_sha256:
        raise StoreRefusal("backup_not_verified")
    report = verify_database(
        manifest.backup_path,
        payload_models=store.payload_models,
        synthetic_setup_definitions_for_tests=store._synthetic_definitions,
    )
    if report.has_stop():
        raise StoreRefusal("backup_not_verified")
    return report.report_sha256()


def restore_backup(
    store: EvidenceStore, manifest: BackupManifest, *, authorization_ref: str
) -> StoreState:
    """Authorized recovery behind the checkpoint, steps 1-7 of design §10.6."""
    # 1. Verify the backup, the authorization syntax and the existing checkpoint.
    report_sha = verify_backup(store, manifest)
    checkpoint, raw = read_checkpoint(store.checkpoint_config)
    if checkpoint is None or raw is None:
        raise StoreRefusal("checkpoint_missing")
    if checkpoint.store_instance_id != manifest.store_instance_id:
        raise StoreRefusal("recovery_store_mismatch")
    replaced = None
    try:
        with connect(store.database_path, read_only=True) as conn:
            replaced = read_tip(conn)
    except Exception:  # noqa: BLE001 - an unreadable database simply has no high-water mark
        replaced = None
    record = RecoveryRecord(
        recovery_kind=RecoveryKind.BACKUP_RESTORATION,
        restored_high_water_commit_seq=manifest.high_water_commit_seq,
        restored_high_water_commit_digest=manifest.high_water_commit_digest,
        backup_file_sha256=manifest.file_sha256,
        backup_verification_sha256=report_sha,
        replaced_high_water_commit_seq=replaced.commit_seq if replaced else None,
        replaced_high_water_commit_digest=replaced.commit_digest if replaced else None,
        superseded_checkpoint_commit_seq=checkpoint.commit_seq,
        superseded_checkpoint_commit_digest=checkpoint.commit_digest,
        superseded_checkpoint_created_at_utc=checkpoint.created_at_utc,
        superseded_checkpoint_sha256=checkpoint_file_sha256(raw),
        recovery_authorization_ref=authorization_ref,
    )
    # 2. Restore the verified database byte for byte.
    with store.writer_lock():
        if _wal_path(store.database_path).exists():
            raise StoreRefusal("database_wal_present")
        staging = store.database_path.with_name(f".restore-{secrets.token_hex(8)}.duckdb")
        shutil.copyfile(manifest.backup_path, staging)
        with staging.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(staging, store.database_path)
    store.verified = None  # the proof described the replaced database
    store._repaired_projections = ()
    store._registry_cache.clear()
    # 3. Verify identity, hashes and the complete chain.
    restored = verify_database(
        store.database_path,
        payload_models=store.payload_models,
        synthetic_setup_definitions_for_tests=store._synthetic_definitions,
    )
    if restored.has_stop():
        store.state = StoreState.READ_REFUSED
        raise IntegrityStop(restored.first_stop())
    store.instance_id = manifest.store_instance_id
    store.state = StoreState.RECOVERY_IN_PROGRESS

    # 4. One recovery commit; nothing claims the checkpoint exists yet.
    def build(txn: Txn) -> None:
        row = txn.connection.execute(
            "SELECT coalesce(max(recovery_seq), 0) FROM store_recovery_events"
        ).fetchone()
        txn.add("store_recovery_events", rec.recovery_columns(int(row[0]) + 1, record))
        for event, reason in (
            (StoreAuditEventType.STORE_RESTORED, "store_restored"),
            (StoreAuditEventType.CHECKPOINT_REISSUE_REQUIRED, "checkpoint_reissue_required"),
        ):
            txn.audit(
                store._event(
                    event,
                    reason,
                    actor=StoreWriterId.RECOVERY_OPERATOR.value,
                    reference=txn.commit_seq,
                )
            )

    store.run_write(
        StoreWriterId.RECOVERY_OPERATOR,
        StoreOperation.RECORD_RECOVERY,
        build,
        allowed_states=frozenset({StoreState.RECOVERY_IN_PROGRESS}),
        advance=False,
    )
    # 5-7. Write the checkpoint, prove equality, then resume.
    return complete_recovery_checkpoint(store)


def complete_recovery_checkpoint(store: EvidenceStore) -> StoreState:
    """Also the restart path: never re-appends the recovery commit. The
    restored store, recovery commit included, passes the complete scan
    before the checkpoint is reissued and service resumes."""
    store._verify_full()
    with connect(store.database_path, read_only=True) as conn:
        tip = read_tip(conn)
    if tip is None or tip.operation != StoreOperation.RECORD_RECOVERY.value:
        store.state = StoreState.READ_REFUSED
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_RECOVERY_MISMATCH)
    try:
        store._write_checkpoint_for(tip.commit_seq, tip.commit_digest)
        if not store.checkpoint_equals_tip():
            raise StoreRefusal("checkpoint_write_failed")
    except StoreRefusal:
        store.state = StoreState.RECOVERY_IN_PROGRESS
        raise StoreRefusal("workflow_incomplete") from None
    store.state = StoreState.SERVING
    return store.state


def rotate_checkpoint_key(
    store: EvidenceStore, *, new_key_id: str, authorization_ref: str
) -> StoreState:
    """Verify everything first, record key IDs only, re-sign with the new key,
    and accept no other write until the rotation finishes."""
    store._require_state(frozenset({StoreState.SERVING}))
    keyring = store.checkpoint_config.keyring
    if keyring is None:
        raise StoreRefusal("checkpoint_key_missing")
    if new_key_id not in keyring.keys:
        raise StoreRefusal("checkpoint_key_id_unknown")
    previous = store._signing_config().keyring.active_key_id  # type: ignore[union-attr]
    rotation = CheckpointKeyRotation(
        previous_key_id=previous,
        new_key_id=new_key_id,
        algorithm=CheckpointAuthAlgorithm.HMAC_SHA256,
        authorization_ref=authorization_ref,
    )
    store.state = StoreState.CHECKPOINT_KEY_ROTATION_IN_PROGRESS
    try:
        checkpoint, _ = read_checkpoint(store.checkpoint_config)
        report = verify_database(
            store.database_path,
            payload_models=store.payload_models,
            synthetic_setup_definitions_for_tests=store._synthetic_definitions,
        )
        if report.has_stop():
            raise IntegrityStop(report.first_stop())
        if checkpoint is None or not store.checkpoint_equals_tip():
            raise StoreRefusal("checkpoint_not_at_tip")
    except IntegrityStop:
        store.state = StoreState.READ_REFUSED
        raise
    except StoreRefusal:
        store.state = StoreState.SERVING
        raise

    def build(txn: Txn) -> None:
        row = txn.connection.execute(
            "SELECT coalesce(max(rotation_seq), 0) FROM store_checkpoint_key_rotations"
        ).fetchone()
        txn.add("store_checkpoint_key_rotations", rec.rotation_columns(int(row[0]) + 1, rotation))
        txn.audit(
            store._event(
                StoreAuditEventType.CHECKPOINT_KEY_ROTATED,
                "checkpoint_key_rotated",
                actor=StoreWriterId.CHECKPOINT_OPERATOR.value,
            )
        )

    try:
        commit = store.run_write(
            StoreWriterId.CHECKPOINT_OPERATOR,
            StoreOperation.ROTATE_CHECKPOINT_KEY,
            build,
            allowed_states=frozenset({StoreState.CHECKPOINT_KEY_ROTATION_IN_PROGRESS}),
            advance=False,
        )
    except StoreRefusal:
        store.state = StoreState.SERVING
        raise
    try:
        store._write_checkpoint_for(commit.commit_seq, commit.commit_digest)
        if not store.checkpoint_equals_tip():
            raise StoreRefusal("checkpoint_write_failed")
    except StoreRefusal:
        store.state = StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
        raise StoreRefusal("workflow_incomplete") from None
    store.state = StoreState.SERVING
    return store.state
