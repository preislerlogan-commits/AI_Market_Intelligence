"""Prove the SPY intraday regime engine is fully offline: no connector,
OpenAI client, database, agent, orchestration, or network dependency --
statically (AST import scan) and dynamically (fresh-interpreter module
import). Mirrors market_intelligence/tests/test_evaluation_offline.py.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PACKAGE_DIR = Path(__file__).resolve().parents[1] / "market_features"

REGIME_ENGINE_FILES = sorted(
    p for p in PACKAGE_DIR.glob("spy_regime_*.py")
)

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


def test_there_are_regime_engine_files_to_scan():
    assert REGIME_ENGINE_FILES
    assert len(REGIME_ENGINE_FILES) == 3


@pytest.mark.parametrize("path", REGIME_ENGINE_FILES, ids=lambda p: p.name)
def test_no_regime_engine_module_imports_a_forbidden_dependency(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in _imported_names(tree):
        for prefix in FORBIDDEN_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"{path.name} imports forbidden module {name!r}"
            )


EXPECTED_PURE_SPY_MODULES = {
    "spy_regime_contracts.py",
    "spy_regime_features.py",
    "spy_regime_classifier.py",
}


def test_scanned_regime_engine_files_are_exactly_the_pure_modules():
    assert {p.name for p in REGIME_ENGINE_FILES} == EXPECTED_PURE_SPY_MODULES


def test_storage_reading_input_builder_lives_in_orchestration_not_market_features():
    """The read-only VWAP-reversion input builder opens DuckDB, so it belongs
    in ``orchestration/``, never beside the pure regime engine in
    ``market_features/``; the pure modules stay free of forbidden imports."""
    assert not (PACKAGE_DIR / "spy_vwap_reversion_input_builder.py").exists()
    assert (
        PACKAGE_DIR.parent / "orchestration" / "spy_vwap_reversion_input_builder.py"
    ).is_file()
    for path in REGIME_ENGINE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name in _imported_names(tree):
            for prefix in FORBIDDEN_IMPORT_PREFIXES:
                assert not (name == prefix or name.startswith(prefix + ".")), (
                    f"{path.name} imports forbidden module {name!r}"
                )


def test_importing_the_regime_engine_in_a_fresh_interpreter_pulls_in_nothing_networked():
    code = (
        "import sys\n"
        "from datetime import date, datetime\n"
        "from decimal import Decimal\n"
        "from zoneinfo import ZoneInfo\n"
        "from market_intelligence.market_features.spy_regime_contracts import (\n"
        "    RegimeEngineInput, IntradayBar, CatalystState, BreadthState,\n"
        ")\n"
        "from market_intelligence.market_features.spy_regime_features import compute_features\n"
        "from market_intelligence.market_features.spy_regime_classifier import classify\n"
        "eastern = ZoneInfo('America/New_York')\n"
        "bar = IntradayBar(\n"
        "    timestamp=datetime(2026, 6, 10, 9, 30, tzinfo=eastern),\n"
        "    open=Decimal('500'), high=Decimal('501'), low=Decimal('499'),\n"
        "    close=Decimal('500'), volume=1000,\n"
        ")\n"
        "engine_input = RegimeEngineInput(\n"
        "    session_date=date(2026, 6, 10), bars=[bar], prior_day=None,\n"
        "    same_time_historical_volume_baseline=None,\n"
        "    catalyst_state=CatalystState.UNKNOWN, breadth_state=BreadthState.UNAVAILABLE,\n"
        ")\n"
        "classify(compute_features(engine_input))\n"
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
