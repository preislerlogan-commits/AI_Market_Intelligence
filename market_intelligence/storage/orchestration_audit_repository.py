"""Persists the orchestration audit trail (migration 0007) into DuckDB.

Never makes network requests and never accepts, stores, or logs
credentials. Stores only orchestration run/job identifiers, provider/
dataset labels, timing, status, a sanitized error category, and sanitized
counts -- never credentials, URLs/query parameters, raw responses/errors,
headlines/summaries, OHLCV values, macro values, prompts/model outputs,
recommendations, or order/execution data. Assumes the database has already
been brought up to at least migration 0007; this class only writes rows,
it never applies migrations itself.

Unlike the per-dataset repositories (``NewsArticleRepository``,
``BarRepository``, ``MacroObservationRepository``), this repository does
not write one atomic batch inside a single transaction: an orchestration
run's audit trail is written incrementally, in real time, as jobs are
started/completed, potentially minutes apart and interleaved with each
job's own separate repository transaction. Each write here is its own
short-lived connection and statement, so a durable "running" row can exist
before later jobs are even attempted -- which is exactly what lets a crash
mid-run still leave a truthful (if incomplete) audit record, rather than a
silently-absent one.

Any failure here raises a sanitized ``OrchestrationAuditError`` -- never a
raw DuckDB exception, SQL text, database path, or credential. A failure
while recording the audit trail is treated as fatal to the enclosing
orchestration run (see ``market_intelligence/orchestration/runner.py``): it
indicates an infrastructure problem with the audit trail itself, not a
single job's provider/storage failure, so it is not subject to the
per-job failure-isolation guarantee that governs job adapters.

``complete_run``/``complete_job`` only ever transition a row that is still
``running`` (an atomic conditional ``UPDATE ... WHERE status = 'running'
... RETURNING``), and both verify that a row was actually returned before
reporting success -- a missing or already-terminal row raises a sanitized
``OrchestrationAuditError`` instead of silently reporting a false
completion. Consequently, if this process crashes or is fatally
interrupted between starting a run/job and completing it, that row is left
durably ``running`` forever: this is a deliberate, truthful signal that the
run/job was interrupted and its outcome is unknown, not a claim that it
succeeded or failed -- an operator must review it manually. No automatic
recovery/reconciliation of such a row is implemented here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.orchestration.contracts import JobContract
from market_intelligence.orchestration.results import JobResult
from market_intelligence.storage.database import DuckDBManager

CODE_VERSION = "orchestration_audit_repository_v1"


class OrchestrationAuditError(RuntimeError):
    """Sanitized orchestration-audit storage failure.

    Never includes database internals, credentials, or raw exception text.
    """


def _to_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(UTC)
    return value.replace(tzinfo=None)


def job_run_id(*, orchestration_run_id: str, job_id: str) -> str:
    """The stable, idempotent identity for one job within one orchestration run."""
    return f"{orchestration_run_id}:{job_id}"


class OrchestrationAuditRepository:
    """Persists orchestration run/job audit rows (migration 0007)."""

    def __init__(
        self, settings: Settings | None = None, database_path: Path | None = None
    ) -> None:
        self._settings = settings or Settings()
        self._manager = DuckDBManager(settings=self._settings, database_path=database_path)

    @property
    def database_path(self) -> Path:
        return self._manager.database_path

    @staticmethod
    def _schema_version(connection: duckdb.DuckDBPyConnection) -> str | None:
        row = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def start_run(self, *, orchestration_run_id: str, started_at: datetime) -> None:
        """Record a new orchestration run as ``running``."""
        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise OrchestrationAuditError(
                "Failed to open the local database connection to start the orchestration run."
            ) from None
        try:
            schema_version = self._schema_version(connection)
            connection.execute(
                "INSERT INTO orchestration_runs "
                "(orchestration_run_id, started_at_utc, status, code_version, schema_version) "
                "VALUES (?, ?, 'running', ?, ?)",
                [orchestration_run_id, _to_naive_utc(started_at), CODE_VERSION, schema_version],
            )
        except Exception:
            raise OrchestrationAuditError(
                "Failed to record the orchestration run as running."
            ) from None
        finally:
            connection.close()

    def complete_run(
        self, *, orchestration_run_id: str, status: str, completed_at: datetime
    ) -> None:
        """Record an orchestration run's final, truthfully-derived overall status.

        Only transitions the row if it is still ``running``; raises
        ``OrchestrationAuditError`` if no such running row exists (already
        terminal, or missing) rather than silently reporting success.
        """
        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise OrchestrationAuditError(
                "Failed to open the local database connection to complete the orchestration run."
            ) from None
        try:
            rows = connection.execute(
                "UPDATE orchestration_runs SET status = ?, completed_at_utc = ? "
                "WHERE orchestration_run_id = ? AND status = 'running' "
                "RETURNING orchestration_run_id",
                [status, _to_naive_utc(completed_at), orchestration_run_id],
            ).fetchall()
        except Exception:
            raise OrchestrationAuditError(
                "Failed to record the orchestration run's final status."
            ) from None
        finally:
            try:
                connection.close()
            except Exception:
                pass

        if not rows:
            raise OrchestrationAuditError(
                "Failed to record the orchestration run's final status: no matching "
                "running run was found (already terminal or missing)."
            )

    def start_job(
        self, *, orchestration_run_id: str, contract: JobContract, started_at: datetime
    ) -> None:
        """Record one job of a run as ``running``."""
        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise OrchestrationAuditError(
                "Failed to open the local database connection to start a job run."
            ) from None
        try:
            schema_version = self._schema_version(connection)
            connection.execute(
                "INSERT INTO orchestration_job_runs "
                "(orchestration_job_run_id, orchestration_run_id, job_id, job_type, provider, "
                "dataset_name, started_at_utc, status, code_version, schema_version) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)",
                [
                    job_run_id(orchestration_run_id=orchestration_run_id, job_id=contract.job_id),
                    orchestration_run_id,
                    contract.job_id,
                    contract.job_type,
                    contract.provider,
                    contract.dataset_name,
                    _to_naive_utc(started_at),
                    CODE_VERSION,
                    schema_version,
                ],
            )
        except Exception:
            raise OrchestrationAuditError("Failed to record a job run as running.") from None
        finally:
            connection.close()

    def complete_job(self, *, orchestration_run_id: str, result: JobResult) -> None:
        """Record one job's final, truthful status/counts.

        Only transitions the row if it is still ``running``; raises
        ``OrchestrationAuditError`` if no such running row exists (already
        terminal, or missing) rather than silently reporting success.
        """
        try:
            connection = duckdb.connect(str(self._manager.database_path))
        except Exception:
            raise OrchestrationAuditError(
                "Failed to open the local database connection to complete a job run."
            ) from None
        try:
            rows = connection.execute(
                "UPDATE orchestration_job_runs SET status = ?, completed_at_utc = ?, "
                "error_category = ?, records_received = ?, records_inserted = ?, "
                "records_existing = ?, records_failed = ?, ingestion_run_id = ? "
                "WHERE orchestration_job_run_id = ? AND status = 'running' "
                "RETURNING orchestration_job_run_id",
                [
                    result.status,
                    _to_naive_utc(result.completed_at),
                    result.error_category,
                    result.received,
                    result.inserted,
                    result.existing_or_updated,
                    result.failed,
                    result.ingestion_run_id,
                    job_run_id(orchestration_run_id=orchestration_run_id, job_id=result.job_id),
                ],
            ).fetchall()
        except Exception:
            raise OrchestrationAuditError("Failed to record a job run's final status.") from None
        finally:
            try:
                connection.close()
            except Exception:
                pass

        if not rows:
            raise OrchestrationAuditError(
                "Failed to record a job run's final status: no matching running job run "
                "was found (already terminal or missing)."
            )
