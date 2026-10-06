"""Low-level DuckDB access for the evidence store: insert and select only.

There is deliberately no update, delete, replace or upsert helper here. Every
authoritative write goes through ``insert_row``, which computes the row hash
over every other column. Timestamps are stored as naive UTC ``TIMESTAMP``
values and read back as aware UTC, so a row hash recomputed from a read row
equals the one written.
"""

from __future__ import annotations

import functools
import hashlib
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb

from market_intelligence.config.settings import REPO_ROOT
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence_store.contracts import compute_row_sha256
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.storage.database import DATABASE_FILENAME

REAL_DATA_DIR = (REPO_ROOT / "data").resolve()
REAL_DATABASE_PATH = REAL_DATA_DIR / DATABASE_FILENAME

AUTHORITATIVE_TABLES = (
    "store_instance",
    "store_commits",
    "registry_versions",
    "registry_activations",
    "evidence_items",
    "evidence_envelopes",
    "evidence_conflicts",
    "evidence_bundles",
    "setup_cards",
    "setup_card_chains",
    "store_recovery_events",
    "store_checkpoint_key_rotations",
    "store_audit_events",
)
PROJECTION_TABLES = (
    "evidence_item_subjects",
    "evidence_item_parents",
    "evidence_item_revisions",
    "evidence_envelope_items",
    "evidence_conflict_items",
    "evidence_bundle_entries",
    "evidence_bundle_requirement_outcomes",
)


@functools.lru_cache(maxsize=1)
def real_data_directories() -> tuple[Path, ...]:
    """The repository's ``data/`` directory plus the configured project data
    directory (``PROJECT_DATA_PATH``), resolved at call time, never at import.
    Both may hold the real project database."""
    directories = [REAL_DATA_DIR]
    try:
        from market_intelligence.config.settings import Settings

        directories.append(Settings().project_data_path.resolve())
    except Exception:  # noqa: BLE001 - an unreadable configuration adds nothing
        pass
    return tuple(dict.fromkeys(directories))


def refuse_real_database_path(path: Path) -> Path:
    """This offline core never opens the real project database or anything in
    a real project data directory (repository ``data/`` or the configured one)."""
    resolved = Path(path).resolve()
    for directory in real_data_directories():
        if resolved == directory or directory in resolved.parents:
            raise StoreRefusal("real_database_refused")
    return resolved


def to_db(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def from_db(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value


def canonical_text(obj: Any) -> str:
    return canonical_json_bytes(obj).decode("utf-8")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def connect(path: Path, *, read_only: bool) -> Iterator[duckdb.DuckDBPyConnection]:
    """A short-lived connection. Readers are always read-only."""
    refuse_real_database_path(path)
    try:
        connection = duckdb.connect(str(path), read_only=read_only)
    except duckdb.Error:
        raise StoreRefusal("store_unavailable") from None
    try:
        yield connection
    finally:
        connection.close()


def insert_row(connection: duckdb.DuckDBPyConnection, table: str, columns: dict[str, Any]) -> str:
    """Insert one row; returns its row hash. ``table`` and column names come
    only from this package's fixed schema, never from callers' data."""
    if table not in AUTHORITATIVE_TABLES and table not in PROJECTION_TABLES:
        raise StoreRefusal("unknown_store_table")
    values = dict(columns)
    if table in AUTHORITATIVE_TABLES and table != "store_commits":
        values["row_sha256"] = compute_row_sha256(values)
    names = list(values)
    placeholders = ", ".join("?" for _ in names)
    sql = f"INSERT INTO {table} ({', '.join(names)}) VALUES ({placeholders})"
    connection.execute(sql, [to_db(values[name]) for name in names])
    return values.get("row_sha256", "")


def select_rows(
    connection: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()
) -> list[dict[str, Any]]:
    cursor = connection.execute(sql, [to_db(p) for p in params])
    names = [d[0] for d in cursor.description]
    return [{n: from_db(v) for n, v in zip(names, row, strict=True)} for row in cursor.fetchall()]


def select_one(
    connection: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any] = ()
) -> dict[str, Any] | None:
    rows = select_rows(connection, sql, params)
    if len(rows) > 1:
        raise StoreRefusal("unexpected_multiple_rows")
    return rows[0] if rows else None


def table_exists(connection: duckdb.DuckDBPyConnection, table: str) -> bool:
    row = connection.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [table]
    ).fetchone()
    return bool(row and row[0])


def as_date(value: Any) -> date:
    return value if isinstance(value, date) and not isinstance(value, datetime) else value.date()
