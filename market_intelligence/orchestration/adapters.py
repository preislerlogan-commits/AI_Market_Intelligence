"""Narrow adapters that run one job contract via existing, reviewed connectors/repositories.

Each adapter calls only the already-reviewed client (``AlpacaNewsClient``,
``AlpacaBarsClient``, ``FredMacroDataClient``) and repository
(``NewsArticleRepository``, ``BarRepository``, ``MacroObservationRepository``)
classes directly -- never a shell command or subprocess, and never one of
the manual ``scripts/ingest_*.py`` entry points. No adapter has any method
that could place an order or call an account/execution endpoint; none of
that functionality exists anywhere in the connectors this module imports.

Every adapter returns a sanitized ``JobResult`` and never raises: any
unexpected exception is caught at this boundary and converted to a fixed,
sanitized error category (see
``market_intelligence/orchestration/results.py``), so a bug in a connector
or repository can never surface a raw traceback, credential, or provider
value through the orchestration layer.

An empty provider result (zero news articles / zero bars / zero
observations) is a legitimate, successful fetch -- it is reported as
``JOB_STATUS_SKIPPED`` (storage is skipped entirely; there is nothing to
store, and ``MacroObservationRepository`` explicitly rejects an empty
batch), never as a failure. This mirrors the existing
``scripts/ingest_fred_observations.py``'s "skipped_empty" convention,
applied uniformly across all three job types for consistency.
"""

from __future__ import annotations

from datetime import UTC, datetime

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import (
    AlpacaBarsClient,
    AlpacaBarsCredentialsMissingError,
    AlpacaBarsError,
)
from market_intelligence.data_connectors.alpaca_news import (
    AlpacaNewsClient,
    AlpacaNewsCredentialsMissingError,
    AlpacaNewsError,
)
from market_intelligence.data_connectors.fred_macro_data import (
    FredCredentialsMissingError,
    FredMacroDataClient,
    FredMacroDataError,
)
from market_intelligence.orchestration.clock import Clock
from market_intelligence.orchestration.contracts import (
    AlpacaBarsJobParams,
    AlpacaNewsJobParams,
    FredObservationsJobParams,
    JobContract,
)
from market_intelligence.orchestration.results import (
    ERROR_CATEGORY_NOT_CONFIGURED,
    ERROR_CATEGORY_PROVIDER_ERROR,
    ERROR_CATEGORY_STORAGE_ERROR,
    ERROR_CATEGORY_UNEXPECTED_ERROR,
    JOB_STATUS_FAILED,
    JOB_STATUS_SKIPPED,
    JOB_STATUS_SUCCEEDED,
    JobResult,
)
from market_intelligence.orchestration.windows import bars_window, fred_observations_window
from market_intelligence.storage.bar_repository import BarRepository, BarStorageError
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_observation_repository import (
    MacroObservationRepository,
    MacroObservationStorageError,
)
from market_intelligence.storage.news_repository import NewsArticleRepository, NewsStorageError


def _now() -> datetime:
    return datetime.now(UTC)


def _result(
    contract: JobContract,
    *,
    started_at: datetime,
    status: str,
    error_category: str | None,
    received: int = 0,
    inserted: int = 0,
    existing_or_updated: int = 0,
    failed: int = 0,
    ingestion_run_id: str | None = None,
) -> JobResult:
    return JobResult(
        job_id=contract.job_id,
        job_type=contract.job_type,
        provider=contract.provider,
        dataset_name=contract.dataset_name,
        status=status,
        error_category=error_category,
        received=received,
        inserted=inserted,
        existing_or_updated=existing_or_updated,
        failed=failed,
        ingestion_run_id=ingestion_run_id,
        started_at=started_at,
        completed_at=_now(),
    )


def _initialize_database(settings: Settings) -> bool:
    """Bring the database up to the latest migration. Returns False, never raises, on failure."""
    try:
        DuckDBManager(settings=settings).initialize()
    except Exception:
        return False
    return True


def run_alpaca_news_job(contract: JobContract, *, settings: Settings, clock: Clock) -> JobResult:
    """Run one ``alpaca_news`` job. Never raises."""
    del clock  # news jobs do not compute a date window
    started_at = _now()
    params = contract.params
    assert isinstance(params, AlpacaNewsJobParams)

    client = AlpacaNewsClient(settings=settings)
    if not client.is_configured():
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_NOT_CONFIGURED,
        )

    try:
        items = client.get_news(params.symbol, limit=params.limit)
    except AlpacaNewsCredentialsMissingError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_NOT_CONFIGURED,
        )
    except AlpacaNewsError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_PROVIDER_ERROR,
        )
    except Exception:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
        )

    if not items:
        return _result(
            contract, started_at=started_at, status=JOB_STATUS_SKIPPED, error_category=None
        )

    if not _initialize_database(settings):
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_STORAGE_ERROR,
            received=len(items),
        )

    try:
        result = NewsArticleRepository(settings=settings).store_news_items(
            items, provider=contract.provider, dataset_name=contract.dataset_name
        )
    except NewsStorageError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_STORAGE_ERROR,
            received=len(items),
        )
    except Exception:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
            received=len(items),
        )

    succeeded = result.ingestion_run_status == "succeeded"
    return _result(
        contract,
        started_at=started_at,
        status=JOB_STATUS_SUCCEEDED if succeeded else JOB_STATUS_FAILED,
        error_category=None if succeeded else ERROR_CATEGORY_STORAGE_ERROR,
        received=result.received,
        inserted=result.inserted,
        existing_or_updated=result.updated,
        failed=result.failed,
        ingestion_run_id=result.ingestion_run_id,
    )


def run_alpaca_bars_job(contract: JobContract, *, settings: Settings, clock: Clock) -> JobResult:
    """Run one ``alpaca_bars`` job. Never raises."""
    started_at = _now()
    params = contract.params
    assert isinstance(params, AlpacaBarsJobParams)

    start, end = bars_window(clock(), params.lookback_days)

    client = AlpacaBarsClient(settings=settings)
    if not client.is_configured():
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_NOT_CONFIGURED,
        )

    try:
        bars = client.get_bars(
            params.symbol,
            params.timeframe,
            start,
            end,
            limit=params.limit,
            max_pages=params.max_pages,
        )
    except AlpacaBarsCredentialsMissingError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_NOT_CONFIGURED,
        )
    except AlpacaBarsError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_PROVIDER_ERROR,
        )
    except Exception:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
        )

    if not bars:
        return _result(
            contract, started_at=started_at, status=JOB_STATUS_SKIPPED, error_category=None
        )

    if not _initialize_database(settings):
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_STORAGE_ERROR,
            received=len(bars),
        )

    try:
        result = BarRepository(settings=settings).store_bars(
            bars, provider=contract.provider, dataset_name=contract.dataset_name
        )
    except BarStorageError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_STORAGE_ERROR,
            received=len(bars),
        )
    except Exception:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
            received=len(bars),
        )

    succeeded = result.ingestion_run_status == "succeeded"
    return _result(
        contract,
        started_at=started_at,
        status=JOB_STATUS_SUCCEEDED if succeeded else JOB_STATUS_FAILED,
        error_category=None if succeeded else ERROR_CATEGORY_STORAGE_ERROR,
        received=result.received,
        inserted=result.inserted,
        existing_or_updated=result.existing_or_updated,
        failed=result.failed,
        ingestion_run_id=result.ingestion_run_id,
    )


def run_fred_observations_job(
    contract: JobContract, *, settings: Settings, clock: Clock
) -> JobResult:
    """Run one ``fred_observations`` job. Never raises."""
    started_at = _now()
    params = contract.params
    assert isinstance(params, FredObservationsJobParams)

    start, end = fred_observations_window(clock(), params.lookback_days)

    client = FredMacroDataClient(settings=settings)
    if not client.is_configured():
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_NOT_CONFIGURED,
        )

    try:
        observations = client.get_observations(
            params.series_id, start, end, limit=params.limit, max_pages=params.max_pages
        )
    except FredCredentialsMissingError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_NOT_CONFIGURED,
        )
    except FredMacroDataError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_PROVIDER_ERROR,
        )
    except Exception:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
        )

    if not observations:
        return _result(
            contract, started_at=started_at, status=JOB_STATUS_SKIPPED, error_category=None
        )

    if not _initialize_database(settings):
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_STORAGE_ERROR,
            received=len(observations),
        )

    try:
        result = MacroObservationRepository(settings=settings).store_observations(
            observations, provider=contract.provider, dataset_name=contract.dataset_name
        )
    except MacroObservationStorageError:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_STORAGE_ERROR,
            received=len(observations),
        )
    except Exception:
        return _result(
            contract,
            started_at=started_at,
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
            received=len(observations),
        )

    succeeded = result.ingestion_run_status == "succeeded"
    return _result(
        contract,
        started_at=started_at,
        status=JOB_STATUS_SUCCEEDED if succeeded else JOB_STATUS_FAILED,
        error_category=None if succeeded else ERROR_CATEGORY_STORAGE_ERROR,
        received=result.received,
        inserted=result.inserted,
        existing_or_updated=result.existing_or_updated,
        failed=result.failed,
        ingestion_run_id=result.ingestion_run_id,
    )


_ADAPTERS_BY_JOB_TYPE = {
    "alpaca_news": run_alpaca_news_job,
    "alpaca_bars": run_alpaca_bars_job,
    "fred_observations": run_fred_observations_job,
}


def run_job(contract: JobContract, *, settings: Settings, clock: Clock) -> JobResult:
    """Dispatch to the adapter for ``contract.job_type``. Never raises."""
    adapter = _ADAPTERS_BY_JOB_TYPE[contract.job_type]
    try:
        return adapter(contract, settings=settings, clock=clock)
    except Exception:
        # Defense in depth: every adapter above already catches everything
        # and never raises; this guarantees the orchestration boundary can
        # never leak a raw exception even if that contract is ever
        # violated by a future change.
        return _result(
            contract,
            started_at=_now(),
            status=JOB_STATUS_FAILED,
            error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
        )
