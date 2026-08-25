"""Manual, one-shot FRED series-metadata ingestion into the local
macro_series_metadata table.

Makes at most one small, bounded, explicit, read-only FRED series-metadata
request (via ``FredMacroDataClient.get_series_metadata`` -- see
``market_intelligence/data_connectors/fred_macro_data.py``) for a single
supplied series, then stores the normalized result through
``MacroSeriesMetadataRepository``. This script performs no prediction,
sentiment, recommendation, or trading logic -- storage only.

The command-line ``--series-id`` is strictly validated before any network
request is constructed: an invalid value is rejected by the same
normalization the connector uses internally, with zero HTTP requests and
zero database writes made in the invalid-input case. Any invalid-input case
prints only a fixed sanitized error category (``error category:
invalid_input``) -- never the raw exception message or type, and never the
raw, unvalidated series ID that triggered it.

Database initialization and repository storage are also wrapped: a DuckDB
initialization failure or a ``MacroSeriesMetadataStorageError`` from storage
is caught and reported as a fixed, sanitized outcome/category and a nonzero
exit code -- never a raw traceback containing SQL, paths, provider-reported
free text, or other internals, and never a falsely reported success.
Storage also has a final defensive catch-all for any unexpected
non-``MacroSeriesMetadataStorageError`` exception, so a bug in the
repository can never surface a raw traceback here either.

Only sanitized metadata is ever printed: configured, fetch outcome, series
ID, and a storage outcome/status (whether the row was inserted or updated,
plus the ingestion-run status). Never the series title, units, frequency,
seasonal adjustment, notes, popularity, last-updated timestamp, database
rows, credentials, headers, URLs, or raw responses.

This script is not run live as part of implementing this storage layer --
live ingestion requires separate, explicit authorization.
"""

from __future__ import annotations

import argparse

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FredCredentialsMissingError,
    FredInvalidSeriesIdError,
    FredMacroDataClient,
    FredMacroDataError,
    normalize_series_id,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_series_metadata_repository import (
    MacroSeriesMetadataRepository,
    MacroSeriesMetadataStorageError,
)

DEFAULT_SERIES_ID = "FEDFUNDS"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot, bounded, read-only FRED series-metadata ingestion."
    )
    parser.add_argument("--series-id", default=DEFAULT_SERIES_ID)
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
    except FredInvalidSeriesIdError:
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
        return 1

    try:
        metadata = client.get_series_metadata(series_id)
    except FredCredentialsMissingError:
        print("fetch outcome: not_configured")
        print(f"series id: {series_id}")
        return 1
    except FredMacroDataError as exc:
        print("fetch outcome: failed")
        print(f"series id: {series_id}")
        print(f"error category: {type(exc).__name__}")
        return 1

    print("fetch outcome: success")
    print(f"series id: {series_id}")

    try:
        DuckDBManager(settings=settings).initialize()
    except Exception:
        print("storage outcome: failed")
        print("error category: database_initialization_failed")
        return 1

    repository = MacroSeriesMetadataRepository(settings=settings)
    try:
        result = repository.store_metadata(metadata, provider="fred")
    except MacroSeriesMetadataStorageError:
        print("storage outcome: failed")
        print("error category: storage_error")
        return 1
    except Exception:
        # Defensive catch-all: MacroSeriesMetadataRepository.store_metadata
        # is documented to never raise anything but
        # MacroSeriesMetadataStorageError, but this guards against an
        # unexpected raw exception -- and any SQL, path, or credential it
        # might embed -- ever reaching a printed traceback.
        print("storage outcome: failed")
        print("error category: unexpected_error")
        return 1

    print(f"stored: {'inserted' if result.inserted else 'updated'}")
    print(f"ingestion-run status: {result.ingestion_run_status}")

    return 0 if result.ingestion_run_status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
