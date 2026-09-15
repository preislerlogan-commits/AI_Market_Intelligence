"""Prove the evaluation foundation is fully offline: no connector, OpenAI
client, database, agent, orchestration, or network dependency -- statically
(AST import scan) and dynamically (fresh-interpreter module import)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "evaluation"

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "duckdb",
    "httpx",
    "pandas",
    "pyarrow",
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

# The step-d SPY VWAP-extension/reversion evaluation modules
# (spy_vwap_reversion_*.py) are deliberately exempt from the blanket
# "no market_features" rule above: their whole purpose is to consume
# outputs from the existing offline SPY intraday regime engine (see
# docs/OPTIONS_DECISION_WORKFLOW.md, step d). They still make no network,
# database, connector, model-client, agent, or orchestration import -- that
# boundary is proven separately and more precisely by
# test_spy_vwap_reversion_offline.py.
PYTHON_FILES = sorted(
    p for p in PACKAGE_DIR.rglob("*.py") if not p.name.startswith("spy_vwap_reversion_")
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


def test_there_are_python_files_to_scan():
    assert PYTHON_FILES


@pytest.mark.parametrize("path", PYTHON_FILES, ids=lambda p: p.name)
def test_no_evaluation_module_imports_a_forbidden_dependency(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in _imported_names(tree):
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"{path.name} imports forbidden module {name!r}"
            )


def test_importing_the_package_in_a_fresh_interpreter_pulls_in_nothing_networked():
    code = (
        "import sys\n"
        "import market_intelligence.evaluation as e\n"
        "from market_intelligence.evaluation import fixtures\n"
        "fixtures.load_all_fixtures()\n"
        "banned = {'openai', 'duckdb', 'httpx', 'socket', 'pandas', 'pyarrow'}\n"
        "leaked = banned & set(sys.modules)\n"
        "assert not leaked, leaked\n"
        "agenty = [m for m in sys.modules if m.startswith('market_intelligence.') and any(\n"
        "    k in m for k in ('data_connectors', 'model_clients', 'storage', 'agents',\n"
        "                     'orchestration', 'market_features'))]\n"
        "assert not agenty, agenty\n"
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
