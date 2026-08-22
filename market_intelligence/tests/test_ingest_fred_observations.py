"""Tests for scripts/ingest_fred_observations.py.

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

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest_fred_observations.py"

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


def configured_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    monkeypatch.setenv("FRED_API_KEY", "unit-test-fred-key")
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ingest_fred_observations", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    """Monkeypatch httpx.Client.send to record calls and refuse to proceed."""
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError(
            "Unexpected live HTTP request from ingest_fred_observations script test"
        )

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


def database_file_exists(settings: Settings) -> bool:
    from market_intelligence.storage.database import default_database_path

    return default_database_path(settings).exists()


def _observations_payload(dates_and_values: list[tuple[str, str]]) -> dict:
    observations = [
        {
            "realtime_start": "2026-08-20",
            "realtime_end": "2026-08-20",
            "date": date_,
            "value": value,
        }
        for date_, value in dates_and_values
    ]
    return {
        "realtime_start": "2026-08-20",
        "realtime_end": "2026-08-20",
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


def _fetch_observations_fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
    payload = _observations_payload([("2026-08-01", "5.33"), ("2026-08-02", "5.34")])
    return httpx.Response(200, json=payload, request=request)


# --- invalid CLI input makes zero requests and zero writes ---------------------


def test_invalid_series_id_makes_zero_requests_and_writes(
    monkeypatch, tmp_path, isolated_env_file
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--series-id", "not a valid series!!"], settings=settings)

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

    exit_code = module.main(["--end", "2026-08-20T00:00:00Z"], settings=settings)  # datetime

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


def test_invalid_input_error_never_echoes_raw_series_id(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    secret_marker = "SUPER-SECRET-INPUT-MARKER"

    module.main(["--series-id", f"not valid {secret_marker}"])

    captured = capsys.readouterr()
    assert secret_marker not in captured.out


def test_start_after_end_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-16", "--end", "2026-08-15"], settings=settings
    )

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False


def test_start_equal_end_makes_zero_requests_but_is_not_rejected(
    monkeypatch, tmp_path, isolated_env_file
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_observations_fake_send)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(["--start", "2026-08-15", "--end", "2026-08-15"], settings=settings)

    assert exit_code == 0


def test_start_after_end_never_echoes_raw_dates(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    secret_start = "2026-08-16"
    secret_end = "2026-08-15"

    module.main(["--start", secret_start, "--end", secret_end], settings=settings)

    captured = capsys.readouterr()
    assert "fetch outcome: invalid_input" in captured.out


# --- not configured makes zero requests and writes ------------------------------


def test_not_configured_makes_zero_requests_and_writes(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    assert exit_code == 1
    assert calls == []
    assert database_file_exists(settings) is False


def test_not_configured_prints_only_sanitized_status(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    module.main([], settings=settings)

    captured = capsys.readouterr()
    assert "configured: False" in captured.out
    assert "fetch outcome: not_configured" in captured.out
    assert "series id: FEDFUNDS" in captured.out


# --- module sanity ----------------------------------------------------------------


def test_default_series_id_is_conservative():
    module = load_script_module()
    assert module.DEFAULT_SERIES_ID == "FEDFUNDS"
    assert 1 <= module.DEFAULT_LOOKBACK_DAYS <= 366
    assert 1 <= module.DEFAULT_MAX_PAGES <= 5


def test_default_window_is_bounded_and_ends_before_today():
    from datetime import UTC, datetime

    module = load_script_module()
    start, end = module._default_window()

    start_date = datetime.fromisoformat(start).date()
    end_date = datetime.fromisoformat(end).date()

    assert start_date < end_date
    assert end_date < datetime.now(UTC).date()


def test_script_module_importable_without_side_effects(monkeypatch):
    """Importing/loading the module must not perform any network I/O."""
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []
    sys.modules.pop("ingest_fred_observations", None)


# --- successful fetch + storage, sanitized output -------------------------------


def test_successful_ingestion_prints_sanitized_output_and_stores_observations(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        assert request.url.params["sort_order"] == "asc"
        payload = _observations_payload([("2026-08-01", "5.33"), ("2026-08-02", "5.34")])
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "configured: True" in captured.out
    assert "fetch outcome: success" in captured.out
    assert "series id: FEDFUNDS" in captured.out
    assert "requested observation start: 2026-08-01" in captured.out
    assert "requested observation end: 2026-08-02" in captured.out
    assert "received: 2" in captured.out
    assert "inserted: 2" in captured.out
    assert "existing/updated: 0" in captured.out
    assert "failed: 0" in captured.out
    assert "ingestion-run status: succeeded" in captured.out

    # Never leaks observation values, credentials, or raw request details.
    assert "5.33" not in captured.out
    assert "5.34" not in captured.out
    assert "unit-test-fred-key" not in captured.out


def test_empty_provider_result_is_reported_as_success_not_failure(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(200, json=_observations_payload([]), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "fetch outcome: success" in captured.out
    assert "received: 0" in captured.out
    assert "ingestion-run status: skipped_empty" in captured.out
    assert database_file_exists(settings) is False  # nothing to store, no DB touched


# --- database initialization and storage failures are sanitized -----------------


def test_database_initialization_failure_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_observations_fake_send)

    from market_intelligence.storage.database import DuckDBManager

    secret_marker = "SECRET-DB-INTERNAL-DETAIL"

    def fake_initialize(self):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(DuckDBManager, "initialize", fake_initialize)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: database_initialization_failed" in captured.out
    assert secret_marker not in captured.out
    assert "RuntimeError" not in captured.out
    assert "ingestion-run status: succeeded" not in captured.out


def test_repository_storage_error_is_sanitized(monkeypatch, tmp_path, isolated_env_file, capsys):
    monkeypatch.setattr(httpx.Client, "send", _fetch_observations_fake_send)

    from market_intelligence.storage.macro_observation_repository import (
        MacroObservationRepository,
        MacroObservationStorageError,
    )

    secret_marker = "SECRET-STORAGE-INTERNAL-DETAIL"

    def fake_store_observations(self, items, *, provider="fred", dataset_name="macro_observations"):
        raise MacroObservationStorageError(f"boom {secret_marker}")

    monkeypatch.setattr(
        MacroObservationRepository, "store_observations", fake_store_observations
    )

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
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
    """A non-MacroObservationStorageError leak must never surface as a traceback."""
    monkeypatch.setattr(httpx.Client, "send", _fetch_observations_fake_send)

    from market_intelligence.storage.macro_observation_repository import (
        MacroObservationRepository,
    )

    secret_marker = "SECRET-UNEXPECTED-INTERNAL-DETAIL"

    def fake_store_observations(self, items, *, provider="fred", dataset_name="macro_observations"):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(
        MacroObservationRepository, "store_observations", fake_store_observations
    )

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
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
    monkeypatch.setattr(httpx.Client, "send", _fetch_observations_fake_send)

    from market_intelligence.storage.macro_observation_repository import (
        MacroObservationRepository,
        MacroObservationStorageValidationError,
    )

    def fake_store_observations(self, items, *, provider="fred", dataset_name="macro_observations"):
        raise MacroObservationStorageValidationError("simulated validation failure")

    monkeypatch.setattr(
        MacroObservationRepository, "store_observations", fake_store_observations
    )

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: storage_error" in captured.out
    assert "ingestion-run status: succeeded" not in captured.out


def test_fetch_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file, capsys):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--start", "2026-08-01", "--end", "2026-08-02"], settings=settings
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "fetch outcome: failed" in captured.out
    assert "error category: FredMacroDataError" in captured.out
    assert database_file_exists(settings) is False
