"""Registry registration and activation (docs/REGISTRY_LOADING_DESIGN.md §6-§9, §12).

Offline core. Nothing here registers or activates anything in the real
database, and no real registry file exists.

Every operation is planned first (dry run) and executed only with the second
confirmation token derived from the plan's canonical digest. Execution
re-derives the plan inside the write transaction, so a token for a stale plan
cannot execute.

Activation refuses:
- an unregistered version, a wrong reason, a non-tip predecessor, a reused
  effective time, or a backdated effective time;
- any change to a payload schema's ``spy_price_content`` against the
  **complete** prior activation history (true->false and false->true);
- a payload schema with no code model, or dropping a schema or freshness
  policy that stored evidence references;
- a non-empty setup-definition registry (structurally impossible);
- a machine-decision-affecting activation that is not immediate, or that
  would change any unresolved conflict's matched requirements,
  ``involves_required_item`` or severity
  (``unresolved_conflict_requires_contract_amendment``).
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from market_intelligence.evidence.canonical import canonical_sha256
from market_intelligence.evidence.enums import ConflictStatus
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.contracts import (
    RegistryActivationContent,
    RegistryVersionRecord,
    seal_activation,
)
from market_intelligence.evidence_store.enums import (
    ActivationReason,
    IntegrityFinding,
    RegistryKind,
    SeverityRuleVersion,
    StoreAuditEventType,
    StoreOperation,
    StoreWriterId,
)
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.evidence_store.registry_files import (
    DEFAULT_REGISTRIES_ROOT,
    LoadedRegistry,
    load_registry_file,
)
from market_intelligence.evidence_store.severity import (
    derive_conflict_severity,
    machine_decision_signature,
)
from market_intelligence.evidence_store.store import EvidenceStore, Txn
from market_intelligence.evidence_store.store_io import select_one, select_rows

SourceVerifier = Callable[[str, str], bytes]
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SOURCE_PATH = re.compile(
    r"^registries/(evidence|setup_definitions)/[a-z0-9][a-z0-9._-]{0,63}\.json$"
)


def git_source_verifier(source_path: str, source_commit_sha: str) -> bytes:
    """The committed bytes of ``source_path`` at ``source_commit_sha`` (read-only git)."""
    import subprocess

    from market_intelligence.config.settings import REPO_ROOT

    if not _COMMIT_SHA.fullmatch(source_commit_sha) or not _SOURCE_PATH.fullmatch(source_path):
        raise StoreRefusal("registry_not_committed")
    try:
        result = subprocess.run(
            ["git", "show", f"{source_commit_sha}:{source_path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        raise StoreRefusal("registry_not_committed") from None
    return result.stdout


def confirmation_token(digest: str) -> str:
    return "cfm_" + canonical_sha256({"confirm": digest})[:32]


# --- Registration --------------------------------------------------------------------------------


@dataclass(frozen=True)
class RegistrationPlan:
    record: RegistryVersionRecord
    digest: str
    confirmation_token: str

    def dry_run(self) -> dict[str, str]:
        """Sanitized output: identities, labels, hashes and the token only."""
        return {
            "operation": "register",
            "registry_kind": self.record.registry_kind.value,
            "registry_version_id": self.record.registry_version_id,
            "registry_label": self.record.registry_label,
            "source_path": self.record.source_path,
            "source_file_sha256": self.record.source_file_sha256,
            "source_commit_sha": self.record.source_commit_sha,
            "confirmation_token": self.confirmation_token,
        }


def _registration_digest(record: RegistryVersionRecord) -> str:
    return canonical_sha256(
        {
            "operation": "register",
            "registry_kind": record.registry_kind.value,
            "registry_version_id": record.registry_version_id,
            "source_path": record.source_path,
            "source_file_sha256": record.source_file_sha256,
            "source_commit_sha": record.source_commit_sha,
        }
    )


def plan_registration(
    store: EvidenceStore,
    kind: RegistryKind,
    path: Path,
    *,
    source_commit_sha: str,
    registries_root: Path = DEFAULT_REGISTRIES_ROOT,
    source_verifier: SourceVerifier = git_source_verifier,
) -> RegistrationPlan:
    if not _COMMIT_SHA.fullmatch(source_commit_sha):
        # Validated before any verifier runs: an unchecked value could reach
        # ``git`` as an option rather than a revision.
        raise StoreRefusal("registry_not_committed")
    loaded = load_registry_file(kind, path, registries_root=registries_root)
    committed = source_verifier(loaded.source_path, source_commit_sha)
    import hashlib

    if hashlib.sha256(committed).hexdigest() != loaded.source_file_sha256:
        raise StoreRefusal("registry_not_committed")
    _check_code_compatibility(store, loaded)
    record = RegistryVersionRecord(
        registry_version_id=loaded.registry_version_id,
        registry_kind=kind,
        registry_label=loaded.registry_label,
        file_format=loaded.file_format,  # type: ignore[arg-type]
        content_json=loaded.content_json,
        content_sha256=loaded.registry_version_id[5:],
        source_path=loaded.source_path,
        source_file_sha256=loaded.source_file_sha256,
        source_commit_sha=source_commit_sha,
    )
    digest = _registration_digest(record)
    return RegistrationPlan(record, digest, confirmation_token(digest))


def _check_code_compatibility(store: EvidenceStore, loaded: LoadedRegistry) -> None:
    if loaded.evidence_registry is not None:
        for schema in loaded.evidence_registry.payload_schemas:
            if schema.payload_schema_id not in store.payload_models:
                raise StoreRefusal("payload_model_missing")
    if loaded.setup_registry is not None and loaded.setup_registry.definitions:
        raise StoreRefusal("setup_definitions_not_authorized")  # unreachable: max_length=0


def execute_registration(store: EvidenceStore, plan: RegistrationPlan, token: str) -> None:
    if token != plan.confirmation_token or _registration_digest(plan.record) != plan.digest:
        raise StoreRefusal("confirmation_token_mismatch")
    record = plan.record

    def build(txn: Txn) -> None:
        conn = txn.connection
        existing = select_one(
            conn,
            "SELECT record_json FROM registry_versions WHERE registry_version_id = ?",
            [record.registry_version_id],
        )
        if existing is not None:
            stored = rec.parse_registry_version(existing["record_json"])
            if stored.content_json != record.content_json:
                store._stop(IntegrityFinding.IMPOSSIBLE_DUPLICATE_IDENTITY)
            txn.audit(
                store._event(
                    StoreAuditEventType.APPEND_DUPLICATE_IGNORED,
                    "append_duplicate_ignored",
                    actor=StoreWriterId.REGISTRY_OPERATOR.value,
                    subject=record.registry_version_id,
                )
            )
            return
        reused = select_one(
            conn,
            "SELECT registry_version_id FROM registry_versions WHERE "
            "registry_kind = ? AND registry_label = ?",
            [record.registry_kind.value, record.registry_label],
        )
        if reused is not None:
            raise StoreRefusal("registry_label_reused")
        txn.add("registry_versions", rec.registry_version_columns(record))
        txn.audit(
            store._event(
                StoreAuditEventType.REGISTRY_VERSION_REGISTERED,
                "registry_version_registered",
                actor=StoreWriterId.REGISTRY_OPERATOR.value,
                subject=record.registry_version_id,
            )
        )

    store.run_write(
        StoreWriterId.REGISTRY_OPERATOR, StoreOperation.REGISTER_REGISTRY_VERSION, build
    )


# --- Activation ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ActivationRequest:
    registry_kind: RegistryKind
    registry_version_id: str
    activation_reason: ActivationReason
    effective_from_utc: datetime | None  # None means "immediately, at the commit"
    authorization_ref: str


@dataclass
class ActivationEvaluation:
    effective_from_utc: datetime
    supersedes_activation_id: str | None
    machine_decision_triggered: bool
    affected_unresolved_conflicts: int
    grant_changes: list[str] = field(default_factory=list)
    refusal: str | None = None


@dataclass(frozen=True)
class ActivationPlan:
    request: ActivationRequest
    supersedes_activation_id: str | None
    evaluation: ActivationEvaluation
    digest: str
    confirmation_token: str

    def dry_run(self) -> dict[str, Any]:
        """Sanitized: identities, times, a count and tokens; no conflict or item ID."""
        return {
            "operation": "activate",
            "registry_kind": self.request.registry_kind.value,
            "registry_version_id": self.request.registry_version_id,
            "activation_reason": self.request.activation_reason.value,
            "effective_from": "immediate"
            if self.request.effective_from_utc is None
            else self.request.effective_from_utc.isoformat(),
            "machine_decision_check_triggered": self.evaluation.machine_decision_triggered,
            "affected_unresolved_conflicts": self.evaluation.affected_unresolved_conflicts,
            "grant_changes": list(self.evaluation.grant_changes),
            "would_refuse": self.evaluation.refusal,
            "confirmation_token": self.confirmation_token,
        }


def _activation_digest(request: ActivationRequest, supersedes: str | None) -> str:
    return canonical_sha256(
        {
            "operation": "activate",
            "registry_kind": request.registry_kind.value,
            "registry_version_id": request.registry_version_id,
            "activation_reason": request.activation_reason.value,
            "effective_from": "immediate"
            if request.effective_from_utc is None
            else request.effective_from_utc.isoformat(),
            "authorization_ref": request.authorization_ref,
            "supersedes_activation_id": supersedes,
        }
    )


def _activation_history(
    conn: duckdb.DuckDBPyConnection, kind: RegistryKind
) -> list[dict[str, Any]]:
    return select_rows(
        conn,
        "SELECT activation_id, registry_version_id, supersedes_activation_id, commit_seq "
        "FROM registry_activations WHERE registry_kind = ? ORDER BY commit_seq",
        [kind.value],
    )


def _tip_activation(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    superseded = {r["supersedes_activation_id"] for r in history if r["supersedes_activation_id"]}
    tips = [r for r in history if r["activation_id"] not in superseded]
    if len(tips) > 1:
        raise StoreRefusal("activation_history_ambiguous")
    return tips[0] if tips else None


def _grant_changes(current: EvidenceRegistry | None, proposed: EvidenceRegistry) -> list[str]:
    def grants(registry: EvidenceRegistry | None) -> dict[str, str]:
        if registry is None:
            return {}
        return {
            g.consumer_id.value: json.dumps(g.model_dump(mode="json"), sort_keys=True)
            for g in registry.consumer_grants
        }

    before, after = grants(current), grants(proposed)
    return sorted(
        consumer
        for consumer in set(before) | set(after)
        if before.get(consumer) != after.get(consumer)
    )


def evaluate_activation(
    store: EvidenceStore, conn: duckdb.DuckDBPyConnection, request: ActivationRequest, now: datetime
) -> tuple[str | None, ActivationEvaluation]:
    """Every activation check, read-only. Returns the expected predecessor and
    the evaluation (``refusal`` set if the activation must be refused)."""
    kind = request.registry_kind
    history = _activation_history(conn, kind)
    tip = _tip_activation(history)
    supersedes = tip["activation_id"] if tip else None
    effective = request.effective_from_utc or now
    evaluation = ActivationEvaluation(effective, supersedes, False, 0)

    def refuse(reason: str) -> tuple[str | None, ActivationEvaluation]:
        evaluation.refusal = reason
        return supersedes, evaluation

    row = select_one(
        conn,
        "SELECT registry_kind FROM registry_versions WHERE registry_version_id = ?",
        [request.registry_version_id],
    )
    if row is None or row["registry_kind"] != kind.value:
        return refuse("registry_version_not_registered")
    proposed = store.load_registry(conn, request.registry_version_id)
    previously_active = {r["registry_version_id"] for r in history}
    reason = request.activation_reason
    if tip is None and reason is not ActivationReason.INITIAL_ACTIVATION:
        return refuse("activation_reason_mismatch")
    if tip is not None:
        if reason is ActivationReason.INITIAL_ACTIVATION:
            return refuse("activation_reason_mismatch")
        if tip["registry_version_id"] == request.registry_version_id:
            return refuse("activation_no_change")
        if (
            reason is ActivationReason.ROLLBACK
            and request.registry_version_id not in previously_active
        ):
            return refuse("activation_reason_mismatch")
        if (
            reason is ActivationReason.VERSION_UPGRADE
            and request.registry_version_id in previously_active
        ):
            return refuse("activation_reason_mismatch")
    if kind is RegistryKind.SETUP_DEFINITION_REGISTRY:
        if proposed.definitions:  # unreachable: the model is structurally empty
            return refuse("setup_definitions_not_authorized")
    else:
        refusal = _evidence_activation_checks(
            store, conn, proposed, history, evaluation, request, now
        )
        if refusal is not None:
            return refuse(refusal)
    if evaluation.effective_from_utc < now:
        return refuse("activation_backdated")
    taken = select_one(
        conn,
        "SELECT activation_id FROM registry_activations WHERE "
        "registry_kind = ? AND effective_from_utc = ?",
        [kind.value, evaluation.effective_from_utc],
    )
    if taken is not None:
        return refuse("activation_effective_time_taken")
    pending = select_one(
        conn,
        "SELECT count(*) AS n FROM registry_activations "
        "WHERE registry_kind = ? AND effective_from_utc > ?",
        [kind.value, now],
    )
    if pending and pending["n"]:
        # A pending future activation would later replace whatever this one
        # activates, a transition the machine-decision and unresolved-conflict
        # checks above never saw. One pending activation at a time.
        return refuse("activation_pending")
    return supersedes, evaluation


def _evidence_activation_checks(
    store: EvidenceStore,
    conn: duckdb.DuckDBPyConnection,
    proposed: EvidenceRegistry,
    history: list[dict[str, Any]],
    evaluation: ActivationEvaluation,
    request: ActivationRequest,
    now: datetime,
) -> str | None:
    for schema in proposed.payload_schemas:
        if schema.payload_schema_id not in store.payload_models:
            return "payload_model_missing"
    declared = {s.payload_schema_id: s.spy_price_content for s in proposed.payload_schemas}
    for version_id in sorted({r["registry_version_id"] for r in history}):
        prior = store.load_registry(conn, version_id)
        for schema in prior.payload_schemas:
            new_value = declared.get(schema.payload_schema_id)
            if new_value is None:
                continue
            if schema.spy_price_content and not new_value:
                return "holdout_declaration_weakened"
            if not schema.spy_price_content and new_value:
                return "holdout_declaration_changed"
    stored_schemas: set[str] = set()
    stored_policies: set[str] = set()
    for row in select_rows(conn, "SELECT record_json FROM evidence_items"):
        record = json.loads(row["record_json"])
        stored_schemas.add(record["payload_schema_id"])
        stored_policies.add(record["freshness_policy_id"])
    if not stored_schemas <= set(declared):
        return "registry_drops_referenced_schema"
    if not stored_policies <= {p.policy_id for p in proposed.freshness_policies}:
        return "registry_drops_referenced_policy"
    current_id = store.in_force(conn, RegistryKind.EVIDENCE_REGISTRY, now)
    current = store.load_registry(conn, current_id) if current_id else None
    evaluation.grant_changes = _grant_changes(current, proposed)
    current_signature = (
        machine_decision_signature(current)
        if current
        else machine_decision_signature(proposed.model_copy(update={"consumer_grants": []}))
    )
    triggered = machine_decision_signature(proposed) != current_signature
    evaluation.machine_decision_triggered = triggered
    if not triggered:
        return None
    if request.effective_from_utc is not None:
        return "machine_decision_activation_must_take_effect_immediately"
    evaluation.effective_from_utc = now
    affected = _unresolved_conflicts_changed(store, conn, proposed)
    evaluation.affected_unresolved_conflicts = affected
    if affected:
        return "unresolved_conflict_requires_contract_amendment"
    return None


def _unresolved_conflicts_changed(
    store: EvidenceStore, conn: duckdb.DuckDBPyConnection, proposed: EvidenceRegistry
) -> int:
    """How many unresolved conflict tips would get a different derivation."""
    rows = select_rows(conn, "SELECT * FROM evidence_conflicts")
    superseded = {r["supersedes_conflict_id"] for r in rows if r["supersedes_conflict_id"]}
    affected = 0
    for row in rows:
        if row["conflict_id"] in superseded or row["status"] != ConflictStatus.UNRESOLVED.value:
            continue
        conflict = rec.parse_conflict(row["record_json"])
        involved = list(store._load_items(conn, set(conflict.involved_item_ids)).values())
        recomputed = derive_conflict_severity(
            conflict,
            involved,
            proposed,
            rule_version=SeverityRuleVersion(row["severity_rule_version"]),
        )
        stored = rec.stored_derivation(row)
        if (
            recomputed.matched_requirement_ids != stored.matched_requirement_ids
            or recomputed.involves_required_item != stored.involves_required_item
            or recomputed.severity != stored.severity
        ):
            affected += 1
    return affected


def plan_activation(store: EvidenceStore, request: ActivationRequest) -> ActivationPlan:
    with store.read_connection() as conn:
        supersedes, evaluation = evaluate_activation(store, conn, request, store.clock())
    digest = _activation_digest(request, supersedes)
    return ActivationPlan(request, supersedes, evaluation, digest, confirmation_token(digest))


def execute_activation(store: EvidenceStore, plan: ActivationPlan, token: str) -> None:
    if token != plan.confirmation_token:
        raise StoreRefusal("confirmation_token_mismatch")
    request = plan.request

    def build(txn: Txn) -> None:
        supersedes, evaluation = evaluate_activation(store, txn.connection, request, txn.now)
        if _activation_digest(request, supersedes) != plan.digest:
            raise StoreRefusal("confirmation_token_mismatch")
        if evaluation.refusal is not None:
            raise StoreRefusal(evaluation.refusal)
        activation = seal_activation(
            RegistryActivationContent(
                registry_kind=request.registry_kind,
                registry_version_id=request.registry_version_id,
                effective_from_utc=evaluation.effective_from_utc,
                activation_reason=request.activation_reason,
                supersedes_activation_id=supersedes,
                authorization_ref=request.authorization_ref,
            ),
            txn.now,
        )
        txn.add("registry_activations", rec.activation_columns(activation))
        txn.audit(
            store._event(
                StoreAuditEventType.REGISTRY_ACTIVATED,
                "registry_activated",
                actor=StoreWriterId.REGISTRY_OPERATOR.value,
                subject=activation.activation_id,
            )
        )

    store.run_write(StoreWriterId.REGISTRY_OPERATOR, StoreOperation.APPEND_ACTIVATION, build)
    store._registry_cache.clear()
