"""Tests for scripts/characterize_macro_report.py.

Offline only: the CLI consumes a local ``MacroCharacterizationInput`` JSON file
and never opens a database, makes a network request, or calls OpenAI / an agent
runtime. These tests assert that boundary statically and exercise the
explicit-path / dry-run / no-overwrite / path-refusal / sanitization behaviour.
"""

from __future__ import annotations

import ast
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.evaluation.serialization import read_record

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "characterize_macro_report.py"
FIXTURE_JSON = (
    REPO_ROOT
    / "market_intelligence"
    / "evaluation"
    / "fixtures"
    / "macro_characterization_input_complete.json"
)

_NOW = datetime(2031, 5, 1, tzinfo=UTC)

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "duckdb",
    "httpx",
    "socket",
    "urllib",
    "http",
    "requests",
    "market_intelligence.data_connectors",
    "market_intelligence.model_clients",
    "market_intelligence.storage",
    "market_intelligence.agents",
    "market_intelligence.orchestration",
    "market_intelligence.market_features",
    "market_intelligence.config",
)


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("characterize_macro_report", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(module: ModuleType, capsys, argv: list[str]) -> tuple[int, dict]:
    code = module.main(argv, now=_NOW)
    out = capsys.readouterr().out
    return code, json.loads(out)


# ---------------------------------------------------------------------------
# Offline boundary
# ---------------------------------------------------------------------------


def test_cli_script_imports_no_forbidden_dependency():
    tree = ast.parse(SCRIPT_PATH.read_text(encoding="utf-8"), filename=str(SCRIPT_PATH))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    for name in names:
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), name


# ---------------------------------------------------------------------------
# Dry-run (default)
# ---------------------------------------------------------------------------


def test_dry_run_validates_and_writes_nothing(tmp_path, capsys):
    module = load_script_module()
    output = tmp_path / "rec.json"

    code, payload = _run(
        module, capsys, ["--input", str(FIXTURE_JSON), "--output", str(output)]
    )

    assert code == 0
    assert payload["mode"] == "dry_run"
    assert payload["output_written"] is False
    assert payload["transcription_outcomes"] == {"match": 2, "mismatch": 1, "human_review": 1}
    assert payload["pending_adjudication_count"] == 5
    assert all(p["classification"] is None for p in payload["pending_adjudications"])
    assert not output.exists()


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------


def test_write_creates_a_valid_evaluation_run_record(tmp_path, capsys):
    module = load_script_module()
    output = tmp_path / "rec.json"

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(output), "--write"],
    )

    assert code == 0
    assert payload["output_written"] is True
    record = read_record(output)
    assert record.agent == "macro_analyst"
    assert record.adjudications == []
    assert len(record.expected_pairs) == 5


def test_write_refuses_to_overwrite_an_existing_output(tmp_path, capsys):
    module = load_script_module()
    output = tmp_path / "rec.json"
    output.write_text("sentinel", encoding="utf-8")

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(output), "--write"],
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_write_does_not_create_a_missing_directory(tmp_path, capsys):
    module = load_script_module()
    output = tmp_path / "missing" / "rec.json"

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(output), "--write"],
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert not output.parent.exists()


# ---------------------------------------------------------------------------
# Tracked-directory output refusal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "docs/rec.json",
        "market_intelligence/evaluation/fixtures/rec.json",
        "market_intelligence/tests/rec.json",
    ],
)
def test_write_refuses_output_inside_a_tracked_directory(relative, capsys):
    module = load_script_module()
    target = REPO_ROOT / relative

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(target), "--write"],
    )

    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not target.exists()


# ---------------------------------------------------------------------------
# Sanitized errors
# ---------------------------------------------------------------------------


def test_malformed_input_yields_a_sanitized_error(tmp_path, capsys):
    module = load_script_module()
    bad = tmp_path / "bad.json"
    bad.write_text("this is not json", encoding="utf-8")

    code, payload = _run(
        module, capsys, ["--input", str(bad), "--output", str(tmp_path / "o.json")]
    )

    assert code == 2
    assert payload == {"error": "invalid_input"}


def test_missing_input_file_yields_a_sanitized_error(tmp_path, capsys):
    module = load_script_module()

    code, payload = _run(
        module,
        capsys,
        ["--input", str(tmp_path / "nope.json"), "--output", str(tmp_path / "o.json")],
    )

    assert code == 2
    assert payload == {"error": "invalid_input"}


def test_schema_violating_input_yields_a_sanitized_error(tmp_path, capsys):
    module = load_script_module()
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps({"characterization_label": "x"}), encoding="utf-8")

    code, payload = _run(
        module,
        capsys,
        ["--input", str(partial), "--output", str(tmp_path / "o.json")],
    )

    assert code == 2
    assert payload == {"error": "invalid_input"}


def test_both_paths_are_required():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module.main(["--input", "only-input.json"], now=_NOW)
