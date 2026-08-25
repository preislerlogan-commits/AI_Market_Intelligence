"""Tests for scripts/ingest_fred_series_metadata.py.

These tests must never make a live HTTP request. ``httpx.Client.send`` is
globally monkeypatched to record/raise on any call, and ``Settings`` is
always constructed pointed at a nonexistent ``.env`` file with credential
env vars cleared -- this script is never run live as part of this change,
mirroring market_intelligence/tests/test_ingest_fred_observations.py.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from market_intelligence.config.settings import Settings

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest_fred_series_metadata.py"

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
    spec = importlib.util.spec_from_file_location("ingest_fred_series_metadata", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError(
            "Unexpected live HTTP request from ingest_fred_series_metadata script test"
        )

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


def database_file_exists(settings: Settings) -> bool:
    from market_intelligence.storage.database import default_database_path

    return default_database_path(settings).exists()


def _series_metadata_payload(
    *,
    id_: str = "FEDFUNDS",
    title: str = "Federal Funds Effective Rate",
    notes: str | None = "Averages of daily figures.",
) -> dict:
    return {
        "realtime_start": "2026-08-20",
        "realtime_end": "2026-08-20",
        "seriess": [
            {
                "id": id_,
                "realtime_start": "2026-08-20",
                "realtime_end": "2026-08-20",
                "title": title,
                "observation_start": "1954-07-01",
                "observation_end": "2026-08-01",
                "frequency": "Monthly",
                "frequency_short": "M",
                "units": "Percent",
                "units_short": "%",
                "seasonal_adjustment": "Not Seasonally Adjusted",
                "seasonal_adjustment_short": "NSA",
                "last_updated": "2026-08-20 08:35:01-05",
                "popularity": 84,
                "group_popularity": 84,
                "notes": notes,
            }
        ],
    }


def _fetch_metadata_fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
    return httpx.Response(200, json=_series_metadata_payload(), request=request)


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


def test_invalid_input_prints_only_sanitized_error_category(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--series-id", "not a valid series!!"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error category: invalid_input" in captured.out
    assert "FredInvalidSeriesIdError" not in captured.out
    assert "Traceback" not in captured.out


@pytest.mark.parametrize(
    "malicious_series_id",
    [
        "'; DROP TABLE macro_series_metadata;--",
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "FEDFUNDS\nFEDFUNDS2",
    ],
)
def test_malicious_series_id_never_echoed_in_output(monkeypatch, capsys, malicious_series_id):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--series-id", malicious_series_id])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert malicious_series_id not in captured.out
    assert "error category: invalid_input" in captured.out


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


def test_script_module_importable_without_side_effects(monkeypatch):
    """Importing/loading the module must not perform any network I/O."""
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []


# --- successful fetch + storage, sanitized output -------------------------------


def test_successful_ingestion_prints_sanitized_output_and_stores_metadata(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_metadata_fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "configured: True" in captured.out
    assert "fetch outcome: success" in captured.out
    assert "series id: FEDFUNDS" in captured.out
    assert "stored: inserted" in captured.out
    assert "ingestion-run status: succeeded" in captured.out

    # Never leaks title, units, notes, or credentials.
    assert "Federal Funds Effective Rate" not in captured.out
    assert "Percent" not in captured.out
    assert "Averages of daily figures." not in captured.out
    assert "unit-test-fred-key" not in captured.out


def test_repeated_ingestion_reports_updated(monkeypatch, tmp_path, isolated_env_file, capsys):
    monkeypatch.setattr(httpx.Client, "send", _fetch_metadata_fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    module.main([], settings=settings)
    capsys.readouterr()
    exit_code = module.main([], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "stored: updated" in captured.out


# --- database initialization and storage failures are sanitized -----------------


def test_database_initialization_failure_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_metadata_fake_send)

    from market_intelligence.storage.database import DuckDBManager

    secret_marker = "SECRET-DB-INTERNAL-DETAIL"

    def fake_initialize(self):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(DuckDBManager, "initialize", fake_initialize)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: database_initialization_failed" in captured.out
    assert secret_marker not in captured.out
    assert "RuntimeError" not in captured.out


def test_repository_storage_error_is_sanitized(monkeypatch, tmp_path, isolated_env_file, capsys):
    monkeypatch.setattr(httpx.Client, "send", _fetch_metadata_fake_send)

    from market_intelligence.storage.macro_series_metadata_repository import (
        MacroSeriesMetadataRepository,
        MacroSeriesMetadataStorageError,
    )

    secret_marker = "SECRET-STORAGE-INTERNAL-DETAIL"

    def fake_store_metadata(self, item, *, provider="fred", dataset_name="macro_series_metadata"):
        raise MacroSeriesMetadataStorageError(f"boom {secret_marker}")

    monkeypatch.setattr(
        MacroSeriesMetadataRepository, "store_metadata", fake_store_metadata
    )

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: storage_error" in captured.out
    assert secret_marker not in captured.out


def test_unexpected_repository_exception_is_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _fetch_metadata_fake_send)

    from market_intelligence.storage.macro_series_metadata_repository import (
        MacroSeriesMetadataRepository,
    )

    secret_marker = "SECRET-UNEXPECTED-INTERNAL-DETAIL"

    def fake_store_metadata(self, item, *, provider="fred", dataset_name="macro_series_metadata"):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(
        MacroSeriesMetadataRepository, "store_metadata", fake_store_metadata
    )

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "storage outcome: failed" in captured.out
    assert "error category: unexpected_error" in captured.out
    assert secret_marker not in captured.out
    assert "RuntimeError" not in captured.out
    assert "Traceback" not in captured.out


def test_fetch_failure_is_sanitized(monkeypatch, tmp_path, isolated_env_file, capsys):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        return httpx.Response(500, json={"message": "internal error"}, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)

    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "fetch outcome: failed" in captured.out
    assert "error category: FredSeriesMetadataError" in captured.out
    assert database_file_exists(settings) is False
