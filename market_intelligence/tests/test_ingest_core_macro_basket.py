"""Tests for scripts/ingest_core_macro_basket.py.

These tests must never make a live HTTP request. ``httpx.Client.send`` is
globally monkeypatched to record/raise on any call unless a test explicitly
provides a fake response, and ``Settings`` is always constructed pointed at
a nonexistent ``.env`` file with credential env vars cleared. This script is
never run live as part of this change.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import OBSERVATIONS_PATH, SERIES_PATH
from market_intelligence.orchestration.lock import RunLock, default_lock_path
from market_intelligence.storage.database import (
    DuckDBManager,
    HealthCheckResult,
    default_database_path,
)

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest_core_macro_basket.py"

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


def healthy_settings(monkeypatch, tmp_path: Path, isolated_env_file: Path) -> Settings:
    """Settings pointed at a real local database already initialized to the latest migration."""
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return settings


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ingest_core_macro_basket", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register in sys.modules before exec: dataclasses defined in this
    # module use `from __future__ import annotations` (string annotations),
    # and resolving them requires looking the module up by name in
    # sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError("Unexpected live HTTP request from ingest_core_macro_basket test")

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


def fixed_clock():
    return datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def database_file_exists(settings: Settings) -> bool:
    return default_database_path(settings).exists()


def _series_metadata_payload(*, id_: str) -> dict:
    return {
        "seriess": [
            {
                "id": id_,
                "realtime_start": "2026-08-24",
                "realtime_end": "2026-08-24",
                "title": "some official title",
                "observation_start": "1954-07-01",
                "observation_end": "2026-07-01",
                "frequency": "Monthly",
                "frequency_short": "M",
                "units": "Percent",
                "units_short": "%",
                "seasonal_adjustment": "Not Seasonally Adjusted",
                "seasonal_adjustment_short": "NSA",
                "last_updated": "2026-08-20 08:35:01-05",
                "popularity": 84,
                "notes": "some official notes",
            }
        ]
    }


def _observations_payload(dates_and_values: list[tuple[str, str]]) -> dict:
    observations = [
        {"realtime_start": "1776-07-04", "realtime_end": "9999-12-31", "date": d, "value": v}
        for d, v in dates_and_values
    ]
    return {
        "realtime_start": "1776-07-04",
        "realtime_end": "9999-12-31",
        "count": len(observations),
        "offset": 0,
        "limit": 1000,
        "observations": observations,
    }


def _write_basket_config(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "core_macro_series.json"
    path.write_text(json.dumps({"series": entries}), encoding="utf-8")
    return path


def _patch_config_path(monkeypatch, module: ModuleType, config_path: Path) -> None:
    from market_intelligence.config.macro_basket import load_core_macro_series

    monkeypatch.setattr(
        module, "load_core_macro_series", lambda: load_core_macro_series(config_path)
    )


def _disabled_fedfunds_config(tmp_path: Path) -> Path:
    return _write_basket_config(
        tmp_path,
        [
            {
                "series_id": "FEDFUNDS",
                "category": "policy_rate",
                "enabled": False,
                "observation_lookback_days": 400,
                "recent_observations_limit": 6,
            }
        ],
    )


def _success_fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
    series_id = request.url.params.get("series_id")
    if request.url.path == SERIES_PATH:
        return httpx.Response(200, json=_series_metadata_payload(id_=series_id), request=request)
    if request.url.path == OBSERVATIONS_PATH:
        payload = _observations_payload([("2026-06-01", "5.00"), ("2026-07-01", "5.33")])
        return httpx.Response(200, json=payload, request=request)
    raise AssertionError(f"unexpected request path: {request.url.path}")


# --- selection validation happens before Settings/network/DB -----------------------


def test_neither_series_nor_all_rejected_by_argparse(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    with pytest.raises(SystemExit):
        module.main([])

    assert calls == []


def test_both_series_and_all_rejected_by_argparse(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    with pytest.raises(SystemExit):
        module.main(["--series", "FEDFUNDS", "--all"])

    assert calls == []


def test_invalid_series_id_rejected_before_any_activity(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--series", "not a valid series!!"], settings=settings)

    assert exit_code == 2
    assert calls == []
    assert database_file_exists(settings) is False
    assert not default_lock_path(settings).exists()


def test_duplicate_series_flag_rejected(monkeypatch, tmp_path, isolated_env_file, capsys):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--series", "FEDFUNDS", "--series", "FEDFUNDS"], settings=settings
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == []
    assert "selection outcome: duplicate_series_id" in captured.out


def test_unknown_series_id_rejected(monkeypatch, tmp_path, isolated_env_file, capsys):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main(["--series", "CPILFESL"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == []
    assert "selection outcome: unknown_series_id" in captured.out


def test_disabled_series_rejected_using_temporary_config(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    _patch_config_path(monkeypatch, module, _disabled_fedfunds_config(tmp_path))

    exit_code = module.main(["--series", "FEDFUNDS"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert calls == []
    assert "selection outcome: disabled_series_id" in captured.out


def test_all_with_zero_enabled_series_rejected_before_any_activity(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    _patch_config_path(monkeypatch, module, _disabled_fedfunds_config(tmp_path))

    exit_code = module.main(["--all", "--execute"], settings=settings, clock=fixed_clock)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "selection outcome: no_enabled_series" in captured.out
    assert calls == []
    assert database_file_exists(settings) is False
    assert not default_lock_path(settings).exists()


# --- dry run is the default and is safe ---------------------------------------------


def test_dry_run_is_default_and_makes_zero_settings_network_or_db_activity(
    monkeypatch, tmp_path, isolated_env_file
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    def _forbidden_settings(*args, **kwargs):
        raise AssertionError("Settings must never be constructed during a dry run")

    monkeypatch.setattr(module, "Settings", _forbidden_settings)

    exit_code = module.main(["--all"], settings=settings, clock=fixed_clock)

    assert exit_code == 0
    assert calls == []
    assert database_file_exists(settings) is False
    assert not default_lock_path(settings).exists()


def test_dry_run_prints_plan_for_all_seven_series(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--all"], clock=fixed_clock)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "mode: dry_run" in captured.out
    assert "selected series count: 7" in captured.out
    for series_id in ("FEDFUNDS", "GS10", "CPIAUCSL", "PCEPI", "UNRATE", "INDPRO", "GDPC1"):
        assert f"series_id: {series_id}" in captured.out


def test_dry_run_single_series_selects_only_that_series(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--series", "FEDFUNDS"], clock=fixed_clock)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "selected series count: 1" in captured.out
    assert "series_id: FEDFUNDS" in captured.out
    assert "series_id: UNRATE" not in captured.out


def test_dry_run_output_never_leaks_credentials_or_internals(monkeypatch, capsys):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    module.main(["--all"], clock=fixed_clock)

    captured = capsys.readouterr()
    for forbidden in ("api_key", "Traceback", "SELECT", "duckdb", "Percent", "title"):
        assert forbidden not in captured.out


def test_dry_run_computes_correct_bounded_windows_for_each_configured_lookback(
    monkeypatch, capsys
):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--all"], clock=fixed_clock)

    captured = capsys.readouterr()
    assert exit_code == 0
    # as_of = 2026-08-24 (fixed_clock). end always equals the as_of date.
    assert "planned_observation_window_end: 2026-08-24" in captured.out
    # FEDFUNDS/GS10 (monthly, 400-day lookback): start = 2026-08-24 - 400 days.
    assert "planned_observation_window_start: 2025-07-20" in captured.out
    # GDPC1 (quarterly, 1100-day lookback): start = 2026-08-24 - 1100 days.
    assert "planned_observation_window_start: 2023-08-20" in captured.out


def test_shared_clock_is_called_exactly_once_for_the_whole_run(monkeypatch):
    blocked_http_send(monkeypatch)
    module = load_script_module()

    call_count = 0

    def counting_clock():
        nonlocal call_count
        call_count += 1
        return fixed_clock()

    exit_code = module.main(["--all"], clock=counting_clock)

    assert exit_code == 0
    assert call_count == 1


# --- --execute: database health gate before any network request ----------------------


def test_execute_unhealthy_database_fails_before_any_network_request(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = configured_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--series", "FEDFUNDS", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []
    assert "execution outcome: database_not_healthy" in captured.out


def test_execute_wrong_schema_version_fails_before_any_network_request(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = healthy_settings(monkeypatch, tmp_path, isolated_env_file)

    def fake_check_health(self):
        return HealthCheckResult(
            database_path=default_database_path(settings),
            database_exists=True,
            schema_version="0007",
            applied_migration_count=7,
            required_tables_present=True,
            required_columns_present=True,
            migration_history_valid=True,
            checksums_valid=True,
            is_current=True,
            healthy=True,
        )

    monkeypatch.setattr(DuckDBManager, "check_health", fake_check_health)

    exit_code = module.main(
        ["--series", "FEDFUNDS", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []
    assert "execution outcome: database_not_healthy" in captured.out


def test_execute_not_configured_reported_after_healthy_db_before_network(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()

    exit_code = module.main(
        ["--series", "FEDFUNDS", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []
    assert "execution outcome: not_configured" in captured.out


# --- --execute: success path ----------------------------------------------------------


def test_execute_success_single_series_stores_metadata_and_observations(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    monkeypatch.setattr(httpx.Client, "send", _success_fake_send)
    module = load_script_module()
    settings = healthy_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--series", "FEDFUNDS", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "overall status: succeeded" in captured.out
    assert "series_id: FEDFUNDS" in captured.out
    assert "metadata_status: succeeded" in captured.out
    assert "observation_status: succeeded" in captured.out
    assert "observation_received: 2" in captured.out
    assert "observation_inserted: 2" in captured.out

    # Sanitization: never a title, unit, note, value, or credential.
    for forbidden in (
        "some official title",
        "some official notes",
        "Percent",
        "5.00",
        "5.33",
        "unit-test-fred-key",
        "Traceback",
        "SELECT",
    ):
        assert forbidden not in captured.out


def test_execute_empty_observations_reported_as_skipped_not_failed(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        series_id = request.url.params.get("series_id")
        if request.url.path == SERIES_PATH:
            return httpx.Response(
                200, json=_series_metadata_payload(id_=series_id), request=request
            )
        return httpx.Response(200, json=_observations_payload([]), request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = healthy_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--series", "FEDFUNDS", "--execute"], settings=settings, clock=fixed_clock
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "overall status: succeeded" in captured.out
    assert "observation_status: skipped_empty" in captured.out
    assert "observation_received: 0" in captured.out


# --- --execute: sequential order, per-series failure isolation, no retry --------------


def test_execute_order_and_failure_isolation_across_two_series(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    metadata_calls: list[str] = []

    def fake_send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        series_id = request.url.params.get("series_id")
        if request.url.path == SERIES_PATH:
            metadata_calls.append(series_id)
            if series_id == "UNRATE":
                return httpx.Response(500, json={"error_message": "boom"}, request=request)
            return httpx.Response(
                200, json=_series_metadata_payload(id_=series_id), request=request
            )
        payload = _observations_payload([("2026-07-01", "5.33")])
        return httpx.Response(200, json=payload, request=request)

    monkeypatch.setattr(httpx.Client, "send", fake_send)
    module = load_script_module()
    settings = healthy_settings(monkeypatch, tmp_path, isolated_env_file)

    exit_code = module.main(
        ["--series", "UNRATE", "--series", "FEDFUNDS", "--execute"],
        settings=settings,
        clock=fixed_clock,
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "overall status: failed" in captured.out
    # Both series were attempted despite UNRATE's metadata failure -- one
    # series' failure never prevents another selected series from running.
    assert set(metadata_calls) == {"FEDFUNDS", "UNRATE"}
    # No automatic retry: each series' metadata endpoint is called exactly once.
    assert metadata_calls.count("UNRATE") == 1
    assert metadata_calls.count("FEDFUNDS") == 1

    fedfunds_index = captured.out.index("series_id: FEDFUNDS")
    unrate_index = captured.out.index("series_id: UNRATE")
    fedfunds_block = captured.out[fedfunds_index : fedfunds_index + 400]
    unrate_block = captured.out[unrate_index : unrate_index + 400]
    assert "status: succeeded" in fedfunds_block
    assert "status: failed" in unrate_block
    assert "metadata_error_category: provider_error" in unrate_block


def test_execute_lock_contention_reported_sanitized(
    monkeypatch, tmp_path, isolated_env_file, capsys
):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = healthy_settings(monkeypatch, tmp_path, isolated_env_file)

    held_lock = RunLock(default_lock_path(settings))
    held_lock.acquire()
    try:
        exit_code = module.main(
            ["--series", "FEDFUNDS", "--execute"], settings=settings, clock=fixed_clock
        )
    finally:
        held_lock.release()

    captured = capsys.readouterr()
    assert exit_code == 1
    assert calls == []
    assert "execution outcome: lock_contention" in captured.out


# --- module import safety --------------------------------------------------------------


def test_script_module_importable_without_side_effects(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []
