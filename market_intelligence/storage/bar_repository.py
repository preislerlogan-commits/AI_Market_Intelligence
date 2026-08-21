"""Market-bar storage: persists normalized ``Bar`` objects into DuckDB.

This module never makes network requests and never accepts, stores, or logs
credentials. It writes only to the ``market_bars`` table (migration
``0005``, see ``market_intelligence/storage/migrations/``) and the existing
``ingestion_runs`` bookkeeping table -- never indicators, returns, labels,
sentiment, predictions, recommendations, option-contract data, orders,
execution fields, request headers, or raw API responses. It assumes the
database has already been brought up to at least migration ``0005`` (e.g.
via ``DuckDBManager.initialize()``); this class only writes rows, it never
applies migrations itself.

Every batch -- and the final ``succeeded`` ``ingestion_runs`` status update
-- is written inside a single DuckDB transaction: if any item in the batch
cannot be safely stored (invalid input, or an existing bar identity whose
OHLCV/trade_count/vwap values conflict with the incoming values), or the
final ``succeeded`` status update itself fails, nothing in that batch is
persisted, and the corresponding ``ingestion_runs`` row is separately
recorded as ``failed`` with a sanitized error category -- never a raw
exception message, and never prices, timestamps, or credentials. Stored bar
changes are never left associated with a ``running`` or ``failed`` run. If
recording the ``failed`` status itself also fails, a sanitized
``BarStorageError`` is raised.

Duplicate ``Bar`` objects within a single incoming batch are handled
deterministically without any special-cased duplicate-tracking code: every
bar is written through the same DuckDB connection inside one transaction,
so a second occurrence of the same identity later in the same batch sees
the first occurrence's not-yet-committed row exactly as it would see an
already-committed row from a prior call -- it is either an exact match
(counted as ``existing_or_updated``) or a conflict (rolls back the whole
batch), never a silent double-insert.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import (
    DATA_ADJUSTMENT,
    DATA_CURRENCY,
    DATA_FEED,
    AlpacaBarsInvalidInputError,
    Bar,
    normalize_timestamp,
)
from market_intelligence.storage.database import DuckDBManager

CODE_VERSION = "bar_repository_v1"
DEFAULT_DATASET_NAME = "bars"
DEFAULT_PROVIDER = "alpaca"

_ALLOWED_TIMEFRAMES = frozenset({"1Min", "5Min", "1Day"})

# Matches the market_bars schema's open/high/low/close/vwap columns
# (migration 0005: DECIMAL(18,6) -- 12 integer digits, 6 fractional digits).
# A value with more than 6 fractional digits would be silently rounded by
# DuckDB, and a value outside this magnitude would silently overflow; both
# are rejected here before any write rather than allowed to happen inside
# DuckDB.
_DECIMAL_SCALE = 6
_DECIMAL_MAX_ABS = Decimal("999999999999.999999")


class BarStorageError(RuntimeError):
    """Sanitized storage failure.

    Never includes OHLCV values, timestamps, database internals, or
    credentials.
    """


class BarStorageValidationError(BarStorageError):
    """Raised when supplied items fail validation before any database write.

    No ``ingestion_runs`` row is created for this failure mode -- it
    indicates a caller/programming error (malformed input), not a data or
    infrastructure failure to record for audit purposes.
    """


class BarStorageConflictError(BarStorageError):
    """Raised internally when an incoming bar conflicts with a stored bar.

    Caught by ``store_bars`` to trigger a full-batch rollback; never
    propagated to callers directly.
    """


@dataclass(frozen=True)
class BarStorageResult:
    """Sanitized result of one ``store_bars`` call.

    Never includes OHLCV values, timestamps, database internals, or
    credentials.
    """

    received: int
    inserted: int
    existing_or_updated: int
    failed: int
    ingestion_run_id: str
    ingestion_run_status: str


def _require_non_blank_str(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise BarStorageValidationError(f"Invalid items: {field_name} must be a non-blank string.")


def _require_rfc3339_timestamp(value: object, *, field_name: str) -> str:
    """Require ``value`` to be a strict RFC3339 timestamp (explicit Z/offset).

    Delegates to the bars connector's own RFC3339 validator so storage
    rejects naive, date-only, malformed, blank, and non-string timestamps
    identically to the connector, and returns the UTC-normalized form.
    Never echoes the raw input value.
    """
    if not isinstance(value, str):
        raise BarStorageValidationError(
            f"Invalid items: {field_name} must be a valid RFC3339 timestamp."
        )
    try:
        return normalize_timestamp(value, field_name=field_name)
    except AlpacaBarsInvalidInputError as exc:
        raise BarStorageValidationError(f"Invalid items: {exc}") from None


def _validate_decimal_storage_precision(value: Decimal, *, field_name: str) -> None:
    """Reject a Decimal that DuckDB's DECIMAL(18,6) column would silently round or overflow.

    Never echoes the rejected value. Trailing zeros beyond the 6th
    fractional digit do not require rounding (they are normalized away
    before the fractional-digit check), but any value with more than 6
    significant fractional digits, or a magnitude the column cannot hold,
    is rejected.
    """
    normalized_exponent = value.normalize().as_tuple().exponent
    if isinstance(normalized_exponent, str):
        raise BarStorageValidationError(f"Invalid items: {field_name} is not a supported value.")
    if normalized_exponent < -_DECIMAL_SCALE:
        raise BarStorageValidationError(
            f"Invalid items: {field_name} exceeds the maximum supported decimal precision."
        )
    if abs(value) > _DECIMAL_MAX_ABS:
        raise BarStorageValidationError(
            f"Invalid items: {field_name} exceeds the maximum supported decimal range."
        )


def _require_decimal(
    value: object,
    *,
    field_name: str,
    allow_none: bool,
    require_positive: bool = False,
    reject_negative: bool = False,
) -> None:
    if value is None:
        if allow_none:
            return
        raise BarStorageValidationError(f"Invalid items: {field_name} must be a Decimal.")
    if not isinstance(value, Decimal):
        raise BarStorageValidationError(f"Invalid items: {field_name} must be a Decimal.")
    if not value.is_finite():
        raise BarStorageValidationError(f"Invalid items: {field_name} must be a finite value.")
    _validate_decimal_storage_precision(value, field_name=field_name)
    if require_positive and value <= 0:
        raise BarStorageValidationError(f"Invalid items: {field_name} must be greater than zero.")
    if reject_negative and value < 0:
        raise BarStorageValidationError(f"Invalid items: {field_name} must not be negative.")


def _require_nonneg_int(value: object, *, field_name: str, allow_none: bool) -> None:
    message = f"Invalid items: {field_name} must be a non-negative integer."
    if value is None:
        if allow_none:
            return
        raise BarStorageValidationError(message)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BarStorageValidationError(message)
    if value < 0:
        raise BarStorageValidationError(message)


def _validate_candle(item: Bar) -> None:
    high, low, open_, close = item.high, item.low, item.open, item.close
    if not (high >= low and high >= open_ and high >= close and low <= open_ and low <= close):
        raise BarStorageValidationError("Invalid items: candle values are inconsistent.")


def _validate_items(items: Sequence[Bar], *, provider: str) -> None:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise BarStorageValidationError("Invalid items: expected a sequence of Bar.")

    for item in items:
        if not isinstance(item, Bar):
            raise BarStorageValidationError("Invalid items: every item must be a Bar.")
        if item.provider != provider:
            raise BarStorageValidationError(
                "Invalid items: every item's provider must match the requested provider."
            )
        _require_non_blank_str(item.provider, field_name="provider")
        _require_non_blank_str(item.symbol, field_name="symbol")

        if item.timeframe not in _ALLOWED_TIMEFRAMES:
            raise BarStorageValidationError("Invalid items: timeframe is not project-approved.")
        if item.feed != DATA_FEED:
            raise BarStorageValidationError("Invalid items: feed does not match fixed provenance.")
        if item.adjustment != DATA_ADJUSTMENT:
            raise BarStorageValidationError(
                "Invalid items: adjustment does not match fixed provenance."
            )
        if item.currency != DATA_CURRENCY:
            raise BarStorageValidationError(
                "Invalid items: currency does not match fixed provenance."
            )

        _require_rfc3339_timestamp(item.timestamp, field_name="timestamp")
        _require_rfc3339_timestamp(item.retrieved_at, field_name="retrieved_at")

        _require_decimal(item.open, field_name="open", allow_none=False, require_positive=True)
        _require_decimal(item.high, field_name="high", allow_none=False, require_positive=True)
        _require_decimal(item.low, field_name="low", allow_none=False, require_positive=True)
        _require_decimal(item.close, field_name="close", allow_none=False, require_positive=True)
        _require_decimal(item.vwap, field_name="vwap", allow_none=True, reject_negative=True)

        _require_nonneg_int(item.volume, field_name="volume", allow_none=False)
        _require_nonneg_int(item.trade_count, field_name="trade_count", allow_none=True)

        _validate_candle(item)


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None)


def _parse_timestamp(value: str) -> datetime:
    """Parse an already-validated RFC3339 timestamp into a naive UTC datetime."""
    normalized = normalize_timestamp(value, field_name="timestamp")
    return _to_naive_utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))


def _now_naive_utc() -> datetime:
    return _to_naive_utc(datetime.now(UTC))


class BarRepository:
    """Persists normalized ``Bar`` objects into the local ``market_bars`` table."""

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

    def store_bars(
        self,
        items: Sequence[Bar],
        *,
        provider: str = DEFAULT_PROVIDER,
        dataset_name: str = DEFAULT_DATASET_NAME,
    ) -> BarStorageResult:
        """Store a batch of already-normalized ``Bar`` objects.

        Validates every item before any database write (raising
        ``BarStorageValidationError`` and writing nothing if validation
        fails). Otherwise records a ``running`` ``ingestion_runs`` row, then
        writes the whole batch -- plus the final ``succeeded``
        ``ingestion_runs`` status update -- inside one DuckDB transaction:
        an unseen bar identity is inserted; an already-known bar identity
        whose OHLCV/trade_count/vwap values match has its retrieval/last-
        seen/run provenance refreshed; an already-known bar identity whose
        values conflict aborts the entire batch. If any bar write, or the
        final ``succeeded`` status update itself, fails, the whole
        transaction is rolled back (no bar changes are left associated with
        the run) and the run is separately recorded as ``failed`` with a
        sanitized error category, so stored bar changes are never left
        attached to a ``running`` or ``failed`` run. Never raises for a
        conflict or storage failure -- both are reported truthfully via the
        returned ``BarStorageResult`` -- unless recording the ``failed``
        status itself also fails, in which case a sanitized
        ``BarStorageError`` is raised.
        """
        _validate_items(items, provider=provider)
        received = len(items)
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
                existing_or_updated = 0
                for item in items:
                    if self._store_one(connection, item, run_id=run_id):
                        inserted += 1
                    else:
                        existing_or_updated += 1
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
                    if isinstance(exc, BarStorageConflictError)
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
                    raise BarStorageError(
                        "Failed to record the ingestion run as failed after a storage error."
                    ) from None
                return BarStorageResult(
                    received=received,
                    inserted=0,
                    existing_or_updated=0,
                    failed=received,
                    ingestion_run_id=run_id,
                    ingestion_run_status="failed",
                )
        finally:
            connection.close()

        return BarStorageResult(
            received=received,
            inserted=inserted,
            existing_or_updated=existing_or_updated,
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
        item: Bar,
        *,
        run_id: str,
    ) -> bool:
        """Insert or refresh one bar. Returns True if inserted, False if refreshed.

        Raises ``BarStorageConflictError`` if an existing row's OHLCV/
        trade_count/vwap values conflict with the incoming bar.
        """
        bar_timestamp = _parse_timestamp(item.timestamp)
        retrieved_at = _parse_timestamp(item.retrieved_at)
        now = _now_naive_utc()

        existing = connection.execute(
            "SELECT open, high, low, close, volume, trade_count, vwap FROM market_bars "
            "WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ? "
            "AND adjustment = ? AND currency = ? AND bar_timestamp = ?",
            [
                item.provider,
                item.symbol,
                item.timeframe,
                item.feed,
                item.adjustment,
                item.currency,
                bar_timestamp,
            ],
        ).fetchone()

        if existing is None:
            connection.execute(
                "INSERT INTO market_bars ("
                "provider, symbol, timeframe, feed, adjustment, currency, bar_timestamp, "
                "open, high, low, close, volume, trade_count, vwap, retrieved_at, "
                "first_ingested_at, last_seen_at, ingestion_run_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item.provider,
                    item.symbol,
                    item.timeframe,
                    item.feed,
                    item.adjustment,
                    item.currency,
                    bar_timestamp,
                    item.open,
                    item.high,
                    item.low,
                    item.close,
                    item.volume,
                    item.trade_count,
                    item.vwap,
                    retrieved_at,
                    now,
                    now,
                    run_id,
                ],
            )
            return True

        existing_open, existing_high, existing_low, existing_close, existing_volume, \
            existing_trade_count, existing_vwap = existing
        if (
            existing_open != item.open
            or existing_high != item.high
            or existing_low != item.low
            or existing_close != item.close
            or existing_volume != item.volume
            or existing_trade_count != item.trade_count
            or existing_vwap != item.vwap
        ):
            raise BarStorageConflictError(
                "Conflicting OHLCV/trade_count/vwap values for an existing bar identity; "
                "refusing to overwrite."
            )

        connection.execute(
            "UPDATE market_bars SET retrieved_at = ?, last_seen_at = ?, ingestion_run_id = ? "
            "WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ? "
            "AND adjustment = ? AND currency = ? AND bar_timestamp = ?",
            [
                retrieved_at,
                now,
                run_id,
                item.provider,
                item.symbol,
                item.timeframe,
                item.feed,
                item.adjustment,
                item.currency,
                bar_timestamp,
            ],
        )
        return False
