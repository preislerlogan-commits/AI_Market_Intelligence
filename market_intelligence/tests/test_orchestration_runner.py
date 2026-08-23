"""Tests for market_intelligence.orchestration.runner.

These tests never make a live HTTP request and never touch the real
repository database -- Settings is always pointed at an isolated tmp_path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.orchestration.contracts import (
    AlpacaBarsJobParams,
    AlpacaNewsJobParams,
    FredObservationsJobParams,
    JobContract,
)
from market_intelligence.orchestration.lock import RunLock, default_lock_path
from market_intelligence.orchestration.results import (
    JOB_STATUS_FAILED,
    JOB_STATUS_SKIPPED,
    JOB_STATUS_SUCCEEDED,
)
from market_intelligence.orchestration.runner import (
    OrchestrationRunnerError,
    build_plan,
    execute_run,
)
from market_intelligence.storage.database import default_database_path

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


def unconfigured_settings(tmp_path: Path, isolated_env_file: Path) -> Settings:
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def alpaca_configured_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "unit-test-secret")
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def fixed_clock():
    return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


def news_contract(job_id: str = "alpaca_news_spy") -> JobContract:
    return JobContract(
        job_id=job_id,
        job_type="alpaca_news",
        enabled=True,
        provider="alpaca",
        dataset_name="news",
        params=AlpacaNewsJobParams(symbol="SPY", limit=10),
    )


def bars_contract(job_id: str = "alpaca_bars_spy_5min") -> JobContract:
    return JobContract(
        job_id=job_id,
        job_type="alpaca_bars",
        enabled=True,
        provider="alpaca",
        dataset_name="bars",
        params=AlpacaBarsJobParams(
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


def fred_contract(job_id: str = "fred_fedfunds_observations") -> JobContract:
    return JobContract(
        job_id=job_id,
        job_type="fred_observations",
        enabled=True,
        provider="fred",
        dataset_name="macro_observations",
        params=FredObservationsJobParams(
            series_id="FEDFUNDS", lookback_days=90, limit=1000, max_pages=1
        ),
    )


def news_payload(articles: list[dict]) -> dict:
    return {"news": articles}


def make_article(article_id: int = 1) -> dict:
    return {
        "id": article_id,
        "headline": "headline",
        "source": "benzinga",
        "url": "https://example.com/a",
        "created_at": "2026-08-20T12:00:00Z",
        "updated_at": "2026-08-20T12:00:00Z",
        "symbols": ["SPY"],
    }


def fake_send_factory(payload: dict, status_code: int = 200):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    return fake_send


def fetch_run_statuses(db_path: Path) -> list[str]:
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return [
            row[0]
            for row in connection.execute("SELECT status FROM orchestration_runs").fetchall()
        ]
    finally:
        connection.close()


def fetch_job_statuses(db_path: Path) -> dict[str, str]:
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = connection.execute("SELECT job_id, status FROM orchestration_job_runs").fetchall()
        return dict(rows)
    finally:
        connection.close()


# --- build_plan is pure -------------------------------------------------------------


def test_build_plan_never_touches_settings_network_or_database(tmp_path, isolated_env_file):
    contracts = (news_contract(), bars_contract(), fred_contract())
    plan = build_plan(contracts, clock=fixed_clock)

    assert len(plan) == 3
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    assert not default_database_path(settings).exists()


def test_build_plan_preserves_input_order():
    contracts = (fred_contract(), news_contract(), bars_contract())
    plan = build_plan(contracts, clock=fixed_clock)
    assert [entry.job_id for entry in plan] == [
        "fred_fedfunds_observations",
        "alpaca_news_spy",
        "alpaca_bars_spy_5min",
    ]


def test_build_plan_computes_windows_for_bars_and_fred_but_not_news():
    plan = build_plan((news_contract(), bars_contract(), fred_contract()), clock=fixed_clock)
    by_id = {entry.job_id: entry for entry in plan}

    assert by_id["alpaca_news_spy"].requested_window_start is None
    assert by_id["alpaca_bars_spy_5min"].requested_window_start == "2026-08-18T00:00:00Z"
    assert by_id["fred_fedfunds_observations"].requested_window_start == "2026-05-24"


def test_build_plan_is_deterministic_for_same_clock():
    contracts = (news_contract(), bars_contract())
    assert build_plan(contracts, clock=fixed_clock) == build_plan(contracts, clock=fixed_clock)


def test_build_plan_calls_clock_exactly_once_for_multiple_contracts():
    calls = []

    def counting_clock():
        calls.append(1)
        return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

    build_plan((news_contract(), bars_contract(), fred_contract()), clock=counting_clock)

    assert len(calls) == 1


# --- execute_run: success path -------------------------------------------------------


def test_execute_run_single_succeeding_job_reports_success(
    monkeypatch, tmp_path, isolated_env_file
):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = execute_run((news_contract(),), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SUCCEEDED
    assert len(result.job_results) == 1
    assert result.job_results[0].status == JOB_STATUS_SUCCEEDED


def test_execute_run_writes_audit_trail(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = execute_run((news_contract(),), settings=settings, clock=fixed_clock)

    db_path = default_database_path(settings)
    assert fetch_run_statuses(db_path) == ["succeeded"]
    assert fetch_job_statuses(db_path) == {"alpaca_news_spy": "succeeded"}
    assert result.orchestration_run_id  # non-empty


def test_execute_run_releases_lock_after_success(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    execute_run((news_contract(),), settings=settings, clock=fixed_clock)

    assert not default_lock_path(settings).exists()


# --- execute_run: failure isolation --------------------------------------------------


def test_one_failed_job_does_not_prevent_a_later_job_from_running(
    monkeypatch, tmp_path, isolated_env_file
):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    # First job (news) is unconfigured-equivalent via a broken send; second
    # job (bars) succeeds via the same monkeypatched transport returning an
    # empty (but successful) bars payload -- distinguished by request path.
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if "/v1beta1/news" in str(request.url):
            return httpx.Response(500, json={"message": "boom"}, request=request)
        return httpx.Response(200, json={"bars": [], "next_page_token": None}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    result = execute_run(
        (news_contract(), bars_contract()), settings=settings, clock=fixed_clock
    )

    statuses = {jr.job_id: jr.status for jr in result.job_results}
    assert statuses["alpaca_news_spy"] == JOB_STATUS_FAILED
    assert statuses["alpaca_bars_spy_5min"] == JOB_STATUS_SKIPPED  # ran, empty result


def test_overall_status_is_failed_if_any_job_failed(monkeypatch, tmp_path, isolated_env_file):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if "/v1beta1/news" in str(request.url):
            return httpx.Response(500, json={"message": "boom"}, request=request)
        return httpx.Response(200, json={"bars": [], "next_page_token": None}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    result = execute_run(
        (news_contract(), bars_contract()), settings=settings, clock=fixed_clock
    )

    assert result.status == JOB_STATUS_FAILED


def test_overall_status_never_succeeded_when_any_job_failed_even_if_others_succeeded(
    monkeypatch, tmp_path, isolated_env_file
):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if "/v1beta1/news" in str(request.url):
            return httpx.Response(200, json=news_payload([make_article(1)]), request=request)
        return httpx.Response(500, json={"message": "boom"}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    result = execute_run(
        (news_contract(), bars_contract()), settings=settings, clock=fixed_clock
    )

    assert result.status == JOB_STATUS_FAILED
    statuses = {jr.job_id: jr.status for jr in result.job_results}
    assert statuses["alpaca_news_spy"] == JOB_STATUS_SUCCEEDED
    assert statuses["alpaca_bars_spy_5min"] == JOB_STATUS_FAILED


def test_execute_run_releases_lock_even_when_a_job_fails(monkeypatch, tmp_path, isolated_env_file):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory({"message": "boom"}, 500))

    execute_run((news_contract(),), settings=settings, clock=fixed_clock)

    assert not default_lock_path(settings).exists()


def test_all_skipped_jobs_yield_overall_succeeded(monkeypatch, tmp_path, isolated_env_file):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([])))

    result = execute_run((news_contract(),), settings=settings, clock=fixed_clock)

    assert result.job_results[0].status == JOB_STATUS_SKIPPED
    assert result.status == JOB_STATUS_SUCCEEDED


# --- shared clock ---------------------------------------------------------------------


def test_execute_run_calls_clock_exactly_once_across_multiple_jobs(
    monkeypatch, tmp_path, isolated_env_file
):
    monkeypatch.setenv("FRED_API_KEY", "unit-test-fred-key")
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        if "/v2/stocks" in str(request.url):
            return httpx.Response(200, json={"bars": [], "next_page_token": None}, request=request)
        return httpx.Response(
            200,
            json={
                "realtime_start": "1776-07-04",
                "realtime_end": "9999-12-31",
                "observation_start": "1600-01-01",
                "observation_end": "9999-12-31",
                "units": "lin",
                "output_type": 1,
                "file_type": "json",
                "order_by": "observation_date",
                "sort_order": "asc",
                "count": 0,
                "offset": 0,
                "limit": 1000,
                "observations": [],
            },
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    calls = []

    def counting_clock():
        calls.append(1)
        return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

    result = execute_run(
        (bars_contract(), fred_contract()), settings=settings, clock=counting_clock
    )

    assert len(calls) == 1
    assert result.status == JOB_STATUS_SUCCEEDED


# --- empty contract selection -----------------------------------------------------------


def test_execute_run_rejects_empty_contracts_tuple(tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    with pytest.raises(OrchestrationRunnerError):
        execute_run((), settings=settings, clock=fixed_clock)


def test_execute_run_empty_contracts_never_touches_lock_or_database(
    tmp_path, isolated_env_file
):
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    with pytest.raises(OrchestrationRunnerError):
        execute_run((), settings=settings, clock=fixed_clock)

    assert not default_lock_path(settings).exists()
    assert not default_database_path(settings).exists()


# --- lock contention -----------------------------------------------------------------


def test_execute_run_fails_when_lock_already_held(monkeypatch, tmp_path, isolated_env_file):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))

    held_lock = RunLock(default_lock_path(settings))
    held_lock.acquire()
    try:
        from market_intelligence.orchestration.lock import OrchestrationLockContentionError

        with pytest.raises(OrchestrationLockContentionError):
            execute_run((news_contract(),), settings=settings, clock=fixed_clock)
    finally:
        held_lock.release()


def test_execute_run_does_not_write_audit_trail_on_lock_contention(
    monkeypatch, tmp_path, isolated_env_file
):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))

    held_lock = RunLock(default_lock_path(settings))
    held_lock.acquire()
    try:
        from market_intelligence.orchestration.lock import OrchestrationLockContentionError

        with pytest.raises(OrchestrationLockContentionError):
            execute_run((news_contract(),), settings=settings, clock=fixed_clock)
    finally:
        held_lock.release()

    assert not default_database_path(settings).exists()


# --- fatal initialization failure ------------------------------------------------------


def test_database_initialization_failure_raises_sanitized_error_and_releases_lock(
    monkeypatch, tmp_path, isolated_env_file
):
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)
    from market_intelligence.orchestration.runner import OrchestrationRunnerError
    from market_intelligence.storage.database import DuckDBManager

    secret_marker = "SECRET-INIT-DETAIL"

    def fake_initialize(self):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(DuckDBManager, "initialize", fake_initialize)

    with pytest.raises(OrchestrationRunnerError) as exc_info:
        execute_run((news_contract(),), settings=settings, clock=fixed_clock)

    assert secret_marker not in str(exc_info.value)
    assert not default_lock_path(settings).exists()
