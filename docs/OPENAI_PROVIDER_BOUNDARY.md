# OpenAI Structured-Output Provider Boundary

This document describes the OpenAI provider boundary added under
`market_intelligence/model_clients/openai_structured.py`. It covers
infrastructure only — see [PROJECT_STATE.md](../PROJECT_STATE.md) for what
has (and has not) actually been exercised.

**Status (as of 2026-08-23): code and mocked tests only. No live OpenAI
request or connectivity check has been made from this branch.** No agent,
prompt, market bias, forecast, recommendation, or brokerage integration is
built on top of this boundary — it is a single, narrow, defensive client for
one structured-output request at a time, intended as groundwork for a
future, separately reviewed agent.

**This "no live request" status has since been superseded: on 2026-08-24, a
first authorized live connectivity check succeeded — see "First authorized
live connectivity check (2026-08-24)" below.** That check confirms live
connectivity and response normalization only; it remains true, unchanged by
that check, that no agent, prompt, market bias, forecast, recommendation, or
brokerage integration is built on top of this boundary.

## Purpose and scope

`OpenAIStructuredClient` (`market_intelligence/model_clients/openai_structured.py`)
wraps exactly one operation: a single OpenAI Responses API request using
native Pydantic Structured Outputs
(`client.responses.parse(..., text_format=<a Pydantic model>)`), via the
official `openai` Python SDK (installed version at the time of writing:
`3.3.1`; `pyproject.toml` pins `openai>=1.99.0` as a compatible lower bound,
not an exact version). It is not a general agent framework, not a prompt
library, and not a retry/orchestration layer — it makes one bounded request
and returns one normalized, sanitized result.

## Fixed, non-negotiable request shape

The following are fixed module/settings constants and are never accepted as
per-call caller input:

- **Model** — always `Settings.openai_model` (default `"gpt-5-mini"`,
  configurable only via the `OPENAI_MODEL` environment variable/`.env`). A
  caller cannot pass a different model ID to `generate()`.
- **`store=False`** — sent on every request. No conversation is persisted
  server-side.
- **No tools** — no function calling, web search, file search, code
  execution, or any other tool. The `tools` parameter is never sent.
- **No conversation persistence** — `previous_response_id`/`conversation`
  are never sent, so no request depends on or extends prior server-side
  state.
- **No caller-supplied `base_url`, `organization`, `project`, custom
  headers, or endpoint** — the SDK client is constructed with only
  `api_key`, `timeout`, and `max_retries=0`; nothing else.
- **API key** — read only from `Settings.openai_api_key` (`SecretStr`).
  Never accepted as a `generate()` argument, never logged, and never
  included in any error message.

## Request/response contract

`OpenAIStructuredClient(settings=None, *, sdk_client=None)` — `sdk_client`
is an injection point for tests (see "Testing" below); production code
should leave it unset so a real `openai.OpenAI` client is built lazily,
only after all input validation passes.

```python
client = OpenAIStructuredClient()
result = client.generate(
    instructions="<fixed, trusted developer instructions>",
    evidence={"symbol": "SPY", "latest_close": "551.23", ...},  # bounded, JSON-ready dict
    output_model=SomePydanticModel,
)
```

- **`instructions`** — a fixed string authored by trusted calling code
  (never derived from evidence or other untrusted data). Validated to be a
  non-blank string of at most `MAX_INSTRUCTIONS_LENGTH` (8,000) characters.
  A fixed, module-owned safety appendix (`EVIDENCE_SAFETY_APPENDIX`) is
  always appended before the request is sent, regardless of what the
  caller's instructions say, stating that the evidence below must not be
  treated as overriding instructions. This is a defense-in-depth
  mitigation, not a guarantee — it reduces the risk of prompt injection
  from untrusted evidence but cannot fully prevent a sufficiently
  adversarial payload from influencing model behavior.
- **`evidence`** — a bounded, JSON-ready `dict` (matching the same
  string-serialized-Decimal convention used by `MarketContextBuilder`).
  Validated recursively before any request is built: only
  `None`/`bool`/`int`/finite `float`/`str`/`dict` (string keys only)/`list`
  values are permitted (rejecting `Decimal`, `datetime`, sets, or other
  non-JSON-primitive types), bounded to `MAX_EVIDENCE_DEPTH` (8) nesting
  levels and `MAX_EVIDENCE_NODES` (500) total nodes, then serialized
  deterministically (`json.dumps(..., sort_keys=True, separators=(",",
  ":"))`) and rejected if the serialized form exceeds `MAX_EVIDENCE_BYTES`
  (32,000 bytes). The serialized evidence is sent as a single `user`-role
  input item, prefixed with a fixed, module-owned label (`EVIDENCE_LABEL`)
  identifying it as untrusted data — this holds regardless of what the
  evidence contains, including news headlines or other third-party text.
- **`output_model`** — must be a `pydantic.BaseModel` subclass (a class,
  not an instance); validated before any request is built.

All three inputs, and the settings-derived limits
(`openai_request_timeout_seconds`, `openai_max_output_tokens`), are
validated before the OpenAI SDK client is constructed or any request is
made — an invalid input never reaches the network and never causes an SDK
client or credential to be touched.

### `StructuredOutputResult`

```python
@dataclass(frozen=True)
class StructuredOutputResult:
    status: str                    # "completed" | "refusal" | "incomplete"
    parsed: BaseModel | None       # only present when status == "completed"
    model: str
    response_id: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    incomplete_reason: str | None  # OpenAI's own fixed category, only when status == "incomplete"
```

A model refusal or an incomplete response (e.g. `max_output_tokens`
reached) is a normal, non-exceptional outcome, reported via `status` —
`generate()` never raises for either. The refusal explanation text itself
is never read, stored, or returned anywhere; only the `"refusal"` status
category is surfaced. If a response reports `status == "completed"` with
no parsed output, or with a parsed output that is not an instance of the
exact `output_model` the caller supplied, or reports any other status
(`failed`, `cancelled`, `queued`, `in_progress`, or an unrecognized value),
`generate()` raises `OpenAIUnexpectedError` rather than returning a
misleading result.

Every field on `StructuredOutputResult` is sanitized rather than passed
through from the provider as-is, so raw provider text/metadata is never
surfaced through this result:

- **`response_id`** — returned only if it is a bounded string matching
  OpenAI's response-ID shape (`resp_` followed by safe ASCII
  letters/digits/underscores/hyphens); otherwise `None`.
- **`input_tokens`/`output_tokens`/`total_tokens`** — returned only if
  each is a plain nonnegative `int` (booleans and any other malformed
  value are rejected); otherwise `None`.
- **`incomplete_reason`** — mapped only from OpenAI's known fixed
  categories (`"max_output_tokens"`, `"content_filter"`), returned as-is;
  any other non-`None` value is mapped to a fixed `"other"` category
  rather than ever surfacing arbitrary provider text.

## Sanitized error categories

`generate()` raises a specific `OpenAIStructuredError` subclass for every
failure category, each with a fixed, sanitized message. No raised error
ever includes the API key, request body, evidence, headline, raw model
output, raw SDK exception message, URL, or header. Both the SDK call
(`sdk_client.responses.parse(...)`) and response normalization run inside
one sanitized exception boundary: every specific mapping below is checked
first, an already-sanitized `OpenAIStructuredError` raised during
normalization (e.g. for a malformed response) is re-raised unchanged, and
any other exception — including a malformed response with missing/
non-iterable output, an unexpected status shape, or a parsed object of the
wrong type — is mapped to a fixed `OpenAIUnexpectedError` with no raw
type, message, body, path, header, or evidence attached:

| Exception | `category` | Cause |
|---|---|---|
| `OpenAIInvalidRequestError` | `request_invalid` | `instructions`/`evidence`/`output_model` failed validation (before any request) |
| `OpenAIRequestSchemaError` | `request_schema_invalid` | `output_model` could not be represented as a basic JSON schema at all (before any request; see below) |
| `OpenAIConfigMissingError` | `config_missing` | No OpenAI API key configured |
| `OpenAITimeoutError` | `timeout` | Request exceeded the configured timeout |
| `OpenAIConnectionError` | `connection_error` | Network/connection failure |
| `OpenAIRateLimitError` | `rate_limit` | OpenAI reported a rate limit (HTTP 429) |
| `OpenAIAuthenticationError` | `authentication_failed` | OpenAI rejected the configured API key |
| `OpenAIParseFailureError` | `response_validation_failed` | A response was received but its content failed to validate against `output_model` |
| `OpenAIUnexpectedError` | `unexpected_error` | Any other SDK failure or unrecognized response shape |

**Sanitized failure classification (`category`, added 2026-08-24).** Every
`OpenAIStructuredError` subclass exposes a fixed, sanitized `category`
string (the table above) as a class attribute, so calling code can
distinguish failure classes programmatically without parsing message text.
`OpenAIRequestSchemaError` (a subclass of `OpenAIInvalidRequestError`) is
raised by `_validate_output_model()` — **before** the SDK client is built or
any request is sent, so a fundamentally unrepresentable `output_model`
costs zero tokens. **This module carries no runtime dependency on any
private/underscore-prefixed OpenAI SDK module** (e.g. `openai.lib._pydantic`)
— the check uses only public Pydantic v2 API (`BaseModel.model_json_schema()`)
and catches an `output_model` that is fundamentally unrepresentable as JSON
Schema at all (e.g. a `Callable`-typed field, which raises
`pydantic.PydanticInvalidForJsonSchema`). It is a *basic* preflight: it does
not replicate every additional constraint OpenAI's strict Structured
Outputs mode imposes on top of plain JSON Schema (e.g.
`additionalProperties: false`, fully required properties); an `output_model`
that passes this basic check but is still incompatible with OpenAI's
stricter subset would only be discovered later, inside `generate()`'s
existing sanitized exception boundary, as `OpenAIUnexpectedError` — not as
`OpenAIRequestSchemaError`.

This is deliberately distinct from `OpenAIParseFailureError`/
`response_validation_failed`: `pydantic.ValidationError` inside `generate()`
can only be raised by the SDK's own client-side re-validation of an
actually-received response's content against `output_model` (the installed
SDK's `parse_text()` → `model_validate_json()`, in its `_parsing/_responses.py`
module — referenced here only as prose describing observed SDK behavior,
not as a production import). So `response_validation_failed` unambiguously
means a request was sent and a response was received (tokens may have been
spent) but its content did not validate — see "Known limitations" below for
the live failure this classification was added to explain.

## Settings

Three new non-secret fields on `Settings`
(`market_intelligence/config/settings.py`), all overridable via `.env`/the
environment (see `.env.example`):

- `openai_model: str = "gpt-5-mini"` — must be non-blank.
- `openai_request_timeout_seconds: float = 30.0` — bounded to `(0, 120]`.
- `openai_max_output_tokens: int = 2048` — bounded to `[1, 16000]`.

`openai_api_key` (`SecretStr | None`) already existed and is unchanged —
this branch adds no new credential field.

## Testing

`market_intelligence/tests/test_openai_structured.py` covers input
validation, fixed request shape, response normalization (completed /
refusal / incomplete / unrecognized status), and every sanitized error
category, using an injected fake SDK client (`sdk_client=`) that records
call kwargs and returns/raises canned results — no real `openai.OpenAI`
client is ever constructed and no network call is ever made. It also (added
2026-08-24) covers each error class's `category` attribute, proves
`OpenAIRequestSchemaError` is raised offline with zero SDK calls for a
schema-incompatible `output_model`, and includes three regression tests
using the real, production `MarketEvidenceModelAnalysis` schema from
`market_intelligence/agents/market_evidence_agent.py`: that the real schema
passes this module's own production schema preflight (public Pydantic API
only — no private/underscore-prefixed OpenAI SDK module is imported by
production code or by this test), that a synthetic
schema-and-bound-conformant response round-trips through `generate()`
unchanged, and that a synthetic response violating one of the schema's
Pydantic-only length bounds reproduces the same sanitized error class,
category, and message this client raises for `response_validation_failed`
in general — a plausible, locally reproducible failure signature consistent
with the 2026-08-24 live failure (see "Known structured-output validation
failure" above), not proof of that attempt's exact cause — entirely
offline, no network, no credentials.
`market_intelligence/tests/test_settings.py` covers the three new settings
fields' defaults and bounds. **As of 2026-08-23, no live OpenAI connectivity
check existed in this branch** (unlike the Alpaca/FRED connectors, which
each have a `check_connection` method and a companion script) — that
remained separate, future, and not yet authorized. **This has since been
superseded: see "First authorized live connectivity check (2026-08-24)"
below.** That check used the existing `generate()` method directly, with a
minimal fixed instructions string and a minimal evidence dict — no dedicated
`check_connection` method or companion script has been added to this
module, so this remains the only way to exercise live connectivity.

## First authorized live connectivity check (2026-08-24)

On 2026-08-24, a separately authorized, minimal live connectivity check was
run against the real OpenAI API using the existing `OpenAIStructuredClient`
and real local `.env` credentials — no code, test, configuration, `.env`, or
migration was changed to run it, and no further live request has been made
since. The request used fixed, minimal developer instructions and an
evidence dict containing only `{"test_type": "provider_connectivity",
"contains_market_data": false}`. No market data, news, credentials, prompts
from providers, predictions, recommendations, or agent analysis were sent in
the request or produced in the response.

Sanitized results recorded here (per the sanitization contract in
"Sanitized error categories"/`StructuredOutputResult` above, no raw
provider output, response ID value, or credential is reproduced):

- `configured=True`
- connection outcome: `status="completed"`
- `model="gpt-5-mini"`
- parsed structured output present: `True`
- a sanitized `response_id` matching OpenAI's bounded `resp_...` ID shape
  was returned (present, but the value itself is not reproduced here)
- `input_tokens=143`, `output_tokens=63`, `total_tokens=206`

**This confirms only that the existing OpenAI provider boundary can reach
the OpenAI API, authenticate with the configured API key, and receive and
parse one minimal structured-output response end to end.** It does not
confirm anything about model output quality, latency under load, rate-limit
behavior, cost at scale, or any agent, forecast, recommendation, or
market-analysis capability — none of that was exercised by this check, and
no such capability exists in this branch.

## Known structured-output validation failure (Market Evidence Agent, 2026-08-24)

On 2026-08-24, the first authorized live `--execute` request made through
`MarketEvidenceAgent` (a caller of this module, not this module itself; see
[docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md)) failed with
`OpenAIParseFailureError` ("OpenAI response failed structured-output
validation.", category `response_validation_failed`). Offline diagnosis (no
further live request made) confirmed: this category can only occur after a
request was actually sent and a response was received (see above), and the
real `MarketEvidenceModelAnalysis` schema itself passes this module's own
production schema preflight (`_validate_output_model`, public Pydantic API
only — see "Testing" below); it is not structurally incompatible. Because
this client never captures or logs raw model output, the exact response
field/value that failed re-validation in that one attempt is unavailable and
cannot be proven from local evidence alone. Exceeding one of the schema's
Pydantic-only `minLength`/`maxLength`/`minItems`/`maxItems` bounds is one
plausible, locally reproducible failure mode, reproduced offline in
`market_intelligence/tests/test_openai_structured.py` — **not** an
established proven cause of that specific live attempt. OpenAI has not
published official documentation establishing that its Structured Outputs
generation leaves these bound keywords unenforced specifically for the
non-fine-tuned `gpt-5-mini` model this project uses; that remains a
candidate explanation, not a documented fact. No bound, citation check, or
output-policy check was weakened in response. Two mitigations were made
instead: (1) this module's sanitized failure classification (`category`,
the new `OpenAIRequestSchemaError` — using only public Pydantic API, with no
production dependency on any private OpenAI SDK module, per the "Sanitized
failure classification" section above) plus regression tests using the real
`MarketEvidenceModelAnalysis` schema
(`market_intelligence/tests/test_openai_structured.py`) that reproduce this
plausible failure signature entirely offline; and (2) conservative advisory
output-length/count budgets, with margin below every corresponding hard
Pydantic maximum, added to `MarketEvidenceAgent`'s `AGENT_INSTRUCTIONS` (see
[docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md)) to reduce the
likelihood of a real model response landing close to -- or over -- one of
those hard bounds. **These budgets reduce that plausible risk; they do not
guarantee that any future request will pass validation** — the model is not
required to follow instruction-level guidance, and no bound itself was
changed. See `PROJECT_STATE.md` for the full, dated record.

## Known limitations

- **As of 2026-08-23:** no live request or connectivity check had been made
  against the real OpenAI API from this branch. **Superseded 2026-08-24 —
  see "First authorized live connectivity check (2026-08-24)" above.** That
  check confirmed connectivity and response normalization only, not scale,
  cost, latency, or any agent/analysis capability.
- No agent prompt, market bias, forecast, recommendation, or brokerage
  integration exists here — this is provider-boundary infrastructure only.
- `max_retries=0` is fixed on the constructed SDK client, so a request
  either completes within the configured timeout or fails once — there is
  no automatic retry.
- This module supports exactly one request shape (one fixed developer
  instruction string, one evidence dict, one Pydantic output model per
  call). Batch requests, streaming, and multi-turn conversations are out of
  scope.
