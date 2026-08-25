"""Macro series-metadata storage: persists normalized ``FredSeriesMetadata`` objects into DuckDB.

This module never makes network requests and never accepts, stores, or logs
credentials. It writes only to the ``macro_series_metadata`` table
(migration ``0008``, see ``market_intelligence/storage/migrations/``) and the
existing ``ingestion_runs`` bookkeeping table -- never prediction, direction,
sentiment, impact, recommendation, option-contract, order, execution,
request headers, or raw API responses. It assumes the database has already
been brought up to at least migration ``0008`` (e.g. via
``DuckDBManager.initialize()``); this class only writes rows, it never
applies migrations itself.

Unlike ``macro_observations`` (see
``market_intelligence/storage/macro_observation_repository.py``), series
metadata has no revision/vintage window to preserve -- FRED's series
endpoint always reports the current metadata for a series. A repeat
ingestion of an already-known ``(provider, series_id)`` therefore always
refreshes every mutable metadata/provenance column in place; it is never
rejected as a conflict, since a series' title, units, popularity, or notes
may legitimately change over time from FRED's own perspective.

The single-item write -- and the final ``succeeded`` ``ingestion_runs``
status update -- is written inside a single DuckDB transaction: if the write
cannot be safely stored (invalid input), or the final ``succeeded`` status
update itself fails, nothing is persisted, and the corresponding
``ingestion_runs`` row is separately recorded as ``failed`` with a sanitized
error category -- never a raw exception message, and never metadata field
values, provider-reported free text, or credentials. Stored metadata changes
are never left associated with a ``running`` or ``failed`` run. If rollback
itself fails, or recording the ``failed`` status itself also fails, a
sanitized ``MacroSeriesMetadataStorageError`` is raised without leaking the
original or rollback exception.

Any failure before a durable ``running`` ``ingestion_runs`` row exists --
opening the DuckDB connection, reading the current schema version, or
inserting the initial ``running`` row -- also raises a sanitized
``MacroSeriesMetadataStorageError`` directly; no raw DuckDB exception, SQL
text, database path, credential, provider-reported free text, or timestamp
ever escapes ``store_metadata``. Connection cleanup can never replace a
sanitized error (or a successful result) with a raw connection-close
exception.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FredInvalidObservationRequestError,
    FredInvalidSeriesIdError,
    FredSeriesMetadata,
    normalize_observation_date,
    normalize_series_id,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_observation_repository import (
    _RFC3339_PATTERN,
    MAX_TIMESTAMP_LENGTH,
)

CODE_VERSION = "macro_series_metadata_repository_v1"
DEFAULT_DATASET_NAME = "macro_series_metadata"
DEFAULT_PROVIDER = "fred"

_METADATA_STRING_FIELDS = (
    "title",
    "frequency",
    "frequency_short",
    "units",
    "units_short",
    "seasonal_adjustment",
    "seasonal_adjustment_short",
)


class MacroSeriesMetadataStorageError(RuntimeError):
    """Sanitized storage failure.

    Never includes provider-reported free text (title, notes), other
    metadata field values, database internals, or credentials.
    """


class MacroSeriesMetadataStorageValidationError(MacroSeriesMetadataStorageError):
    """Raised when a supplied item fails validation before any database write.

    No ``ingestion_runs`` row is created for this failure mode -- it
    indicates a caller/programming error (malformed input), not a data or
    infrastructure failure to record for audit purposes.
    """


@dataclass(frozen=True)
class MacroSeriesMetadataStorageResult:
    """Sanitized result of one ``store_metadata`` call.

    Never includes provider-reported free text, other metadata field
    values, database internals, or credentials.
    """

    inserted: bool
    ingestion_run_id: str
    ingestion_run_status: str


def _require_non_blank_str(value: object, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise MacroSeriesMetadataStorageValidationError(
            f"Invalid item: {field_name} must be a non-blank string."
        )


def _require_provider_is_fred(value: object) -> str:
    """Require ``value`` to be exactly ``DEFAULT_PROVIDER`` ("fred").

    Rejects any alternate, blank, malformed, or non-string provider value
    before any connection is opened, any ``ingestion_runs`` row is written,
    or any metadata is written. The rejected value is never echoed in the
    error message.
    """
    if not isinstance(value, str) or value != DEFAULT_PROVIDER:
        raise MacroSeriesMetadataStorageValidationError(
            f'Invalid item: provider must be exactly "{DEFAULT_PROVIDER}".'
        )
    return value


def _require_normalized_series_id(value: object) -> str:
    if not isinstance(value, str):
        raise MacroSeriesMetadataStorageValidationError("Invalid item: series_id must be a string.")
    try:
        normalized = normalize_series_id(value)
    except FredInvalidSeriesIdError as exc:
        raise MacroSeriesMetadataStorageValidationError(f"Invalid item: {exc}") from None
    if normalized != value:
        raise MacroSeriesMetadataStorageValidationError(
            "Invalid item: series_id must already be normalized."
        )
    return normalized


def _require_normalized_date(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise MacroSeriesMetadataStorageValidationError(
            f"Invalid item: {field_name} must be a calendar date string."
        )
    try:
        normalized = normalize_observation_date(value, field_name=field_name)
    except FredInvalidObservationRequestError as exc:
        raise MacroSeriesMetadataStorageValidationError(f"Invalid item: {exc}") from None
    if normalized != value:
        raise MacroSeriesMetadataStorageValidationError(
            f"Invalid item: {field_name} must already be normalized."
        )
    return normalized


def _require_popularity(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MacroSeriesMetadataStorageValidationError(
            "Invalid item: popularity must be a nonnegative integer."
        )
    return value


def _require_notes(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        raise MacroSeriesMetadataStorageValidationError(
            "Invalid item: notes must be a string or null."
        )
    return value


def _require_rfc3339_timestamp(value: object, *, field_name: str) -> datetime:
    """Require ``value`` to be a strict RFC3339 timestamp (explicit Z/offset).

    Returns the parsed value as a naive UTC ``datetime``. Rejects naive,
    date-only, malformed, blank, and non-string timestamps. Never echoes
    the raw input value.
    """
    message = f"Invalid item: {field_name} must be a valid RFC3339 timestamp."
    if isinstance(value, bool) or not isinstance(value, str):
        raise MacroSeriesMetadataStorageValidationError(message)

    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_TIMESTAMP_LENGTH:
        raise MacroSeriesMetadataStorageValidationError(message)

    match = _RFC3339_PATTERN.match(trimmed)
    if match is None:
        raise MacroSeriesMetadataStorageValidationError(message)

    offset = match.group("offset")
    candidate = trimmed[:-1] + "+00:00" if offset in ("Z", "z") else trimmed

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise MacroSeriesMetadataStorageValidationError(message) from None

    if parsed.tzinfo is None:
        raise MacroSeriesMetadataStorageValidationError(message)

    return parsed.astimezone(UTC).replace(tzinfo=None)


def _validate_item(item: FredSeriesMetadata, *, provider: str) -> None:
    if not isinstance(item, FredSeriesMetadata):
        raise MacroSeriesMetadataStorageValidationError(
            "Invalid item: expected a FredSeriesMetadata."
        )
    if item.provider != provider:
        raise MacroSeriesMetadataStorageValidationError(
            "Invalid item: item's provider must match the requested provider."
        )
    _require_non_blank_str(item.provider, field_name="provider")
    _require_normalized_series_id(item.series_id)

    for field_name in _METADATA_STRING_FIELDS:
        _require_non_blank_str(getattr(item, field_name), field_name=field_name)

    _require_normalized_date(item.observation_start, field_name="observation_start")
    _require_normalized_date(item.observation_end, field_name="observation_end")
    _require_popularity(item.popularity)
    _require_notes(item.notes)
    _require_rfc3339_timestamp(item.last_updated, field_name="last_updated")
    _require_rfc3339_timestamp(item.retrieved_at_utc, field_name="retrieved_at_utc")


def _now_naive_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class MacroSeriesMetadataRepository:
    """Persists a normalized ``FredSeriesMetadata`` object into ``macro_series_metadata``."""

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

    def store_metadata(
        self,
        item: FredSeriesMetadata,
        *,
        provider: str = DEFAULT_PROVIDER,
        dataset_name: str = DEFAULT_DATASET_NAME,
    ) -> MacroSeriesMetadataStorageResult:
        """Store one already-normalized ``FredSeriesMetadata`` object.

        ``provider`` must be exactly ``DEFAULT_PROVIDER`` ("fred") -- any
        alternate, blank, malformed, or non-string value is rejected with a
        sanitized ``MacroSeriesMetadataStorageValidationError`` before any
        connection is opened, any ``ingestion_runs`` row is written, or any
        metadata is written, and the rejected value is never echoed. The
        entire item is likewise fully validated before any database write
        (raising ``MacroSeriesMetadataStorageValidationError`` and writing
        nothing if validation fails).

        Otherwise records a ``running`` ``ingestion_runs`` row, then writes
        the item -- plus the final ``succeeded`` ``ingestion_runs`` status
        update -- inside one DuckDB transaction: an unseen
        ``(provider, series_id)`` identity is inserted; an already-known
        identity has every mutable metadata/provenance column refreshed in
        place (never rejected as a conflict -- series metadata is expected
        to change over time). If the write, or the final ``succeeded``
        status update itself, fails, the whole transaction is rolled back
        (nothing is left associated with the run) and the run is separately
        recorded as ``failed`` with a sanitized error category, so stored
        metadata changes are never left attached to a ``running`` or
        ``failed`` run. Never raises for a storage failure -- it is reported
        truthfully via the returned ``MacroSeriesMetadataStorageResult`` --
        unless recording the ``failed`` status itself also fails, in which
        case a sanitized ``MacroSeriesMetadataStorageError`` is raised.

        Any failure before a durable ``running`` ``ingestion_runs`` row
        exists -- opening the connection, reading the schema version, or
        inserting that row -- raises a sanitized
        ``MacroSeriesMetadataStorageError`` directly, since there is no
        durable run to mark ``failed``. If rolling back the write
        transaction itself fails, a sanitized
        ``MacroSeriesMetadataStorageError`` is likewise raised without
        attempting to record a ``failed`` status.
        """
        _require_provider_is_fred(provider)
        _validate_item(item, provider=provider)
        _require_non_blank_str(dataset_name, field_name="dataset_name")

        run_id = str(uuid.uuid4())
        started_at = _now_naive_utc()

        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise MacroSeriesMetadataStorageError(
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
                raise MacroSeriesMetadataStorageError(
                    "Failed to record a running ingestion run before any metadata write."
                ) from None

            try:
                connection.execute("BEGIN TRANSACTION")
                inserted = self._store_one(connection, item, run_id=run_id)
                self._complete_run(
                    connection,
                    run_id=run_id,
                    status="succeeded",
                    records_received=1,
                    error_category=None,
                )
                connection.execute("COMMIT")
            except Exception:
                try:
                    connection.execute("ROLLBACK")
                except Exception:
                    raise MacroSeriesMetadataStorageError(
                        "Failed to roll back the metadata write after a storage error."
                    ) from None
                try:
                    self._complete_run(
                        connection,
                        run_id=run_id,
                        status="failed",
                        records_received=1,
                        error_category="storage_error",
                    )
                except Exception:
                    raise MacroSeriesMetadataStorageError(
                        "Failed to record the ingestion run as failed after a storage error."
                    ) from None
                return MacroSeriesMetadataStorageResult(
                    inserted=False,
                    ingestion_run_id=run_id,
                    ingestion_run_status="failed",
                )
        finally:
            try:
                connection.close()
            except Exception:
                pass

        return MacroSeriesMetadataStorageResult(
            inserted=inserted,
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
        item: FredSeriesMetadata,
        *,
        run_id: str,
    ) -> bool:
        """Insert or refresh the metadata row. Returns True if inserted, False if refreshed."""
        observation_start = date.fromisoformat(item.observation_start)
        observation_end = date.fromisoformat(item.observation_end)
        last_updated = _require_rfc3339_timestamp(item.last_updated, field_name="last_updated")
        retrieved_at_utc = _require_rfc3339_timestamp(
            item.retrieved_at_utc, field_name="retrieved_at_utc"
        )
        now = _now_naive_utc()

        existing = connection.execute(
            "SELECT 1 FROM macro_series_metadata WHERE provider = ? AND series_id = ?",
            [item.provider, item.series_id],
        ).fetchone()

        if existing is None:
            connection.execute(
                "INSERT INTO macro_series_metadata ("
                "provider, series_id, title, observation_start, observation_end, frequency, "
                "frequency_short, units, units_short, seasonal_adjustment, "
                "seasonal_adjustment_short, last_updated, popularity, notes, retrieved_at_utc, "
                "first_ingested_at, last_seen_at, ingestion_run_id"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    item.provider,
                    item.series_id,
                    item.title,
                    observation_start,
                    observation_end,
                    item.frequency,
                    item.frequency_short,
                    item.units,
                    item.units_short,
                    item.seasonal_adjustment,
                    item.seasonal_adjustment_short,
                    last_updated,
                    item.popularity,
                    item.notes,
                    retrieved_at_utc,
                    now,
                    now,
                    run_id,
                ],
            )
            return True

        connection.execute(
            "UPDATE macro_series_metadata SET title = ?, observation_start = ?, "
            "observation_end = ?, frequency = ?, frequency_short = ?, units = ?, "
            "units_short = ?, seasonal_adjustment = ?, seasonal_adjustment_short = ?, "
            "last_updated = ?, popularity = ?, notes = ?, retrieved_at_utc = ?, "
            "last_seen_at = ?, ingestion_run_id = ? "
            "WHERE provider = ? AND series_id = ?",
            [
                item.title,
                observation_start,
                observation_end,
                item.frequency,
                item.frequency_short,
                item.units,
                item.units_short,
                item.seasonal_adjustment,
                item.seasonal_adjustment_short,
                last_updated,
                item.popularity,
                item.notes,
                retrieved_at_utc,
                now,
                run_id,
                item.provider,
                item.series_id,
            ],
        )
        return False
