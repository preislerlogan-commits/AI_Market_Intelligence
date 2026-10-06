"""Full read-only integrity verification (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §10.2, §10.3).

``verify_database`` re-parses every authoritative row and reports only IDs
and bounded tokens. Findings in ``STOP_FINDINGS`` put a store into read-refused
mode, ``key_column_mismatch`` and ``bundle_reproduction_mismatch`` included.
Only ``projection_mismatch`` is not a stop, and a store never serves while one
is known: startup rebuilds and reverifies the projection first.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from market_intelligence.evidence.canonical import canonical_sha256
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.contracts import compute_rows_sha256
from market_intelligence.evidence_store.enums import (
    STOP_FINDINGS,
    IntegrityFinding,
    RegistryKind,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.severity import derive_conflict_severity
from market_intelligence.evidence_store.store import (
    EvidenceStore,
    verify_commit_chain,
    verify_row,
)
from market_intelligence.evidence_store.store_io import (
    AUTHORITATIVE_TABLES,
    PROJECTION_TABLES,
    connect,
    select_rows,
)
from market_intelligence.setup_cards.definitions import SetupDefinition
from market_intelligence.setup_cards.supersession import validate_card_history

_KEY_TABLES = {"evidence_items", "evidence_conflicts", "evidence_bundles", "setup_cards"}


@dataclass(frozen=True)
class Finding:
    finding: IntegrityFinding
    table: str
    record_id: str | None = None


@dataclass
class IntegrityReport:
    checked_at_utc: datetime
    high_water_commit_seq: int
    findings: list[Finding] = field(default_factory=list)

    def add(self, finding: IntegrityFinding, table: str, record_id: Any = None) -> None:
        self.findings.append(Finding(finding, table, None if record_id is None else str(record_id)))

    def has_stop(self) -> bool:
        return any(f.finding in STOP_FINDINGS for f in self.findings)

    def first_stop(self) -> IntegrityFinding:
        return next(f.finding for f in self.findings if f.finding in STOP_FINDINGS)

    def tokens(self) -> set[str]:
        return {f.finding.value for f in self.findings}

    def report_sha256(self) -> str:
        return canonical_sha256(
            {
                "high_water_commit_seq": self.high_water_commit_seq,
                "findings": sorted(
                    [f.finding.value, f.table, f.record_id or ""] for f in self.findings
                ),
            }
        )


def _id_column(table: str) -> str | None:
    entry = rec.RECORD_TABLES.get(table)
    return entry[0] if entry else None


def _expected_key_columns(table: str, row: dict[str, Any], parsed: Any) -> dict[str, Any]:
    if table == "evidence_items":
        return rec.item_columns(parsed, row["validated_registry_version_id"])
    if table == "evidence_conflicts":
        derivation = rec.stored_derivation(row)
        return rec.conflict_columns(parsed, derivation, row["validated_registry_version_id"])
    if table == "evidence_bundles":
        from market_intelligence.evidence_store.contracts import BundleBuilderIdentity

        builder = BundleBuilderIdentity(
            builder_version=row["builder_version"],
            builder_code_commit_sha=row["builder_code_commit_sha"],
            builder_configuration_identity=row["builder_configuration_identity"],
        )
        return rec.bundle_columns(parsed, builder, row["visible_through_commit_seq"])
    return rec.card_columns(parsed, row["setup_definition_registry_version_id"])


def _keys_agree(expected: dict[str, Any], row: dict[str, Any]) -> bool:
    for name, value in expected.items():
        stored = row.get(name)
        if isinstance(value, datetime):
            value = value.astimezone(UTC)
        if stored != value:
            return False
    return True


def verify_database(
    path: Path,
    *,
    payload_models: Mapping[str, type[BaseModel]],
    synthetic_setup_definitions_for_tests: Mapping[str, tuple[SetupDefinition, ...]] | None = None,
    reproduce_bundles: bool = True,
) -> IntegrityReport:
    reader = EvidenceStore.offline_reader(synthetic_setup_definitions_for_tests)
    del payload_models  # payload models are checked at write and activation
    with connect(path, read_only=True) as conn:
        tip_row = conn.execute("SELECT coalesce(max(commit_seq), 0) FROM store_commits").fetchone()
        report = IntegrityReport(datetime.now(UTC), int(tip_row[0]) if tip_row else 0)
        try:
            verify_commit_chain(conn)
        except IntegrityStop as stop:
            report.add(stop.finding, "store_commits")
        commits = {r["commit_seq"]: r for r in select_rows(conn, "SELECT * FROM store_commits")}
        per_commit: dict[int, list[str]] = defaultdict(list)
        parsed: dict[str, dict[Any, tuple[dict[str, Any], Any]]] = defaultdict(dict)
        for table in AUTHORITATIVE_TABLES:
            if table == "store_commits":
                continue
            id_column = _id_column(table) or "event_seq"
            for row in select_rows(conn, f"SELECT * FROM {table}"):
                per_commit[row["commit_seq"]].append(row["row_sha256"])
                record_id = row.get(id_column)
                try:
                    verify_row(table, row)
                except IntegrityStop as stop:
                    report.add(stop.finding, table, record_id)
                    continue
                entry = rec.RECORD_TABLES.get(table)
                if entry is None:
                    continue
                try:
                    record = entry[1](row["record_json"])
                except (IntegrityStop, ValueError):
                    report.add(IntegrityFinding.UNKNOWN_RECORD_SCHEMA, table, record_id)
                    continue
                if table in _KEY_TABLES and not _keys_agree(
                    _expected_key_columns(table, row, record), row
                ):
                    report.add(IntegrityFinding.KEY_COLUMN_MISMATCH, table, record_id)
                parsed[table][record_id] = (row, record)
        for seq, commit in commits.items():
            hashes = per_commit.pop(seq, [])
            if (
                len(hashes) != commit["row_count"]
                or compute_rows_sha256(hashes) != commit["rows_sha256"]
            ):
                report.add(IntegrityFinding.COMMIT_CHAIN_MISMATCH, "store_commits", seq)
        for seq in per_commit:
            report.add(IntegrityFinding.COMMIT_CHAIN_MISMATCH, "store_commits", seq)

        _verify_activations(report, parsed["registry_activations"])
        registries: dict[str, Any] = {}
        for version_id in parsed["registry_versions"]:
            try:
                registries[version_id] = reader.load_registry(conn, version_id)
            except (IntegrityStop, StoreRefusal):
                report.add(
                    IntegrityFinding.INVALID_REGISTRY_ACTIVATION, "registry_versions", version_id
                )
        _verify_items(report, parsed["evidence_items"], registries)
        _verify_conflicts(
            report, parsed["evidence_conflicts"], parsed["evidence_items"], registries
        )
        _verify_cards(
            report,
            parsed["setup_cards"],
            parsed["setup_card_chains"],
            parsed["evidence_bundles"],
            registries,
        )
        _verify_projections(report, conn, parsed)
        if reproduce_bundles:
            for bundle_id, (_, bundle) in parsed["evidence_bundles"].items():
                try:
                    rebuilt, n_t, _ = reader.rebuild_bundle(conn, bundle)
                    stored_n = parsed["evidence_bundles"][bundle_id][0][
                        "visible_through_commit_seq"
                    ]
                    if rebuilt.bundle_id != bundle_id or n_t != stored_n:
                        report.add(
                            IntegrityFinding.BUNDLE_REPRODUCTION_MISMATCH,
                            "evidence_bundles",
                            bundle_id,
                        )
                except IntegrityStop as stop:
                    report.add(stop.finding, "evidence_bundles", bundle_id)
                except (StoreRefusal, EvidenceValidationError):
                    report.add(
                        IntegrityFinding.BUNDLE_REPRODUCTION_MISMATCH, "evidence_bundles", bundle_id
                    )
    return report


def _verify_activations(report: IntegrityReport, activations: dict[Any, tuple[dict, Any]]) -> None:
    by_kind: dict[str, list[tuple[dict, Any]]] = defaultdict(list)
    for row, activation in activations.values():
        by_kind[row["registry_kind"]].append((row, activation))
    for rows in by_kind.values():
        roots = [a for _, a in rows if a.supersedes_activation_id is None]
        successors = [a.supersedes_activation_id for _, a in rows if a.supersedes_activation_id]
        ids = {a.activation_id for _, a in rows}
        seq = {a.activation_id: r["commit_seq"] for r, a in rows}
        if len(roots) != 1 or len(successors) != len(set(successors)):
            report.add(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY, "registry_activations")
        for row, activation in rows:
            prior = activation.supersedes_activation_id
            if prior is not None and (prior not in ids or seq[prior] >= row["commit_seq"]):
                report.add(
                    IntegrityFinding.INVALID_REGISTRY_ACTIVATION,
                    "registry_activations",
                    activation.activation_id,
                )
            if activation.activated_at_utc != row["recorded_at_utc"]:
                report.add(
                    IntegrityFinding.INVALID_REGISTRY_ACTIVATION,
                    "registry_activations",
                    activation.activation_id,
                )


def _verify_items(report: IntegrityReport, items: dict, registries: dict[str, Any]) -> None:
    for item_id, (row, item) in items.items():
        for parent in item.provenance.parent_evidence_ids:
            parent_entry = items.get(parent)
            if parent_entry is None or parent_entry[0]["commit_seq"] > row["commit_seq"]:
                report.add(IntegrityFinding.MISSING_PARENT, "evidence_items", item_id)
        registry = registries.get(row["validated_registry_version_id"])
        if registry is None:
            report.add(IntegrityFinding.INVALID_REGISTRY_ACTIVATION, "evidence_items", item_id)
            continue
        try:
            if is_holdout_restricted(item, registry):
                report.add(IntegrityFinding.PERSISTED_HOLDOUT_VIOLATION, "evidence_items", item_id)
        except EvidenceValidationError:
            report.add(IntegrityFinding.UNKNOWN_RECORD_SCHEMA, "evidence_items", item_id)


def _verify_conflicts(
    report: IntegrityReport, conflicts: dict, items: dict, registries: dict
) -> None:
    by_chain: dict[str, list[tuple[dict, Any]]] = defaultdict(list)
    for row, conflict in conflicts.values():
        by_chain[row["conflict_chain_key"]].append((row, conflict))
        registry = registries.get(row["severity_registry_version_id"])
        involved = [items[i][1] for i in conflict.involved_item_ids if i in items]
        if registry is None or len(involved) != len(conflict.involved_item_ids):
            report.add(
                IntegrityFinding.CONFLICT_SEVERITY_IRREPRODUCIBLE,
                "evidence_conflicts",
                conflict.conflict_id,
            )
            continue
        recomputed = derive_conflict_severity(conflict, involved, registry)
        stored = rec.stored_derivation(row)
        if recomputed.model_dump(mode="json") != stored.model_dump(mode="json"):
            report.add(
                IntegrityFinding.CONFLICT_SEVERITY_IRREPRODUCIBLE,
                "evidence_conflicts",
                conflict.conflict_id,
            )
    for chain in by_chain.values():
        ids = {c.conflict_id: r["commit_seq"] for r, c in chain}
        roots = [c for _, c in chain if c.resolution is None]
        successors = [c.resolution.supersedes_conflict_id for _, c in chain if c.resolution]
        bad = len(roots) != 1 or len(successors) != len(set(successors))
        for row, conflict in chain:
            if conflict.resolution is not None:
                prior = conflict.resolution.supersedes_conflict_id
                if prior not in ids or ids[prior] >= row["commit_seq"]:
                    bad = True
        if bad:
            report.add(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY, "evidence_conflicts")


def _verify_cards(
    report: IntegrityReport, cards: dict, chains: dict, bundles: dict, registries: dict
) -> None:
    by_chain: dict[str, list[tuple[dict, Any]]] = defaultdict(list)
    for row, card in cards.values():
        by_chain[row["chain_key_sha256"]].append((row, card))
        if card.decision_context_bundle_id not in bundles:
            report.add(IntegrityFinding.CARD_BUNDLE_MISSING, "setup_cards", card.card_id)
        if row["setup_definition_registry_version_id"] not in registries:
            report.add(IntegrityFinding.CARD_REGISTRY_VERSION_MISSING, "setup_cards", card.card_id)
    chain_roots = {}
    for key, (row, chain) in chains.items():
        if (
            canonical_sha256(chain["chain_key"]) != key
            or chain["root_card_id"] != row["root_card_id"]
        ):
            report.add(IntegrityFinding.IDENTITY_MISMATCH, "setup_card_chains", key)
        chain_roots[key] = row
    for key, chain in by_chain.items():
        roots = [c for _, c in chain if c.card_revision.supersedes_card_id is None]
        root_row = chain_roots.get(key)
        bad = len(roots) != 1 or root_row is None or root_row["root_card_id"] != roots[0].card_id
        seqs = {c.card_id: r["commit_seq"] for r, c in chain}
        for row, card in chain:
            prior = card.card_revision.supersedes_card_id
            if prior is not None and (prior not in seqs or seqs[prior] >= row["commit_seq"]):
                bad = True
        if not bad:
            try:
                validate_card_history([c for _, c in chain])
            except EvidenceValidationError:
                bad = True
        if bad:
            report.add(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY, "setup_cards")
    for key in chain_roots:
        if key not in by_chain:
            report.add(IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY, "setup_card_chains")


def expected_projections(parsed: dict, registries: dict[str, Any]) -> dict[str, set[str]]:
    """Every projection row derivable from authoritative rows, as canonical JSON."""
    expected: dict[str, set[str]] = {table: set() for table in PROJECTION_TABLES}

    def add(table: str, columns: dict[str, Any]) -> None:
        expected[table].add(json.dumps(columns, sort_keys=True, default=str))

    items = {k: v[1] for k, v in parsed["evidence_items"].items()}
    for row, item in parsed["evidence_items"].values():
        for table, columns in rec.item_projection_rows(item, row["commit_seq"]):
            add(table, columns)
    for row, header in parsed["evidence_envelopes"].values():
        for item_id in header["items"]:
            add("evidence_envelope_items", {"envelope_id": row["envelope_id"], "item_id": item_id})
    for _, conflict in parsed["evidence_conflicts"].values():
        for item_id in conflict.involved_item_ids:
            add(
                "evidence_conflict_items", {"conflict_id": conflict.conflict_id, "item_id": item_id}
            )
    for _, bundle in parsed["evidence_bundles"].values():
        registry = registries.get(bundle.registry_version_id)
        if registry is None:
            continue
        for table, columns in rec.bundle_projection_rows(bundle, registry, items):
            add(table, columns)
    return expected


def _verify_projections(report: IntegrityReport, conn: Any, parsed: dict) -> None:
    reader = EvidenceStore.offline_reader()
    registries = {}
    for version_id in parsed["registry_versions"]:
        try:
            registries[version_id] = reader.load_registry(conn, version_id)
        except (IntegrityStop, StoreRefusal):
            continue
    expected = expected_projections(parsed, registries)
    for table in PROJECTION_TABLES:
        actual = {
            json.dumps(row, sort_keys=True, default=str)
            for row in select_rows(conn, f"SELECT * FROM {table}")
        }
        if actual != expected[table]:
            report.add(IntegrityFinding.PROJECTION_MISMATCH, table)


def kind_of(version_id: str) -> RegistryKind:
    return (
        RegistryKind.EVIDENCE_REGISTRY
        if version_id.startswith("evr1_")
        else RegistryKind.SETUP_DEFINITION_REGISTRY
    )
