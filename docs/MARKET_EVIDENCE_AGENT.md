# Market Evidence Agent

This document describes the Market Evidence Agent added under
`market_intelligence/agents/market_evidence_agent.py` and
`scripts/run_market_evidence_agent.py`. It covers infrastructure only -- see
[PROJECT_STATE.md](../PROJECT_STATE.md) for what has (and has not) actually
been exercised, including whether a live OpenAI request has been made using
this agent.

## Purpose and scope

`MarketEvidenceAgent` is a **single-turn, no-tools analysis component** --
not an autonomous or multi-agent system. Given a symbol and an optional
session date, it:

1. Builds a bounded, deterministic evidence package from two existing,
   already-reviewed read-only builders --
   [`MarketContextBuilder`](MARKET_CONTEXT_SNAPSHOT.md) and
   [`SessionQualityBuilder`](SESSION_QUALITY.md) -- assigning every fact a
   stable, code-generated evidence ID.
2. Evaluates a fixed, deterministic preflight gate against that evidence
   (see "Deterministic preflight" below). If the gate fails, the agent
   returns a truthful `status="abstained"` report and makes **zero OpenAI
   requests**.
3. If the gate passes, makes **exactly one** structured-output request via
   the existing `OpenAIStructuredClient`
   ([docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)), asking
   the model to summarize and organize the evidence package -- and only the
   evidence package.
4. Validates every evidence ID the model cites against the exact evidence
   package sent, and assembles a final, sanitized, schema-validated report.

**This agent never predicts market direction, never recommends a trade, and
never discusses option strikes or contracts.** The final report's
`directional_assessment` and `trade_recommendation` fields are always the
fixed literal string `"not_performed"` -- the model-facing schema
(`MarketEvidenceModelAnalysis`) does not even include those fields, so the
model has no way to set them. No tool, web search, function calling, file
access, or Agents-SDK-style loop is used anywhere in this component; it
makes at most one bounded provider request per call.

## Inputs

The agent's public entry points (`build_preflight()`/`run()`) take exactly:

- `symbol` -- validated via the same `normalize_symbol` used throughout this
  project (1-10 characters, starts with a letter/digit, only
  letters/digits/`.`/`-`).
- `session_date` (optional) -- validated via the same `normalize_session_date`
  used by `SessionQualityBuilder` (`YYYY-MM-DD` or omitted to auto-select the
  most recent stored date with at least one regular-session bar).

Both are validated **before** either builder is called (so a malformed
symbol/date never reaches storage), and again by each builder internally
(defense in depth). Internally, the agent also consumes:

- `MarketContextBuilder.build_snapshot(symbol)` output (default limits --
  `recent_bars_limit`, `recent_news_limit`, `macro_series_ids` are not
  exposed as agent inputs; this is a deliberately narrow agent, not a
  general feature consumer).
- `SessionQualityBuilder.build_report(symbol, session_date=...)` output.
- Fixed, code-authored developer instructions (`AGENT_INSTRUCTIONS`) --
  never derived from evidence, provider text, or any other untrusted data.

## Deterministic preflight

`MarketEvidenceAgent.build_preflight()` evaluates a fixed set of gates
before any OpenAI request is considered. The agent **never calls OpenAI
unless every one of these holds**:

| Gate | Source | Abstention reason if it fails |
|---|---|---|
| Requested symbol matches both builders' reported symbol | both | `symbol_mismatch` |
| `flags.bars_missing == False` | `MarketContextBuilder` | `bars_missing` |
| `flags.bars_stale == False` | `MarketContextBuilder` | `bars_stale` |
| `completeness.complete == True` | `SessionQualityBuilder` | `session_incomplete` |
| `completeness.partial_session == False` | `SessionQualityBuilder` | `partial_session` |
| `completeness.missing_data == False` | `SessionQualityBuilder` | `missing_data` |
| `completeness.unexpected_or_duplicate_timestamps_utc` is empty | `SessionQualityBuilder` | `unexpected_or_duplicate_timestamps` |

If any gate fails, `run()` returns a `MarketEvidenceReport` with
`status="abstained"` and `abstained_reasons` listing every failed gate (not
just the first) -- **zero model tokens are spent**. Multiple reasons can
appear together (e.g. `missing_data` and `session_incomplete` both fail
together whenever no regular-session bars exist at all).

**Missing or stale news/macro data does not block execution.** Those
conditions are deliberately excluded from the gate above; instead, when the
agent does proceed to call the model, it appends a fixed, deterministic
limitation sentence for each such condition (e.g. "No stored news articles
are available for this symbol.", "Stored macro observation for series
FEDFUNDS is stale.") to the final report's `limitations`, ahead of any
limitation the model itself supplies, capped at `MAX_LIMITATIONS` (6) with
duplicates removed.

**Known caveat:** `MarketContextBuilder` selects whichever stored bar
*identity* (provider/timeframe/feed/adjustment/currency) has the most recent
bar for the symbol -- which is not necessarily the `5Min` identity that
`SessionQualityBuilder` is fixed to. If a symbol has bars stored under more
than one timeframe, `bars_missing`/`bars_stale` could in principle reflect a
different identity than the one `completeness` describes. This project
currently ingests exactly one bars identity per symbol
(`market_intelligence/orchestration/jobs.json`), so this has not been
observed in practice, but it is not structurally prevented by either
builder or by this agent.

## Evidence package and evidence-ID contract

`_build_evidence_package()` builds one bounded dict:

```python
{
  "symbol": "SPY",
  "session_date_et": "2026-08-19",
  "facts": {
    "bars_latest": {"category": "price", "statement": "..."},
    "bars_provenance": {"category": "data_quality", "statement": "..."},
    "bars_coverage": {"category": "data_quality", "statement": "..."},
    "bars_return": {"category": "price", "statement": "..."},
    "bars_recent_0": {"category": "price", "statement": "..."},
    "...": "...",
    "news_0": {"category": "news", "statement": "..."},
    "news_coverage": {"category": "data_quality", "statement": "..."},
    "macro_FEDFUNDS": {"category": "macro", "statement": "..."},
    "session_completeness": {"category": "data_quality", "statement": "..."},
    "session_regular": {"category": "price", "statement": "..."},
    "session_volume": {"category": "volume", "statement": "..."},
    "session_same_date_bars": {"category": "data_quality", "statement": "..."}
  }
}
```

- Every `facts` key is a **stable, code-generated evidence ID** (fixed
  prefixes like `bars_recent_{i}`, `news_{i}`, `macro_{series_id}`) --
  never chosen or supplied by the model, and never derived from provider
  text.
- A fact is only added when the underlying value is actually present (e.g.
  no `bars_return` entry when `short_return` is `null`), so the model is
  never given a fact to cite that doesn't correspond to real stored data.
- This exact dict is passed as-is to `OpenAIStructuredClient.generate(evidence=...)`,
  which already treats it as untrusted data, labels it as such, and appends
  a fixed safety appendix to the instructions regardless of its contents
  (see [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)).
  **News headlines are embedded verbatim inside this evidence dict** -- they
  remain untrusted, third-party, user-message-role data throughout; they are
  never copied into the developer instructions sent to the model.
- The evidence package never contains a database path, SQL text, credential,
  or any ingestion-internal detail -- only values already produced by
  `MarketContextBuilder`/`SessionQualityBuilder`'s own sanitized output.

## Structured output

Two strict Pydantic models (`extra="forbid"`, every field bounded):

- **`MarketEvidenceModelAnalysis`** -- the *only* schema sent to OpenAI as
  `output_model=`. It deliberately excludes `status`, `symbol`,
  `session_date_et`, `directional_assessment`, and `trade_recommendation` --
  those are agent-authored/fixed, so the model has no opportunity to set
  them. Fields: `evidence_quality` (`"sufficient"|"limited"|"insufficient"`),
  `evidence_summary` (bounded string), `observations` (1-6
  `EvidenceObservation`), `limitations` (0-6 bounded strings).
- **`EvidenceObservation`**: `category`
  (`"data_quality"|"price"|"volume"|"news"|"macro"`), `statement` (bounded
  string), `evidence_ids` (1-5 bounded strings).
- **`MarketEvidenceReport`** -- the final report returned by `run()` for both
  a completed and an abstained run:

```python
{
  "status": "completed" | "abstained",
  "symbol": "SPY",
  "session_date_et": "2026-08-19" | None,
  "evidence_quality": "sufficient" | "limited" | "insufficient" | None,
  "evidence_summary": "..." | None,
  "observations": [...],       # [] when abstained
  "limitations": [...],        # [] when abstained
  "directional_assessment": "not_performed",   # always fixed
  "trade_recommendation": "not_performed",     # always fixed
  "abstained_reasons": [...]    # [] when completed
}
```

## Post-response validation

Before a model response is accepted as `status="completed"`:

- **Fabricated citation** -- every `evidence_id` cited in every observation
  must exist as a key in the exact evidence package sent for this request;
  otherwise `MarketEvidenceCitationError` is raised.
- **Duplicate citation** -- an observation citing the same `evidence_id`
  twice raises `MarketEvidenceCitationError` (the schema's list-length bound
  alone cannot catch this, since two identical IDs still satisfy it).
- **Missing/excessive citation** -- every observation must cite between 1
  and 5 evidence IDs (also schema-bounded at parse time; re-checked
  explicitly as defense in depth).
- **Refusal** -- if OpenAI reports `status="refusal"`,
  `MarketEvidenceRefusalError` is raised. The refusal explanation text is
  never read anywhere (this is already true of `OpenAIStructuredClient`
  itself).
- **Incomplete response** -- if OpenAI reports `status="incomplete"` (e.g.
  `max_output_tokens` reached), `MarketEvidenceIncompleteError` is raised,
  carrying only OpenAI's already-sanitized fixed reason category
  (`"max_output_tokens"`, `"content_filter"`, or `"other"`).
- **Neither refusal nor incomplete is ever silently converted into a
  completed analysis.** `run()` raises for both rather than returning a
  `MarketEvidenceReport` claiming `status="completed"`.

**Passing schema validation and citation validation is not the same as the
analysis being factually correct.** A syntactically valid, correctly-cited
`MarketEvidenceReport` only proves the model followed the citation and shape
rules -- it is not a claim that the model's `evidence_summary` or
`observations` correctly characterize the underlying stored data. No
automated evaluation of analytical accuracy exists in this repository.

## Error categories

All errors are sanitized `RuntimeError` subclasses under
`MarketEvidenceAgentError`; none ever include a database path, SQL text, API
key, raw provider output, or raw evidence content.

| Exception | Cause |
|---|---|
| `MarketEvidenceValidationError` | Invalid `symbol`/`session_date`, before any database or model access |
| `MarketEvidenceRefusalError` | The model refused the request |
| `MarketEvidenceIncompleteError` | The model's response was incomplete |
| `MarketEvidenceCitationError` | Missing, fabricated, duplicated, or excessive evidence-ID citation |
| `MarketEvidenceUnexpectedError` | Any other unexpected failure |

A sanitized `OpenAIStructuredError` subclass (config missing, timeout,
connection failure, rate limit, authentication failure, parse failure) also
propagates unchanged from `run()` -- it is already fully sanitized by
`OpenAIStructuredClient`.

## Command-line usage

```
python scripts/run_market_evidence_agent.py --symbol SPY
python scripts/run_market_evidence_agent.py --symbol SPY --session-date 2026-08-19
python scripts/run_market_evidence_agent.py --symbol SPY --execute
```

**Default mode is a dry run**: it builds the evidence package and evaluates
preflight only, making **zero OpenAI requests**, and prints only:

```json
{
  "mode": "dry_run",
  "eligible": true,
  "reasons": [],
  "symbol": "SPY",
  "session_date_et": "2026-08-19",
  "evidence_item_count": 13,
  "flags": {
    "bars_missing": false, "bars_stale": false,
    "session_complete": true, "partial_session": false, "missing_data": false,
    "unexpected_or_duplicate_timestamps_count": 0,
    "news_missing": false, "news_stale": false,
    "macro_missing_series": [], "macro_stale_series": []
  }
}
```

The evidence package contents themselves are never printed in dry-run mode
-- only the item count.

**`--execute` is required for a paid model request**, and only makes one if
preflight passes; an ineligible `--execute` run still makes zero model calls
and prints a truthful `status="abstained"` report (exit code `0` -- this is
not an error). Execute output prints the validated `MarketEvidenceReport`
plus sanitized model/token metadata (model name, `input_tokens`,
`output_tokens`, `total_tokens` only):

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
entirely (unlike the connectivity-check record in
[docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md), which notes
a sanitized `response_id` was present but does not reproduce it either) --
neither the raw provider response, a response ID, the full evidence
payload, credentials, nor a database path is ever printed by this CLI, in
either mode.

## Testing

`market_intelligence/tests/test_market_evidence_agent.py`,
`test_run_market_evidence_agent.py`, and
`test_market_evidence_agent_eval_fixtures.py` cover: eligible execution,
every deterministic abstention reason (including `symbol_mismatch`),
execution proceeding despite missing/stale news or macro data (with the
resulting limitation), model refusal, an incomplete response, fabricated
citations, duplicate citations, excessive/zero citations, an
injection-shaped adversarial headline staying confined to evidence data,
sanitized error leakage (no secret/traceback/exception-type ever printed),
dry-run zero-provider-call behavior, and `--execute` gating. `OpenAIStructuredClient`
is always injected as a fake recording calls and returning/raising canned
`StructuredOutputResult` values (mirroring `test_openai_structured.py`) --
no real SDK client is ever constructed and no network call is ever made.
`MarketContextBuilder`/`SessionQualityBuilder` are replaced with small fake
builders returning fixed, hand-authored dicts matching the documented
contract shape, so these tests never open a real database either.

The eval fixture file is a small, local, deterministic table of
representative scenarios (typical / missing-data / stale-data /
partial-session / adversarial-headline) asserting the agent's deterministic
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

- Single symbol, single optional session date per call -- no batch, no
  multi-symbol comparison, no multi-turn conversation.
- No tools, no web search, no file access by the model, no persistence, no
  migration, and no orchestration integration -- this agent is not wired
  into `market_intelligence/orchestration/`.
- The bars-identity mismatch caveat described under "Deterministic
  preflight" above.
- No dashboard, alerting, or Robinhood/brokerage integration of any kind.
- This is groundwork for future, separately reviewed work (e.g. an
  orchestrated agent, a Macro Analyst agent) -- it is not itself a general
  agent framework, and none of that further work is implied or authorized
  by this change.
