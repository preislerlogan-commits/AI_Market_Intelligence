"""Read-only market-context snapshot builder.

``MarketContextBuilder`` produces exactly one deterministic, JSON-ready
snapshot dict from data already stored in the local DuckDB database (see
``market_intelligence/storage/``). It makes no network request of any kind
(no Alpaca, FRED, OpenAI, or Anthropic call), never writes to the database,
never applies a migration, and never imports or touches any connector,
ingestion repository, or orchestration code. It only reads, via a read-only
DuckDB connection (``duckdb.connect(path, read_only=True)``), from tables
that other, already-reviewed parts of this project populate.

This module deliberately contains no sentiment, prediction, trading bias,
confidence score, options recommendation, support/resistance, or other
agent-conclusion field -- only provider-reported data already stored,
plus its own provenance and coverage/staleness bookkeeping. See
``docs/MARKET_CONTEXT_SNAPSHOT.md`` for the full field contract, the
staleness thresholds used, and known limitations.

Every public input (symbol, the two result-count limits, and the requested
macro series IDs) is strictly validated and normalized *before* any DuckDB
connection is opened, so malformed or malicious input never reaches
storage. A missing database file is not an error: it produces a valid
snapshot with every section reported missing, since this snapshot layer
must also work before any ingestion has ever run.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)
from market_intelligence.data_connectors.fred_macro_data import (
    FredInvalidSeriesIdError,
    normalize_series_id,
)
from market_intelligence.orchestration.clock import Clock, resolve_as_of, system_clock
from market_intelligence.storage.database import DuckDBManager

# This project currently only ingests one macro series (see
# market_intelligence/orchestration/jobs.json); this default mirrors that,
# but a caller may request a different bounded set explicitly.
DEFAULT_MACRO_SERIES_IDS: tuple[str, ...] = ("FEDFUNDS",)
MAX_MACRO_SERIES_IDS = 10

DEFAULT_RECENT_BARS_LIMIT = 5
MAX_RECENT_BARS_LIMIT = 50

DEFAULT_RECENT_NEWS_LIMIT = 5
MAX_RECENT_NEWS_LIMIT = 50

# The short-period return compares the latest stored bar's close against the
# close RETURN_PERIOD_BARS bars earlier (same symbol/timeframe/feed/
# adjustment/currency identity) -- a plain, historical, already-stored-data
# computation, never a forecast.
RETURN_PERIOD_BARS = 5

# Fixed, documented staleness thresholds -- see docs/MARKET_CONTEXT_SNAPSHOT.md
# for why each value was chosen. These are data-freshness signals only, not
# a judgment about market conditions.
#
# BARS_STALE_AFTER is a plain elapsed-time threshold, not an exchange-
# calendar or holiday-aware one: it does not know which days are trading
# days. 24 hours would falsely flag a perfectly current Friday-afternoon
# bar as stale by Saturday morning, since no new bar is expected over a
# weekend. 72 hours is a conservative, deliberately weekend-tolerant
# threshold (it comfortably spans a normal Friday-close-to-Monday-open
# gap) at the cost of being slower to flag staleness caused by a market
# holiday or an actual ingestion gap. The actual latest stored bar
# timestamp is still always exposed (``price.latest_bar_timestamp_utc``,
# ``coverage.bars.latest_bar_timestamp_utc``), so a future consumer that
# needs a stricter, calendar-aware staleness decision can compute one
# itself from that raw timestamp rather than relying on this flag alone.
BARS_STALE_AFTER = timedelta(hours=72)
NEWS_STALE_AFTER = timedelta(days=7)
MACRO_STALE_AFTER = timedelta(days=90)

# Fixed, deterministic provenance value recorded on every bars_provenance
# dict (present, missing, or empty). Alpaca's historical bars endpoint (see
# AlpacaBarsClient) returns exactly what it reports for the requested feed
# with no regular-trading-hours filter applied by this project, so stored
# bars -- and therefore the "latest" bar this module reports -- may reflect
# a pre-market or after-hours observation rather than the official regular-
# session close. This field exists so a consumer never has to assume
# RTH-only coverage, and so this module never silently implies that
# ``price.latest_close`` is an official market close.
BARS_SESSION_SCOPE = "provider_returned_unfiltered"

_RETURN_DECIMAL_EXPONENT = Decimal("0.000001")


class MarketContextError(RuntimeError):
    """Sanitized error for the market-context snapshot builder.

    Never includes the database path, SQL text, or a raw underlying
    exception -- only a fixed, non-input-derived description.
    """


class MarketContextValidationError(MarketContextError):
    """Raised when an input fails validation before any DuckDB connection is opened.

    The message never echoes raw, unvalidated input.
    """


def normalize_bounded_limit(value: Any, *, field_name: str, maximum: int) -> int:
    """Normalize and validate a strictly bounded, positive result-count limit.

    Raises ``MarketContextValidationError`` unless ``value`` is a plain
    ``int`` (booleans rejected, since ``bool`` is a subclass of ``int``)
    within ``[1, maximum]``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MarketContextValidationError(f"Invalid {field_name}: expected an integer.")
    if not (1 <= value <= maximum):
        raise MarketContextValidationError(
            f"Invalid {field_name}: must be between 1 and {maximum}."
        )
    return value


def normalize_macro_series_ids(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize and validate the configured macro series IDs.

    Raises ``MarketContextValidationError`` for a non-sequence (a plain
    string is rejected even though it is technically iterable character by
    character), more than ``MAX_MACRO_SERIES_IDS`` entries, or any entry
    that fails ``FredMacroDataClient``'s own series-ID validation. Returns a
    sorted, deduplicated tuple so downstream ordering is always
    deterministic regardless of input order.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise MarketContextValidationError(
            "Invalid macro_series_ids: expected a sequence of strings."
        )
    if len(values) > MAX_MACRO_SERIES_IDS:
        raise MarketContextValidationError(
            f"Invalid macro_series_ids: at most {MAX_MACRO_SERIES_IDS} series may be requested."
        )
    normalized: set[str] = set()
    for value in values:
        try:
            normalized.add(normalize_series_id(value))
        except FredInvalidSeriesIdError as exc:
            raise MarketContextValidationError(f"Invalid macro series ID: {exc}") from None
    return tuple(sorted(normalized))


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _utc_timestamp_str(value: datetime | None) -> str | None:
    """Format a stored TIMESTAMP value as an RFC3339 UTC string.

    Every TIMESTAMP column this module reads holds a naive ``datetime`` that
    was already normalized to UTC before being written (see
    ``market_intelligence/storage/bar_repository.py`` and
    ``news_repository.py``); this only formats that existing convention, it
    performs no timezone conversion of its own.
    """
    if value is None:
        return None
    return value.isoformat() + "Z"


def _date_str(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _format_as_of(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class _BarsSection:
    provenance: dict[str, str | None]
    latest_bar_timestamp_utc: str | None
    latest_close: str | None
    short_return: dict[str, Any] | None
    recent_bars: list[dict[str, Any]]
    coverage: dict[str, Any]
    missing: bool
    stale: bool


def _empty_bars_section(symbol: str) -> _BarsSection:
    return _BarsSection(
        provenance={
            "provider": None,
            "symbol": symbol,
            "timeframe": None,
            "feed": None,
            "adjustment": None,
            "currency": None,
            "session_scope": BARS_SESSION_SCOPE,
        },
        latest_bar_timestamp_utc=None,
        latest_close=None,
        short_return=None,
        recent_bars=[],
        coverage={
            "row_count": 0,
            "earliest_bar_timestamp_utc": None,
            "latest_bar_timestamp_utc": None,
        },
        missing=True,
        stale=True,
    )


def _empty_news_section() -> dict[str, Any]:
    return {
        "recent_articles": [],
        "coverage": {
            "row_count": 0,
            "earliest_created_at_utc": None,
            "latest_created_at_utc": None,
        },
        "missing": True,
        "stale": True,
    }


def _empty_macro_series(series_id: str) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "has_stored_observation": False,
        "observation_date": None,
        "is_missing": None,
        "value": None,
        "coverage": {
            "row_count": 0,
            "earliest_observation_date": None,
            "latest_observation_date": None,
        },
        "stale": True,
    }


class MarketContextBuilder:
    """Builds one deterministic, read-only market-context snapshot from local DuckDB storage.

    See the module docstring and ``docs/MARKET_CONTEXT_SNAPSHOT.md`` for the
    full field contract and known limitations.
    """

    def __init__(self, settings: Settings | None = None, clock: Clock = system_clock) -> None:
        self._settings = settings or Settings()
        self._clock = clock
        # DuckDBManager's constructor only resolves and safety-checks the
        # database path; it never touches the filesystem or opens a
        # connection, and it never applies a migration.
        self._database_path: Path = DuckDBManager(settings=self._settings).database_path

    def build_snapshot(
        self,
        symbol: str,
        *,
        recent_bars_limit: int = DEFAULT_RECENT_BARS_LIMIT,
        recent_news_limit: int = DEFAULT_RECENT_NEWS_LIMIT,
        macro_series_ids: Sequence[str] = DEFAULT_MACRO_SERIES_IDS,
    ) -> dict[str, Any]:
        """Build one sanitized, JSON-ready market-context snapshot dict.

        All inputs are strictly validated and normalized before any DuckDB
        connection is opened. Raises ``MarketContextValidationError`` for an
        invalid ``symbol``/``recent_bars_limit``/``recent_news_limit``/
        ``macro_series_ids``, or ``MarketContextError`` (sanitized) if the
        local database exists but cannot be read. A missing database file is
        not an error -- it produces a valid snapshot with every section
        reported missing.
        """
        try:
            normalized_symbol = normalize_symbol(symbol)
        except AlpacaInvalidSymbolError as exc:
            raise MarketContextValidationError(f"Invalid symbol: {exc}") from None

        normalized_bars_limit = normalize_bounded_limit(
            recent_bars_limit, field_name="recent_bars_limit", maximum=MAX_RECENT_BARS_LIMIT
        )
        normalized_news_limit = normalize_bounded_limit(
            recent_news_limit, field_name="recent_news_limit", maximum=MAX_RECENT_NEWS_LIMIT
        )
        normalized_series_ids = normalize_macro_series_ids(macro_series_ids)

        as_of = resolve_as_of(self._clock)

        if not self._database_path.exists():
            bars_section = _empty_bars_section(normalized_symbol)
            news_section = _empty_news_section()
            macro_section = {
                "series": [_empty_macro_series(series_id) for series_id in normalized_series_ids]
            }
        else:
            try:
                connection = duckdb.connect(str(self._database_path), read_only=True)
            except duckdb.Error:
                raise MarketContextError("Failed to open local storage for reading.") from None

            try:
                existing_tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT table_name FROM information_schema.tables"
                    ).fetchall()
                }
                try:
                    bars_section = self._read_bars_section(
                        connection, normalized_symbol, normalized_bars_limit, as_of, existing_tables
                    )
                    news_section = self._read_news_section(
                        connection, normalized_symbol, normalized_news_limit, as_of, existing_tables
                    )
                    macro_section = self._read_macro_section(
                        connection, normalized_series_ids, as_of, existing_tables
                    )
                except duckdb.Error:
                    raise MarketContextError(
                        "Failed to read market context data from local storage."
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
                    raise MarketContextError(
                        "Failed to close local storage connection."
                    ) from None

        macro_missing_series = [
            entry["series_id"]
            for entry in macro_section["series"]
            if not entry["has_stored_observation"]
        ]
        macro_stale_series = [
            entry["series_id"] for entry in macro_section["series"] if entry["stale"]
        ]

        return {
            "snapshot_created_at_utc": _format_as_of(as_of),
            "symbol": normalized_symbol,
            "request": {
                "symbol": normalized_symbol,
                "recent_bars_limit": normalized_bars_limit,
                "recent_news_limit": normalized_news_limit,
                "macro_series_ids": list(normalized_series_ids),
            },
            "price": {
                "latest_bar_timestamp_utc": bars_section.latest_bar_timestamp_utc,
                "latest_close": bars_section.latest_close,
                "short_return": bars_section.short_return,
                "recent_bars": bars_section.recent_bars,
            },
            "bars_provenance": bars_section.provenance,
            "news": {"recent_articles": news_section["recent_articles"]},
            "macro": {"series": macro_section["series"]},
            "coverage": {
                "bars": bars_section.coverage,
                "news": news_section["coverage"],
            },
            "flags": {
                "bars_missing": bars_section.missing,
                "bars_stale": bars_section.stale,
                "news_missing": news_section["missing"],
                "news_stale": news_section["stale"],
                "macro_missing_series": macro_missing_series,
                "macro_stale_series": macro_stale_series,
            },
        }

    def _read_bars_section(
        self,
        connection: duckdb.DuckDBPyConnection,
        symbol: str,
        limit: int,
        as_of: datetime,
        existing_tables: set[str],
    ) -> _BarsSection:
        if "market_bars" not in existing_tables:
            return _empty_bars_section(symbol)

        identity_row = connection.execute(
            """
            SELECT provider, timeframe, feed, adjustment, currency
            FROM market_bars
            WHERE symbol = ?
            ORDER BY bar_timestamp DESC, provider, timeframe, feed, adjustment, currency
            LIMIT 1
            """,
            [symbol],
        ).fetchone()

        if identity_row is None:
            return _empty_bars_section(symbol)

        provider, timeframe, feed, adjustment, currency = identity_row
        identity_params = [provider, symbol, timeframe, feed, adjustment, currency]

        query_limit = max(limit, RETURN_PERIOD_BARS + 1)
        rows = connection.execute(
            """
            SELECT bar_timestamp, open, high, low, close, volume, trade_count, vwap
            FROM market_bars
            WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ?
              AND adjustment = ? AND currency = ?
            ORDER BY bar_timestamp DESC
            LIMIT ?
            """,
            [*identity_params, query_limit],
        ).fetchall()

        row_count, earliest_ts, latest_ts = connection.execute(
            """
            SELECT count(*), min(bar_timestamp), max(bar_timestamp)
            FROM market_bars
            WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ?
              AND adjustment = ? AND currency = ?
            """,
            identity_params,
        ).fetchone()

        latest_bar_timestamp = rows[0][0]
        latest_close = rows[0][4]

        short_return = None
        if len(rows) > RETURN_PERIOD_BARS:
            start_timestamp = rows[RETURN_PERIOD_BARS][0]
            start_close = rows[RETURN_PERIOD_BARS][4]
            if start_close != 0:
                return_pct = ((latest_close - start_close) / start_close).quantize(
                    _RETURN_DECIMAL_EXPONENT
                )
                short_return = {
                    "period_bars": RETURN_PERIOD_BARS,
                    "start_bar_timestamp_utc": _utc_timestamp_str(start_timestamp),
                    "start_close": _decimal_str(start_close),
                    "end_bar_timestamp_utc": _utc_timestamp_str(latest_bar_timestamp),
                    "end_close": _decimal_str(latest_close),
                    "return_pct": _decimal_str(return_pct),
                }

        recent_bars = [
            {
                "bar_timestamp_utc": _utc_timestamp_str(row[0]),
                "open": _decimal_str(row[1]),
                "high": _decimal_str(row[2]),
                "low": _decimal_str(row[3]),
                "close": _decimal_str(row[4]),
                "volume": row[5],
                "trade_count": row[6],
                "vwap": _decimal_str(row[7]),
            }
            for row in rows[:limit]
        ]

        stale = (as_of - latest_bar_timestamp.replace(tzinfo=UTC)) > BARS_STALE_AFTER

        return _BarsSection(
            provenance={
                "provider": provider,
                "symbol": symbol,
                "timeframe": timeframe,
                "feed": feed,
                "adjustment": adjustment,
                "currency": currency,
                "session_scope": BARS_SESSION_SCOPE,
            },
            latest_bar_timestamp_utc=_utc_timestamp_str(latest_bar_timestamp),
            latest_close=_decimal_str(latest_close),
            short_return=short_return,
            recent_bars=recent_bars,
            coverage={
                "row_count": row_count,
                "earliest_bar_timestamp_utc": _utc_timestamp_str(earliest_ts),
                "latest_bar_timestamp_utc": _utc_timestamp_str(latest_ts),
            },
            missing=False,
            stale=stale,
        )

    def _read_news_section(
        self,
        connection: duckdb.DuckDBPyConnection,
        symbol: str,
        limit: int,
        as_of: datetime,
        existing_tables: set[str],
    ) -> dict[str, Any]:
        if "news_articles" not in existing_tables:
            return _empty_news_section()

        rows = connection.execute(
            """
            SELECT provider_article_id, headline, source, created_at, related_symbols
            FROM news_articles
            WHERE list_contains(related_symbols, ?)
            ORDER BY created_at DESC NULLS LAST, provider_article_id
            LIMIT ?
            """,
            [symbol, limit],
        ).fetchall()

        row_count, earliest_created_at, latest_created_at = connection.execute(
            """
            SELECT count(*), min(created_at), max(created_at)
            FROM news_articles
            WHERE list_contains(related_symbols, ?)
            """,
            [symbol],
        ).fetchone()

        articles = [
            {
                "provider_article_id": row[0],
                "headline": row[1],
                "source": row[2],
                "created_at_utc": _utc_timestamp_str(row[3]),
                "related_symbols": list(row[4]),
            }
            for row in rows
        ]

        missing = row_count == 0
        stale = (
            missing
            or latest_created_at is None
            or (as_of - latest_created_at.replace(tzinfo=UTC)) > NEWS_STALE_AFTER
        )

        return {
            "recent_articles": articles,
            "coverage": {
                "row_count": row_count,
                "earliest_created_at_utc": _utc_timestamp_str(earliest_created_at),
                "latest_created_at_utc": _utc_timestamp_str(latest_created_at),
            },
            "missing": missing,
            "stale": stale,
        }

    def _read_macro_section(
        self,
        connection: duckdb.DuckDBPyConnection,
        series_ids: tuple[str, ...],
        as_of: datetime,
        existing_tables: set[str],
    ) -> dict[str, Any]:
        series_entries = []
        for series_id in series_ids:
            if "macro_observations" not in existing_tables:
                series_entries.append(_empty_macro_series(series_id))
                continue

            latest_row = connection.execute(
                """
                SELECT observation_date, is_missing, value
                FROM macro_observations
                WHERE provider = 'fred' AND series_id = ?
                ORDER BY observation_date DESC, realtime_start DESC, realtime_end DESC
                LIMIT 1
                """,
                [series_id],
            ).fetchone()

            if latest_row is None:
                series_entries.append(_empty_macro_series(series_id))
                continue

            row_count, earliest_date, latest_date = connection.execute(
                """
                SELECT count(*), min(observation_date), max(observation_date)
                FROM macro_observations
                WHERE provider = 'fred' AND series_id = ?
                """,
                [series_id],
            ).fetchone()

            observation_date, is_missing, value = latest_row
            stale = (as_of.date() - observation_date) > MACRO_STALE_AFTER

            series_entries.append(
                {
                    "series_id": series_id,
                    "has_stored_observation": True,
                    "observation_date": _date_str(observation_date),
                    "is_missing": bool(is_missing),
                    "value": _decimal_str(value),
                    "coverage": {
                        "row_count": row_count,
                        "earliest_observation_date": _date_str(earliest_date),
                        "latest_observation_date": _date_str(latest_date),
                    },
                    "stale": stale,
                }
            )

        return {"series": series_entries}
