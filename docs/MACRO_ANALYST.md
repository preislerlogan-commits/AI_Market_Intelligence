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
   comparison.
2. Evaluates a fixed, deterministic, **all-or-nothing** preflight gate
   against that snapshot (see "Deterministic preflight" below). If the gate
   fails for *any* requested series, the agent returns a truthful
   `status="abstained"` report and makes **zero OpenAI requests**.
3. If the gate passes, builds a bounded, model-facing evidence package from
   the snapshot's official stored observation/metadata fields only (never a
   database path, SQL text, ingestion ID, credential, or raw audit/internal
   field) and makes **exactly one** structured-output request via the
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
      "recent_observations": [
        {
          "observation_date": "2026-07-01",
          "value": "5.330000",
          "is_missing": false,
          "realtime_start": "1776-07-04",
          "realtime_end": "9999-12-31",
          "evidence_id": "macro_3f2a9c1d4e5b6789"
        },
        {
          "observation_date": "2026-06-01",
          "value": "5.000000",
          "is_missing": false,
          "realtime_start": "1776-07-04",
          "realtime_end": "9999-12-31",
          "evidence_id": "macro_7a1b2c3d4e5f6081"
        }
      ],
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

- Every `series` key is the series entry's existing, **stable,
  code-generated** `evidence_id` from `MacroEvidenceBuilder` -- never chosen
  or supplied by the model. This same key always equals
  `latest_change_from_previous.latest_evidence_id`, since both are derived
  from the identical chosen latest row.
- Only `series_id`, `title`, `frequency`, `frequency_short`,
  `seasonal_adjustment`, `observation_date` (the chosen row's
  `latest_observation_date`), `latest_value`, `realtime_start`,
  `realtime_end`, `coverage`, `recent_observations`, and
  `latest_change_from_previous` are preserved -- **no database path, SQL
  text, ingestion ID, credential, or raw audit/internal field is ever
  included** (none of those exist on the snapshot to begin with, so there
  is nothing to filter out).
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

## Structured output

Two strict Pydantic models (`extra="forbid"`, every field bounded):

- **`MacroAnalystModelAnalysis`** -- the *only* schema sent to OpenAI as
  `output_model=`. It deliberately excludes `status`, `series_ids`,
  `snapshot_created_at_utc`, `source_series_count`,
  `directional_assessment`, and `trade_recommendation` -- those are
  agent-authored/fixed, so the model has no opportunity to set them. Fields:
  `evidence_quality` (`"sufficient"|"limited"|"insufficient"`),
  `macro_claims` (**0**-6 `MacroClaimDraft`), `limitations` (0-6 bounded
  strings).
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
  characters, each `limitation` at most `ADVISORY_MAX_LIMITATION_LENGTH`
  (200, hard max 300) characters, and `ADVISORY_PREFERRED_MIN_MACRO_CLAIMS`
  to `ADVISORY_PREFERRED_MAX_MACRO_CLAIMS` (1 to 4, hard max 6) macro claims
  preferred, only exceeded if genuinely necessary. **This is
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
- **Content scope (`_validate_content_scope`, raises
  `MacroAnalystContentScopeError`)** -- a fixed, deterministic, fail-closed
  denylist rejecting language describing acceleration/deceleration,
  surprise, historical extreme (record/all-time high or low), a trend,
  correlation, causation, a policy change, or a market regime, in every
  macro claim's `claim_summary`/`conditional_mechanism` and every
  model-supplied `limitation`. This is a conservative, bounded pattern
  match, not general semantic understanding -- it is not proof that every
  possible unsupported statement is caught (see "Known limitations" below).
  Increase/decrease/unchanged language is deliberately **not** covered here
  -- it is instead conditionally permitted; see "Comparison-claim
  validation" below.
- **Post-response content policy** -- after the checks above and before
  `MacroAnalystReport` is constructed, every model-authored free-text field
  is checked against the same fixed, deterministic, fail-closed denylist the
  Market Evidence Agent and News Analyst use (see
  `market_intelligence/agents/non_directional_output_policy.py`), covering
  directional predictions, bullish/bearish bias, trade
  recommendations/actions, and options-related detail. The first match
  raises `MacroAnalystPolicyError`; the rejected text is **never** included
  in the raised error or logged anywhere -- only a fixed, code-authored
  field name and category name are recorded.
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
  verifiably addressed (a fixed, word-boundary token pattern per channel)
  by that claim's `conditional_mechanism`; `"other"` has no such pattern
  and is always rejected if listed (fail closed on a clearly unsupported
  channel).
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
accurately transcribes the underlying stored value or metadata. No automated
evaluation of factual accuracy exists in this repository.

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
(112 tests total): an eligible FEDFUNDS snapshot; every preflight abstention
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
scenario; a valid two-series completed response; a parametrized sweep of
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
and numerically below its hard Pydantic maximum; that the model-facing
schemas contain no sentiment/probability/confidence/forecast/
`directional_assessment`/`trade_recommendation`/`content_basis` field at
all; that `recent_observations`/`latest_change_from_previous`/
`frequency_short` are present in the built evidence package; a fully
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
that the error never echoes rejected text; and `recent_observations_limit`
forwarding from the agent to the evidence builder and from the CLI to the
agent (both dry-run and `--execute`, including the CLI default).

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
  using different wording would still be rejected.
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
