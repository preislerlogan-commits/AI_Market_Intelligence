"""Tests for scripts/ingest_alpaca_options_chain.py.

These tests must never make a live HTTP request. ``httpx.Client.send`` is
monkeypatched to record/raise on any call unless a test explicitly installs
a mock transport, and ``Settings`` is always constructed pointed at a
nonexistent ``.env`` file with credential env vars cleared -- this script is
never run live as part of this change.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from market_intelligence.config.settings import Settings

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest_alpaca_options_chain.py"

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]

VALID_ARGS = [
    "--feed", "opra",
    "--expiration-gte", "2026-09-01",
    "--expiration-lte", "2026-09-30",
    "--strike-gte", "400",
    "--strike-lte", "600",
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


def configured_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "unit-test-alpaca-secret")
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ingest_alpaca_options_chain", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError("Unexpected live HTTP request from ingest_alpaca_options_chain test")

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


def database_file_exists(settings: Settings) -> bool:
    from market_intelligence.storage.database import default_database_path

    return default_database_path(settings).exists()


# --- dry run: zero HTTP, zero DB writes -----------------------------------


def test_dry_run_zero_http_and_zero_db_writes(monkeypatch, tmp_path, isolated_env_file, capsys):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS, settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert calls == []
    assert database_file_exists(settings) is False
    assert "mode: dry_run" in captured.out
    assert "underlying: SPY" in captured.out
    assert "feed: opra" in captured.out
    assert "expiration_date_gte: 2026-09-01" in captured.out
    assert "strike_price_gte: 400" in captured.out
    assert "request planned: false (dry run)" in captured.out


def test_dry_run_reports_configured_status(monkeypatch, tmp_path, isolated_env_file, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    module.main(VALID_ARGS, settings=settings)

    captured = capsys.readouterr()
    assert "configured: True" in captured.out


def test_dry_run_with_type_filter(monkeypatch, tmp_path, isolated_env_file, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    module.main(VALID_ARGS + ["--type", "put"], settings=settings)

    captured = capsys.readouterr()
    assert "option_type: put" in captured.out


# --- invalid args: exit 2, zero HTTP, zero writes ------------------------


@pytest.mark.parametrize(
    "extra",
    [
        ["--expiration-gte", "2026-09-30", "--expiration-lte", "2026-09-01"],  # reversed
        ["--strike-gte", "600", "--strike-lte", "400"],  # reversed
        ["--strike-gte", "0"],  # non-positive
        ["--expiration-gte", "not-a-date"],
        ["--limit", "0"],
        ["--limit", "5000"],
        ["--max-pages", "0"],
    ],
)
def test_invalid_args_exit_2_zero_http_zero_writes(
    monkeypatch, tmp_path, isolated_env_file, extra
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS + extra, settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_feed_rejected_by_argparse(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    with pytest.raises(SystemExit):
        module.main(
            ["--feed", "sip", "--expiration-gte", "2026-09-01", "--expiration-lte", "2026-09-30",
             "--strike-gte", "400", "--strike-lte", "600"]
        )
    assert calls == []


def test_invalid_input_error_hides_raw_value(monkeypatch, tmp_path, isolated_env_file, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    secret = "SECRET-9999-88-77"

    module.main(
        ["--feed", "opra", "--expiration-gte", secret, "--expiration-lte", "2026-09-30",
         "--strike-gte", "400", "--strike-lte", "600"],
        settings=settings,
    )
    captured = capsys.readouterr()
    assert "outcome: invalid_input" in captured.out
    assert secret not in captured.out


# --- execute: not configured -----------------------------------------


def test_execute_not_configured_makes_zero_requests_and_writes(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS + ["--execute"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []
    assert database_file_exists(settings) is False
    assert "fetch outcome: not_configured" in captured.out


# --- execute: success writes transactionally --------------------------


def _snapshot_payload() -> dict:
    return {
        "snapshots": {
            "SPY260918C00500000": {
                "latestQuote": {"t": "2026-09-02T15:30:00Z", "bp": 5.25, "bs": 10,
                                "ap": 5.35, "as": 12},
                "greeks": {"delta": 0.42, "gamma": 0.03, "theta": -0.06, "vega": 0.11,
                           "rho": -0.02},
                "impliedVolatility": 0.1543,
            }
        },
        "next_page_token": None,
    }


def test_execute_success_writes_and_prints_sanitized_output(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        assert request.url.path == "/v1beta1/options/snapshots/SPY"
        assert request.url.params["feed"] == "opra"
        return httpx.Response(200, json=_snapshot_payload(), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS + ["--execute"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "mode: execute" in captured.out
    assert "fetch outcome: success" in captured.out
    assert "received: 1" in captured.out
    assert "inserted: 1" in captured.out
    assert "batch outcome: succeeded" in captured.out
    assert "ingestion-run status: succeeded" in captured.out
    # never leaks contract data / quotes / greeks / credentials
    assert "SPY260918C00500000" not in captured.out
    assert "5.25" not in captured.out
    assert "0.1543" not in captured.out
    assert "0.42" not in captured.out
    assert "unit-test-alpaca-key" not in captured.out

    # the snapshot row and its run-level batch row are actually persisted
    import duckdb

    from market_intelligence.storage.database import default_database_path

    connection = duckdb.connect(str(default_database_path(settings)), read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM option_chain_snapshots"
        ).fetchone()[0] == 1
        batch = connection.execute(
            "SELECT contract_count, outcome FROM option_chain_snapshot_batches"
        ).fetchone()
    finally:
        connection.close()
    assert batch == (1, "succeeded")


def test_execute_empty_result_is_skipped_empty(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(200, json={"snapshots": {}, "next_page_token": None}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS + ["--execute"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "received: 0" in captured.out
    assert "storage outcome: complete" in captured.out
    assert "batch outcome: skipped_empty" in captured.out
    assert "ingestion-run status: succeeded" in captured.out

    # a run-level batch row is persisted even though zero snapshot rows were written
    import duckdb

    from market_intelligence.storage.database import default_database_path

    connection = duckdb.connect(str(default_database_path(settings)), read_only=True)
    try:
        assert connection.execute(
            "SELECT count(*) FROM option_chain_snapshots"
        ).fetchone()[0] == 0
        batch = connection.execute(
            "SELECT contract_count, outcome, requested_feed FROM option_chain_snapshot_batches"
        ).fetchone()
    finally:
        connection.close()
    assert batch == (0, "skipped_empty", "opra")


def test_execute_exactly_one_request_per_page_and_no_retry_on_failure(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = []

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500, json={"message": "boom"}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS + ["--execute"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert len(calls) == 1
    assert "fetch outcome: failed" in captured.out
    assert "boom" not in captured.out


def test_execute_storage_error_is_sanitized(monkeypatch, tmp_path, isolated_env_file, capsys):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(200, json=_snapshot_payload(), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    from market_intelligence.storage.option_chain_snapshot_repository import (
        OptionChainSnapshotRepository,
        OptionChainSnapshotStorageError,
    )

    def fake_store(self, items, *, request, retrieved_at=None, provider="alpaca",
                   dataset_name="option_chain_snapshots"):
        raise OptionChainSnapshotStorageError("boom SECRET-STORAGE-DETAIL")

    monkeypatch.setattr(OptionChainSnapshotRepository, "store_snapshots", fake_store)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(VALID_ARGS + ["--execute"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "SECRET-STORAGE-DETAIL" not in captured.out


def test_script_module_importable_without_side_effects(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []
