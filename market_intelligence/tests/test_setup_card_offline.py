"""Prove the setup-card core is offline and has no execution, ranking,
notification, dashboard or retrieval surface: static AST import scan and a
fresh-interpreter import check."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "setup_cards"
FILES = sorted(PACKAGE_DIR.glob("*.py"))

FORBIDDEN_IMPORT_PREFIXES = (
    "openai",
    "duckdb",
    "httpx",
    "socket",
    "requests",
    "urllib.request",
    "http",
    "market_intelligence.data_connectors",
    "market_intelligence.model_clients",
    "market_intelligence.storage",
    "market_intelligence.orchestration",
    "market_intelligence.config",
    "market_intelligence.agents",
)

EXPECTED_MODULES = {
    "__init__.py",
    "builder.py",
    "contracts.py",
    "definitions.py",
    "enums.py",
    "supersession.py",
    "validation.py",
}


def test_package_contains_exactly_the_core_modules():
    assert {p.name for p in FILES} == EXPECTED_MODULES


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_forbidden_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                assert not (name == prefix or name.startswith(prefix + ".")), (
                    f"{path.name} imports {name}"
                )


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_no_file_environment_or_clock_access(path):
    source = path.read_text(encoding="utf-8")
    for pattern in (
        r"\bopen\(",
        r"\bos\.environ\b",
        r"\bgetenv\(",
        r"\bPath\(",
        r"\bdatetime\.now\(",
        r"\butcnow\(",
    ):
        assert not re.search(pattern, source), f"{path.name} matches {pattern}"


def test_no_execution_ranking_or_notification_surface():
    for path in FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.ClassDef):
                lowered = node.name.lower()
                assert not any(
                    word in lowered
                    for word in ("order", "execute", "trade", "rank", "notify", "alert", "sms")
                ), f"{path.name} defines {node.name}"


def test_fresh_import_loads_no_network_or_database_module():
    code = (
        "import sys\n"
        "import market_intelligence.setup_cards.builder\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in "
        "('openai', 'duckdb', 'httpx', 'requests', 'socket')]\n"
        "bad += [m for m in sys.modules if m.startswith(("
        "'market_intelligence.data_connectors', 'market_intelligence.model_clients', "
        "'market_intelligence.storage', 'market_intelligence.orchestration'))]\n"
        "print(','.join(sorted(bad)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
