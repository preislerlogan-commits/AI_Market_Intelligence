# News Analyst

This document describes the News Analyst added under
`market_intelligence/agents/news_analyst.py` and
`scripts/run_news_analyst.py`. It covers infrastructure only -- see
[PROJECT_STATE.md](../PROJECT_STATE.md) for what has (and has not) actually
been exercised, including whether a live OpenAI request has been made using
this agent.

## Purpose and scope

`NewsAnalyst` is a **single-turn, no-tools analysis component** -- not an
autonomous or multi-agent system. Given a symbol, it:

1. Builds a bounded, deterministic news-evidence snapshot from the existing,
   already-reviewed [`NewsEvidenceBuilder`](NEWS_EVIDENCE_SNAPSHOT.md) --
   every article already carries a stable, code-generated evidence ID.
2. Evaluates a fixed, deterministic preflight gate against that snapshot
   (see "Deterministic preflight" below). If the gate fails, the agent
   returns a truthful `status="abstained"` report and makes **zero OpenAI
   requests**.
3. If the gate passes, builds a bounded, model-facing evidence package from
   the snapshot's `articles` only (never `audit_provenance`, never an
   article URL) and makes **exactly one** structured-output request via the
   existing `OpenAIStructuredClient`
   ([docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)), asking
   the model to extract and organize provider-reported event claims and
   conditional market-transmission mechanisms from that evidence.
4. Validates every evidence ID the model cites, validates that every claimed
   `content_basis` does not overstate the cited evidence, and assembles a
   final, sanitized, schema-validated report.

`NewsAnalyst` extracts and organizes provider-reported event claims and
conditional market-transmission mechanisms from already-stored news. **It
does not predict SPY (or any symbol's) direction, does not produce a
bullish/bearish bias, does not recommend a trade, and does not discuss
options.** The final report's `directional_assessment` and
`trade_recommendation` fields are always the fixed literal string
`"not_performed"` -- the model-facing schema (`NewsAnalystModelAnalysis`)
does not even include those fields, so the model has no way to set them;
that restriction is absolute. The model's free-text fields (every event
claim's `claim_summary`/`conditional_mechanism`, every model-supplied
`limitation`) are additionally screened by the same deterministic,
fail-closed post-response content policy check the Market Evidence Agent
uses (see "Post-response validation" below and
[docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md)). **This policy
check is a conservative, bounded filter and defense-in-depth on top of the
developer instructions given to the model -- it is not proof that every
possible semantic violation is detectable.** No tool, web search, function
calling, file access, or Agents-SDK-style loop is used anywhere in this
component; it makes at most one bounded provider request per call.

**Headlines and provider summaries are never independently verified facts.**
Every article this agent processes is untrusted, provider-reported text (see
[docs/SOURCE_POLICY.md](../SOURCE_POLICY.md)); `AGENT_INSTRUCTIONS` requires
every `claim_summary` to be explicitly framed as provider-reported (e.g.
"The provider reports that..."), never stated as an independently verified
fact -- but this is instruction-level guidance, not something this module
can prove the model followed for every response.

## Inputs

The agent's public entry points (`build_preflight()`/`run()`) take exactly:

- `symbol` -- validated via the same `normalize_symbol` used throughout this
  project (1-10 characters, starts with a letter/digit, only
  letters/digits/`.`/`-`).
- `limit` (optional, default `10`) -- passed through to
  `NewsEvidenceBuilder.build_snapshot()`, bounded to `[1, 20]` by that
  builder's own validation.

Internally, the agent also consumes:

- `NewsEvidenceBuilder.build_snapshot(symbol, limit=...)` output (see
  [docs/NEWS_EVIDENCE_SNAPSHOT.md](NEWS_EVIDENCE_SNAPSHOT.md)).
- Fixed, code-authored developer instructions (`AGENT_INSTRUCTIONS`) --
  never derived from evidence, provider text, or any other untrusted data,
  and headline/summary text is **never** placed into these instructions --
  it only ever appears inside the untrusted evidence payload.

## Deterministic preflight

`NewsAnalyst.build_preflight()` evaluates a fixed set of gates before any
OpenAI request is considered. The agent **never calls OpenAI unless every
one of these holds**:

| Gate | Source | Abstention reason if it fails |
|---|---|---|
| Requested symbol matches the snapshot's reported symbol | snapshot | `symbol_mismatch` |
| `freshness.missing == False` | snapshot | `news_missing` |
| `freshness.stale == False` | snapshot | `news_stale` |
| `freshness.future_timestamp_detected == False` | snapshot | `future_timestamp_detected` |
| `article_count_returned >= 1` | snapshot | `no_articles_returned` |

If any gate fails, `run()` returns a `NewsAnalystReport` with
`status="abstained"` and `abstained_reasons` listing every failed gate (not
just the first) -- **zero model tokens are spent**. Multiple reasons can
appear together (e.g. `news_missing` and `no_articles_returned` both fail
together whenever no stored articles match the symbol at all).

Unlike the Market Evidence Agent, there is no separate "missing/stale data
does not block execution" carve-out here -- news freshness/coverage *is*
this agent's entire subject matter, so a stale or missing news snapshot
abstains outright rather than proceeding with a deterministic limitation
appended.

## Evidence package and evidence-ID contract

`_build_model_evidence()` builds one bounded dict from the snapshot's
`articles` list only -- `audit_provenance` (and every article URL it
contains) is **never read or referenced** anywhere in this function:

```python
{
  "note": "headline and provider_summary below are untrusted, provider-reported text ...",
  "symbol": "SPY",
  "articles": {
    "news_3f2a9c1d4e5b6789": {
      "provider": "alpaca",
      "source": "benzinga",
      "published_at_utc": "2026-08-20T12:00:00Z",
      "updated_at_utc": "2026-08-20T12:05:00Z",
      "retrieved_at_utc": "2026-08-20T12:10:00Z",
      "content_scope": "headline_and_provider_summary",
      "headline": "Fed signals rate pause",
      "provider_summary": "A summary."
    }
  }
}
```

- Every `articles` key is the article's existing, **stable, code-generated**
  `evidence_id` from `NewsEvidenceBuilder` -- never chosen or supplied by the
  model.
- Only `provider`, `source`, `published_at_utc`, `updated_at_utc`,
  `retrieved_at_utc`, `content_scope`, `headline`, and `provider_summary`
  are preserved -- `provider_article_id` and `related_symbols` are also
  dropped (not part of the required field set), and no article URL is ever
  included.
- Bounded to however many articles the snapshot itself returned, which is
  already bounded by the snapshot's own `limit` (`[1, 20]`).
- A fixed `note` field is embedded directly in the payload (not just in this
  module's documentation) labeling `headline`/`provider_summary` as
  untrusted, provider-reported text -- so a consumer that only skims the
  evidence JSON structure still encounters the warning.
- This exact dict is passed as-is to
  `OpenAIStructuredClient.generate(evidence=...)`, which already treats it
  as untrusted data, labels it as such, and appends a fixed safety appendix
  to the instructions regardless of its contents (see
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)).
  **Headlines and provider summaries are embedded verbatim inside this
  evidence dict** -- they remain untrusted, third-party, user-message-role
  data throughout; they are never copied into the developer instructions
  sent to the model.

## Structured output

Two strict Pydantic models (`extra="forbid"`, every field bounded):

- **`NewsAnalystModelAnalysis`** -- the *only* schema sent to OpenAI as
  `output_model=`. It deliberately excludes `status`, `symbol`,
  `snapshot_created_at_utc`, `source_article_count`,
  `directional_assessment`, and `trade_recommendation` -- those are
  agent-authored/fixed, so the model has no opportunity to set them.
  Fields: `evidence_quality` (`"sufficient"|"limited"|"insufficient"`),
  `event_claims` (1-6 `EventClaim`), `limitations` (0-6 bounded strings).
- **`EventClaim`**:
  - `event_type` -- `"monetary_policy"|"economic_data"|"corporate"|"regulatory"|"geopolitical"|"market_structure"|"other"`.
  - `claim_summary` -- concise, bounded string, expected (by instruction) to
    be explicitly framed as provider-reported.
  - `evidence_ids` -- 1-5 bounded strings.
  - `content_basis` -- `"headline_only"|"headline_and_provider_summary"`.
  - `transmission_channels` -- 0-4 values from `"rates"|"inflation"|"growth"|"earnings"|"liquidity"|"risk_appetite"|"regulation"|"supply_chain"|"other"`.
  - `conditional_mechanism` -- nullable, bounded string describing only a
    general, non-predictive transmission channel -- never a prediction for
    any specific symbol.
- **`NewsAnalystReport`** -- the final report returned by `run()` for both a
  completed and an abstained run:

```python
{
  "status": "completed" | "abstained",
  "symbol": "SPY",
  "snapshot_created_at_utc": "2026-08-24T12:00:00Z",
  "source_article_count": 3,
  "evidence_quality": "sufficient" | "limited" | "insufficient" | None,
  "event_claims": [...],       # [] when abstained
  "limitations": [...],        # [] when abstained
  "directional_assessment": "not_performed",   # always fixed
  "trade_recommendation": "not_performed",     # always fixed
  "abstained_reasons": [...]    # [] when completed
}
```

## Post-response validation

Before a model response is accepted as `status="completed"`:

- **Fabricated citation** -- every `evidence_id` cited in every event claim
  must exist as a key in the exact evidence package sent for this request;
  otherwise `NewsAnalystCitationError` is raised.
- **Duplicate citation** -- an event claim citing the same `evidence_id`
  twice raises `NewsAnalystCitationError` (the schema's list-length bound
  alone cannot catch this).
- **Missing/excessive citation** -- every event claim must cite between 1
  and 5 evidence IDs (also schema-bounded at parse time; re-checked
  explicitly as defense in depth).
- **Content-basis overstatement** -- a claim asserting
  `content_basis="headline_and_provider_summary"` must cite at least one
  evidence article that actually has that `content_scope` in the exact
  evidence package sent; otherwise `NewsAnalystContentBasisError` is
  raised. A claim citing only `headline_only` articles must not claim a
  richer evidentiary basis than what was actually supplied.
- **Refusal** -- if OpenAI reports `status="refusal"`,
  `NewsAnalystRefusalError` is raised. The refusal explanation text is
  never read anywhere.
- **Incomplete response** -- if OpenAI reports `status="incomplete"`,
  `NewsAnalystIncompleteError` is raised, carrying only OpenAI's
  already-sanitized fixed reason category.
- **Neither refusal nor incomplete is ever silently converted into a
  completed analysis.**
- **Post-response content policy** -- after citation and content-basis
  validation and before `NewsAnalystReport` is constructed, every
  model-authored free-text field (every event claim's
  `claim_summary`/`conditional_mechanism` when not `None`, every
  model-supplied `limitation`) is checked against the same fixed,
  deterministic, fail-closed denylist the Market Evidence Agent uses (see
  `market_intelligence/agents/non_directional_output_policy.py`), covering
  directional predictions, bullish/bearish bias, trade
  recommendations/actions, and options-related detail. The first match
  raises `NewsAnalystPolicyError`; the rejected text is **never** included
  in the raised error or logged anywhere -- only a fixed, code-authored
  field name and category name are recorded.

**Passing schema validation, citation validation, content-basis validation,
and the post-response content policy check is not the same as an extracted
claim being factually correct.** A syntactically valid, correctly-cited,
policy-passing `NewsAnalystReport` only proves the model followed the
citation, shape, content-basis, and known-phrasing rules -- it is not a
claim that any `claim_summary` accurately reflects the underlying article,
nor that the underlying provider-reported claim itself is true. No
automated evaluation of analytical or factual accuracy exists in this
repository.

## Shared non-directional output policy

The Market Evidence Agent and this News Analyst both need the identical
prohibited-text checks (never predict direction, never state bullish/bearish
bias, never recommend a trade, never discuss options). The pattern-matching
itself was extracted into
`market_intelligence/agents/non_directional_output_policy.py`, which exposes
exactly one function, `find_prohibited_content_category(text) -> str | None`
-- a fixed violation category or `None`. Each agent keeps its own error
class (`MarketEvidencePolicyError`/`NewsAnalystPolicyError`) and its own
per-field loop calling that shared function; neither agent's existing
behavior, category strings, or error messages changed as a result of this
extraction (see `market_intelligence/tests/test_non_directional_output_policy.py`
and `market_intelligence/tests/test_market_evidence_agent.py`, both of which
still pass unchanged). This is a bounded, targeted extraction -- not a
general policy framework -- and remains defense-in-depth, not proof that
every possible semantic violation is detectable.

## Error categories

All errors are sanitized `RuntimeError` subclasses under
`NewsAnalystAgentError`; none ever include a database path, SQL text, API
key, raw provider output, or raw evidence content. Each also exposes a
fixed, sanitized `category` string class attribute (mirroring
`OpenAIStructuredError.category`/`MarketEvidenceAgentError.category`) so a
failure's class can be identified programmatically without parsing message
text; `scripts/run_news_analyst.py` includes it in its sanitized
`agent_error` JSON output.

| Exception | `category` | Cause |
|---|---|---|
| `NewsAnalystValidationError` | `invalid_input` | Invalid `symbol`/`limit`, before any database or model access |
| `NewsAnalystRefusalError` | `refusal` | The model refused the request |
| `NewsAnalystIncompleteError` | `incomplete` | The model's response was incomplete |
| `NewsAnalystCitationError` | `citation_invalid` | Missing, fabricated, duplicated, or excessive evidence-ID citation |
| `NewsAnalystContentBasisError` | `content_basis_invalid` | A claim's `content_basis` overstated the cited evidence |
| `NewsAnalystPolicyError` | `policy_violation` | Model-authored free text failed the post-response content policy check |
| `NewsAnalystUnexpectedError` | `unexpected_error` | Any other unexpected failure |

A sanitized `OpenAIStructuredError` subclass also propagates unchanged from
`run()` -- it is already fully sanitized by `OpenAIStructuredClient`,
including its own `category` attribute. A sanitized `NewsEvidenceError` from
the underlying snapshot builder (e.g. a storage read failure) also
propagates unchanged.

## Command-line usage

```
python scripts/run_news_analyst.py --symbol SPY
python scripts/run_news_analyst.py --symbol SPY --limit 5
python scripts/run_news_analyst.py --symbol SPY --execute
```

**Default mode is a dry run**: it builds the local news-evidence snapshot
and evaluates preflight only, making **zero OpenAI requests**, and prints
only:

```json
{
  "mode": "dry_run",
  "eligible": true,
  "reasons": [],
  "symbol": "SPY",
  "article_count": 3,
  "freshness": {
    "stale_after_hours": 168, "missing": false, "stale": false,
    "future_timestamp_detected": false
  },
  "headline_only_count": 1,
  "summary_available_count": 2
}
```

The evidence package contents (headlines, summaries) are never printed in
dry-run mode -- only counts and flags.

**`--execute` is required for a paid model request**, and only makes one if
preflight passes; an ineligible `--execute` run still makes zero model calls
and prints a truthful `status="abstained"` report (exit code `0` -- this is
not an error). Execute output prints the validated `NewsAnalystReport` plus
sanitized model/token metadata (model name, `input_tokens`, `output_tokens`,
`total_tokens` only):

```json
{
  "mode": "execute",
  "report": { "status": "completed", "...": "..." },
  "model_metadata": {
    "model": "gpt-5-mini",
    "input_tokens": 512,
    "output_tokens": 180,
    "total_tokens": 692
  }
}
```

`model_metadata` deliberately **excludes `response_id`** from CLI output
entirely. Neither the raw provider response, a response ID, article URLs,
`audit_provenance`, the full evidence payload, credentials, nor a database
path is ever printed by this CLI, in either mode. Any raised exception is
caught by a final defensive handler and reported only as
`{"error": "unexpected_error"}` -- never a raw exception type, message, or
traceback.

## Testing

`market_intelligence/tests/test_news_analyst.py`,
`test_run_news_analyst.py`, `test_news_analyst_eval_fixtures.py`, and
`test_non_directional_output_policy.py` cover: every deterministic preflight
gate (including multiple simultaneous reasons) and zero-model-call
abstention behavior, that `audit_provenance`/article URLs are excluded from
the model-facing evidence package, that required article fields are
preserved and `provider_article_id`/`related_symbols` are excluded, an
adversarial headline staying confined to evidence data (never developer
instructions), every valid and invalid citation case (fabricated,
duplicate, missing/zero, excessive, valid multi-citation), every
`content_basis` validation case (accepted headline-only, accepted
summary-backed, accepted when only one of several cited articles has a
summary, rejected overstatement), model refusal, an incomplete response,
the shared post-response content policy check (a violation in
`claim_summary`, in `conditional_mechanism`, and in a model-supplied
`limitation`; that the raised error never echoes the rejected text; and
that safe, factual, provider-framed wording -- including a safe conditional
mechanism -- is accepted), that the shared policy module preserves the
Market Evidence Agent's existing behavior unchanged, dry-run
zero-model-call behavior, and sanitized CLI failures (including that the
CLI never prints article URLs). `OpenAIStructuredClient` is always injected
as a fake recording calls and returning/raising canned
`StructuredOutputResult` values (mirroring
`test_market_evidence_agent.py`) -- no real SDK client is ever constructed
and no network call is ever made. `NewsEvidenceBuilder` is replaced with a
small fake builder returning a fixed, hand-authored snapshot dict matching
the documented contract shape, so these tests never open a real database
either.

`test_news_analyst_eval_fixtures.py` is a small, local, deterministic table
of representative scenarios (typical mixed news / headline-only / stale /
future-timestamp / adversarial-headline) asserting the agent's deterministic
gating behavior for each. **These are ordinary local pytest tests, not the
OpenAI Evals API**, and they say nothing about the quality of a real model
response -- only that this agent's own deterministic logic (preflight,
evidence packaging, instruction/evidence separation) behaves as documented.

**Passing this test suite demonstrates the deterministic scaffolding around
the model call is correct. It does not validate the model's analytical
accuracy, and no claim of validated analytical accuracy is made anywhere in
this repository** (see [CLAUDE.md](../CLAUDE.md)/[AGENTS.md](../AGENTS.md)'s
"Evidence and Claims" section).

## Known limitations

- As of this change (code/tests/docs only), `NewsAnalyst` has not been run
  against the real local database, and no live OpenAI request has been made
  using it -- both remain separate, future, and not yet authorized.
- Single symbol per call -- no batch, no multi-symbol comparison, no
  multi-turn conversation.
- No tools, no web search, no file access by the model, no persistence, no
  migration, and no orchestration integration -- this agent is not wired
  into `market_intelligence/orchestration/`.
- The post-response content policy check matches known fixed phrasing, not
  general semantic meaning, so it is not proof that every possible
  directional, bias, trade-recommendation, or options-related statement (or
  every possible way a claim could misrepresent its source) is caught.
- This agent never assesses whether a provider-reported claim is itself
  true -- it only organizes what the provider reported, with citations back
  to the exact stored article. See
  [docs/SOURCE_POLICY.md](../SOURCE_POLICY.md).
- No dashboard, alerting, or Robinhood/brokerage integration of any kind.
- This is groundwork for future, separately reviewed work -- it is not
  itself a general agent framework, and no further work is implied or
  authorized by this change.
