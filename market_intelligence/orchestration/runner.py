"""Orchestration run planning and execution.

``build_plan`` is pure: it never touches ``Settings``, a client, the
database, or the run lock -- it only reads already-loaded job contracts and
one injected clock. ``execute_run`` is the only function in this package
that acquires the run lock, opens the database, and writes the
orchestration audit trail; it never invokes a job adapter through a shell
or subprocess, and it isolates one job's failure from every other selected
job: a failure in one job never prevents a later selected job in the same
run from running, and each job's own repository call keeps its own
independent database transaction (see the individual repositories).

Overall run status is always derived truthfully from the selected jobs'
own outcomes: the run is only ever reported ``succeeded`` if every
selected job's status is ``succeeded`` or ``skipped``; if any job's status
is ``failed``, the whole run is reported ``failed``, even if other jobs in
the same run succeeded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from market_intelligence.config.settings import Settings
from market_intelligence.orchestration.adapters import run_job
from market_intelligence.orchestration.clock import Clock, resolve_as_of, system_clock
from market_intelligence.orchestration.contracts import (
    AlpacaBarsJobParams,
    FredObservationsJobParams,
    JobContract,
)
from market_intelligence.orchestration.lock import RunLock, default_lock_path
from market_intelligence.orchestration.results import (
    ERROR_CATEGORY_UNEXPECTED_ERROR,
    JOB_STATUS_FAILED,
    JOB_STATUS_SKIPPED,
    JOB_STATUS_SUCCEEDED,
    PlanEntry,
    RunResult,
    failed_job_result,
)
from market_intelligence.orchestration.windows import bars_window, fred_observations_window
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.orchestration_audit_repository import (
    OrchestrationAuditRepository,
)

_TERMINAL_NON_FAILURE_STATUSES = frozenset({JOB_STATUS_SUCCEEDED, JOB_STATUS_SKIPPED})


class OrchestrationRunnerError(RuntimeError):
    """Sanitized fatal failure before any job could be attempted.

    Raised only for a failure that occurs before a durable orchestration
    run record can even be relied upon (e.g. database initialization
    failing before the audit trail can be opened). Never includes a raw
    exception message.
    """


def build_plan(
    contracts: tuple[JobContract, ...], *, clock: Clock = system_clock
) -> tuple[PlanEntry, ...]:
    """Build a sanitized, read-only dry-run plan for the given contracts.

    Never constructs ``Settings``, a client, a database connection, or a
    lock -- safe to call with zero network/database/credential activity of
    any kind. Calls ``clock`` exactly once (see
    ``market_intelligence.orchestration.clock.resolve_as_of``) and reuses
    that one resolved, UTC-normalized instant for every contract's window.
    """
    as_of = resolve_as_of(clock)
    entries = []
    for contract in contracts:
        window_start: str | None = None
        window_end: str | None = None
        if isinstance(contract.params, AlpacaBarsJobParams):
            window_start, window_end = bars_window(as_of, contract.params.lookback_days)
        elif isinstance(contract.params, FredObservationsJobParams):
            window_start, window_end = fred_observations_window(
                as_of, contract.params.lookback_days
            )
        entries.append(
            PlanEntry(
                job_id=contract.job_id,
                job_type=contract.job_type,
                enabled=contract.enabled,
                provider=contract.provider,
                dataset_name=contract.dataset_name,
                requested_window_start=window_start,
                requested_window_end=window_end,
            )
        )
    return tuple(entries)


def execute_run(
    contracts: tuple[JobContract, ...],
    *,
    settings: Settings,
    clock: Clock = system_clock,
) -> RunResult:
    """Acquire the run lock, then execute every selected job in order.

    Runs jobs strictly in the order given (the caller is responsible for
    passing contracts in deterministic order -- see
    ``market_intelligence/orchestration/config.py``). Raises
    ``market_intelligence.orchestration.lock.OrchestrationLockContentionError``/
    ``OrchestrationLockError`` if the run lock cannot be acquired (dry runs
    never call this function, so they never acquire the lock), or
    ``OrchestrationRunnerError`` if the database cannot be initialized
    before the audit trail can even be opened, or
    ``market_intelligence.storage.orchestration_audit_repository.OrchestrationAuditError``
    if the audit trail itself cannot be durably written. The run lock is
    always released before returning or raising, regardless of outcome.

    Rejects an empty ``contracts`` tuple and calls ``clock`` exactly once
    (see ``market_intelligence.orchestration.clock.resolve_as_of``) before
    the lock is even acquired; the one resolved, UTC-normalized instant is
    then reused for every selected job's window in this run, so a run
    combining e.g. a bars job and a FRED job cannot resolve two different
    "as-of" instants for the same run.
    """
    if not contracts:
        raise OrchestrationRunnerError("No jobs were selected to execute.")

    as_of = resolve_as_of(clock)

    def _shared_clock() -> datetime:
        return as_of

    lock = RunLock(default_lock_path(settings))
    lock.acquire()
    try:
        try:
            DuckDBManager(settings=settings).initialize()
        except Exception:
            raise OrchestrationRunnerError(
                "Failed to initialize the local database before the orchestration run "
                "could start."
            ) from None

        audit = OrchestrationAuditRepository(settings=settings)
        orchestration_run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        audit.start_run(orchestration_run_id=orchestration_run_id, started_at=started_at)

        job_results = []
        for contract in contracts:
            audit.start_job(
                orchestration_run_id=orchestration_run_id,
                contract=contract,
                started_at=datetime.now(UTC),
            )
            try:
                job_result = run_job(contract, settings=settings, clock=_shared_clock)
            except Exception:
                # Defense in depth: run_job/its adapters are documented to
                # already catch everything and never raise.
                job_result = failed_job_result(
                    contract,
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                    error_category=ERROR_CATEGORY_UNEXPECTED_ERROR,
                )
            audit.complete_job(orchestration_run_id=orchestration_run_id, result=job_result)
            job_results.append(job_result)

        overall_status = (
            JOB_STATUS_SUCCEEDED
            if all(result.status in _TERMINAL_NON_FAILURE_STATUSES for result in job_results)
            else JOB_STATUS_FAILED
        )
        completed_at = datetime.now(UTC)
        audit.complete_run(
            orchestration_run_id=orchestration_run_id,
            status=overall_status,
            completed_at=completed_at,
        )

        return RunResult(
            orchestration_run_id=orchestration_run_id,
            status=overall_status,
            job_results=tuple(job_results),
            started_at=started_at,
            completed_at=completed_at,
        )
    finally:
        lock.release()
