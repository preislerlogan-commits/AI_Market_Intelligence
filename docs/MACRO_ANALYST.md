# Macro Analyst

This document describes the Macro Analyst added under
`market_intelligence/agents/macro_analyst.py` and
`scripts/run_macro_analyst.py`. It covers infrastructure only -- see
[PROJECT_STATE.md](../PROJECT_STATE.md) for what has (and has not) actually
been exercised, including whether a live OpenAI request has been made using
this agent.

## Purpose and scope

`MacroAnalyst` is a **single-turn, no-tools analysis component** -- not an
autonomous or multi-agent system. It is also explicitly **not a regime
classifier, predictor, directional market model, or trading agent** -- it is
a first, bounded, *factual* macro-evidence analyst. Given a list of FRED
series IDs, it:

1. Builds a bounded, deterministic macro-evidence snapshot from the
   existing, already-reviewed
   [`MacroEvidenceBuilder`](MACRO_EVIDENCE_SNAPSHOT.md) -- every series entry
   already carries a stable, code-generated evidence ID plus its official
   stored metadata (title, frequency, frequency_short, units, seasonal
   adjustment) when available, a bounded `recent_observations` excerpt, and
   (when precisely supported) one exact `latest_change_from_previous`
   comparison. **This full snapshot -- including `recent_observations` --
   is retained unchanged for local deterministic evidence and audits; it is
   not what is sent to the model (see item 3 and "Model-facing evidence
   compaction" below).**
2. Evaluates a fixed, deterministic, **all-or-nothing** preflight gate
   against that snapshot (see "Deterministic preflight" below). If the gate
   fails for *any* requested series, the agent returns a truthful
   `status="abstained"` report and makes **zero OpenAI requests**.
3. If the gate passes, builds a bounded, **compacted**, model-facing
   evidence package from the snapshot's official stored observation/metadata
   fields only -- deliberately **excluding** the snapshot's
   `recent_observations` history (see "Model-facing evidence compaction"
   below) -- never a database path, SQL text, ingestion ID, credential, or
   raw audit/internal field, and makes **exactly one** structured-output
   request via the
   existing [`OpenAIStructuredClient`](OPENAI_PROVIDER_BOUNDARY.md), asking
   the model to describe, factually and without interpretation, the single
   latest stored observation for each requested series using only its
   official stored metadata and its own official reporting frequency --
   plus, at most, one precisely supported two-observation comparison per
   series when `latest_change_from_previous.available` is `true`.
4. Validates every evidence ID and series ID the model cites, validates that
   every claim's cited evidence actually belongs to the series it names,
   validates that no claim describes acceleration/deceleration, surprise, a
   historical extreme, correlation, causation, a policy change, or a market
   regime (none of which stored observations can ever support), validates
   that any increase/decrease/unchanged language is a fully supported
   two-observation comparison (see "Comparison claims" below), validates
   frequency-aware, non-point-in-time wording, validates that every listed
   transmission channel is addressed by `conditional_mechanism`, and
   assembles a final, sanitized, schema-validated report.

`MacroAnalyst` describes only the latest stored level of each requested
series (and its official stored metadata), plus, when precisely supported,
one exact two-observation comparison. **It never states or implies that a
value accelerated, decelerated, surprised, reached a historical extreme,
followed a trend, correlated with or caused any other outcome, reflected a
policy change, or described a market regime** -- none of those claims can
ever be supported by stored observations alone. This is enforced both by
`AGENT_INSTRUCTIONS` and, as defense-in-depth, by a deterministic,
fail-closed post-response content-scope check (`_validate_content_scope`/
`MacroAnalystContentScopeError` -- see "Post-response validation" below).
**A model-authored `limitation` that clearly negates this same prohibited
content-scope language, or frames it as unavailable/insufficient (e.g.
"insufficient observations to establish a trend"), is narrowly and
separately allowed -- see "Post-response validation" below -- but
`claim_summary` and `conditional_mechanism` are never exempted, and an
unnegated affirmative claim anywhere, including elsewhere in the same
`limitation`, is still rejected. A second, narrower allowance exists for the
shared post-response content *policy* check (as distinct from the
content-scope check above): a model-authored `limitation` that clearly
negates a directional-prediction match specifically (e.g. "does not predict
future market direction") is also narrowly allowed -- see "Post-response
validation" below. `claim_summary`/`conditional_mechanism` are never
exempted from the policy check either, and bullish/bearish bias, trade
recommendation/action, and options-related language are never exempted in a
`limitation` this way, negated or not.**

**Comparison claims.** A claim may additionally state that a series'
latest stored observation *increased*, *decreased*, or was *unchanged*
relative to the immediately preceding stored observation, but only when
`latest_change_from_previous.available` is `true` for that series **and**
the claim cites exactly its `latest_evidence_id`/`previous_evidence_id`
**and** states both exact dates and both exact values **and** states a
direction matching `direction` exactly -- checked by
`_validate_comparison_claims`/`MacroAnalystComparisonError` (see
"Post-response validation" below). Increase/decrease/unchanged language
anywhere else (a single-evidence claim, `conditional_mechanism`, or a
`limitation`) is always rejected. No percentage, annualized, or
basis-point change is ever computed, and two observations are never
described as a trend.

**Frequency-aware wording.** Every claim must describe its series' latest
observation using that series' own official reporting frequency, never as
a live point-in-time reading -- e.g. "the stored monthly observation dated
2026-07-01", never "at 3.63 percent on 2026-07-01". Checked by
`_validate_frequency_wording`/`MacroAnalystFrequencyWordingError`. A series
whose official `frequency_short` is not one of a small, fixed, recognized
set fails preflight instead (`series_frequency_unrecognized`) rather than
let the model guess a wording for it.

**Transmission-channel addressing.** Every `transmission_channels` entry a
claim lists must be explicitly and verifiably addressed by that claim's
`conditional_mechanism`, checked deterministically per channel by
`_validate_transmission_channels`/`MacroAnalystTransmissionChannelError`.
`"other"` can never be verified this way and is always rejected if listed.

The final report's `directional_assessment` and `trade_recommendation`
fields are always the fixed literal string `"not_performed"` -- the
model-facing schema (`MacroAnalystModelAnalysis`) does not even include
those fields, so the model has no way to set them; that restriction is
absolute, exactly as for the Market Evidence Agent and News Analyst. The
model's free-text fields (every macro claim's `claim_summary`/
`conditional_mechanism`, every model-supplied `limitation`) are additionally
screened by the same shared, deterministic, fail-closed post-response
content policy check those two agents use
(`market_intelligence/agents/non_directional_output_policy.py`), covering
directional predictions, bullish/bearish bias, trade recommendations/
actions, and options-related detail. **No sentiment, probability, confidence
score, forecast, options detail, or recommendation field exists anywhere in
this agent's schema** -- these are not merely filtered out of free text, they
have no field to be set in at all. No tool, web search, function calling,
file access, or Agents-SDK-style loop is used anywhere in this component; it
makes at most one bounded provider request per call.

**Official series titles and metadata are provider-reported text, not this
project's own editorial judgment.** Every series entry's `title`,
`frequency`, `units`, and `seasonal_adjustment` is read exactly as stored by
the existing, separately reviewed FRED series-metadata pipeline (see
[docs/MACRO_EVIDENCE_SNAPSHOT.md](MACRO_EVIDENCE_SNAPSHOT.md)) and is
explicitly labeled as untrusted, provider-reported text inside the
model-facing evidence payload itself (`EVIDENCE_UNTRUSTED_TEXT_NOTE`), never
treated as instructions.

## Inputs

The agent's public entry points (`build_preflight()`/`run()`) take exactly:

- `series_ids` (optional, default `("FEDFUNDS",)` -- the same
  `DEFAULT_SERIES_IDS` constant `MacroEvidenceBuilder` uses) -- validated via
  the same `normalize_series_ids` used by `MacroEvidenceBuilder` (a bounded
  sequence of 1-10 normalized FRED series IDs, duplicates rejected, order
  preserved).
- `recent_observations_limit` (optional, default `DEFAULT_RECENT_OBSERVATIONS_LIMIT`
  (`6`) -- the same constant `MacroEvidenceBuilder` uses) -- forwarded
  as-is to `MacroEvidenceBuilder.build_snapshot`, so it is validated the
  same way (a plain `int` between `2` and `24`) before any DuckDB access.

Internally, the agent also consumes:

- `MacroEvidenceBuilder.build_snapshot(series_ids)` output (see
  [docs/MACRO_EVIDENCE_SNAPSHOT.md](MACRO_EVIDENCE_SNAPSHOT.md)).
- Fixed, code-authored developer instructions (`AGENT_INSTRUCTIONS`) --
  never derived from evidence, provider text, or any other untrusted data,
  and series title/metadata text is **never** placed into these
  instructions -- it only ever appears inside the untrusted evidence
  payload.

## Deterministic preflight

`MacroAnalyst.build_preflight()` evaluates a fixed set of gates before any
OpenAI request is considered. Unlike the Market Evidence Agent's and News
Analyst's preflight gates (which describe a single symbol), this gate is
**all-or-nothing across the full requested series list**: the agent never
calls OpenAI unless *every* requested series passes *every* gate below.

| Gate | Source | Abstention reason if it fails for any requested series |
|---|---|---|
| Snapshot's echoed request exactly matches the normalized requested series IDs | snapshot | `requested_series_mismatch` |
| `has_stored_observation == True` | snapshot, per series | `series_missing` |
| `metadata_available == True` | snapshot, per series | `series_metadata_missing` |
| `freshness.stale == False` | snapshot, per series | `series_stale` |
| `freshness.future_date_detected == False` | snapshot, per series | `series_future_dated` |
| `latest_is_missing == False` | snapshot, per series | `series_latest_missing` |
| `evidence_id is not None` | snapshot, per series | `series_no_evidence_id` |
| `frequency_short` (when metadata available) is one of `FREQUENCY_SHORT_WORDS`' recognized codes | snapshot, per series | `series_frequency_unrecognized` |

If any gate fails for any requested series, `run()` returns a
`MacroAnalystReport` with `status="abstained"` and `abstained_reasons`
listing every failed reason *category* (not a per-series breakdown -- that
detail is instead available on `MacroAnalystPreflightResult.flags`, see
below) -- **zero model tokens are spent**. There is no partial admission: a
three-series request where two series are fully eligible and one is stale
abstains for the entire request, exactly like the Market Evidence Agent's
and News Analyst's own preflight gates abstain outright rather than
proceeding with a partial evidence set for their own subject matter.

`MacroAnalystPreflightResult.flags` additionally reports the per-series
detail (mirroring `MacroEvidenceBuilder`'s own snapshot-level `flags`, plus
three new fields this agent adds): `missing_series`, `stale_series`,
`future_dated_series`, `missing_metadata_series`, `latest_missing_series`,
`no_evidence_id_series`, `frequency_unrecognized_series` -- each a list of
the specific series IDs that triggered that condition.

`latest_change_from_previous` availability is deliberately **not** a
preflight gate: a series' plain single-observation claim remains fully
describable even when a comparison is unavailable for it; the model is
simply never permitted to use increase/decrease/unchanged language for
that series (see "Comparison claims" above).

## Evidence package and evidence-ID contract

`_build_model_evidence()` builds one bounded dict from the snapshot's
`series` list only:

```python
{
  "note": "title, frequency, units, and seasonal_adjustment below are official ...",
  "series_ids": ["FEDFUNDS"],
  "series": {
    "macro_3f2a9c1d4e5b6789": {
      "series_id": "FEDFUNDS",
      "title": "Federal Funds Effective Rate",
      "frequency": "Monthly",
      "frequency_short": "M",
      "units": "Percent",
      "seasonal_adjustment": "Not Seasonally Adjusted",
      "observation_date": "2026-07-01",
      "latest_value": "5.330000",
      "realtime_start": "1776-07-04",
      "realtime_end": "9999-12-31",
      "coverage": {
        "row_count": 12,
        "earliest_observation_date": "2025-08-01",
        "latest_observation_date": "2026-07-01",
        "missing_observation_count": 0
      },
      "latest_change_from_previous": {
        "available": true,
        "unavailable_reason": null,
        "latest_observation_date": "2026-07-01",
        "latest_value": "5.330000",
        "latest_evidence_id": "macro_3f2a9c1d4e5b6789",
        "previous_observation_date": "2026-06-01",
        "previous_value": "5.000000",
        "previous_evidence_id": "macro_7a1b2c3d4e5f6081",
        "absolute_change_native_units": "0.330000",
        "direction": "increased"
      }
    }
  }
}
```

**Note there is deliberately no `recent_observations` key above** -- see
"Model-facing evidence compaction" below.

- Every `series` key is the series entry's existing, **stable,
  code-generated** `evidence_id` from `MacroEvidenceBuilder` -- never chosen
  or supplied by the model. This same key always equals
  `latest_change_from_previous.latest_evidence_id`, since both are derived
  from the identical chosen latest row.
- Only `series_id`, `title`, `frequency`, `frequency_short`,
  `seasonal_adjustment`, `observation_date` (the chosen row's
  `latest_observation_date`), `latest_value`, `realtime_start`,
  `realtime_end`, `coverage`, and `latest_change_from_previous` are
  preserved -- **no database path, SQL text, ingestion ID, credential, or
  raw audit/internal field is ever included** (none of those exist on the
  snapshot to begin with, so there is nothing to filter out).
- A series entry with no stable evidence ID (`evidence_id is None`, i.e. no
  stored observation at all) is never added to the evidence package -- the
  model is never given a fact to cite that doesn't correspond to real,
  addressable stored data. In practice this only matters for a diagnostic
  read of `evidence_package`, since a missing evidence ID for *any*
  requested series already fails preflight for the *entire* request (see
  above), so `run()` never reaches the model in that case.
- The model may cite `previous_evidence_id` (from a series'
  `latest_change_from_previous`, when `available`) alongside that series'
  primary evidence_id in the **same** claim, for a valid two-observation
  comparison claim only (see "Comparison claims" above and "Structured
  output" below) -- `_evidence_series_map` maps both IDs to the same
  `series_id` so citation/series validation treats either as belonging to
  that series.
- A fixed `note` field is embedded directly in the payload (not just in this
  module's documentation) labeling `title`/`frequency`/`units`/
  `seasonal_adjustment` as official provider-reported metadata -- so a
  consumer that only skims the evidence JSON structure still encounters the
  warning.
- This exact dict is passed as-is to
  `OpenAIStructuredClient.generate(evidence=...)`, which already treats it
  as untrusted data, labels it as such, and appends a fixed safety appendix
  to the instructions regardless of its contents (see
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)).
- No tool, web search, or additional data retrieval of any kind occurs --
  the evidence package is built entirely from data `MacroEvidenceBuilder`
  already read from local storage before this agent runs.

## Model-facing evidence compaction

**`_build_model_evidence()` deliberately omits the snapshot's
`recent_observations` excerpt from the payload sent to OpenAI.**
`MacroEvidenceBuilder`'s own snapshot -- used for local deterministic
evidence and audits (see [docs/MACRO_EVIDENCE_SNAPSHOT.md](MACRO_EVIDENCE_SNAPSHOT.md))
-- is unchanged and still retains up to `recent_observations_limit` recent
rows per series. Only the model-facing subset above excludes it.

This is narrow and behavior-preserving: every claim this agent can validate
is scoped to either the single latest stored observation
(`observation_date`/`latest_value`, both still sent) or one exact
two-observation comparison against the immediately preceding stored
observation (`latest_change_from_previous`, which already carries both
observations' dates, values, and evidence IDs, and is still sent
unchanged) -- see "Comparison claims" above. No post-response validator
(`_validate_citations_and_series`, `_validate_coverage`,
`_validate_content_scope`, `_validate_comparison_claims`,
`_validate_frequency_wording`, `_validate_transmission_channels`, the
shared non-directional output policy) ever reads or needs a multi-row
observation history, so sending `recent_observations` to the model was
always redundant with respect to what this agent can actually check.

**This fixes a real, live failure.** The first authorized seven-series
Core Macro Basket `--execute` attempt (`FEDFUNDS`, `GS10`, `CPIAUCSL`,
`PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`, `recent_observations_limit=6`) passed
the deterministic preflight but failed locally, before any request was
sent, with `OpenAIInvalidRequestError`
(`category=request_invalid`, `"Invalid evidence: exceeds the maximum node
count."` -- see
[docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)'s
`MAX_EVIDENCE_NODES` (500) bound). This is a local, pre-request validation
failure raised entirely inside `OpenAIStructuredClient.generate()`'s
input-validation step (see `_validate_evidence_shape`) -- **zero provider
requests were sent and zero tokens were spent for that attempt.**

Offline analysis reproduced the exact failure: with `recent_observations`
included, each series entry's six-row excerpt alone contributed 43 of the
evidence tree's nodes (`OpenAIStructuredClient`'s node counter counts every
dict, every list, and every scalar leaf it recurses into), and across seven
series plus the payload's own wrapping structure the total reached 501 nodes
-- one over the 500-node cap -- reproducing
`"exceeds the maximum node count"` exactly. Removing `recent_observations`
from the model-facing payload (this change) reduces the real seven-series
Core Macro Basket evidence package to 200 nodes / 5,643 serialized bytes,
comfortably under both `MAX_EVIDENCE_NODES` (500) and `MAX_EVIDENCE_BYTES`
(32,000) -- and, notably, this reduction is now independent of
`recent_observations_limit` entirely, since that field is no longer sent to
the model at all regardless of the limit requested. A synthetic worst-case
ten-series request (`MAX_SERIES_IDS`, the largest this agent ever accepts),
built with conservative, deliberately oversized placeholder metadata
strings (a title, frequency, units, and seasonal-adjustment string each
longer than any real Core Macro Basket series' actual metadata), still
measures only 284 nodes / 11,495 bytes -- comfortably under both limits
with substantial margin. These exact figures are measured directly in
`market_intelligence/tests/test_macro_analyst.py` (see "Testing" below),
not merely asserted in this document.

**No global limit was raised to fix this.** `OpenAIStructuredClient`'s
`MAX_EVIDENCE_NODES`/`MAX_EVIDENCE_DEPTH`/`MAX_EVIDENCE_BYTES` constants
(see [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)) are
unchanged, and no comparison validation, citation validation, full-series
coverage requirement, frequency wording, channel validation, content-scope
policy, or non-directional policy was weakened -- only redundant history
data was removed from what is sent to the model. **This fix has not yet
been exercised against a live OpenAI response for the seven-series basket
or any other request** -- it has been validated only by the offline,
deterministic tests described below (including one dedicated test that
sends the real, production seven-series evidence package through the real
`OpenAIStructuredClient`'s evidence-size validation, with only its SDK
transport faked). See `PROJECT_STATE.md` for the full sanitized record of
both the live failure and this fix.

## Structured output

Two strict Pydantic models (`extra="forbid"`, every field bounded):

- **`MacroAnalystModelAnalysis`** -- the *only* schema sent to OpenAI as
  `output_model=`. It deliberately excludes `status`, `series_ids`,
  `snapshot_created_at_utc`, `source_series_count`,
  `directional_assessment`, and `trade_recommendation` -- those are
  agent-authored/fixed, so the model has no opportunity to set them. Fields:
  `evidence_quality` (`"sufficient"|"limited"|"insufficient"`),
  `macro_claims` (**0**-`MAX_MACRO_CLAIMS` `MacroClaimDraft`), `limitations`
  (0-6 bounded strings). `MAX_MACRO_CLAIMS` is fixed to
  `macro_evidence.MAX_SERIES_IDS` (`10`) -- the existing, already-
  reviewed module-level evidence-layer bound on how many series may be
  requested in one call at all --
  deliberately reused rather than an independent literal, so a
  `"sufficient"`/`"limited"` response can always structurally fit one claim
  per requested series, however many were requested (see "Full-basket
  coverage" below).
- **`MacroClaimDraft`** (the model-facing shape of one claim):
  - `series_id` -- must be one of the requested series IDs (validated
    post-response, see below).
  - `claim_summary` -- concise, bounded string (hard max 400 characters),
    describing only the single latest stored observation and its official
    metadata, using frequency-aware, non-point-in-time wording (see
    "Frequency-aware wording" above) -- and, optionally, one precisely
    supported two-observation comparison (see "Comparison claims" above).
  - `evidence_ids` -- **one or two** bounded strings (hard bound `1`-`2`,
    `MAX_EVIDENCE_IDS_PER_CLAIM`). A plain single-observation claim cites
    exactly one (that series' primary evidence_id). A valid two-observation
    comparison claim cites exactly two: that series'
    `latest_change_from_previous.latest_evidence_id` and
    `.previous_evidence_id`, and only those two -- checked post-response by
    `_validate_comparison_claims` (see "Post-response validation" below).
  - `economic_category` --
    `"policy_rate"|"inflation"|"labor"|"growth"|"liquidity"|"housing"|"energy"|"other"`.
  - `transmission_channels` -- 0-4 values from
    `"rates"|"inflation"|"growth"|"liquidity"|"risk_appetite"|"credit_conditions"|"currency"|"housing"|"energy"|"other"`.
  - `conditional_mechanism` -- nullable, bounded string (hard max 300
    characters) describing only a general, non-predictive, non-directional
    transmission channel -- never a prediction for any specific symbol.

  **`content_basis` is deliberately excluded from this model-facing
  schema.** Every macro claim's evidentiary basis is always exactly one
  fixed value -- a stored observation plus its official stored metadata,
  nothing else -- so, mirroring how `directional_assessment`/
  `trade_recommendation` are handled, this field is never sent to or
  received from the model at all. `MacroAnalyst` sets it itself (to the
  fixed constant `CONTENT_BASIS_STORED_OBSERVATION_AND_OFFICIAL_METADATA` =
  `"stored_observation_and_official_metadata"`) when building each claim on
  the *final report* (see `MacroClaim` below); the model has no way to set
  it to anything else.

  **Limitation-wording guidance.** `AGENT_INSTRUCTIONS` also directly tells
  the model that every `limitation` must describe a bounded evidence gap
  directly (what the stored evidence lacks or cannot support), and should
  not mention predictions, market direction, trades, or options at all, even
  as a disclaimer -- preferring to describe the evidence gap itself over
  stating what it does not predict. This is instruction-level guidance only:
  it reduces the likelihood of a `limitation` needing the narrow negated-
  directional-prediction allowance described in "Post-response validation"
  below at all, but does not replace or weaken that check, which still
  applies deterministically to whatever the model actually returns.

  **Advisory output budgets.** `AGENT_INSTRUCTIONS` includes explicit,
  conservative advisory output budgets, each with deliberate margin below
  its corresponding hard Pydantic maximum, mirroring the same mitigation
  already applied to the Market Evidence Agent's and News Analyst's own
  instructions after their 2026-08-24 `response_validation_failed` live
  attempts (see
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)):
  `claim_summary` at most `ADVISORY_MAX_CLAIM_SUMMARY_LENGTH` (300, hard max
  400) characters, `conditional_mechanism` at most
  `ADVISORY_MAX_CONDITIONAL_MECHANISM_LENGTH` (200, hard max 300)
  characters, and each `limitation` at most `ADVISORY_MAX_LIMITATION_LENGTH`
  (200, hard max 300) characters. **There is deliberately no "preferred"
  advisory range for the number of macro claims** -- unlike the Market
  Evidence Agent's/News Analyst's own advisory observation/event-claim
  counts, the correct macro-claim count is not a preference here, it is
  mandatory and exactly determined by the request: one claim per requested
  series whenever `evidence_quality` is `"sufficient"`/`"limited"` (see
  "Full-basket coverage" below), so `AGENT_INSTRUCTIONS` states that
  requirement directly instead of a numeric budget. **This is
  instruction-level guidance only: no hard Pydantic bound was changed, and
  the agent still performs zero truncation, silent modification, retry, or
  acceptance of invalid output** -- a response that ignores this guidance
  and still violates a hard bound still fails schema validation exactly as
  before (`NewsAnalystUnexpectedError`-equivalent: propagates as
  `OpenAIParseFailureError`/`response_validation_failed`, unchanged). These
  budgets reduce the likelihood of a real response landing close to -- or
  over -- a hard bound; they do not guarantee any future request will pass
  validation.
- **`MacroClaim`** (the report-facing shape of one claim -- identical to
  `MacroClaimDraft` plus the fixed `content_basis` field described above).
- **`MacroAnalystReport`** -- the final report returned by `run()` for both
  a completed and an abstained run:

```python
{
  "status": "completed" | "abstained",
  "series_ids": ["FEDFUNDS"],
  "snapshot_created_at_utc": "2026-08-24T12:00:00Z",
  "source_series_count": 1,
  # "insufficient" here, on a status="abstained" report, means the
  # all-insufficient-evidence outcome specifically -- a model response was
  # received but retained zero macro claims; it is None only for a
  # *preflight*-abstained report, where no model call was ever made.
  "evidence_quality": "sufficient" | "limited" | "insufficient" | None,
  "macro_claims": [...],       # [] when abstained (either kind)
  "limitations": [...],        # [] for a preflight abstention; may be
                                # populated for the all-insufficient-evidence
                                # abstention
  "directional_assessment": "not_performed",   # always fixed
  "trade_recommendation": "not_performed",     # always fixed
  "abstained_reasons": [...]    # [] when completed; one or more
                                 # PREFLIGHT_REASON_* values for a preflight
                                 # abstention, or
                                 # ["no_sufficient_macro_evidence"] for the
                                 # all-insufficient-evidence outcome
}
```

## Full-basket coverage

Every `MacroClaim`/`MacroClaimDraft` remains bound to exactly one
`series_id` -- this task deliberately does not redesign the schema into a
grouped or cross-series claim shape. Full coverage of a multi-series request
is instead enforced entirely by requiring the right *count and set* of
single-series claims:

- **When `evidence_quality` is `"sufficient"` or `"limited"`:** every
  requested `series_id` must appear in **exactly one** retained macro claim
  -- no requested series may be omitted, no series may appear in more than
  one claim, and no unrequested series may appear. Checked by
  `_validate_coverage`/`MacroAnalystCoverageError` (see "Post-response
  validation" below).
- **When `evidence_quality` is `"insufficient"`:** `macro_claims` must be
  (and, by the existing biconditional described below, already is) fully
  empty -- this is the existing, distinct, code-controlled
  all-insufficient-evidence abstention outcome, not a partial-coverage
  state. `_validate_coverage` exempts this case entirely (it returns
  immediately when `evidence_quality == "insufficient"`), so it never
  conflicts with `_validate_claims_quality_consistency`'s biconditional.

This is a deliberate, code-controlled bound, not merely a preference. The
deterministic, all-or-nothing preflight gate (see above) already requires
*every* requested series to have a stored observation, official stored
metadata, be non-stale, non-future-dated, have a non-missing latest value,
and a recognized reporting frequency before any OpenAI request is ever
made. Consequently, by the time a response reaches `_validate_coverage`,
there is never a legitimate per-series reason for a `"sufficient"`/
`"limited"` response to omit a requested series -- so `"limited"` evidence
quality (thin evidence) still requires full coverage; it never licenses
partial coverage. A model that genuinely cannot usefully describe some
requested series must instead use the existing `"insufficient"`/
zero-claims outcome for the *entire* response, never a silent, code-
uncontrolled partial omission.

`MAX_MACRO_CLAIMS` (see "Structured output" above) is fixed to
`macro_evidence.MAX_SERIES_IDS` (`10`) specifically so that this
coverage requirement can always be structurally satisfied, however many
series (up to that existing cap) are requested in one call -- including the
seven-series Core Macro Basket (see
[docs/CORE_MACRO_BASKET.md](CORE_MACRO_BASKET.md)). Before this change,
`MAX_MACRO_CLAIMS` was a separate, smaller literal (`6`) that could never
have retained one claim per series for a seven-series request; that
contract mismatch is what this change fixes.

## Post-response validation

Before a model response is accepted as `status="completed"`:

- **Claims/quality consistency (`_validate_claims_quality_consistency`,
  raises `MacroAnalystQualityConsistencyError`)** -- runs first, since it
  decides whether the response is the all-insufficient-evidence abstention
  outcome at all. Enforces a strict biconditional: `macro_claims` is empty
  if and only if `evidence_quality == "insufficient"`. Rejects empty
  `macro_claims` paired with `"sufficient"`/`"limited"`, and rejects
  nonempty `macro_claims` paired with `"insufficient"` (an incompatible
  abstention state -- this also prevents a low-effort claim from being used
  as a fabricated placeholder while still flagging the evidence as
  insufficient). Mirrors the News Analyst's identical biconditional check.
- **Fabricated citation** -- every `evidence_id` cited in every macro claim
  must exist as a key in the exact evidence package sent for this request;
  otherwise `MacroAnalystCitationError` is raised.
- **Duplicate/missing/excessive citation** -- every macro claim must cite
  one or two evidence IDs (also schema-bounded at parse time; re-checked
  explicitly as defense in depth, since a caller constructing
  `MacroClaimDraft` via `model_construct` -- as this module's own tests do
  -- bypasses Pydantic validation entirely).
- **Series mismatch (`MacroAnalystSeriesError`)** -- a claim's `series_id`
  must be one of the requested series IDs, and every evidence ID it cites
  must belong to that exact same series (never evidence from a different
  requested series) -- both checked explicitly, since neither can be caught
  by the schema alone (the set of valid series/evidence IDs is only known at
  request time).
- **Full-basket coverage (`_validate_coverage`, raises
  `MacroAnalystCoverageError`)** -- runs immediately after series validation
  above. For a `"sufficient"`/`"limited"` response, requires the retained
  claims' `series_id` values to equal exactly the requested series set, with
  no series repeated. Exempts the `"insufficient"`/zero-claims response
  entirely. See "Full-basket coverage" above for the full rule and its
  rationale.
- **Content scope (`_validate_content_scope`, raises
  `MacroAnalystContentScopeError`)** -- a fixed, deterministic, fail-closed
  denylist rejecting language describing acceleration/deceleration,
  surprise, historical extreme (record/all-time high or low), a trend,
  correlation, causation, a policy change, or a market regime. Applied
  **unconditionally** to every macro claim's `claim_summary`/
  `conditional_mechanism` -- neither field is ever exempted, regardless of
  wording. For every model-supplied `limitation` **only**, a narrow,
  fail-closed allowance
  (`_limitation_content_scope_violation`/`_match_is_negated`) permits a
  match when it is immediately adjacent, within a small bounded word gap
  and in the same clause, to one of a fixed set of negation/insufficiency
  cues: `no`/`not`, `cannot`/`can't`, `insufficient to`, `does not`/
  `do not`, `unavailable`, `limited evidence for`, and `cannot be
  inferred/established/determined/assessed`. This exists because a
  limitation honestly stating that the evidence is *too thin* to support a
  trend/regime/causation/correlation claim -- e.g. "Six observations are
  insufficient to establish a trend," "No regime conclusion can be drawn
  from this bounded excerpt," or "The supplied evidence does not establish
  causation" -- is a desirable, truthful limitation, not a prohibited
  affirmative claim, and should not be rejected identically to one.
  `limitations` text is first split into clauses (on sentence terminators
  and on a comma before a coordinating conjunction) so a negated disclaimer
  clause never shields a separate, unnegated affirmative claim elsewhere in
  the same limitation -- e.g. "insufficient data to draw conclusions, but
  the rate is clearly following an accelerating trend" is still rejected,
  for its second clause. Every prohibited match in a `limitation` must be
  individually negated; a single unnegated match anywhere still rejects the
  whole limitation. This is a conservative, bounded pattern match, not
  general semantic understanding -- it is not proof that every possible
  unsupported statement is caught, nor that every possible honest
  limitation phrasing is recognized as negated (see "Known limitations"
  below). Increase/decrease/unchanged language is deliberately **not**
  covered here -- it is instead conditionally permitted; see
  "Comparison-claim validation" below.
- **Post-response content policy** -- after the checks above and before
  `MacroAnalystReport` is constructed, every model-authored free-text field
  is checked against the same fixed, deterministic, fail-closed denylist the
  Market Evidence Agent and News Analyst use (see
  `market_intelligence/agents/non_directional_output_policy.py`), covering
  directional predictions, bullish/bearish bias, trade
  recommendations/actions, and options-related detail. For every macro
  claim's `claim_summary`/`conditional_mechanism`, this check is applied
  **unconditionally**, via the shared module's own
  `find_prohibited_content_category`, exactly as for the Market Evidence
  Agent and News Analyst -- no exemption of any kind, even for clearly
  negated wording. For every model-supplied `limitation` **only**, a
  second, narrower, fail-closed allowance
  (`_limitation_policy_violation_category`, distinct from the content-scope
  allowance above and reusing its same clause-splitting/adjacent-negation-cue
  machinery, `_CLAUSE_SPLIT_RE`/`_match_is_negated`, rather than duplicating
  it) additionally permits a match **only** when its category is
  `directional_prediction` **and** it is immediately negated by one of the
  same fixed cue phrases the content-scope allowance uses (`no`/`not`,
  `cannot`/`can't`, `does not`/`do not`, `insufficient to`, `unavailable`,
  `limited evidence for`, or the fixed post-match wrap phrases) **and** the
  same clause contains no other prohibited-policy match at all (an unnegated
  directional-prediction match, or *any* bullish/bearish-bias, trade-
  recommendation/action, or options-detail match, negated or not) --
  e.g. "These observations do not predict future market direction" is
  allowed, but "These observations do not predict future market direction,
  but the rate is expected to rise further" is still rejected (for its
  second clause), and "This is not a recommendation to buy this stock" is
  still rejected outright (the trade-recommendation/action category is never
  exempted here, regardless of negation). The first non-exempt match raises
  `MacroAnalystPolicyError`; the rejected text is **never** included in the
  raised error or logged anywhere -- only a fixed, code-authored field name
  and category name are recorded. This allowance motivated a new public
  constant, `non_directional_output_policy.POLICY_PATTERNS` -- a **read-only**
  `Mapping` view (`types.MappingProxyType`) over the exact patterns
  `find_prohibited_content_category` itself iterates, never a duplicate copy
  and never a mutable alias `MacroAnalyst` (or any other caller) could add
  to, replace an entry in, or delete from -- so this per-match, per-clause
  check can reuse the shared module's own patterns; `find_prohibited_content_category`
  itself, and every other caller's use of it (including this agent's own
  `claim_summary`/`conditional_mechanism` checks above), is completely
  unchanged.
- **Comparison-claim validation (`_validate_comparison_claims`, raises
  `MacroAnalystComparisonError`)** -- runs after content-scope/policy so a
  deliberate policy or content-scope violation is still caught by its own
  check first. Increase/decrease/unchanged language in
  `conditional_mechanism` or any `limitation` is always rejected (never
  verifiable there). In `claim_summary`: such language on a claim citing
  fewer than two evidence IDs is rejected outright; on a claim citing
  exactly two, it is accepted only if **all** of the following hold for
  that series' `latest_change_from_previous`: `available` is `true`; the
  two cited evidence IDs equal exactly `latest_evidence_id`/
  `previous_evidence_id`; `claim_summary` states both exact observation
  dates; `claim_summary` states both exact values (checked by `Decimal`
  numeric equality, e.g. "3.63" matches a stored "3.630000" -- never string
  equality); and `claim_summary` states a single, unambiguous direction
  matching `direction` exactly.
- **Frequency-aware wording (`_validate_frequency_wording`, raises
  `MacroAnalystFrequencyWordingError`)** -- rejects point-in-time "at ...
  on `<date>`" phrasing in `claim_summary`, and rejects a `claim_summary`
  missing the required "stored `<frequency>` observation" phrase for its
  series' own official reporting frequency (`FREQUENCY_SHORT_WORDS`) --
  never assumed "monthly" for a non-monthly series.
- **Transmission-channel addressing (`_validate_transmission_channels`,
  raises `MacroAnalystTransmissionChannelError`)** -- every
  `transmission_channels` entry a claim lists must be explicitly and
  verifiably addressed (a fixed, word-boundary token pattern per channel,
  `_TRANSMISSION_CHANNEL_PATTERNS`) by that claim's `conditional_mechanism`;
  `"other"` has no such pattern and is always rejected if listed (fail
  closed on a clearly unsupported channel). **As of 2026-08-25, each
  channel's pattern also recognizes a small, carefully reviewed set of
  ordinary, economically accurate synonym tokens**: bare `rate`/`rates`,
  `interest rate(s)`, `bond yield(s)`, `Treasury yield(s)`, or `government
  bond yield(s)` for `rates`; `inflation`, `price level(s)`, `consumer
  price(s)`, `prices of goods and services`, `purchasing power`, or `cost
  of living` for `inflation`; `growth`, `economic activity`, `output`,
  `economic expansion`, or `GDP` for `growth`; "funding conditions" for
  `liquidity`; "risk aversion", "risk-taking" for `risk_appetite`; "cost of
  credit" for `credit_conditions`; plural "exchange rates" for `currency`;
  "real estate" for `housing`; "petroleum", "gasoline" for `energy` -- see
  "Known limitations" below for the live failure and offline
  false-positive analysis that motivated this. Every added token remains
  channel-specific, word-boundary-safe (`\b...\b`), and still rejects
  generic language such as "affects markets" or "has economic effects"; no
  semantic judging, fuzzy matching, or substring matching was added, and
  `"other"` is still always rejected. **Bare `price`/`prices`, bare
  `yield`/`yields`, and bare `expansion` are deliberately NOT accepted
  synonyms** -- a 2026-08-25 code-review correction found each too
  contextually ambiguous (e.g. "stock price"/"house price"/"energy
  prices" for `inflation`; dividend/earnings/crop "yield" for `rates`;
  "credit expansion"/"balance-sheet expansion" for `growth`) and replaced
  them with the explicit phrases listed above; see the first "Known
  limitations" entry below.
- **Refusal** -- if OpenAI reports `status="refusal"`,
  `MacroAnalystRefusalError` is raised. The refusal explanation text is
  never read anywhere.
- **Incomplete response** -- if OpenAI reports `status="incomplete"`,
  `MacroAnalystIncompleteError` is raised, carrying only OpenAI's
  already-sanitized fixed reason category.
- **Neither refusal nor incomplete is ever silently converted into a
  completed analysis.**

**Passing schema validation, citation validation, series validation,
content-scope validation, and the post-response content policy check is not
the same as a described observation being factually correct.** A
syntactically valid, correctly-cited, policy-passing `MacroAnalystReport`
only proves the model followed the citation, shape, series, content-scope,
and known-phrasing rules -- it is not a claim that any `claim_summary`
accurately transcribes the underlying stored value or metadata. A narrow,
deterministic factual-transcription check does now exist for the recognized
Macro claim grammar
(`market_intelligence/evaluation/macro_factual_transcription.py` -- see
[AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md)): for the two exact
controlled claim forms it recognizes (single stored observation;
increase/decrease/unchanged two-observation comparison), it checks that a
claim's stated series ID, observation date, `Decimal` value, frequency wording,
units-when-stated, and -- for a comparison -- the previous observation and
direction match the cited stored evidence, emitting `info` on an exact match,
`failure` on a mismatch, or `warning` on wording it does not recognize. This is
**not** general semantic verification, source-truth validation, agent
validation, or proof of factual accuracy: an exact match is explicitly not a
validation, unrecognized wording is deferred to human review, and no check of
this kind exists for any other agent.

An *offline* workflow to **characterize** one Macro Analyst report exists
(`market_intelligence/evaluation/macro_characterization_workflow.py`,
`scripts/characterize_macro_report.py`,
`scripts/complete_macro_characterization.py` -- see
[AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md)): it runs the
deterministic Macro factual-transcription check for every claim, produces one
pending human citation-support adjudication template per expected claim/citation
pair, and records the completed human adjudications onto the scaffold. It makes
no live request and no database access beyond a read-only lookup to resolve
cited evidence records.

**First real characterization (2026-08-28).** One accepted seven-claim Macro
Analyst report (each claim a two-observation comparison) was characterized with
this workflow: 14 expected claim/citation pairs, 14 human adjudications (one per
pair -- none missing, duplicated, or unexpected), `rubric_complete: true`,
classification tally `supported: 0` / `partially_supported: 14` /
`unsupported: 0` / `unable_to_determine: 0`, all 14 reasons
`claim_scope_exceeds_single_observation`, finding tally `info: 8` / `warning: 0`
/ `failure: 0` (seven exact factual-transcription matches; one preserved
scope-boundary information finding). Every classification was the human
reviewer's; no LLM judge generated, recommended, or changed one; no live request
and no Macro Analyst rerun occurred. All 14 pairs are `partially_supported`
because a two-observation comparison depends on both cited observations while
each individual pair carries only one. This **satisfies P0-7 and closes Phase
0** -- it is **not** a claim that any `claim_summary` is factually accurate or
that the Macro Analyst is validated, repeatable, or profitable. The real
artifacts are gitignored under `data/evaluations/local/` and are not committed.

## Error categories

All errors are sanitized `RuntimeError` subclasses under
`MacroAnalystAgentError`; none ever include a database path, SQL text, API
key, raw provider output, or raw evidence content. Each also exposes a
fixed, sanitized `category` string class attribute (mirroring
`OpenAIStructuredError.category`/`MarketEvidenceAgentError.category`/
`NewsAnalystAgentError.category`) so a failure's class can be identified
programmatically without parsing message text;
`scripts/run_macro_analyst.py` includes it in its sanitized `agent_error`
JSON output.

| Exception | `category` | Cause |
|---|---|---|
| `MacroAnalystValidationError` | `invalid_input` | Invalid `series_ids`, before any database or model access |
| `MacroAnalystRefusalError` | `refusal` | The model refused the request |
| `MacroAnalystIncompleteError` | `incomplete` | The model's response was incomplete |
| `MacroAnalystCitationError` | `citation_invalid` | Missing, fabricated, duplicated, or excessive evidence-ID citation |
| `MacroAnalystSeriesError` | `series_invalid` | A claimed series not among those requested, or evidence cited from the wrong series |
| `MacroAnalystCoverageError` | `coverage_invalid` | A `"sufficient"`/`"limited"` response omitted a requested series, or claimed the same series more than once |
| `MacroAnalystQualityConsistencyError` | `quality_consistency_invalid` | `macro_claims` being empty/nonempty was incompatible with `evidence_quality` |
| `MacroAnalystContentScopeError` | `content_scope_invalid` | Model-authored free text described acceleration/deceleration, surprise, historical extreme, a trend, correlation, causation, a policy change, or a market regime |
| `MacroAnalystPolicyError` | `policy_violation` | Model-authored free text failed the shared post-response content policy check |
| `MacroAnalystComparisonError` | `comparison_invalid` | Increase/decrease/unchanged language used without satisfying every condition for a valid two-observation comparison claim |
| `MacroAnalystFrequencyWordingError` | `frequency_wording_invalid` | Point-in-time "at ... on `<date>`" phrasing, or missing the required frequency-aware "stored `<frequency>` observation" wording |
| `MacroAnalystTransmissionChannelError` | `transmission_channel_invalid` | A listed `transmission_channels` entry not addressed by `conditional_mechanism` (including `"other"`, always rejected) |
| `MacroAnalystUnexpectedError` | `unexpected_error` | Any other unexpected failure |

A sanitized `OpenAIStructuredError` subclass also propagates unchanged from
`run()` -- it is already fully sanitized by `OpenAIStructuredClient`,
including its own `category` attribute. A sanitized `MacroEvidenceError`
from the underlying snapshot builder (e.g. a storage read failure) also
propagates unchanged.

## Command-line usage

```
python scripts/run_macro_analyst.py
python scripts/run_macro_analyst.py --series FEDFUNDS
python scripts/run_macro_analyst.py --series FEDFUNDS --series UNRATE
python scripts/run_macro_analyst.py --series FEDFUNDS --execute
python scripts/run_macro_analyst.py --series FEDFUNDS --recent-observations-limit 12
```

`--series` is repeatable and defaults to `FEDFUNDS` (via
`DEFAULT_SERIES_IDS`) when omitted entirely. `--recent-observations-limit`
is forwarded as-is to `MacroEvidenceBuilder.build_snapshot` (see
[docs/MACRO_EVIDENCE_SNAPSHOT.md](MACRO_EVIDENCE_SNAPSHOT.md)) and defaults
to `DEFAULT_RECENT_OBSERVATIONS_LIMIT` (`6`) when omitted.

**Default mode is a dry run**: it builds the local macro-evidence snapshot
and evaluates preflight only, making **zero OpenAI requests**, and prints
only:

```json
{
  "mode": "dry_run",
  "eligible": true,
  "reasons": [],
  "series_ids": ["FEDFUNDS"],
  "series_count": 1,
  "flags": {
    "missing_series": [], "stale_series": [], "future_dated_series": [],
    "missing_metadata_series": [], "latest_missing_series": [],
    "no_evidence_id_series": [], "frequency_unrecognized_series": []
  }
}
```

The evidence package contents (official titles, values) are never printed in
dry-run mode -- only counts and per-series flags. The absence of a
`model_metadata` key in dry-run output is itself how "zero model metadata"
is satisfied -- the key simply never appears.

**`--execute` is required for a paid model request**, and only makes one if
the all-or-nothing preflight passes; an ineligible `--execute` run still
makes zero model calls and prints a truthful `status="abstained"` report
(exit code `0` -- this is not an error). Execute output prints the validated
`MacroAnalystReport` plus sanitized model/token metadata (model name,
`input_tokens`, `output_tokens`, `total_tokens` only):

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
entirely. Neither the raw provider response, a response ID, the full
evidence payload, credentials, nor a database path is ever printed by this
CLI, in either mode. Any raised exception is caught by a final defensive
handler and reported only as `{"error": "unexpected_error"}` -- never a raw
exception type, message, or traceback.

## Testing

`market_intelligence/tests/test_macro_analyst.py`,
`test_run_macro_analyst.py`, and `test_macro_analyst_eval_fixtures.py` cover
(180 tests total across the three files, up from 155 after the narrow
transmission-channel synonym/word-boundary hardening described below -- see
"Known limitations" below): an eligible FEDFUNDS snapshot; every preflight abstention
reason with zero model calls (missing, missing metadata, stale, future-
dated, `latest_is_missing`, no evidence ID, requested-series mismatch, and
now also `series_frequency_unrecognized`, plus every known
`FREQUENCY_SHORT_WORDS` code being eligible), the all-or-nothing behavior
across multiple requested series, and multiple simultaneous reasons; a valid
synthetic structured response producing a completed report; the zero-claim
`evidence_quality="insufficient"` abstention (with `model_metadata` still
populated, unlike a preflight abstention); every contradictory
evidence-quality/claim combination (parametrized over
`"sufficient"`/`"limited"` paired with empty claims, and nonempty claims
paired with `"insufficient"`); fabricated, duplicate, missing, and excessive
citation (the latter three via `model_construct` to bypass the schema bound
and exercise the explicit defense-in-depth checks, now including a
genuinely excessive 3-ID case since the hard bound is `2`); a series ID not
among those requested; evidence cited from the wrong series in a two-series
scenario; a valid two-series completed response; **full-basket coverage
(`_validate_coverage`/`MacroAnalystCoverageError`)**: `MAX_MACRO_CLAIMS`
proven equal to `macro_evidence.MAX_SERIES_IDS`; a full seven-series
Core-Macro-Basket-shaped response (`FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`,
`UNRATE`, `INDPRO`, `GDPC1`) accepted with exactly one claim per series and
no duplicate series among the retained claims; the identical seven-series
response accepted with its claims reordered (coverage depends only on the
covered series *set*, never claim order); a response omitting one requested
series rejected; a response claiming one series twice (while omitting
another) rejected; an extra claim for a series that was never requested
rejected (via the existing series-validation check, which runs first); full
coverage required and accepted for **both** `"sufficient"` and `"limited"`
evidence quality (parametrized), and partial coverage rejected for **both**
(parametrized) -- proving `"limited"` never licenses omitting a requested
series; the existing zero-claim `"insufficient"` abstention re-verified
unaffected for a full seven-series request (still exactly one model call,
still a code-controlled abstention, never a fabricated claim); a coverage
violation making zero retry calls; that the coverage error never echoes
model-authored claim text; that the coverage error carries the
`coverage_invalid` category; and a dedicated test that builds the **real**
production `MacroAnalystModelAnalysis`/`MacroClaimDraft` schema (not the
fake stand-ins used elsewhere in this file) with a valid seven-series
response and round-trips it through the real `OpenAIStructuredClient`
(only its SDK transport faked), proving the schema and client stay
compatible for a full-basket response; a parametrized sweep of
forbidden acceleration/surprise/historical-extreme/trend/correlation/
causation/policy-change/regime phrases rejected in `claim_summary`, plus
dedicated cases for `conditional_mechanism` and `limitations`, that the
rejection error never echoes the rejected text, and that purely factual,
snapshot-scoped wording is accepted; that an adversarial series title stays
confined to the evidence payload and never appears in the instructions
actually sent to the model; the shared non-directional output policy
(directional prediction, bullish/bearish bias, trade recommendation,
options detail, each in a different field, and that the raised error never
echoes the rejected text); model refusal, an incomplete response, and
`OpenAIStructuredError` subclasses (timeout, parse failure) propagating
unchanged; an unexpected model-client failure becoming a sanitized error;
invalid/duplicate/excessive `series_ids` rejected before any builder or
model call; every advisory output budget present in `AGENT_INSTRUCTIONS`
and numerically below its hard Pydantic maximum, and that
`AGENT_INSTRUCTIONS` states the mandatory full-coverage requirement (there
is deliberately no "preferred claim count" advisory left to test, since
coverage is now mandatory rather than a preference); that the model-facing
schemas contain no sentiment/probability/confidence/forecast/
`directional_assessment`/`trade_recommendation`/`content_basis` field at
all; that `latest_change_from_previous`/`frequency_short` are present in the
built evidence package **and that `recent_observations` is deliberately
absent from it entirely** (see "Model-facing evidence compaction" above),
including a dedicated test asserting the exact preserved field set for a
series entry; that a real seven-series production evidence package (the
same request shape that failed live) passes the real
`OpenAIStructuredClient`'s node/byte evidence-size validation end to end
(only its SDK transport faked), with the measured node/byte counts asserted
against the client's own `MAX_EVIDENCE_NODES`/`MAX_EVIDENCE_BYTES`; that a
synthetic worst-case ten-series request (`MAX_SERIES_IDS`, conservative
oversized placeholder metadata) also fits those same limits; that a full
seven-series response mixing plain-latest and fully supported
two-observation comparison claims still passes every post-response
validator after the compaction; and that a future evidence package that
would still exceed the client's size limits is rejected locally by the real
`OpenAIStructuredClient` with zero SDK calls; a fully
supported two-observation comparison claim accepted, and rejection of a
one-citation comparison attempt, a wrong evidence-ID pair, an unavailable
comparison, a missing date, a mismatched value, a wrong stated direction,
change words in `conditional_mechanism`/`limitations` (always rejected),
and that the rejection error never echoes rejected text; point-in-time
phrasing rejected, the required frequency phrase missing rejected, a
non-monthly series correctly using its own frequency word, and that the
error never echoes rejected text; a transmission channel addressed by
`conditional_mechanism` accepted, an unaddressed channel rejected
(reproducing the manual-review finding described in `PROJECT_STATE.md`), a
missing `conditional_mechanism` rejected, `"other"` always rejected, and
that the error never echoes rejected text; every allowed channel accepted
via both its pre-existing baseline mechanism and a representative
economically accurate synonym (reproducing the plausible false-positive
classes described in "Known limitations" below, now fixed), generic
language ("affects markets"/"has economic effects") and an unrelated
mechanism still rejected, a word-boundary safety case proving a "rate"
substring inside unrelated words never satisfies the `rates` token,
multiple channels where one is addressed via a synonym and the other is
not still rejected, exactly one model call with no automatic retry after a
transmission-channel rejection, a synonym-based rejection never echoing
rejected text, and `AGENT_INSTRUCTIONS` stating both the
omit-if-unaddressed instruction and the generic-language exclusion; `recent_observations_limit`
forwarding from the agent to the evidence builder and from the CLI to the
agent (both dry-run and `--execute`, including the CLI default); and the
narrow negated-limitation content-scope allowance described above -- an
accepted negated-trend limitation, an accepted no-regime limitation, an
accepted no-causation/no-correlation limitation, an accepted
insufficient-history limitation, an accepted limitation with multiple
independently negated clauses, that an accepted negated limitation still
makes exactly one model call, a parametrized sweep of affirmative
(unnegated) trend/regime/causation limitations still rejected, a
disclaimer-then-affirmative-claim limitation still rejected with no retry
following the rejection, that the rejection error never echoes rejected
text, that identical negated wording is still rejected outright in
`claim_summary` and `conditional_mechanism` (no exemption), and that the
shared non-directional output policy still fires on a limitation whose
negated scope language separately passes the content-scope allowance; and
the narrow negated-directional-prediction disclaimer allowance for the
shared output policy described above -- an accepted "does not predict
future market direction" limitation, with exactly one model call made; an
affirmative (unnegated) prediction limitation still rejected
(`category=directional_prediction` present in the error); a mixed
disclaimer-then-affirmative-prediction limitation (two clauses) still
rejected with no retry, and that its error never echoes either clause's
text; identical negated directional wording still rejected outright in
`claim_summary` and in `conditional_mechanism` (no exemption there); and
that a negated bullish/bearish-bias limitation and a negated trade-
recommendation limitation are each still rejected with their own correct
category, proving the allowance is scoped to the directional-prediction
category only. `test_non_directional_output_policy.py` additionally covers:
the new public `POLICY_PATTERNS` constant covering exactly the same fixed
categories, in the same fixed order, as `find_prohibited_content_category`;
and a local reproduction, directly against the shared, unmodified
`find_prohibited_content_category`, that a clearly negated directional
disclaimer ("does not predict future market direction") is still flagged
`CATEGORY_DIRECTIONAL_PREDICTION` by that shared function itself -- proving
this fix's narrow allowance lives entirely in `MacroAnalyst`'s own
limitations-only post-processing, never in the shared module every agent
calls. `test_news_analyst.py` additionally covers that the same negated
directional disclaimer is still rejected by the News Analyst's own
`limitations` handling, confirming the shared policy's behavior for other
agents is unchanged by this fix.

`OpenAIStructuredClient` is always injected as a fake recording calls and
returning/raising canned `StructuredOutputResult` values (mirroring
`test_market_evidence_agent.py`/`test_news_analyst.py`'s injection pattern)
-- no real SDK client is ever constructed and no network call is ever made.
`MacroEvidenceBuilder` is replaced with a small fake builder returning a
fixed, hand-authored snapshot dict matching the documented contract shape,
so these tests never open a real database either.

`test_run_macro_analyst.py` covers the CLI: dry-run defaults to `FEDFUNDS`
when `--series` is omitted, `--series` is repeatable, dry-run prints
eligibility/reasons/series_ids/series_count/flags and never the evidence
payload or `model_metadata` key, invalid-series rejection, unknown-argument
rejection, `--execute` gating (calls `run()` not `build_preflight()`
directly), the validated report plus sanitized metadata excluding
`response_id`, an ineligible `--execute` run's zero-token abstained report,
sanitized refusal/incomplete/`agent_error` (with `category`) output, and
that a raw, unsanitized exception (containing a fake path and SQL text) is
never leaked by the final defensive exception handler in either mode.

`test_macro_analyst_eval_fixtures.py` is a small, local, deterministic table
of representative scenarios (typical/missing/stale/missing-metadata/
adversarial-metadata) asserting the agent's deterministic gating behavior
for each. **These are ordinary local pytest tests, not the OpenAI Evals
API**, and they say nothing about the quality of a real model response --
only that this agent's own deterministic logic (preflight, evidence
packaging, instruction/evidence separation) behaves as documented.

**Passing this test suite demonstrates the deterministic scaffolding around
the model call is correct. It does not validate the model's factual
accuracy, and no claim of validated factual accuracy is made anywhere in
this repository** (see [CLAUDE.md](../CLAUDE.md)/[AGENTS.md](../AGENTS.md)'s
"Evidence and Claims" section). The existing Market Evidence Agent and News
Analyst test suites were re-run unchanged and still pass -- this addition
touches only `market_intelligence/agents/macro_analyst.py`,
`scripts/run_macro_analyst.py`, and their own new tests/docs.

## Known limitations

- **First successfully accepted seven-series `--execute` run (2026-08-26)
  -- the Macro Analyst live-validation milestone.** After the model-facing
  evidence compaction fix and the sequence of rejected seven-series
  attempts recorded below and in `PROJECT_STATE.md` (items 26-33), a
  separately authorized `--execute` run against the complete seven-series
  Core Macro Basket (`FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`,
  `INDPRO`, `GDPC1`) was accepted end to end for the first time:
  `status="completed"`, `source_series_count=7`,
  `evidence_quality="sufficient"`, exactly one retained macro claim per
  requested series, `limitations` empty, `transmission_channels` empty for
  every claim, `directional_assessment` and `trade_recommendation` both the
  fixed `"not_performed"` literal, `abstained_reasons` empty, model
  `gpt-5-mini`, `input_tokens=3829`, `output_tokens=3272`,
  `total_tokens=7101`, exactly one OpenAI request and no retry. The empty
  `transmission_channels` lists were valid precisely because no claim
  asserted an unsupported transmission mechanism -- with no channel listed
  and no mechanism claimed, `_validate_transmission_channels` has nothing
  to substantiate or reject. **Every `OpenAIStructuredClient`
  structured-output validation step and every Macro Analyst post-response
  validator passed** (`_validate_claims_quality_consistency`,
  `_validate_citations_and_series`, `_validate_coverage`,
  `_validate_content_scope`, `_validate_comparison_claims`,
  `_validate_frequency_wording`, `_validate_transmission_channels`, and the
  shared non-directional output policy check). No evidence IDs, exact
  observation values or dates, the snapshot timestamp, or the model
  report's free text are reproduced here or elsewhere in this repository.
  **This is one accepted run.** It demonstrates the deterministic
  scaffolding can now accept a complete seven-series response end to end;
  it is **not** a validated evaluation methodology and **not** a claim that
  any `claim_summary` is factually accurate -- no automated evaluation of
  factual accuracy exists in this repository (see "Post-response
  validation" above and [CLAUDE.md](../CLAUDE.md)/[AGENTS.md](../AGENTS.md)).
  The earlier rejected attempts below are preserved unchanged as honest
  records and are not reclassified as successes. See `PROJECT_STATE.md`
  item 34 for the full sanitized record.

- **Live seven-series `--execute` attempt failed
  `response_validation_failed` before any post-response validator ran, and
  the structured-output validation diagnostics this motivated (2026-08-25).**
  A separately authorized seven-series Core Macro Basket dry run against the
  real local database passed the deterministic preflight (`eligible: true`,
  every flag list empty). A separately authorized `--execute` attempt was
  then made against the same seven series (`FEDFUNDS`, `GS10`, `CPIAUCSL`,
  `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`): it passed preflight and one paid
  OpenAI request reached OpenAI, but the response failed OpenAI SDK's
  client-side structured-output re-validation against
  `MacroAnalystModelAnalysis` -- `agent_error`, `detail: "OpenAI response
  failed structured-output validation."`, `category:
  response_validation_failed`. **This failure occurs entirely inside
  `OpenAIStructuredClient.generate()`, before this agent's own
  `_validate_claims_quality_consistency`, `_validate_citations_and_series`,
  `_validate_coverage`, `_validate_content_scope`,
  `_validate_comparison_claims`, `_validate_frequency_wording`,
  `_validate_transmission_channels`, or the shared non-directional output
  policy check ever runs -- none of them saw this response at all. No
  report was accepted from this attempt, and no retry was made.** This is a
  distinct failure from the seven-series `transmission_channel_invalid`
  rejection documented immediately below: that earlier attempt's response
  *did* reach and get rejected by a post-response validator; this attempt's
  response never reached one.

  Because `OpenAIStructuredClient` never captures or logs raw model output,
  and because the bounded, sanitized structured-output validation
  diagnostics described in
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md) did not
  yet exist at the time of this attempt, **the exact schema field/value
  that failed re-validation for this specific occurrence is unavailable and
  permanently unrecoverable.** No bound of `MacroAnalystModelAnalysis`/
  `MacroClaimDraft` was changed or is claimed to be the cause -- exceeding
  one of the schema's Pydantic-only `minLength`/`maxLength`/`minItems`/
  `maxItems` bounds (e.g. `claim_summary`'s 400-character maximum, or
  `macro_claims`' 10-item maximum) remains one plausible, locally
  reproducible failure mode, reproduced offline with a real,
  locally-constructed seven-series stress fixture in
  `market_intelligence/tests/test_openai_structured.py` -- **not** an
  established proven cause of this specific live attempt.

  In direct response, `OpenAIStructuredClient.generate()`'s existing
  `except pydantic.ValidationError` handling (the only place this category
  is ever raised) was extended to attach bounded, sanitized diagnostics
  (`ValidationDiagnostics`: an `issue_count`, and up to five deduplicated
  `(field_path, category)` pairs derived from `ValidationError.errors()`,
  where `field_path` is validated against the exact `output_model` schema's
  own real, declared fields -- not merely identifier syntax, a distinction
  hardened on 2026-08-26 after a follow-up review; see
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)'s
  "Security correction" note) to the raised `OpenAIParseFailureError`, and
  `scripts/run_macro_analyst.py`'s sanitized `agent_error` JSON output was
  extended to include them when available -- see
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)'s
  "Structured-output validation diagnostics" section for the full,
  sanitized contract (never a model-authored value, raw evidence, a
  credential, a raw response, a URL, or a raw exception/type/message).
  **No hard Pydantic bound, citation check, coverage check, content-scope
  check, comparison check, frequency-wording check, transmission-channel
  check, or output-policy check was weakened, and the existing
  `MacroAnalystModelAnalysis`/`MacroAnalystSchema` bounds were not changed
  or even speculated about as part of this hardening** -- this change adds
  observability for a *future* occurrence of this same category only. No
  further live OpenAI request was made to test the new diagnostics against
  a real failure; they have been validated only by offline, deterministic
  tests using synthetic `pydantic.ValidationError` instances, including a
  locally constructed seven-series `MacroAnalystModelAnalysis` stress
  fixture (a valid seven-claim response still parses successfully; separate
  invalid fixtures reproduce an oversized `claim_summary` and excessive
  `macro_claims` as reproducible possibilities, not proof of this attempt's
  cause). See `PROJECT_STATE.md` for the full, dated record.

- **Code-review correction: three ambiguous bare transmission-channel
  synonym tokens narrowed to explicit economic phrases (2026-08-25, code/
  tests/docs only -- no live FRED/OpenAI request, no migration, no
  configuration/dependency/`.env` change, and no write to the real local
  database).** A review of the synonym expansion described in the entry
  immediately below found that three of its bare tokens were themselves
  too permissive: bare `\bprices?\b` (added for `inflation`) also matches
  "stock price," "house price," and "energy prices," none of which is an
  inflation concept; bare `\byields?\b` (added for `rates`) also matches
  dividend yield, earnings yield, and crop yield, none of which is a rates
  concept; and bare `\bexpansion\b` (added for `growth`) also matches
  "credit expansion" and "balance-sheet expansion," neither of which is a
  growth concept on its own. Each of these three tokens was replaced in
  `_TRANSMISSION_CHANNEL_PATTERNS` with the explicit phrases it should have
  required from the start: `price level(s)`, `consumer price(s)`, `prices
  of goods and services` (alongside the already-specific `purchasing
  power`/`cost of living`) for `inflation`; `bond yield(s)`, `Treasury
  yield(s)`, `government bond yield(s)` (alongside the already-accepted
  bare `rate`/`rates` and `interest rate(s)`, neither of which was
  ambiguous) for `rates`; and `economic expansion` (alongside the
  already-specific `growth`/`economic activity`/`output`/`GDP`) for
  `growth`. `AGENT_INSTRUCTIONS` was updated to match exactly. Every other
  synonym token added by the prior change (e.g. "funding conditions",
  "risk aversion", "risk-taking", "cost of credit", plural "exchange
  rates", "real estate", "petroleum", "gasoline") was reviewed for the same
  ambiguity and found to already be a channel-specific phrase, so it is
  unchanged. This is strictly a narrowing: no previously rejected mechanism
  is newly accepted, and every mechanism that relied only on an
  intentionally-retained token (e.g. bare `rate`/`rates`, `growth`,
  `inflation`) is unaffected. 21 new focused tests were added to
  `market_intelligence/tests/test_macro_analyst.py` (176 tests in that
  file, up from 155; 2074 in the full repository suite, up from 2053),
  covering: explicit inflation phrases (`price level`, `consumer prices`,
  `prices of goods and services`, `purchasing power`, `cost of living`,
  bare `inflation`) accepted; "stock price," "house price," and "energy
  prices" rejected for `inflation`; explicit `rates` phrases (`rate`,
  `interest rate`, `bond yield`, `Treasury yield`, `government bond
  yield`) accepted; dividend yield, earnings yield, and crop yield rejected
  for `rates`; "economic expansion" accepted for `growth`; "credit
  expansion" and "balance-sheet expansion" rejected for `growth`; and that
  `AGENT_INSTRUCTIONS` no longer advertises the removed bare synonyms and
  does state the replacement explicit phrases. `python -m pytest` (2074
  passed), `python -m ruff check .` (all checks passed), and `git diff
  --check` (no whitespace errors) were all run as part of this change and
  pass. The existing generic-language-rejected, unrelated-mechanism-
  rejected, multi-channel-partial-coverage, word-boundary, `"other"`-
  always-rejected, exactly-one-model-call-with-no-retry, and
  never-echoes-rejected-text tests from the entry below were re-run
  unchanged and still pass, confirming this correction touches only the
  three flagged tokens and `AGENT_INSTRUCTIONS`' wording.

- **Live seven-series `--execute` attempt rejected as
  `transmission_channel_invalid`, and the narrow synonym/word-boundary
  hardening this motivated (2026-08-25).** A separately authorized
  seven-series Core Macro Basket dry run against the real local database
  passed the deterministic preflight (`eligible: true`, every flag list
  empty). A separately authorized `--execute` attempt was then made: it
  passed preflight and reached OpenAI, and **exactly one paid OpenAI
  request was sent and one structured response was received**, but that
  response was rejected by `_validate_transmission_channels` with
  `MacroAnalystTransmissionChannelError`
  (`category=transmission_channel_invalid`) -- `"Model output listed a
  transmission_channel that conditional_mechanism did not clearly and
  verifiably address."` **No report was accepted from this attempt, and no
  retry was made.** Per this agent's sanitization contract, the
  model-authored `transmission_channels`/`conditional_mechanism` text that
  triggered the rejection was never captured or recorded, so **the exact
  live channel, mechanism wording, and root cause are not known and are
  not reproduced anywhere in this repository.**

  Offline analysis of `_TRANSMISSION_CHANNEL_PATTERNS` as it existed at the
  time found several plausible, locally reproducible false-positive
  classes -- ordinary, economically accurate ways of describing an allowed
  channel that the prior, narrower tokens did not match: "yield(s)" for
  `rates` (e.g. "shifts in government bond yields"); singular "price" for
  `inflation` (the prior pattern required plural "prices" or the exact
  phrase "price level"); "expansion" for `growth`; "real estate" for
  `housing`; "risk aversion"/"risk-taking" for `risk_appetite`; "cost of
  credit" for `credit_conditions`; "funding conditions" for `liquidity`;
  and "petroleum"/"gasoline" for `energy`. Offline analysis also found a
  genuine word-boundary bug, independent of vocabulary breadth: the
  `currency` channel's pattern required the literal singular
  `\bexchange\s+rate\b`, which never matches the equally common plural
  "exchange rates" -- the trailing "s" sits immediately after "rate" with
  no intervening non-word character, so the word boundary required right
  after "rate" is never satisfied there. **Each of these is offline
  analysis of a plausible failure class, reproduced only with locally
  authored test fixtures in `test_macro_analyst.py` -- none of it is proof
  of the exact wording, channel, or cause rejected in the live attempt.**

  The fix, made entirely in `_TRANSMISSION_CHANNEL_PATTERNS` and
  `AGENT_INSTRUCTIONS` in `market_intelligence/agents/macro_analyst.py`:
  each channel's existing pattern gained a small, carefully reviewed set of
  additional word-boundary-safe synonym tokens (listed in
  "Transmission-channel addressing" above), and the `currency` pattern was
  corrected to `\bexchange\s+rates?\b` so both singular and plural match.
  `AGENT_INSTRUCTIONS` was also strengthened to enumerate the concrete
  economic wording that addresses each specific channel, state explicitly
  that generic language such as "affects markets" or "has economic
  effects" does not count as addressing any channel, and instruct the
  model to omit a channel entirely (never guess or include it anyway) when
  it cannot explicitly and directly explain that channel this way. **No
  semantic model judging, retry, truncation, or fuzzy/substring matching
  was added anywhere** -- every added token is still a fixed,
  case-insensitive, word-boundary regex alternative checked deterministically
  by the same unchanged `_validate_transmission_channels` function;
  `"other"` is still always rejected; and every other validator (citation,
  series, full-basket coverage, content-scope, comparison-claim,
  frequency-wording, and the shared non-directional output policy) is
  completely unchanged and re-verified passing. 25 new focused tests were
  added to `market_intelligence/tests/test_macro_analyst.py` (155 tests in
  that file, up from 130; 180 across the three Macro Analyst test files,
  up from 155; 2053 in the full repository suite, up from 2028), covering:
  every allowed channel accepted via its pre-existing baseline mechanism;
  every allowed channel accepted via a representative economic synonym
  (reproducing the plausible false-positive classes above, now fixed);
  generic language ("affects markets"/"has economic effects") still
  rejected; an unrelated mechanism still rejected; a word-boundary safety
  case proving "rate" appearing only as a substring of unrelated words
  ("moderate", "corporate") never satisfies the `rates` token; multiple
  listed channels where one is addressed via a synonym and the other is
  not, still rejected; exactly one model call with no automatic retry after
  a transmission-channel rejection; a dedicated case that a synonym-based
  rejection still never echoes the rejected mechanism text; and that
  `AGENT_INSTRUCTIONS` states both the omit-if-unaddressed instruction and
  the generic-language exclusion. The existing Market Evidence Agent, News
  Analyst, and shared `non_directional_output_policy` test suites were
  re-run unchanged and still pass, confirming this fix touches only the
  Macro Analyst's own transmission-channel patterns and instructions. **As
  of this change, this fix has not been exercised against a live OpenAI
  response** -- no further live `--execute` attempt has been made. It does
  not establish that a real model response will phrase a mechanism using
  one of these newly recognized tokens, that the live rejection above
  shared the same root cause as any locally reproduced class described
  here, or that any described observation is factually accurate. See
  `PROJECT_STATE.md` for the full sanitized record of both the live
  attempt and this fix.

- **First live seven-series `--execute` attempt after the model-facing
  evidence compaction fix, rejected by the shared post-response content
  policy, and the narrow negated-directional-prediction disclaimer allowance
  this motivated (2026-08-25).** After the model-facing evidence compaction
  fix described immediately below, the first authorized post-compaction
  seven-series Core Macro Basket `--execute` attempt passed the
  deterministic preflight, and its structured response reached local
  post-response content policy validation -- **exactly one paid OpenAI
  request was made and one structured response was received** -- but that
  response was rejected by the shared non-directional output policy check
  (`_enforce_output_policy`) at `field=limitations[1]`,
  `category=policy_violation` (the underlying shared-policy category
  `directional_prediction`). **No report was accepted, and no retry was
  made.** Per this agent's sanitization contract, the model-authored text
  that triggered the rejection was never recorded, so **the exact live
  wording is not known and is not reproduced anywhere in this repository.**

  Offline analysis found a plausible, locally reproducible false-positive
  class: the shared policy's `find_prohibited_content_category` (see
  `market_intelligence/agents/non_directional_output_policy.py`) matches
  known directional-prediction phrasing (e.g. "predict", "forecast")
  unconditionally, with no negation awareness of any kind -- so a
  `limitation` that honestly and explicitly *disclaims* prediction, e.g.
  "These observations do not predict future market direction," would also
  be flagged, identically to an affirmative directional claim. This is
  reproduced directly in
  `market_intelligence/tests/test_non_directional_output_policy.py`
  (`test_negated_directional_disclaimer_is_still_flagged_by_the_raw_function`).
  **This is offline analysis of a plausible failure class reproduced with a
  locally authored test fixture -- it is not proof of the exact wording
  rejected in that live run.**

  The fix, in this same change, made entirely in
  `market_intelligence/agents/macro_analyst.py` and its tests/docs: a new,
  narrow, fail-closed allowance,
  `_limitation_policy_violation_category`, applies **only** to
  model-supplied `limitations`, reusing (never duplicating) the existing
  content-scope allowance's clause-splitting/adjacent-negation-cue machinery
  (`_CLAUSE_SPLIT_RE`/`_match_is_negated`) already added for the item below.
  A `limitations` match is allowed **only** when its category is
  `directional_prediction` (never bullish/bearish bias, trade
  recommendation/action, or options detail, negated or not) **and** it is
  immediately negated by one of the same fixed cue phrases the content-scope
  allowance uses **and** the same clause contains no other prohibited-policy
  match at all -- so a mixed clause such as "...but the rate is expected to
  rise further" still fails closed. `claim_summary`/`conditional_mechanism`
  are completely unaffected -- they continue to use the shared module's
  plain, unconditional `find_prohibited_content_category` with zero
  exemption, exactly as for the Market Evidence Agent and News Analyst.
  `AGENT_INSTRUCTIONS` was also updated to tell the model that a
  `limitation` should describe a bounded evidence gap directly and avoid
  mentioning predictions, market direction, trades, or options at all, even
  as a disclaimer -- reducing how often the model needs this allowance at
  all, without weakening or replacing the deterministic check.

  A new public constant, `non_directional_output_policy.POLICY_PATTERNS` --
  a **read-only** `Mapping` view (`types.MappingProxyType`) over the exact
  patterns `find_prohibited_content_category` already iterates, never a
  duplicate copy and never a mutable alias, so no caller can add, replace,
  or delete an entry through it -- was added so this per-match, per-clause
  check can reuse the shared module's own fixed patterns.
  `find_prohibited_content_category` itself is completely unchanged, and
  every other caller (the Market Evidence Agent, the News Analyst, and this
  agent's own `claim_summary`/`conditional_mechanism` checks) is unaffected
  -- confirmed by a dedicated test
  (`test_run_rejects_negated_directional_language_in_limitation` in
  `test_news_analyst.py`) that the same negated directional disclaimer is
  still rejected outright by the News Analyst's unmodified `limitations`
  handling. No citation, series, full-basket-coverage, comparison-claim,
  frequency-wording, transmission-channel, or content-scope validation was
  weakened, and the existing negated content-scope allowance (trend/regime/
  correlation/causation, described in the item below) is completely
  unchanged by this fix -- both allowances are narrow, independent, and
  scoped to different fixed category sets. **As of this change, this fix has
  not been exercised against a live OpenAI response** -- it has been
  validated only by offline, deterministic tests using fake evidence-
  builder/model-client stand-ins, mirroring how the item below was
  validated. See `PROJECT_STATE.md` for the full sanitized record of both
  the live failure and this fix.

- **Model-facing evidence compaction fix (2026-08-25), and the failed
  seven-series `--execute` attempt that motivated it.** After the
  full-basket coverage fix described immediately below, the first
  authorized seven-series Core Macro Basket `--execute` attempt (`FEDFUNDS`,
  `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`,
  `recent_observations_limit=6`) passed the deterministic preflight but
  failed locally, before any provider request was sent, with
  `OpenAIInvalidRequestError` (`category=request_invalid`, `"Invalid
  evidence: exceeds the maximum node count."`) -- **zero provider requests
  and zero tokens were spent for this attempt; no analysis was accepted.**
  Offline analysis found the root cause: the model-facing evidence payload
  included every `recent_observations` row for every requested series, even
  though no post-response validator ever reads that field -- every claim
  this agent can validate is scoped to either the single latest stored
  observation or one exact two-observation comparison, both already fully
  covered by fields already sent independently of `recent_observations`
  (`observation_date`/`latest_value`/`latest_change_from_previous`). Across
  seven series this redundant history pushed the serialized evidence tree to
  501 nodes -- one over `OpenAIStructuredClient`'s existing, unchanged
  500-node `MAX_EVIDENCE_NODES` cap. The fix, in this same change:
  `MacroAnalyst._build_model_evidence` now omits `recent_observations` from
  the model-facing payload entirely (see "Model-facing evidence compaction"
  above) -- `MacroEvidenceBuilder`'s own snapshot, used for local
  deterministic evidence and audits, is unchanged and still retains it. This
  reduces the real seven-series evidence package to 200 nodes / 5,643 bytes
  and a synthetic worst-case ten-series request to 284 nodes / 11,495
  bytes -- both comfortably under `MAX_EVIDENCE_NODES` (500) and
  `MAX_EVIDENCE_BYTES` (32,000), with no global limit raised and no
  comparison, citation, coverage, frequency-wording, channel, content-scope,
  or non-directional-policy validation weakened. **As of this change, this
  fix has not been exercised against a live OpenAI response for the
  seven-series basket or any other multi-series request** -- it has been
  validated only by offline, deterministic tests, including one that sends
  the real, production seven-series evidence package through the real
  `OpenAIStructuredClient`'s evidence-size validation (only its SDK
  transport faked, never a live network call). See `PROJECT_STATE.md` for
  the full sanitized record.

- **Full-basket coverage fix (2026-08-25), and the seven-series dry run that
  motivated it.** After the frequency-aware macro-evidence staleness fix
  (see `PROJECT_STATE.md`), a read-only dry run of the Macro Analyst against
  the complete, approved seven-series Core Macro Basket
  (`FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`) was
  run against the real local database and succeeded: `eligible: true`, all
  seven series echoed back in `series_ids`, and every flag list (`missing`,
  `stale`, `future_dated`, `missing_metadata`, `latest_missing`,
  `no_evidence_id`, `frequency_unrecognized`) empty. This is a dry run only
  -- it makes **zero** OpenAI requests (dry-run mode never calls the model),
  so it confirms only that the deterministic preflight gate passes for the
  full seven-series basket, not that a full-basket model response has ever
  been requested or evaluated.

  This dry-run pass exposed a contract mismatch that had not been reachable
  before: `MacroAnalyst` accepted up to ten requested series (via
  `macro_evidence.MAX_SERIES_IDS`), but `MacroAnalystModelAnalysis`'s
  `macro_claims` was hard-bounded to a **separate**, smaller literal (`6`,
  `MAX_MACRO_CLAIMS`) -- so a fully eligible seven-series request could never
  have structurally retained one claim per series in a `"sufficient"`/
  `"limited"` response; the model would have been forced to omit at least
  one requested series or exceed the hard schema bound and fail validation
  outright.

  The fix, in this same change: `MAX_MACRO_CLAIMS` is now fixed to
  `macro_evidence.MAX_SERIES_IDS` directly (no longer an independent
  literal that could silently drift out of sync again), and a new
  deterministic check, `_validate_coverage`/`MacroAnalystCoverageError`, now
  requires a `"sufficient"`/`"limited"` response to cover every requested
  series in exactly one claim each -- see "Full-basket coverage" above for
  the full rule. `AGENT_INSTRUCTIONS` was updated to state this as a
  mandatory requirement (replacing the previous "prefer 1 to 4 claims"
  advisory, which is no longer accurate now that coverage is mandatory
  rather than a preference). Every existing hard bound, citation/series
  check, content-scope check, comparison-claim check, frequency-wording
  check, transmission-channel check, the shared non-directional output
  policy, the existing `macro_claims`-empty-iff-`"insufficient"`
  biconditional, the one-OpenAI-request/zero-retry behavior, and the dry-run
  default are all unchanged and re-verified passing.

  **As of this change, this fix has not been exercised against a live
  OpenAI response for the seven-series basket or any other multi-series
  request -- no paid full-basket (or any other) Macro Analyst `--execute`
  request has been made as part of this change.** It has been validated
  only by focused offline tests using a fake, hand-authored model client and
  fake evidence builder, plus one dedicated test that builds the real
  `MacroAnalystModelAnalysis`/`MacroClaimDraft` schema and round-trips a
  valid seven-series response through the real `OpenAIStructuredClient`
  (with only its SDK transport faked, never a live network call). Passing
  these tests demonstrates the deterministic scaffolding is now internally
  consistent for a full seven-series request; it does not demonstrate that a
  real model response will actually cover every requested series correctly,
  or that any described observation is factually accurate. See
  `PROJECT_STATE.md` for the full sanitized record.

- **Second live run failed content-scope validation, and this change's
  motivation.** A separately authorized live `--execute` run made after the
  item-21 hardening below (FEDFUNDS, `recent_observations_limit=6`) had its
  deterministic preflight pass and sent exactly one OpenAI request, but the
  response was rejected by `_validate_content_scope` at `field=limitations[0]`
  (`category=content_scope_invalid`) -- no report was accepted and no retry
  was made. Per this agent's sanitization contract, the model-authored text
  that triggered the rejection was never recorded, so **the exact live
  wording is not known and is not reproduced anywhere in this repository.**
  Offline analysis found a locally reproducible false-positive class in the
  content-scope denylist as it existed at the time: it rejected a
  `limitation` for using words like "trend"/"regime"/"correlation"/
  "causation" even when clearly negated (e.g. "insufficient observations to
  establish a trend"), which are desirable, truthful limitations rather
  than prohibited claims. This motivated the narrow, fail-closed negation
  allowance for `limitations` described above (`_validate_content_scope`,
  "Post-response validation"), plus the correction of this check's error
  wording (it previously said "not supported by a single-snapshot
  observation," stale since the item-21 hardening added a bounded
  `recent_observations` history excerpt to the evidence package; it now
  says "not supported by the bounded stored evidence"). **This is offline
  analysis of a plausible failure class reproduced with locally authored
  test fixtures -- it is not proof of the exact wording rejected in that
  live run, and it does not weaken `claim_summary`/`conditional_mechanism`
  content-scope enforcement (still unconditional), the shared
  non-directional output policy, the comparison-claim validator, the
  frequency-wording validator, the transmission-channel validator, any
  schema bound, or the one-OpenAI-request/zero-retry behavior -- all
  verified unchanged by the existing test suite.** As of this change, the
  fix has **not** been exercised against a live OpenAI response. See
  `PROJECT_STATE.md` for the full sanitized record of both the live failure
  and this fix.
- **First live run and this change's motivation.** The first live
  `--execute` run (2026-08-24, FEDFUNDS, `gpt-5-mini`) completed
  successfully -- see `PROJECT_STATE.md` for the full sanitized record
  (stored value, observation date, frequency, token counts). **A manual
  review of that one output found two issues, both addressed by this
  change:** (1) wording like "at 3.63 percent on 2026-07-01" could
  misleadingly imply a point-in-time reading, motivating the frequency-aware
  "stored `<frequency>` observation dated ..." requirement and the
  point-in-time denylist (`_validate_frequency_wording`); and (2) a listed
  transmission channel (inflation) was not explained in
  `conditional_mechanism`, motivating the per-channel addressing check
  (`_validate_transmission_channels`). **One completed run, and one manual
  review of its output, is not a validated evaluation methodology --** it
  surfaced two concrete wording defects; it does not establish that this
  change's fixes, or any future response, are correct. As of *this* change
  (adding `recent_observations`/`latest_change_from_previous` to
  `MacroEvidenceBuilder` and the comparison/frequency-wording/
  transmission-channel validators above), the agent has **not** been run
  again against the real local database, and no further live OpenAI request
  has been made using it -- both remain separate, future, and not yet
  authorized. No migration was added or changed.
- Single call, single bounded list of series IDs (1-10) per call -- no
  batch beyond that bound, no multi-turn conversation, no scheduling.
- No tools, no web search, no file access by the model, no additional data
  retrieval of any kind, no persistence, no migration, and no orchestration
  integration -- this agent is not wired into
  `market_intelligence/orchestration/`.
- The content-scope denylist (`_validate_content_scope`), the
  comparison-claim wording checks (`_validate_comparison_claims`), the
  frequency-wording checks (`_validate_frequency_wording`), the
  transmission-channel token checks (`_validate_transmission_channels`),
  and the shared post-response content policy check all match known fixed
  phrasing or fixed tokens, not general semantic meaning, so none of them
  is proof that every possible unsupported statement, incorrect comparison,
  point-in-time phrasing, unaddressed channel, or directional/bias/
  trade-recommendation/options statement is caught. The frequency-wording
  required-phrase check in particular requires one specific fixed phrase
  ("stored `<frequency>` observation") -- a factually accurate description
  using different wording would still be rejected. The `limitations`-only
  negation allowance within `_validate_content_scope` is likewise a fixed,
  bounded set of negation/insufficiency cue phrases matched by adjacency,
  not general semantic understanding: a truthful, negated limitation
  phrased with a cue word or construction outside that fixed set (or with
  the negation cue too far from the prohibited term) would still be
  rejected -- a conservative, fail-closed choice per this change's
  "narrow allowance" requirement, not a claim that every honest negated
  phrasing is recognized.
- The two-observation comparison is only ever between the latest stored
  observation and the immediately preceding **chronological** one on file
  -- never a longer lookback, a percentage/annualized/basis-point change,
  a seasonally adjusted comparison, or a comparison against a consensus
  expectation. See [docs/MACRO_EVIDENCE_SNAPSHOT.md](MACRO_EVIDENCE_SNAPSHOT.md)
  for the full comparability rule and its `unavailable_reason` enum.
- This agent never assesses whether a stored value or its official metadata
  is itself correct or current beyond the freshness/staleness bookkeeping
  `MacroEvidenceBuilder` already performs -- it only describes what is
  already stored, with citations back to the exact stored observation and
  metadata. See [docs/SOURCE_POLICY.md](../SOURCE_POLICY.md).
- The all-or-nothing preflight gate means a single stale or unmetadata'd
  series in a multi-series request blocks a description of every other,
  otherwise-eligible series in that same request -- there is no partial
  admission. A caller wanting a description of the eligible series only
  must issue a separate, narrower request excluding the ineligible series.
- No dashboard, alerting, or Robinhood/brokerage integration of any kind.
- This is a first, bounded, factual macro-evidence analyst -- explicitly not
  a regime classifier, predictor, directional market model, or trading
  agent, and no such capability is implied or authorized by this change.
