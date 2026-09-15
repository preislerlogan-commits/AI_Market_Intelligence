"""Tests for scripts/evaluate_spy_vwap_reversion.py.

Offline only: the CLI consumes a local ``SpyVwapReversionEvaluationInput``
JSON file and never opens a database, makes a network request, or calls
OpenAI / an agent runtime -- it never reads the real DuckDB database. These
tests assert that boundary statically and exercise the explicit-path /
dry-run / no-overwrite / path-refusal / sanitization behaviour. Mirrors
test_characterize_macro_report_cli.py.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    SessionBars,
    SpyVwapReversionEvaluationInput,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    input_to_json_str,
    read_record,
)
from market_intelligence.market_features.spy_regime_contracts import IntradayBar

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "evaluate_spy_vwap_reversion.py"
LOCAL_DIR = REPO_ROOT / "data" / "evaluations" / "local"

EASTERN = ZoneInfo("America/New_York")
DAY = date(2026, 6, 10)
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
    "market_intelligence.config",
)


def _bar(hour, minute, price="500", volume=1000):
    p = Decimal(price)
    return IntradayBar(
        timestamp=datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=EASTERN),
        open=p, high=p, low=p, close=p, volume=volume,
    )


def _small_valid_input() -> SpyVwapReversionEvaluationInput:
    session = SessionBars(
        session_date=DAY,
        bars=[_bar(9, 30), _bar(9, 35, "506", volume=0), _bar(9, 40, "504")],
    )
    return SpyVwapReversionEvaluationInput(sessions=[session])


@pytest.fixture
def input_file(tmp_path):
    path = tmp_path / "input.json"
    path.write_text(input_to_json_str(_small_valid_input()), encoding="utf-8")
    return path


@pytest.fixture
def local_output():
    """Yield a factory for uniquely named paths under
    ``data/evaluations/local/``, cleaned up afterwards."""
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


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_spy_vwap_reversion", SCRIPT_PATH)
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


def test_dry_run_validates_and_writes_nothing(input_file, capsys, tmp_path):
    module = load_script_module()
    output = tmp_path / "rec.json"

    code, payload = _run(module, capsys, ["--input", str(input_file), "--output", str(output)])

    assert code == 0
    assert payload["mode"] == "dry_run"
    assert payload["output_written"] is False
    assert payload["symbol"] == "SPY"
    assert payload["unique_session_count"] == 1
    assert payload["candidate_decision_point_count"] == 3
    assert not output.exists()


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------


def test_write_inside_the_local_dir_is_allowed(input_file, local_output, capsys):
    module = load_script_module()
    output = local_output()

    code, payload = _run(
        module, capsys, ["--input", str(input_file), "--output", str(output), "--write"]
    )

    assert code == 0
    assert payload["output_written"] is True
    record = read_record(output)
    assert record.symbol == "SPY"
    assert record.candidate_decision_point_count == 3


def test_write_refuses_to_overwrite_an_existing_output(input_file, local_output, capsys):
    module = load_script_module()
    output = local_output()
    output.write_text("sentinel", encoding="utf-8")

    code, payload = _run(
        module, capsys, ["--input", str(input_file), "--output", str(output), "--write"]
    )

    assert code == 1
    assert payload == {"error": "write_failed"}
    assert output.read_text(encoding="utf-8") == "sentinel"


def test_write_does_not_create_a_missing_directory(input_file, capsys):
    module = load_script_module()
    output = LOCAL_DIR / f"missing-{uuid.uuid4().hex}" / "rec.json"

    code, payload = _run(
        module, capsys, ["--input", str(input_file), "--output", str(output), "--write"]
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
def test_write_refuses_output_outside_the_local_allowlist(relative, input_file, capsys):
    module = load_script_module()
    target = REPO_ROOT / relative

    code, payload = _run(
        module, capsys, ["--input", str(input_file), "--output", str(target), "--write"]
    )

    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not target.exists()


def test_write_refuses_output_outside_the_repository(input_file, tmp_path, capsys):
    module = load_script_module()
    target = tmp_path / "rec.json"

    code, payload = _run(
        module, capsys, ["--input", str(input_file), "--output", str(target), "--write"]
    )

    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not target.exists()


def test_write_refuses_dotdot_traversal_escaping_the_local_dir(input_file, capsys):
    module = load_script_module()
    target = LOCAL_DIR / ".." / "rec.json"
    escaped = REPO_ROOT / "data" / "evaluations" / "rec.json"

    code, payload = _run(
        module, capsys, ["--input", str(input_file), "--output", str(target), "--write"]
    )

    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not escaped.exists()


def test_write_refuses_a_symlink_escaping_the_local_dir(input_file, tmp_path, capsys):
    module = load_script_module()
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    link = LOCAL_DIR / f"pytest-escape-{uuid.uuid4().hex}"
    _symlink_or_skip(tmp_path, link, target_is_directory=True)

    try:
        code, payload = _run(
            module,
            capsys,
            ["--input", str(input_file), "--output", str(link / "rec.json"), "--write"],
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
    partial.write_text(json.dumps({"symbol": "SPY"}), encoding="utf-8")

    code, payload = _run(
        module, capsys, ["--input", str(partial), "--output", str(tmp_path / "o.json")]
    )

    assert code == 2
    assert payload == {"error": "invalid_input"}


def test_both_paths_are_required():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module.main(["--input", "only-input.json"], now=_NOW)
