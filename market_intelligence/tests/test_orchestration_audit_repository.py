"""Tests for market_intelligence.storage.orchestration_audit_repository.

Every repository under test is pointed at an isolated temporary directory
via a Settings whose project_data_path is tmp_path -- never the real
repository database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.orchestration.contracts import AlpacaNewsJobParams, JobContract
from market_intelligence.orchestration.results import JOB_STATUS_SUCCEEDED, JobResult
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.orchestration_audit_repository import (
    OrchestrationAuditError,
    OrchestrationAuditRepository,
    job_run_id,
)

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def isolated_settings(tmp_path: Path, isolated_env_file: Path) -> Settings:
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def initialized_repository(tmp_path: Path, isolated_env_file: Path) -> OrchestrationAuditRepository:
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return OrchestrationAuditRepository(settings=settings)


def contract() -> JobContract:
    return JobContract(
        job_id="alpaca_news_spy",
        job_type="alpaca_news",
        enabled=True,
        provider="alpaca",
        dataset_name="news",
        params=AlpacaNewsJobParams(symbol="SPY", limit=10),
    )


def make_result(**overrides) -> JobResult:
    defaults = dict(
        job_id="alpaca_news_spy",
        job_type="alpaca_news",
        provider="alpaca",
        dataset_name="news",
        status=JOB_STATUS_SUCCEEDED,
        error_category=None,
        received=1,
        inserted=1,
        existing_or_updated=0,
        failed=0,
        ingestion_run_id="run-123",
        started_at=datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 23, 12, 0, 5, tzinfo=UTC),
    )
    defaults.update(overrides)
    return JobResult(**defaults)


def fetch_run_row(db_path: Path, orchestration_run_id: str):
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return connection.execute(
            "SELECT status, started_at_utc, completed_at_utc, code_version, schema_version "
            "FROM orchestration_runs WHERE orchestration_run_id = ?",
            [orchestration_run_id],
        ).fetchone()
    finally:
        connection.close()


def fetch_job_row(db_path: Path, orchestration_job_run_id: str):
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return connection.execute(
            "SELECT status, job_id, job_type, provider, dataset_name, error_category, "
            "records_received, records_inserted, records_existing, records_failed, "
            "ingestion_run_id FROM orchestration_job_runs WHERE orchestration_job_run_id = ?",
            [orchestration_job_run_id],
        ).fetchone()
    finally:
        connection.close()


# --- run lifecycle -----------------------------------------------------------------


def test_start_run_records_running_status(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    started_at = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

    repository.start_run(orchestration_run_id="run-1", started_at=started_at)

    row = fetch_run_row(repository.database_path, "run-1")
    assert row[0] == "running"
    assert row[2] is None  # completed_at_utc


def test_complete_run_records_final_status(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    started_at = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    completed_at = datetime(2026, 8, 23, 12, 5, 0, tzinfo=UTC)

    repository.start_run(orchestration_run_id="run-1", started_at=started_at)
    repository.complete_run(
        orchestration_run_id="run-1", status="succeeded", completed_at=completed_at
    )

    row = fetch_run_row(repository.database_path, "run-1")
    assert row[0] == "succeeded"
    assert row[2] is not None


def test_run_never_falsely_left_running_after_failed_completion(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    started_at = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    completed_at = datetime(2026, 8, 23, 12, 5, 0, tzinfo=UTC)

    repository.start_run(orchestration_run_id="run-1", started_at=started_at)
    repository.complete_run(
        orchestration_run_id="run-1", status="failed", completed_at=completed_at
    )

    row = fetch_run_row(repository.database_path, "run-1")
    assert row[0] == "failed"


def test_run_status_rejects_invalid_value_via_check_constraint(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.start_run(orchestration_run_id="run-1", started_at=datetime.now(UTC))

    with pytest.raises(OrchestrationAuditError):
        repository.complete_run(
            orchestration_run_id="run-1", status="not_a_status", completed_at=datetime.now(UTC)
        )


# --- job lifecycle -----------------------------------------------------------------


def test_start_job_records_running_status(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.start_run(orchestration_run_id="run-1", started_at=datetime.now(UTC))

    repository.start_job(
        orchestration_run_id="run-1", contract=contract(), started_at=datetime.now(UTC)
    )

    row = fetch_job_row(
        repository.database_path, job_run_id(orchestration_run_id="run-1", job_id="alpaca_news_spy")
    )
    assert row[0] == "running"
    assert row[1] == "alpaca_news_spy"
    assert row[2] == "alpaca_news"


def test_complete_job_records_final_status_and_counts(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.start_run(orchestration_run_id="run-1", started_at=datetime.now(UTC))
    repository.start_job(
        orchestration_run_id="run-1", contract=contract(), started_at=datetime.now(UTC)
    )

    repository.complete_job(orchestration_run_id="run-1", result=make_result())

    row = fetch_job_row(
        repository.database_path, job_run_id(orchestration_run_id="run-1", job_id="alpaca_news_spy")
    )
    assert row[0] == "succeeded"
    assert row[6] == 1  # records_received
    assert row[7] == 1  # records_inserted
    assert row[10] == "run-123"  # ingestion_run_id


def test_job_status_rejects_invalid_value_via_check_constraint(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.start_run(orchestration_run_id="run-1", started_at=datetime.now(UTC))
    repository.start_job(
        orchestration_run_id="run-1", contract=contract(), started_at=datetime.now(UTC)
    )

    with pytest.raises(OrchestrationAuditError):
        repository.complete_job(
            orchestration_run_id="run-1", result=make_result(status="not_a_status")
        )


def test_job_run_id_is_stable_and_scoped_to_run():
    assert job_run_id(orchestration_run_id="run-1", job_id="a") != job_run_id(
        orchestration_run_id="run-2", job_id="a"
    )


# --- multiple jobs in one run --------------------------------------------------------


def test_multiple_jobs_in_one_run_are_independently_tracked(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.start_run(orchestration_run_id="run-1", started_at=datetime.now(UTC))

    other_contract = JobContract(
        job_id="alpaca_bars_spy_5min",
        job_type="alpaca_bars",
        enabled=True,
        provider="alpaca",
        dataset_name="bars",
        params=__import__(
            "market_intelligence.orchestration.contracts", fromlist=["AlpacaBarsJobParams"]
        ).AlpacaBarsJobParams(
            symbol="SPY",
            timeframe="5Min",
            lookback_days=5,
            limit=500,
            max_pages=1,
            feed="iex",
            adjustment="raw",
            currency="USD",
        ),
    )

    repository.start_job(
        orchestration_run_id="run-1", contract=contract(), started_at=datetime.now(UTC)
    )
    repository.start_job(
        orchestration_run_id="run-1", contract=other_contract, started_at=datetime.now(UTC)
    )
    repository.complete_job(orchestration_run_id="run-1", result=make_result(status="failed"))
    repository.complete_job(
        orchestration_run_id="run-1",
        result=make_result(job_id="alpaca_bars_spy_5min", job_type="alpaca_bars"),
    )

    news_row = fetch_job_row(
        repository.database_path, job_run_id(orchestration_run_id="run-1", job_id="alpaca_news_spy")
    )
    bars_row = fetch_job_row(
        repository.database_path,
        job_run_id(orchestration_run_id="run-1", job_id="alpaca_bars_spy_5min"),
    )
    assert news_row[0] == "failed"
    assert bars_row[0] == "succeeded"


# --- sanitization ------------------------------------------------------------------


def test_connection_failure_raises_sanitized_error(tmp_path, isolated_env_file, monkeypatch):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-CONNECT-DETAIL"

    def fake_connect(*args, **kwargs):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(duckdb, "connect", fake_connect)

    with pytest.raises(OrchestrationAuditError) as exc_info:
        repository.start_run(orchestration_run_id="run-1", started_at=datetime.now(UTC))

    assert secret_marker not in str(exc_info.value)
