"""Option-chain snapshot storage: persists normalized ``OptionChainSnapshot``
objects into DuckDB.

This module never makes network requests and never accepts, stores, or logs
credentials. It writes only to the ``option_chain_snapshot_batches`` and
``option_chain_snapshots`` tables (migration ``0009``) and the existing
``ingestion_runs`` bookkeeping table -- never a raw provider payload, a
directional forecast, a contract ranking, a strategy recommendation, an
order, an execution field, request headers, request URLs, page tokens, or a
response body. It assumes the database has already been brought up to at
least migration ``0009``; this class only writes rows, it never applies
migrations itself.

Run-level provenance: every successful stored retrieval writes exactly one
``option_chain_snapshot_batches`` row -- ingestion-run id, provider,
underlying, requested feed, requested expiration/strike window, requested
option type, the UTC retrieval instant, and the contract count. A successful
retrieval that returned zero contracts still writes that batch row (with
``contract_count = 0`` and ``outcome = 'skipped_empty'``) and zero snapshot
rows, so its feed/bounds/retrieved_at are durably recorded. Request-level
fields are stored only on the batch row, never duplicated onto each snapshot
row.

Every batch -- the run-level row, all snapshot rows, and the final
``succeeded`` ``ingestion_runs`` status update -- is written inside a single
DuckDB transaction: if any item fails validation, or an existing snapshot
identity ``(provider, underlying, feed, contract_symbol, retrieved_at)`` has
conflicting stored values, or the final ``succeeded`` status update itself
fails, nothing in that batch is persisted (no batch row, no partial snapshot
rows) and the ``ingestion_runs`` row is separately recorded as ``failed``
with a sanitized error category -- never a raw exception message,
quote/Greek value, contract symbol, or credential. Stored changes are never
left associated with a ``running`` or ``failed`` run.

Any failure before a durable ``running`` ``ingestion_runs`` row exists --
opening the connection, reading the schema version, or inserting that row --
also raises a sanitized ``OptionChainSnapshotStorageError`` directly.

Idempotency and OPRA/indicative separation: an option snapshot is a
point-in-time observation, so the snapshot identity includes ``retrieved_at``
(the single UTC instant the connector stamped on one ``get_chain_snapshot``
call). Re-storing the same connector result refreshes only
retrieval/last-seen/run provenance; re-storing the same identity with
conflicting values aborts the batch; a later ingestion run has a different
``retrieved_at``, a new batch row, and is stored as new observation rows.
``feed`` is part of the snapshot identity and is recorded verbatim as
``requested_feed`` on the batch row, so an ``opra`` observation and an
``indicative`` observation of the same contract at the same instant are
never merged. Each ``option_chain_snapshot_batches`` row keys on its
ingestion-run id (one fresh id per store call), so it is only ever inserted.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_options_chain import (
    ALLOWED_FEEDS,
    ALLOWED_OPTION_TYPES,
    UNDERLYING,
    AlpacaOptionsChainInvalidInputError,
    OptionChainRequest,
    OptionChainSnapshot,
    normalize_expiration_date,
    parse_occ_symbol,
)
from market_intelligence.data_connectors.alpaca_options_chain import (
    PROVIDER as CONNECTOR_PROVIDER,
)
from market_intelligence.storage.database import DuckDBManager

CODE_VERSION = "option_chain_snapshot_repository_v1"
DEFAULT_DATASET_NAME = "option_chain_snapshots"
DEFAULT_PROVIDER = CONNECTOR_PROVIDER

# option_chain_snapshots price/strike columns: DECIMAL(18,6).
_PRICE_SCALE = 6
_PRICE_MAX_ABS = Decimal("999999999999.999999")
# implied_volatility / Greek columns: DECIMAL(20,10).
_GREEK_SCALE = 10
_GREEK_MAX_ABS = Decimal("9999999999.9999999999")


class OptionChainSnapshotStorageError(RuntimeError):
    """Sanitized storage failure.

    Never includes quote/trade/Greek values, contract payloads, database
    internals, or credentials.
    """


class OptionChainSnapshotStorageValidationError(OptionChainSnapshotStorageError):
    """Raised when supplied items fail validation before any database write.

    No ``ingestion_runs`` row is created for this failure mode -- it
    indicates a caller/programming error, not a data or infrastructure
    failure to record for audit purposes.
    """


class OptionChainSnapshotStorageConflictError(OptionChainSnapshotStorageError):
    """Raised internally when an incoming snapshot conflicts with a stored one.

    Caught by ``store_snapshots`` to trigger a full-batch rollback; never
    propagated to callers directly.
    """


@dataclass(frozen=True)
class OptionChainSnapshotStorageResult:
    """Sanitized result of one ``store_snapshots`` call.

    Never includes quote/trade/Greek values, contract symbols, database
    internals, or credentials. ``batch_outcome`` mirrors the persisted
    ``option_chain_snapshot_batches.outcome`` for a successful store
    (``"succeeded"`` for a non-empty chain, ``"skipped_empty"`` for a
    successful chain that returned zero contracts) and is ``"failed"`` when
    the store rolled back.
    """

    received: int
    inserted: int
    existing_or_updated: int
    failed: int
    ingestion_run_id: str
    ingestion_run_status: str
    batch_outcome: str


def _require_non_blank_str(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be a non-blank string."
        )


def _require_rfc3339_timestamp(value: object, *, field_name: str) -> None:
    from market_intelligence.data_connectors.alpaca_options_chain import _parse_snapshot_timestamp

    if not isinstance(value, str):
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be a valid RFC3339 timestamp."
        )
    try:
        _parse_snapshot_timestamp(value)
    except Exception:
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be a valid RFC3339 timestamp."
        ) from None


def _validate_decimal_precision(
    value: Decimal, *, field_name: str, scale: int, max_abs: Decimal
) -> None:
    normalized_exponent = value.normalize().as_tuple().exponent
    if isinstance(normalized_exponent, str):
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} is not a supported value."
        )
    if normalized_exponent < -scale:
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} exceeds the maximum supported decimal precision."
        )
    if abs(value) > max_abs:
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} exceeds the maximum supported decimal range."
        )


def _require_decimal(
    value: object,
    *,
    field_name: str,
    allow_none: bool,
    allow_negative: bool,
    require_positive: bool = False,
    scale: int = _PRICE_SCALE,
    max_abs: Decimal = _PRICE_MAX_ABS,
) -> None:
    if value is None:
        if allow_none:
            return
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be a Decimal."
        )
    if not isinstance(value, Decimal):
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be a Decimal."
        )
    if not value.is_finite():
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be a finite value."
        )
    _validate_decimal_precision(value, field_name=field_name, scale=scale, max_abs=max_abs)
    if require_positive and value <= 0:
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must be greater than zero."
        )
    if not allow_negative and value < 0:
        raise OptionChainSnapshotStorageValidationError(
            f"Invalid items: {field_name} must not be negative."
        )


def _require_nonneg_int(value: object, *, field_name: str, allow_none: bool) -> None:
    message = f"Invalid items: {field_name} must be a non-negative integer."
    if value is None:
        if allow_none:
            return
        raise OptionChainSnapshotStorageValidationError(message)
    if isinstance(value, bool) or not isinstance(value, int):
        raise OptionChainSnapshotStorageValidationError(message)
    if value < 0:
        raise OptionChainSnapshotStorageValidationError(message)


def _validate_request(request: object) -> OptionChainRequest:
    if not isinstance(request, OptionChainRequest):
        raise OptionChainSnapshotStorageValidationError(
            "Invalid request: expected a normalized OptionChainRequest."
        )
    if request.underlying != UNDERLYING:
        raise OptionChainSnapshotStorageValidationError(
            "Invalid request: underlying must be SPY."
        )
    if request.feed not in ALLOWED_FEEDS:
        raise OptionChainSnapshotStorageValidationError("Invalid request: unknown feed.")
    if request.option_type is not None and request.option_type not in ALLOWED_OPTION_TYPES:
        raise OptionChainSnapshotStorageValidationError("Invalid request: unknown option type.")
    try:
        normalize_expiration_date(request.expiration_date_gte, field_name="expiration_date_gte")
        normalize_expiration_date(request.expiration_date_lte, field_name="expiration_date_lte")
    except AlpacaOptionsChainInvalidInputError as exc:
        raise OptionChainSnapshotStorageValidationError(f"Invalid request: {exc}") from None
    return request


def _validate_items(
    items: Sequence[OptionChainSnapshot], *, provider: str, request: OptionChainRequest
) -> None:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise OptionChainSnapshotStorageValidationError(
            "Invalid items: expected a sequence of OptionChainSnapshot."
        )
    # An empty batch is allowed: it represents a successful chain retrieval
    # that returned zero contracts. It still records a run-level batch row
    # (see ``_resolve_batch_retrieved_at`` / ``_store_batch_record``).

    seen_symbols: set[str] = set()
    for item in items:
        if not isinstance(item, OptionChainSnapshot):
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: every item must be an OptionChainSnapshot."
            )
        if item.provider != provider:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: every item's provider must match the requested provider."
            )
        _require_non_blank_str(item.provider, field_name="provider")

        if item.underlying != UNDERLYING or item.underlying != request.underlying:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: underlying must be SPY and match the request."
            )
        if item.feed not in ALLOWED_FEEDS or item.feed != request.feed:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: feed must be a known feed and match the request feed."
            )
        if item.option_type not in ALLOWED_OPTION_TYPES:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: option_type must be 'call' or 'put'."
            )

        _require_non_blank_str(item.contract_symbol, field_name="contract_symbol")
        try:
            parsed = parse_occ_symbol(item.contract_symbol)
        except Exception:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: contract_symbol is not a valid OCC option symbol."
            ) from None
        if (
            parsed.underlying != item.underlying
            or parsed.option_type != item.option_type
            or parsed.expiration_date != item.expiration_date
            or parsed.strike_price != item.strike_price
        ):
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: parsed contract symbol disagrees with the snapshot fields."
            )
        if request.option_type is not None and item.option_type != request.option_type:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: option_type is outside the requested filter."
            )
        if not (
            request.strike_price_gte <= item.strike_price <= request.strike_price_lte
        ):
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: strike_price is outside the requested range."
            )
        if not (
            request.expiration_date_gte
            <= item.expiration_date
            <= request.expiration_date_lte
        ):
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: expiration_date is outside the requested range."
            )

        if item.contract_symbol in seen_symbols:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: duplicate contract_symbol in the batch."
            )
        seen_symbols.add(item.contract_symbol)

        _require_rfc3339_timestamp(item.retrieved_at, field_name="retrieved_at")
        for ts_field in ("quote_timestamp", "trade_timestamp"):
            value = getattr(item, ts_field)
            if value is not None:
                _require_rfc3339_timestamp(value, field_name=ts_field)

        _require_decimal(
            item.strike_price, field_name="strike_price", allow_none=False,
            allow_negative=False, require_positive=True,
        )
        _require_decimal(
            item.bid_price, field_name="bid_price", allow_none=True, allow_negative=False
        )
        _require_decimal(
            item.ask_price, field_name="ask_price", allow_none=True, allow_negative=False
        )
        _require_decimal(
            item.trade_price, field_name="trade_price", allow_none=True, allow_negative=False
        )
        _require_decimal(
            item.implied_volatility, field_name="implied_volatility", allow_none=True,
            allow_negative=False, scale=_GREEK_SCALE, max_abs=_GREEK_MAX_ABS,
        )
        for greek_field, allow_negative in (
            ("delta", True), ("gamma", False), ("theta", True), ("vega", False), ("rho", True),
        ):
            _require_decimal(
                getattr(item, greek_field), field_name=greek_field, allow_none=True,
                allow_negative=allow_negative, scale=_GREEK_SCALE, max_abs=_GREEK_MAX_ABS,
            )

        _require_nonneg_int(item.bid_size, field_name="bid_size", allow_none=True)
        _require_nonneg_int(item.ask_size, field_name="ask_size", allow_none=True)
        _require_nonneg_int(item.trade_size, field_name="trade_size", allow_none=True)


def _resolve_batch_retrieved_at(
    items: Sequence[OptionChainSnapshot], retrieved_at: object
) -> str:
    """Return the single RFC3339 retrieval instant for this batch.

    Every snapshot in one connector retrieval shares one ``retrieved_at``.
    When ``retrieved_at`` is supplied it must be a valid RFC3339 timestamp
    and must match every item's ``retrieved_at``. When it is ``None`` it is
    taken from the items -- which therefore must be non-empty and must all
    agree. An empty batch with no explicit ``retrieved_at`` is rejected: the
    run-level batch row needs a retrieval instant. Never echoes raw input.
    """
    if retrieved_at is not None:
        _require_rfc3339_timestamp(retrieved_at, field_name="retrieved_at")
        resolved = retrieved_at
    elif not items:
        raise OptionChainSnapshotStorageValidationError(
            "Invalid retrieved_at: an explicit retrieval timestamp is required "
            "for an empty batch."
        )
    else:
        resolved = items[0].retrieved_at
        _require_rfc3339_timestamp(resolved, field_name="retrieved_at")

    for item in items:
        if item.retrieved_at != resolved:
            raise OptionChainSnapshotStorageValidationError(
                "Invalid items: every snapshot must share the batch retrieved_at."
            )
    return str(resolved)


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None)


def _parse_timestamp(value: str) -> datetime:
    from market_intelligence.data_connectors.alpaca_options_chain import _parse_snapshot_timestamp

    normalized = _parse_snapshot_timestamp(value)
    return _to_naive_utc(datetime.fromisoformat(normalized.replace("Z", "+00:00")))


def _now_naive_utc() -> datetime:
    return _to_naive_utc(datetime.now(UTC))


# The mutable value columns compared for idempotency on option_chain_snapshots.
# Request-level fields are deliberately absent: they live once on the
# option_chain_snapshot_batches row this snapshot's ingestion_run_id points to.
_VALUE_COLUMNS = (
    "expiration_date", "option_type", "strike_price",
    "quote_timestamp", "bid_price", "bid_size", "ask_price", "ask_size",
    "trade_timestamp", "trade_price", "trade_size",
    "implied_volatility", "delta", "gamma", "theta", "vega", "rho",
)

_BATCH_COLUMNS = (
    "ingestion_run_id", "provider", "underlying", "requested_feed",
    "requested_expiration_date_gte", "requested_expiration_date_lte",
    "requested_strike_price_gte", "requested_strike_price_lte", "requested_option_type",
    "retrieved_at", "contract_count", "outcome", "first_ingested_at", "last_seen_at",
)


class OptionChainSnapshotRepository:
    """Persists normalized ``OptionChainSnapshot`` objects into DuckDB."""

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

    def store_snapshots(
        self,
        items: Sequence[OptionChainSnapshot],
        *,
        request: OptionChainRequest,
        retrieved_at: str | None = None,
        provider: str = DEFAULT_PROVIDER,
        dataset_name: str = DEFAULT_DATASET_NAME,
    ) -> OptionChainSnapshotStorageResult:
        """Store one bounded, already-retrieved SPY option chain.

        ``items`` are the already-normalized ``OptionChainSnapshot`` objects
        from one ``AlpacaOptionsChainClient.get_chain_snapshot`` call; it may
        be empty for a successful retrieval that returned zero contracts.
        ``retrieved_at`` is that call's single UTC retrieval instant -- it is
        required when ``items`` is empty and, when supplied for a non-empty
        batch, must match every item; when omitted for a non-empty batch it
        is taken from the items (which must all agree).

        Validates every item, ``request`` and ``retrieved_at`` before any
        database write (raising ``OptionChainSnapshotStorageValidationError``
        and writing nothing if validation fails). Otherwise records a
        ``running`` ``ingestion_runs`` row, then -- inside one DuckDB
        transaction -- writes exactly one ``option_chain_snapshot_batches``
        row (run-level provenance and the contract count, ``0`` included),
        every snapshot row, and the final ``succeeded`` status update. An
        unseen ``(provider, underlying, feed, contract_symbol, retrieved_at)``
        identity is inserted; an already-known identity whose stored values
        match has its last-seen/run provenance refreshed; an already-known
        identity whose values conflict aborts the entire batch (no batch row,
        no partial snapshot rows). A conflict or storage failure is reported
        truthfully via the returned result (run status ``failed``,
        ``batch_outcome`` ``"failed"``); only a failure to record that
        ``failed`` status itself raises ``OptionChainSnapshotStorageError``.
        """
        validated_request = _validate_request(request)
        _validate_items(items, provider=provider, request=validated_request)
        resolved_retrieved_at = _resolve_batch_retrieved_at(items, retrieved_at)
        received = len(items)
        batch_outcome = "skipped_empty" if received == 0 else "succeeded"
        _require_non_blank_str(provider, field_name="provider")
        _require_non_blank_str(dataset_name, field_name="dataset_name")

        run_id = str(uuid.uuid4())
        started_at = _now_naive_utc()

        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise OptionChainSnapshotStorageError(
                "Failed to open the local database connection before any ingestion run "
                "could be recorded."
            ) from None

        try:
            try:
                schema_version = self._current_schema_version(connection)
                connection.execute(
                    "INSERT INTO ingestion_runs "
                    "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
                    "schema_version) VALUES (?, ?, ?, ?, 'running', ?, ?)",
                    [run_id, provider, dataset_name, started_at, CODE_VERSION, schema_version],
                )
            except Exception:
                raise OptionChainSnapshotStorageError(
                    "Failed to record a running ingestion run before any batch write."
                ) from None

            try:
                connection.execute("BEGIN TRANSACTION")
                self._store_batch_record(
                    connection, request=validated_request, provider=provider,
                    retrieved_at=resolved_retrieved_at, contract_count=received,
                    outcome=batch_outcome, run_id=run_id,
                )
                inserted = 0
                existing_or_updated = 0
                for item in items:
                    if self._store_one(connection, item, run_id=run_id):
                        inserted += 1
                    else:
                        existing_or_updated += 1
                self._complete_run(
                    connection, run_id=run_id, status="succeeded",
                    records_received=received, error_category=None,
                )
                connection.execute("COMMIT")
            except Exception as exc:
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    raise OptionChainSnapshotStorageError(
                        "Failed to roll back the batch after a storage error."
                    ) from None
                error_category = (
                    "content_conflict"
                    if isinstance(exc, OptionChainSnapshotStorageConflictError)
                    else "storage_error"
                )
                try:
                    self._complete_run(
                        connection, run_id=run_id, status="failed",
                        records_received=received, error_category=error_category,
                    )
                except Exception:
                    raise OptionChainSnapshotStorageError(
                        "Failed to record the ingestion run as failed after a storage error."
                    ) from None
                return OptionChainSnapshotStorageResult(
                    received=received, inserted=0, existing_or_updated=0, failed=received,
                    ingestion_run_id=run_id, ingestion_run_status="failed",
                    batch_outcome="failed",
                )
        finally:
            try:
                connection.close()
            except Exception:
                pass

        return OptionChainSnapshotStorageResult(
            received=received, inserted=inserted, existing_or_updated=existing_or_updated,
            failed=0, ingestion_run_id=run_id, ingestion_run_status="succeeded",
            batch_outcome=batch_outcome,
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

    @staticmethod
    def _store_batch_record(
        connection: duckdb.DuckDBPyConnection,
        *,
        request: OptionChainRequest,
        provider: str,
        retrieved_at: str,
        contract_count: int,
        outcome: str,
        run_id: str,
    ) -> None:
        """Insert the one run-level provenance row for this stored retrieval.

        Keyed on ``ingestion_run_id`` (a fresh id per store call), so this is
        always a plain insert. Records the requested feed, the bounded
        expiration/strike window, the requested option type, the UTC
        retrieval instant and the contract count (``0`` for a successful
        empty chain). No raw payload, URL, token or credential.
        """
        now = _now_naive_utc()
        values = {
            "ingestion_run_id": run_id,
            "provider": provider,
            "underlying": request.underlying,
            "requested_feed": request.feed,
            "requested_expiration_date_gte": request.expiration_date_gte,
            "requested_expiration_date_lte": request.expiration_date_lte,
            "requested_strike_price_gte": request.strike_price_gte,
            "requested_strike_price_lte": request.strike_price_lte,
            "requested_option_type": request.option_type,
            "retrieved_at": _parse_timestamp(retrieved_at),
            "contract_count": contract_count,
            "outcome": outcome,
            "first_ingested_at": now,
            "last_seen_at": now,
        }
        placeholders = ", ".join(["?"] * len(_BATCH_COLUMNS))
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batches ({', '.join(_BATCH_COLUMNS)}) "
            f"VALUES ({placeholders})",
            [values[column] for column in _BATCH_COLUMNS],
        )

    def _store_one(
        self,
        connection: duckdb.DuckDBPyConnection,
        item: OptionChainSnapshot,
        *,
        run_id: str,
    ) -> bool:
        """Insert or refresh one snapshot. Returns True if inserted, False if refreshed.

        Raises ``OptionChainSnapshotStorageConflictError`` if an existing
        row's stored values conflict with the incoming snapshot.
        """
        retrieved_at = _parse_timestamp(item.retrieved_at)
        quote_ts = _parse_timestamp(item.quote_timestamp) if item.quote_timestamp else None
        trade_ts = _parse_timestamp(item.trade_timestamp) if item.trade_timestamp else None
        now = _now_naive_utc()

        incoming = {
            "expiration_date": item.expiration_date,
            "option_type": item.option_type,
            "strike_price": item.strike_price,
            "quote_timestamp": quote_ts,
            "bid_price": item.bid_price,
            "bid_size": item.bid_size,
            "ask_price": item.ask_price,
            "ask_size": item.ask_size,
            "trade_timestamp": trade_ts,
            "trade_price": item.trade_price,
            "trade_size": item.trade_size,
            "implied_volatility": item.implied_volatility,
            "delta": item.delta,
            "gamma": item.gamma,
            "theta": item.theta,
            "vega": item.vega,
            "rho": item.rho,
        }

        existing = connection.execute(
            f"SELECT {', '.join(_VALUE_COLUMNS)} FROM option_chain_snapshots "
            "WHERE provider = ? AND underlying = ? AND feed = ? AND contract_symbol = ? "
            "AND retrieved_at = ?",
            [item.provider, item.underlying, item.feed, item.contract_symbol, retrieved_at],
        ).fetchone()

        if existing is None:
            columns = (
                ["provider", "underlying", "feed", "contract_symbol", "retrieved_at"]
                + list(_VALUE_COLUMNS)
                + ["first_ingested_at", "last_seen_at", "ingestion_run_id"]
            )
            placeholders = ", ".join(["?"] * len(columns))
            values = (
                [item.provider, item.underlying, item.feed, item.contract_symbol, retrieved_at]
                + [incoming[column] for column in _VALUE_COLUMNS]
                + [now, now, run_id]
            )
            connection.execute(
                f"INSERT INTO option_chain_snapshots ({', '.join(columns)}) "
                f"VALUES ({placeholders})",
                values,
            )
            return True

        for column, stored in zip(_VALUE_COLUMNS, existing, strict=True):
            if not _values_equal(stored, incoming[column]):
                raise OptionChainSnapshotStorageConflictError(
                    "Conflicting stored values for an existing option-snapshot identity; "
                    "refusing to overwrite."
                )

        connection.execute(
            "UPDATE option_chain_snapshots SET last_seen_at = ?, ingestion_run_id = ? "
            "WHERE provider = ? AND underlying = ? AND feed = ? AND contract_symbol = ? "
            "AND retrieved_at = ?",
            [now, run_id, item.provider, item.underlying, item.feed, item.contract_symbol,
             retrieved_at],
        )
        return False


def _values_equal(stored: object, incoming: object) -> bool:
    """Compare a stored DuckDB column value against an incoming Python value.

    Handles the type round-tripping DuckDB applies: ``DATE`` columns come
    back as ``datetime.date`` while the incoming value is an ``YYYY-MM-DD``
    string, and ``DECIMAL`` columns come back as ``Decimal``.
    """
    if stored is None or incoming is None:
        return stored is None and incoming is None
    if isinstance(incoming, str) and hasattr(stored, "isoformat") and not isinstance(
        stored, datetime
    ):
        return stored.isoformat() == incoming
    if isinstance(stored, datetime) and isinstance(incoming, datetime):
        return stored == incoming
    if isinstance(stored, Decimal) and isinstance(incoming, Decimal):
        return stored == incoming
    if isinstance(stored, float) and isinstance(incoming, Decimal):
        if not math.isfinite(stored):
            return False
        return Decimal(str(stored)) == incoming
    return stored == incoming
