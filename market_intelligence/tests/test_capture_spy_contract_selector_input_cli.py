"""Tests for scripts/capture_spy_contract_selector_input.py.

Offline only: every Alpaca client class the script would construct is
monkeypatched to a hand-written fake before ``--execute`` is exercised, so
these tests never make a network request. Mirrors
test_select_spy_option_contracts_cli.py's load-module-then-monkeypatch
pattern and its --write / path-allowlist coverage.
"""

from __future__ import annotations

import importlib.util
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.contract_selection.serialization import read_input
from market_intelligence.data_connectors.alpaca_bars import Bar
from market_intelligence.data_connectors.alpaca_options_chain import (
    OptionChainSnapshot,
    OptionChainSnapshotBatch,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "capture_spy_contract_selector_input.py"
LOCAL_DIR = REPO_ROOT / "data" / "evaluations" / "local"


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "capture_spy_contract_selector_input", SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(module: ModuleType, capsys, argv: list[str]) -> tuple[int, dict]:
    code = module.main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


@pytest.fixture
def local_output():
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    def _make(name: str = "capture.json") -> Path:
        path = LOCAL_DIR / f"pytest-{uuid.uuid4().hex}-{name}"
        created.append(path)
        return path

    yield _make

    for path in created:
        path.unlink(missing_ok=True)


def _bar(hour, minute, close="680.00"):
    ts = datetime(2026, 9, 16, hour, minute, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return Bar(
        provider="alpaca", symbol="SPY", timeframe="5Min", feed="iex", adjustment="raw",
        currency="USD", timestamp=ts, open=Decimal(close), high=Decimal(close),
        low=Decimal(close), close=Decimal(close), volume=1000, trade_count=10,
        vwap=Decimal(close), retrieved_at=ts,
    )


def _trending_bars():
    closes = ["680.00", "680.50", "681.00", "681.60", "682.20", "682.90", "683.60", "684.40"]
    bars = []
    for offset, close in zip(range(0, 40, 5), closes):
        hour = 13 + (30 + offset) // 60
        minute = (30 + offset) % 60
        bars.append(_bar(hour, minute, close=close))
    return bars


def _flat_bars():
    return [_bar(13, m) for m in (30, 35, 40, 45, 50, 55)]


def _snapshot():
    return OptionChainSnapshot(
        provider="alpaca", underlying="SPY", feed="indicative",
        contract_symbol="SPY260918C00680000", expiration_date="2026-09-18",
        option_type="call", strike_price=Decimal("680"),
        quote_timestamp="2026-09-16T14:11:50Z", bid_price=Decimal("1.00"), bid_size=10,
        ask_price=Decimal("1.10"), ask_size=10, trade_timestamp=None, trade_price=None,
        trade_size=None, implied_volatility=Decimal("0.2"), delta=Decimal("0.4"),
        gamma=Decimal("0.05"), theta=Decimal("-0.1"), vega=Decimal("0.1"),
        rho=Decimal("0.01"), retrieved_at="2026-09-16T14:11:50Z",
    )


class _FakeConfigurableClient:
    configured = True

    def __init__(self, settings=None):
        pass

    def is_configured(self) -> bool:
        return self.__class__.configured


class _FakeBarsClient(_FakeConfigurableClient):
    bars: list = []

    def get_bars(self, symbol, timeframe, *, start, end, limit, max_pages, client=None):
        return list(self.__class__.bars)


class _FakeMarketDataClient(_FakeConfigurableClient):
    payload: dict = {"latestTrade": {"p": "684.50", "t": "2026-09-16T14:11:30Z"}}

    def get_snapshot(self, symbol, *, client=None):
        return dict(self.__class__.payload)


class _FakeOptionsChainClient(_FakeConfigurableClient):
    snapshots: tuple = (_snapshot(),)
    retrieved_at = "2026-09-16T14:11:50Z"

    def get_chain_snapshot(self, request, *, client=None):
        return OptionChainSnapshotBatch(
            retrieved_at=self.__class__.retrieved_at, request=request,
            snapshots=self.__class__.snapshots,
        )


def _patch_clients(monkeypatch, module, *, bars, payload, configured=True):
    _FakeBarsClient.bars = bars
    _FakeBarsClient.configured = configured
    _FakeMarketDataClient.payload = payload
    _FakeMarketDataClient.configured = configured
    _FakeOptionsChainClient.configured = configured
    monkeypatch.setattr(module, "AlpacaBarsClient", _FakeBarsClient)
    monkeypatch.setattr(module, "AlpacaMarketDataClient", _FakeMarketDataClient)
    monkeypatch.setattr(module, "AlpacaOptionsChainClient", _FakeOptionsChainClient)


class _FixedClock:
    def __init__(self, *values):
        self._values = list(values)

    def __call__(self):
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


# ---------------------------------------------------------------------------
# Dry run (default) -- zero network calls
# ---------------------------------------------------------------------------


def test_dry_run_makes_no_client_calls_and_prints_the_fixed_plan(monkeypatch, capsys):
    module = load_script_module()

    calls = []

    class _NoisyClient(_FakeConfigurableClient):
        def get_bars(self, *a, **k):
            calls.append("bars")

        def get_snapshot(self, *a, **k):
            calls.append("price")

        def get_chain_snapshot(self, *a, **k):
            calls.append("chain")

    monkeypatch.setattr(module, "AlpacaBarsClient", _NoisyClient)
    monkeypatch.setattr(module, "AlpacaMarketDataClient", _NoisyClient)
    monkeypatch.setattr(module, "AlpacaOptionsChainClient", _NoisyClient)

    code, payload = _run(module, capsys, [])

    assert code == 0
    assert payload["mode"] == "dry_run"
    assert payload["underlying"] == "SPY"
    assert payload["feed"] == "indicative"
    assert payload["earliest_capture_time_et"] == "10:00:00"
    assert payload["min_completed_bars"] == 6
    assert payload["request_planned"] is False
    assert calls == []


def test_dry_run_reports_not_configured(monkeypatch, capsys):
    module = load_script_module()
    _patch_clients(monkeypatch, module, bars=[], payload={}, configured=False)
    code, payload = _run(module, capsys, [])
    assert code == 0
    assert payload["configured"] is False


# ---------------------------------------------------------------------------
# --execute
# ---------------------------------------------------------------------------


def test_execute_not_configured_yields_an_error_exit(monkeypatch, capsys):
    module = load_script_module()
    _patch_clients(monkeypatch, module, bars=[], payload={}, configured=False)
    code, payload = _run(module, capsys, ["--execute"])
    assert code == 1
    assert payload["outcome"] == "not_configured"


def test_execute_resolved_capture_reports_sanitized_summary(monkeypatch, capsys):
    module = load_script_module()
    _patch_clients(monkeypatch, module, bars=_trending_bars(), payload={
        "latestTrade": {"p": "684.50", "t": "2026-09-16T14:11:30Z"}
    })

    def _fake_capture(**kwargs):
        from market_intelligence.orchestration.spy_contract_capture import (
            capture_contract_selector_input,
        )

        kwargs["clock"] = _FixedClock(
            datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC),
            datetime(2026, 9, 16, 14, 12, 5, tzinfo=UTC),
        )
        return capture_contract_selector_input(**kwargs)

    monkeypatch.setattr(module, "capture_contract_selector_input", _fake_capture)

    code, payload = _run(module, capsys, ["--execute"])

    assert code == 0
    assert payload["status"] == "resolved"
    assert payload["scenario_horizon"] == "intraday_2h"
    assert payload["underlying_price"] == "684.50"
    assert payload["candidate_contract_count"] == 1
    assert payload["output_written"] is False
    assert "credential" not in json.dumps(payload).lower()


def test_execute_indeterminate_capture_reports_no_chain_activity(monkeypatch, capsys):
    module = load_script_module()
    _patch_clients(monkeypatch, module, bars=_flat_bars(), payload={
        "latestTrade": {"p": "680.00", "t": "2026-09-16T13:59:50Z"}
    })

    def _fake_capture(**kwargs):
        from market_intelligence.orchestration.spy_contract_capture import (
            capture_contract_selector_input,
        )

        kwargs["clock"] = _FixedClock(datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC))
        return capture_contract_selector_input(**kwargs)

    monkeypatch.setattr(module, "capture_contract_selector_input", _fake_capture)

    code, payload = _run(module, capsys, ["--execute"])

    assert code == 1
    assert payload["status"] == "indeterminate"
    assert payload["candidate_contract_count"] is None


# ---------------------------------------------------------------------------
# --write
# ---------------------------------------------------------------------------


def test_write_requires_output(monkeypatch, capsys):
    module = load_script_module()
    code, payload = _run(module, capsys, ["--execute", "--write"])
    assert code == 2
    assert payload == {"error": "output_required_with_write"}


def test_write_refuses_output_outside_the_local_allowlist(monkeypatch, capsys, tmp_path):
    module = load_script_module()
    target = tmp_path / "capture.json"
    code, payload = _run(
        module, capsys, ["--execute", "--write", "--output", str(target)]
    )
    assert code == 2
    assert payload == {"error": "output_path_refused"}
    assert not target.exists()


def test_write_persists_a_resolved_capture(monkeypatch, capsys, local_output):
    module = load_script_module()
    _patch_clients(monkeypatch, module, bars=_trending_bars(), payload={
        "latestTrade": {"p": "684.50", "t": "2026-09-16T14:11:30Z"}
    })

    def _fake_capture(**kwargs):
        from market_intelligence.orchestration.spy_contract_capture import (
            capture_contract_selector_input,
        )

        kwargs["clock"] = _FixedClock(
            datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC),
            datetime(2026, 9, 16, 14, 12, 5, tzinfo=UTC),
        )
        return capture_contract_selector_input(**kwargs)

    monkeypatch.setattr(module, "capture_contract_selector_input", _fake_capture)

    output = local_output()
    code, payload = _run(
        module, capsys, ["--execute", "--write", "--output", str(output)]
    )

    assert code == 0
    assert payload["output_written"] is True
    selector_input = read_input(output)
    assert selector_input.scenario_horizon.value == "intraday_2h"
    assert selector_input.batch.feed.value == "indicative"


def test_write_is_skipped_when_capture_is_not_resolved(monkeypatch, capsys, local_output):
    module = load_script_module()
    _patch_clients(monkeypatch, module, bars=_flat_bars(), payload={
        "latestTrade": {"p": "680.00", "t": "2026-09-16T13:59:50Z"}
    })

    def _fake_capture(**kwargs):
        from market_intelligence.orchestration.spy_contract_capture import (
            capture_contract_selector_input,
        )

        kwargs["clock"] = _FixedClock(datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC))
        return capture_contract_selector_input(**kwargs)

    monkeypatch.setattr(module, "capture_contract_selector_input", _fake_capture)

    output = local_output()
    code, payload = _run(
        module, capsys, ["--execute", "--write", "--output", str(output)]
    )

    assert code == 1
    assert payload["output_written"] is False
    assert payload["write_skipped_reason"] == "capture_not_resolved"
    assert not output.exists()
