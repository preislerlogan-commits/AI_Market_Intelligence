"""Tests for scripts/ingest_alpaca_news.py.

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

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ingest_alpaca_news.py"

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
    spec = importlib.util.spec_from_file_location("ingest_alpaca_news", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def blocked_http_send(monkeypatch) -> list[httpx.Request]:
    """Monkeypatch httpx.Client.send to record calls and refuse to proceed."""
    calls: list[httpx.Request] = []

    def _send(self, request: httpx.Request, **kwargs):
        calls.append(request)
        raise AssertionError("Unexpected live HTTP request from ingest_alpaca_news script test")

    monkeypatch.setattr(httpx.Client, "send", _send)
    return calls


# --- invalid CLI input makes zero requests ------------------------------------


def test_invalid_symbol_makes_zero_requests(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--symbol", "not a valid symbol!!"])

    assert exit_code == 2
    assert calls == []


def test_invalid_limit_value_makes_zero_requests(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--limit", "0"])

    assert exit_code == 2
    assert calls == []


def test_invalid_limit_too_large_makes_zero_requests(monkeypatch):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()

    exit_code = module.main(["--limit", "1000"])

    assert exit_code == 2
    assert calls == []


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


# --- not configured makes zero requests ----------------------------------------


def test_not_configured_makes_zero_requests(monkeypatch, tmp_path, isolated_env_file):
    calls = blocked_http_send(monkeypatch)
    module = load_script_module()
    settings = unconfigured_settings(tmp_path, isolated_env_file)

    exit_code = module.main([], settings=settings)

    assert exit_code == 1
    assert calls == []


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
    assert "symbol: SPY" in captured.out


# --- module sanity ----------------------------------------------------------------


def test_default_symbol_and_limit_are_conservative():
    module = load_script_module()
    assert module.DEFAULT_SYMBOL == "SPY"
    assert 1 <= module.DEFAULT_LIMIT <= 20


def test_script_module_importable_without_side_effects(monkeypatch):
    """Importing/loading the module must not perform any network I/O."""
    calls = blocked_http_send(monkeypatch)
    load_script_module()
    assert calls == []
    sys.modules.pop("ingest_alpaca_news", None)
