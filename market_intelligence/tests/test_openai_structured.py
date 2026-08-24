"""Tests for market_intelligence.model_clients.openai_structured.

These tests never make a live network call. The OpenAI SDK client is always
injected (``sdk_client=``) as a small fake object recording call kwargs and
returning/raising canned results, mirroring how the other connector tests in
this project stub their transport (e.g.
market_intelligence/tests/test_fred_macro_data.py uses httpx.MockTransport).
Settings are always built from monkeypatched environment variables pointed
at a nonexistent .env file, so a real local .env is never read.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import httpx2
import openai
import pydantic
import pytest
from pydantic import BaseModel

from market_intelligence.config.settings import Settings
from market_intelligence.model_clients.openai_structured import (
    EVIDENCE_LABEL,
    EVIDENCE_SAFETY_APPENDIX,
    MAX_EVIDENCE_BYTES,
    MAX_INSTRUCTIONS_LENGTH,
    OpenAIAuthenticationError,
    OpenAIConfigMissingError,
    OpenAIConnectionError,
    OpenAIInvalidRequestError,
    OpenAIParseFailureError,
    OpenAIRateLimitError,
    OpenAIStructuredClient,
    OpenAITimeoutError,
    OpenAIUnexpectedError,
    StructuredOutputResult,
)

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]

FAKE_OPENAI_KEY = "unit-test-openai-key-should-never-appear-in-errors"


class Verdict(BaseModel):
    label: str
    confidence_note: str


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def configured_settings(monkeypatch, isolated_env_file: Path, **overrides) -> Settings:
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_OPENAI_KEY)
    return Settings(_env_file=isolated_env_file, **overrides)


def unconfigured_settings(isolated_env_file: Path) -> Settings:
    return Settings(_env_file=isolated_env_file)


class FakeResponses:
    """Stands in for ``openai.OpenAI().responses``: records calls, returns/raises canned results."""

    def __init__(self, *, result=None, exception: Exception | None = None):
        self._result = result
        self._exception = exception
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self._exception is not None:
            raise self._exception
        return self._result


class FakeSDKClient:
    def __init__(self, *, result=None, exception: Exception | None = None):
        self.responses = FakeResponses(result=result, exception=exception)


def make_content(*, type_: str, parsed=None) -> SimpleNamespace:
    return SimpleNamespace(type=type_, parsed=parsed)


def make_message(*, contents: list) -> SimpleNamespace:
    return SimpleNamespace(type="message", content=contents)


def make_response(
    *,
    status: str = "completed",
    output: list | None = None,
    output_parsed=None,
    incomplete_reason: str | None = None,
    response_id: str = "resp_unit_test_1",
    input_tokens: int | None = 12,
    output_tokens: int | None = 34,
    total_tokens: int | None = 46,
) -> SimpleNamespace:
    incomplete_details = (
        SimpleNamespace(reason=incomplete_reason) if status == "incomplete" else None
    )
    usage = (
        None
        if input_tokens is None and output_tokens is None and total_tokens is None
        else SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens
        )
    )
    return SimpleNamespace(
        id=response_id,
        status=status,
        incomplete_details=incomplete_details,
        usage=usage,
        output=output or [],
        output_parsed=output_parsed,
    )


def completed_response(**overrides) -> SimpleNamespace:
    verdict = Verdict(label="steady", confidence_note="from stored evidence only")
    content = make_content(type_="output_text", parsed=verdict)
    return make_response(
        status="completed",
        output=[make_message(contents=[content])],
        output_parsed=verdict,
        **overrides,
    )


def refusal_response(**overrides) -> SimpleNamespace:
    content = make_content(type_="refusal")
    return make_response(
        status="completed",
        output=[make_message(contents=[content])],
        output_parsed=None,
        **overrides,
    )


def incomplete_response(reason: str = "max_output_tokens", **overrides) -> SimpleNamespace:
    return make_response(
        status="incomplete",
        output=[],
        output_parsed=None,
        incomplete_reason=reason,
        **overrides,
    )


VALID_INSTRUCTIONS = "Classify the evidence as steady, bullish, or bearish. Be concise."
VALID_EVIDENCE = {"symbol": "SPY", "latest_close": "551.23", "headlines": ["Fed holds rates."]}


# ---------------------------------------------------------------------------
# Input validation (before any SDK client is built or request is made)
# ---------------------------------------------------------------------------


def test_generate_rejects_non_string_instructions(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(instructions=123, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert fake.responses.calls == []


def test_generate_rejects_blank_instructions(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(instructions="   ", evidence=VALID_EVIDENCE, output_model=Verdict)

    assert fake.responses.calls == []


def test_generate_rejects_oversized_instructions(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)
    too_long = "x" * (MAX_INSTRUCTIONS_LENGTH + 1)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(instructions=too_long, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert fake.responses.calls == []


def test_generate_rejects_non_dict_evidence(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=["not", "a", "dict"], output_model=Verdict
        )

    assert fake.responses.calls == []


def test_generate_rejects_evidence_with_non_string_keys(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence={1: "bad key"}, output_model=Verdict
        )

    assert fake.responses.calls == []


def test_generate_rejects_evidence_with_non_finite_float(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(
            instructions=VALID_INSTRUCTIONS,
            evidence={"value": float("nan")},
            output_model=Verdict,
        )

    assert fake.responses.calls == []


def test_generate_rejects_evidence_with_unsupported_type(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(
            instructions=VALID_INSTRUCTIONS,
            evidence={"value": {1, 2, 3}},
            output_model=Verdict,
        )

    assert fake.responses.calls == []


def test_generate_rejects_oversized_evidence(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)
    huge_evidence = {"headline": "x" * (MAX_EVIDENCE_BYTES + 1)}

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=huge_evidence, output_model=Verdict
        )

    assert fake.responses.calls == []


def test_generate_rejects_deeply_nested_evidence(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    nested: dict = {}
    cursor = nested
    for _ in range(20):
        cursor["next"] = {}
        cursor = cursor["next"]

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(instructions=VALID_INSTRUCTIONS, evidence=nested, output_model=Verdict)

    assert fake.responses.calls == []


def test_generate_rejects_output_model_instance_instead_of_class(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(
            instructions=VALID_INSTRUCTIONS,
            evidence=VALID_EVIDENCE,
            output_model=Verdict(label="x", confidence_note="y"),
        )

    assert fake.responses.calls == []


def test_generate_rejects_non_basemodel_output_model(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIInvalidRequestError):
        client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=dict)

    assert fake.responses.calls == []


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_is_configured_false_without_api_key(isolated_env_file):
    client = OpenAIStructuredClient(unconfigured_settings(isolated_env_file))
    assert client.is_configured() is False


def test_is_configured_true_with_api_key(monkeypatch, isolated_env_file):
    client = OpenAIStructuredClient(configured_settings(monkeypatch, isolated_env_file))
    assert client.is_configured() is True


def test_generate_raises_config_missing_without_api_key(isolated_env_file):
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(unconfigured_settings(isolated_env_file), sdk_client=fake)

    with pytest.raises(OpenAIConfigMissingError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )

    assert fake.responses.calls == []


# ---------------------------------------------------------------------------
# Fixed, non-caller-overridable request shape
# ---------------------------------------------------------------------------


def test_generate_always_sends_store_false(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert fake.responses.calls[0]["store"] is False


def test_generate_never_sends_tools(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert "tools" not in fake.responses.calls[0]


def test_generate_uses_configured_model_and_ignores_no_caller_override(
    monkeypatch, isolated_env_file
):
    settings = configured_settings(monkeypatch, isolated_env_file, openai_model="gpt-5-mini-test")
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert fake.responses.calls[0]["model"] == "gpt-5-mini-test"


def test_generate_default_model_is_gpt_5_mini(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert settings.openai_model == "gpt-5-mini"
    assert fake.responses.calls[0]["model"] == "gpt-5-mini"


def test_generate_passes_configured_timeout_and_max_output_tokens(monkeypatch, isolated_env_file):
    settings = configured_settings(
        monkeypatch,
        isolated_env_file,
        openai_request_timeout_seconds=12.5,
        openai_max_output_tokens=777,
    )
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert fake.responses.calls[0]["timeout"] == 12.5
    assert fake.responses.calls[0]["max_output_tokens"] == 777


def test_generate_wraps_evidence_with_fixed_label(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    sent_input = fake.responses.calls[0]["input"]
    assert sent_input[0]["role"] == "user"
    assert sent_input[0]["content"].startswith(EVIDENCE_LABEL)


def test_generate_appends_fixed_safety_appendix_to_instructions(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    sent_instructions = fake.responses.calls[0]["instructions"]
    assert sent_instructions.startswith(VALID_INSTRUCTIONS)
    assert EVIDENCE_SAFETY_APPENDIX in sent_instructions


def test_generate_serializes_evidence_deterministically(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake_a = FakeSDKClient(result=completed_response())
    fake_b = FakeSDKClient(result=completed_response())
    client_a = OpenAIStructuredClient(settings, sdk_client=fake_a)
    client_b = OpenAIStructuredClient(settings, sdk_client=fake_b)

    client_a.generate(
        instructions=VALID_INSTRUCTIONS, evidence={"b": 2, "a": 1}, output_model=Verdict
    )
    client_b.generate(
        instructions=VALID_INSTRUCTIONS, evidence={"a": 1, "b": 2}, output_model=Verdict
    )

    assert fake_a.responses.calls[0]["input"] == fake_b.responses.calls[0]["input"]


def test_generate_does_not_send_a_response_format_derived_from_caller_url_or_headers(
    monkeypatch, isolated_env_file
):
    """Only the documented kwargs (model/instructions/input/text_format/store/
    max_output_tokens/timeout) are ever sent -- confirming no base_url, headers,
    or endpoint override leaks through as an extra call kwarg."""
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    client.generate(instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict)

    assert set(fake.responses.calls[0].keys()) == {
        "model",
        "instructions",
        "input",
        "text_format",
        "store",
        "max_output_tokens",
        "timeout",
    }


# ---------------------------------------------------------------------------
# Response normalization
# ---------------------------------------------------------------------------


def test_generate_completed_response_returns_parsed_output_and_usage(
    monkeypatch, isolated_env_file
):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    result = client.generate(
        instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
    )

    assert isinstance(result, StructuredOutputResult)
    assert result.status == "completed"
    assert result.parsed == Verdict(label="steady", confidence_note="from stored evidence only")
    assert result.model == "gpt-5-mini"
    assert result.response_id == "resp_unit_test_1"
    assert result.input_tokens == 12
    assert result.output_tokens == 34
    assert result.total_tokens == 46
    assert result.incomplete_reason is None


def test_generate_completed_without_parsed_or_refusal_raises_unexpected(
    monkeypatch, isolated_env_file
):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=make_response(status="completed", output=[], output_parsed=None))
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIUnexpectedError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )


def test_generate_refusal_returns_refusal_status_without_parsed_output(
    monkeypatch, isolated_env_file
):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=refusal_response())
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    result = client.generate(
        instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
    )

    assert result.status == "refusal"
    assert result.parsed is None
    assert result.incomplete_reason is None


def test_generate_incomplete_returns_incomplete_status_and_reason(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=incomplete_response(reason="max_output_tokens"))
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    result = client.generate(
        instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
    )

    assert result.status == "incomplete"
    assert result.parsed is None
    assert result.incomplete_reason == "max_output_tokens"


def test_generate_unrecognized_status_raises_unexpected(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(result=make_response(status="cancelled", output=[], output_parsed=None))
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIUnexpectedError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )


def test_generate_handles_missing_usage(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    response = completed_response(input_tokens=None, output_tokens=None, total_tokens=None)
    fake = FakeSDKClient(result=response)
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    result = client.generate(
        instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
    )

    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.total_tokens is None


# ---------------------------------------------------------------------------
# Sanitized error-category mapping for SDK/parse failures
# ---------------------------------------------------------------------------


def _fake_request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.openai.com/v1/responses")


def _fake_response(status_code: int) -> httpx2.Response:
    return httpx2.Response(status_code, request=_fake_request())


def test_generate_maps_timeout_error(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(exception=openai.APITimeoutError(_fake_request()))
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAITimeoutError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )


def test_generate_maps_connection_error(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    fake = FakeSDKClient(exception=openai.APIConnectionError(request=_fake_request()))
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIConnectionError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )


def test_generate_maps_rate_limit_error(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    exc = openai.RateLimitError(
        f"rate limited, key={FAKE_OPENAI_KEY}", response=_fake_response(429), body=None
    )
    fake = FakeSDKClient(exception=exc)
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIRateLimitError) as exc_info:
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )

    assert FAKE_OPENAI_KEY not in str(exc_info.value)


def test_generate_maps_authentication_error(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    exc = openai.AuthenticationError(
        f"invalid api key {FAKE_OPENAI_KEY}", response=_fake_response(401), body=None
    )
    fake = FakeSDKClient(exception=exc)
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIAuthenticationError) as exc_info:
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )

    assert FAKE_OPENAI_KEY not in str(exc_info.value)


def test_generate_maps_other_sdk_status_errors_to_unexpected(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    exc = openai.InternalServerError(
        "server exploded, evidence dump: secret headline text",
        response=_fake_response(500),
        body=None,
    )
    fake = FakeSDKClient(exception=exc)
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIUnexpectedError) as exc_info:
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )

    assert "secret headline text" not in str(exc_info.value)


def test_generate_maps_pydantic_validation_error_to_parse_failure(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)

    try:
        Verdict.model_validate({"label": "bad"})
    except pydantic.ValidationError as captured:
        validation_error = captured
    else:  # pragma: no cover - defensive; Verdict requires confidence_note
        raise AssertionError("expected a ValidationError")

    fake = FakeSDKClient(exception=validation_error)
    client = OpenAIStructuredClient(settings, sdk_client=fake)

    with pytest.raises(OpenAIParseFailureError):
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )


# ---------------------------------------------------------------------------
# Sanitization: no secret/raw content ever leaks into errors or repr
# ---------------------------------------------------------------------------


def test_client_repr_never_reveals_api_key(monkeypatch, isolated_env_file):
    settings = configured_settings(monkeypatch, isolated_env_file)
    client = OpenAIStructuredClient(settings)

    assert FAKE_OPENAI_KEY not in repr(client)
    assert FAKE_OPENAI_KEY not in str(client)


def test_config_missing_error_message_is_fixed_and_sanitized(isolated_env_file):
    fake = FakeSDKClient(result=completed_response())
    client = OpenAIStructuredClient(unconfigured_settings(isolated_env_file), sdk_client=fake)

    with pytest.raises(OpenAIConfigMissingError) as exc_info:
        client.generate(
            instructions=VALID_INSTRUCTIONS, evidence=VALID_EVIDENCE, output_model=Verdict
        )

    assert str(exc_info.value) == "OpenAI API key is not configured."
