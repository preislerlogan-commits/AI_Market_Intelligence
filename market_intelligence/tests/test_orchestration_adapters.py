"""Tests for market_intelligence.orchestration.adapters.

These tests never make a live HTTP request (httpx.Client.send is always
monkeypatched) and never touch the real repository database (Settings is
always pointed at an isolated tmp_path).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.orchestration.adapters import (
    run_alpaca_bars_job,
    run_alpaca_news_job,
    run_fred_observations_job,
    run_job,
)
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


def unconfigured_settings(tmp_path: Path, isolated_env_file: Path) -> Settings:
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def alpaca_configured_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "unit-test-secret")
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def fred_configured_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("FRED_API_KEY", "unit-test-fred-key")
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def fixed_clock():
    return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


def news_contract(**overrides) -> JobContract:
    defaults = dict(
        job_id="alpaca_news_spy",
        job_type="alpaca_news",
        enabled=True,
        provider="alpaca",
        dataset_name="news",
        params=AlpacaNewsJobParams(symbol="SPY", limit=10),
    )
    defaults.update(overrides)
    return JobContract(**defaults)


def bars_contract(**overrides) -> JobContract:
    defaults = dict(
        job_id="alpaca_bars_spy_5min",
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
    defaults.update(overrides)
    return JobContract(**defaults)


def fred_contract(**overrides) -> JobContract:
    defaults = dict(
        job_id="fred_fedfunds_observations",
        job_type="fred_observations",
        enabled=True,
        provider="fred",
        dataset_name="macro_observations",
        params=FredObservationsJobParams(
            series_id="FEDFUNDS", lookback_days=90, limit=1000, max_pages=1
        ),
    )
    defaults.update(overrides)
    return JobContract(**defaults)


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


def bars_payload(bars: list[dict]) -> dict:
    return {"bars": bars, "next_page_token": None}


def make_bar(minute: int = 30) -> dict:
    return {
        "t": f"2026-08-20T09:{minute:02d}:00Z",
        "o": 100.0,
        "h": 101.0,
        "l": 99.0,
        "c": 100.5,
        "v": 1000,
        "n": 50,
        "vw": 100.2,
    }


def observations_payload(rows: list[tuple[str, str]]) -> dict:
    observations = [
        {"realtime_start": "2026-08-20", "realtime_end": "2026-08-20", "date": d, "value": v}
        for d, v in rows
    ]
    return {
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "observation_start": "1600-01-01",
        "observation_end": "9999-12-31",
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "asc",
        "count": len(observations),
        "offset": 0,
        "limit": 1000,
        "observations": observations,
    }


def fake_send_factory(payload: dict, status_code: int = 200):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    return fake_send


def count_rows(db_path: Path, table: str) -> int:
    connection = duckdb.connect(str(db_path), read_only=True)
    try:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


# --- not configured ---------------------------------------------------------------


def test_news_job_not_configured_returns_failed(tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    result = run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)
    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_NOT_CONFIGURED


def test_bars_job_not_configured_returns_failed(tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    result = run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)
    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_NOT_CONFIGURED


def test_fred_job_not_configured_returns_failed(tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    result = run_fred_observations_job(fred_contract(), settings=settings, clock=fixed_clock)
    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_NOT_CONFIGURED


def test_not_configured_makes_zero_database_writes(tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)
    from market_intelligence.storage.database import default_database_path

    assert not default_database_path(settings).exists()


# --- successful path: news --------------------------------------------------------


def test_news_job_success_stores_articles(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SUCCEEDED
    assert result.received == 1
    assert result.inserted == 1
    assert result.ingestion_run_id is not None
    assert count_rows(result_db_path(settings), "news_articles") == 1


def result_db_path(settings: Settings) -> Path:
    from market_intelligence.storage.database import default_database_path

    return default_database_path(settings)


def test_news_job_empty_result_is_skipped_with_zero_writes(
    monkeypatch, tmp_path, isolated_env_file
):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SKIPPED
    assert result.received == 0
    assert not result_db_path(settings).exists()


def test_news_job_provider_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory({"message": "boom"}, 500))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_PROVIDER_ERROR


def test_news_job_storage_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    from market_intelligence.storage.news_repository import NewsArticleRepository, NewsStorageError

    def fake_store(self, items, *, provider="alpaca", dataset_name="news"):
        raise NewsStorageError("simulated storage failure with SECRET-DETAIL")

    monkeypatch.setattr(NewsArticleRepository, "store_news_items", fake_store)

    result = run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_STORAGE_ERROR


def test_news_job_unexpected_storage_exception_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file
):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    from market_intelligence.storage.news_repository import NewsArticleRepository

    def fake_store(self, items, *, provider="alpaca", dataset_name="news"):
        raise RuntimeError("boom SECRET-DETAIL")

    monkeypatch.setattr(NewsArticleRepository, "store_news_items", fake_store)

    result = run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_UNEXPECTED_ERROR


# --- successful path: bars ---------------------------------------------------------


def test_bars_job_success_stores_bars(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(bars_payload([make_bar(30)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SUCCEEDED
    assert result.received == 1
    assert result.inserted == 1
    assert count_rows(result_db_path(settings), "market_bars") == 1


def test_bars_job_empty_result_is_skipped(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(bars_payload([])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SKIPPED
    assert not result_db_path(settings).exists()


def test_bars_job_provider_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory({"message": "boom"}, 500))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_PROVIDER_ERROR


def test_bars_job_storage_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(bars_payload([make_bar(30)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    from market_intelligence.storage.bar_repository import BarRepository, BarStorageError

    def fake_store(self, items, *, provider="alpaca", dataset_name="bars"):
        raise BarStorageError("simulated storage failure SECRET-DETAIL")

    monkeypatch.setattr(BarRepository, "store_bars", fake_store)

    result = run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_STORAGE_ERROR


def test_bars_job_uses_shared_as_of_clock_for_window(monkeypatch, tmp_path, isolated_env_file):
    captured = {}

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        captured["start"] = request.url.params["start"]
        captured["end"] = request.url.params["end"]
        return httpx.Response(200, json=bars_payload([]), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)

    assert captured["end"] == "2026-08-23T00:00:00Z"
    assert captured["start"] == "2026-08-18T00:00:00Z"


# --- successful path: fred ---------------------------------------------------------


def test_fred_job_success_stores_observations(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(
        httpx.Client, "send", fake_send_factory(observations_payload([("2026-08-01", "5.33")]))
    )
    settings = fred_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_fred_observations_job(fred_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SUCCEEDED
    assert result.received == 1
    assert result.inserted == 1
    assert count_rows(result_db_path(settings), "macro_observations") == 1


def test_fred_job_empty_result_is_skipped_without_calling_repository(
    monkeypatch, tmp_path, isolated_env_file
):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(observations_payload([])))
    settings = fred_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_fred_observations_job(fred_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_SKIPPED
    # MacroObservationRepository.store_observations rejects an empty batch;
    # the adapter must never call it in this case.
    assert not result_db_path(settings).exists()


def test_fred_job_provider_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory({"error_message": "bad"}, 400))
    settings = fred_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_fred_observations_job(fred_contract(), settings=settings, clock=fixed_clock)

    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_PROVIDER_ERROR


def test_fred_job_uses_shared_as_of_clock_for_window(monkeypatch, tmp_path, isolated_env_file):
    captured = {}

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        captured["start"] = request.url.params["observation_start"]
        captured["end"] = request.url.params["observation_end"]
        return httpx.Response(200, json=observations_payload([]), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    settings = fred_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    run_fred_observations_job(fred_contract(), settings=settings, clock=fixed_clock)

    assert captured["end"] == "2026-08-22"
    assert captured["start"] == "2026-05-24"


# --- dispatch via run_job -----------------------------------------------------------


def test_run_job_dispatches_by_job_type(monkeypatch, tmp_path, isolated_env_file):
    monkeypatch.setattr(httpx.Client, "send", fake_send_factory(news_payload([make_article(1)])))
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    result = run_job(news_contract(), settings=settings, clock=fixed_clock)
    assert result.status == JOB_STATUS_SUCCEEDED
    assert result.job_type == "alpaca_news"


def test_run_job_never_raises_even_if_adapter_misbehaves(monkeypatch, tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    def broken_adapter(contract, *, settings, clock):
        raise RuntimeError("simulated adapter bug with SECRET-DETAIL")

    monkeypatch.setitem(
        __import__(
            "market_intelligence.orchestration.adapters", fromlist=["_ADAPTERS_BY_JOB_TYPE"]
        )._ADAPTERS_BY_JOB_TYPE,
        "alpaca_news",
        broken_adapter,
    )

    result = run_job(news_contract(), settings=settings, clock=fixed_clock)
    assert result.status == JOB_STATUS_FAILED
    assert result.error_category == ERROR_CATEGORY_UNEXPECTED_ERROR
    assert "SECRET-DETAIL" not in repr(result)


# --- no live HTTP requests guard -----------------------------------------------------


def test_no_adapter_makes_a_live_http_request_when_not_configured(tmp_path, isolated_env_file):
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    calls = []

    def blocked_send(self, request, **kwargs):
        calls.append(request)
        raise AssertionError("Unexpected live HTTP request")

    import httpx as httpx_module

    original = httpx_module.Client.send
    httpx_module.Client.send = blocked_send
    try:
        run_alpaca_news_job(news_contract(), settings=settings, clock=fixed_clock)
        run_alpaca_bars_job(bars_contract(), settings=settings, clock=fixed_clock)
        run_fred_observations_job(fred_contract(), settings=settings, clock=fixed_clock)
    finally:
        httpx_module.Client.send = original

    assert calls == []
