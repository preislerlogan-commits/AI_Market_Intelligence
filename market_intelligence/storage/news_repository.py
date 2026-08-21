"""News-article storage: persists normalized ``NewsItem`` objects into DuckDB.

This module never makes network requests and never accepts, stores, or logs
credentials. It writes only to the ``news_articles`` table (migration
``0004``, see ``market_intelligence/storage/migrations/``) and the existing
``ingestion_runs`` bookkeeping table -- never provider sentiment, impact,
direction, confidence, model output, recommendations, option-contract data,
orders, request headers, or raw API responses. It assumes the database has
already been brought up to at least migration ``0004`` (e.g. via
``DuckDBManager.initialize()``); this class only writes rows, it never
applies migrations itself.

Every batch -- and the final ``succeeded`` ``ingestion_runs`` status update
-- is written inside a single DuckDB transaction: if any item in the batch
cannot be safely stored (invalid input, or an existing provider article
whose stable content conflicts with the incoming values), or the final
``succeeded`` status update itself fails, nothing in that batch is
persisted, and the corresponding ``ingestion_runs`` row is separately
recorded as ``failed`` with a sanitized error category -- never a raw
exception message, and never article text, URLs, or credentials. Stored
article changes are never left associated with a ``running`` or ``failed``
run. If recording the ``failed`` status itself also fails, a sanitized
``NewsStorageError`` is raised.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_news import (
    AlpacaNewsInvalidInputError,
    NewsItem,
    normalize_timestamp,
)
from market_intelligence.storage.database import DuckDBManager

CODE_VERSION = "news_repository_v1"
DEFAULT_DATASET_NAME = "news"


class NewsStorageError(RuntimeError):
    """Sanitized storage failure.

    Never includes article text, URLs, database internals, or credentials.
    """


class NewsStorageValidationError(NewsStorageError):
    """Raised when supplied items fail validation before any database write.

    No ``ingestion_runs`` row is created for this failure mode -- it
    indicates a caller/programming error (malformed input), not a data or
    infrastructure failure to record for audit purposes.
    """


class NewsStorageConflictError(NewsStorageError):
    """Raised internally when an incoming article conflicts with stored content.

    Caught by ``store_news_items`` to trigger a full-batch rollback; never
    propagated to callers directly.
    """


@dataclass(frozen=True)
class NewsStorageResult:
    """Sanitized result of one ``store_news_items`` call.

    Never includes article text, URLs, database internals, or credentials.
    """

    received: int
    inserted: int
    updated: int
    failed: int
    ingestion_run_id: str
    ingestion_run_status: str


def _require_non_blank_str(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise NewsStorageValidationError(f"Invalid items: {field_name} must be a non-blank string.")


def _require_rfc3339_timestamp(value: object, *, field_name: str, optional: bool) -> None:
    """Require ``value`` to be a strict RFC3339 timestamp (explicit Z/offset).

    Delegates to ``AlpacaNewsClient``'s own RFC3339 validator so both layers
    reject naive, date-only, malformed, blank, and non-string timestamps
    identically. When ``optional`` is True, ``None`` is accepted; otherwise
    ``None`` is rejected. Never echoes the raw input value.
    """
    if value is None:
        if optional:
            return
        raise NewsStorageValidationError(
            f"Invalid items: {field_name} must be a valid RFC3339 timestamp."
        )
    if not isinstance(value, str):
        raise NewsStorageValidationError(
            f"Invalid items: {field_name} must be a valid RFC3339 timestamp."
        )
    try:
        normalize_timestamp(value, field_name=field_name)
    except AlpacaNewsInvalidInputError as exc:
        raise NewsStorageValidationError(f"Invalid items: {exc}") from None


def _validate_items(items: Sequence[NewsItem], *, provider: str) -> None:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise NewsStorageValidationError("Invalid items: expected a sequence of NewsItem.")

    for item in items:
        if not isinstance(item, NewsItem):
            raise NewsStorageValidationError("Invalid items: every item must be a NewsItem.")
        if item.provider != provider:
            raise NewsStorageValidationError(
                "Invalid items: every item's provider must match the requested provider."
            )
        _require_non_blank_str(item.provider, field_name="provider")
        _require_non_blank_str(item.provider_article_id, field_name="provider_article_id")
        _require_non_blank_str(item.headline, field_name="headline")
        _require_non_blank_str(item.source, field_name="source")
        _require_non_blank_str(item.url, field_name="url")
        _require_rfc3339_timestamp(item.created_at, field_name="created_at", optional=True)
        _require_rfc3339_timestamp(item.updated_at, field_name="updated_at", optional=True)
        _require_rfc3339_timestamp(item.retrieved_at, field_name="retrieved_at", optional=False)
        if item.summary is not None and not isinstance(item.summary, str):
            raise NewsStorageValidationError("Invalid items: summary must be a string or None.")
        if not isinstance(item.related_symbols, tuple) or not all(
            isinstance(symbol, str) and symbol.strip() for symbol in item.related_symbols
        ):
            raise NewsStorageValidationError(
                "Invalid items: related_symbols must be a tuple of non-blank strings."
            )


def _sorted_related_symbols(item: NewsItem) -> list[str]:
    """Deterministic, deduplicated, order-independent symbol representation."""
    return sorted({symbol.strip().upper() for symbol in item.related_symbols})


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None)


def _parse_timestamp(value: str) -> datetime:
    """Parse an already-validated RFC3339 timestamp into a naive UTC datetime.

    Routes through ``normalize_timestamp`` (rather than a bare
    ``fromisoformat``) so any explicit-offset form accepted by validation --
    including a lowercase ``z`` suffix -- is normalized identically before
    parsing.
    """
    normalized = normalize_timestamp(value, field_name="timestamp")
    return _to_naive_utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))


def _parse_optional_timestamp(value: str | None) -> datetime | None:
    return None if value is None else _parse_timestamp(value)


def _now_naive_utc() -> datetime:
    return _to_naive_utc(datetime.now(UTC))


class NewsArticleRepository:
    """Persists normalized ``NewsItem`` objects into the local ``news_articles`` table."""

    def __init__(
        self,
        settings: Settings | None = None,
        database_path: Path | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._manager = DuckDBManager(settings=self._settings, database_path=database_path)

    @property
    def database_path(self) -> Path:
        return self._manager.database_path

    def store_news_items(
        self,
        items: Sequence[NewsItem],
        *,
        provider: str = "alpaca",
        dataset_name: str = DEFAULT_DATASET_NAME,
    ) -> NewsStorageResult:
        """Store a batch of already-normalized ``NewsItem`` objects.

        Validates every item before any database write (raising
        ``NewsStorageValidationError`` and writing nothing if validation
        fails). Otherwise records a ``running`` ``ingestion_runs`` row, then
        writes the whole batch -- plus the final ``succeeded``
        ``ingestion_runs`` status update -- inside one DuckDB transaction:
        an unseen article is inserted; an already-known article with
        matching stable content (headline, source, url, publication time)
        has its mutable fields (summary, updated_at, related_symbols) and
        provenance (retrieved_at, last_seen_at, ingestion_run_id)
        refreshed; an already-known article whose stable content conflicts
        aborts the entire batch. If any article write, or the final
        ``succeeded`` status update itself, fails, the whole transaction is
        rolled back (no article changes are left associated with the run)
        and the run is separately recorded as ``failed`` with a sanitized
        error category, so stored article changes are never left attached
        to a ``running`` or ``failed`` run. Never raises for a conflict or
        storage failure -- both are reported truthfully via the returned
        ``NewsStorageResult`` -- unless recording the ``failed`` status
        itself also fails, in which case a sanitized ``NewsStorageError``
        is raised.
        """
        received = len(items)
        _validate_items(items, provider=provider)
        _require_non_blank_str(provider, field_name="provider")
        _require_non_blank_str(dataset_name, field_name="dataset_name")

        run_id = str(uuid.uuid4())
        started_at = _now_naive_utc()

        connection = duckdb.connect(str(self._manager.database_path))
        try:
            schema_version = self._current_schema_version(connection)
            connection.execute(
                "INSERT INTO ingestion_runs "
                "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
                "schema_version) VALUES (?, ?, ?, ?, 'running', ?, ?)",
                [run_id, provider, dataset_name, started_at, CODE_VERSION, schema_version],
            )

            try:
                connection.execute("BEGIN TRANSACTION")
                inserted = 0
                updated = 0
                for item in items:
                    if self._store_one(connection, item, run_id=run_id):
                        inserted += 1
                    else:
                        updated += 1
                self._complete_run(
                    connection,
                    run_id=run_id,
                    status="succeeded",
                    records_received=received,
                    error_category=None,
                )
                connection.execute("COMMIT")
            except Exception as exc:
                connection.execute("ROLLBACK")
                error_category = (
                    "content_conflict"
                    if isinstance(exc, NewsStorageConflictError)
                    else "storage_error"
                )
                try:
                    self._complete_run(
                        connection,
                        run_id=run_id,
                        status="failed",
                        records_received=received,
                        error_category=error_category,
                    )
                except Exception:
                    raise NewsStorageError(
                        "Failed to record the ingestion run as failed after a storage error."
                    ) from None
                return NewsStorageResult(
                    received=received,
                    inserted=0,
                    updated=0,
                    failed=received,
                    ingestion_run_id=run_id,
                    ingestion_run_status="failed",
                )
        finally:
            connection.close()

        return NewsStorageResult(
            received=received,
            inserted=inserted,
            updated=updated,
            failed=0,
            ingestion_run_id=run_id,
            ingestion_run_status="succeeded",
        )

    @staticmethod
    def _current_schema_version(connection: duckdb.DuckDBPyConnection) -> str | None:
        row = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    @staticmethod
    def _complete_run(
        connection: duckdb.DuckDBPyConnection,
        *,
        run_id: str,
        status: str,
        records_received: int,
        error_category: str | None,
    ) -> None:
        connection.execute(
            "UPDATE ingestion_runs SET status = ?, completed_at_utc = ?, "
            "records_received = ?, error_category = ? WHERE run_id = ?",
            [status, _now_naive_utc(), records_received, error_category, run_id],
        )

    def _store_one(
        self,
        connection: duckdb.DuckDBPyConnection,
        item: NewsItem,
        *,
        run_id: str,
    ) -> bool:
        """Insert or refresh one article. Returns True if inserted, False if refreshed.

        Raises ``NewsStorageConflictError`` if an existing row's stable
        content conflicts with the incoming article.
        """
        created_at = _parse_optional_timestamp(item.created_at)
        updated_at = _parse_optional_timestamp(item.updated_at)
        retrieved_at = _parse_timestamp(item.retrieved_at)
        related_symbols = _sorted_related_symbols(item)
        now = _now_naive_utc()

        existing = connection.execute(
            "SELECT headline, source, article_url, created_at FROM news_articles "
            "WHERE provider = ? AND provider_article_id = ?",
            [item.provider, item.provider_article_id],
        ).fetchone()

        if existing is None:
            connection.execute(
                "INSERT INTO news_articles ("
                "provider, provider_article_id, headline, source, article_url, summary, "
                "created_at, updated_at, related_symbols, retrieved_at, first_ingested_at, "
                "last_seen_at, ingestion_run_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item.provider,
                    item.provider_article_id,
                    item.headline,
                    item.source,
                    item.url,
                    item.summary,
                    created_at,
                    updated_at,
                    related_symbols,
                    retrieved_at,
                    now,
                    now,
                    run_id,
                ],
            )
            return True

        existing_headline, existing_source, existing_url, existing_created_at = existing
        if (
            existing_headline != item.headline
            or existing_source != item.source
            or existing_url != item.url
            or existing_created_at != created_at
        ):
            raise NewsStorageConflictError(
                "Conflicting stable content for an existing provider article; "
                "refusing to overwrite."
            )

        connection.execute(
            "UPDATE news_articles SET summary = ?, updated_at = ?, related_symbols = ?, "
            "retrieved_at = ?, last_seen_at = ?, ingestion_run_id = ? "
            "WHERE provider = ? AND provider_article_id = ?",
            [
                item.summary,
                updated_at,
                related_symbols,
                retrieved_at,
                now,
                run_id,
                item.provider,
                item.provider_article_id,
            ],
        )
        return False
