"""Tests for scripts/run_ingestion_pipeline.py.

These tests must never make a live HTTP request. ``httpx.Client.send`` is
globally monkeypatched to record/raise on any call unless a test explicitly
provides a fake response, and ``Settings`` is always constructed pointed at
a nonexistent ``.env`` file with credential env vars cleared.
"""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from market_intelligence.config.settings import Settings

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_ingestion_pipeline.py"

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


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_ingestion_pipeline", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError("Unexpected live HTTP request from run_ingestion_pipeline test")

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


def fixed_clock():
    return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)


def database_file_exists(settings: Settings) -> bool:
    from market_intelligence.storage.database import default_database_path

    return default_database_path(settings).exists()


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


# --- --job/--all required, mutually exclusive ----------------------------------------


def test_neither_job_nor_all_exits_before_any_request(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    with pytest.raises(SystemExit):
        module.main([])

    assert calls == []


def test_both_job_and_all_is_rejected(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    with pytest.raises(SystemExit):
        module.main(["--job", "alpaca_news_spy", "--all"])

    assert calls == []


def test_execute_without_job_or_all_is_rejected_by_argparse(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    with pytest.raises(SystemExit):
        module.main(["--execute"])

    assert calls == []


# --- unknown/disabled/blank job id -----------------------------------------------------


def test_unknown_job_id_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--job", "not_a_real_job"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_blank_job_id_is_rejected(monkeypatch, tmp_path, isolated_env_file, capsys):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--job", "   "], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == []
    assert "selection outcome: invalid_job_id" in captured.out


def test_unknown_job_id_never_echoed(monkeypatch, tmp_path, isolated_env_file, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    secret_marker = "SECRET-JOB-ID-MARKER"

    module.main(["--job", secret_marker], settings=settings)

    captured = capsys.readouterr()
    assert secret_marker not in captured.out


def test_disabled_job_rejected_using_temporary_config(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    config_path = tmp_path / "jobs.json"
    config_path.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "job_id": "alpaca_news_spy",
                        "job_type": "alpaca_news",
                        "enabled": False,
                        "provider": "alpaca",
                        "dataset_name": "news",
                        "params": {"symbol": "SPY", "limit": 10},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module, "load_job_contracts", lambda: __import__(
            "market_intelligence.orchestration.config", fromlist=["load_job_contracts"]
        ).load_job_contracts(config_path)
    )

    exit_code = module.main(["--job", "alpaca_news_spy"], settings=settings)

    assert exit_code == 2
    assert calls == []


# --- dry run is the default and is safe -----------------------------------------------


def test_dry_run_is_default_and_makes_zero_requests_and_writes(
    monkeypatch, tmp_path, isolated_env_file
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--all"], settings=settings, clock=fixed_clock)

    assert exit_code == 0
    assert calls == []
    assert database_file_exists(settings) is False


def test_dry_run_prints_plan_for_all_enabled_jobs(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--all"], clock=fixed_clock)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "mode: dry_run" in captured.out
    assert "selected job count: 3" in captured.out
    assert "job_id: alpaca_news_spy" in captured.out
    assert "job_id: alpaca_bars_spy_5min" in captured.out
    assert "job_id: fred_fedfunds_observations" in captured.out


def test_dry_run_single_job_selects_only_that_job(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--job", "alpaca_news_spy"], clock=fixed_clock)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "selected job count: 1" in captured.out
    assert "job_id: alpaca_news_spy" in captured.out
    assert "alpaca_bars_spy_5min" not in captured.out


def test_dry_run_never_touches_lock_file(monkeypatch, tmp_path, isolated_env_file):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    from market_intelligence.orchestration.lock import default_lock_path

    module.main(["--all"], settings=settings, clock=fixed_clock)

    assert not default_lock_path(settings).exists()


def test_dry_run_output_never_leaks_credentials_or_internals(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    module.main(["--all"], clock=fixed_clock)

    captured = capsys.readouterr()
    for forbidden in ("APCA-API-KEY-ID", "api_key", "Traceback", "SELECT", "duckdb"):
        assert forbidden not in captured.out


# --- --execute path ----------------------------------------------------------------


def test_execute_not_configured_reports_failure(monkeypatch, tmp_path, isolated_env_file, capsys):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--job", "alpaca_news_spy", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []  # not_configured is detected before any HTTP request
    assert "mode: execute" in captured.out
    assert "overall status: failed" in captured.out


def test_execute_success_reports_success_and_sanitized_counts(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(200, json=news_payload([make_article(1)]), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--job", "alpaca_news_spy", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "overall status: succeeded" in captured.out
    assert "received: 1" in captured.out
    assert "inserted: 1" in captured.out
    assert "unit-test-key" not in captured.out
    assert "unit-test-secret" not in captured.out


def test_execute_lock_contention_reports_sanitized_outcome(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    from market_intelligence.orchestration.lock import RunLock, default_lock_path

    held_lock = RunLock(default_lock_path(settings))
    held_lock.acquire()
    try:
        exit_code = module.main(
            ["--job", "alpaca_news_spy", "--execute"], settings=settings, clock=fixed_clock
        )
    finally:
        held_lock.release()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "execution outcome: lock_contention" in captured.out


def test_execute_overall_failure_never_reports_succeeded(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = alpaca_configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--job", "alpaca_news_spy", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "overall status: succeeded" not in captured.out


# --- module import safety -----------------------------------------------------------


def test_script_module_importable_without_side_effects(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []
