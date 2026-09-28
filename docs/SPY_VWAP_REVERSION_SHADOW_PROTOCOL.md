# SPY VWAP-Extension/Reversion — Shadow-Research Protocol (draft)

**Status: DRAFT — awaiting review. Not frozen. Documentation only.** Nothing in
this document is implemented, scheduled, collected, or authorized.

- **Nothing is authorized.** This document authorizes no recorder,
  scheduler, unattended operation, dashboard, alert, migration, database
  write, ingestion, collection, selector integration, or agent.
- **Proposed values.** Every numerical value marked *proposed* becomes
  binding only when this document is reviewed and merged.
- **How it freezes.** The protocol is frozen **only by merge** (§L). Each
  stage in §K needs its own explicit authorization.

Subordinate to [PROJECT_STATE.md](../PROJECT_STATE.md),
[DECISION_RULES.md](../DECISION_RULES.md),
[SPY_VWAP_REVERSION_PREREGISTRATION.md](SPY_VWAP_REVERSION_PREREGISTRATION.md)
(with clarification C1), and
[SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md](SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md).
Where this document appears to conflict with any of those, they govern.

## A. Purpose and status

- **Where this starts.** The confirmation study's primary label was
  **`supported_for_further_shadow_research`** (2026-09-25). Under
  preregistration §8, that label only permits *proposing* a separately
  reviewed shadow-research stage. This document is that proposal.
- **What shadow research is.** Shadow research is **prospective process
  validation**, not another retrospective study or optimization.
- **What it tests:**
  - detection reliability
  - data availability
  - timing integrity
  - operational coverage
  - reconciliation against deterministic replay
  - descriptive forward behavior of the frozen measures
- **What it does not do.** It does not measure options returns, transaction
  costs, or P&L. It does not establish profitability. It does not authorize
  trading, recommendations, alerts framed as recommendations, contract
  selection, a selector run, order routing, execution, or the Options
  Strategy Agent. Manual-only trading (DECISION_RULES.md) is unchanged.
- **Observations are not forecasts.** A slot observation is a mechanical
  research record, not a forecast or a directional call. If any future
  stage turned observations into forecasts, DECISION_RULES.md "Forecast
  Requirements" would apply in full.
- **The sealed holdout is separate.** The prospective confirmation holdout
  (2026-09-23 → 2026-12-04) is a separate, frozen test (§4, C1.1). No
  holdout date ever becomes a shadow observation or shadow outcome (§G).

## B. Units, eligibility, and timestamps

### B.1 Three distinct units

| Unit | Definition |
|---|---|
| **Expected session slot** | One of the 78 regular-session decision indices *k* = 0…77 of a session. Slot *k* is the bar starting 09:30 + 5·*k* minutes America/New_York and closing 5 minutes later |
| **Slot observation** | The single immutable live record stating what was, or was not, available at one slot, and how it classified under the frozen rules. It is written once (§C) |
| **Eligible shadow candidate** | A slot observation whose decision-time classification passes **every** frozen eligibility rule in B.2 (`EligibilityStatus = eligible`) |

- **Coverage.** In every authorized, active collection session, every
  expected slot must end with either one slot observation or one explicit
  missing-slot audit event (§C).
- **Behavioral population.** Only eligible shadow candidates can enter
  behavioral estimates, and only when the §H.2 population conditions hold.
  Ineligible or unavailable slots are retained for process auditing. They
  are never called eligible candidates and never enter behavioral
  estimates.

### B.2 Frozen decision-time eligibility, enumerated

Derived from the unmodified `spy_vwap_reversion_evaluator._determine_eligibility`,
`spy_regime_features.compute_features`, the `SessionBars` /
`RegimeEngineInput` / `IntradayBar` contracts, and the input builder's
session rules. **No threshold is added.** Every rule is evaluated only on
the closed-bar prefix `bars[0..k]`.

| # | Rule | Source | Failure classification |
|---|---|---|---|
| E1 | Provenance is exactly `alpaca` / `SPY` / `5Min` / `iex` / `raw` / `USD` | input builder / bars connector constants | invocation refused (*`wrong_provenance`*) |
| E2 | Every prefix bar is on the canonical 5-minute grid, starts within 09:30–15:55 ET on the session's weekday date, and prefix bars are strictly ascending | `RegimeEngineInput` validators | `off_grid_bar` / *`non_regular_session_timestamp`* / `session_contract_rejected` |
| E3 | Every prefix bar has finite open, high, low, and close with 0 < price ≤ 1,000,000, integer volume 0 ≤ v ≤ 10,000,000,000, and high ≥ max(open, close), low ≤ min(open, close), high ≥ low | `IntradayBar`, builder `_MAX_PRICE` / `_MAX_VOLUME` | `invalid_price_or_volume` / `ohlc_inconsistent` |
| E4 | Point-in-time context is the narrowed values: `same_time_historical_volume_baseline=None`, `catalyst_state=unknown`, `breadth_state=unavailable` | `SessionBars` validator | not applicable (fixed by construction) |
| E5 | Decision index: **every** slot *k* = 0…77 is a decision point; no index is skipped | evaluator `_build_decision_points` | none |
| E6 | VWAP available: the cumulative prefix volume is greater than 0, so the session VWAP (typical price × volume / volume) exists | `compute_features`, `_determine_eligibility` | `vwap_unavailable` (ineligible) |
| E7 | Non-zero extension: `close_to_vwap_distance_bps` (quantized to 0.0001 bps) ≠ 0 | `_determine_eligibility` | `zero_extension` (ineligible) |
| E8 | Side: `above_vwap` if the distance is > 0, else `below_vwap` | `_determine_eligibility` | none (a classification, not a filter) |

- **No magnitude threshold.** There is no minimum-extension or regime
  eligibility rule. `extension_threshold_for_reversion` (1.5) and the other
  classifier thresholds affect only the recorded `regime` label, which is
  exploratory context, never a filter.
- **Normalized extension can be null.** `normalized_vwap_extension` may be
  null (for example in the first bars, before volatility is defined). That
  does not affect eligibility.
- **Prior-day context** is supplied only when the immediately preceding
  weekday is a stored session that passes the input builder's session
  rules; otherwise `prior_day_status` is `previous_weekday_not_stored` or
  `previous_weekday_session_excluded`. It affects only the regime label.

### B.3 After the decision timestamp (not eligibility)

These never change an observation's eligibility.

- **O1 — Outcome availability per horizon** (evaluator rules, unchanged):
  - `intraday_30m` requires the target bar closing exactly
    decision + 30 min, no later than 16:00 ET, with a gapless chain. That
    is structurally possible only for *k* ≤ 71.
  - `intraday_2h` is the same with + 120 min, possible only for *k* ≤ 53.
  - `to_session_close` requires the session's own 78-bar gapless close and
    at least one later bar, possible only for *k* ≤ 76.
  - `next_session` is always unavailable (no exchange calendar).
- **O2 — Session completeness:** exactly 78 slots, indices 0–77, with the
  final slot's `session_bars_complete=True` (C1.6). It is known only after
  the session, and is required for behavioral inclusion.
- **O3 — The $0.10 floor:** percentage retraced is calculated only when
  `|signal_close − signal_vwap| ≥ $0.10` (exact comparison). It affects that
  one descriptive outcome field, never eligibility or any other metric.

### B.5 Candidate-horizon obligations

A **candidate-horizon obligation** is a pair (eligible candidate, horizon)
where that horizon is **structurally available** at the candidate's slot
index under the frozen evaluator (O1):

| Horizon | Structurally available at slot index *k* |
|---|---|
| `intraday_30m` | *k* ≤ 71 |
| `intraday_2h` | *k* ≤ 53 |
| `to_session_close` | *k* ≤ 76 |
| `next_session` | never (no exchange calendar) |

- **Defining H.** **H** is the set of all such obligations for the
  eligible candidates of the locked sample (§H.2). Structural availability
  depends only on *k*, so it is recorded at write time as
  `structurally_available_horizons`, before any outcome exists.
- **Unavailable horizons are not missing outcomes.** A structurally
  unavailable horizon is recorded with the fixed reason
  `structurally_unavailable_horizon`. It is **not** a missing outcome and
  never enters H.
- **The last slot has no obligations.** A candidate at *k* = 77 has no
  structurally available horizon. It is retained for process auditing but
  can't contribute to any behavioral horizon estimate.
- **Counts differ by horizon by design.** Observation and session counts
  differ across horizons (for example `intraday_2h` has fewer obligations
  than `intraday_30m`). That is expected and is not evidence of missing
  data.
- **Status vocabulary.** Obligation and session statuses use exactly three
  terms:
  - **terminal**: no further outcome event is expected for the obligation.
    It reaches this either through `outcome_recorded` (the outcome is
    durably recorded) or through a permitted **technical terminal reason**:
    `replay_source_unavailable` or
    `transactional_write_failure_recorded_live` (§I.2).
  - **successfully_finalized**: the required outcome was durably recorded
    (`outcome_recorded`) no later than the obligation's P6 deadline
    (§H.1). Only these obligations enter H_f.
  - **reconciliation_complete**: every applicable obligation is terminal.
- **Technical terminal reasons.** A technical terminal reason makes an
  obligation terminal but **not** successfully_finalized. It never enters
  H_f, so it counts against P6. An outcome recorded after its deadline is
  likewise terminal but not successfully_finalized.
- **Candidates and sessions.**
  - A **candidate** is reconciliation_complete when all of its structurally
    available obligations are terminal. A candidate with no obligations
    (ineligible, or *k* = 77) is reconciliation_complete once its slot is
    reconciled (§I).
  - A **session** is reconciliation_complete when all of its obligations
    are terminal and all 78 of its slots are reconciled (§I).
  - Neither status implies that every obligation was successfully
    finalized; P6 measures that separately.
- **Values never decide status.** No outcome's numerical value affects
  terminal, successfully_finalized, or reconciliation_complete status.

### B.4 Timestamps

All timestamps are persisted in UTC. America/New_York is a derived session
interpretation only (§J).

| Timestamp | Meaning |
|---|---|
| **Scheduled bar close** (source time) | The slot's bar start + 5 min: the evaluator's `signal_as_of`, and the decision timestamp. Derived from the bar identity, never from a clock |
| **First-observed timestamp** | Wall-clock UTC when the recorder first obtained the closed bar |
| **Durable-write timestamp** | Wall-clock UTC when the slot-observation transaction committed |
| **Outcome-availability timestamp** | Close of the horizon's endpoint bar (decision + 30 min, decision + 120 min, or the 15:55 bar's 16:00 close) |
| **Session-completion classification** | When the after-session replay classifies the session (§I) |
| **Terminal / reconciliation-complete timestamps** | For an obligation, when it became terminal (and, for `outcome_recorded`, whether that was by its P6 deadline); for a candidate or session, when it became reconciliation_complete (§B.5) |

**Closed-bar data only.** No decision-time field may depend on a bar whose
close is later than that observation's scheduled close. Outcome fields use
later bars only after their availability timestamp. Latency (§F.2) never
changes decision-time feature values.

## C. Lifecycle: immutable observation plus append-only events

**Model.** Each slot has at most one immutable **slot observation**, written
once in one transaction. Everything afterwards is an **append-only event**
linked to it by `slot_id`. A slot's state is derived from its observation
and events; nothing is ever overwritten or deleted.

- **Eligibility is fixed at write time.** A decision-time ineligibility
  (`vwap_unavailable`, `zero_extension`, or a contract failure) is a
  **terminal classification** recorded in the observation. No later event
  can make an ineligible observation eligible.
- **Exclusion is irreversible.** Exclusion from the behavioral population
  (for example late, prefix gap, incomplete session, or a reconciliation
  mismatch) is an **appended, irreversible event**, not a destructive
  transition. The observation stays, and nothing ever reverses it.
- **Corrections are audit events.** A technical correction is a linked
  audit event and never replaces history.

| From (derived state) | Permitted event → next state | Required evidence | Terminal? |
|---|---|---|---|
| *no record* | `slot_observation_written` → `observed` | Closed prefix bars passing E1–E3; frozen code/config/protocol identity; UTC timestamps | no |
| *no record* | `missing_slot_recorded` → `missing` | Scheduled close + L passed with no durable observation; a fixed reason | no (may later reconcile) |
| `missing` | `late_observation_written` → `observed_late` | The closed bar obtained after L; marked late | no |
| `observed` / `observed_late` (eligible) | `outcome_recorded(h)` or `outcome_terminal_technical(h, reason)`, once per structurally available horizon *h* | Endpoint bar closed (availability timestamp passed); for a technical reason, a permitted §I.2 code with contemporaneous evidence | no |
| `observed` / `observed_late` / `missing` | `session_classified` | After-session replay (§I): complete or structurally incomplete, with an existing reason | no |
| any not yet `reconciliation_complete` | `population_excluded(reason)` | A fixed reason (§F); irreversible | no (membership terminal) |
| any not yet `reconciliation_complete` | `reconciled(class)` | Replay comparison; exactly one §I class | no |
| any, once every applicable obligation is terminal and the slot is reconciled | `reconciliation_completed` → `reconciliation_complete` | Every structurally available obligation of an eligible observation is terminal (§B.5); ineligible observations and those at *k* = 77 have no obligations; session classified; reconciled. Outcome values never affect this | **yes** |
| `reconciliation_complete` | `audit_correction` (linked) only | Documented cause; the original is unchanged | yes |

**Prohibited transitions:**
- overwriting or deleting an observation or event
- ineligible → eligible
- excluded → included
- writing a second observation for a `slot_id`
- converting `missing` or `observed_late` into a timely observation
- creating an observation from replay data
- any event after `reconciliation_complete` other than a linked `audit_correction`

## D. Proposed contract (not implemented)

Proposed schema versions:
- `spy-vwap-shadow-slot-observation-1`
- `spy-vwap-shadow-slot-event-1`

They follow the repository's existing style: strict Pydantic v2,
`extra="forbid"`, bounded fields, finite decimals, fixed enums, and exact
rationals for inferential values (C1.2).

**Slot observation — identity and provenance**
- `schema_version`
- `slot_id`: deterministic, `SPY:<session_date>:<k>`, unique
- `symbol` and `session_date` (America/New_York date)
- `slot_index` *k*
- `scheduled_close_utc`
- provider, feed, timeframe, adjustment, currency: fixed literals
- `code_commit_sha`: operator-supplied, 40 lowercase hex, not attested
- `configuration_sha256`: canonical frozen configuration
- `protocol_commit_sha`: external merge provenance (§L)
- `collection_period_id`
- `source_bar_reference`: bounded; the provenance tuple, first and last
  prefix bar timestamps, prefix bar count, and SHA-256 of the canonical
  prefix. Never raw bars

**Slot observation — decision-time fields**
- `availability_status`: `closed_bar_available` or a fixed reason
- eligibility status (E6/E7) with each E1–E8 sub-result
- `extension_side`
- `prefix_complete`
- `signal_close` and `signal_vwap`
- `close_to_vwap_distance_bps` and `normalized_vwap_extension`
- extension bucket, `regime`, `scenario_horizon`, `time_of_day_bucket`, and
  `prior_day_status`: exploratory context
- the narrowed point-in-time context
- `first_observed_utc`, `durable_write_utc`, `latency_seconds`, and
  `timeliness` (`timely` / `late`)
- `structurally_available_horizons`: derived from *k* only (§B.5)
- `detector_invocation_id`

**Events**
- `slot_id`, `event_type` (fixed enum), `event_utc`, fixed-vocabulary
  `reason`, and a linked `audit_of_event_id` where applicable
- Outcome fields per horizon (evaluator formulas):
  - `available` and availability timestamp
  - signed return toward VWAP (bps)
  - touch, bars-to-touch, minutes-to-touch
  - MFE and MAE
  - percentage retraced, or unavailable under the $0.10 floor
  - an outcome state: `outcome_recorded`, a permitted technical terminal reason, or `structurally_unavailable_horizon`
  - terminal timestamp, and a `successfully_finalized` flag (by the P6 deadline)
- Reconciliation class (§I)
- Reconciliation-complete timestamp

**Excluded.** No credentials, headers, raw provider payloads, unrestricted
exception text, positions, sizes, recommendations, or trading instructions.

## E. Multiplicity and overlap

The confirmed population is every eligible decision point, meaning
overlapping five-minute observations. The protocol preserves this:

- **Identity.** Each symbol/session/slot has one deterministic identity and
  a database uniqueness constraint (§J).
- **Idempotent replay.** Re-invoking the detector for an observed slot must
  reproduce identical decision-time fields. It writes nothing new and logs
  a rejected duplicate attempt.
- **No retroactive creation.** No observation is ever created from later
  bars or from replay.
- **No trading interpretation.** Observations are never deduplicated into
  trade-like events. They carry no position, capital, entry/exit, or
  execution meaning.
- **Overlap in inference.** Because points overlap, observation-level
  summaries are descriptive only. The session is the independent unit.

## F. Data, timing, and clock gates (fail closed)

### F.1 Gate vocabulary

**Reused vocabularies:**
- `SessionExclusionReason`: `no_regular_session_bars`, `off_grid_bar`,
  `invalid_price_or_volume`, `ohlc_inconsistent`, `incomplete_session`,
  `session_contract_rejected`
- `EligibilityStatus`: `vwap_unavailable`, `zero_extension`

Everything below marked *italic* is **proposed** and bounded to this list.

| Condition | Reason | Effect |
|---|---|---|
| Wrong provenance | *`wrong_provenance`* | Invocation refused; no observation; missing-slot event |
| Malformed, off-grid, invalid, or inconsistent bar | existing `SessionExclusionReason` values | Observation written as contract-failed (ineligible, terminal) |
| Non-regular timestamp | *`non_regular_session_timestamp`* | Not a slot; counted only |
| Prefix gap at detection | *`prefix_incomplete`* | Observation written; `population_excluded` |
| Closed bar not yet available | *`stale_input`* | Retried until L; then `missing_slot_recorded` |
| Provider error or outage | *`provider_unavailable`* / *`provider_invalid_response`* | Retried until L; then `missing_slot_recorded` |
| Durable write after L | *`late_detection`* | `observed_late`; `population_excluded` |
| Horizon structurally unavailable at *k* (§B.5) | *`structurally_unavailable_horizon`* | Recorded at write; not an obligation; not a missing outcome |
| Obligation cannot be computed after its availability timestamp (not expected in a replay-complete session) | *`replay_source_unavailable`* / *`transactional_write_failure_recorded_live`* (§I.2) | Obligation terminal but not successfully_finalized; counts against P6 |
| Session fails the 78-slot contract | existing `incomplete_session` / `no_regular_session_bars` (+ *`structurally_incomplete_day`* summary class) | All that day's slots are excluded from the population and from P1–P3 denominators (§H.1) |
| Duplicate `slot_id` | *`duplicate_rejected`* | Refused by the uniqueness constraint |
| Code, config, or protocol identity differs from the period's frozen values | *`identity_mismatch`* | Invocation refused |
| Clock health unknown or out of tolerance, detected **before** recording | *`clock_unhealthy`* | Invocation refused; missing-slot event (bounded exclusion or pause, not a P5 violation) |
| Any P5 condition (§H.1) | *`timing_violation`* | P5 violation; immediate permanent stop (§H.4) |

**No exchange calendar is assumed.** A session is complete only if replay
finds all 78 valid gapless slots.
- Holidays and early-close days (for example 2026-12-24) are classified
  deterministically from stored bars by the existing contract.
- An early close is **never** inferred.
- Such a day is not a complete covered session, doesn't count toward the
  60-session sample, and is excluded from P1's denominator by that frozen,
  outcome-free rule.
- It is **not** counted as a detector failure.
- The protocol does not guess whether a partial date was an official early
  close or a data gap. Both are `structurally_incomplete_day` and are
  reported by count.

### F.2 Recording latency (*proposed*, not shown feasible)

- **Definition.** Latency = durable-write timestamp − scheduled bar close.
  It starts at the scheduled five-minute close, not at detector start, and
  it ends at the durable commit, not at first observation.
- **Timestamps stay separate.** The source (scheduled close),
  first-observed, and durable-write timestamps are all recorded.
- **Negative latency** (durable write before the scheduled close) is a P5
  `timing_violation`.
- **Proposed bound L ≤ 240 s.** An observation written after L is `late`.
  It is retained for audit, never portrayed as timely, and always excluded
  from the behavioral population.
- **Feasibility is not yet shown.** A separately authorized, bounded IEX
  connectivity test must show the bound is achievable before implementation
  readiness is declared (§K). If the test fails, L may be revised only by a
  reviewed protocol amendment before any collection.

### F.3 Clock policy (*proposed*, not implemented)

- **Canonical time.** UTC is the canonical persisted time. America/New_York
  is derived for session interpretation only.
- **Wall clock.** The OS clock is synchronized to a documented time source.
  The proposed maximum skew is ±1 second, checked at detector start and
  periodically; the tolerance is subject to implementation review.
- **Elapsed time.** A monotonic clock measures elapsed in-process
  durations. Persisted timestamps are UTC wall-clock.
- **Fail closed.** Unknown or out-of-tolerance clock health refuses
  invocation (`clock_unhealthy`).
- **No network now.** No network request is required or made by this
  document.

## G. Collection start boundary and operating model

- **Holdout sequence.** The holdout window ends after the 2026-12-04
  session. Opening, ingesting, and evaluating it requires its own
  authorization, and its result must be recorded immutably first.
- **Conditions for collection.** Shadow collection additionally requires,
  **all complete before the first expected slot (09:30 ET) of the chosen
  start session**:
  1. implementation readiness passed (§K stages 2–4);
  2. the holdout result recorded; and
  3. an explicit collection authorization.

  Otherwise that whole session is skipped and collection starts at a later
  full session. A partially attended start session can never enter the
  locked sample.
- **Earliest theoretical start: 2026-12-07 (Monday).** Only if every
  condition above is met in time.
- **Prior-day context from the holdout.** The 2026-12-04 session may supply
  prior-day context only after the holdout evaluation is recorded. It never
  becomes a shadow observation or shadow outcome.
- **Operating model:**
  - **No authorization yet.** No collection is currently authorized, and
    no unattended execution is currently authorized.
  - **Why unattended.** A 60-session study is not operationally reasonable
    as a fully manual, bar-by-bar attended process. The intended
    implementation is deterministic unattended recording.
  - **Separate review.** Unattended operation needs its own code, security,
    scheduling, and operational review before any collection.
  - **Manual use is bounded.** Manual observation may be used only for
    bounded implementation checks. It must not become the locked shadow
    sample unless separately authorized.
- **Invocation (conceptual, no scheduler):**
  - one invocation per slot close;
  - an end-of-session pass after 16:00 ET;
  - a reconciliation pass that runs the §I replay and records outcomes
    within 2 weekdays.
- **Restart.** After a restart, the detector resumes at the next slot
  close. Missed slots become missing-slot events, never backfilled
  observations.

## H. Gates, sample lock, stopping, and labels

All values are **proposed**. They are justified by operational reliability,
not by returns.

### H.1 Denominators and process metrics

**Scope.** "Collection days" are all weekdays from the start session through
the end of collection (§H.3).
- A **replay-complete session** is a collection day whose after-session
  replay satisfies the frozen 78-slot contract.
- A **complete covered session** is a replay-complete session in which the
  detector recorded an invocation attempt at all 78 slot closes, with no
  pause or outage gap. Individual slot misses within it are measured by
  P2/P3, not here.

**Denominator symbols:**

| Symbol | Count |
|---|---|
| S_rc | replay-complete sessions |
| S_cc | complete covered sessions |
| X = 78·S_rc | expected slots |
| O | observed slots (durable observations, timely or late) in those sessions |
| C | eligible shadow candidates in the behavioral population of the locked sample (§H.2) |
| H | candidate-horizon obligations of those candidates: (candidate, horizon) pairs structurally available at the candidate's slot index (§B.5); \|H\| ≤ 3·C |
| H_f | successfully_finalized obligations in H (`outcome_recorded` no later than the P6 deadline) |
| U | all reconciliation units over all collection days (§I) |
| M_rc | units classified `exact_match` that belong to replay-complete sessions; 0 ≤ M_rc ≤ X |

**Rules that apply to every metric:**
- **Rounding.** Rates are computed as exact rationals and compared exactly
  with no rounding. Displays use 2 decimal places.
- **Zero denominators.** A zero denominator makes the gate **not
  evaluable**, which counts as a failure at the locked review and as
  "not assessed" at the interim check.

| ID | Numerator | Denominator | Scope | Explained failures count? | When evaluated | Threshold |
|---|---|---|---|---|---|---:|
| P1 | S_cc | S_rc | collection days | n/a | interim + locked | ≥ 95% |
| P2 | expected slots with a **timely** durable observation (latency ≤ L) | X | replay-complete sessions | yes: explained misses still fail | interim + locked | ≥ 99% |
| P3 | M_rc | X | replay-complete sessions only (identical scope for numerator and denominator) | yes: explained mismatches still reduce P3 | interim + locked | ≥ 99% |
| P4 | **count** of duplicate observations accepted as new canonical observations (more than one observation row for a `slot_id`) | — (absolute count, not a rate) | all | n/a | continuous | = 0 |
| P5 | **count** of confirmed timing-integrity or look-ahead violations (list below) | — (absolute count, not a rate) | all collection days | n/a | continuous | = 0 |
| P6 | H_f | \|H\| | locked-sample eligible candidates and only their structurally available horizons | yes: technical terminal obligations are not successfully_finalized and count against P6 | interim (sessions to date) + locked | ≥ 99% |
| P7 | units classified `unexplained_live_missing` + `unexplained_extra_live` + `unexplained_field_mismatch` (§I.3) | U | all collection days | **no**: only the three unexplained classes | interim + locked | ≤ 0.1% |
| P8 | expected slots with ≥ 1 technical-error or `stale_input` event (even if later timely) | X | replay-complete sessions | yes | interim + locked | ≤ 2% |
| L | latency = durable write − scheduled close | per observation | all | — | continuous | ≤ 240 s |

- **P6 deadline.** An obligation's deadline is 23:59:59.999999 America/New_York
  on the second weekday after the ET date of its outcome-availability
  timestamp. Weekdays are Monday–Friday; there is no holiday calendar.
  Comparison is exact, with no rounding. A zero |H| is not evaluable, which
  is a locked-review failure.
- **P5 violations.** P5 counts, at minimum:
  1. a durable-write timestamp earlier than the scheduled decision time
     (negative latency);
  2. a source bar treated as closed before its scheduled close;
  3. any decision-time field derived from a bar whose close is later than
     the decision time;
  4. any outcome information present in the immutable decision-time
     observation;
  5. any live observation created retroactively from replay data;
  6. any accepted observation whose timestamp ordering can't be proven
     valid. This includes an observation accepted as timely while clock
     health was unknown or out of tolerance.

  Clock uncertainty that is detected, and fails closed, **before** recording
  is a bounded exclusion or pause (`clock_unhealthy`), not a P5 violation.
  Using an untrusted clock to accept a supposedly timely observation **is**
  a P5 violation.
- **P3 versus P7.** P3 = M_rc / X is exact-match coverage over
  replay-complete sessions only. Exact matches on collection days that are
  not replay-complete stay classified in U for reconciliation reporting but
  never enter P3. P7 is the narrower, unexplained subset of non-matches
  across all of U. An explained non-match lowers P3 but not P7. Each
  reconciliation unit belongs to exactly one §I class.
- **Overlaps, stated explicitly.** A slot can fail P2 (not timely) and also
  appear in P8 (it had an error event): P2 measures final timeliness, while
  P8 measures error incidence. Likewise, a missing slot counts against both
  P2 and P3. No other double counting occurs.

### H.2 Behavioral population and reporting (descriptive only)

**Population.** A primary-population member is an eligible shadow
candidate (E1–E8) that meets all of the following:
- it is `timely`;
- `prefix_complete` is true;
- it is classified `exact_match`;
- it has no `population_excluded` event;
- it sits in a complete covered session of the locked sample.

Above-VWAP is primary and below-VWAP is secondary. A candidate contributes
to a horizon's estimate only through an obligation in H for that horizon
that is terminal in state `outcome_recorded`. Technical terminal
obligations carry no outcome value. Per-horizon observation and session counts differ by design
(§B.5).

**Reporting.**
- Report the preregistration's frozen estimands with the frozen method:
  session-level mean signed return per side × horizon, touch rate, MFE/MAE,
  floored percentage retraced, and session-blocked bootstrap intervals
  (10,000 replicates, `random.Random(20260923)`, `[249]`/`[9749]`, exact
  arithmetic).
- There are no new significance thresholds, no p-value decisions, no
  copying of confirmation values as targets, and no tuning.
- Observation-level points are never treated as independent.
- A **replay-population sensitivity** report (all eligible replay points in
  the same sessions) shows any selection caused by operational misses.

### H.3 Sample lock and calendar endpoints

- **Endpoints.** Let S0 be the first collection session's ET date. Both
  endpoints are **ET calendar dates at 00:00:00 America/New_York**,
  converted to UTC for storage:
  - `review_not_before` = S0 + 84 days (12 weeks)
  - `hard_stop` = S0 + 182 days (26 weeks)
- **Locked sample.** Exactly the **first 60 complete covered sessions** in
  date order. Once the 60th is reconciliation_complete, no later session
  may enter.
- **Stopping at the 60th.** Collection stops after the session close at
  which the provisional count of complete covered sessions reaches 60.
  - If reconciliation later demotes one of them, collection resumes at the
    next full session, but only before `hard_stop`.
  - Any observation collected beyond the 60th qualifying session is outside
    the locked sample and is never inspected in the locked review.
- **When review may happen.** The locked behavioral review may occur only
  when both hold:
  1. all 60 locked-sample sessions are reconciliation_complete (§B.5); and
  2. the current time is ≥ `review_not_before`.
- **Hard stop.** No session dated on or after `hard_stop` may enter. If
  fewer than 60 complete covered sessions exist from sessions before
  `hard_stop` (reconciliation may complete up to 2 weekdays later), the label
  is `shadow_insufficient_sessions`. The window is never extended.

### H.4 Early-stop classes (outcome-blind)

| Class | Conditions | Action |
|---|---|---|
| **Immediate permanent stop** | Confirmed P5 timing or look-ahead violation; P4 accepted duplicate; any observation dated in the holdout window or before authorization; configuration or protocol identity change within the sample | Stop; label `shadow_stopped_integrity_violation` |
| **Pause pending repair** | Reproducible unexplained mismatch cause; `clock_unhealthy`; storage error; provider outage lasting a full session; security issue; interim P1–P3/P6–P8 failure | Pause. Paused sessions aren't covered (they count in P1's denominator if replay-complete). Resume only after a documented, reviewed repair. A repair may change `code_commit_sha` but never the configuration, protocol, thresholds, or candidate definition |
| **Continue with explained exclusion** | Isolated stale, late, or missing slots; provider errors within a session; structurally incomplete days; units carrying a §I.2 explained code | Recorded with fixed reasons; judged only through P1–P8 |
| **Fail only at locked review** | P1, P2, P3, P6, P7, P8 below threshold | Label `shadow_process_not_feasible` |

- **Outcome-blind decisions.** Repair and pause decisions use only process
  metrics and sanitized reconciliation counts. Behavioral outcome fields
  stay concealed and are not queried or displayed until the locked review.
- **Never for results.** Collection is never stopped, extended, or
  restarted because of behavioral results.

### H.5 Locked-review label (first match wins)

1. `shadow_stopped_integrity_violation`: a P4 count > 0 or a P5 count > 0,
   or any other immediate-stop condition in H.4.
2. `shadow_insufficient_sessions`: fewer than 60 complete covered sessions
   before `hard_stop`.
3. `shadow_process_not_feasible`: any of P1, P2, P3, P6, P7, or P8 fails or
   is not evaluable. This also applies if any required candidate-horizon
   obligation in H is **not terminal**, or if any of the three above-VWAP
   horizons lacks a **nonempty** set of contributing sessions. A
   contributing session is one with at least one obligation for that
   horizon, in the primary population, that is terminal in state
   `outcome_recorded`.
4. **Direction labels.** Assigned only if rules 1–3 did not match, so the
   integrity, session, and process gates have all passed, the locked
   sample is reconciliation_complete, and each above-VWAP horizon has a
   nonempty contributing-session set. Each label uses the exact rational
   session-level mean signed return of the primary population per
   above-VWAP horizon, computed only from obligations terminal in state
   `outcome_recorded`. An estimate of exactly 0 counts as ≤ 0.
   - `shadow_feasible_direction_consistent`: all three > 0.
   - `shadow_feasible_direction_inconsistent`: two or more ≤ 0.
   - `shadow_feasible_direction_mixed`: exactly one ≤ 0.

**Evidence against continuing:** labels 1, 2, 3, and
`direction_inconsistent`. The direction rule is a descriptive consistency
check, not a significance test. **No label means validated, profitable,
accurate, or ready to trade, and none authorizes any further stage.**

## I. Reconciliation and audit

After each session, a post-session replay rebuilds every slot from the
day's stored bars using the frozen code and configuration.

### I.1 Units and kinds

- **Units.** A reconciliation unit is every slot identity present in either
  the replay or the live observations, over **all collection days**, so
  U = Σ units. On replay-complete sessions the units are exactly the X
  expected slots.
- **Kinds.** Each non-matching unit has one kind:
  - `live_missing`: replay slot present, no live observation
  - `extra_live`: live observation present, replay slot absent
  - `field_mismatch`: both present, but a decision-time field or
    classification differs

### I.2 Frozen explained-cause vocabulary (exhaustive)

The only permitted **explained** classifications are the ten codes below.
Nine apply to reconciliation units; `structurally_unavailable_horizon`
applies only to candidate-horizon obligations (§B.5). There is no generic
"explained field mismatch".

| # | Code | Applies to kind(s) | Required contemporaneous evidence |
|---|---|---|---|
| 1 | `structurally_incomplete_session` | any, only on days the replay classifies as failing the 78-slot contract (existing `SessionExclusionReason`) | the replay's session classification |
| 2 | `replay_source_unavailable` | `extra_live`; obligation terminal technical reason | a bounded event written by the reconciliation job when source bars can't be obtained, before any outcome is inspected |
| 3 | `authorized_session_not_active` | `live_missing` | an authorization/collection-period record showing the slot outside an active authorized period, written before the slot |
| 4 | `authorized_pause_recorded_before_affected_slot` | `live_missing` | a pause event whose timestamp precedes the slot's scheduled close |
| 5 | `clock_health_failure_detected_live` | `live_missing` | a `clock_unhealthy` event for that slot, written live |
| 6 | `provider_outage_documented_before_reconciliation` | `live_missing` | a `provider_unavailable` / `provider_invalid_response` event for that slot, written live before its L deadline |
| 7 | `stale_input_detected_live` | `live_missing`; `field_mismatch` only where the live observation had `prefix_complete=false` and the missing prefix bar's own slot carries a live `stale_input` event | a live `stale_input` event for the affected slot |
| 8 | `malformed_input_rejected_live` | `field_mismatch` (live contract failure E1–E3 while replay is valid); `live_missing` (invocation refused for `wrong_provenance`) | the live observation's or refusal event's bounded contract-failure code |
| 9 | `transactional_write_failure_recorded_live` | `live_missing`; obligation terminal technical reason | a live `storage_error` event for that slot or obligation |
| 10 | `structurally_unavailable_horizon` | candidate-horizon pairs only (never a reconciliation unit) | the slot index *k* (§B.5), recorded at write time |

**Requirements for any explained classification:**
- The evidence was created contemporaneously, before any behavioral outcome
  was inspected.
- It carries a bounded event code from the vocabulary above.
- It carries the affected session and slot (or horizon) identity.
- It carries a first-observed timestamp.
- It contains no unrestricted exception text.
- It is never chosen on the basis of any outcome's direction or magnitude.

**What does not qualify:**
- **Post-hoc explanations.** Operator explanations written after the fact
  never qualify.
- **No live evidence.** An event with no contemporaneous bounded evidence is
  unexplained. A provider outage discovered only during replay stays
  unexplained unless the live system recorded contemporaneous evidence of
  it.
- **Revisions.** Provider revisions and late-arriving bars without a live
  `stale_input` event are unexplained.
- **Changing codes.** No new code may be added during a running sample.
  Adding or changing a code needs a reviewed protocol version and a new
  collection period (§L).

### I.3 Classification precedence (exactly one class per unit)

Each unit receives the **first** matching class:

1. `timing_violation`: any P5 condition (§H.1).
2. `exact_match`: both present, and every decision-time field and
   classification equal.
3. The **first applicable explained code**, in the §I.2 order 1 → 9, whose
   kind matches and whose required evidence exists.
4. Otherwise, the unexplained class for its kind: `unexplained_live_missing`,
   `unexplained_extra_live`, or `unexplained_field_mismatch`.

The class counts sum exactly to U. P3 uses the class-2 units of
replay-complete sessions (M_rc) over X. P7 uses the
three unexplained classes over U. P5 uses class 1 plus any other P5
condition. No unit is counted in more than one class.

### I.4 Preservation

- **Originals are preserved.** Live observations are never edited. A
  correction is a linked, append-only `audit_correction` event that can
  never change a unit's class to "explained" without §I.2 evidence.
- **No backfilling.** A missed slot is never backfilled as though it were
  detected live.
- **Sanitized reporting.** Reports give sanitized counts per class, per
  code, and per session only.

## J. Storage (protocol-level design selection)

**Selected design:**
- **Versioned DuckDB tables** for slot observations and slot events.
- **Append-only lifecycle.** Lifecycle and audit events are append-only.
- **Uniqueness.** A uniqueness constraint on `slot_id` enforces
  deterministic identity.
- **Transactions.** Writes are transactional.
- **No overwrite.** Original observations are never overwritten.
- **Reviewed changes only.** Any migration and repository needs its own
  explicit review. The database is backed up before any migration.
- **Exports.** Sanitized exports are produced for review only if needed.

**Why not loose files.** Gitignored loose files are rejected as the
canonical store. They offer weaker transactional integrity, no concurrency
control, no enforced uniqueness, and harder reconciliation joins.

**Not authorized.** This is a design selection only. It does not authorize
a migration, a table, or any database write.

## K. Authorization ladder

Each stage needs explicit authorization. **No stage automatically
authorizes the next.**

1. Protocol review and merge, which freezes this document (§L).
2. Separately authorized design of the contract, recorder, migration, and
   repository, including the bounded IEX latency/connectivity test (§F.2).
3. Offline implementation with synthetic tests only.
4. Deterministic replay testing using only already-authorized, non-holdout
   fixtures (for example the confirmation window 2026-02-23 → 2026-08-14,
   read-only). This checks only that the recorder's decision-time fields
   equal the frozen evaluator's (pipeline equivalence). It reports no new
   outcome statistics. Never discovery re-analysis, never holdout dates.
5. A separately reviewed unattended-operation and security review.
6. Explicit collection authorization, given only after the holdout result
   is recorded (§G).
7. Bounded shadow collection under the frozen gates.
8. Locked review (§H.5), recorded in full, including unfavorable or
   insufficient outcomes.

**Permanently out of scope:** the Options Strategy Agent, alerts framed as
recommendations, contract selection, order routing, and automated
execution.

## L. Freezing and version identity

- **Frozen only by merge.** A document cannot contain the SHA of the commit
  that first contains it without a self-reference problem. So this
  document does not embed its own merge SHA.
- **External provenance.** The merged protocol commit SHA is treated as
  external, immutable provenance. The future implementation's frozen
  configuration and every slot observation record it as
  `protocol_commit_sha`.
- **Amendments.** Any amendment needs a new, reviewed protocol version. It
  never silently modifies a running collection period. An amendment
  during collection starts a new period with a new sample.

## M. Reporting tiers and caveats

1. **Operational/process metrics:** P1–P8, latency distribution,
   reconciliation classes. These are the feasibility evidence.
2. **Underlying behavioral outcomes:** §H.2, descriptive only.
3. **Exploratory context:** regime, time of day, extension bucket, prior-day
   availability. No claim, and no effect on any label.
4. **Prohibited interpretations:** profitability, options edge, trade
   signals, recommendations, sizing, validation, or agent authorization.

**Carried forward verbatim from the confirmation result:**

- `underlying_setup_only_no_options_or_pnl`
- `research_result_not_validation`
- `not_a_recommendation_or_trading_action`
- `labels_never_mean_validated_profitable_accurate_or_tradeable`
- `observation_level_points_overlap_no_inference`
- `thresholds_frozen_unmodified`
- `next_session_unavailable_no_exchange_calendar`
- `options_strategy_agent_not_authorized`

**Also:**
- IEX data only.
- No options returns, transaction costs, or P&L.
- No relative-volume, catalyst, or breadth conclusions unless separately
  preregistered.
- The code commit is operator-supplied, not externally attested.

## Open questions for review

1. **Latency feasibility.** Can IEX deliver closed bars within the proposed
   L? This needs a bounded connectivity test at stage 2.
2. **Values to freeze.** P1–P8, L, the 2-weekday P6 deadline, and
   the 60 / 84-day / 182-day values need review and freezing.
3. **Time source.** Which documented time source to use, and whether the
   ±1-second skew tolerance is practical on the target host.
4. **Post-session data.** The exact post-session replay data acquisition
   (bounded ingestion through the existing connector) needs design review.
