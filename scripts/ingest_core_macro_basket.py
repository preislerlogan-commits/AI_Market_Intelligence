"""Dry-run-first Core Macro Basket batch ingestion.

This is a **manual, controlled batch tool** for the seven series-reviewed,
committed Core Macro Basket (see
``market_intelligence/config/core_macro_series.json``/
``market_intelligence/config/macro_basket.py`` and
``docs/CORE_MACRO_BASKET.md``) -- it is not scheduling, not continuous or
unattended operation, and it does not modify
``market_intelligence/orchestration/`` in any way.

For each selected, enabled series it makes at most two requests -- one
bounded ``FredMacroDataClient.get_series_metadata`` request and one bounded
``FredMacroDataClient.get_observations`` request -- and stores the results
through the existing, reviewed ``MacroSeriesMetadataRepository``/
``MacroObservationRepository``. No title, unit, frequency, seasonal-
adjustment, note, or observation value is ever hardcoded anywhere in this
script or the committed configuration -- every one of those comes only from
the reviewed FRED metadata endpoint and local storage, at request time.

Default behavior is a dry run: it validates series selection, loads the
committed configuration, resolves one shared UTC "as-of" instant, computes
each selected series' bounded observation window, and prints a sanitized
plan -- zero ``Settings`` construction, zero network requests, zero database
activity of any kind. ``--execute`` is required for real network/database
activity.

Series selection (``--series SERIES_ID``, repeatable, or ``--all`` --
mutually exclusive, exactly one required) is validated purely against the
already-loaded committed configuration -- before ``Settings``, any client,
the network, the database, or the run lock are ever constructed. Selected
series are always processed in the committed configuration file's own
deterministic order, regardless of the order ``--series`` flags were given
in.

``--execute`` reuses the existing orchestration run lock
(``market_intelligence/orchestration/lock.py``) for overlap protection, and
requires the real local database to already be healthy at the latest schema
version (``0009``) -- checked read-only, before any network request -- since this
script deliberately never applies a migration itself (schema/migration
changes are separate, explicitly reviewed work). Each selected series is
processed sequentially and independently: one series' failure is recorded
truthfully but never prevents a later selected series from being attempted,
and there is no automatic retry of any request. The overall run status is
only ``succeeded`` if every selected series' metadata request/storage
succeeded and its observations request/storage either succeeded or was
validly empty (``skipped_empty``); any per-series failure makes the whole
run's reported status ``failed``, even if other series in the same run
succeeded.

All output -- in both modes -- is sanitized: series ID, category, enabled
flag, configured lookback/limit values, computed request-window calendar
dates, per-series/overall status, and sanitized counts only. This script
never prints a series title, unit, frequency, seasonal adjustment, note,
observation value, URL, query parameter, raw exception text, SQL, a
database path, or a credential.

**Known limitation:** a single bounded window per series, ending at the
shared as-of date, does not guarantee complete or gap-free coverage of that
series' full published history -- see ``docs/CORE_MACRO_BASKET.md``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from market_intelligence.config.macro_basket import (
    CoreMacroSeriesConfig,
    CoreMacroSeriesConfigError,
    load_core_macro_series,
)
from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import (
    FredCredentialsMissingError,
    FredInvalidSeriesIdError,
    FredMacroDataClient,
    FredMacroDataError,
    FredSeriesMetadata,
    normalize_observation_date,
    normalize_series_id,
)
from market_intelligence.orchestration.clock import Clock, resolve_as_of, system_clock
from market_intelligence.orchestration.lock import (
    OrchestrationLockContentionError,
    OrchestrationLockError,
    RunLock,
    default_lock_path,
)
from market_intelligence.orchestration.results import (
    ERROR_CATEGORY_NOT_CONFIGURED,
    ERROR_CATEGORY_PROVIDER_ERROR,
    ERROR_CATEGORY_STORAGE_ERROR,
    ERROR_CATEGORY_UNEXPECTED_ERROR,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_observation_repository import (
    MacroObservationRepository,
    MacroObservationStorageError,
)
from market_intelligence.storage.macro_series_metadata_repository import (
    MacroSeriesMetadataRepository,
    MacroSeriesMetadataStorageError,
)

# The database must already be healthy at exactly this schema version before
# any request is made -- this script never applies a migration itself. This
# tracks the latest migration version; bump it whenever a migration is added.
REQUIRED_SCHEMA_VERSION = "0009"

# Bounded observations request shape shared by every selected series. Every
# configured lookback window (see market_intelligence/config/macro_basket.py,
# max 1,100 days) comfortably fits well under this single-page limit for
# every approved series' reporting frequency (daily/monthly/quarterly), so
# one page is always sufficient.
OBSERVATIONS_LIMIT = 1000
OBSERVATIONS_MAX_PAGES = 1

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED_EMPTY = "skipped_empty"


@dataclass(frozen=True)
class SeriesPlanEntry:
    """One selected series' sanitized dry-run plan entry.

    Built purely from an already-loaded ``CoreMacroSeriesConfig`` and one
    injected clock -- never touches ``Settings``, a client, the network, the
    database, or the run lock.
    """

    series_id: str
    category: str
    enabled: bool
    observation_lookback_days: int
    recent_observations_limit: int
    requested_observation_window_start: str
    requested_observation_window_end: str


@dataclass(frozen=True)
class SeriesExecutionResult:
    """Sanitized result of running one series' metadata + observations requests.

    Never includes a series title, unit, frequency, seasonal adjustment,
    note, observation value, URL, raw exception text, SQL, a database path,
    or a credential -- only identifiers, fixed status/error-category
    strings, and sanitized counts.
    """

    series_id: str
    status: str
    metadata_status: str
    metadata_error_category: str | None
    observation_status: str
    observation_error_category: str | None
    observation_received: int
    observation_inserted: int
    observation_existing_or_updated: int
    observation_failed: int


def _compute_observation_window(as_of: datetime, lookback_days: int) -> tuple[str, str]:
    """A bounded observation window: ``lookback_days`` before ``as_of``'s UTC date, through it.

    Uses the same strict calendar-date normalization
    (``normalize_observation_date``) the FRED connector itself requires for
    ``observation_start``/``observation_end``. This bounded window does not
    guarantee complete or gap-free coverage of the series' full published
    history -- see the module docstring.
    """
    end_date = as_of.astimezone(UTC).date()
    start_date = end_date - timedelta(days=lookback_days)
    start = normalize_observation_date(start_date.isoformat(), field_name="observation_start")
    end = normalize_observation_date(end_date.isoformat(), field_name="observation_end")
    return start, end


def build_plan(
    selected: tuple[CoreMacroSeriesConfig, ...], *, clock: Clock = system_clock
) -> tuple[SeriesPlanEntry, ...]:
    """Build a sanitized, read-only dry-run plan for the given selected series.

    Pure: never constructs ``Settings``, a client, a database connection, or
    a lock. Calls ``clock`` exactly once (via
    ``market_intelligence.orchestration.clock.resolve_as_of``) and reuses
    that one resolved, UTC-normalized instant for every selected series'
    window.
    """
    as_of = resolve_as_of(clock)
    entries = []
    for config in selected:
        start, end = _compute_observation_window(as_of, config.observation_lookback_days)
        entries.append(
            SeriesPlanEntry(
                series_id=config.series_id,
                category=config.category,
                enabled=config.enabled,
                observation_lookback_days=config.observation_lookback_days,
                recent_observations_limit=config.recent_observations_limit,
                requested_observation_window_start=start,
                requested_observation_window_end=end,
            )
        )
    return tuple(entries)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run-first Core Macro Basket ingestion (metadata + bounded "
            "observations) for the seven approved FRED series."
        )
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--series",
        dest="series_ids",
        action="append",
        default=None,
        help="Select one approved series by ID (repeatable).",
    )
    selection.add_argument(
        "--all", action="store_true", help="Select every enabled configured series."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually make network/database requests. Without this flag, "
            "only a dry-run plan is printed."
        ),
    )
    return parser.parse_args(argv)


def _select_configs(
    configs: tuple[CoreMacroSeriesConfig, ...],
    *,
    series_ids: list[str] | None,
    select_all: bool,
) -> tuple[CoreMacroSeriesConfig, ...] | str:
    """Return the selected configs, in committed-file order, or a fixed sanitized error category.

    Never echoes a raw requested series ID back -- only a fixed category.
    Purely validated against the already-loaded committed configuration --
    before ``Settings``, the lock, the database, or the network are ever
    touched.
    """
    if select_all:
        enabled = tuple(config for config in configs if config.enabled)
        if not enabled:
            return "no_enabled_series"
        return enabled

    if not series_ids:
        return "invalid_series_id"

    normalized_requested: list[str] = []
    for raw in series_ids:
        try:
            normalized_requested.append(normalize_series_id(raw))
        except FredInvalidSeriesIdError:
            return "invalid_series_id"

    if len(set(normalized_requested)) != len(normalized_requested):
        return "duplicate_series_id"

    by_series_id = {config.series_id: config for config in configs}
    for series_id in normalized_requested:
        config = by_series_id.get(series_id)
        if config is None:
            return "unknown_series_id"
        if not config.enabled:
            return "disabled_series_id"

    requested = set(normalized_requested)
    return tuple(config for config in configs if config.series_id in requested)


def _run_series_metadata(
    entry: SeriesPlanEntry,
    *,
    client: FredMacroDataClient,
    repository: MacroSeriesMetadataRepository,
) -> tuple[str, str | None]:
    """Fetch and store one series' metadata. Returns ``(status, error_category)``. Never raises."""
    try:
        metadata: FredSeriesMetadata = client.get_series_metadata(entry.series_id)
    except FredCredentialsMissingError:
        return STATUS_FAILED, ERROR_CATEGORY_NOT_CONFIGURED
    except FredMacroDataError:
        return STATUS_FAILED, ERROR_CATEGORY_PROVIDER_ERROR
    except Exception:
        return STATUS_FAILED, ERROR_CATEGORY_UNEXPECTED_ERROR

    try:
        repository.store_metadata(metadata, provider="fred")
    except MacroSeriesMetadataStorageError:
        return STATUS_FAILED, ERROR_CATEGORY_STORAGE_ERROR
    except Exception:
        return STATUS_FAILED, ERROR_CATEGORY_UNEXPECTED_ERROR

    return STATUS_SUCCEEDED, None


def _run_series_observations(
    entry: SeriesPlanEntry,
    *,
    client: FredMacroDataClient,
    repository: MacroObservationRepository,
) -> tuple[str, str | None, int, int, int, int]:
    """Fetch and store one series' bounded observations. Never raises.

    Returns ``(status, error_category, received, inserted, existing_or_updated, failed)``.
    """
    try:
        observations = client.get_observations(
            entry.series_id,
            entry.requested_observation_window_start,
            entry.requested_observation_window_end,
            limit=OBSERVATIONS_LIMIT,
            max_pages=OBSERVATIONS_MAX_PAGES,
        )
    except FredCredentialsMissingError:
        return STATUS_FAILED, ERROR_CATEGORY_NOT_CONFIGURED, 0, 0, 0, 0
    except FredMacroDataError:
        return STATUS_FAILED, ERROR_CATEGORY_PROVIDER_ERROR, 0, 0, 0, 0
    except Exception:
        return STATUS_FAILED, ERROR_CATEGORY_UNEXPECTED_ERROR, 0, 0, 0, 0

    if not observations:
        # A legitimate, successful empty result -- nothing to store.
        # MacroObservationRepository rejects an empty batch, so it is never
        # called for this case, mirroring scripts/ingest_fred_observations.py.
        return STATUS_SKIPPED_EMPTY, None, 0, 0, 0, 0

    received = len(observations)
    try:
        result = repository.store_observations(observations, provider="fred")
    except MacroObservationStorageError:
        return STATUS_FAILED, ERROR_CATEGORY_STORAGE_ERROR, received, 0, 0, 0
    except Exception:
        return STATUS_FAILED, ERROR_CATEGORY_UNEXPECTED_ERROR, received, 0, 0, 0

    if result.ingestion_run_status != STATUS_SUCCEEDED:
        return (
            STATUS_FAILED,
            ERROR_CATEGORY_STORAGE_ERROR,
            result.received,
            result.inserted,
            result.existing_or_updated,
            result.failed,
        )

    return (
        STATUS_SUCCEEDED,
        None,
        result.received,
        result.inserted,
        result.existing_or_updated,
        result.failed,
    )


def _run_one_series(
    entry: SeriesPlanEntry,
    *,
    client: FredMacroDataClient,
    metadata_repository: MacroSeriesMetadataRepository,
    observation_repository: MacroObservationRepository,
) -> SeriesExecutionResult:
    """Run one series' metadata + bounded observations requests. Never raises, never retries."""
    metadata_status, metadata_error_category = _run_series_metadata(
        entry, client=client, repository=metadata_repository
    )
    (
        observation_status,
        observation_error_category,
        observation_received,
        observation_inserted,
        observation_existing_or_updated,
        observation_failed,
    ) = _run_series_observations(entry, client=client, repository=observation_repository)

    overall = (
        STATUS_SUCCEEDED
        if metadata_status == STATUS_SUCCEEDED
        and observation_status in (STATUS_SUCCEEDED, STATUS_SKIPPED_EMPTY)
        else STATUS_FAILED
    )

    return SeriesExecutionResult(
        series_id=entry.series_id,
        status=overall,
        metadata_status=metadata_status,
        metadata_error_category=metadata_error_category,
        observation_status=observation_status,
        observation_error_category=observation_error_category,
        observation_received=observation_received,
        observation_inserted=observation_inserted,
        observation_existing_or_updated=observation_existing_or_updated,
        observation_failed=observation_failed,
    )


def _print_plan(plan: tuple[SeriesPlanEntry, ...]) -> None:
    print("mode: dry_run")
    print(f"selected series count: {len(plan)}")
    for entry in plan:
        print(f"- series_id: {entry.series_id}")
        print(f"  category: {entry.category}")
        print(f"  enabled: {entry.enabled}")
        print(f"  observation_lookback_days: {entry.observation_lookback_days}")
        print(f"  recent_observations_limit: {entry.recent_observations_limit}")
        print("  planned_metadata_request: true")
        print(f"  planned_observation_window_start: {entry.requested_observation_window_start}")
        print(f"  planned_observation_window_end: {entry.requested_observation_window_end}")


def _print_execution_results(
    results: tuple[SeriesExecutionResult, ...], *, overall_status: str
) -> None:
    print(f"overall status: {overall_status}")
    for result in results:
        print(f"- series_id: {result.series_id}")
        print(f"  status: {result.status}")
        print(f"  metadata_status: {result.metadata_status}")
        if result.metadata_error_category is not None:
            print(f"  metadata_error_category: {result.metadata_error_category}")
        print(f"  observation_status: {result.observation_status}")
        if result.observation_error_category is not None:
            print(f"  observation_error_category: {result.observation_error_category}")
        print(f"  observation_received: {result.observation_received}")
        print(f"  observation_inserted: {result.observation_inserted}")
        print(f"  observation_existing_or_updated: {result.observation_existing_or_updated}")
        print(f"  observation_failed: {result.observation_failed}")


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    clock: Clock = system_clock,
) -> int:
    """Run one dry-run or execute pass. ``settings``/``clock`` are test injection points only.

    Real usage never passes ``settings`` -- it defaults to a fresh
    ``Settings()`` reading the process environment/local ``.env``, and
    ``Settings`` is only ever constructed for an ``--execute`` run, never for
    a dry run.
    """
    args = _parse_args(argv)

    try:
        configs = load_core_macro_series()
    except CoreMacroSeriesConfigError:
        print("selection outcome: invalid_configuration")
        return 2

    selection = _select_configs(configs, series_ids=args.series_ids, select_all=args.all)
    if isinstance(selection, str):
        print(f"selection outcome: {selection}")
        return 2
    selected = selection

    plan = build_plan(selected, clock=clock)

    if not args.execute:
        _print_plan(plan)
        return 0

    print("mode: execute")
    resolved_settings = settings if settings is not None else Settings()

    lock = RunLock(default_lock_path(resolved_settings))
    try:
        lock.acquire()
    except OrchestrationLockContentionError:
        print("execution outcome: lock_contention")
        return 1
    except OrchestrationLockError:
        print("execution outcome: lock_error")
        return 1

    try:
        try:
            health = DuckDBManager(settings=resolved_settings).check_health()
        except Exception:
            print("execution outcome: database_health_check_failed")
            return 1

        if not health.healthy or health.schema_version != REQUIRED_SCHEMA_VERSION:
            print("execution outcome: database_not_healthy")
            return 1

        client = FredMacroDataClient(settings=resolved_settings)
        if not client.is_configured():
            print("execution outcome: not_configured")
            return 1

        metadata_repository = MacroSeriesMetadataRepository(settings=resolved_settings)
        observation_repository = MacroObservationRepository(settings=resolved_settings)

        results: list[SeriesExecutionResult] = []
        for entry in plan:
            try:
                result = _run_one_series(
                    entry,
                    client=client,
                    metadata_repository=metadata_repository,
                    observation_repository=observation_repository,
                )
            except Exception:
                # Defense in depth: _run_one_series/its helpers are
                # documented to already catch everything and never raise.
                # This guarantees one series' unexpected failure can never
                # prevent a later selected series from being attempted.
                result = SeriesExecutionResult(
                    series_id=entry.series_id,
                    status=STATUS_FAILED,
                    metadata_status=STATUS_FAILED,
                    metadata_error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
                    observation_status=STATUS_FAILED,
                    observation_error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
                    observation_received=0,
                    observation_inserted=0,
                    observation_existing_or_updated=0,
                    observation_failed=0,
                )
            results.append(result)

        overall_status = (
            STATUS_SUCCEEDED
            if all(result.status == STATUS_SUCCEEDED for result in results)
            else STATUS_FAILED
        )
        _print_execution_results(tuple(results), overall_status=overall_status)
        return 0 if overall_status == STATUS_SUCCEEDED else 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
