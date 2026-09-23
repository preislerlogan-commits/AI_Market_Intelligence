# SPY VWAP-Extension/Reversion — Confirmation Study Preregistration

**Status: preregistered design, not implemented, not run.** Drafted
2026-09-23, before any SPY bar outside the discovery sample has been
ingested, built into an evaluation input, or evaluated. Nothing in this
document is a result. It authorizes no ingestion, no evaluation run, and no
code change on its own; each step in "Implementation sequence" below needs
its own review.

Subordinate to [PROJECT_STATE.md](../PROJECT_STATE.md) (authoritative
status), [DECISION_RULES.md](../DECISION_RULES.md) (binding boundaries), and
[OPTIONS_DECISION_WORKFLOW.md](OPTIONS_DECISION_WORKFLOW.md) (Phase 1 step
d).

## Scope and boundaries

- **Underlying SPY setup only.** No option return, P&L, contract
  selection, selector run, strategy-agent output, recommendation, alert, or
  trading claim is part of this study.
- **The Options Strategy Agent (step f) remains unauthorized.** No outcome
  of this study, including the most favorable one, authorizes it.
- **Discovery sample is closed.** The expanded run over 2026-08-17 through
  2026-09-22 (26 complete sessions, `PROJECT_STATE.md` Completed Work Log
  item 52, which contains the 5-session run of item 44) is the
  **discovery sample**. Its results motivated this plan. It is therefore
  **not confirmation evidence**. It is never pooled with, re-run
  alongside, or used to re-explain confirmation or holdout results. Its
  record is preserved unchanged.
- **Frozen hypothesis.** The step-c `RegimeThresholds` defaults and the
  evaluator's existing `SampleSizeThresholds` defaults (50 observations /
  20 sessions) stay unmodified. This study does no threshold optimization,
  grid search, or parameter search. Nothing below may be changed based on
  confirmation or holdout outcomes.
- **Sides are separate hypotheses.** Above-VWAP and below-VWAP extensions
  are analyzed separately. Symmetry is not assumed.
- **Result labels are not validation.** None of the labels defined below
  means validated, profitable, predictive, or ready to trade.

## 1. Research questions

- **Primary (confirmatory).** On unseen SPY sessions, do above-VWAP
  extensions show a positive session-level signed return toward the
  frozen signal-time VWAP at each of `intraday_30m`, `intraday_2h`, and
  `to_session_close`?
- **Secondary (confirmatory, lower tier).** Is below-VWAP behavior
  horizon-dependent, as opposed to the same reversion pattern at every
  horizon? The discovery sample hinted at this (weaker than above-VWAP,
  adverse at observation level by session close).
- **Exploratory.** Every regime, time-of-day, and extension-bucket
  breakdown in §6–§7 is exploratory. None of them can change the primary
  or secondary label.

## 2. Primary outcome

- **Metric:** `signed_return_toward_vwap_bps`, exactly as currently
  implemented in `spy_vwap_reversion_evaluator.py`, measured in bps of the
  signal close, with positive meaning toward the frozen VWAP.
- **Population:** every **eligible** above-VWAP decision point in every
  regime. No regime, bucket, extension-size, or time-of-day filter is
  applied (the "all-eligible" result). This matches the discovery
  evaluation.
- **Why the population is not limited to `vwap_mean_reversion` points:**
  - That regime label is assigned by the classifier being studied. If the
    classifier chose the primary evidence, the selection rule would
    already partly contain the behavior the study is testing.
  - Regime-specific results are still reported, as predefined secondary
    subgroup analyses (§7).
  - The unfiltered all-eligible result is always published first. No
    subgroup finding can replace or hide it.
- **Sampling unit: the session.** For horizon *h*, a session contributes
  when it has at least one eligible above-VWAP point with an available *h*
  outcome. Its value `m_s` is the mean of those points' signed returns.
  The estimate `θ_h` is the unweighted mean of `m_s` across contributing
  sessions. This is exactly the existing `session_level` mean.
- **Horizons:** `intraday_30m`, `intraday_2h`, and `to_session_close`,
  each reported and tested separately.
- **`next_session` stays unavailable.** It is not reported as a result.
  Using it requires a separately designed and reviewed
  exchange-calendar-aware contract.
- **Observation-level summaries** (pooled five-minute points) are still
  reported, but only as description. The points overlap, so they are never
  used for inference or for the decision rule.

## 3. Secondary outcomes

Each secondary outcome is reported per side × horizon at session level,
with the §5 bootstrap 95% interval. The intervals are unadjusted, and no
secondary outcome enters the primary decision rule.

- **VWAP touch rate** (existing `touched_vwap`, reduced per session to a
  proportion).
- **MFE and MAE in bps** (existing `max_favorable_excursion_bps` /
  `max_adverse_excursion_bps`).
- **Session-level distribution of `m_s`:** the 10th, 25th, 50th, 75th, and
  90th percentiles (nearest-rank), next to the mean, so a few sessions
  cannot hide behind an average.
- **Minutes to touch.** Reported only if a separately reviewed
  implementation adds it to the validated aggregate contract before the
  confirmation run. If it is added, report the per-session mean
  `minutes_to_touch` over touched points, summarized across sessions. It is
  conditional on a touch and is always shown next to the touch rate. If it
  is not implemented by then, it is not reported. Values derived ad hoc
  from decision points are never canonical results (the same rule as item
  52).
- **Percentage retraced is secondary only, and has a denominator floor.**
  - `pct_extension_retraced` divides by
    `original_extension = |signal_close − signal_vwap|` (dollars). When
    that denominator is tiny, the ratio becomes unstable.
  - **Floor: `original_extension ≥ $0.10`.** Below the floor, the point's
    percentage retraced is `unavailable`. Unavailable points are counted
    per cell, and the point stays eligible for every other metric.
  - **Derivation (units and precision, not outcomes):**
    - SPY's minimum quoting increment is $0.01.
    - Price movement is observed in whole ticks. IEX midpoint executions
      can print at half-ticks, which only makes the steps finer.
    - So one tick of movement changes the ratio by
      `100 × $0.01 / original_extension` percentage points.
    - Requiring that step to be ≤ 10 percentage points gives
      `original_extension ≥ 10 ticks = $0.10`.
    - The floor is fixed in dollars and was chosen without reference to
      any outcome distribution.

## 4. Sample design

### Windows (fixed before ingestion)

| Sample | Dates (inclusive, America/New_York session dates) | Weekdays | Expected full sessions* | Role |
|---|---|---:|---:|---|
| Discovery (closed) | 2026-08-17 → 2026-09-22 | 27 | 26 (observed) | Motivated this plan; never confirmation evidence |
| **Confirmation** | **2026-02-23 → 2026-08-14** | 125 | 121 | The single confirmation run |
| Prior-day context only | 2026-02-20 | 1 | 1 | Supplies `prior_day` for 2026-02-23. No decision points and no outcomes |
| **Reserved holdout** | **2026-09-23 → 2026-12-04** | 53 | 51 | Untouched (see below) |

\*Planning estimate from the published NYSE 2026 holidays: Presidents Day
2/16, Good Friday 4/3, Memorial Day 5/25, Juneteenth 6/19, Independence
Day observed 7/3, Labor Day 9/7, Thanksgiving 11/26. The 2026-11-27 early
close also falls in the holdout and will fail the 78-bar rule. The
repository has no exchange calendar. The input builder's fixed 78-bar rule
and its exclusion reasons are authoritative, not this estimate.

- **Non-overlapping.** The three samples share no session. The
  confirmation window ends on the Friday before discovery begins. The
  holdout begins on the first session after the discovery sample.
- **Fits existing bounds.** 121 expected sessions is within
  `MAX_SESSIONS = 130` for one evaluation input. The 173-day confirmation
  range is within the builder's 366-day limit.
- **Adjacency is safe.** Every scored horizon ends inside its own session
  (`next_session` is unavailable), so no outcome crosses a window
  boundary.

### Gates

- **Study gate:** the confirmation input must contain **≥ 100 complete
  sessions**. Otherwise the study result is `insufficient_sample`.
- **Cell gate:** each tested side × horizon cell needs **≥ 80
  contributing sessions**, meaning the `session_level` `available_count`.
  Otherwise that cell is `insufficient_sample`.
- **Separate from the existing gates.** Both gates apply in addition to
  the evaluator's existing 50/20 gates, which stay unchanged.

### Session contract and exclusions

- **Session contract unchanged:** strict 78-bar, five-minute, gapless
  09:30–15:55 regular sessions. The builder's fixed exclusion reasons
  (`no_regular_session_bars`, `off_grid_bar`, `invalid_price_or_volume`,
  `ohlc_inconsistent`, `incomplete_session`, `session_contract_rejected`)
  are recorded, with counts, for every weekday in the window.
- **No repair.** A session is never repaired, interpolated, or
  re-admitted.

### If data availability falls short

- **Count-based check first.** If the bounded ingestion or the builder's
  count-only report shows fewer than 100 complete confirmation sessions,
  stop and document the shortfall.
- **Window changes are deviations.** The window may change only through a
  recorded deviation (§9), committed before the evaluator is run on any
  confirmation data. It is never changed after outcomes are seen.
- **No silent substitutions.** The discovery sample is never borrowed to
  make up numbers. SIP or any other feed is never silently substituted.

### IEX limitations (accepted, not corrected)

- **Provenance matches discovery.** All three samples use the same
  provenance: `alpaca` / `SPY` / `5Min` / `feed=iex` / `adjustment=raw` /
  `currency=USD`.
- **IEX volume is only part of consolidated volume.** The session VWAP is
  therefore an IEX-volume-weighted VWAP, not the consolidated VWAP.
- **IEX highs and lows can understate the consolidated range.** This
  biases touch rate and MFE/MAE downward.
- **Sparse IEX trading can leave a five-minute slot empty.** Such a session
  fails the 78-bar rule and is excluded with a recorded reason.
- **Raw (unadjusted) prices.** Across an ex-dividend date, `prior_day`
  levels are not dividend-adjusted. Intraday outcomes are unaffected.
- **Conclusions are limited to this measurement.** Findings apply to this
  IEX measurement only and do not transfer automatically to consolidated
  data.

### Reserved holdout (resolved: prospective, 2026-09-23 → 2026-12-04)

- **Genuinely unseen.** Every holdout session happens after this plan was
  written, so no one could have seen its outcomes.
- **Untouched until the whole window exists.** The holdout is not
  ingested, built, or evaluated before the 2026-12-04 session has
  finished. After that, opening it still needs its own authorization,
  given only once the confirmation result is recorded.
- **Confirmation does not wait for it.** The historical confirmation
  study (§4, §10) may be implemented and run before the holdout window
  ends.
- **Never used for anything else.** The holdout is not used for
  implementation debugging, threshold selection, bucket selection, or
  confirmation conclusions.
- **When opened, the pipeline is frozen.** It runs through the identical
  frozen pipeline and decision rule (same commit, same configuration)
  exactly once.
- **No extension or replacement.** If fewer than 40 complete sessions are
  available, the holdout result is `insufficient_sample`. The window is
  not extended or replaced after outcomes have been examined.

## 5. Uncertainty and multiplicity

### Session-blocked bootstrap

- **Whole sessions are resampled, never decision points.**
- **Per-session values.** For a cell with `N` eligible sessions, each
  session contributes one precomputed session-level mean. For the primary
  outcome that is `m_s`; for secondary outcomes it is the per-session
  touch proportion, MFE, or MAE. The `N` values are ordered by ascending
  `session_date`.
- **Replicates:** exactly `replicates = 10,000`.
  - Each replicate draws `N` indices with replacement using
    `rng.randrange(N)`, i.e. `N` sessions from the cell's `N` eligible
    sessions.
  - The replicate statistic `bootstrap_mean` is the mean of the selected
    sessions' values.
- **Generator:** `random.Random(20260923)`, reset independently for each
  cell. Results therefore do not depend on the order cells are processed.
  The Python version is recorded with the result.
- **Interval:** two-sided 95% percentile interval, i.e. the 2.5th and
  97.5th percentiles of the replicates. Sort the 10,000 bootstrap
  estimates ascending. Using zero-based indexing, the percentile interval
  is `[sorted_estimates[249], sorted_estimates[9749]]`, corresponding to
  the 250th and 9,750th order statistics under the fixed nearest-rank
  rule. No interpolation is performed.
- **Arithmetic:** `Decimal`, quantized to `0.0001` with
  `ROUND_HALF_EVEN`, as the evaluator already does.

### Exact p-value

The p-value is a deterministic, two-sided, sign-based bootstrap p-value
with finite-sample correction:

```
p_raw = min(
    1.0,
    2 * min(
        (count(bootstrap_mean <= 0) + 1) / (replicates + 1),
        (count(bootstrap_mean >= 0) + 1) / (replicates + 1)
    )
)
```

### Multiplicity (primary family only)

- **Family:** the three primary above-VWAP horizons.
- **Method:** Holm step-down applied to their `p_raw` values, at
  family-wise `α = 0.05`.
  - Sort `p₍₁₎ ≤ p₍₂₎ ≤ p₍₃₎`.
  - Adjusted `p_holm₍ᵢ₎ = max over j ≤ i of min(1, (3 − j + 1) · p₍ⱼ₎)`.
- **Secondary and exploratory p-values and intervals are unadjusted**, and
  they are labeled secondary or exploratory.

### Secondary paired contrast (below-VWAP, session close vs 30 minutes)

- **Sessions:** only sessions eligible for both below-VWAP
  `to_session_close` and below-VWAP `intraday_30m`.
- **Per-session value:** `d_s` = the session's below-VWAP
  `to_session_close` mean minus its below-VWAP `intraday_30m` mean.
- **Bootstrap:** the `d_s` values are bootstrapped by whole session with
  the same 10,000 replicates, the same seed (reset for this cell), the
  same percentile interval, and the same `p_raw` formula.
- **Unadjusted.** Its p-value is reported as a secondary result.

### Observation-level summaries

Observation-level summaries stay descriptive and get no inferential
status (no interval, p-value, or label), because their five-minute
decision points overlap.

### Smallest effect of interest: 1.0 bps

- **Fixed at 1.0 bps.** It applies to each primary above-VWAP horizon and
  to the below-VWAP paired contrast (§8).
- **When it was chosen.** The 1.0-bps floor was selected after reviewing
  the discovery study but before inspecting any confirmation or holdout
  outcomes. It is therefore preregistered for confirmation and holdout
  analysis, not independent of the discovery study.
- **What it is:** a research-relevance floor for movement in the
  underlying.
- **What it is not:** it is not a transaction-cost estimate, an
  options-return threshold, a profitability threshold, or a trading
  recommendation.
- **Small results stay visible.** Results below 1.0 bps are still reported
  in full (estimate, interval, p-values, and counts), even when they are
  classified `inconclusive`.

## 6. Fixed extension buckets (exploratory)

The units are the existing `normalized_vwap_extension`: the
close-to-VWAP distance in bps divided by the session-to-date standard
deviation of five-minute close-to-close returns in bps. Boundaries apply
to `|normalized_vwap_extension|` and match the frozen classifier's own
comparisons:

| Bucket | Rule | Boundary source |
|---|---|---|
| `unavailable` | `normalized_vwap_extension is None` (e.g. the first bars, before volatility is defined) | Existing feature definition |
| `within_range_band` | `≤ 1.0` | `range_extension_max = 1.0` |
| `intermediate` | `> 1.0` and `< 1.5` | Between the two frozen constants |
| `at_or_above_reversion_threshold` | `≥ 1.5` | `extension_threshold_for_reversion = 1.5` |

- **Where the boundaries come from.** They are existing, frozen classifier
  constants, not the discovery outcome distribution. The buckets partition
  every eligible point, so their counts sum to the all-eligible count.
- **No absolute minimum extension for the primary analysis.**
  - Signed return is scaled by the signal close, not by the extension, so
    small extensions cannot make it unstable. They only add noise, and
    that noise is shown openly in `within_range_band`.
  - A minimum would be a new filter that was not part of discovery, and
    it could hide an unfavorable overall result.
  - Ratio instability is handled only where it arises: the §3 dollar floor
    on percentage retraced.
- **The all-eligible primary result is always reported first.** No bucket
  result can replace or rescue it.

## 7. Regime and time-of-day breakdowns (exploratory)

- **Regime:** the existing `Regime` enum, all five values.
  - `trend_continuation`, `vwap_mean_reversion`, `range`,
    `event_driven`, `indeterminate`.
  - `event_driven` is expected to be 0 because `catalyst_state=unknown`
    everywhere. It is reported anyway.
- **Time of day:** the existing `TimeOfDayBucket`, evaluated at the signal
  bar's completion time.
  - `open` `<10:00`, `mid_morning` `<11:30`, `midday` `<14:00`,
    `afternoon` `<15:00`, `power_hour` `≥15:00`.
  - Some cells are structurally empty, for example `intraday_2h` at
    `power_hour`. They are reported with their counts, not merged.
- **Breakdown structure:**
  - Only one-way breakdowns are allowed: regime, time of day, extension
    bucket (§6).
  - Each is reported per side × horizon, as a session-level mean with an
    unadjusted session-blocked 95% interval.
  - Cross-tabulations are not part of this study.
- **Subgroup gate:** a subgroup cell needs **≥ 40 contributing sessions**
  and **≥ 50 observations**. Otherwise it is reported as
  `insufficient_sample`, with its counts and no statistic.
  - 40 is half the primary cell gate. It was set before any confirmation
    data exists.
  - 50 is the existing observation gate.
- **Groups are fixed.** They are never combined, split, renamed, or
  redefined after results are seen.

## 8. Decision rules

### Primary horizon status

Each primary above-VWAP horizon *h* gets exactly one status. The effect
size, the interval, and the Holm-adjusted p-value must all pass:

- `positive` requires all three of:
  - point estimate `θ̂_h ≥ +1.0` bps
  - two-sided 95% session-blocked bootstrap interval lower bound `> 0`
  - Holm-adjusted `p_holm_h < 0.05`
- `negative` requires all three of:
  - `θ̂_h ≤ −1.0` bps
  - interval upper bound `< 0`
  - `p_holm_h < 0.05`
- `inconclusive`: every other case.

### Primary study label

Apply the rules in order; the first match wins:

1. `insufficient_sample`: the overall gate fails (fewer than 100 complete
   confirmation sessions), or a required cell gate fails (any of the
   three above-VWAP cells has fewer than 80 eligible sessions).
2. `supported_for_further_shadow_research`: all three primary horizons are
   `positive`.
3. `not_supported`: none of the three primary horizons is `positive`.
4. `mixed`: every other combination. A `negative` horizon alongside one or
   more `positive` horizons therefore gives `mixed`, not `not_supported`.

For every primary horizon, the result must publish:

- the point estimate
- the 95% interval
- `p_raw` and the Holm-adjusted p-value
- the eligible-session count, and the observation count as description
- the status

Publishing all of these keeps any contradiction between horizons visible.

### Secondary (below-VWAP) label

**Per-horizon status.** Each below-VWAP horizon uses the unadjusted 95%
interval only:

- `positive`: the lower bound is `> 0`
- `negative`: the upper bound is `< 0`
- `inconclusive`: every other case

**Paired contrast** (§5, session close minus 30 minutes). It is
`materially_different` only when all three hold:

- `|contrast estimate| ≥ 1.0` bps
- the two-sided 95% paired-session bootstrap interval excludes zero
- the unadjusted `p_raw < 0.05`

Otherwise the contrast is `inconclusive`.

**Label.** Apply the rules in order; the first match wins:

1. `insufficient_sample`: any below-VWAP cell has fewer than 80 eligible
   sessions.
2. `below_horizon_dependent`: the three below-VWAP statuses are not all
   equal, or the paired contrast is `materially_different`.
3. `below_consistent_reversion`: all three statuses are `positive`.
4. `below_no_reversion`: every other case, meaning all three are
   `inconclusive` or all three are `negative`.

**Asymmetry.** Above-VWAP `m_s` minus below-VWAP `m_s`, per horizon, is
reported descriptively with an unadjusted paired interval. It carries no
label.

### What the labels permit

- **No label** (primary, secondary, or holdout) means validated,
  profitable, accurate, or ready to trade.
- **`supported_for_further_shadow_research`** only permits proposing a
  separately reviewed shadow-research step on the underlying setup. It
  does not authorize step f, a selector run, or any options analysis.
  Opening the holdout remains governed by §4.
- **`mixed`, `not_supported`, and `insufficient_sample`** are recorded
  with the same prominence. They are never re-run with a different
  window, filter, or threshold in pursuit of a better label.

## 9. Reproducibility

- **Configuration snapshot:** the result records every value this
  document fixes:
  - windows, gates, seed, replicate count, `α`, the interval and p-value
    definitions, the 1.0-bps smallest effect of interest, bucket
    boundaries, and the percentage-retraced floor
  - the regime- and sample-threshold snapshots (already recorded today)
  - the Python version
  - this document's path and the merge commit SHA
- **Determinism:**
  - The evaluator and the new analysis stay pure: no clock read, no I/O,
    no unseeded randomness.
  - `generated_at` is supplied at the CLI boundary, as it is today.
  - A re-run on the same input must reproduce the record exactly, apart
    from `generated_at`.
- **Canonical serialization:** the existing sorted-key, byte-stable JSON
  helpers.
- **Exact provenance:** the bar identity above, the ingestion-run IDs with
  their received/inserted counts per chunk, the builder's count report
  (including exclusions per reason), the SHA-256 of the input file, and
  the code commit SHA.
- **One immutable result per run:** the existing no-overwrite, atomic
  write inside gitignored `data/evaluations/local/`. The confirmation
  evaluation runs once. A failed or aborted run is recorded as such; it is
  not deleted and then quietly re-run.
- **Real artifacts stay gitignored.** Only sanitized aggregates are
  documented.
- **Deviations:** any change to this plan is added as a dated entry in a
  "Deviations" section at the end of this document, through a reviewed
  change, **before** any confirmation outcome is examined. After outcomes
  are seen, nothing here changes; later ideas become a new preregistration
  on new data.

## 10. Implementation sequence

1. **Review and merge this preregistration.**
2. **Audit Alpaca availability without requesting data.**
   - Check the documented IEX historical coverage and the connector
     bounds.
   - Print the planned chunks with `scripts/ingest_alpaca_bars.py` in its
     default dry-run mode. It makes zero requests.
   - If the window cannot be supported, stop and record a deviation.
3. **Implement the §3–§8 additions with synthetic tests only.**
   - Proposed shape: a separate pure module that post-processes a
     validated `SpyVwapReversionEvaluationRecord`, so the existing
     evaluator, contracts, and thresholds stay untouched.
   - Tests use hand-built fixtures with known bootstrap, Holm, and label
     outcomes.
4. **Review that implementation** before any real confirmation run.
5. **Ingest the confirmation range in bounded chunks** with `--execute`,
   each separately authorized.
   - Chunks run from 2026-02-20T00:00Z through 2026-08-15T00:00Z, split by
     calendar month, with `limit=1000` and `max_pages=5` (the item-52
     precedent).
   - Back up the database first.
6. **Build and validate the input** for 2026-02-23 → 2026-08-14.
   - Check the count-only report against the ≥ 100-session study gate.
7. **Run the confirmation once**: a dry-run first, then one `--write`.
8. **Record the results**, including unfavorable, mixed, or
   insufficient results, in `PROJECT_STATE.md`.
9. **Leave the holdout untouched** until its whole window (through the
   2026-12-04 session) exists, the confirmation is recorded, and a
   separate authorization opens it. Steps 1–8 do not wait for the
   holdout window.
10. **Do not begin the Options Strategy Agent** on the basis of this study
    alone.

## Status of each element

| Element | Status |
|---|---|
| Pure evaluator, contracts, serialization, and dry-run-first CLIs; read-only input builder; dry-run-first bars ingestion | **Already implemented** |
| Signed return, touch rate, MFE/MAE, and percentage retraced (no floor), at observation and session level; per-point regime, time-of-day bucket, and `normalized_vwap_extension` recorded | **Already implemented** |
| Existing 50-observation / 20-session gates; frozen `RegimeThresholds`; `next_session` always unavailable; 130-session input cap | **Already implemented, unchanged by this study** |
| Session-blocked bootstrap intervals and exact finite-sample p-values; Holm correction; 1.0-bps smallest effect of interest; primary and secondary labels; 100-session study gate and 80-session cell gate; paired contrasts; session-level percentiles | **Preregistered, unimplemented** |
| Extension buckets; one-way regime and time-of-day aggregation with the 40-session subgroup gate; percentage-retraced $0.10 floor; configuration snapshot in the result | **Preregistered, unimplemented** |
| Minutes-to-touch aggregate | **Optional, needs separate review; not reported unless implemented before the run** |
| Confirmation ingestion, input build, and run; holdout evaluation | **Preregistered, not started** |
| `next_session` (exchange calendar); relative-volume, catalyst, and breadth point-in-time context; SIP/consolidated data; option returns, P&L, selector runs; step f; any threshold change | **Future or out of scope** |

## Resolved design decisions

These were settled by the reviewer after reviewing the discovery study
but before inspecting any confirmation or holdout outcome. They are
preregistered for confirmation and holdout analysis, not independent of
the discovery study. Any later change is a deviation (§9).

1. **Holdout placement: prospective, 2026-09-23 → 2026-12-04** (§4).
2. **Primary study label:** a `negative` horizon alongside one or more
   `positive` horizons gives `mixed`. Every horizon's details are always
   published (§8).
3. **Smallest effect of interest: 1.0 bps.** It applies to the primary
   horizons and to the below-VWAP paired contrast (§5, §8).
4. **Primary population:** all eligible decision points, not limited to
   `vwap_mean_reversion` (§2).
5. **P-value:** the exact finite-sample sign-based bootstrap p-value,
   with Holm correction for the primary family only (§5).
