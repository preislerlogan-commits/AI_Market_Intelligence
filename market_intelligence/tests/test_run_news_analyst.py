"""Tests for scripts/run_news_analyst.py.

Every test injects a fake ``NewsAnalyst`` via ``main()``'s ``agent=``
parameter, so nothing here ever opens a real database or makes a live
OpenAI request.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.agents.news_analyst import (
    AGENT_CATEGORY_CITATION_INVALID,
    NewsAnalystCitationError,
    NewsAnalystIncompleteError,
    NewsAnalystModelMetadata,
    NewsAnalystPreflightResult,
    NewsAnalystRefusalError,
    NewsAnalystReport,
    NewsAnalystRunResult,
    NewsAnalystValidationError,
)
from market_intelligence.model_clients.openai_structured import (
    CATEGORY_RESPONSE_VALIDATION_FAILED,
    OpenAIParseFailureError,
)

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_news_analyst.py"

_UNEXPECTED_FAILURE_MARKER = (
    "simulated unexpected failure C:\\secret\\path SELECT * FROM news_articles"
)


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_news_analyst", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_preflight(
    *, eligible: bool = True, reasons: tuple[str, ...] = ()
) -> NewsAnalystPreflightResult:
    return NewsAnalystPreflightResult(
        eligible=eligible,
        reasons=reasons,
        symbol="SPY",
        snapshot_created_at_utc="2026-08-24T12:00:00Z",
        source_article_count=1,
        freshness={
            "stale_after_hours": 168,
            "missing": False,
            "stale": not eligible,
            "future_timestamp_detected": False,
        },
        headline_only_count=0,
        summary_available_count=1,
        evidence_package={
            "note": "untrusted",
            "symbol": "SPY",
            "articles": {"news_aaaa1111bbbb2222": {}},
        },
        snapshot={},
    )


class FakeAgent:
    """Records calls and returns/raises canned results for build_preflight/run."""

    def __init__(
        self,
        *,
        preflight: NewsAnalystPreflightResult | None = None,
        run_result: NewsAnalystRunResult | None = None,
        preflight_exception: Exception | None = None,
        run_exception: Exception | None = None,
    ) -> None:
        self._preflight = preflight
        self._run_result = run_result
        self._preflight_exception = preflight_exception
        self._run_exception = run_exception
        self.preflight_calls: list[tuple[str, int]] = []
        self.run_calls: list[tuple[str, int]] = []

    def build_preflight(self, symbol, *, limit=10):
        self.preflight_calls.append((symbol, limit))
        if self._preflight_exception is not None:
            raise self._preflight_exception
        return self._preflight

    def run(self, symbol, *, limit=10):
        self.run_calls.append((symbol, limit))
        if self._run_exception is not None:
            raise self._run_exception
        return self._run_result


def completed_run_result(**overrides) -> NewsAnalystRunResult:
    report_fields = dict(
        status="completed",
        symbol="SPY",
        snapshot_created_at_utc="2026-08-24T12:00:00Z",
        source_article_count=1,
        evidence_quality="sufficient",
        event_claims=[
            {
                "event_type": "monetary_policy",
                "claim_summary": "The provider reports the Fed held rates steady.",
                "evidence_ids": ["news_aaaa1111bbbb2222"],
                "content_basis": "headline_only",
                "transmission_channels": ["rates"],
                "conditional_mechanism": None,
            }
        ],
        limitations=[],
    )
    report_fields.update(overrides.pop("report_fields", {}))
    metadata = overrides.pop(
        "model_metadata",
        NewsAnalystModelMetadata(
            model="gpt-5-mini",
            response_id="resp_unit_test_1",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        ),
    )
    return NewsAnalystRunResult(report=NewsAnalystReport(**report_fields), model_metadata=metadata)


def abstained_run_result() -> NewsAnalystRunResult:
    report = NewsAnalystReport(
        status="abstained",
        symbol="SPY",
        snapshot_created_at_utc="2026-08-24T12:00:00Z",
        source_article_count=0,
        abstained_reasons=["news_missing"],
    )
    return NewsAnalystRunResult(report=report, model_metadata=None)


# ---------------------------------------------------------------------------
# Dry run (default mode): zero model calls, prints only sanitized summary
# ---------------------------------------------------------------------------


def test_dry_run_is_default_and_makes_zero_agent_run_calls():
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    exit_code = module.main(["--symbol", "SPY"], agent=fake_agent)

    assert exit_code == 0
    assert fake_agent.run_calls == []
    assert len(fake_agent.preflight_calls) == 1


def test_dry_run_prints_eligibility_reasons_symbol_counts_and_freshness(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight(eligible=False, reasons=("news_stale",)))

    exit_code = module.main(["--symbol", "SPY"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry_run"
    assert output["eligible"] is False
    assert output["reasons"] == ["news_stale"]
    assert output["symbol"] == "SPY"
    assert output["article_count"] == 1
    assert "freshness" in output
    assert output["headline_only_count"] == 0
    assert output["summary_available_count"] == 1


def test_dry_run_never_prints_full_evidence_payload(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main(["--symbol", "SPY"], agent=fake_agent)

    raw_output = capsys.readouterr().out
    assert "articles" not in raw_output


def test_dry_run_rejects_invalid_symbol(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight_exception=NewsAnalystValidationError("Invalid symbol."))

    exit_code = module.main(["--symbol", "not a symbol"], agent=fake_agent)

    assert exit_code == 2
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "invalid_input"


def test_main_rejects_unknown_argument():
    module = load_script_module()
    with pytest.raises(SystemExit):
        module._parse_args(["--not-a-real-flag", "1"])


def test_dry_run_passes_limit_through():
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main(["--symbol", "SPY", "--limit", "5"], agent=fake_agent)

    assert fake_agent.preflight_calls == [("SPY", 5)]


# ---------------------------------------------------------------------------
# Execute mode: requires --execute, gated by preflight inside agent.run()
# ---------------------------------------------------------------------------


def test_execute_with_flag_calls_run_not_preflight_directly():
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


def test_execute_never_prints_article_urls(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_result=completed_run_result())

    module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    raw_output = capsys.readouterr().out
    assert "https://" not in raw_output


def test_execute_ineligible_preflight_prints_abstained_report_with_zero_tokens(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_result=abstained_run_result())

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["report"]["status"] == "abstained"
    assert output["report"]["abstained_reasons"] == ["news_missing"]
    assert "model_metadata" not in output


def test_execute_prints_model_refusal_error_without_detail(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=NewsAnalystRefusalError("The model refused to analyze the evidence.")
    )

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {"error": "model_refusal"}


def test_execute_prints_model_incomplete_error_with_sanitized_reason(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=NewsAnalystIncompleteError(
            "The model response was incomplete (reason=max_output_tokens)."
        )
    )

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "model_incomplete"
    assert "max_output_tokens" in output["detail"]


# --- Sanitized failure classification: agent_error payloads carry `category` ---


def test_execute_prints_response_validation_failed_category_for_parse_failure(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=OpenAIParseFailureError(
            "OpenAI response failed structured-output validation."
        )
    )

    exit_code = module.main(["--symbol", "SPY", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "agent_error"
    assert output["category"] == CATEGORY_RESPONSE_VALIDATION_FAILED


def test_dry_run_prints_category_for_news_analyst_agent_errors(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        preflight_exception=NewsAnalystCitationError(
            "Model output cited an evidence ID that was not in the evidence package sent."
        )
    )

    exit_code = module.main(["--symbol", "SPY"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "agent_error"
    assert output["category"] == AGENT_CATEGORY_CITATION_INVALID


# --- Defensive final except: never leak a raw exception ------------------------


def test_dry_run_agent_construction_failure_prints_only_unexpected_error(monkeypatch, capsys):
    module = load_script_module()

    def _raising_agent(*args, **kwargs):
        raise RuntimeError(_UNEXPECTED_FAILURE_MARKER)

    monkeypatch.setattr(module, "NewsAnalyst", _raising_agent)

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
