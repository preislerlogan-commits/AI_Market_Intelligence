"""Manual, one-shot FRED historical-observations ingestion into the local
macro_observations table.

Makes at most a small, bounded, explicit, read-only sequence of FRED
observations requests (via ``FredMacroDataClient.get_observations`` --
see ``market_intelligence/data_connectors/fred_macro_data.py``) for a
single supplied series/date-range, then stores the normalized results
through ``MacroObservationRepository``. This script performs no
prediction, sentiment, recommendation, or trading logic -- storage only.

Command-line ``--series-id``, ``--start``, ``--end``, ``--limit``, and
``--max-pages`` are all strictly validated before any network request is
constructed: an invalid ``--limit``/``--max-pages`` value is rejected by
argparse itself (before ``Settings``/the FRED client are ever created), and
an invalid ``--series-id``/``--start``/``--end`` is rejected by the same
normalization the connector uses internally, with zero HTTP requests and
zero database writes made in any invalid-input case. The normalized
``start``/``end`` are also compared here (rejecting ``start > end``) before
``Settings``, the FRED client, any network request, or any database
construction happens -- not left to be discovered later inside the
connector or the repository. Any invalid-input case prints only a fixed
sanitized error category (``error category: invalid_input``) -- never the
raw exception message or type, and never the raw, unvalidated
series/date/limit input that triggered it.

Database initialization and repository storage are also wrapped: a DuckDB
initialization failure or a ``MacroObservationStorageError`` from storage is
caught and reported as a fixed, sanitized outcome/category and a nonzero
exit code -- never a raw traceback containing SQL, paths, observation
values, or other internals, and never a falsely reported success. Storage
also has a final defensive catch-all for any unexpected
non-``MacroObservationStorageError`` exception, so a bug in the repository
can never surface a raw traceback here either.

If ``--start``/``--end`` are omitted, a small, clearly bounded, fully
completed historical window is used by default: the DEFAULT_LOOKBACK_DAYS
calendar days immediately preceding yesterday (UTC). Ending at yesterday
(rather than today) deliberately avoids requesting a still-possibly-revised
or not-yet-published "today" observation for daily/weekly series.

Only sanitized metadata is ever printed: configured, fetch outcome, series
ID, requested observation start/end, and received/inserted/existing-or-
updated/failed counts plus the ingestion-run status. Never observation
values, individual observation dates, database rows, credentials, headers,
URLs, raw responses, or page tokens.

This script is not run live as part of implementing this storage layer --
live ingestion requires separate, explicit authorization.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FredCredentialsMissingError,
    FredInvalidObservationRequestError,
    FredInvalidSeriesIdError,
    FredMacroDataClient,
    FredMacroDataError,
    normalize_observation_date,
    normalize_observation_max_pages,
    normalize_observations_limit,
    normalize_series_id,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_observation_repository import (
    MacroObservationRepository,
    MacroObservationStorageError,
)

DEFAULT_SERIES_ID = "FEDFUNDS"
DEFAULT_LOOKBACK_DAYS = 30
DEFAULT_LIMIT = 1000
DEFAULT_MAX_PAGES = 1


def _default_window() -> tuple[str, str]:
    """A small, bounded, fully completed historical window ending yesterday (UTC)."""
    end_date = (datetime.now(UTC) - timedelta(days=1)).date()
    start_date = end_date - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return start_date.isoformat(), end_date.isoformat()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot, bounded, read-only FRED historical-observations ingestion."
    )
    parser.add_argument("--series-id", default=DEFAULT_SERIES_ID)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run one ingestion pass. ``settings`` is an injection point for tests only.

    Real usage never passes ``settings`` -- it always defaults to a fresh
    ``Settings()`` reading the process environment/local ``.env``.
    """
    args = _parse_args(argv)

    # Validate before constructing Settings/any client, so malformed input
    # never reaches the network and never triggers a database write.
    try:
        series_id = normalize_series_id(args.series_id)
        default_start, default_end = _default_window()
        start = normalize_observation_date(
            args.start if args.start is not None else default_start,
            field_name="observation_start",
        )
        end = normalize_observation_date(
            args.end if args.end is not None else default_end, field_name="observation_end"
        )
        if start > end:
            raise FredInvalidObservationRequestError(
                "Invalid observation_start/observation_end: observation_start must not be "
                "after observation_end."
            )
        limit = normalize_observations_limit(args.limit)
        max_pages = normalize_observation_max_pages(args.max_pages)
    except (FredInvalidSeriesIdError, FredInvalidObservationRequestError):
        print("configured: unknown")
        print("fetch outcome: invalid_input")
        print("series id: (invalid)")
        print("error category: invalid_input")
        return 2

    settings = settings or Settings()
    client = FredMacroDataClient(settings=settings)
    configured = client.is_configured()
    print(f"configured: {configured}")

    if not configured:
        print("fetch outcome: not_configured")
        print(f"series id: {series_id}")
        print(f"requested observation start: {start}")
        print(f"requested observation end: {end}")
        return 1

    try:
        observations = client.get_observations(
            series_id, start, end, limit=limit, max_pages=max_pages
        )
    except FredCredentialsMissingError:
        print("fetch outcome: not_configured")
        print(f"series id: {series_id}")
        print(f"requested observation start: {start}")
        print(f"requested observation end: {end}")
        return 1
    except FredMacroDataError as exc:
        print("fetch outcome: failed")
        print(f"series id: {series_id}")
        print(f"requested observation start: {start}")
        print(f"requested observation end: {end}")
        print(f"error category: {type(exc).__name__}")
        return 1

    print("fetch outcome: success")
    print(f"series id: {series_id}")
    print(f"requested observation start: {start}")
    print(f"requested observation end: {end}")
    print(f"received: {len(observations)}")

    if not observations:
        # A legitimate, successful empty result (e.g. no published
        # observations in the requested range) -- not a failure. Nothing to
        # store, so the repository (which rejects empty batches) is never
        # called for this case.
        print("inserted: 0")
        print("existing/updated: 0")
        print("failed: 0")
        print("ingestion-run status: skipped_empty")
        return 0

    try:
        DuckDBManager(settings=settings).initialize()
    except Exception:
        print("storage outcome: failed")
        print("error category: database_initialization_failed")
        return 1

    repository = MacroObservationRepository(settings=settings)
    try:
        result = repository.store_observations(observations, provider="fred")
    except MacroObservationStorageError:
        print("storage outcome: failed")
        print("error category: storage_error")
        return 1
    except Exception:
        # Defensive catch-all: MacroObservationRepository.store_observations
        # is documented to never raise anything but
        # MacroObservationStorageError, but this guards against an
        # unexpected raw exception -- and any SQL, path, or credential it
        # might embed -- ever reaching a printed traceback.
        print("storage outcome: failed")
        print("error category: unexpected_error")
        return 1

    print(f"inserted: {result.inserted}")
    print(f"existing/updated: {result.existing_or_updated}")
    print(f"failed: {result.failed}")
    print(f"ingestion-run status: {result.ingestion_run_status}")

    return 0 if result.ingestion_run_status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
