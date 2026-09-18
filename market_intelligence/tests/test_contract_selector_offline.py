"""Prove the deterministic SPY options-contract eligibility selector is
fully offline: no LLM, network, connector, brokerage, or database
dependency -- statically (AST import scan) and dynamically (fresh-
interpreter module import). Mirrors test_spy_vwap_reversion_offline.py,
extended to the contract_selection package plus its CLI script.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "market_intelligence" / "contract_selection"
CLI_SCRIPT = REPO_ROOT / "scripts" / "select_spy_option_contracts.py"

MODULE_FILES = sorted(p for p in PACKAGE_DIR.glob("*.py") if p.name != "__init__.py")

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


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                names.add(f".{node.module or ''}")
            elif node.module:
                names.add(node.module)
    return names


def test_there_are_selector_module_files_to_scan():
    assert MODULE_FILES
    assert len(MODULE_FILES) == 3  # contracts.py, selector.py, serialization.py


@pytest.mark.parametrize("path", MODULE_FILES, ids=lambda p: p.name)
def test_no_selector_module_imports_a_forbidden_dependency(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in _imported_names(tree):
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"{path.name} imports forbidden module {name!r}"
            )


def test_cli_script_imports_no_forbidden_dependency():
    tree = ast.parse(CLI_SCRIPT.read_text(encoding="utf-8"), filename=str(CLI_SCRIPT))
    for name in _imported_names(tree):
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"{CLI_SCRIPT.name} imports forbidden module {name!r}"
            )


def test_importing_the_selector_modules_in_a_fresh_interpreter_pulls_in_nothing_networked():
    code = (
        "import sys\n"
        "from datetime import date, datetime, UTC\n"
        "from decimal import Decimal\n"
        "from market_intelligence.contract_selection.contracts import (\n"
        "    ContractSelectorInput, OptionChainBatch, OptionContractQuote, OptionType,\n"
        ")\n"
        "from market_intelligence.contract_selection.selector import (\n"
        "    select_eligible_contracts,\n"
        ")\n"
        "from market_intelligence.contract_selection.serialization import (\n"
        "    to_json_str, from_json_str, input_to_json_str, input_from_json_str,\n"
        ")\n"
        "from market_intelligence.market_features.spy_regime_contracts import (\n"
        "    ScenarioHorizon,\n"
        ")\n"
        "quote = OptionContractQuote(\n"
        "    contract_symbol='SPY260916C00680000', option_type=OptionType.CALL,\n"
        "    expiration_date=date(2026, 9, 16), strike_price=Decimal('680'),\n"
        "    bid_price=Decimal('1.00'), bid_size=10, ask_price=Decimal('1.10'),\n"
        "    ask_size=10, implied_volatility=Decimal('0.2'), delta=Decimal('0.4'),\n"
        "    gamma=Decimal('0.05'), theta=Decimal('-0.1'), vega=Decimal('0.1'),\n"
        "    rho=Decimal('0.01'),\n"
        ")\n"
        "batch = OptionChainBatch(\n"
        "    provider='alpaca', feed='opra',\n"
        "    retrieved_at=datetime(2026, 9, 16, 14, 58, tzinfo=UTC), contracts=[quote],\n"
        ")\n"
        "selector_input = ContractSelectorInput(\n"
        "    scenario_horizon=ScenarioHorizon.INTRADAY_30M,\n"
        "    regime_as_of_timestamp=datetime(2026, 9, 16, 14, 58, tzinfo=UTC),\n"
        "    underlying_price_timestamp=datetime(2026, 9, 16, 14, 58, tzinfo=UTC),\n"
        "    as_of_timestamp=datetime(2026, 9, 16, 15, 0, tzinfo=UTC),\n"
        "    underlying_price=Decimal('680'), batch=batch,\n"
        ")\n"
        "moment = datetime(2026, 9, 16, 15, 1, tzinfo=UTC)\n"
        "result = select_eligible_contracts(selector_input, generated_at=moment)\n"
        "assert from_json_str(to_json_str(result)) == result\n"
        "assert input_from_json_str(input_to_json_str(selector_input)) == selector_input\n"
        "banned = {'openai', 'duckdb', 'httpx', 'socket', 'requests'}\n"
        "leaked = banned & set(sys.modules)\n"
        "assert not leaked, leaked\n"
        "prefixes = ('data_connectors', 'model_clients', 'storage', 'agents',\n"
        "            'orchestration', 'config')\n"
        "boundary_violations = [\n"
        "    m for m in sys.modules\n"
        "    if m.startswith('market_intelligence.') and any(k in m for k in prefixes)\n"
        "]\n"
        "assert not boundary_violations, boundary_violations\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
