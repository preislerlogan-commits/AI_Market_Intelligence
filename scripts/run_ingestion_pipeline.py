"""Dry-run-first ingestion orchestration entry point.

Runs the existing reviewed Alpaca news, Alpaca bars, and FRED observations
pipelines through explicit, strictly validated job contracts (see
``market_intelligence/orchestration/``). This script is groundwork for
future specialized agents -- it does not itself build any AI agent,
analysis, prediction, recommendation, scheduling, or Robinhood/brokerage
integration.

Default behavior is a dry run: it validates job selection, builds a
sanitized plan, and prints it -- zero network requests, zero database
writes, zero migrations, and zero credential reads, since dry-run planning
never constructs ``Settings``, a client, or a database connection. Real
network/database execution requires the explicit ``--execute`` flag.

Exactly one of ``--job JOB_ID`` or ``--all`` is always required (this also
trivially satisfies "reject --execute without exactly one of --job or
--all", since that condition can then never occur). An unknown, duplicate,
blank, malformed, or disabled job ID is rejected before ``Settings``, any
client, the network, the database, or the run lock are ever constructed --
job selection is validated purely against the committed, already-loaded
job configuration.

Never invokes an ingestion script through a shell or subprocess: job
adapters (``market_intelligence/orchestration/adapters.py``) call the
reviewed connector/repository Python classes directly.

Dry-run output is limited to job/run identifiers, status categories,
counts, and the computed (but not yet requested) window boundaries --
never credentials, provider values, headlines, URLs, OHLCV values,
observation values, SQL, or database rows. Execute-mode output is limited
to the same categories, plus timing is recorded (not printed) in the
orchestration audit trail.
"""

from __future__ import annotations

import argparse

from market_intelligence.config.settings import Settings
from market_intelligence.orchestration.clock import Clock, system_clock
from market_intelligence.orchestration.config import JobConfigError, load_job_contracts
from market_intelligence.orchestration.contracts import JobContract
from market_intelligence.orchestration.lock import (
    OrchestrationLockContentionError,
    OrchestrationLockError,
)
from market_intelligence.orchestration.results import JOB_STATUS_SUCCEEDED
from market_intelligence.orchestration.runner import (
    OrchestrationRunnerError,
    build_plan,
    execute_run,
)
from market_intelligence.storage.orchestration_audit_repository import OrchestrationAuditError


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run-first orchestration over reviewed ingestion job contracts."
    )
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job", dest="job_id", default=None, help="Run exactly one job by ID.")
    selection.add_argument("--all", action="store_true", help="Run every enabled job.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Actually run the selected job(s). Without this flag, only a "
            "dry-run plan is printed."
        ),
    )
    return parser.parse_args(argv)


def _select_contracts(
    contracts: tuple[JobContract, ...], *, job_id: str | None, select_all: bool
) -> tuple[JobContract, ...] | str:
    """Return the selected contracts, or a fixed sanitized error category string.

    Never echoes the raw ``job_id`` value back -- only a fixed category.
    ``--all`` with zero enabled jobs is rejected here, before ``Settings``,
    the lock, the database, migrations, credentials, or the network are
    ever touched -- this check happens purely against the already-loaded,
    committed job configuration.
    """
    if select_all:
        enabled = tuple(contract for contract in contracts if contract.enabled)
        if not enabled:
            return "no_enabled_jobs"
        return enabled

    if not isinstance(job_id, str) or not job_id.strip():
        return "invalid_job_id"

    matches = [contract for contract in contracts if contract.job_id == job_id]
    if not matches:
        return "unknown_job_id"
    contract = matches[0]
    if not contract.enabled:
        return "disabled_job_id"
    return (contract,)


def main(
    argv: list[str] | None = None,
    *,
    settings: Settings | None = None,
    clock: Clock = system_clock,
) -> int:
    """Run one dry-run or execute pass. ``settings``/``clock`` are test injection points only.

    Real usage never passes ``settings`` -- it defaults to a fresh
    ``Settings()`` reading the process environment/local ``.env``, and
    ``Settings`` is only ever constructed for an ``--execute`` run, never
    for a dry run.
    """
    args = _parse_args(argv)

    try:
        contracts = load_job_contracts()
    except JobConfigError:
        print("selection outcome: invalid_configuration")
        return 2

    selection = _select_contracts(contracts, job_id=args.job_id, select_all=args.all)
    if isinstance(selection, str):
        print(f"selection outcome: {selection}")
        return 2
    selected = selection

    if not args.execute:
        plan = build_plan(selected, clock=clock)
        print("mode: dry_run")
        print(f"selected job count: {len(plan)}")
        for entry in plan:
            print(f"- job_id: {entry.job_id}")
            print(f"  job_type: {entry.job_type}")
            print(f"  enabled: {entry.enabled}")
            print(f"  provider: {entry.provider}")
            print(f"  dataset_name: {entry.dataset_name}")
            if entry.requested_window_start is not None:
                print(f"  requested_window_start: {entry.requested_window_start}")
                print(f"  requested_window_end: {entry.requested_window_end}")
        return 0

    print("mode: execute")
    resolved_settings = settings if settings is not None else Settings()
    try:
        run_result = execute_run(selected, settings=resolved_settings, clock=clock)
    except OrchestrationLockContentionError:
        print("execution outcome: lock_contention")
        return 1
    except OrchestrationLockError:
        print("execution outcome: lock_error")
        return 1
    except OrchestrationRunnerError:
        print("execution outcome: initialization_failed")
        return 1
    except OrchestrationAuditError:
        print("execution outcome: audit_error")
        return 1

    print(f"orchestration_run_id: {run_result.orchestration_run_id}")
    print(f"overall status: {run_result.status}")
    for job_result in run_result.job_results:
        print(f"- job_id: {job_result.job_id}")
        print(f"  status: {job_result.status}")
        if job_result.error_category is not None:
            print(f"  error_category: {job_result.error_category}")
        print(f"  received: {job_result.received}")
        print(f"  inserted: {job_result.inserted}")
        print(f"  existing_or_updated: {job_result.existing_or_updated}")
        print(f"  failed: {job_result.failed}")

    return 0 if run_result.status == JOB_STATUS_SUCCEEDED else 1


if __name__ == "__main__":
    raise SystemExit(main())
