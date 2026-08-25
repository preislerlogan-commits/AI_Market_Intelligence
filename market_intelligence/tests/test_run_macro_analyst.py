"""Tests for scripts/run_macro_analyst.py.

Every test injects a fake ``MacroAnalyst`` via ``main()``'s ``agent=``
parameter, so nothing here ever opens a real database or makes a live
OpenAI request.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.agents.macro_analyst import (
    AGENT_CATEGORY_CITATION_INVALID,
    MacroAnalystCitationError,
    MacroAnalystIncompleteError,
    MacroAnalystModelMetadata,
    MacroAnalystPreflightResult,
    MacroAnalystRefusalError,
    MacroAnalystReport,
    MacroAnalystRunResult,
    MacroAnalystValidationError,
)
from market_intelligence.model_clients.openai_structured import (
    CATEGORY_RESPONSE_VALIDATION_FAILED,
    OpenAIParseFailureError,
)

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_macro_analyst.py"

_UNEXPECTED_FAILURE_MARKER = (
    "simulated unexpected failure C:\\secret\\path SELECT * FROM macro_observations"
)


def load_script_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_macro_analyst", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_preflight(
    *, eligible: bool = True, reasons: tuple[str, ...] = ()
) -> MacroAnalystPreflightResult:
    return MacroAnalystPreflightResult(
        eligible=eligible,
        reasons=reasons,
        series_ids=("FEDFUNDS",),
        snapshot_created_at_utc="2026-08-24T12:00:00Z",
        source_series_count=1,
        flags={
            "missing_series": [],
            "stale_series": [],
            "future_dated_series": [],
            "missing_metadata_series": [],
            "latest_missing_series": [],
            "no_evidence_id_series": [],
        },
        evidence_package={"note": "...", "series_ids": ["FEDFUNDS"], "series": {"macro_x": {}}},
        snapshot={},
    )


class FakeAgent:
    """Records calls and returns/raises canned results for build_preflight/run."""

    def __init__(
        self,
        *,
        preflight: MacroAnalystPreflightResult | None = None,
        run_result: MacroAnalystRunResult | None = None,
        preflight_exception: Exception | None = None,
        run_exception: Exception | None = None,
    ) -> None:
        self._preflight = preflight
        self._run_result = run_result
        self._preflight_exception = preflight_exception
        self._run_exception = run_exception
        self.preflight_calls: list[list[str]] = []
        self.run_calls: list[list[str]] = []

    def build_preflight(self, series_ids):
        self.preflight_calls.append(list(series_ids))
        if self._preflight_exception is not None:
            raise self._preflight_exception
        return self._preflight

    def run(self, series_ids):
        self.run_calls.append(list(series_ids))
        if self._run_exception is not None:
            raise self._run_exception
        return self._run_result


def completed_run_result(**overrides) -> MacroAnalystRunResult:
    report_fields = dict(
        status="completed",
        series_ids=["FEDFUNDS"],
        snapshot_created_at_utc="2026-08-24T12:00:00Z",
        source_series_count=1,
        evidence_quality="sufficient",
        macro_claims=[
            {
                "series_id": "FEDFUNDS",
                "claim_summary": "The stored FEDFUNDS observation is 5.33 percent.",
                "evidence_ids": ["macro_x"],
                "economic_category": "policy_rate",
                "transmission_channels": ["rates"],
                "conditional_mechanism": None,
            }
        ],
        limitations=[],
    )
    report_fields.update(overrides.pop("report_fields", {}))
    metadata = overrides.pop(
        "model_metadata",
        MacroAnalystModelMetadata(
            model="gpt-5-mini",
            response_id="resp_unit_test_1",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
        ),
    )
    return MacroAnalystRunResult(
        report=MacroAnalystReport(**report_fields), model_metadata=metadata
    )


def abstained_run_result() -> MacroAnalystRunResult:
    report = MacroAnalystReport(
        status="abstained",
        series_ids=["FEDFUNDS"],
        snapshot_created_at_utc="2026-08-24T12:00:00Z",
        source_series_count=1,
        macro_claims=[],
        limitations=[],
        abstained_reasons=["series_missing"],
    )
    return MacroAnalystRunResult(report=report, model_metadata=None)


# ---------------------------------------------------------------------------
# Dry run (default mode): zero model calls, prints only sanitized summary
# ---------------------------------------------------------------------------


def test_dry_run_is_default_and_makes_zero_agent_run_calls(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    exit_code = module.main(["--series", "FEDFUNDS"], agent=fake_agent)

    assert exit_code == 0
    assert fake_agent.run_calls == []
    assert len(fake_agent.preflight_calls) == 1


def test_dry_run_defaults_to_fedfunds_when_series_omitted(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main([], agent=fake_agent)

    assert fake_agent.preflight_calls == [["FEDFUNDS"]]


def test_dry_run_series_flag_is_repeatable(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main(["--series", "FEDFUNDS", "--series", "UNRATE"], agent=fake_agent)

    assert fake_agent.preflight_calls == [["FEDFUNDS", "UNRATE"]]


def test_dry_run_prints_eligibility_reasons_series_count_and_flags(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight(eligible=False, reasons=("series_missing",)))

    exit_code = module.main(["--series", "FEDFUNDS"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["mode"] == "dry_run"
    assert output["eligible"] is False
    assert output["reasons"] == ["series_missing"]
    assert output["series_ids"] == ["FEDFUNDS"]
    assert output["series_count"] == 1
    assert "flags" in output
    assert output["flags"]["missing_series"] == []


def test_dry_run_never_prints_full_evidence_payload(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main(["--series", "FEDFUNDS"], agent=fake_agent)

    raw_output = capsys.readouterr().out
    assert "macro_x" not in raw_output
    assert '"series":' not in raw_output


def test_dry_run_prints_zero_model_metadata(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight=make_preflight())

    module.main(["--series", "FEDFUNDS"], agent=fake_agent)

    output = json.loads(capsys.readouterr().out)
    assert "model_metadata" not in output


def test_dry_run_rejects_invalid_series(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(preflight_exception=MacroAnalystValidationError("Invalid series_ids."))

    exit_code = module.main(["--series", "not a series"], agent=fake_agent)

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

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

    assert exit_code == 0
    assert len(fake_agent.run_calls) == 1
    assert fake_agent.preflight_calls == []


def test_execute_prints_validated_report_and_sanitized_metadata_without_response_id(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_result=completed_run_result())

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

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

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["report"]["status"] == "abstained"
    assert output["report"]["abstained_reasons"] == ["series_missing"]
    assert "model_metadata" not in output


def test_execute_prints_model_refusal_error_without_detail(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=MacroAnalystRefusalError("The model refused to analyze the evidence.")
    )

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output == {"error": "model_refusal"}


def test_execute_prints_model_incomplete_error_with_sanitized_reason(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        run_exception=MacroAnalystIncompleteError(
            "The model response was incomplete (reason=max_output_tokens)."
        )
    )

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

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

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "agent_error"
    assert output["detail"] == "OpenAI response failed structured-output validation."
    assert output["category"] == CATEGORY_RESPONSE_VALIDATION_FAILED


def test_dry_run_prints_category_for_macro_analyst_errors(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(
        preflight_exception=MacroAnalystCitationError(
            "Model output cited an evidence ID that was not in the evidence package sent."
        )
    )

    exit_code = module.main(["--series", "FEDFUNDS"], agent=fake_agent)

    assert exit_code == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"] == "agent_error"
    assert output["category"] == AGENT_CATEGORY_CITATION_INVALID


# --- Defensive final except: never leak a raw exception ------------------------


def test_dry_run_agent_construction_failure_prints_only_unexpected_error(monkeypatch, capsys):
    module = load_script_module()

    def _raising_agent(*args, **kwargs):
        raise RuntimeError(_UNEXPECTED_FAILURE_MARKER)

    monkeypatch.setattr(module, "MacroAnalyst", _raising_agent)

    exit_code = module.main(["--series", "FEDFUNDS"])

    assert exit_code != 0
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert _UNEXPECTED_FAILURE_MARKER not in raw_output
    assert "RuntimeError" not in raw_output


def test_execute_unexpected_run_failure_prints_only_unexpected_error(capsys):
    module = load_script_module()
    fake_agent = FakeAgent(run_exception=ValueError(_UNEXPECTED_FAILURE_MARKER))

    exit_code = module.main(["--series", "FEDFUNDS", "--execute"], agent=fake_agent)

    assert exit_code != 0
    raw_output = capsys.readouterr().out
    output = json.loads(raw_output)
    assert output == {"error": "unexpected_error"}
    assert _UNEXPECTED_FAILURE_MARKER not in raw_output
    assert "ValueError" not in raw_output
