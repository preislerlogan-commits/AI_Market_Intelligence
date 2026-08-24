"""Tests for scripts/build_macro_evidence.py.

This script never makes a network request -- MacroEvidenceBuilder itself
imports no networking or model library. Every test injects an isolated,
Settings-backed MacroEvidenceBuilder (or a small fake) via main()'s
``builder=`` parameter, so nothing here ever touches the real repository
database, mirroring market_intelligence/tests/test_build_news_evidence.py.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.market_features.macro_evidence import (
    MacroEvidenceBuilder,
    MacroEvidenceError,
)

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build_macro_evidence.py"


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("build_macro_evidence", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def isolated_builder(tmp_path: Path, isolated_env_file: Path) -> MacroEvidenceBuilder:
    settings = Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)
    return MacroEvidenceBuilder(
        settings=settings, clock=lambda: datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    )


class _RaisingBuilder:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def build_snapshot(self, series_ids=("FEDFUNDS",)):
        raise self._exc


def test_main_prints_json_snapshot_to_stdout(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main([], builder=builder)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["request"]["series_ids"] == ["FEDFUNDS"]
    assert output["snapshot_created_at_utc"] == "2026-08-24T12:00:00Z"


def test_main_default_series_used_when_omitted(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main([], builder=builder)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["series"][0]["series_id"] == "FEDFUNDS"


def test_main_repeatable_series_argument(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--series", "FEDFUNDS", "--series", "UNRATE"], builder=builder)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["request"]["series_ids"] == ["FEDFUNDS", "UNRATE"]


def test_main_rejects_invalid_series(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--series", "not a valid id!"], builder=builder)

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "invalid_input"


def test_main_rejects_duplicate_series_after_normalization(tmp_path, isolated_env_file, capsys):
    module = load_script_module()
    builder = isolated_builder(tmp_path, isolated_env_file)

    exit_code = module.main(["--series", "FEDFUNDS", "--series", "fedfunds"], builder=builder)

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "invalid_input"


def test_main_rejects_unknown_argument():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module._parse_args(["--not-a-real-flag", "1"])


def test_main_unexpected_error_returns_sanitized_json(capsys):
    module = load_script_module()
    secret_marker = "simulated unexpected failure C:\\secret\\path"
    builder = _RaisingBuilder(RuntimeError(secret_marker))

    exit_code = module.main([], builder=builder)

    assert exit_code == 1
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert secret_marker not in raw_output


def test_main_storage_error_returns_sanitized_json(capsys):
    module = load_script_module()
    builder = _RaisingBuilder(MacroEvidenceError("Failed to open local storage for reading."))

    exit_code = module.main([], builder=builder)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "storage_error"
    assert output["detail"] == "Failed to open local storage for reading."


def test_script_source_has_no_network_or_model_imports():
    module = load_script_module()
    source = inspect.getsource(module)
    forbidden_imports = (
        "import httpx",
        "import openai",
        "import anthropic",
        "from openai",
        "from anthropic",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source
