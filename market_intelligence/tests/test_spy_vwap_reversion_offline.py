"""Prove the SPY VWAP-extension/reversion evaluation is fully offline: no
LLM, network, connector, brokerage, or database dependency -- statically
(AST import scan) and dynamically (fresh-interpreter module import).
Mirrors test_spy_regime_engine_offline.py, extended to the three new
evaluation modules plus the CLI script.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVALUATION_DIR = REPO_ROOT / "market_intelligence" / "evaluation"
CLI_SCRIPT = REPO_ROOT / "scripts" / "evaluate_spy_vwap_reversion.py"

MODULE_FILES = sorted(EVALUATION_DIR.glob("spy_vwap_reversion_*.py"))

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


def test_there_are_evaluation_module_files_to_scan():
    assert MODULE_FILES
    assert len(MODULE_FILES) == 3


@pytest.mark.parametrize("path", MODULE_FILES, ids=lambda p: p.name)
def test_no_evaluation_module_imports_a_forbidden_dependency(path):
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


def test_importing_the_evaluation_modules_in_a_fresh_interpreter_pulls_in_nothing_networked():
    code = (
        "import sys\n"
        "from datetime import date, datetime, UTC\n"
        "from decimal import Decimal\n"
        "from zoneinfo import ZoneInfo\n"
        "from market_intelligence.evaluation.spy_vwap_reversion_contracts import (\n"
        "    SessionBars, SpyVwapReversionEvaluationInput,\n"
        ")\n"
        "from market_intelligence.evaluation.spy_vwap_reversion_evaluator import (\n"
        "    evaluate_spy_vwap_reversion,\n"
        ")\n"
        "from market_intelligence.evaluation.spy_vwap_reversion_serialization import (\n"
        "    to_json_str, from_json_str, input_to_json_str, input_from_json_str,\n"
        ")\n"
        "from market_intelligence.market_features.spy_regime_contracts import IntradayBar\n"
        "eastern = ZoneInfo('America/New_York')\n"
        "bar = IntradayBar(\n"
        "    timestamp=datetime(2026, 6, 10, 9, 30, tzinfo=eastern),\n"
        "    open=Decimal('500'), high=Decimal('501'), low=Decimal('499'),\n"
        "    close=Decimal('500'), volume=1000,\n"
        ")\n"
        "session = SessionBars(session_date=date(2026, 6, 10), bars=[bar])\n"
        "evaluation_input = SpyVwapReversionEvaluationInput(sessions=[session])\n"
        "moment = datetime(2026, 6, 20, tzinfo=UTC)\n"
        "record = evaluate_spy_vwap_reversion(evaluation_input, generated_at=moment)\n"
        "assert from_json_str(to_json_str(record)) == record\n"
        "assert input_from_json_str(input_to_json_str(evaluation_input)) == evaluation_input\n"
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
