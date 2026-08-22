"""Macro-observation storage: persists normalized ``FredObservation`` objects into DuckDB.

This module never makes network requests and never accepts, stores, or logs
credentials. It writes only to the ``macro_observations`` table (migration
``0006``, see ``market_intelligence/storage/migrations/``) and the existing
``ingestion_runs`` bookkeeping table -- never prediction, direction,
sentiment, impact, recommendation, option-contract, order, execution, or
other derived-feature fields, request headers, or raw API responses. It
assumes the database has already been brought up to at least migration
``0006`` (e.g. via ``DuckDBManager.initialize()``); this class only writes
rows, it never applies migrations itself.

Every batch -- and the final ``succeeded`` ``ingestion_runs`` status update
-- is written inside a single DuckDB transaction: if any item in the batch
cannot be safely stored (invalid input, or an existing observation identity
whose value/is_missing conflicts with the incoming values), or the final
``succeeded`` status update itself fails, nothing in that batch is
persisted, and the corresponding ``ingestion_runs`` row is separately
recorded as ``failed`` with a sanitized error category -- never a raw
exception message, and never observation values, timestamps, or
credentials. Stored observation changes are never left associated with a
``running`` or ``failed`` run. If rollback itself fails, or recording the
``failed`` status itself also fails, a sanitized ``MacroObservationStorageError``
is raised without leaking the original or rollback exception.

Any failure before a durable ``running`` ``ingestion_runs`` row exists --
opening the DuckDB connection, reading the current schema version, or
inserting the initial ``running`` row -- also raises a sanitized
``MacroObservationStorageError`` directly; no raw DuckDB exception, SQL
text, database path, credential, observation value, or timestamp ever
escapes ``store_observations``. Connection cleanup can never replace a
sanitized error (or a successful result) with a raw connection-close
exception.

Duplicate ``FredObservation`` objects within a single incoming batch are
handled deterministically without any special-cased duplicate-tracking
code: every observation is written through the same DuckDB connection
inside one transaction, so a second occurrence of the same identity later
in the same batch sees the first occurrence's not-yet-committed row exactly
as it would see an already-committed row from a prior call -- it is either
an exact match (counted as ``existing_or_updated``) or a conflict (rolls
back the whole batch), never a silent double-insert.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FredInvalidObservationRequestError,
    FredInvalidSeriesIdError,
    FredObservation,
    normalize_observation_date,
    normalize_series_id,
)
from market_intelligence.storage.database import DuckDBManager

CODE_VERSION = "macro_observation_repository_v1"
DEFAULT_DATASET_NAME = "macro_observations"
DEFAULT_PROVIDER = "fred"

# Matches the macro_observations schema's value column (migration 0006:
# DECIMAL(20,6) -- 14 integer digits, 6 fractional digits). This is a
# deliberately chosen, bounded supported range for this project's
# currently-ingested series -- not a claim of universal coverage of every
# value any FRED series could ever report. A value with more than 6
# fractional digits would be silently rounded by DuckDB, and a value outside
# this magnitude would silently overflow; both are rejected here before any
# write rather than allowed to happen inside DuckDB.
_DECIMAL_SCALE = 6
_DECIMAL_MAX_ABS = Decimal("99999999999999.999999")

MAX_TIMESTAMP_LENGTH = 40
_RFC3339_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])"
    r"[Tt](?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)(?:\.\d+)?"
    r"(?P<offset>[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


class MacroObservationStorageError(RuntimeError):
    """Sanitized storage failure.

    Never includes observation values, timestamps, database internals, or
    credentials.
    """


class MacroObservationStorageValidationError(MacroObservationStorageError):
    """Raised when supplied items fail validation before any database write.

    No ``ingestion_runs`` row is created for this failure mode -- it
    indicates a caller/programming error (malformed input), not a data or
    infrastructure failure to record for audit purposes.
    """


class MacroObservationStorageConflictError(MacroObservationStorageError):
    """Raised internally when an incoming observation conflicts with a stored observation.

    Caught by ``store_observations`` to trigger a full-batch rollback; never
    propagated to callers directly.
    """


@dataclass(frozen=True)
class MacroObservationStorageResult:
    """Sanitized result of one ``store_observations`` call.

    Never includes observation values, timestamps, database internals, or
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
        raise MacroObservationStorageValidationError(
            f"Invalid items: {field_name} must be a non-blank string."
        )


def _require_provider_is_fred(value: object) -> str:
    """Require ``value`` to be exactly ``DEFAULT_PROVIDER`` ("fred").

    Rejects any alternate, blank, malformed, or non-string provider value
    before any connection is opened, any ``ingestion_runs`` row is written,
    or any observation is written. The rejected value is never echoed in
    the error message.
    """
    if not isinstance(value, str) or value != DEFAULT_PROVIDER:
        raise MacroObservationStorageValidationError(
            f'Invalid items: provider must be exactly "{DEFAULT_PROVIDER}".'
        )
    return value


def _require_normalized_series_id(value: object) -> str:
    if not isinstance(value, str):
        raise MacroObservationStorageValidationError("Invalid items: series_id must be a string.")
    try:
        normalized = normalize_series_id(value)
    except FredInvalidSeriesIdError as exc:
        raise MacroObservationStorageValidationError(f"Invalid items: {exc}") from None
    if normalized != value:
        raise MacroObservationStorageValidationError(
            "Invalid items: series_id must already be normalized."
        )
    return normalized


def _require_normalized_date(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise MacroObservationStorageValidationError(
            f"Invalid items: {field_name} must be a calendar date string."
        )
    try:
        normalized = normalize_observation_date(value, field_name=field_name)
    except FredInvalidObservationRequestError as exc:
        raise MacroObservationStorageValidationError(f"Invalid items: {exc}") from None
    if normalized != value:
        raise MacroObservationStorageValidationError(
            f"Invalid items: {field_name} must already be normalized."
        )
    return normalized


def _validate_decimal_storage_precision(value: Decimal, *, field_name: str) -> None:
    """Reject a Decimal that DuckDB's DECIMAL(20,6) column would silently round or overflow.

    Never echoes the rejected value. Trailing zeros beyond the 6th
    fractional digit do not require rounding (they are normalized away
    before the fractional-digit check), but any value with more than 6
    significant fractional digits, or a magnitude the column cannot hold,
    is rejected.
    """
    normalized_exponent = value.normalize().as_tuple().exponent
    if isinstance(normalized_exponent, str):
        raise MacroObservationStorageValidationError(
            f"Invalid items: {field_name} is not a supported value."
        )
    if normalized_exponent < -_DECIMAL_SCALE:
        raise MacroObservationStorageValidationError(
            f"Invalid items: {field_name} exceeds the maximum supported decimal precision."
        )
    if abs(value) > _DECIMAL_MAX_ABS:
        raise MacroObservationStorageValidationError(
            f"Invalid items: {field_name} exceeds the maximum supported decimal range."
        )


def _require_rfc3339_timestamp(value: object, *, field_name: str) -> datetime:
    """Require ``value`` to be a strict RFC3339 timestamp (explicit Z/offset).

    Returns the parsed value as a naive UTC ``datetime``. Rejects naive,
    date-only, malformed, blank, and non-string timestamps. Never echoes
    the raw input value.
    """
    message = f"Invalid items: {field_name} must be a valid RFC3339 timestamp."
    if isinstance(value, bool) or not isinstance(value, str):
        raise MacroObservationStorageValidationError(message)

    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_TIMESTAMP_LENGTH:
        raise MacroObservationStorageValidationError(message)

    match = _RFC3339_PATTERN.match(trimmed)
    if match is None:
        raise MacroObservationStorageValidationError(message)

    offset = match.group("offset")
    candidate = trimmed[:-1] + "+00:00" if offset in ("Z", "z") else trimmed

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise MacroObservationStorageValidationError(message) from None

    if parsed.tzinfo is None:
        raise MacroObservationStorageValidationError(message)

    return parsed.astimezone(UTC).replace(tzinfo=None)


def _validate_items(items: Sequence[FredObservation], *, provider: str) -> None:
    if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
        raise MacroObservationStorageValidationError(
            "Invalid items: expected a sequence of FredObservation."
        )

    for item in items:
        if not isinstance(item, FredObservation):
            raise MacroObservationStorageValidationError(
                "Invalid items: every item must be a FredObservation."
            )
        if item.provider != provider:
            raise MacroObservationStorageValidationError(
                "Invalid items: every item's provider must match the requested provider."
            )
        _require_non_blank_str(item.provider, field_name="provider")
        _require_normalized_series_id(item.series_id)
        _require_normalized_date(item.observation_date, field_name="observation_date")
        _require_normalized_date(item.realtime_start, field_name="realtime_start")
        _require_normalized_date(item.realtime_end, field_name="realtime_end")

        if item.is_missing:
            if item.value is not None:
                raise MacroObservationStorageValidationError(
                    "Invalid items: a missing observation must have value=None."
                )
        else:
            if not isinstance(item.value, Decimal):
                raise MacroObservationStorageValidationError(
                    "Invalid items: a present observation must have a Decimal value."
                )
            if not item.value.is_finite():
                raise MacroObservationStorageValidationError(
                    "Invalid items: value must be a finite value."
                )
            _validate_decimal_storage_precision(item.value, field_name="value")

        _require_rfc3339_timestamp(item.retrieved_at, field_name="retrieved_at")


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MacroObservationRepository:
    """Persists normalized ``FredObservation`` objects into the ``macro_observations`` table."""

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

    def store_observations(
        self,
        items: Sequence[FredObservation],
        *,
        provider: str = DEFAULT_PROVIDER,
        dataset_name: str = DEFAULT_DATASET_NAME,
    ) -> MacroObservationStorageResult:
        """Store a batch of already-normalized ``FredObservation`` objects.

        ``provider`` must be exactly ``DEFAULT_PROVIDER`` ("fred") -- any
        alternate, blank, malformed, or non-string value is rejected with a
        sanitized ``MacroObservationStorageValidationError`` before any
        connection is opened, any ``ingestion_runs`` row is written, or any
        observation is written, and the rejected value is never echoed. This
        is a fixed constant, not caller-configurable data: every stored
        observation is likewise required to have ``provider == "fred"``.

        Validates every item before any database write (raising
        ``MacroObservationStorageValidationError`` and writing nothing if
        validation fails); an empty batch is rejected the same way, since
        there is no meaningful ingestion run to record for zero
        observations. Otherwise records a ``running`` ``ingestion_runs``
        row, then writes the whole batch -- plus the final ``succeeded``
        ``ingestion_runs`` status update -- inside one DuckDB transaction:
        an unseen observation identity is inserted; an already-known
        observation identity whose value/is_missing matches has its
        retrieval/last-seen/run provenance refreshed; an already-known
        observation identity whose value/is_missing conflicts aborts the
        entire batch. If any observation write, or the final ``succeeded``
        status update itself, fails, the whole transaction is rolled back
        (no observation changes are left associated with the run) and the
        run is separately recorded as ``failed`` with a sanitized error
        category, so stored observation changes are never left attached to
        a ``running`` or ``failed`` run. Never raises for a conflict or
        storage failure -- both are reported truthfully via the returned
        ``MacroObservationStorageResult`` -- unless recording the ``failed``
        status itself also fails, in which case a sanitized
        ``MacroObservationStorageError`` is raised.

        Any failure before a durable ``running`` ``ingestion_runs`` row
        exists -- opening the connection, reading the schema version, or
        inserting that row -- raises a sanitized
        ``MacroObservationStorageError`` directly, since there is no
        durable run to mark ``failed``. If rolling back the batch
        transaction itself fails, a sanitized ``MacroObservationStorageError``
        is likewise raised without attempting to record a ``failed``
        status.
        """
        if isinstance(items, (str, bytes)) or not isinstance(items, Sequence):
            raise MacroObservationStorageValidationError(
                "Invalid items: expected a sequence of FredObservation."
            )
        if len(items) == 0:
            raise MacroObservationStorageValidationError("Invalid items: batch must not be empty.")

        _require_provider_is_fred(provider)
        _validate_items(items, provider=provider)
        received = len(items)
        _require_non_blank_str(dataset_name, field_name="dataset_name")

        run_id = str(uuid.uuid4())
        started_at = _now_naive_utc()

        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise MacroObservationStorageError(
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
                raise MacroObservationStorageError(
                    "Failed to record a running ingestion run before any batch write."
                ) from None

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
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    raise MacroObservationStorageError(
                        "Failed to roll back the batch after a storage error."
                    ) from None
                error_category = (
                    "content_conflict"
                    if isinstance(exc, MacroObservationStorageConflictError)
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
                    raise MacroObservationStorageError(
                        "Failed to record the ingestion run as failed after a storage error."
                    ) from None
                return MacroObservationStorageResult(
                    received=received,
                    inserted=0,
                    existing_or_updated=0,
                    failed=received,
                    ingestion_run_id=run_id,
                    ingestion_run_status="failed",
                )
        finally:
            try:
                connection.close()
            except Exception:
                pass

        return MacroObservationStorageResult(
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
        item: FredObservation,
        *,
        run_id: str,
    ) -> bool:
        """Insert or refresh one observation. Returns True if inserted, False if refreshed.

        Raises ``MacroObservationStorageConflictError`` if an existing row's
        value/is_missing conflicts with the incoming observation.
        """
        observation_date = date.fromisoformat(item.observation_date)
        realtime_start = date.fromisoformat(item.realtime_start)
        realtime_end = date.fromisoformat(item.realtime_end)
        retrieved_at = _require_rfc3339_timestamp(item.retrieved_at, field_name="retrieved_at")
        now = _now_naive_utc()

        existing = connection.execute(
            "SELECT value, is_missing FROM macro_observations "
            "WHERE provider = ? AND series_id = ? AND observation_date = ? "
            "AND realtime_start = ? AND realtime_end = ?",
            [item.provider, item.series_id, observation_date, realtime_start, realtime_end],
        ).fetchone()

        if existing is None:
            connection.execute(
                "INSERT INTO macro_observations ("
                "provider, series_id, observation_date, realtime_start, realtime_end, value, "
                "is_missing, retrieved_at, first_ingested_at, last_seen_at, ingestion_run_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item.provider,
                    item.series_id,
                    observation_date,
                    realtime_start,
                    realtime_end,
                    item.value,
                    item.is_missing,
                    retrieved_at,
                    now,
                    now,
                    run_id,
                ],
            )
            return True

        existing_value, existing_is_missing = existing
        if existing_value != item.value or existing_is_missing != item.is_missing:
            raise MacroObservationStorageConflictError(
                "Conflicting value/is_missing for an existing observation identity; "
                "refusing to overwrite."
            )

        connection.execute(
            "UPDATE macro_observations SET retrieved_at = ?, last_seen_at = ?, "
            "ingestion_run_id = ? WHERE provider = ? AND series_id = ? "
            "AND observation_date = ? AND realtime_start = ? AND realtime_end = ?",
            [
                retrieved_at,
                now,
                run_id,
                item.provider,
                item.series_id,
                observation_date,
                realtime_start,
                realtime_end,
            ],
        )
        return False
