"""Tests for scripts/run_market_evidence_agent.py.

Every test injects a fake ``MarketEvidenceAgent`` via ``main()``'s ``agent=``
parameter, so nothing here ever opens a real database or makes a live
OpenAI request.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.agents.market_evidence_agent import (
    AgentRunResult,
    MarketEvidenceIncompleteError,
    MarketEvidenceRefusalError,
    MarketEvidenceReport,
    MarketEvidenceValidationError,
    ModelMetadata,
    PreflightResult,
)

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_market_evidence_agent.py"

_UNEXPECTED_FAILURE_MARKER = (
    "simulated unexpected failure C:\\secret\\path SELECT * FROM market_bars"
)


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_market_evidence_agent", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_preflight(*, eligible: bool = True, reasons: tuple[str, ...] = ()) -> PreflightResult:
    return PreflightResult(
        eligible=eligible,
        reasons=reasons,
        symbol="SPY",
        session_date_et="2026-08-19",
        flags={
            "bars_missing": False,
            "bars_stale": False,
            "session_complete": eligible,
            "partial_session": False,
            "missing_data": False,
            "unexpected_or_duplicate_timestamps_count": 0,
            "news_missing": False,
            "news_stale": False,
            "macro_missing_series": [],
            "macro_stale_series": [],
        },
        evidence_package={
            "symbol": "SPY",
            "session_date_et": "2026-08-19",
            "facts": {"bars_latest": {}},
        },
        deterministic_limitations=(),
        market_context={},
        session_quality={},
    )


class FakeAgent:
    """Records calls and returns/raises canned results for build_preflight/run."""

    def __init__(
        self,
        *,
        preflight: PreflightResult | None = None,
        run_result: AgentRunResult | None = None,
        preflight_exception: Exception | None = None,
        run_exception: Exception | None = None,
    ) -> None:
        self._preflight = preflight
        self._run_result = run_result
        self._preflight_exception = preflight_exception
        self._run_exception = run_exception
        self.preflight_calls: list[tuple[str, str | None]] = []
        self.run_calls: list[tuple[str, str | None]] = []

    def build_preflight(self, symbol, *, session_date=None):
        self.preflight_calls.append((symbol, session_date))
        if self._preflight_exception is not None:
            raise self._preflight_exception
        return self._preflight

    def run(self, symbol, *, session_date=None):
        self.run_calls.append((symbol, session_date))
        if self._run_exception is not None:
            raise self._run_exception
        return self._run_result


def completed_run_result(**overrides) -> AgentRunResult:
    report_fields = dict(
        status="completed",
        symbol="SPY",
        session_date_et="2026-08-19",
        evidence_quality="sufficient",
        evidence_summary="Evidence looks consistent.",
        observations=[
            {
                "category": "price",
                "statement": "Latest close is 551.23.",
                "evidence_ids": ["bars_latest"],
            }
        ],
        limitations=[],
    )
    report_fields.update(overrides.pop("report_fields", {}))
    metadata = overrides.pop(
        "model_metadata",
        ModelMetadata(
            model="gpt-5-mini",
            response_id="resp_unit_test_1",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        ),
    )
    return AgentRunResult(report=MarketEvidenceReport(**report_fields), model_metadata=metadata)


def abstained_run_result() -> AgentRunResult:
    report = MarketEvidenceReport(
        status="abstained",
        symbol="SPY",
        session_date_et="2026-08-19",
        abstained_reasons=["bars_missing"],
    )
    return AgentRunResult(report=report, model_metadata=None)


# ---------------------------------------------------------------------------
# Dry run (default mode): zero model calls, prints only sanitized summary
# ---------------------------------------------------------------------------


def test_dry_run_is_default_and_makes_zero_agent_run_calls(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    exit_code = module.main(["--symbol", "SPY"], agent=fake_agent)

    assert exit_code == 0
    assert fake_agent.run_calls == []
    assert len(fake_agent.preflight_calls) == 1


def test_dry_run_prints_eligibility_reasons_symbol_session_date_and_flags(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight(eligible=False, reasons=("bars_missing",)))

    exit_code = module.main(["--symbol", "SPY"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry_run"
    assert output["eligible"] is False
    assert output["reasons"] == ["bars_missing"]
    assert output["symbol"] == "SPY"
    assert output["session_date_et"] == "2026-08-19"
    assert output["evidence_item_count"] == 1
    assert "flags" in output
    assert output["flags"]["bars_missing"] is False


def test_dry_run_never_prints_full_evidence_payload(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main(["--symbol", "SPY"], agent=fake_agent)

    raw_output = capsys.readouterr().out
    assert "facts" not in raw_output


def test_dry_run_rejects_invalid_symbol(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight_exception=MarketEvidenceValidationError("Invalid symbol."))

    exit_code = module.main(["--symbol", "not a symbol"], agent=fake_agent)

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "invalid_input"


def test_main_rejects_unknown_argument():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module._parse_args(["--not-a-real-flag", "1"])


# ---------------------------------------------------------------------------
# Execute mode: requires --execute, gated by preflight inside agent.run()
# ---------------------------------------------------------------------------


def test_execute_with_flag_calls_run_not_preflight_directly(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_result=completed_run_result())

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 0
    assert len(fake_agent.run_calls) == 1
    assert fake_agent.preflight_calls == []


def test_execute_prints_validated_report_and_sanitized_metadata_without_response_id(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_result=completed_run_result())

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "execute"
    assert output["report"]["status"] == "completed"
    assert output["report"]["directional_assessment"] == "not_performed"
    assert output["report"]["trade_recommendation"] == "not_performed"
    assert output["model_metadata"]["model"] == "gpt-5-mini"
    assert output["model_metadata"]["total_tokens"] == 150
    assert "response_id" not in output["model_metadata"]


def test_execute_ineligible_preflight_prints_abstained_report_with_zero_tokens(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_result=abstained_run_result())

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["report"]["status"] == "abstained"
    assert output["report"]["abstained_reasons"] == ["bars_missing"]
    assert "model_metadata" not in output


def test_execute_prints_model_refusal_error_without_detail(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=MarketEvidenceRefusalError("The model refused to analyze the evidence.")
    )

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {"error": "model_refusal"}


def test_execute_prints_model_incomplete_error_with_sanitized_reason(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=MarketEvidenceIncompleteError(
            "The model response was incomplete (reason=max_output_tokens)."
        )
    )

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "model_incomplete"
    assert "max_output_tokens" in output["detail"]


# --- Defensive final except: never leak a raw exception ------------------------


def test_dry_run_agent_construction_failure_prints_only_unexpected_error(monkeypatch, capsys):
    module = load_script_module()

    def _raising_agent(*args, **kwargs):
        raise RuntimeError(_UNEXPECTED_FAILURE_MARKER)

    monkeypatch.setattr(module, "MarketEvidenceAgent", _raising_agent)

    exit_code = module.main(["--symbol", "SPY"])

    assert exit_code != 0
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert _UNEXPECTED_FAILURE_MARKER not in raw_output
    assert "RuntimeError" not in raw_output


def test_execute_unexpected_run_failure_prints_only_unexpected_error(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_exception=ValueError(_UNEXPECTED_FAILURE_MARKER))

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code != 0
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert _UNEXPECTED_FAILURE_MARKER not in raw_output
    assert "ValueError" not in raw_output
