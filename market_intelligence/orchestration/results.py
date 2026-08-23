"""Sanitized result/status types shared across the orchestration layer.

Every field on every type here is a plain identifier, enum-like string,
count, or timestamp -- never a credential, provider value, headline, URL,
OHLCV value, observation value, SQL, database row, or raw exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from market_intelligence.orchestration.contracts import JobContract

JOB_STATUS_PLANNED = "planned"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_SUCCEEDED = "succeeded"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_SKIPPED = "skipped"
JOB_STATUSES = (
    JOB_STATUS_PLANNED,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCEEDED,
    JOB_STATUS_FAILED,
    JOB_STATUS_SKIPPED,
)

ERROR_CATEGORY_NOT_CONFIGURED = "not_configured"
ERROR_CATEGORY_PROVIDER_ERROR = "provider_error"
ERROR_CATEGORY_STORAGE_ERROR = "storage_error"
ERROR_CATEGORY_UNEXPECTED_ERROR = "unexpected_error"


@dataclass(frozen=True)
class PlanEntry:
    """One job's sanitized dry-run plan entry.

    Built purely from an already-loaded ``JobContract`` and one injected
    clock -- never touches ``Settings``, a client, the network, the
    database, or the run lock.
    """

    job_id: str
    job_type: str
    enabled: bool
    provider: str
    dataset_name: str
    requested_window_start: str | None
    requested_window_end: str | None


@dataclass(frozen=True)
class JobResult:
    """Sanitized result of running one job adapter.

    Never includes credentials, provider values, headlines, URLs, OHLCV
    values, or observation values -- only identifiers, a status, a fixed
    error category, and sanitized counts.
    """

    job_id: str
    job_type: str
    provider: str
    dataset_name: str
    status: str
    error_category: str | None
    received: int
    inserted: int
    existing_or_updated: int
    failed: int
    ingestion_run_id: str | None
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True)
class RunResult:
    """Sanitized result of one orchestration run."""

    orchestration_run_id: str
    status: str
    job_results: tuple[JobResult, ...]
    started_at: datetime
    completed_at: datetime


def failed_job_result(
    contract: JobContract, *, started_at: datetime, completed_at: datetime, error_category: str
) -> JobResult:
    """Build a sanitized failed ``JobResult`` with zero counts.

    Used only for the orchestration boundary's defense-in-depth path (a job
    adapter is documented to always catch its own exceptions and never
    raise; this exists only in case that contract is ever violated by a
    future change).
    """
    return JobResult(
        job_id=contract.job_id,
        job_type=contract.job_type,
        provider=contract.provider,
        dataset_name=contract.dataset_name,
        status=JOB_STATUS_FAILED,
        error_category=error_category,
        received=0,
        inserted=0,
        existing_or_updated=0,
        failed=0,
        ingestion_run_id=None,
        started_at=started_at,
        completed_at=completed_at,
    )
