"""Rebuild of derived projection tables (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §3.2, §10.3).

Projections are lookup aids with no authority. A ``projection_mismatch`` is
repaired here, and only here, by clearing **projection tables only** and
re-deriving them from verified authoritative rows in one transaction. No
statement in this module touches an authoritative table; a static test
enforces that. Startup rebuilds only the mismatched tables while service is
blocked, then reverifies (``EvidenceStore._verify_full``).
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.enums import StoreState
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.store import EvidenceStore, verify_row
from market_intelligence.evidence_store.store_io import (
    PROJECTION_TABLES,
    connect,
    insert_row,
    select_rows,
)
from market_intelligence.evidence_store.verification import expected_projections


def rebuild_projections(store: EvidenceStore) -> None:
    """Operator rebuild of every projection on a serving store."""
    store._require_state(frozenset({StoreState.SERVING}))
    rebuild_projection_tables(store, PROJECTION_TABLES)


def rebuild_projection_tables(store: EvidenceStore, tables: Iterable[str]) -> None:
    """Rebuild only ``tables``. Never on a read-refused store; nothing here
    can clear a finding in an authoritative row."""
    if store.state is StoreState.READ_REFUSED:
        raise StoreRefusal("store_read_refused")
    wanted = set(tables)
    if not wanted <= set(PROJECTION_TABLES):
        raise StoreRefusal("unknown_store_table")
    with store.writer_lock(), connect(store.database_path, read_only=False) as conn:
        parsed: dict[str, dict] = {
            "evidence_items": {},
            "evidence_envelopes": {},
            "evidence_conflicts": {},
            "evidence_bundles": {},
        }
        for table, id_column in (
            ("evidence_items", "item_id"),
            ("evidence_envelopes", "envelope_id"),
            ("evidence_conflicts", "conflict_id"),
            ("evidence_bundles", "bundle_id"),
        ):
            parse = rec.RECORD_TABLES[table][1]
            for row in select_rows(conn, f"SELECT * FROM {table}"):
                verify_row(table, row)
                parsed[table][row[id_column]] = (row, parse(row["record_json"]))
        registries = {}
        for row in select_rows(conn, "SELECT registry_version_id FROM registry_versions"):
            try:
                registries[row["registry_version_id"]] = store.load_registry(
                    conn, row["registry_version_id"]
                )
            except (IntegrityStop, StoreRefusal):
                continue
        expected = expected_projections(parsed, registries)
        conn.execute("BEGIN TRANSACTION")
        try:
            for table in PROJECTION_TABLES:
                if table not in wanted:
                    continue
                conn.execute(f"DELETE FROM {table}")  # projection tables only
                for text in sorted(expected[table]):
                    insert_row(conn, table, json.loads(text))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
