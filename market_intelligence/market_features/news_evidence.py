"""Read-only news-evidence snapshot builder.

``NewsEvidenceBuilder`` produces exactly one deterministic, JSON-ready
snapshot dict from data already stored in the local DuckDB database (see
``market_intelligence/storage/news_repository.py``). It is infrastructure for
a future News Analyst agent -- it is **not** an AI agent itself and makes no
model request of any kind.

This module makes no network request of any kind -- no Alpaca, FRED, OpenAI,
or Anthropic call. It does not import ``httpx``, any connector's HTTP
client, or any model client. It opens the local database only with
``duckdb.connect(path, read_only=True)`` and never writes a row, never
applies a migration, and never touches ``ingestion_runs`` or any
orchestration table. The only thing it reuses from
``market_intelligence/data_connectors/`` is ``normalize_symbol`` -- a small,
already-reviewed, read-only validation helper -- mirroring how
``market_context.py``/``session_quality.py`` reuse the same helper. It never
imports a connector's HTTP client, a storage repository's write path, or any
model client.

Every public input (symbol, limit) is strictly validated and normalized
*before* any DuckDB connection is opened, so malformed or malicious input
never reaches storage. A missing database file, a missing ``news_articles``
table, or a symbol with no stored articles are all valid, non-error input --
this module always returns a truthful, non-crashing snapshot with
``missing: true`` and ``stale: true``.

**Headlines and provider summaries are treated as untrusted, third-party
provider text throughout.** This module preserves them exactly as stored --
it never interprets, sanitizes semantically, summarizes, classifies, scores
sentiment, or infers market impact from them. Every evidence ``evidence_id``
is a stable, code-generated identifier derived only from
``(provider, provider_article_id)`` -- never from headline/summary text --
so the same stored article always produces the same ``evidence_id`` across
calls.

**Article URLs are deliberately excluded from the per-article evidence
fields.** If a future consumer needs a URL for provenance/audit purposes, it
is available only in the separate top-level ``audit_provenance`` field. A
future News Analyst (or any other model-facing consumer) **must** exclude
``audit_provenance`` from whatever payload it sends to a model -- see the
field's own ``note`` value and ``docs/NEWS_EVIDENCE_SNAPSHOT.md``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)
from market_intelligence.orchestration.clock import Clock, resolve_as_of, system_clock
from market_intelligence.storage.database import DuckDBManager

DEFAULT_LIMIT = 10
MIN_LIMIT = 1
MAX_LIMIT = 20

# A plain, fixed, elapsed-time freshness threshold -- not a market-calendar-
# or news-cadence-aware one. Mirrors the same 7-day rationale documented for
# news staleness in market_intelligence/market_features/market_context.py
# (NEWS_STALE_AFTER): comfortably exceeds a normal inter-article gap for an
# actively covered symbol without flagging routine quiet periods as stale.
NEWS_STALE_AFTER = timedelta(days=7)

HEADLINE_ONLY_SCOPE = "headline_only"
HEADLINE_AND_SUMMARY_SCOPE = "headline_and_provider_summary"

AUDIT_PROVENANCE_NOTE = (
    "Provenance/audit only -- article URLs, kept separate from the "
    "model-facing evidence fields above. A future News Analyst (or any "
    "other model-facing consumer of this snapshot) MUST exclude this "
    "field from any payload sent to a model."
)

_EVIDENCE_ID_PREFIX = "news_"
_EVIDENCE_ID_HASH_LENGTH = 16


class NewsEvidenceError(RuntimeError):
    """Sanitized error for the news-evidence snapshot builder.

    Never includes the database path, SQL text, credentials, or a raw
    underlying exception -- only a fixed, non-input-derived description.
    """


class NewsEvidenceValidationError(NewsEvidenceError):
    """Raised when an input fails validation before any DuckDB connection is opened.

    The message never echoes raw, unvalidated input.
    """


def normalize_limit(value: Any) -> int:
    """Normalize and validate the bounded article-count limit.

    Raises ``NewsEvidenceValidationError`` unless ``value`` is a plain
    ``int`` (``bool`` is explicitly rejected, since ``bool`` is a subclass
    of ``int`` in Python) within ``[MIN_LIMIT, MAX_LIMIT]``. Validation
    happens before any DuckDB connection is opened.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise NewsEvidenceValidationError("Invalid limit: expected an integer.")
    if not (MIN_LIMIT <= value <= MAX_LIMIT):
        raise NewsEvidenceValidationError(
            f"Invalid limit: must be between {MIN_LIMIT} and {MAX_LIMIT}."
        )
    return value


def _evidence_id(provider: str, provider_article_id: str) -> str:
    """Build a stable, deterministic evidence ID for one stored article.

    Derived only from ``(provider, provider_article_id)`` -- never from
    headline/summary text, which is untrusted provider content -- so the
    same stored article always produces the same ``evidence_id`` across
    calls, regardless of ordering or which other articles are returned.
    """
    digest = hashlib.sha256(f"{provider}:{provider_article_id}".encode()).hexdigest()
    return f"{_EVIDENCE_ID_PREFIX}{digest[:_EVIDENCE_ID_HASH_LENGTH]}"


def _utc_timestamp_str(value: datetime | None) -> str | None:
    """Format a stored TIMESTAMP value as an RFC3339 UTC string.

    Every TIMESTAMP column this module reads holds a naive ``datetime`` that
    was already normalized to UTC before being written (see
    ``market_intelligence/storage/news_repository.py``); this only formats
    that existing convention, it performs no timezone conversion of its own.
    """
    if value is None:
        return None
    return value.isoformat() + "Z"


def _format_as_of(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _stale_after_hours() -> int:
    return int(NEWS_STALE_AFTER.total_seconds() // 3600)


def _empty_snapshot(symbol: str, limit: int, as_of: datetime) -> dict[str, Any]:
    return {
        "snapshot_created_at_utc": _format_as_of(as_of),
        "symbol": symbol,
        "request": {"symbol": symbol, "limit": limit},
        "article_count_returned": 0,
        "total_stored_article_count_for_symbol": 0,
        "coverage": {
            "earliest_published_at_utc": None,
            "latest_published_at_utc": None,
        },
        "freshness": {
            "stale_after_hours": _stale_after_hours(),
            "missing": True,
            "stale": True,
        },
        "articles": [],
        "audit_provenance": {
            "note": AUDIT_PROVENANCE_NOTE,
            "articles": [],
        },
    }


class NewsEvidenceBuilder:
    """Builds one deterministic, read-only news-evidence snapshot from local DuckDB storage.

    See the module docstring and ``docs/NEWS_EVIDENCE_SNAPSHOT.md`` for the
    full field contract and known limitations.
    """

    def __init__(self, settings: Settings | None = None, clock: Clock = system_clock) -> None:
        self._settings = settings or Settings()
        self._clock = clock
        # DuckDBManager's constructor only resolves and safety-checks the
        # database path; it never touches the filesystem or opens a
        # connection, and it never applies a migration.
        self._database_path: Path = DuckDBManager(settings=self._settings).database_path

    def build_snapshot(self, symbol: str, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """Build one sanitized, JSON-ready news-evidence snapshot dict.

        ``symbol`` and ``limit`` are strictly validated and normalized
        before any DuckDB connection is opened. Raises
        ``NewsEvidenceValidationError`` for an invalid ``symbol``/``limit``,
        or ``NewsEvidenceError`` (sanitized) if the local database exists
        but cannot be read. A missing database file, a missing
        ``news_articles`` table, or a symbol with no stored articles are all
        valid, non-error input -- the snapshot's flags describe this
        truthfully rather than raising.
        """
        try:
            normalized_symbol = normalize_symbol(symbol)
        except AlpacaInvalidSymbolError as exc:
            raise NewsEvidenceValidationError(f"Invalid symbol: {exc}") from None

        normalized_limit = normalize_limit(limit)

        as_of = resolve_as_of(self._clock)

        if not self._database_path.exists():
            return _empty_snapshot(normalized_symbol, normalized_limit, as_of)

        try:
            connection = duckdb.connect(str(self._database_path), read_only=True)
        except duckdb.Error:
            raise NewsEvidenceError("Failed to open local storage for reading.") from None

        try:
            try:
                table_exists = connection.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'news_articles'"
                ).fetchone()[0]
                if not table_exists:
                    snapshot = _empty_snapshot(normalized_symbol, normalized_limit, as_of)
                else:
                    snapshot = self._build_populated_snapshot(
                        connection, normalized_symbol, normalized_limit, as_of
                    )
            except duckdb.Error:
                raise NewsEvidenceError(
                    "Failed to read news evidence data from local storage."
                ) from None
        except Exception:
            # A read already failed (sanitized or not). A close() failure here
            # must never replace that exception, so it is swallowed rather than
            # propagated -- the original failure is what the caller sees.
            try:
                connection.close()
            except Exception:
                pass
            raise
        else:
            # The read otherwise succeeded. A close() failure here is the only
            # error the caller should see, and it must be sanitized like every
            # other storage failure -- never the raw underlying exception.
            try:
                connection.close()
            except Exception:
                raise NewsEvidenceError("Failed to close local storage connection.") from None

        return snapshot

    @staticmethod
    def _build_populated_snapshot(
        connection: duckdb.DuckDBPyConnection,
        symbol: str,
        limit: int,
        as_of: datetime,
    ) -> dict[str, Any]:
        total_count, earliest_published, latest_published = connection.execute(
            """
            SELECT count(*), min(created_at), max(created_at)
            FROM news_articles
            WHERE list_contains(related_symbols, ?)
            """,
            [symbol],
        ).fetchone()

        rows = connection.execute(
            """
            SELECT provider, provider_article_id, source, article_url, headline,
                   summary, created_at, updated_at, retrieved_at, related_symbols
            FROM news_articles
            WHERE list_contains(related_symbols, ?)
            ORDER BY created_at DESC NULLS LAST, provider_article_id ASC
            LIMIT ?
            """,
            [symbol, limit],
        ).fetchall()

        articles: list[dict[str, Any]] = []
        audit_articles: list[dict[str, Any]] = []
        for row in rows:
            (
                provider,
                provider_article_id,
                source,
                article_url,
                headline,
                summary,
                created_at,
                updated_at,
                retrieved_at,
                related_symbols,
            ) = row

            evidence_id = _evidence_id(provider, provider_article_id)
            content_scope = (
                HEADLINE_AND_SUMMARY_SCOPE if summary is not None else HEADLINE_ONLY_SCOPE
            )

            articles.append(
                {
                    "evidence_id": evidence_id,
                    "provider": provider,
                    "provider_article_id": provider_article_id,
                    "source": source,
                    "published_at_utc": _utc_timestamp_str(created_at),
                    "updated_at_utc": _utc_timestamp_str(updated_at),
                    "retrieved_at_utc": _utc_timestamp_str(retrieved_at),
                    "headline": headline,
                    "provider_summary": summary,
                    "related_symbols": list(related_symbols),
                    "content_scope": content_scope,
                }
            )
            audit_articles.append({"evidence_id": evidence_id, "article_url": article_url})

        missing = total_count == 0
        stale = (
            missing
            or latest_published is None
            or (as_of - latest_published.replace(tzinfo=UTC)) > NEWS_STALE_AFTER
        )

        return {
            "snapshot_created_at_utc": _format_as_of(as_of),
            "symbol": symbol,
            "request": {"symbol": symbol, "limit": limit},
            "article_count_returned": len(articles),
            "total_stored_article_count_for_symbol": total_count,
            "coverage": {
                "earliest_published_at_utc": _utc_timestamp_str(earliest_published),
                "latest_published_at_utc": _utc_timestamp_str(latest_published),
            },
            "freshness": {
                "stale_after_hours": _stale_after_hours(),
                "missing": missing,
                "stale": stale,
            },
            "articles": articles,
            "audit_provenance": {
                "note": AUDIT_PROVENANCE_NOTE,
                "articles": audit_articles,
            },
        }
