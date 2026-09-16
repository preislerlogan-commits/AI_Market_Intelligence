"""Tests for scripts/select_spy_option_contracts.py.

Offline only: the CLI consumes a local ``ContractSelectorInput`` JSON file
and never opens a database, makes a network request, or calls OpenAI / an
agent runtime -- it never reads the real DuckDB database. These tests assert
that boundary statically and exercise the explicit-path / dry-run /
no-overwrite / path-refusal / sanitization behaviour. Mirrors
test_evaluate_spy_vwap_reversion_cli.py.
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

import pytest

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    SelectorConfig,
)
from market_intelligence.contract_selection.serialization import (
    input_to_json_str,
    read_record,
)
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "select_spy_option_contracts.py"
LOCAL_DIR = REPO_ROOT / "data" / "evaluations" / "local"

AS_OF = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 9, 16, 14, 58, tzinfo=UTC)
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


def _quote() -> OptionContractQuote:
    return OptionContractQuote(
        contract_symbol="SPY260916C00680000",
        option_type=OptionType.CALL,
        expiration_date=date(2026, 9, 16),
        strike_price=Decimal("680"),
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal("1.10"),
        ask_size=10,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.10"),
        rho=Decimal("0.01"),
    )


def _small_valid_input() -> ContractSelectorInput:
    batch = OptionChainBatch(
        provider="alpaca", feed="opra", retrieved_at=RETRIEVED_AT, contracts=[_quote()],
    )
    return ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        batch=batch,
    )


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
    spec = importlib.util.spec_from_file_location("select_spy_option_contracts", SCRIPT_PATH)
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
    assert payload["status"] == "eligible"
    assert payload["candidate_contract_count"] == 1
    assert payload["eligible_contract_count"] == 1
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
    assert record.candidate_contract_count == 1


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
        "market_intelligence/contract_selection/rec.json",
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


# ---------------------------------------------------------------------------
# Feed-safety boundary: --allow-indicative-research
# ---------------------------------------------------------------------------


def _indicative_input_file(tmp_path, *, allow_in_file: bool, name: str = "indicative.json"):
    batch = OptionChainBatch(
        provider="alpaca", feed="indicative", retrieved_at=RETRIEVED_AT, contracts=[_quote()],
    )
    selector_input = ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        batch=batch,
        config=SelectorConfig(allow_indicative_for_research=allow_in_file),
    )
    path = tmp_path / name
    path.write_text(input_to_json_str(selector_input), encoding="utf-8")
    return path


def test_indicative_batch_without_the_flag_is_rejected_deterministically(tmp_path, capsys):
    module = load_script_module()
    path = _indicative_input_file(tmp_path, allow_in_file=False)

    code, payload = _run(
        module, capsys, ["--input", str(path), "--output", str(tmp_path / "o.json")]
    )

    assert code == 0
    assert payload["status"] == "no_eligible_contracts"
    assert payload["status"] != "eligible"
    assert payload["allow_indicative_research"] is False
    assert payload["eligible_contract_count"] == 0
    assert payload["research_only_contract_count"] == 0
    assert payload["rejection_counts"]["feed_not_allowed"] == 1


def test_indicative_batch_with_the_flag_returns_research_only_never_eligible(tmp_path, capsys):
    module = load_script_module()
    path = _indicative_input_file(tmp_path, allow_in_file=False)

    code, payload = _run(
        module,
        capsys,
        [
            "--input", str(path), "--output", str(tmp_path / "o.json"),
            "--allow-indicative-research",
        ],
    )

    assert code == 0
    assert payload["status"] == "research_only"
    assert payload["status"] != "eligible"
    assert payload["allow_indicative_research"] is True
    assert payload["eligible_contract_count"] == 0
    assert payload["research_only_contract_count"] == 1


def test_cli_flag_overrides_an_input_file_that_requests_research_mode(tmp_path, capsys):
    """The CLI must require its own explicit flag -- an input file cannot
    smuggle indicative processing through on its own."""
    module = load_script_module()
    path = _indicative_input_file(tmp_path, allow_in_file=True)

    code, payload = _run(
        module, capsys, ["--input", str(path), "--output", str(tmp_path / "o.json")]
    )

    assert code == 0
    assert payload["status"] == "no_eligible_contracts"
    assert payload["allow_indicative_research"] is False
    assert payload["rejection_counts"]["feed_not_allowed"] == 1


def test_flag_has_no_effect_on_an_opra_batch(input_file, capsys):
    module = load_script_module()

    code, payload = _run(
        module,
        capsys,
        [
            "--input", str(input_file), "--output", str(Path("o.json")),
            "--allow-indicative-research",
        ],
    )

    assert code == 0
    assert payload["status"] == "eligible"
    assert payload["feed"] == "opra"
    assert payload["allow_indicative_research"] is True


def test_write_persists_a_research_only_result(tmp_path, local_output, capsys):
    module = load_script_module()
    path = _indicative_input_file(tmp_path, allow_in_file=False)
    output = local_output()

    code, payload = _run(
        module,
        capsys,
        [
            "--input", str(path), "--output", str(output),
            "--write", "--allow-indicative-research",
        ],
    )

    assert code == 0
    assert payload["output_written"] is True
    record = read_record(output)
    assert record.status.value == "research_only"
    assert record.eligible_contracts == []
    assert record.research_only_contract_count == 1


# ---------------------------------------------------------------------------
# Indeterminate horizon end to end through the CLI
# ---------------------------------------------------------------------------


def test_indeterminate_horizon_reports_no_eligible_contracts(tmp_path, capsys):
    module = load_script_module()
    batch = OptionChainBatch(
        provider="alpaca", feed="opra", retrieved_at=RETRIEVED_AT, contracts=[_quote()],
    )
    selector_input = ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INDETERMINATE,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        batch=batch,
    )
    path = tmp_path / "indeterminate.json"
    path.write_text(input_to_json_str(selector_input), encoding="utf-8")

    code, payload = _run(
        module, capsys, ["--input", str(path), "--output", str(tmp_path / "o.json")]
    )

    assert code == 0
    assert payload["status"] == "indeterminate"
    assert payload["eligible_contract_count"] == 0
    assert payload["rejection_counts"]["horizon_indeterminate"] == 1
