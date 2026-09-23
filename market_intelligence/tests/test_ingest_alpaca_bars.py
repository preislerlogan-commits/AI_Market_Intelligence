"""Tests for scripts/ingest_alpaca_bars.py.

These tests must never make a live HTTP request. ``httpx.Client.send`` is
globally monkeypatched to record/raise on any call, and ``Settings`` is
always constructed pointed at a nonexistent ``.env`` file with credential
env vars cleared -- this script is never run live as part of this change.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from market_intelligence.config.settings import Settings

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest_alpaca_bars.py"

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


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ingest_alpaca_bars", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    """Monkeypatch httpx.Client.send to record calls and refuse to proceed."""
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError("Unexpected live HTTP request from ingest_alpaca_bars script test")

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


def database_file_exists(settings: Settings) -> bool:
    from market_intelligence.storage.database import default_database_path

    return default_database_path(settings).exists()


# --- invalid CLI input makes zero requests and zero writes ---------------------


def test_invalid_symbol_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--symbol", "not a valid symbol!!"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_timeframe_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--timeframe", "1Hour"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_start_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--start", "not-a-date"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_end_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--end", "2026-08-20"], settings=settings)  # date-only, naive

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_limit_value_makes_zero_requests_and_writes(
    monkeypatch, tmp_path, isolated_env_file
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--limit", "0"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_limit_too_large_makes_zero_requests_and_writes(
    monkeypatch, tmp_path, isolated_env_file
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--limit", "100000"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_max_pages_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--max-pages", "0"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_invalid_limit_type_exits_before_any_request(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    with pytest.raises(SystemExit):
        module.main(["--limit", "not-a-number"])

    assert calls == []


def test_invalid_input_error_never_echoes_raw_symbol(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    secret_marker = "SUPER-SECRET-INPUT-MARKER"

    module.main(["--symbol", f"not valid {secret_marker}"])

    captured = capsys.readouterr()
    assert secret_marker not in captured.out


def test_start_after_end_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-16T00:00:00Z", "--end", "2026-08-15T00:00:00Z"], settings=settings
    )

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_start_equal_end_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-15T00:00:00Z", "--end", "2026-08-15T00:00:00Z"], settings=settings
    )

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_start_after_end_never_echoes_raw_timestamps(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    secret_start = "2026-08-16T00:00:00Z"
    secret_end = "2026-08-15T00:00:00Z"

    module.main(["--start", secret_start, "--end", secret_end], settings=settings)

    captured = capsys.readouterr()
    assert "fetch outcome: invalid_input" in captured.out
    assert secret_start not in captured.out
    assert secret_end not in captured.out


# --- --execute, not configured: zero requests and writes ------------------------


def test_not_configured_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--execute"], settings=settings)

    assert exit_code == 1
    assert calls == []
    assert database_file_exists(settings) is False


def test_not_configured_prints_only_sanitized_status(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    module.main(["--execute"], settings=settings)

    captured = capsys.readouterr()
    assert "configured: False" in captured.out
    assert "fetch outcome: not_configured" in captured.out
    assert "symbol: SPY" in captured.out
    assert "timeframe: 5Min" in captured.out


# --- module sanity ----------------------------------------------------------------


def test_default_symbol_and_timeframe_are_conservative():
    module = load_script_module()
    assert module.DEFAULT_SYMBOL == "SPY"
    assert module.DEFAULT_TIMEFRAME == "5Min"
    assert 1 <= module.DEFAULT_LOOKBACK_DAYS <= 30
    assert 1 <= module.DEFAULT_MAX_PAGES <= 5


def test_default_window_is_bounded_and_ends_before_now():
    from datetime import UTC, datetime

    module = load_script_module()
    start, end = module._default_window()

    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))

    assert start_dt < end_dt
    assert end_dt <= datetime.now(UTC)


def test_script_module_importable_without_side_effects(monkeypatch):
    """Importing/loading the module must not perform any network I/O."""
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []
    sys.modules.pop("ingest_alpaca_bars", None)


# --- successful fetch + storage, sanitized output -------------------------------


def _bar_json(t: str, c: float = 100.5) -> dict:
    return {"t": t, "o": 100.0, "h": 101.0, "l": 99.5, "c": c, "v": 1000, "n": 50, "vw": 100.2}


def configured_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "unit-test-alpaca-secret")
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def test_successful_ingestion_prints_sanitized_output_and_stores_bars(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        assert request.url.params["feed"] == "iex"
        assert request.url.params["adjustment"] == "raw"
        assert request.url.params["currency"] == "USD"
        payload = {
            "bars": [_bar_json("2026-08-15T09:30:00Z"), _bar_json("2026-08-15T09:31:00Z")],
            "next_page_token": None,
        }
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        [
            "--execute",
            "--start",
            "2026-08-15T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
        ],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "configured: True" in captured.out
    assert "fetch outcome: success" in captured.out
    assert "symbol: SPY" in captured.out
    assert "timeframe: 5Min" in captured.out
    assert "feed: iex" in captured.out
    assert "adjustment: raw" in captured.out
    assert "currency: USD" in captured.out
    assert "received: 2" in captured.out
    assert "inserted: 2" in captured.out
    assert "existing/updated: 0" in captured.out
    assert "failed: 0" in captured.out
    assert "ingestion-run status: succeeded" in captured.out

    # Never leaks OHLCV values, individual bar timestamps, or credentials.
    assert "100.0" not in captured.out
    assert "100.5" not in captured.out
    assert "2026-08-15T09:30:00Z" not in captured.out
    assert "unit-test-alpaca-key" not in captured.out
    assert "unit-test-alpaca-secret" not in captured.out


# --- database initialization and storage failures are sanitized -----------------


def _fetch_bars_fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
    payload = {
        "bars": [_bar_json("2026-08-15T09:30:00Z")],
        "next_page_token": None,
    }
    return httpx.Response(200, json=payload, request=request)


def test_database_initialization_failure_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_bars_fake_send)

    from market_intelligence.storage.database import DuckDBManager

    secret_marker = "SECRET-DB-INTERNAL-DETAIL"

    def fake_initialize(self):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(DuckDBManager, "initialize", fake_initialize)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        [
            "--execute",
            "--start",
            "2026-08-15T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
        ],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: database_initialization_failed" in captured.out
    assert secret_marker not in captured.out
    assert "RuntimeError" not in captured.out
    assert "ingestion-run status: succeeded" not in captured.out


def test_repository_storage_error_is_sanitized(monkeypatch, tmp_path, isolated_env_file, capsys):
    monkeypatch.setattr(httpx.Client, "send", _fetch_bars_fake_send)

    from market_intelligence.storage.bar_repository import BarRepository, BarStorageError

    secret_marker = "SECRET-STORAGE-INTERNAL-DETAIL"

    def fake_store_bars(self, items, *, provider="alpaca", dataset_name="bars"):
        raise BarStorageError(f"boom {secret_marker}")

    monkeypatch.setattr(BarRepository, "store_bars", fake_store_bars)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        [
            "--execute",
            "--start",
            "2026-08-15T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
        ],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: storage_error" in captured.out
    assert secret_marker not in captured.out
    assert "ingestion-run status: succeeded" not in captured.out


def test_unexpected_repository_exception_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    """A non-BarStorageError leaking from the repository must never surface as a traceback."""
    monkeypatch.setattr(httpx.Client, "send", _fetch_bars_fake_send)

    from market_intelligence.storage.bar_repository import BarRepository

    secret_marker = "SECRET-UNEXPECTED-INTERNAL-DETAIL"

    def fake_store_bars(self, items, *, provider="alpaca", dataset_name="bars"):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(BarRepository, "store_bars", fake_store_bars)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        [
            "--execute",
            "--start",
            "2026-08-15T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
        ],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: unexpected_error" in captured.out
    assert secret_marker not in captured.out
    assert "RuntimeError" not in captured.out
    assert "Traceback" not in captured.out
    assert "ingestion-run status: succeeded" not in captured.out


def test_repository_validation_error_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_bars_fake_send)

    from market_intelligence.storage.bar_repository import (
        BarRepository,
        BarStorageValidationError,
    )

    def fake_store_bars(self, items, *, provider="alpaca", dataset_name="bars"):
        raise BarStorageValidationError("simulated validation failure")

    monkeypatch.setattr(BarRepository, "store_bars", fake_store_bars)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        [
            "--execute",
            "--start",
            "2026-08-15T00:00:00Z",
            "--end",
            "2026-08-16T00:00:00Z",
        ],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: storage_error" in captured.out
    assert "ingestion-run status: succeeded" not in captured.out


# --- dry-run default: zero HTTP, zero Settings/client/repository/database -------


PLANNED_ARGS = [
    "--start",
    "2026-08-24T00:00:00Z",
    "--end",
    "2026-09-23T00:00:00Z",
    "--limit",
    "1000",
    "--max-pages",
    "5",
]


def forbid_execute_side_effects(monkeypatch, module: ModuleType) -> list[str]:
    """Replace every I/O-capable collaborator in the script's namespace with a
    recorder that raises, so any construction in dry-run mode fails loudly."""
    constructed: list[str] = []

    def _forbidden(name: str):
        def _factory(*args, **kwargs):
            constructed.append(name)
            raise AssertionError(f"{name} must not be constructed in dry-run mode")

        return _factory

    for name in ("Settings", "AlpacaBarsClient", "BarRepository", "DuckDBManager"):
        monkeypatch.setattr(module, name, _forbidden(name))

    import duckdb

    monkeypatch.setattr(duckdb, "connect", _forbidden("duckdb.connect"))
    return constructed


def test_default_mode_makes_zero_http_and_database_activity(
    monkeypatch, tmp_path, isolated_env_file
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    constructed = forbid_execute_side_effects(monkeypatch, module)
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(PLANNED_ARGS, settings=settings)

    assert exit_code == 0
    assert calls == []
    assert constructed == []
    assert database_file_exists(settings) is False
    assert not (tmp_path / "data").exists()


def test_default_mode_with_no_arguments_is_a_dry_run(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    constructed = forbid_execute_side_effects(monkeypatch, module)

    assert module.main([]) == 0
    assert calls == []
    assert constructed == []


def test_dry_run_prints_only_the_sanitized_plan(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    forbid_execute_side_effects(monkeypatch, module)

    module.main(PLANNED_ARGS)

    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "mode: dry_run",
        "configured: not_checked",
        "symbol: SPY",
        "timeframe: 5Min",
        "start: 2026-08-24T00:00:00Z",
        "end: 2026-09-23T00:00:00Z",
        "limit: 1000",
        "max_pages: 5",
        "max_total_rows: 5000",
        "feed: iex",
        "adjustment: raw",
        "currency: USD",
        "request_planned: False",
    ]


def test_dry_run_never_prints_credentials_even_when_configured(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    monkeypatch.setenv("ALPACA_API_KEY", "unit-test-alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "unit-test-alpaca-secret")
    module = load_script_module()
    forbid_execute_side_effects(monkeypatch, module)

    assert module.main(PLANNED_ARGS) == 0

    out = capsys.readouterr().out
    assert "unit-test-alpaca-key" not in out
    assert "unit-test-alpaca-secret" not in out
    assert "duckdb" not in out.lower()
    assert "configured: not_checked" in out


@pytest.mark.parametrize(
    "bad_args",
    [
        ["--symbol", "not a valid symbol!!"],
        ["--timeframe", "1Hour"],
        ["--start", "not-a-date"],
        ["--end", "2026-08-20"],
        ["--limit", "0"],
        ["--limit", "100000"],
        ["--max-pages", "0"],
        ["--max-pages", "51"],
        ["--start", "2026-08-16T00:00:00Z", "--end", "2026-08-15T00:00:00Z"],
    ],
)
@pytest.mark.parametrize("mode_args", [[], ["--execute"]])
def test_invalid_input_fails_before_any_io_in_either_mode(
    monkeypatch, capsys, bad_args, mode_args
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    constructed = forbid_execute_side_effects(monkeypatch, module)

    exit_code = module.main([*mode_args, *bad_args])

    out = capsys.readouterr().out
    assert exit_code == 2
    assert calls == []
    assert constructed == []
    assert "fetch outcome: invalid_input" in out
    assert "mode: dry_run" not in out


# --- --execute never leaks response bodies, raw exceptions, or database paths -----


def test_execute_http_failure_never_leaks_response_body_or_paths(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    body_marker = "SECRET-RESPONSE-BODY-MARKER"

    def failing_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(500, text=f"internal error {body_marker}", request=request)

    monkeypatch.setattr(httpx.Client, "send", failing_send)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(["--execute", *PLANNED_ARGS], settings=settings)

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "fetch outcome: failed" in out
    assert "error category: AlpacaBarsError" in out
    assert body_marker not in out
    assert "unit-test-alpaca-key" not in out
    assert "unit-test-alpaca-secret" not in out
    assert str(tmp_path) not in out
    assert database_file_exists(settings) is False


def test_execute_success_output_never_contains_database_path(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_bars_fake_send)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--execute", "--start", "2026-08-15T00:00:00Z", "--end", "2026-08-16T00:00:00Z"],
        settings=settings,
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "ingestion-run status: succeeded" in out
    assert "mode: dry_run" not in out
    assert str(tmp_path) not in out
    assert "market_intelligence.duckdb" not in out
    assert database_file_exists(settings) is True
