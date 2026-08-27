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
import os
import uuid
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
LOCAL_DIR = REPO_ROOT / "data" / "evaluations" / "local"

_NOW = datetime(2031, 5, 1, tzinfo=UTC)


@pytest.fixture
def local_output():
    """Yield a factory for uniquely named paths under ``data/evaluations/local/``.

    ``data/evaluations/local/`` is the only directory the CLI will write to;
    every path handed out is removed afterwards so the gitignored directory
    stays clean.
    """
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    def _make(name: str = "rec.json") -> Path:
        path = LOCAL_DIR / f"pytest-{uuid.uuid4().hex}-{name}"
        created.append(path)
        return path

    yield _make

    for path in created:
        path.unlink(missing_ok=True)


def _symlink_or_skip(src, dst, target_is_directory=False):
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")

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


def test_write_inside_the_local_dir_is_allowed(local_output, capsys):
    module = load_script_module()
    output = local_output()

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


def test_write_refuses_to_overwrite_an_existing_output(local_output, capsys):
    module = load_script_module()
    output = local_output()
    output.write_text("sentinel", encoding="utf-8")

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(output), "--write"],
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_write_does_not_create_a_missing_directory(local_output, capsys):
    module = load_script_module()
    output = LOCAL_DIR / f"missing-{uuid.uuid4().hex}" / "rec.json"

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(output), "--write"],
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert not output.parent.exists()


# ---------------------------------------------------------------------------
# Output-location allowlist (data/evaluations/local/ only)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "rec.json",  # repository root
        "docs/rec.json",
        "market_intelligence/tests/rec.json",
        "market_intelligence/evaluation/fixtures/rec.json",
        "data/evaluations/rec.json",  # parent of local/, not local/ itself
    ],
)
def test_write_refuses_output_outside_the_local_allowlist(relative, capsys):
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


def test_write_refuses_output_outside_the_repository(tmp_path, capsys):
    module = load_script_module()
    target = tmp_path / "rec.json"

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(target), "--write"],
    )

    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not target.exists()


def test_write_refuses_dotdot_traversal_escaping_the_local_dir(capsys):
    module = load_script_module()
    target = LOCAL_DIR / ".." / "rec.json"
    escaped = REPO_ROOT / "data" / "evaluations" / "rec.json"

    code, payload = _run(
        module,
        capsys,
        ["--input", str(FIXTURE_JSON), "--output", str(target), "--write"],
    )

    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not escaped.exists()


def test_write_refuses_a_symlink_escaping_the_local_dir(tmp_path, capsys):
    module = load_script_module()
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    link = LOCAL_DIR / f"pytest-escape-{uuid.uuid4().hex}"
    _symlink_or_skip(tmp_path, link, target_is_directory=True)

    try:
        code, payload = _run(
            module,
            capsys,
            [
                "--input",
                str(FIXTURE_JSON),
                "--output",
                str(link / "rec.json"),
                "--write",
            ],
        )

        assert code == 2
        assert payload == {"error": "output_path_refused"}
        assert not (tmp_path / "rec.json").exists()
    finally:
        link.unlink(missing_ok=True)


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
