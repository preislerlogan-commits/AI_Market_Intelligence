"""Read-only macro-evidence snapshot builder.

``MacroEvidenceBuilder`` produces exactly one deterministic, JSON-ready
snapshot dict from data already stored in the ``macro_observations`` table
(migration ``0006``, see ``market_intelligence/storage/migrations/`` and
``market_intelligence/storage/macro_observation_repository.py``). It is
**infrastructure for a future Macro Analyst agent** -- it is not an AI agent
itself, not a prediction, not a market-regime label, and building a snapshot
never makes a model request of any kind.

It is read-only end to end:

- It makes **no network request** of any kind -- no Alpaca, FRED, OpenAI, or
  Anthropic call. It imports no HTTP client and no model client.
- It opens the local database only with ``duckdb.connect(path, read_only=True)``
  and never writes a row, never applies a migration, and never touches
  ``ingestion_runs`` or any orchestration table.
- The only thing it reuses from ``market_intelligence/data_connectors/`` is
  ``normalize_series_id``/``FredInvalidSeriesIdError`` -- the same small,
  already-reviewed, read-only validation helpers used by
  ``MarketContextBuilder`` -- and the existing ``DuckDBManager``/``Clock``
  infrastructure for safe path resolution and an injectable "as of" instant.
  It never imports a connector's HTTP client, a storage repository's write
  path, or any model client.
- It never labels a series bullish/bearish, never classifies a market
  regime, never infers a rate-cut/hike direction, never predicts, never
  describes a transmission mechanism, and never recommends anything
  (including options language). It performs no transformation,
  interpolation, forward-filling, seasonal adjustment, or derived-change
  calculation -- every value is exactly what FRED reported and this project
  already stored, plus this module's own provenance/coverage/staleness
  bookkeeping about it.

Every public input (the requested FRED series IDs) is strictly validated and
normalized *before* any DuckDB connection is opened, so malformed or
malicious input never reaches storage. A missing database file, a missing
``macro_observations`` table, or a series with no stored observation are all
valid, non-error input -- this module always returns a truthful,
non-crashing snapshot with that series reported ``has_stored_observation:
false`` and its own ``freshness.missing``/``freshness.stale`` both ``true``.

Each series entry also reports whether locally stored series-level metadata
(migration ``0008``, ``macro_series_metadata`` table -- see
``market_intelligence/storage/macro_series_metadata_repository.py``) is
available, so a future Macro Analyst never interprets an unlabeled number.
When available, the entry includes that metadata's ``title``, ``frequency``,
``frequency_short``, ``units``, and ``seasonal_adjustment`` fields, read
exactly as stored -- never inferred from the series ID itself. A missing
metadata table or a series with no stored metadata is valid, non-error
input: the entry reports ``metadata_available: false`` and every metadata
field ``null``, and the snapshot's aggregate ``flags.missing_metadata_series``
lists every requested series without stored metadata.

Each series entry also reports a small, deterministic, read-only bounded
excerpt of recent history:

- ``recent_observations`` -- up to ``recent_observations_limit`` most recent
  distinct stored ``observation_date`` values, ordered **newest first**,
  each reduced to exactly **one**
  deterministically chosen vintage per date using the same tie-break rule as
  the single latest observation (latest ``realtime_start``, then latest
  ``realtime_end``). This is a bounded excerpt, entirely separate from
  ``coverage`` (which always reflects the *full* stored history for the
  series regardless of this limit). No interpolation, forward-filling,
  seasonal adjustment, or value rewriting of any kind is ever performed --
  every item is exactly what is already stored.
- ``latest_change_from_previous`` -- one precisely supported comparison
  between the latest and immediately preceding chronological stored
  observation, available only when both are present, both are non-missing,
  and both were selected from the currently valid (non-superseded) vintage
  for their date. When available, it reports the exact absolute difference
  between the two values as a decimal string and a fixed
  ``"increased"``/``"decreased"``/``"unchanged"`` direction -- **never** a
  percentage change, an annualized change, a basis-point change, a
  "surprise", or a trend (two observations are never themselves a trend).
  When unavailable, every value field is ``null`` and
  ``unavailable_reason`` is one of a small, fixed enum.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FredInvalidSeriesIdError,
    normalize_series_id,
)
from market_intelligence.orchestration.clock import Clock, resolve_as_of, system_clock
from market_intelligence.storage.database import DuckDBManager

DEFAULT_PROVIDER = "fred"

# This project currently only ingests one macro series (see
# market_intelligence/orchestration/jobs.json); this default mirrors that,
# but a caller may request a different bounded set explicitly.
DEFAULT_SERIES_IDS: tuple[str, ...] = ("FEDFUNDS",)
MAX_SERIES_IDS = 10

# Bounds for the recent-observations excerpt (see normalize_recent_observations_limit
# and _read_series below). MIN is 2 so that, whenever at least two distinct
# observation dates are stored, the excerpt always contains enough rows to also
# support latest_change_from_previous -- callers never need a separate, larger
# request just to get the two-observation comparison.
MIN_RECENT_OBSERVATIONS_LIMIT = 2
MAX_RECENT_OBSERVATIONS_LIMIT = 24
DEFAULT_RECENT_OBSERVATIONS_LIMIT = 6

# The fixed, non-overridable realtime_end sentinel FredMacroDataClient.get_observations()
# always requests (see market_intelligence/data_connectors/fred_macro_data.py) --
# an observation's chosen vintage having this realtime_end means it is FRED's
# currently valid (non-superseded) revision, not a historical/superseded one.
_OPEN_REALTIME_END = date(9999, 12, 31)

# Fixed, small enum of reasons latest_change_from_previous can be unavailable.
CHANGE_UNAVAILABLE_INSUFFICIENT_HISTORY = "insufficient_history"
CHANGE_UNAVAILABLE_LATEST_MISSING = "latest_missing"
CHANGE_UNAVAILABLE_PREVIOUS_MISSING = "previous_missing"
CHANGE_UNAVAILABLE_INCOMPARABLE_VINTAGE = "incomparable_vintage"

# A conservative, documented default threshold suitable for monthly macro
# observations (mirrors MarketContextBuilder's MACRO_STALE_AFTER rationale):
# comfortably exceeds FEDFUNDS's monthly reporting cadence without flagging
# a normal inter-release gap as stale. This is a plain elapsed-time signal,
# not an economic judgment -- a series under this threshold is not thereby
# claimed to be economically current, only "not yet flagged stale by this
# fixed clock."
STALE_AFTER_DAYS = 90

# observation_date is a calendar DATE with no time component, so comparing
# it directly against as_of's own UTC calendar date could otherwise falsely
# flag a genuinely same-day observation as "future" purely from timezone
# rounding. This small, fixed, documented tolerance absorbs that without
# hiding a genuinely implausible future-dated observation (e.g. a data
# error reporting a date months ahead).
FUTURE_DATE_TOLERANCE_DAYS = 1

_EVIDENCE_ID_PREFIX = "macro_"
_EVIDENCE_ID_HASH_LENGTH = 16


class MacroEvidenceError(RuntimeError):
    """Sanitized error for the macro-evidence snapshot builder.

    Never includes the database path, SQL text, observation values, or a raw
    underlying exception -- only a fixed, non-input-derived description.
    """


class MacroEvidenceValidationError(MacroEvidenceError):
    """Raised when an input fails validation before any DuckDB connection is opened.

    The message never echoes raw, unvalidated input.
    """


def normalize_series_ids(values: Sequence[str]) -> tuple[str, ...]:
    """Normalize and validate the requested FRED series IDs.

    Raises ``MacroEvidenceValidationError`` for: a non-sequence (a plain
    string is rejected even though it is technically iterable character by
    character, as is any other non-sequence such as a boolean); an empty
    selection; more than ``MAX_SERIES_IDS`` entries; a non-string or
    malformed entry (validated via the same ``normalize_series_id`` used by
    ``FredMacroDataClient``); or a duplicate series ID once every entry has
    been normalized (e.g. ``"fedfunds"`` and ``"FEDFUNDS"`` collide).
    Duplicates are rejected rather than silently deduplicated, so a caller's
    mistaken repeated request is never silently accepted. The result
    preserves the caller's requested order -- it is never sorted -- so the
    snapshot's ``series`` array always reflects the order actually
    requested.
    """
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise MacroEvidenceValidationError(
            "Invalid series_ids: expected a sequence of strings."
        )
    if len(values) == 0:
        raise MacroEvidenceValidationError(
            "Invalid series_ids: at least one series ID must be requested."
        )
    if len(values) > MAX_SERIES_IDS:
        raise MacroEvidenceValidationError(
            f"Invalid series_ids: at most {MAX_SERIES_IDS} series may be requested."
        )

    normalized_ids: list[str] = []
    seen: set[str] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, str):
            raise MacroEvidenceValidationError(
                "Invalid series_ids: every entry must be a string."
            )
        try:
            normalized = normalize_series_id(value)
        except FredInvalidSeriesIdError as exc:
            raise MacroEvidenceValidationError(f"Invalid series ID: {exc}") from None
        if normalized in seen:
            raise MacroEvidenceValidationError(
                "Invalid series_ids: duplicate series ID after normalization."
            )
        seen.add(normalized)
        normalized_ids.append(normalized)

    return tuple(normalized_ids)


def normalize_recent_observations_limit(value: Any) -> int:
    """Validate ``recent_observations_limit`` before any DuckDB access.

    Must be a plain ``int`` (a ``bool`` is explicitly rejected even though
    ``bool`` is a subclass of ``int`` in Python) between
    ``MIN_RECENT_OBSERVATIONS_LIMIT`` and ``MAX_RECENT_OBSERVATIONS_LIMIT``
    inclusive. Raises ``MacroEvidenceValidationError`` otherwise; the
    rejected value is never echoed.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise MacroEvidenceValidationError(
            "Invalid recent_observations_limit: must be a plain integer."
        )
    if not (MIN_RECENT_OBSERVATIONS_LIMIT <= value <= MAX_RECENT_OBSERVATIONS_LIMIT):
        raise MacroEvidenceValidationError(
            "Invalid recent_observations_limit: must be between "
            f"{MIN_RECENT_OBSERVATIONS_LIMIT} and {MAX_RECENT_OBSERVATIONS_LIMIT}."
        )
    return value


def _evidence_id(
    provider: str,
    series_id: str,
    observation_date: date,
    realtime_start: date,
    realtime_end: date,
) -> str:
    """Build a stable, deterministic evidence ID for one chosen observation vintage.

    Derived only from ``(provider, series_id, observation_date,
    realtime_start, realtime_end)`` -- the full stored identity -- **never**
    from the observation's value, so re-selecting the same stored vintage
    always produces the same ``evidence_id`` regardless of what value it
    holds, and two different vintages of the same series/date always
    produce different IDs.
    """
    digest = hashlib.sha256(
        f"{provider}:{series_id}:{observation_date.isoformat()}:"
        f"{realtime_start.isoformat()}:{realtime_end.isoformat()}".encode()
    ).hexdigest()
    return f"{_EVIDENCE_ID_PREFIX}{digest[:_EVIDENCE_ID_HASH_LENGTH]}"


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _date_str(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _utc_timestamp_str(value: datetime | None) -> str | None:
    """Format a stored TIMESTAMP value as an RFC3339 UTC string.

    ``retrieved_at`` holds a naive ``datetime`` already normalized to UTC
    before being written (see
    ``market_intelligence/storage/macro_observation_repository.py``); this
    only formats that existing convention, it performs no timezone
    conversion of its own.
    """
    if value is None:
        return None
    return value.isoformat() + "Z"


def _format_as_of(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _unavailable_change(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "unavailable_reason": reason,
        "latest_observation_date": None,
        "latest_value": None,
        "latest_evidence_id": None,
        "previous_observation_date": None,
        "previous_value": None,
        "previous_evidence_id": None,
        "absolute_change_native_units": None,
        "direction": None,
    }


def _empty_series(series_id: str) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "provider": DEFAULT_PROVIDER,
        "has_stored_observation": False,
        "latest_observation_date": None,
        "latest_value": None,
        "latest_is_missing": None,
        "realtime_start": None,
        "realtime_end": None,
        "retrieved_at_utc": None,
        "evidence_id": None,
        "coverage": {
            "row_count": 0,
            "earliest_observation_date": None,
            "latest_observation_date": None,
            "missing_observation_count": 0,
        },
        "freshness": {
            "missing": True,
            "stale": True,
            "future_date_detected": False,
            "stale_after_days": STALE_AFTER_DAYS,
        },
        "recent_observations": [],
        "latest_change_from_previous": _unavailable_change(
            CHANGE_UNAVAILABLE_INSUFFICIENT_HISTORY
        ),
    }


def _empty_metadata() -> dict[str, Any]:
    return {
        "metadata_available": False,
        "title": None,
        "frequency": None,
        "frequency_short": None,
        "units": None,
        "seasonal_adjustment": None,
    }


def _compute_latest_change(
    provider: str, series_id: str, recent_rows: list[tuple[Any, ...]]
) -> dict[str, Any]:
    """Compute the precisely supported latest-vs-previous change, if available.

    ``recent_rows`` must already be ordered newest-first, one deterministically
    chosen vintage per observation_date (see ``_read_series``). Available only
    when at least two distinct observation dates are on file, both the latest
    and immediately preceding chosen rows are non-missing, and both were
    selected from FRED's currently valid (non-superseded) vintage for their
    date (``realtime_end == _OPEN_REALTIME_END``) -- this is the documented
    deterministic "comparable vintage series" rule: both rows were chosen by
    the identical fixed per-date vintage-selection rule, and neither is a
    stale, already-superseded revision. Never computes a percentage,
    annualized, or basis-point change, and never labels two observations a
    trend.
    """
    if len(recent_rows) < 2:
        return _unavailable_change(CHANGE_UNAVAILABLE_INSUFFICIENT_HISTORY)

    latest_date, latest_rs, latest_re, latest_value, latest_missing, _ = recent_rows[0]
    prev_date, prev_rs, prev_re, prev_value, prev_missing, _ = recent_rows[1]

    if latest_missing:
        return _unavailable_change(CHANGE_UNAVAILABLE_LATEST_MISSING)
    if prev_missing:
        return _unavailable_change(CHANGE_UNAVAILABLE_PREVIOUS_MISSING)
    if latest_re != _OPEN_REALTIME_END or prev_re != _OPEN_REALTIME_END:
        return _unavailable_change(CHANGE_UNAVAILABLE_INCOMPARABLE_VINTAGE)

    if latest_value > prev_value:
        direction = "increased"
    elif latest_value < prev_value:
        direction = "decreased"
    else:
        direction = "unchanged"

    return {
        "available": True,
        "unavailable_reason": None,
        "latest_observation_date": _date_str(latest_date),
        "latest_value": _decimal_str(latest_value),
        "latest_evidence_id": _evidence_id(provider, series_id, latest_date, latest_rs, latest_re),
        "previous_observation_date": _date_str(prev_date),
        "previous_value": _decimal_str(prev_value),
        "previous_evidence_id": _evidence_id(provider, series_id, prev_date, prev_rs, prev_re),
        "absolute_change_native_units": _decimal_str(abs(latest_value - prev_value)),
        "direction": direction,
    }


class MacroEvidenceBuilder:
    """Builds one deterministic, read-only macro-evidence snapshot from local DuckDB storage.

    See the module docstring and ``docs/MACRO_EVIDENCE_SNAPSHOT.md`` for the
    full field contract, vintage-selection rule, and known limitations.
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
        series_ids: Sequence[str] = DEFAULT_SERIES_IDS,
        recent_observations_limit: int = DEFAULT_RECENT_OBSERVATIONS_LIMIT,
    ) -> dict[str, Any]:
        """Build one sanitized, JSON-ready macro-evidence snapshot dict.

        ``series_ids`` and ``recent_observations_limit`` are both strictly
        validated and normalized before any DuckDB connection is opened --
        see ``normalize_series_ids``/``normalize_recent_observations_limit``.
        Raises ``MacroEvidenceValidationError`` for invalid input, or
        ``MacroEvidenceError`` (sanitized) if the local database exists but
        cannot be read. A missing database file, a missing
        ``macro_observations`` table, or a requested series with no stored
        observation are all valid, non-error input -- the snapshot's flags
        describe this truthfully rather than raising.
        """
        normalized_series_ids = normalize_series_ids(series_ids)
        normalized_recent_limit = normalize_recent_observations_limit(recent_observations_limit)
        as_of = resolve_as_of(self._clock)

        if not self._database_path.exists():
            series_entries = [
                {**_empty_series(series_id), **_empty_metadata()}
                for series_id in normalized_series_ids
            ]
        else:
            try:
                connection = duckdb.connect(str(self._database_path), read_only=True)
            except duckdb.Error:
                raise MacroEvidenceError("Failed to open local storage for reading.") from None

            try:
                try:
                    existing_tables = {
                        row[0]
                        for row in connection.execute(
                            "SELECT table_name FROM information_schema.tables "
                            "WHERE table_name IN ('macro_observations', 'macro_series_metadata')"
                        ).fetchall()
                    }
                    observations_table_exists = "macro_observations" in existing_tables
                    metadata_table_exists = "macro_series_metadata" in existing_tables

                    series_entries = []
                    for series_id in normalized_series_ids:
                        observation_entry = (
                            self._read_series(
                                connection, series_id, as_of, normalized_recent_limit
                            )
                            if observations_table_exists
                            else _empty_series(series_id)
                        )
                        metadata_entry = (
                            self._read_metadata(connection, series_id)
                            if metadata_table_exists
                            else _empty_metadata()
                        )
                        series_entries.append({**observation_entry, **metadata_entry})
                except duckdb.Error:
                    raise MacroEvidenceError(
                        "Failed to read macro evidence data from local storage."
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
                    raise MacroEvidenceError(
                        "Failed to close local storage connection."
                    ) from None

        missing_series = [
            entry["series_id"] for entry in series_entries if not entry["has_stored_observation"]
        ]
        stale_series = [
            entry["series_id"] for entry in series_entries if entry["freshness"]["stale"]
        ]
        future_dated_series = [
            entry["series_id"]
            for entry in series_entries
            if entry["freshness"]["future_date_detected"]
        ]
        missing_metadata_series = [
            entry["series_id"] for entry in series_entries if not entry["metadata_available"]
        ]

        return {
            "snapshot_created_at_utc": _format_as_of(as_of),
            "request": {
                "series_ids": list(normalized_series_ids),
                "recent_observations_limit": normalized_recent_limit,
            },
            "series": series_entries,
            "flags": {
                "missing_series": missing_series,
                "stale_series": stale_series,
                "future_dated_series": future_dated_series,
                "missing_metadata_series": missing_metadata_series,
            },
        }

    @staticmethod
    def _read_metadata(connection: duckdb.DuckDBPyConnection, series_id: str) -> dict[str, Any]:
        """Read this series' locally stored metadata, if present.

        Read-only; never infers metadata from the series ID itself. A
        missing row is valid, non-error input -- see ``_empty_metadata``.
        """
        row = connection.execute(
            "SELECT title, frequency, frequency_short, units, seasonal_adjustment "
            "FROM macro_series_metadata WHERE provider = ? AND series_id = ?",
            [DEFAULT_PROVIDER, series_id],
        ).fetchone()
        if row is None:
            return _empty_metadata()

        title, frequency, frequency_short, units, seasonal_adjustment = row
        return {
            "metadata_available": True,
            "title": title,
            "frequency": frequency,
            "frequency_short": frequency_short,
            "units": units,
            "seasonal_adjustment": seasonal_adjustment,
        }

    @staticmethod
    def _read_series(
        connection: duckdb.DuckDBPyConnection,
        series_id: str,
        as_of: datetime,
        recent_observations_limit: int,
    ) -> dict[str, Any]:
        # Vintage selection (per observation_date): among any rows sharing a
        # date, break the tie deterministically by the latest realtime_start,
        # then the latest realtime_end -- i.e. the most recently reported
        # revision of that date. This never combines fields from more than
        # one stored row for a given date. Rows are then ordered
        # newest-observation-date-first and bounded to
        # recent_observations_limit -- this single query serves both the
        # "latest" fields below and the recent_observations excerpt, so both
        # always agree on which row is "the latest".
        recent_rows = connection.execute(
            """
            SELECT observation_date, realtime_start, realtime_end, value, is_missing, retrieved_at
            FROM (
                SELECT observation_date, realtime_start, realtime_end, value, is_missing,
                       retrieved_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY observation_date
                           ORDER BY realtime_start DESC, realtime_end DESC
                       ) AS rn
                FROM macro_observations
                WHERE provider = ? AND series_id = ?
            ) ranked
            WHERE rn = 1
            ORDER BY observation_date DESC
            LIMIT ?
            """,
            [DEFAULT_PROVIDER, series_id, recent_observations_limit],
        ).fetchall()

        if not recent_rows:
            return _empty_series(series_id)

        observation_date, realtime_start, realtime_end, value, is_missing, retrieved_at = (
            recent_rows[0]
        )

        row_count, earliest_date, latest_date, missing_count = connection.execute(
            """
            SELECT count(*), min(observation_date), max(observation_date),
                   sum(CASE WHEN is_missing THEN 1 ELSE 0 END)
            FROM macro_observations
            WHERE provider = ? AND series_id = ?
            """,
            [DEFAULT_PROVIDER, series_id],
        ).fetchone()

        future_threshold = as_of.date() + timedelta(days=FUTURE_DATE_TOLERANCE_DAYS)
        future_date_detected = observation_date > future_threshold
        elapsed_days = (as_of.date() - observation_date).days
        stale = future_date_detected or elapsed_days > STALE_AFTER_DAYS

        recent_observations = [
            {
                "observation_date": _date_str(row[0]),
                "value": _decimal_str(row[3]),
                "is_missing": bool(row[4]),
                "realtime_start": _date_str(row[1]),
                "realtime_end": _date_str(row[2]),
                "evidence_id": _evidence_id(DEFAULT_PROVIDER, series_id, row[0], row[1], row[2]),
            }
            for row in recent_rows
        ]

        return {
            "series_id": series_id,
            "provider": DEFAULT_PROVIDER,
            "has_stored_observation": True,
            "latest_observation_date": _date_str(observation_date),
            "latest_value": _decimal_str(value),
            "latest_is_missing": bool(is_missing),
            "realtime_start": _date_str(realtime_start),
            "realtime_end": _date_str(realtime_end),
            "retrieved_at_utc": _utc_timestamp_str(retrieved_at),
            "evidence_id": _evidence_id(
                DEFAULT_PROVIDER, series_id, observation_date, realtime_start, realtime_end
            ),
            "coverage": {
                "row_count": row_count,
                "earliest_observation_date": _date_str(earliest_date),
                "latest_observation_date": _date_str(latest_date),
                "missing_observation_count": int(missing_count) if missing_count else 0,
            },
            "freshness": {
                "missing": False,
                "stale": stale,
                "future_date_detected": future_date_detected,
                "stale_after_days": STALE_AFTER_DAYS,
            },
            "recent_observations": recent_observations,
            "latest_change_from_previous": _compute_latest_change(
                DEFAULT_PROVIDER, series_id, recent_rows
            ),
        }
