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

**A second, distinct kind of abstention (added 2026-08-24) can occur *after*
this preflight passes and a model response is received** -- see
"All-irrelevant evidence path" below. That outcome uses its own fixed
reason, `"no_sufficiently_relevant_articles"`, which is never one of the
`PREFLIGHT_REASON_*` values above and, unlike every reason in this table,
is reached only after a real (token-spending) OpenAI request was made.

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
  `event_claims` (**0**-6 `EventClaim` -- `MIN_EVENT_CLAIMS` was lowered from
  1 to 0, see "All-irrelevant evidence path" below), `limitations` (0-6
  bounded strings).
- **`EventClaim`**:
  - `event_type` -- `"monetary_policy"|"economic_data"|"corporate"|"regulatory"|"geopolitical"|"market_structure"|"other"`.
  - `claim_summary` -- concise, bounded string (hard max 400 characters),
    expected (by instruction) to be explicitly framed as provider-reported.
  - `evidence_ids` -- 1-5 bounded strings.
  - `content_basis` -- `"headline_only"|"headline_and_provider_summary"`.
  - `transmission_channels` -- 0-4 values from `"rates"|"inflation"|"growth"|"earnings"|"liquidity"|"risk_appetite"|"regulation"|"supply_chain"|"other"`.
  - `relevance` -- **added 2026-08-24, market-relevance hardening** --
    `"direct"|"broad_market"|"sector_or_industry"`. Deliberately no
    `"unknown"` value: a claim whose connection to the requested symbol
    cannot be classified into one of these three categories must not be
    produced at all -- the underlying article should be omitted instead
    (see "Relevance classification and article omission" below).
  - `relevance_rationale` -- **added 2026-08-24** -- required, bounded
    string (hard max 300 characters), explaining -- using the cited
    evidence and a recognized transmission channel -- how the article
    connects to the requested symbol. Never nullable and never blank.
  - `conditional_mechanism` -- nullable, bounded string (hard max 300
    characters) describing only a general, non-predictive transmission
    channel -- never a prediction for any specific symbol.

  `limitations` is a list of 0-6 bounded strings (hard max 300 characters
  each).

  **Advisory output budgets (added 2026-08-24, recurrence-reduction
  hardening -- see "Known structured-output validation failure" below).**
  `AGENT_INSTRUCTIONS` includes explicit, conservative advisory output
  budgets, each with deliberate margin below its corresponding hard
  Pydantic maximum above: `claim_summary` at most `ADVISORY_MAX_CLAIM_SUMMARY_LENGTH`
  (300, hard max 400) characters, `conditional_mechanism` at most
  `ADVISORY_MAX_CONDITIONAL_MECHANISM_LENGTH` (200, hard max 300)
  characters, `relevance_rationale` at most
  `ADVISORY_MAX_RELEVANCE_RATIONALE_LENGTH` (200, hard max 300) characters,
  each `limitation` at most `ADVISORY_MAX_LIMITATION_LENGTH`
  (200, hard max 300) characters, and `ADVISORY_PREFERRED_MIN_EVENT_CLAIMS`
  to `ADVISORY_PREFERRED_MAX_EVENT_CLAIMS` (1 to 4, hard max 6) event claims
  preferred, only exceeded if genuinely necessary. **This is
  instruction-level guidance only: no hard Pydantic bound was changed, and
  the agent still performs zero truncation, silent modification, retry, or
  acceptance of invalid output** -- a response that ignores this guidance
  and still violates a hard bound still fails schema validation exactly as
  before (`NewsAnalystUnexpectedError`/`OpenAIParseFailureError` propagate
  unchanged). These budgets reduce the likelihood of a real response landing
  close to -- or over -- a hard bound; they do not guarantee any future
  request will pass validation.
- **`NewsAnalystReport`** -- the final report returned by `run()` for both a
  completed and an abstained run:

```python
{
  "status": "completed" | "abstained",
  "symbol": "SPY",
  "snapshot_created_at_utc": "2026-08-24T12:00:00Z",
  "source_article_count": 3,
  # "insufficient" here, on a status="abstained" report, means the
  # all-irrelevant-evidence outcome specifically -- a model response was
  # received but retained zero event claims (see "All-irrelevant evidence
  # path" below); it is None only for a *preflight*-abstained report, where
  # no model call was ever made.
  "evidence_quality": "sufficient" | "limited" | "insufficient" | None,
  "event_claims": [...],       # [] when abstained (either kind)
  "limitations": [...],        # still populated when abstained via the
                                # all-irrelevant-evidence path (e.g. a
                                # truthful uncited-articles note); [] for a
                                # preflight abstention
  "directional_assessment": "not_performed",   # always fixed
  "trade_recommendation": "not_performed",     # always fixed
  "abstained_reasons": [...]    # [] when completed; a PREFLIGHT_REASON_*
                                 # value for a preflight abstention, or
                                 # ["no_sufficiently_relevant_articles"] for
                                 # the all-irrelevant-evidence outcome
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
- **Claims/quality consistency (added 2026-08-24,
  `_validate_claims_quality_consistency`, raises
  `NewsAnalystRelevanceError`)** -- runs *before* citation/content-basis/
  relevance validation, since it decides whether the response is the
  all-irrelevant-evidence abstention outcome at all (see "All-irrelevant
  evidence path" below). Enforces a strict biconditional: `event_claims` is
  empty if and only if `evidence_quality == "insufficient"`. Rejects empty
  `event_claims` paired with `"sufficient"`/`"limited"`, and rejects
  nonempty `event_claims` paired with `"insufficient"` (an incompatible
  abstention state -- this also prevents a low-effort claim from being used
  as a fabricated placeholder while still flagging the evidence as
  insufficient).
- **Relevance validation (added 2026-08-24, `_validate_relevance`, raises
  `NewsAnalystRelevanceError`)** -- runs after citation and content-basis
  validation (and, transitively, after the claims/quality check above,
  which is a no-op for an empty `event_claims` list). Rejects:
  - a blank or whitespace-only `relevance_rationale` (checked explicitly,
    since a whitespace-only string still satisfies the schema's
    `min_length=1`);
  - an oversized `relevance_rationale` (defense-in-depth alongside the hard
    Pydantic `max_length` bound);
  - a `relevance_rationale` matching a fixed, deterministic denylist for a
    bare, unspecific mechanism (e.g. "this could affect markets" with no
    named channel) -- a conservative pattern match, not general semantic
    understanding, so it is not proof that every unsupported rationale is
    caught; a rationale that names its mechanism immediately after the
    verb phrase (e.g. "...could affect markets **through** higher
    borrowing costs...") is not flagged by this pattern;
  - a claim asserting `relevance="broad_market"` or
    `relevance="sector_or_industry"` with zero `transmission_channels` --
    internally incompatible, since those two relevance values are only
    meaningful when at least one transmission channel actually supports
    the connection (`relevance="direct"` carries no such requirement,
    since it describes the article being about the requested symbol
    itself).
- **Post-response content policy** -- after citation, content-basis, and
  relevance validation and before `NewsAnalystReport` is constructed, every
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
relevance validation, and the post-response content policy check is not the
same as an extracted claim being factually correct.** A syntactically valid,
correctly-cited, policy-passing `NewsAnalystReport` only proves the model
followed the citation, shape, content-basis, relevance, and known-phrasing
rules -- it is not a claim that any `claim_summary` accurately reflects the
underlying article, nor that the underlying provider-reported claim itself
is true. No automated evaluation of analytical or factual accuracy exists in
this repository.

## Relevance classification and article omission

**Added 2026-08-24, market-relevance hardening (corrected the same day per
review -- see the two callouts below).** Manual review of the News Analyst's
first completed live run (see "Live run sequence and manual quality review"
below) found that an article about the expected resignation of the U.S. Army
Secretary had been converted into a weak, speculative SPY event claim built
around a generic defense-procurement mechanism -- a connection that should
generally not have been turned into a claim at all. This section describes
the resulting hardening.

Every retained event claim now carries a strict `relevance` classification
(`"direct"`/`"broad_market"`/`"sector_or_industry"`, deliberately no
`"unknown"` escape hatch that could permit an unsupported claim) and a
`relevance_rationale` explaining the connection using cited evidence and a
named transmission channel -- see "Structured output" above for the field
shapes and "Post-response validation" above for what is rejected.

`AGENT_INSTRUCTIONS` directs the model to **omit an article entirely --
write no event claim about it at all** -- whenever:

- its connection to the requested symbol would require inventing facts not
  stated in the evidence;
- only a generic "could affect markets" mechanism can be given, with no
  specific channel to name; or
- no recognized growth, inflation, rates, earnings, liquidity,
  risk-appetite, supply-chain, regulatory, or sector channel actually
  applies.

It is explicitly valid, and often correct, for the model to produce event
claims for fewer articles than were supplied -- `AGENT_INSTRUCTIONS` states
this directly, and this change does **not** require one claim per article
(`MAX_EVENT_CLAIMS` and the 1-4 preferred advisory range are unchanged).

**This is instruction-level guidance plus the deterministic post-response
checks above -- it cannot prove the model always chooses correctly which
article to leave uncited, only that a claim it does produce carries a
non-blank, non-generic, channel-consistent relevance rationale.** Whether a
specific retained claim's relevance judgment is itself correct is not
something this module can verify automatically; it remains a matter for
manual review, as for every other free-text field this agent produces.

**Truthful uncited-articles limitation (wording corrected per review --
the original wording attributed a reason the code cannot prove).** If,
after validation passes, at least one supplied article's evidence ID was
not cited by any event claim, `_build_report_limitations` -- agent-authored,
never model-authored -- prepends one fixed-format limitation to the final
report, e.g. `"1 of 2 supplied articles were not included in retained
claims."` **This code cannot know *why* the model left an article uncited**
(relevance is only one possible reason among others -- e.g. the model
choosing to group multiple articles under fewer claims) -- so the wording
deliberately states only the observable fact (a count), never a claimed
reason such as "insufficiently relevant". It never names the uncited
article, its headline, or its audit URL. The combined limitations list is
truncated to `MAX_LIMITATIONS` (6), the same hard bound
`NewsAnalystReport.limitations` enforces, so this deterministic addition can
never push the final report over that bound (displacing the
least-preferred model-supplied limitation if the list was already full).

## All-irrelevant evidence path

**Added 2026-08-24, per review finding: the prior hard minimum of one event
claim (`MIN_EVENT_CLAIMS == 1`) could force the model to construct a claim
even when every supplied article was irrelevant.** `MIN_EVENT_CLAIMS` was
lowered to **0** -- `event_claims` may now be structurally empty -- but this
relaxation is tightly gated so it cannot be used to smuggle through a
fabricated placeholder claim or an unexplained empty response:

- **A structurally empty `event_claims` is only accepted when
  `evidence_quality == "insufficient"`.** `_validate_claims_quality_consistency`
  (see "Post-response validation" above) enforces a strict biconditional:
  empty `event_claims` paired with `"sufficient"`/`"limited"` is rejected
  (self-contradictory -- claiming usable evidence while retaining zero
  claims), and nonempty `event_claims` paired with `"insufficient"` is also
  rejected, as an incompatible abstention state. The reverse direction
  matters as much as the forward one: it prevents a model from padding a
  response with one low-effort, barely-grounded claim while still flagging
  the overall evidence as insufficient -- if the model wants to keep a
  genuinely thin-but-real claim, it must use `"limited"`, not
  `"insufficient"`, to describe it.
- **When `event_claims` is empty this way, `NewsAnalyst.run()` builds a
  code-controlled `status="abstained"` report** -- the model never sets
  `status` itself. The fixed reason
  `ABSTAIN_REASON_NO_SUFFICIENTLY_RELEVANT_ARTICLES` =
  `"no_sufficiently_relevant_articles"` is used, distinct from every
  `PREFLIGHT_REASON_*` value (those describe why no OpenAI request was made
  at all; this reason describes a request that *was* made and answered, but
  retained nothing). `evidence_quality` is still recorded on the report
  (`"insufficient"`), and `model_metadata` is still populated -- a real
  model response was received and tokens were spent, unlike a
  preflight-abstained report (`model_metadata=None`).
- **The truthful uncited-articles limitation above still applies.** When
  every supplied article goes uncited (the all-irrelevant case),
  `_build_report_limitations` reports that correctly, e.g. `"2 of 2 supplied
  articles were not included in retained claims."`
- Every genuinely retained (nonempty) response must still separately pass
  the unchanged citation, content-basis, relevance, and output-policy
  checks -- this relaxation only governs whether zero claims can be
  accepted at all, never whether an individual retained claim is
  well-formed.

**No hard schema bound other than this deliberate, gated `MIN_EVENT_CLAIMS`
relaxation was changed**; `MAX_EVENT_CLAIMS` (6) and the 1-4 preferred
advisory range are unchanged, and citation/content-basis/relevance/
output-policy validation are all unchanged and still fully enforced for any
nonempty response.

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
| `NewsAnalystRelevanceError` | `relevance_invalid` | A claim's relevance rationale was blank/oversized/generic, its `relevance` classification was internally incompatible with its `transmission_channels`, or `event_claims` being empty/nonempty was incompatible with `evidence_quality` |
| `NewsAnalystPolicyError` | `policy_violation` | Model-authored free text failed the post-response content policy check |
| `NewsAnalystUnexpectedError` | `unexpected_error` | Any other unexpected failure |

A sanitized `OpenAIStructuredError` subclass also propagates unchanged from
`run()` -- it is already fully sanitized by `OpenAIStructuredClient`,
including its own `category` attribute. A sanitized `NewsEvidenceError` from
the underlying snapshot builder (e.g. a storage read failure) also
propagates unchanged.

## Known structured-output validation failure (2026-08-24)

On 2026-08-24, the first authorized live `--execute` attempt through
`NewsAnalyst` (symbol `SPY`, `limit=5`) failed with a sanitized
`{"error": "agent_error", "detail": "OpenAI response failed
structured-output validation.", "category": "response_validation_failed"}`
-- `OpenAIParseFailureError`, raised inside `OpenAIStructuredClient.generate()`
and propagated unchanged through `NewsAnalyst.run()` (`except
OpenAIStructuredError: raise`, see `news_analyst.py`). **No analysis was
accepted, and no retry or second live request was made.**

**Offline diagnosis (2026-08-24, no live request made to investigate).**
`NewsAnalyst` calls the exact same `OpenAIStructuredClient.generate()` used
by the Market Evidence Agent, whose own 2026-08-24
`response_validation_failed` failure was already diagnosed in detail (see
[docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)'s "Known
structured-output validation failure" section and `PROJECT_STATE.md`).
Reading `OpenAIStructuredClient.generate()`'s exception-mapping code
(`market_intelligence/model_clients/openai_structured.py`) proves the same
three-way distinction holds for this attempt:

- **Not a request-schema construction failure.** `output_model`
  (`NewsAnalystModelAnalysis`) is validated by `_validate_output_model()`
  *before* any SDK client is built or request sent; an unrepresentable
  schema raises `OpenAIRequestSchemaError` (category
  `request_schema_invalid`), not `pydantic.ValidationError`. Regenerating
  the real schema locally (`test_real_news_analyst_schema_passes_the_production_schema_preflight`
  in `market_intelligence/tests/test_openai_structured.py`) confirms
  `NewsAnalystModelAnalysis` passes this preflight -- it is structurally
  valid and buildable, `additionalProperties: false` throughout, every
  property required, enums/nested arrays/`$defs`/`$ref` all resolve. No
  schema restructuring was needed or made.
- **Not a refusal or an incomplete response.** Both are reported via
  `StructuredOutputResult.status` (`"refusal"`/`"incomplete"`), never via a
  raised exception -- `NewsAnalyst.run()` maps those to
  `NewsAnalystRefusalError`/`NewsAnalystIncompleteError` explicitly, neither
  of which was the error observed.
- **Is a received-response content validation failure.** Inside
  `generate()`, `pydantic.ValidationError` can only be raised by the
  installed OpenAI SDK's own client-side re-validation of an *actually
  received* response's text against `output_model`
  (`parse_text()` -> `model_validate_json()`) -- this structurally proves a
  request was sent and a response was received (tokens were spent; no exact
  count was recorded, since this client never captures token/response
  metadata for a failed request) before validation failed.

**Because this client never captures or logs raw model output (by design),
the exact field/value that violated a bound in this one live attempt is
unavailable and cannot be proven from local evidence alone.** Exceeding one
of `NewsAnalystModelAnalysis`'s Pydantic-only `minLength`/`maxLength`/
`minItems`/`maxItems` bounds (e.g. `claim_summary`'s 400-character maximum,
or `event_claims`' 6-item maximum) is one plausible, locally reproducible
failure mode for a response that was otherwise type/enum/shape-conformant --
reproduced offline in
`test_generate_maps_real_news_analyst_schema_bound_violation_to_response_validation_failed`
and
`test_generate_maps_real_news_analyst_schema_excessive_event_claims_to_response_validation_failed`
(`market_intelligence/tests/test_openai_structured.py`) -- it is **not**
established as the proven cause of this specific live attempt. OpenAI has
not published official documentation establishing that its Structured
Outputs generation leaves these bound keywords unenforced specifically for
the non-fine-tuned `gpt-5-mini` model this project uses.

No strict structured output, citation validation, content-basis validation,
output-policy validation, or field bound was removed or weakened to
diagnose or respond to this failure. The mitigation applied instead is the
advisory output budgets described above under "Structured output" -- they
reduce the likelihood of a real response landing close to -- or over -- a
hard bound; they do not guarantee any future request will pass validation.
See `PROJECT_STATE.md` for the full, dated record.

## Live run sequence and manual quality review (2026-08-24)

This section documents, truthfully and in full, every authorized live
`--execute` attempt made against the real local database for symbol `SPY`
while diagnosing and hardening the News Analyst, plus the manual quality
finding that motivated the relevance hardening described above. **No
automatic retries occurred at any point** -- each attempt below was a
separately authorized, single, manual invocation.

1. **First attempt (symbol `SPY`, `limit=5`).** Failed with
   `response_validation_failed` (`OpenAIParseFailureError`) -- a request was
   sent and a response was received, but its content failed this client's
   Pydantic re-validation against `NewsAnalystModelAnalysis`. See "Known
   structured-output validation failure" above for the full offline
   diagnosis. No analysis was accepted.
2. **Second attempt, after the advisory-budget hardening above
   (`OPENAI_MAX_OUTPUT_TOKENS` left at its then-default of 2048).** The
   model's response was incomplete: `StructuredOutputResult.status ==
   "incomplete"`, `incomplete_reason == "max_output_tokens"`, raising
   `NewsAnalystIncompleteError`. No analysis was accepted.
3. **Third attempt, after raising `OPENAI_MAX_OUTPUT_TOKENS` to 4096
   locally (`OPENAI_REQUEST_TIMEOUT_SECONDS` left at its then-default of
   30).** The request exceeded the configured 30-second timeout locally
   (`OpenAITimeoutError`) before OpenAI returned a response. No analysis was
   accepted.
4. **Fourth attempt, with `OPENAI_MAX_OUTPUT_TOKENS=4096` and
   `OPENAI_REQUEST_TIMEOUT_SECONDS=120` both set locally.** This attempt
   **completed**: `input_tokens=1575`, `output_tokens=2474`,
   `total_tokens=4049`, model `gpt-5-mini`. Schema validation, citation
   validation, and content-basis validation all passed; the report contained
   4 cited event claims, `directional_assessment`/`trade_recommendation`
   fixed at `"not_performed"` as always, and appropriate limitations. This
   is the run whose configuration (`OPENAI_MAX_OUTPUT_TOKENS=4096`,
   `OPENAI_REQUEST_TIMEOUT_SECONDS=120`) `.env.example` documents and
   `Settings`' own built-in defaults were subsequently corrected to match
   (see "Settings" in
   [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)).

**One completed example is not validation.** Passing schema/citation/
content-basis validation on this one run proves the deterministic scaffolding
worked for this one response -- it is not a claim of analytical accuracy, and
no automated evaluation methodology exists for it (see "Post-response
validation" above and [CLAUDE.md](../CLAUDE.md)/[AGENTS.md](../AGENTS.md)'s
"Evidence and Claims" section).

**Manual quality finding (this run, reviewed before the relevance hardening
in this document was added).** A manual (human) read of the 4 accepted event
claims found:

1. An article about the expected resignation of the U.S. Army Secretary had
   been converted into a weak, speculative SPY-relevance mechanism involving
   defense procurement. This connection generally should not have been
   turned into a claim at all -- this is the finding that directly motivated
   the relevance classification, rationale, article-omission, and
   all-irrelevant-evidence hardening described in "Relevance classification
   and article omission" and "All-irrelevant evidence path" above.
2. A Baker Hughes rig-count headline was classified as `economic_data` with
   `supply_chain`/`growth` transmission channels. This is defensible, but
   the review noted that energy/commodity relevance should not be overstated
   beyond what the supplied headline actually reported.
3. The other PMI and investor-flow claims were reasonably grounded in their
   cited headline/summary evidence.

This is one manually read example from one live run, not an automated
evaluation, not a validated evaluation methodology, and not proof of factual
accuracy, extraction quality, or reliability across other symbols/articles.
The relevance hardening in this document reduces the likelihood of a
recurrence of finding #1 specifically (a weak, speculative,
invented-mechanism claim); it does not prove such a claim can never occur
again, since the post-response checks are pattern-based, not general
semantic understanding (see "Post-response validation" above).

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

**Advisory-budget and structured-output regression coverage (added
2026-08-24).** `test_news_analyst.py` gained tests proving every advisory
budget is present in `AGENT_INSTRUCTIONS` and is numerically strictly below
its corresponding hard Pydantic maximum, and that
`OpenAIParseFailureError`/`response_validation_failed` propagates unchanged
through `NewsAnalyst.run()` without leaking evidence text. Separately,
`market_intelligence/tests/test_openai_structured.py` gained regression
tests using the real, production `NewsAnalystModelAnalysis` schema (not only
that file's pre-existing generic toy models): one proves the schema passes
production's own schema preflight (`_validate_output_model`, public
Pydantic API only); one proves a synthetic, schema-and-bound-conformant
response round-trips through `generate()` unchanged; two prove distinct
representative oversized/invalid responses (an oversized `claim_summary`,
and an excessive `event_claims` count) each reproduce the same sanitized
`OpenAIParseFailureError`/`response_validation_failed` category and message
this client raises in general -- a plausible, locally reproducible failure
signature, not proof of the live attempt's exact cause. Two further tests
prove no automatic retry occurs: the constructed SDK client is always built
with `max_retries=0`, and a failing SDK call is made exactly once by
`generate()`. All of this runs entirely offline -- no network, no
credentials.

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

**Relevance classification coverage (added 2026-08-24, market-relevance
hardening; wording and test names corrected the same day per review).**
`test_news_analyst.py` gained tests covering: `relevance`/
`relevance_rationale` required at the schema level and `relevance` rejecting
an unrecognized value; `"direct"`/`"broad_market"`/`"sector_or_industry"`
each accepted when grounded (a non-empty rationale, and a supporting
transmission channel for the latter two); a `"broad_market"`/
`"sector_or_industry"` claim rejected when asserted with zero transmission
channels; a blank or whitespace-only rationale rejected; an oversized
rationale rejected (defense-in-depth alongside the hard schema bound); a
generic, mechanism-free rationale rejected, and a specific, channel-naming
rationale accepted; an irrelevant, government-personnel-style headline
(mirroring the Army Secretary finding above) left uncited by a valid
synthetic model response, producing the deterministic, truthful
uncited-articles limitation (`"N of M supplied articles were not included in
retained claims"` -- never a claimed reason) without ever naming the uncited
headline or its URL; that limitation absent when every article is cited;
that the limitation is truncated (never exceeding `MAX_LIMITATIONS`) when
the model already supplied a full limitations list; that an adversarial,
self-asserting-relevance headline cannot force a generic rationale past
validation; and that a `NewsAnalystRelevanceError` never echoes the rejected
rationale text. `test_openai_structured.py`'s existing real-schema
regression tests were updated to include the now-required `relevance`/
`relevance_rationale` fields (so they continue to isolate exactly the bound
violation each one targets) and the schema's new `event_claims`
`minItems=0`. The Market Evidence Agent's own test suite was re-run
unchanged and still passes -- this hardening touches only
`market_intelligence/agents/news_analyst.py`,
`market_intelligence/config/settings.py`, and their own tests.

**All-irrelevant-evidence path coverage (added 2026-08-24, same day, per
review).** Further tests cover: `NewsAnalystModelAnalysis` accepting a
structurally empty `event_claims`; a code-controlled `status="abstained"`
report built with the fixed reason
`"no_sufficiently_relevant_articles"`, `evidence_quality="insufficient"`
recorded, and `directional_assessment`/`trade_recommendation` still fixed at
`"not_performed"`, when the model returns zero claims; that this outcome
still populates `model_metadata` (tokens were spent, unlike a
preflight-abstained report); that the truthful uncited-articles limitation
correctly reports "M of M" when every article is uncited this way; that
empty `event_claims` paired with `evidence_quality="sufficient"` or
`"limited"` is rejected (parametrized over both values); and that nonempty
`event_claims` paired with `evidence_quality="insufficient"` is rejected as
an incompatible abstention state. `test_settings.py`'s default-values test
was updated to assert the corrected `120.0`/`4096` defaults.

## Known limitations

- **As of this change (2026-08-24):** `NewsAnalyst` has been run four times,
  live, against the real local database with `--execute` (symbol `SPY`,
  `limit=5`) -- see "Live run sequence and manual quality review (2026-08-24)"
  above for the full, truthful sequence: a `response_validation_failed`
  failure, then an incomplete response at `max_output_tokens=2048`, then a
  local timeout at the then-default 30-second timeout, then a completed run
  with `max_output_tokens=4096`/`timeout=120s` (`input_tokens=1575`,
  `output_tokens=2474`, `total_tokens=4049`, 4 event claims). **No automatic
  retries occurred at any point.** A manual quality review of that one
  completed run's 4 event claims found one weak, speculative claim (built
  from an article about the expected resignation of the U.S. Army Secretary)
  that motivated the relevance classification/rationale/article-omission and
  all-irrelevant-evidence hardening in this document -- see "Relevance
  classification and article omission" and "All-irrelevant evidence path"
  above. **One completed example is not validation** -- see the live-run
  section above and `PROJECT_STATE.md` for the dated record. No other live
  database access or live OpenAI request has been made using this agent.
- Single symbol per call -- no batch, no multi-symbol comparison, no
  multi-turn conversation.
- No tools, no web search, no file access by the model, no persistence, no
  migration, and no orchestration integration -- this agent is not wired
  into `market_intelligence/orchestration/`.
- The post-response content policy check matches known fixed phrasing, not
  general semantic meaning, so it is not proof that every possible
  directional, bias, trade-recommendation, or options-related statement (or
  every possible way a claim could misrepresent its source) is caught. The
  same limitation applies to the relevance-rationale generic-phrase denylist
  added in this change (see "Post-response validation" above).
- This agent never assesses whether a provider-reported claim is itself
  true -- it only organizes what the provider reported, with citations back
  to the exact stored article. See
  [docs/SOURCE_POLICY.md](../SOURCE_POLICY.md).
- No dashboard, alerting, or Robinhood/brokerage integration of any kind.
- This is groundwork for future, separately reviewed work -- it is not
  itself a general agent framework, and no further work is implied or
  authorized by this change.
