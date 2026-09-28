"""Prove the Evidence Envelope core is fully offline: no connector, model
client, database, storage, orchestration or network dependency -- statically
(AST import scan) and dynamically (fresh-interpreter import). Mirrors
market_intelligence/tests/test_spy_regime_engine_offline.py."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "evidence"
EVIDENCE_FILES = sorted(PACKAGE_DIR.glob("*.py"))

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
    "market_intelligence.agents.market_evidence_agent",
    "market_intelligence.agents.news_analyst",
    "market_intelligence.agents.macro_analyst",
)

EXPECTED_MODULES = {
    "__init__.py",
    "bundle.py",
    "canonical.py",
    "contracts.py",
    "enums.py",
    "errors.py",
    "freshness.py",
    "holdout.py",
    "payloads.py",
    "primitives.py",
    "registry.py",
    "research_rules.py",
    "selector_boundary.py",
    "validation.py",
}


def _imported_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_package_contains_exactly_the_core_modules():
    assert {p.name for p in EVIDENCE_FILES} == EXPECTED_MODULES


@pytest.mark.parametrize("path", EVIDENCE_FILES, ids=lambda p: p.name)
def test_no_module_imports_a_forbidden_dependency(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in _imported_names(tree):
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"{path.name} imports forbidden module {name!r}"
            )


@pytest.mark.parametrize("path", EVIDENCE_FILES, ids=lambda p: p.name)
def test_no_module_opens_files_or_reads_the_environment(path):
    source = path.read_text(encoding="utf-8")
    for pattern in (
        r"\bopen\(",
        r"\bos\.environ\b",
        r"\bgetenv\(",
        r"\bPath\(",
        r"\bdatetime\.now\(",
        r"\butcnow\(",
    ):
        assert not re.search(pattern, source), f"{path.name} matches {pattern!r}"


def test_no_order_or_execution_surface_exists():
    for path in EVIDENCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.ClassDef):
                lowered = node.name.lower()
                assert not any(
                    word in lowered for word in ("order", "execute", "trade", "position")
                ), f"{path.name} defines {node.name}"


def test_fresh_interpreter_import_loads_no_network_or_database_module():
    code = (
        "import sys\n"
        "import market_intelligence.evidence.bundle\n"
        "import market_intelligence.evidence.validation\n"
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
