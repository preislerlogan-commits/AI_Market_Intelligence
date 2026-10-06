"""The offline evidence and setup-card store (docs/EVIDENCE_CARD_STORAGE_DESIGN.md).

Offline only: this class refuses the real project database and anything in the
real ``data/`` directory. It is exercised only on temporary databases and
temporary checkpoint directories.

Invariants implemented here:

- insert-only authoritative tables; no update, delete, replace or upsert;
- one writer at a time (the existing atomic lock-file pattern);
- an integer ``commit_seq`` is the total order; commit times never regress
  but may repeat; no time is ever invented;
- every authoritative commit is followed by an anti-rollback checkpoint
  advancement; a failure leaves the commit in place and the store in
  ``checkpoint_reconciliation_required``;
- ordinary refusals never stop the store; corruption found in authoritative
  storage is an ``IntegrityStop`` (read-refused mode);
- once per startup, and again after reconciliation or recovery, the complete
  authoritative verification scan must pass before the store serves; the
  in-process ``EvidenceStore`` holds that proof (``verified``) and hands out
  short-lived read connections that rely on it; a known projection mismatch
  is rebuilt and reverified before serving, and every other finding is a stop;
- the accepted stricter storage requirements: linear conflict-status chains,
  the frozen conflict-severity derivation, and one-root card chains.
"""

from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import duckdb
from pydantic import BaseModel

from market_intelligence.config.settings import Settings
from market_intelligence.evidence.bundle import RecordedConflict, RecordedItem, build_bundle
from market_intelligence.evidence.contracts import (
    EvidenceBundleManifest,
    EvidenceConflict,
    EvidenceEnvelope,
    EvidenceItem,
    bundle_identity_payload,
    item_identity_payload,
)
from market_intelligence.evidence.enums import ConflictStatus
from market_intelligence.evidence.errors import EvidenceValidationError, HoldoutRestrictedError
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence.validation import validate_conflict, validate_envelope
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.checkpoint import (
    CheckpointConfig,
    read_checkpoint,
    sign_checkpoint,
    validate_checkpoint_config,
    write_checkpoint,
)
from market_intelligence.evidence_store.contracts import (
    AuditEvent,
    BundleBuilderIdentity,
    StoreCheckpointContent,
    StoreCommit,
    StoreCommitContent,
    StoreInstance,
    canonical_column_value,
    compute_row_sha256,
    compute_rows_sha256,
    seal_commit,
)
from market_intelligence.evidence_store.enums import (
    CheckpointMode,
    IntegrityFinding,
    RegistryKind,
    StoreAuditEventType,
    StoreOperation,
    StoreState,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.registry_files import load_registry_content
from market_intelligence.evidence_store.severity import (
    derive_conflict_severity,
    required_item_ids,
)
from market_intelligence.evidence_store.store_io import (
    connect,
    insert_row,
    refuse_real_database_path,
    select_one,
    select_rows,
    sha256_text,
)
from market_intelligence.orchestration.lock import OrchestrationLockError, RunLock
from market_intelligence.setup_cards.contracts import SetupCard, card_identity_payload
from market_intelligence.setup_cards.definitions import SetupDefinition
from market_intelligence.setup_cards.enums import CardKind, LaneQualificationAvailability
from market_intelligence.setup_cards.supersession import validate_supersession
from market_intelligence.setup_cards.validation import (
    CardContext,
    card_cited_ids,
    lane_qualification,
    validate_setup_card,
)
from market_intelligence.storage.database import DuckDBManager

Clock = Callable[[], datetime]
PayloadModels = Mapping[str, type[BaseModel]]
LOCK_FILENAME = "evidence_store.lock"

_REFUSAL_EVENTS = {
    StoreOperation.APPEND_ENVELOPE: StoreAuditEventType.ENVELOPE_REFUSED,
    StoreOperation.APPEND_CONFLICT: StoreAuditEventType.CONFLICT_REFUSED,
    StoreOperation.APPEND_BUNDLE: StoreAuditEventType.BUNDLE_REFUSED,
    StoreOperation.APPEND_CARD: StoreAuditEventType.CARD_REFUSED,
    StoreOperation.APPEND_ACTIVATION: StoreAuditEventType.REGISTRY_ACTIVATION_REFUSED,
}


def _system_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Tip:
    commit_seq: int
    commit_digest: str
    committed_at_utc: datetime
    operation: str


@dataclass(frozen=True)
class VerifiedStartup:
    """Proof that this in-process store passed the complete authoritative
    verification scan. Readers rely on it instead of re-scanning; every
    record they return is still validated at read time.

    ``report_sha256`` is the final, finding-free scan. ``non_stop_findings``
    holds only findings already resolved before serving: today only
    ``projection_mismatch``, and only with the ``rebuilt_projections`` that
    were rebuilt and then reverified. A key-column or bundle-reproduction
    mismatch is a stop and can never appear here."""

    high_water_commit_seq: int
    report_sha256: str
    non_stop_findings: tuple[str, ...]
    rebuilt_projections: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        resolved = {IntegrityFinding.PROJECTION_MISMATCH.value}
        if not set(self.non_stop_findings) <= resolved or bool(self.non_stop_findings) != bool(
            self.rebuilt_projections
        ):
            raise ValueError("unresolved_finding_on_proof")


@dataclass
class Txn:
    """One write transaction: every row gets this commit's sequence and time."""

    connection: duckdb.DuckDBPyConnection
    commit_seq: int
    now: datetime
    tip: Tip | None
    row_hashes: list[str] = field(default_factory=list)
    _next_event_seq: int | None = None

    def add(self, table: str, columns: dict[str, Any]) -> None:
        values = dict(columns)
        values["commit_seq"] = self.commit_seq
        values["recorded_at_utc"] = self.now
        self.row_hashes.append(insert_row(self.connection, table, values))

    def project(self, table: str, columns: dict[str, Any]) -> None:
        insert_row(self.connection, table, columns)

    def audit(self, event: AuditEvent) -> None:
        if self._next_event_seq is None:
            row = self.connection.execute(
                "SELECT coalesce(max(event_seq), 0) FROM store_audit_events"
            ).fetchone()
            self._next_event_seq = int(row[0]) + 1 if row else 1
        self.add(
            "store_audit_events",
            {
                "event_seq": self._next_event_seq,
                "occurred_at_utc": self.now,
                "event_type": event.event_type.value,
                "actor": event.actor,
                "subject_record_id": event.subject_record_id,
                "reason_code": event.reason_code,
                "reference_commit_seq": event.reference_commit_seq,
            },
        )
        self._next_event_seq += 1

    def finish(self, writer: StoreWriterId, operation: StoreOperation) -> StoreCommit:
        if not self.row_hashes:
            raise StoreRefusal("empty_commit")
        commit = seal_commit(
            StoreCommitContent(
                commit_seq=self.commit_seq,
                committed_at_utc=self.now,
                writer_id=writer,
                operation=operation,
                row_count=len(self.row_hashes),
                rows_sha256=compute_rows_sha256(self.row_hashes),
                prev_commit_digest=self.tip.commit_digest if self.tip else None,
            )
        )
        insert_row(self.connection, "store_commits", commit.model_dump(mode="python"))
        return commit


def read_tip(connection: duckdb.DuckDBPyConnection) -> Tip | None:
    row = select_one(
        connection,
        "SELECT commit_seq, commit_digest, committed_at_utc, operation FROM store_commits "
        "WHERE commit_seq = (SELECT max(commit_seq) FROM store_commits)",
    )
    if row is None:
        return None
    return Tip(row["commit_seq"], row["commit_digest"], row["committed_at_utc"], row["operation"])


def visible_through(connection: duckdb.DuckDBPyConnection, as_of: datetime) -> int:
    """N_T: the greatest commit_seq with committed_at_utc <= T (0 if none).
    Because commit times never regress, this is a sequence prefix."""
    row = connection.execute(
        "SELECT coalesce(max(commit_seq), 0) FROM store_commits WHERE committed_at_utc <= ?",
        [as_of.astimezone(UTC).replace(tzinfo=None)],
    ).fetchone()
    return int(row[0]) if row else 0


def verify_commit_chain(connection: duckdb.DuckDBPyConnection) -> None:
    """Contiguous sequence, recomputed digests, previous links, no time regression."""
    rows = select_rows(connection, "SELECT * FROM store_commits ORDER BY commit_seq")
    previous: StoreCommit | None = None
    for expected_seq, row in enumerate(rows, start=1):
        if row["commit_seq"] != expected_seq:
            raise IntegrityStop(IntegrityFinding.COMMIT_SEQUENCE_GAP)
        try:
            commit = StoreCommit.model_validate_json(
                json.dumps({name: canonical_column_value(value) for name, value in row.items()})
            )
        except ValueError:
            raise IntegrityStop(IntegrityFinding.COMMIT_CHAIN_MISMATCH) from None
        expected_prev = previous.commit_digest if previous else None
        if commit.prev_commit_digest != expected_prev:
            raise IntegrityStop(IntegrityFinding.COMMIT_CHAIN_MISMATCH)
        if previous and commit.committed_at_utc < previous.committed_at_utc:
            raise IntegrityStop(IntegrityFinding.COMMIT_TIME_REGRESSION)
        previous = commit


def verify_row(table: str, row: dict[str, Any]) -> None:
    """Content hash and row hash of one authoritative row."""
    if "record_json" in row and sha256_text(row["record_json"]) != row["record_sha256"]:
        raise IntegrityStop(IntegrityFinding.RECORD_HASH_MISMATCH)
    if compute_row_sha256(row) != row["row_sha256"]:
        raise IntegrityStop(IntegrityFinding.ROW_HASH_MISMATCH)


class EvidenceStore:
    """One offline store instance over one temporary DuckDB file."""

    def __init__(
        self,
        *,
        settings: Settings,
        checkpoint: CheckpointConfig,
        payload_models: PayloadModels,
        clock: Clock = _system_clock,
        synthetic_setup_definitions_for_tests: Mapping[str, tuple[SetupDefinition, ...]]
        | None = None,
    ) -> None:
        refuse_real_database_path(settings.project_data_path)
        manager = DuckDBManager(settings=settings)
        self.database_path = refuse_real_database_path(manager.database_path)
        validate_checkpoint_config(checkpoint, self.database_path)
        if (
            synthetic_setup_definitions_for_tests
            and checkpoint.mode is CheckpointMode.PRODUCTION_AUTHENTICATED
        ):
            raise StoreRefusal("synthetic_definitions_test_only")
        self.settings = settings
        self.checkpoint_config = checkpoint
        self.payload_models = dict(payload_models)
        self.clock = clock
        self._synthetic_definitions = dict(synthetic_setup_definitions_for_tests or {})
        self._lock_path = settings.project_data_path / "cache" / LOCK_FILENAME
        self.state = StoreState.NOT_SERVICEABLE
        self.instance_id: str | None = None
        self.verified: VerifiedStartup | None = None
        self._lock_depth = 0
        self._lock_owner: int | None = None
        self._repaired_projections: tuple[str, ...] = ()
        self._registry_cache: dict[str, Any] = {}

    @classmethod
    def offline_reader(
        cls,
        synthetic_setup_definitions_for_tests: Mapping[str, tuple[SetupDefinition, ...]]
        | None = None,
    ) -> EvidenceStore:
        """A reader for verification only: no path, no checkpoint, no writes.
        Its lookups take an explicit read-only connection."""
        reader = cls.__new__(cls)
        reader._registry_cache = {}
        reader._synthetic_definitions = dict(synthetic_setup_definitions_for_tests or {})
        reader.state = StoreState.NOT_SERVICEABLE
        reader.verified = None
        reader._lock_depth = 0
        reader._lock_owner = None
        reader._repaired_projections = ()
        return reader

    # --- Locking and connections -----------------------------------------------------------------

    @contextmanager
    def writer_lock(self):
        """The single-writer lock file. Only the thread that acquired it may
        nest (startup projection repair inside reconciliation or
        initialization); any other caller, even on this store instance, must
        acquire the file and is refused while it exists. The file is removed
        only when the outermost holder exits, normally or by exception."""
        if self._lock_depth and self._lock_owner == threading.get_ident():
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return
        lock = RunLock(self._lock_path)
        try:
            lock.acquire()
        except OrchestrationLockError:
            raise StoreRefusal("store_writer_locked") from None
        self._lock_owner = threading.get_ident()
        self._lock_depth = 1
        try:
            yield
        finally:
            self._lock_depth = 0
            self._lock_owner = None
            lock.release()

    def read_connection(self):
        return connect(self.database_path, read_only=True)

    # --- Startup ---------------------------------------------------------------------------------

    def initialize(self) -> StoreState:
        """Create the schema on a temporary database and write commit 1 plus
        the first checkpoint. A brand-new empty store may lack a checkpoint
        only here."""
        with self.writer_lock():
            DuckDBManager(settings=self.settings).initialize()
            with connect(self.database_path, read_only=True) as conn:
                tip = read_tip(conn)
            if tip is None:
                checkpoint, _ = read_checkpoint(self.checkpoint_config)
                if checkpoint is not None:
                    self._stop(IntegrityFinding.DATABASE_BEHIND_CHECKPOINT)
                instance = StoreInstance(
                    store_instance_id=secrets.token_hex(16), initialized_at_utc=self.clock()
                )

                def build(txn: Txn) -> None:
                    txn.add("store_instance", rec.instance_columns(instance))
                    txn.audit(
                        self._event(StoreAuditEventType.STORE_INITIALIZED, "store_initialized")
                    )

                commit = self._transaction(
                    StoreWriterId.STORE_SERVICE, StoreOperation.INITIALIZE_STORE, build
                )
                self.instance_id = instance.store_instance_id
                self._advance_checkpoint(commit)
                self._verify_full()
                self.state = StoreState.SERVING
                return self.state
        return self.open()

    def open(self) -> StoreState:
        """Startup decision table of design §10.6, gated by one complete
        authoritative verification scan before the store serves."""
        self.verified = None
        self._repaired_projections = ()
        try:
            health = DuckDBManager(settings=self.settings).check_health()
            if not health.healthy:
                raise StoreRefusal("store_not_healthy")
            with self.read_connection() as conn:
                verify_commit_chain(conn)
                tip = read_tip(conn)
                instance_id = self._read_instance_id(conn) if tip is not None else None
                recovery = None
                if tip is not None and tip.operation == StoreOperation.RECORD_RECOVERY.value:
                    recovery = select_one(
                        conn,
                        "SELECT * FROM store_recovery_events WHERE commit_seq = ?",
                        [tip.commit_seq],
                    )
                checkpoint_digest_at = None
            checkpoint, raw = read_checkpoint(self.checkpoint_config)
            if tip is None:
                if checkpoint is not None:
                    self._stop(IntegrityFinding.DATABASE_BEHIND_CHECKPOINT)
                self.state = StoreState.NOT_SERVICEABLE
                return self.state
            self.instance_id = instance_id
            if checkpoint is None:
                self._stop(IntegrityFinding.CHECKPOINT_MISSING)
            if checkpoint.store_instance_id != self.instance_id:
                self._stop(IntegrityFinding.CHECKPOINT_STORE_MISMATCH)
            if recovery is not None:
                return self._open_after_recovery(tip, checkpoint, raw, recovery)
            if checkpoint.commit_seq > tip.commit_seq:
                self._stop(IntegrityFinding.DATABASE_BEHIND_CHECKPOINT)
            with self.read_connection() as conn:
                row = select_one(
                    conn,
                    "SELECT commit_digest FROM store_commits WHERE commit_seq = ?",
                    [checkpoint.commit_seq],
                )
                checkpoint_digest_at = row["commit_digest"] if row else None
            if checkpoint_digest_at != checkpoint.commit_digest:
                self._stop(IntegrityFinding.CHECKPOINT_COMMIT_MISMATCH)
            if checkpoint.commit_seq == tip.commit_seq:
                # The checkpoint and chain alone miss corrupted rows.
                self._verify_full()
                self.state = StoreState.SERVING
                return self.state
            # Reconciliation scans before it writes and again before serving.
            self.state = StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
            return self.reconcile()
        except IntegrityStop:
            self.state = StoreState.READ_REFUSED
            raise
        except StoreRefusal:
            if self.state is not StoreState.CHECKPOINT_RECONCILIATION_REQUIRED:
                self.state = StoreState.NOT_SERVICEABLE
            raise

    def _read_instance_id(self, conn: duckdb.DuckDBPyConnection) -> str:
        """The single database identity row; anything else is an integrity stop."""
        rows = select_rows(conn, "SELECT * FROM store_instance")
        if len(rows) != 1:
            self._stop(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY)
        verify_row("store_instance", rows[0])
        return rows[0]["store_instance_id"]

    def _open_after_recovery(
        self, tip: Tip, checkpoint, raw, recovery: dict[str, Any]
    ) -> StoreState:
        from market_intelligence.evidence_store.checkpoint import checkpoint_file_sha256

        if (
            checkpoint.commit_seq == tip.commit_seq
            and checkpoint.commit_digest == tip.commit_digest
        ):
            self._verify_full()
            self.state = StoreState.SERVING
            return self.state
        record = rec.parse_recovery(recovery["record_json"])
        matches_superseded = (
            checkpoint.commit_seq == record.superseded_checkpoint_commit_seq
            and checkpoint.commit_digest == record.superseded_checkpoint_commit_digest
            and checkpoint.created_at_utc == record.superseded_checkpoint_created_at_utc
            and checkpoint_file_sha256(raw) == record.superseded_checkpoint_sha256
        )
        if not matches_superseded:
            self._stop(IntegrityFinding.CHECKPOINT_RECOVERY_MISMATCH)
        self.state = StoreState.RECOVERY_IN_PROGRESS
        from market_intelligence.evidence_store.recovery import complete_recovery_checkpoint

        return complete_recovery_checkpoint(self)

    def _verify_full(self) -> VerifiedStartup:
        """The complete authoritative scan (the operator verification
        command's checks). Runs at startup and after reconciliation or
        recovery, never per reader or per query.

        A stop finding enters read-refused mode and nothing is repaired. A
        projection mismatch keeps service blocked (no proof exists) while only
        the affected projection tables are rebuilt from verified authoritative
        rows and the whole store is scanned again; any failure is a stop."""
        from market_intelligence.evidence_store import projections, verification

        def scan():
            return verification.verify_database(
                self.database_path,
                payload_models=self.payload_models,
                synthetic_setup_definitions_for_tests=self._synthetic_definitions,
            )

        self.verified = None
        report = scan()
        if report.has_stop():
            self._stop(report.first_stop())
        rebuilt = tuple(
            sorted(
                {
                    f.table
                    for f in report.findings
                    if f.finding is IntegrityFinding.PROJECTION_MISMATCH
                }
            )
        )
        if rebuilt:
            try:
                projections.rebuild_projection_tables(self, rebuilt)
            except IntegrityStop as stop:
                self._stop(stop.finding)
            except (StoreRefusal, EvidenceValidationError, duckdb.Error, ValueError):
                self._stop(IntegrityFinding.PROJECTION_MISMATCH)
            report = scan()
            if report.has_stop():
                self._stop(report.first_stop())
            if report.findings:  # the rebuilt projection still does not match
                self._stop(IntegrityFinding.PROJECTION_MISMATCH)
        # A startup may scan twice (reconciliation); the proof keeps every
        # repair made since this startup or restore began.
        self._repaired_projections = tuple(sorted({*self._repaired_projections, *rebuilt}))
        self.verified = VerifiedStartup(
            high_water_commit_seq=report.high_water_commit_seq,
            report_sha256=report.report_sha256(),
            non_stop_findings=(IntegrityFinding.PROJECTION_MISMATCH.value,)
            if self._repaired_projections
            else (),
            rebuilt_projections=self._repaired_projections,
        )
        return self.verified

    # --- Commit machinery ------------------------------------------------------------------------

    def _stop(self, finding: IntegrityFinding):
        self.state = StoreState.READ_REFUSED
        raise IntegrityStop(finding)

    def _require_state(self, allowed: frozenset[StoreState]) -> None:
        if self.state not in allowed:
            if self.state is StoreState.READ_REFUSED:
                raise StoreRefusal("store_read_refused")
            raise StoreRefusal(self.state.value)
        if self.state is StoreState.SERVING and self.verified is None:
            # Serving always rests on a completed full scan in this process.
            raise StoreRefusal("checkpoint_not_confirmed")

    def _transaction(
        self, writer: StoreWriterId, operation: StoreOperation, build: Callable[[Txn], Any]
    ) -> StoreCommit:
        with connect(self.database_path, read_only=False) as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                tip = read_tip(conn)
                now = self.clock()
                if tip is not None and now < tip.committed_at_utc:
                    raise StoreRefusal("store_clock_regressed")
                txn = Txn(conn, (tip.commit_seq + 1) if tip else 1, now, tip)
                build(txn)
                commit = txn.finish(writer, operation)
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        return commit

    def _event(
        self,
        event_type: StoreAuditEventType,
        reason: str,
        *,
        actor: str = StoreWriterId.STORE_SERVICE.value,
        subject: str | None = None,
        reference: int | None = None,
    ) -> AuditEvent:
        return AuditEvent(
            event_type=event_type,
            actor=actor,
            subject_record_id=subject,
            reason_code=reason,
            reference_commit_seq=reference,
        )

    def _signing_config(self) -> CheckpointConfig:
        """Sign with the key named by the latest rotation row, else the
        configured active key."""
        config = self.checkpoint_config
        if config.keyring is None:
            return config
        with self.read_connection() as conn:
            row = select_one(
                conn,
                "SELECT new_key_id FROM store_checkpoint_key_rotations WHERE rotation_seq = "
                "(SELECT max(rotation_seq) FROM store_checkpoint_key_rotations)",
            )
        key_id = row["new_key_id"] if row else config.keyring.active_key_id
        if key_id not in config.keyring.keys:
            raise StoreRefusal("checkpoint_key_id_unknown")
        return CheckpointConfig(config.state_dir, config.mode, config.keyring.with_active(key_id))

    def _write_checkpoint_for(self, commit_seq: int, commit_digest: str) -> None:
        if self.instance_id is None:
            raise StoreRefusal("store_not_initialized")
        content = StoreCheckpointContent(
            store_instance_id=self.instance_id,
            commit_seq=commit_seq,
            commit_digest=commit_digest,
            created_at_utc=self.clock(),
        )
        config = self._signing_config()
        write_checkpoint(config, sign_checkpoint(content, config))

    def checkpoint_equals_tip(self) -> bool:
        checkpoint, _ = read_checkpoint(self.checkpoint_config)
        with self.read_connection() as conn:
            tip = read_tip(conn)
        return (
            checkpoint is not None
            and tip is not None
            and checkpoint.commit_seq == tip.commit_seq
            and checkpoint.commit_digest == tip.commit_digest
        )

    def _advance_checkpoint(self, commit: StoreCommit) -> None:
        """After every authoritative commit. A failure keeps the commit and
        enters ``checkpoint_reconciliation_required`` (no unrelated writes)."""
        try:
            self._write_checkpoint_for(commit.commit_seq, commit.commit_digest)
            if not self.checkpoint_equals_tip():
                raise StoreRefusal("checkpoint_write_failed")
        except StoreRefusal:
            self.state = StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
            raise StoreRefusal("workflow_incomplete") from None

    def run_write(
        self,
        writer: StoreWriterId,
        operation: StoreOperation,
        build: Callable[[Txn], Any],
        *,
        allowed_states: frozenset[StoreState] = frozenset({StoreState.SERVING}),
        advance: bool = True,
    ) -> StoreCommit:
        """One authoritative write: refuse, or commit then checkpoint."""
        self._require_state(allowed_states)
        with self.writer_lock():
            try:
                commit = self._transaction(writer, operation, build)
            except IntegrityStop:
                self.state = StoreState.READ_REFUSED
                raise
            except HoldoutRestrictedError:
                self._record_refusal(
                    StoreAuditEventType.HOLDOUT_WRITE_REFUSED, "holdout_restricted", writer, None
                )
                raise StoreRefusal("holdout_restricted") from None
            except EvidenceValidationError as refusal:
                event = _REFUSAL_EVENTS.get(operation)
                if event is not None:
                    self._record_refusal(event, refusal.reason, writer, None)
                raise StoreRefusal(refusal.reason) from None
            if advance:
                self._advance_checkpoint(commit)
        return commit

    def _record_refusal(
        self, event: StoreAuditEventType, reason: str, writer: StoreWriterId, subject: str | None
    ) -> None:
        """A refusal is its own audit commit. It never stops the store; if it
        cannot be recorded, the original refusal still stands."""
        if self.state is not StoreState.SERVING:
            return
        audit = self._event(event, reason, actor=writer.value, subject=subject)
        try:
            commit = self._transaction(
                StoreWriterId.STORE_SERVICE,
                StoreOperation.RECORD_AUDIT_EVENTS,
                lambda txn: txn.audit(audit),
            )
            self._advance_checkpoint(commit)
        except (StoreRefusal, EvidenceValidationError, duckdb.Error):
            pass

    def record_audit_event(self, event: AuditEvent) -> StoreCommit:
        """A standalone audit commit (for example ``consumer_access``)."""
        return self.run_write(
            StoreWriterId.STORE_SERVICE,
            StoreOperation.RECORD_AUDIT_EVENTS,
            lambda txn: txn.audit(event),
        )

    # --- Reconciliation --------------------------------------------------------------------------

    def reconcile(self) -> StoreState:
        """The fixed eight-step sequence (design §10.6). The audit event is
        never duplicated: a committed one is found and step 5 is skipped. The
        complete authoritative scan runs before step 4 writes anything and
        again before step 8 resumes service."""
        # Never a way out of an integrity stop or an unfinished recovery:
        # leaving read-refused mode is a human decision after review.
        self._require_state(
            frozenset({StoreState.SERVING, StoreState.CHECKPOINT_RECONCILIATION_REQUIRED})
        )
        with self.writer_lock():
            checkpoint, _ = read_checkpoint(self.checkpoint_config)  # step 1
            if checkpoint is None:
                self._stop(IntegrityFinding.CHECKPOINT_MISSING)
            with self.read_connection() as conn:
                if checkpoint.store_instance_id != self._read_instance_id(conn):
                    self._stop(IntegrityFinding.CHECKPOINT_STORE_MISMATCH)
                verify_commit_chain(conn)  # steps 2-3: the whole chain, prefix included
                row = select_one(
                    conn,
                    "SELECT commit_digest FROM store_commits WHERE commit_seq = ?",
                    [checkpoint.commit_seq],
                )
                if row is None or row["commit_digest"] != checkpoint.commit_digest:
                    self._stop(IntegrityFinding.CHECKPOINT_COMMIT_MISMATCH)
                tip = read_tip(conn)
                already = self._reconciled_event_at_tip(conn, tip)
            self.state = StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
            self._verify_full()  # nothing is written onto an unverified store
            try:
                self._write_checkpoint_for(tip.commit_seq, tip.commit_digest)  # step 4
            except StoreRefusal:
                raise StoreRefusal("workflow_incomplete") from None
            if not already:  # step 5
                event = self._event(
                    StoreAuditEventType.CHECKPOINT_RECONCILED,
                    "checkpoint_reconciled",
                    reference=tip.commit_seq,
                )
                commit = self._transaction(
                    StoreWriterId.STORE_SERVICE,
                    StoreOperation.RECORD_AUDIT_EVENTS,
                    lambda txn: txn.audit(event),
                )
                try:
                    self._write_checkpoint_for(commit.commit_seq, commit.commit_digest)  # step 6
                except StoreRefusal:
                    raise StoreRefusal("workflow_incomplete") from None
            if not self.checkpoint_equals_tip():  # step 7
                raise StoreRefusal("workflow_incomplete")
            self._verify_full()  # the reconciled store passes the full scan first
            self.state = StoreState.SERVING  # step 8
            return self.state

    @staticmethod
    def _reconciled_event_at_tip(conn: duckdb.DuckDBPyConnection, tip: Tip) -> bool:
        if tip.operation != StoreOperation.RECORD_AUDIT_EVENTS.value:
            return False
        events = select_rows(
            conn,
            "SELECT event_type, reference_commit_seq FROM store_audit_events WHERE commit_seq = ?",
            [tip.commit_seq],
        )
        return (
            len(events) == 1
            and events[0]["event_type"] == StoreAuditEventType.CHECKPOINT_RECONCILED.value
            and events[0]["reference_commit_seq"] == tip.commit_seq - 1
        )

    # --- Registry lookups ------------------------------------------------------------------------

    def in_force(
        self, conn: duckdb.DuckDBPyConnection, kind: RegistryKind, at: datetime
    ) -> str | None:
        """The registry version in force at ``at``: the activation with the
        greatest ``effective_from_utc <= at``. Ties are an integrity stop."""
        rows = select_rows(
            conn,
            "SELECT activation_id, registry_version_id FROM registry_activations "
            "WHERE registry_kind = ? AND effective_from_utc = ("
            "SELECT max(effective_from_utc) FROM registry_activations "
            "WHERE registry_kind = ? AND effective_from_utc <= ?)",
            [kind.value, kind.value, at],
        )
        if not rows:
            return None
        if len(rows) > 1:
            self._stop(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY)
        return rows[0]["registry_version_id"]

    def load_registry(self, conn: duckdb.DuckDBPyConnection, version_id: str) -> Any:
        cached = self._registry_cache.get(version_id)
        if cached is not None:
            return cached
        row = select_one(
            conn, "SELECT * FROM registry_versions WHERE registry_version_id = ?", [version_id]
        )
        if row is None:
            raise StoreRefusal("registry_version_not_registered")
        verify_row("registry_versions", row)
        record = rec.parse_registry_version(row["record_json"])
        kind = RegistryKind(row["registry_kind"])
        try:
            registry = load_registry_content(kind, record.content_json)
        except StoreRefusal:
            self._stop(IntegrityFinding.INVALID_REGISTRY_ACTIVATION)
        from market_intelligence.evidence_store.registry_files import registry_identity

        if registry_identity(kind, registry) != version_id:
            self._stop(IntegrityFinding.IDENTITY_MISMATCH)
        self._registry_cache[version_id] = registry
        return registry

    def evidence_registry_at(
        self,
        conn: duckdb.DuckDBPyConnection,
        at: datetime,
        *,
        missing: str = "active_registry_missing",
    ) -> tuple[EvidenceRegistry, str]:
        version_id = self.in_force(conn, RegistryKind.EVIDENCE_REGISTRY, at)
        if version_id is None:
            raise StoreRefusal(missing)
        return self.load_registry(conn, version_id), version_id

    def setup_definitions_for(self, version_id: str, registry: Any) -> tuple[SetupDefinition, ...]:
        """Production format is structurally empty. Test-only synthetic
        definitions are allowed only in offline development mode."""
        synthetic = self._synthetic_definitions.get(version_id)
        if synthetic is not None:
            return synthetic
        return tuple(registry.definitions)

    # --- Item reads ------------------------------------------------------------------------------

    def _load_items(
        self, conn: duckdb.DuckDBPyConnection, ids: set[str]
    ) -> dict[str, EvidenceItem]:
        found: dict[str, EvidenceItem] = {}
        for item_id in sorted(ids):
            row = select_one(conn, "SELECT * FROM evidence_items WHERE item_id = ?", [item_id])
            if row is None:
                continue
            verify_row("evidence_items", row)
            found[item_id] = rec.parse_item(row["record_json"])
        return found

    def records_as_of(
        self, conn: duckdb.DuckDBPyConnection, as_of: datetime, registry: EvidenceRegistry
    ) -> tuple[list[RecordedItem], list[RecordedConflict], int]:
        """Every item and conflict visible at ``as_of`` (commit_seq <= N_T),
        verified, holdout-checked, unfiltered. ID order is serialization only."""
        n_t = visible_through(conn, as_of)
        items: list[RecordedItem] = []
        for row in select_rows(
            conn, "SELECT * FROM evidence_items WHERE commit_seq <= ? ORDER BY item_id", [n_t]
        ):
            verify_row("evidence_items", row)
            item = rec.parse_item(row["record_json"])
            try:
                restricted = is_holdout_restricted(item, registry)
            except EvidenceValidationError:
                self._stop(IntegrityFinding.UNKNOWN_RECORD_SCHEMA)
            if restricted:
                self._stop(IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION)
            items.append(RecordedItem(item=item, recorded_at_utc=row["recorded_at_utc"]))
        conflicts: list[RecordedConflict] = []
        for row in select_rows(
            conn,
            "SELECT * FROM evidence_conflicts WHERE commit_seq <= ? ORDER BY conflict_id",
            [n_t],
        ):
            verify_row("evidence_conflicts", row)
            conflicts.append(
                RecordedConflict(
                    conflict=rec.parse_conflict(row["record_json"]),
                    recorded_at_utc=row["recorded_at_utc"],
                )
            )
        return items, conflicts, n_t

    # --- Envelope append -------------------------------------------------------------------------

    def append_envelope(self, envelope: EvidenceEnvelope) -> StoreCommit:
        def build(txn: Txn) -> None:
            conn = txn.connection
            existing = select_one(
                conn,
                "SELECT record_json FROM evidence_envelopes WHERE envelope_id = ?",
                [envelope.envelope_id],
            )
            if existing is not None:
                stored = json.loads(existing["record_json"])
                incoming = rec.envelope_header(envelope)
                for header in (stored, incoming):
                    header.pop("emitted_at_utc")
                if stored != incoming:
                    self._stop(IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY)
                txn.audit(
                    self._event(
                        StoreAuditEventType.APPEND_DUPLICATE_IGNORED,
                        "append_duplicate_ignored",
                        actor=StoreWriterId.EVIDENCE_INGEST.value,
                        subject=envelope.envelope_id,
                    )
                )
                return
            registry, registry_id = self.evidence_registry_at(conn, txn.now)
            if envelope.registry_version_id != registry_id:
                raise StoreRefusal("registry_version_mismatch")
            referenced = {
                ref
                for item in envelope.items
                for ref in (*item.provenance.parent_evidence_ids, item.revision.supersedes_item_id)
                if ref is not None
            }
            known = self._load_items(conn, referenced | {i.item_id for i in envelope.items})
            stored_items = {
                k: v for k, v in known.items() if k not in {i.item_id for i in envelope.items}
            }
            validate_envelope(
                envelope, registry, known_items=stored_items, payload_models=self.payload_models
            )
            for item in envelope.items:
                if item.item_id in known:
                    if item_identity_payload(known[item.item_id]) != item_identity_payload(item):
                        self._stop(IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY)
                    continue
                txn.add("evidence_items", rec.item_columns(item, registry_id))
                for table, columns in rec.item_projection_rows(item, txn.commit_seq):
                    txn.project(table, columns)
            txn.add("evidence_envelopes", rec.envelope_columns(envelope))
            for item in envelope.items:
                txn.project(
                    "evidence_envelope_items",
                    {"envelope_id": envelope.envelope_id, "item_id": item.item_id},
                )
            txn.audit(
                self._event(
                    StoreAuditEventType.ENVELOPE_ACCEPTED,
                    "envelope_accepted",
                    actor=StoreWriterId.EVIDENCE_INGEST.value,
                    subject=envelope.envelope_id,
                )
            )

        return self.run_write(StoreWriterId.EVIDENCE_INGEST, StoreOperation.APPEND_ENVELOPE, build)

    # --- Conflict append -------------------------------------------------------------------------

    def conflict_chain_tip(
        self, conn: duckdb.DuckDBPyConnection, chain_key: str
    ) -> dict[str, Any] | None:
        rows = select_rows(
            conn, "SELECT * FROM evidence_conflicts WHERE conflict_chain_key = ?", [chain_key]
        )
        if not rows:
            return None
        roots = [r for r in rows if r["supersedes_conflict_id"] is None]
        superseded = {r["supersedes_conflict_id"] for r in rows if r["supersedes_conflict_id"]}
        tips = [r for r in rows if r["conflict_id"] not in superseded]
        if len(roots) != 1 or len(tips) != 1:
            self._stop(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY)
        return tips[0]

    def append_conflict(
        self, conflict: EvidenceConflict, *, writer: StoreWriterId = StoreWriterId.CONFLICT_DETECTOR
    ) -> StoreCommit:
        if writer not in (StoreWriterId.CONFLICT_DETECTOR, StoreWriterId.MANUAL_CONFLICT_REVIEW):
            raise StoreRefusal("writer_not_permitted")
        if (
            writer is StoreWriterId.MANUAL_CONFLICT_REVIEW
            and conflict.status is not ConflictStatus.ACKNOWLEDGED_UNRESOLVABLE
        ):
            raise StoreRefusal("writer_not_permitted")

        def build(txn: Txn) -> None:
            conn = txn.connection
            existing = select_one(
                conn,
                "SELECT record_json FROM evidence_conflicts WHERE conflict_id = ?",
                [conflict.conflict_id],
            )
            if existing is not None:
                stored = rec.parse_conflict(existing["record_json"])
                from market_intelligence.evidence.contracts import conflict_identity_payload

                if conflict_identity_payload(stored) != conflict_identity_payload(conflict):
                    self._stop(IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY)
                txn.audit(
                    self._event(
                        StoreAuditEventType.APPEND_DUPLICATE_IGNORED,
                        "append_duplicate_ignored",
                        actor=writer.value,
                        subject=conflict.conflict_id,
                    )
                )
                return
            if conflict.evaluated_as_of_utc >= txn.now:
                raise StoreRefusal("conflict_as_of_not_past")
            resolution_ids = (
                set(conflict.resolution.resolution_evidence_ids) if conflict.resolution else set()
            )
            items = self._load_items(conn, set(conflict.involved_item_ids) | resolution_ids)
            involved = [items[i] for i in conflict.involved_item_ids if i in items]
            if len(involved) != len(conflict.involved_item_ids):
                raise StoreRefusal("unknown_involved_item")
            registry, _ = self.evidence_registry_at(
                conn, conflict.evaluated_as_of_utc, missing="conflict_registry_version_missing"
            )
            now_registry, current_id = self.evidence_registry_at(conn, txn.now)
            derivation = derive_conflict_severity(conflict, involved, registry)
            if conflict.severity.value != derivation.severity:
                raise StoreRefusal("conflict_severity_mismatch")
            chain_key = rec.conflict_chain_key(conflict)
            tip = self.conflict_chain_tip(conn, chain_key)
            prior = None
            if conflict.resolution is None:
                if tip is not None:
                    raise StoreRefusal("conflict_chain_already_started")
                at_commit = derive_conflict_severity(conflict, involved, now_registry)
                if (
                    at_commit.matched_requirement_ids != derivation.matched_requirement_ids
                    or at_commit.severity != derivation.severity
                ):
                    raise StoreRefusal("conflict_evaluation_stale")
            else:
                supersedes = conflict.resolution.supersedes_conflict_id
                if tip is None or tip["conflict_id"] != supersedes:
                    raise StoreRefusal("conflict_status_not_tip")
                prior = rec.parse_conflict(tip["record_json"])
            validate_conflict(
                conflict,
                items,
                registry,
                required_item_ids=required_item_ids(registry, involved),
                prior=prior,
            )
            txn.add("evidence_conflicts", rec.conflict_columns(conflict, derivation, current_id))
            for item_id in conflict.involved_item_ids:
                txn.project(
                    "evidence_conflict_items",
                    {"conflict_id": conflict.conflict_id, "item_id": item_id},
                )
            txn.audit(
                self._event(
                    StoreAuditEventType.CONFLICT_RECORDED,
                    "conflict_recorded",
                    actor=writer.value,
                    subject=conflict.conflict_id,
                )
            )

        return self.run_write(writer, StoreOperation.APPEND_CONFLICT, build)

    # --- Bundle append ---------------------------------------------------------------------------

    def rebuild_bundle(
        self, conn: duckdb.DuckDBPyConnection, bundle: EvidenceBundleManifest
    ) -> tuple[EvidenceBundleManifest, int, EvidenceRegistry]:
        version_id = self.in_force(conn, RegistryKind.EVIDENCE_REGISTRY, bundle.as_of_utc)
        if version_id is None or version_id != bundle.registry_version_id:
            raise StoreRefusal("bundle_registry_not_in_force")
        registry = self.load_registry(conn, version_id)
        items, conflicts, n_t = self.records_as_of(conn, bundle.as_of_utc, registry)
        rebuilt = build_bundle(
            registry=registry,
            purpose=bundle.purpose,
            as_of=bundle.as_of_utc,
            consumer_context=bundle.consumer_context,
            query=bundle.query,
            recorded_items=items,
            recorded_conflicts=conflicts,
            built_at=bundle.built_at_utc,
        )
        return rebuilt, n_t, registry

    def append_bundle(
        self, bundle: EvidenceBundleManifest, builder: BundleBuilderIdentity
    ) -> StoreCommit:
        def build(txn: Txn) -> None:
            conn = txn.connection
            existing = select_one(
                conn,
                "SELECT record_json FROM evidence_bundles WHERE bundle_id = ?",
                [bundle.bundle_id],
            )
            if existing is not None:
                stored = rec.parse_bundle(existing["record_json"])
                if bundle_identity_payload(stored) != bundle_identity_payload(bundle):
                    self._stop(IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY)
                txn.audit(
                    self._event(
                        StoreAuditEventType.APPEND_DUPLICATE_IGNORED,
                        "append_duplicate_ignored",
                        actor=StoreWriterId.BUNDLE_BUILDER.value,
                        subject=bundle.bundle_id,
                    )
                )
                return
            if bundle.as_of_utc >= txn.now:
                raise StoreRefusal("bundle_as_of_not_past")
            rebuilt, n_t, registry = self.rebuild_bundle(conn, bundle)
            if rebuilt.bundle_id != bundle.bundle_id:
                raise StoreRefusal("bundle_not_reproducible")
            txn.add("evidence_bundles", rec.bundle_columns(bundle, builder, n_t))
            items = self._load_items(conn, {e.item_id for e in bundle.entries})
            for table, columns in rec.bundle_projection_rows(bundle, registry, items):
                txn.project(table, columns)
            txn.audit(
                self._event(
                    StoreAuditEventType.BUNDLE_BUILT,
                    "bundle_built",
                    actor=StoreWriterId.BUNDLE_BUILDER.value,
                    subject=bundle.bundle_id,
                )
            )

        return self.run_write(StoreWriterId.BUNDLE_BUILDER, StoreOperation.APPEND_BUNDLE, build)

    def load_bundle(self, conn: duckdb.DuckDBPyConnection, bundle_id: str) -> dict[str, Any] | None:
        row = select_one(conn, "SELECT * FROM evidence_bundles WHERE bundle_id = ?", [bundle_id])
        if row is not None:
            verify_row("evidence_bundles", row)
        return row

    # --- Card append -----------------------------------------------------------------------------

    def card_context(
        self,
        conn: duckdb.DuckDBPyConnection,
        bundle: EvidenceBundleManifest,
        setup_registry_id: str,
    ) -> CardContext:
        registry = self.load_registry(conn, bundle.registry_version_id)
        setup_registry = self.load_registry(conn, setup_registry_id)
        items = self._load_items(conn, {e.item_id for e in bundle.entries})
        conflicts: dict[str, EvidenceConflict] = {}
        for conflict_id in bundle.conflict_ids:
            row = select_one(
                conn, "SELECT * FROM evidence_conflicts WHERE conflict_id = ?", [conflict_id]
            )
            if row is None:
                self._stop(IntegrityFinding.MISSING_PARENT)
            verify_row("evidence_conflicts", row)
            conflicts[conflict_id] = rec.parse_conflict(row["record_json"])
        return CardContext(
            bundle=bundle,
            items=items,
            conflicts=conflicts,
            registry=registry,
            setup_definitions=self.setup_definitions_for(setup_registry_id, setup_registry),
        )

    def card_chain_tip(
        self, conn: duckdb.DuckDBPyConnection, chain_key: str
    ) -> dict[str, Any] | None:
        rows = select_rows(
            conn, "SELECT * FROM setup_cards WHERE chain_key_sha256 = ?", [chain_key]
        )
        if not rows:
            return None
        superseded = {r["supersedes_card_id"] for r in rows if r["supersedes_card_id"]}
        tips = [r for r in rows if r["card_id"] not in superseded]
        roots = [r for r in rows if r["supersedes_card_id"] is None]
        if len(tips) != 1 or len(roots) != 1:
            self._stop(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY)
        return tips[0]

    def append_card(self, card: SetupCard) -> StoreCommit:
        def build(txn: Txn) -> None:
            conn = txn.connection
            existing = select_one(
                conn, "SELECT record_json FROM setup_cards WHERE card_id = ?", [card.card_id]
            )
            if existing is not None:
                stored = rec.parse_card(existing["record_json"])
                if card_identity_payload(stored) != card_identity_payload(card):
                    self._stop(IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY)
                txn.audit(
                    self._event(
                        StoreAuditEventType.APPEND_DUPLICATE_IGNORED,
                        "append_duplicate_ignored",
                        actor=StoreWriterId.CARD_BUILDER.value,
                        subject=card.card_id,
                    )
                )
                return
            bundle_row = self.load_bundle(conn, card.decision_context_bundle_id)
            if bundle_row is None:
                raise StoreRefusal("card_bundle_not_stored")
            bundle = rec.parse_bundle(bundle_row["record_json"])
            if card.registry_version_id != bundle.registry_version_id:
                raise StoreRefusal("card_registry_mismatch")
            setup_registry_id = self.in_force(
                conn, RegistryKind.SETUP_DEFINITION_REGISTRY, bundle.as_of_utc
            )
            if setup_registry_id is None:
                raise StoreRefusal("setup_definition_not_registered")
            ctx = self.card_context(conn, bundle, setup_registry_id)
            validate_setup_card(card, ctx)
            if card.card_kind is CardKind.NO_QUALIFIED_SETUP:
                availability, _ = lane_qualification(ctx, card.lane, card_cited_ids(card))
                if availability is not LaneQualificationAvailability.AVAILABLE:
                    raise StoreRefusal("lane_qualification_not_available")
            chain_key = rec.card_chain_key(card)
            tip = self.card_chain_tip(conn, chain_key)
            if card.card_revision.revision_number == 1:
                if tip is not None:
                    raise StoreRefusal("card_chain_already_started")
                txn.add("setup_card_chains", rec.chain_columns(card))
            else:
                supersedes = card.card_revision.supersedes_card_id
                if tip is None:
                    raise StoreRefusal("predecessor_card_missing")
                if tip["card_id"] != supersedes:
                    raise StoreRefusal("card_predecessor_not_tip")
                validate_supersession(card, rec.parse_card(tip["record_json"]))
            txn.add("setup_cards", rec.card_columns(card, setup_registry_id))
            txn.audit(
                self._event(
                    StoreAuditEventType.CARD_RECORDED,
                    "card_recorded",
                    actor=StoreWriterId.CARD_BUILDER.value,
                    subject=card.card_id,
                )
            )

        return self.run_write(StoreWriterId.CARD_BUILDER, StoreOperation.APPEND_CARD, build)
