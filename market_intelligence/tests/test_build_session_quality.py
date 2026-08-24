"""Tests for scripts/build_session_quality.py.

This script never makes a network request -- SessionQualityBuilder itself
imports no networking library. Every test injects an isolated,
Settings-backed SessionQualityBuilder via main()'s ``builder=`` parameter,
so nothing here ever touches the real repository database.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.market_features.session_quality import SessionQualityBuilder

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_session_quality.py"


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_session_quality", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def isolated_builder(tmp_path: Path, isolated_env_file: Path) -> SessionQualityBuilder:
    settings = Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)
    return SessionQualityBuilder(settings=settings)


def test_main_prints_json_report_to_stdout(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--symbol", "SPY"], builder=builder)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["symbol"] == "SPY"
    assert output["bar_provenance"]["timeframe"] == "5Min"


def test_main_rejects_invalid_symbol(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--symbol", "not a symbol"], builder=builder)

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "invalid_input"


def test_main_rejects_invalid_session_date(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--session-date", "not-a-date"], builder=builder)

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "invalid_input"


def test_main_rejects_unknown_argument():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module._parse_args(["--not-a-real-flag", "1"])


def test_main_accepts_explicit_session_date(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--session-date", "2026-08-19"], builder=builder)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["session_date_et"] == "2026-08-19"
    assert output["session_date_source"] == "explicit"


# --- Defensive final except: never leak a raw exception ------------------------

_UNEXPECTED_FAILURE_MARKER = (
    "simulated unexpected failure C:\\secret\\path SELECT * FROM market_bars"
)


def test_main_builder_construction_failure_prints_only_unexpected_error(
    tmp_path, isolated_env_file, capsys, monkeypatch
):
    module = load_script_module()

    def _raising_builder(*args, **kwargs):
        raise RuntimeError(_UNEXPECTED_FAILURE_MARKER)

    monkeypatch.setattr(module, "SessionQualityBuilder", _raising_builder)

    exit_code = module.main(["--symbol", "SPY"])

    assert exit_code != 0
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert _UNEXPECTED_FAILURE_MARKER not in raw_output
    assert "RuntimeError" not in raw_output


def test_main_unexpected_build_failure_prints_only_unexpected_error(capsys):
    module = load_script_module()

    class _UnexpectedFailureBuilder:
        def build_report(self, *args, **kwargs):
            raise ValueError(_UNEXPECTED_FAILURE_MARKER)

    exit_code = module.main(["--symbol", "SPY"], builder=_UnexpectedFailureBuilder())

    assert exit_code != 0
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert _UNEXPECTED_FAILURE_MARKER not in raw_output
    assert "ValueError" not in raw_output
