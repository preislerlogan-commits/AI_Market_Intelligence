# SPY VWAP Shadow Recorder — Stage 2 Design

**Status: REVIEWED STAGE 2 DESIGN. Current Stage 2 status is
`design_complete_test_pending`, not complete (§O). DESIGN ONLY.** This
document proposes the contracts,
components, storage, and procedures a future implementation would follow. It
contains no production code.

**Not authorized by this document:**
- implementation
- creating or applying a migration
- database writes
- provider or network requests
- running the latency test
- scheduling or unattended operation
- shadow collection
- holdout access
- alerts, recommendations, options work, execution, or the Options Strategy
  Agent

The bounded IEX latency test is specified separately in
[SPY_VWAP_IEX_LATENCY_TEST_PLAN.md](SPY_VWAP_IEX_LATENCY_TEST_PLAN.md).

## A. Source-of-truth boundary

The frozen shadow protocol,
[SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md](SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md)
**as merged at commit `f79d37e6c762e35da3d575c35276a0be5adbc66a`**, governs
every definition. This design does not reinterpret or change:

- E1–E8 eligibility
- P1–P8
- L = 240 s
- candidate-horizon structural availability (30m *k* ≤ 71, 2h *k* ≤ 53,
  close *k* ≤ 76)
- the sample lock (60 / 84 days / 182 days)
- the explained-cause vocabulary and classification precedence
- label rules
- the authorization ladder

Protocol section references below (§B.2, §H.1, …) mean that commit.

**Where the frozen protocol left room.** Its §D contract is explicitly
"proposed (not implemented)", so Stage 2 may choose field placement and
table layout, but never change a definition. Where the design needs
operational-only vocabulary the protocol does not name (for example
recorder-start events), that vocabulary is **audit-only**. It can never act
as §I.2 explained-cause evidence except where §I.2 already names it.

**Conflict review result.** No inconsistency requiring a protocol amendment
was found. Three issues needed a design-level resolution that stays within
the protocol's text; they are listed in §O.

## B. Component architecture

Retrieval, evaluation, and persistence are kept separate. **Pure** means no
I/O, no clock read, and no randomness; all time comes in as an argument.

| # | Component | Responsibility | Inputs | Outputs | Pure? | Failure behavior | Error vocabulary | Idempotency | Prohibited |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Collection-period authorizer | Validate and persist one immutable `ShadowCollectionPeriod` from an explicit operator authorization | operator-supplied period ordinal, start session date, `code_commit_sha`; frozen config | period row | no (one write) | refuse on any identity, date, or holdout conflict | `identity_mismatch`, `storage_error` | same inputs → same `collection_period_id`; an identical re-insert is a no-op | inferring dates; authorizing holdout dates; authorizing collection by default |
| 2 | Session activator | Before 09:30 ET, confirm an active period, write `ShadowSessionAuthorization`, determine `prior_day_status` | period, session date, clock-health result, read-only prior-day lookup | authorization row, `session_activated` event | no | if not complete before 09:30 ET, skip the whole session (protocol §G) | `clock_unhealthy`, `identity_mismatch`, `storage_error` | deterministic `session_id` | activating mid-session; activating holdout or pre-period dates |
| 3 | Clock-health verifier | Measure wall-clock offset against the documented time source, and wall/monotonic consistency | time-source reading, OS wall clock, monotonic clock | `ShadowClockCheck` row; `clock_health_verified` or `technical_error(clock_unhealthy)` | no | unknown or out of tolerance → fail closed | `clock_unhealthy` | deterministic check id (session + ordinal) | running inside a latency window (§E) |
| 4 | Slot scheduler | Compute the 78 scheduled closes for a session date; hand each slot to the pipeline at its close | session date, America/New_York zone | ordered slot identities with `scheduled_close_utc` | **pure** | invalid date → refuse | none (validation) | deterministic | reading the clock (it receives "now"); skipping or re-ordering slots |
| 5 | Closed-bar retriever | After the scheduled close, request the session prefix `[09:30 ET, slot start]` through the existing read-only `AlpacaBarsClient` | slot identity, now ≥ scheduled close | prefix bars in memory, `first_observed_utc`, bounded status | no (network) | poll until close + L (§H); then give up | `stale_input`, `provider_unavailable`, `provider_invalid_response`, `invocation_timeout`, `wrong_provenance` | retries are the same logical request | writing `market_bars`; any request before the scheduled close; any non-SPY or non-IEX request |
| 6 | Prefix validator | Check E1–E3 and prefix completeness; build a validated `RegimeEngineInput` prefix | prefix bars, slot identity | validated prefix, or a `contract_failure_reason` (`SessionExclusionReason`) | **pure** | a contract failure is a classification, not an exception | existing `SessionExclusionReason` values | deterministic | repairing, interpolating, or reordering bars |
| 7 | Frozen evaluator adapter | Produce decision-time fields by calling the **unmodified** `evaluate_spy_vwap_reversion` on a one-session input truncated at slot *k*, taking decision point *k* | validated prefix, `prior_day`, narrowed context | decision-time field set (E4–E8, regime context); **every outcome field discarded** | **pure** | refuse (never guess) on evaluator error | `unexpected_error` | deterministic | using bars after *k*; persisting any outcome field; changing thresholds |
| 8 | Observation writer | Write one immutable `ShadowSlotObservation` plus its structural obligations in one transaction; then append `observation_commit_confirmed` | observation, obligations | rows, then a confirmation event | no | on write failure, roll back and emit `technical_error(storage_error)` via the event writer or fallback journal | `storage_error`, `duplicate_rejected` | PK on `slot_id`: identical content is a no-op; differing content is refused | UPDATE/DELETE; writing outcome values |
| 9 | Event writer | Append bounded `ShadowEvent` rows | typed event | row | no | on DuckDB failure, append to the fallback journal (§F.6) | `storage_error` | deterministic `event_id` | free-text payloads; editing events |
| 10 | Obligation materializer | For an eligible observation, derive the structurally available horizons from *k* and their availability and deadline timestamps | slot index, scheduled close, session date | `ShadowOutcomeObligation` rows (written by #8) | **pure** | none | none | deterministic `obligation_id` | reading outcomes; creating obligations for structurally unavailable horizons |
| 11 | Outcome finalizer | After replay, compute each obligation's outcome with the frozen evaluator's own window/outcome functions, the live frozen VWAP/close/side, and the replay bars; record the terminal state | obligations, replay bars, live observation | `ShadowOutcomeRecord` rows | no | cannot compute → a technical terminal reason (`replay_source_unavailable` / `transactional_write_failure_recorded_live`) | same | one record per obligation (PK) | changing eligibility or the observation; letting a value decide terminal status |
| 12 | Post-session replay | Bounded post-session ingestion of the session's bars into `market_bars` (existing connector and `BarRepository`), build a one-session input with the existing input builder, run the frozen evaluator | session date | `ShadowReplayRun` row (classification, prefix hashes), replay decision points in memory | no | any failure → `replay_source_unavailable` event; never guess | `replay_source_unavailable`, `storage_error` | one replay run per session (retry of a failed run allowed; succeeded run immutable) | ingesting any other date; ingesting holdout dates |
| 13 | Reconciler | Classify every unit (§J) and complete the session | observations, events, replay run | `ShadowReconciliationUnit` rows, `reconciliation_completed` event | classification **pure**; persistence not | refuse if inputs incomplete | `unexpected_error` | one unit per `slot_id` (PK) | post-hoc explanations; reading outcome values |
| 14 | Metrics and locked-review exporter | Compute P1–P8 from stored facts; admit sessions; seal the manifest; export sanitized review files | stored facts | `ShadowProcessMetrics`, `ShadowLockedReviewManifest`, sanitized exports | computation **pure**; sealing writes | not evaluable → failure at the locked review | `unexpected_error` | the manifest is content-addressed | reading outcome values before sealing (§K) |

## C. Versioned contracts (proposed; not implemented)

### C.1 Common rules

**Model style.** Pydantic v2, `extra="forbid"`, `frozen=True`, strict types,
bounded collections, finite decimals only.

**Serialization.**
- Canonical JSON uses `model_dump(mode="json")`, sorted keys, UTF-8.
- Identity hashing uses compact separators `(",", ":")`. Files use
  `indent=2` plus one trailing newline, as in the existing SPY VWAP
  serializers.
- Decimals are serialized as strings. Datetimes are ISO-8601 UTC.

**Size bounds.**
- Tokens: at most 64 characters, `^[a-z0-9_]+$`.
- IDs: at most 160 characters, `^[A-Za-z0-9:_.\-]+$`.
- SHA-256: 64 lowercase hex characters. Commit SHA: 40 lowercase hex
  characters.
- Lists are bounded as stated per field.

**Forbidden everywhere.** No field may hold any of the following:
- credentials, API keys, headers, URLs, or query strings
- raw provider bodies
- exception messages or tracebacks
- free-text notes
- positions, sizes, orders, or accounts
- recommendations or strategy output

**Field classes.** **D** = decision-time (immutable at write); **L** =
later-derived (written in its own later append-only row); **A** =
audit-only. All persisted contracts are immutable.

**Authority model.**
- **Facts.** Immutable observations and the append-only rows (events,
  obligations, outcome records, replay runs, reconciliation units, clock
  checks) are the **only** factual source of truth.
- **Derived views.** Views, session summaries, and metrics (§C.12) are
  derived and **never authoritative**.
- **The manifest.** The sealed locked-review manifest (§C.13) is a
  canonical, immutable **snapshot** that identifies the locked sample and
  its provenance. It is not an independent source of facts: it must remain
  reproducible from the append-only records, and a recomputation mismatch is
  an integrity failure.

### C.2 Reused enums (no synonyms created)

| Enum | Source | Use |
|---|---|---|
| `EligibilityStatus` | `spy_vwap_reversion_contracts` | eligible / vwap_unavailable / zero_extension |
| `ExtensionSide` | same | above_vwap / below_vwap |
| `ForwardHorizon` | same | restricted to the three structural horizons; `next_session` never stored |
| `Regime`, `ScenarioHorizon`, `TimeOfDayBucket` | `spy_regime_contracts` | exploratory context |
| `ExtensionBucket` | `spy_vwap_reversion_confirmation_contracts` | exploratory context |
| `SessionExclusionReason` | `spy_vwap_reversion_input_builder` | contract failures; replay session classification |
| `PriorDayStatus` | same | prior-day availability |

### C.3 New bounded enums

- **`ShadowEventType`** (audit-only unless §I.2 names it):
  - `recorder_started`, `recorder_stopped`, `recorder_restarted`
  - `session_activated`
  - `clock_health_verified`
  - `technical_error`
  - `missing_slot_recorded`
  - `observation_commit_confirmed`, `observation_commit_confirmation_missing`
  - `duplicate_rejected`
  - `provider_revision_observed`
  - `pause_recorded`, `resume_recorded`
  - `replay_completed`, `replay_source_unavailable`
  - `population_excluded`
  - `reconciliation_completed`
  - `session_admitted`
  - `collection_paused_provisional_limit`, `collection_resumed`
  - `hard_stop_reached`
  - `integrity_stop`
  - `manifest_sealed`
  - `audit_correction`
- **`ShadowTechnicalError`** (the `reason_code` of `technical_error`):
  - `stale_input`
  - `provider_unavailable`, `provider_invalid_response`
  - `storage_error`
  - `clock_unhealthy`
  - `wrong_provenance`
  - `identity_mismatch`
  - `invocation_timeout`
  - `unexpected_error`
- **`ShadowPopulationExclusionReason`**: `prefix_incomplete`,
  `late_detection`, `timeliness_unproven`, `structurally_incomplete_day`.
  `timeliness_unproven` is the fail-closed case where commit confirmation is
  absent or inconsistent (§E); it is treated like `late_detection`.
- **`ShadowOutcomeTerminalState`**: `outcome_recorded`,
  `replay_source_unavailable`, `transactional_write_failure_recorded_live`.
  The latter two are the protocol's technical terminal reasons.
- **`ShadowExplainedCode`** (exactly the ten §I.2 codes, in frozen order):
  1. `structurally_incomplete_session`
  2. `replay_source_unavailable`
  3. `authorized_session_not_active`
  4. `authorized_pause_recorded_before_affected_slot`
  5. `clock_health_failure_detected_live`
  6. `provider_outage_documented_before_reconciliation`
  7. `stale_input_detected_live`
  8. `malformed_input_rejected_live`
  9. `transactional_write_failure_recorded_live`
  10. `structurally_unavailable_horizon` (obligation-level only)
- **`ShadowReconciliationKind`**: `live_missing`, `extra_live`,
  `field_mismatch`. Null for `exact_match`.
- **`ShadowReconciliationClass`**: `timing_violation`, `exact_match`,
  `explained`, `unexplained_live_missing`, `unexplained_extra_live`,
  `unexplained_field_mismatch`. When the class is `explained`, a separate
  `explained_code` (`ShadowExplainedCode` 1–9) is required, so the codes are
  never duplicated as class values.
- **`ShadowCollectionPeriodStatus`** (derived from events):
  `authorized`, `active`, `paused`, `paused_provisional_limit`,
  `locked`, `stopped_integrity_violation`,
  `closed_insufficient_sessions`.
- **`ShadowSessionStatus`** (derived):
  `authorized`, `active`, `collected_pending_reconciliation`,
  `structurally_incomplete`, `not_covered`,
  `reconciliation_complete_admitted`,
  `reconciliation_complete_not_admitted`, `overflow_not_inspected`.
- **`ShadowTimeliness`** (derived): `timely`, `late`, `unproven`.

### C.4 `ShadowConfigurationSnapshot` — `spy-vwap-shadow-configuration-1`

It is identity-bearing: its canonical SHA-256 is the `configuration_sha256`.
Every field is **D**, immutable, and not null.

**Fields:**
- `protocol_commit_sha`: the literal
  **`f79d37e6c762e35da3d575c35276a0be5adbc66a`**, the protocol's merge
  commit. This is distinct from the confirmation study's base
  preregistration commit.
- `schema_versions`: a map of every shadow schema string, bounded to 16
  entries.
- `provenance`: the literals `alpaca`, `SPY`, `5Min`, `iex`, `raw`, `USD`.
- `regime_thresholds`: the existing `RegimeThresholdsSnapshot`, validated
  equal to the defaults.
- `evaluator_sample_thresholds`: 50 / 20.
- `narrowed_context`: `None` / `unknown` / `unavailable`.
- `slot_count` = 78; `bar_minutes` = 5.
- `structural_limits`: `{intraday_30m: 71, intraday_2h: 53,
  to_session_close: 76}`.
- `latency_bound_seconds` = 240.
- `clock_tolerance_ms` = 1000.
- `p_thresholds`:
  - P1: 19/20
  - P2, P3, P6: 99/100
  - P4, P5: 0
  - P7: 1/1000
  - P8: 1/50
  - all as exact fractions
- `p6_deadline_weekdays` = 2.
- `locked_sessions` = 60; `review_not_before_days` = 84;
  `hard_stop_days` = 182.
- `explained_codes`: the ordered list of the ten codes.
- `pct_floor_dollars` = `0.10`.

**Validation.** Must equal the frozen constants exactly. Any difference is
an `identity_mismatch`.

### C.5 `ShadowCollectionPeriod` — `spy-vwap-shadow-collection-period-1`

| Field | Type | Null | Validation | Source | Class |
|---|---|---|---|---|---|
| `schema_version` | literal | no | exact | constant | D |
| `collection_period_id` | id | no | §D | derived | D |
| `period_ordinal` | int | no | 1–999 | operator authorization | D |
| `protocol_commit_sha` | sha40 | no | = frozen constant | config | D |
| `configuration_sha256` | sha64 | no | = hash of C.4 | derived | D |
| `code_commit_sha` | sha40 | no | 40 lowercase hex; operator-supplied, not attested | operator | D |
| `start_session_date` (S0) | date | no | weekday; ≥ 2026-12-07; never inside 2026-09-23 → 2026-12-04 | operator | D |
| `review_not_before_utc` | datetime | no | S0 + 84 days 00:00 America/New_York → UTC | derived | D |
| `hard_stop_utc` | datetime | no | S0 + 182 days 00:00 America/New_York → UTC | derived | D |
| `holdout_result_reference` | sha64 | no | hash of the recorded holdout result artifact (reference only) | operator | A |
| `authorization_token` | token | no | bounded token naming the authorization, e.g. `stage7_collection_auth_1` | operator | A |
| `authorized_at_utc` | datetime | no | < first slot of S0 | clock | A |

### C.6 `ShadowSessionAuthorization` — `spy-vwap-shadow-session-authorization-1`

Fields:
- `schema_version`
- `session_id` (§D)
- `collection_period_id`
- `session_date`: a weekday, ≥ S0, < `hard_stop`, not a holdout date
- `first_slot_close_utc`: 09:35 ET → UTC
- `authorized_before_open`: must be true (activation time < 09:30 ET)
- `activated_at_utc` (A)
- `prior_day_status` (`PriorDayStatus`)
- `prior_day_levels_sha256`: nullable; hash only, never the levels
- `clock_check_id`: the healthy pre-open check

All fields are **D** and immutable.

### C.7 `ShadowSlotIdentity` (value object, not persisted separately)

- `session_date`
- `slot_index` *k*: 0–77
- `slot_id` = `SPY:<YYYY-MM-DD>:<k>` (frozen format)
- `bar_start_utc`
- `scheduled_close_utc` = bar start + 5 min

All are derived purely from the date and *k*.

### C.8 `ShadowSlotObservation` — `spy-vwap-shadow-slot-observation-1`

Immutable. One per `slot_id`.

**Identity and provenance**

| Field | Type | Null | Validation | Class |
|---|---|---|---|---|
| `schema_version`, `slot_id`, `collection_period_id`, `session_date`, `slot_index`, `scheduled_close_utc` | as C.7 | no | consistent with C.7 | D |
| `provider`, `symbol`, `timeframe`, `feed`, `adjustment`, `currency` | literals | no | exact | D |
| `protocol_commit_sha`, `configuration_sha256`, `code_commit_sha` | sha | no | equal to the period's values (a reviewed repair may change only `code_commit_sha`) | D |

**Source reference**

| Field | Type | Null | Validation | Class |
|---|---|---|---|---|
| `prefix_first_bar_utc`, `prefix_last_bar_utc` | datetime | no | the last bar equals slot *k*'s bar start | D |
| `prefix_bar_count` | int | no | 1–78 | D |
| `prefix_sha256` | sha64 | no | SHA-256 of the canonical prefix (timestamps + OHLCV as strings); never the bars themselves | D |

**Decision-time fields**

| Field | Type | Null | Validation | Class |
|---|---|---|---|---|
| `contract_failure_reason` | `SessionExclusionReason` | yes | set ⇔ E2/E3 failed | D |
| `eligibility_status` | `EligibilityStatus` | yes | null ⇔ contract failure | D |
| `extension_side` | `ExtensionSide` | yes | set ⇔ eligible | D |
| `prefix_complete` | bool | no | gapless from 09:30 | D |
| `signal_close` | Decimal(18,6) | yes | finite, > 0; null ⇔ contract failure | D |
| `signal_vwap` | Decimal(18,6) | yes | null ⇔ contract failure or `vwap_unavailable` | D |
| `close_to_vwap_distance_bps` | Decimal(18,4) | yes | as evaluator | D |
| `normalized_vwap_extension` | Decimal(18,6) | yes | as evaluator | D |
| `extension_bucket`, `regime`, `scenario_horizon`, `time_of_day_bucket` | enums | bucket and regime nullable on contract failure | as evaluator; context only | D |
| `prior_day_status` | `PriorDayStatus` | no | equals the session authorization | D |
| `structurally_available_horizons` | list[`ForwardHorizon`] | no | exactly the §B.5 set for *k*; eligible only (empty otherwise); ≤ 3 | D |

**Timing and operation**

| Field | Type | Null | Validation | Class |
|---|---|---|---|---|
| `first_observed_utc` | datetime | no | ≥ `scheduled_close_utc` | D |
| `pre_commit_utc` | datetime | no | ≥ `first_observed_utc`; captured just before COMMIT; **never** the durable-write timestamp | D |
| `pre_commit_mono_ns`, `first_observed_mono_ns` | int | no | ≥ 0; monotonic-clock readings | A |
| `clock_check_id` | id | no | the most recent healthy check (§E) | D |
| `detector_invocation_id` | id | no | §D | A |
| `pre_commit_after_latency_bound` | bool | no | true ⇔ `pre_commit_utc − scheduled_close_utc > 240 s` (certainly late) | D |

**Forbidden in this contract:** outcome fields of any kind, including
outcome values, touch, MFE/MAE, availability of later bars, and free text
(that is protocol P5 #4 plus sanitization).

`pre_commit_after_latency_bound = true` is the protocol's
`late_observation_written` (`observed_late`) case. When it is false,
timeliness is still decided only by the post-commit timestamp (§E.4).

### C.9 `ShadowEvent` — `spy-vwap-shadow-event-1`

Fields:
- `schema_version`
- `event_id` (§D)
- `event_type` (`ShadowEventType`)
- `scope`: `period` / `session` / `slot` / `obligation`
- `collection_period_id`
- `session_date` (nullable)
- `slot_id` (nullable)
- `horizon` (nullable)
- `reason_code`: nullable; must belong to the enum implied by
  `event_type`, i.e. `ShadowTechnicalError`, `ShadowPopulationExclusionReason`,
  `SessionExclusionReason`, or `ShadowExplainedCode`
- `occurrence_key`: token, ≤ 64 characters, from the schedule only (§D.2)
- `event_utc`
- `event_mono_ns` (nullable)
- `post_commit_utc`, `post_commit_mono_ns`: nullable; required ⇔
  `observation_commit_confirmed`. These are the timestamps captured
  immediately after the observation COMMIT returned (§E.4)
- `source`: `live` / `reconciliation` / `fallback_journal_import`
- `code_commit_sha`
- `audit_of_event_id`: nullable; required ⇔ `audit_correction`
- `session_digest_sha256`: nullable; required ⇔ `reconciliation_completed`

All fields are **A** or **L** and immutable.

**Validation.**
- Scope and nullable fields must be consistent with `event_type`.
- No payload dictionary exists.
- `audit_correction` may never carry a `ShadowExplainedCode` unless the
  referenced original has §I.2 evidence.

### C.10 `ShadowOutcomeObligation` — `spy-vwap-shadow-outcome-obligation-1`

Fields:
- `schema_version`
- `obligation_id` = `<slot_id>:<horizon>`
- `slot_id`
- `session_date`
- `horizon` (one of the three)
- `availability_utc`: decision + 30 min, decision + 120 min, or 16:00 ET
- `deadline_utc`: 23:59:59.999999 America/New_York on the second weekday
  after `availability_utc`'s ET date

All fields are **D**. It is created in the observation transaction, only for
eligible observations, and only for horizons structurally available at *k*.

**`ShadowOutcomeRecord`** — `spy-vwap-shadow-outcome-record-1`. This is the
L-class companion to the obligation; one per obligation.

Fields:
- `obligation_id`
- `terminal_state` (`ShadowOutcomeTerminalState`)
- `recorded_at_utc`
- `replay_run_id`
- outcome values, all nullable and required ⇔ `outcome_recorded`:
  - `touched_vwap` (bool)
  - `bars_to_touch`, `minutes_to_touch`
  - `signed_return_toward_vwap_bps`: Decimal(18,4)
  - `max_favorable_excursion_bps`, `max_adverse_excursion_bps`: Decimal(18,4)
  - `pct_extension_retraced`: Decimal(18,4), null below the $0.10 floor
  - `pct_floor_applied` (bool)

`successfully_finalized` is **not stored**. It is always derived as
`terminal_state = outcome_recorded AND recorded_at_utc ≤ deadline_utc`.

### C.11 `ShadowReconciliationUnit` — `spy-vwap-shadow-reconciliation-unit-1`

Fields:
- `schema_version`
- `unit_id` = `<slot_id>:reconciliation`
- `slot_id`
- `session_date`
- `collection_period_id`
- `replay_run_id` (nullable when replay is unavailable)
- `kind` (`ShadowReconciliationKind`, nullable)
- `classification` (`ShadowReconciliationClass`)
- `explained_code`: nullable; required ⇔ `explained`; codes 1–9 only
- `evidence_event_id`: nullable; required ⇔ `explained`, except for code 1,
  whose evidence is the replay run
- `live_prefix_sha256`, `replay_prefix_sha256` (nullable)
- `timing_violation_condition`: nullable, 1–6 (the P5 list); required ⇔
  `timing_violation`
- `classified_at_utc`

All fields are **L** and immutable. Exactly one unit exists per slot
identity.

### C.12 Derived contracts (recomputed; never authoritative)

- **`ShadowSessionSummary`** — `spy-vwap-shadow-session-summary-1`:
  - `session_date`
  - `status` (`ShadowSessionStatus`)
  - `replay_complete`
  - `covered` (all 78 invocation attempts)
  - observation, missing, timely, late, eligible, and obligation counts
  - `terminal_obligations`
  - `successfully_finalized_obligations`
  - class counts per `ShadowReconciliationClass` and `explained_code`
  - `session_digest_sha256`
  - `admitted_ordinal`: nullable, 1–60
- **`ShadowProcessMetrics`** — `spy-vwap-shadow-process-metrics-1`:
  - `scope`: `interim` / `locked`
  - for each of P1–P8: numerator and denominator as integers, the exact
    fraction, and a status of `pass`, `fail`, or `not_evaluable`
  - latency distribution descriptors in ms: p50, p95, p99, max
  - `computed_from_digest_sha256`

  It contains no outcome values.
### C.13 `ShadowLockedReviewManifest` — `spy-vwap-shadow-locked-review-manifest-1`

This is a canonical immutable snapshot, not an independent fact source (§C.1).

**`manifest_payload`** is the hashed part. It contains the frozen
substantive identity only:
- `schema_version`
- `protocol_commit_sha`
- `configuration_sha256`
- `code_commit_shas`: the sorted set of every `code_commit_sha` appearing in
  the locked facts; ≤ 16
- `collection_period_id`
- `review_not_before_utc`, `hard_stop_utc`
- `admitted_sessions`: exactly 60 entries, ordered by admission ordinal,
  each `(admitted_ordinal, session_id, session_date,
  session_digest_sha256)`
- `fact_set_digests`: for each shadow table, the SHA-256 of the canonical
  rows belonging to the period, ordered by primary key. It is computed
  inside the repository; only the digest leaves it, and outcome values are
  never returned or displayed.
- `record_counts`: per table, for the period
- `process_metric_inputs`: P1–P8 numerator and denominator integers (locked
  scope) and pass/fail/not_evaluable status

**Excluded from the payload:**
- `manifest_id`, `manifest_hash`
- the seal event's identity, the seal timestamp
- any database-generated timestamp
- signatures or external attestations
- outcome values

**Derivation.**
- `manifest_hash` = SHA-256(canonical compact JSON of `manifest_payload`).
- `manifest_id` = `spy-vwap-shadow-manifest:` + `manifest_hash`.
- The stored manifest is `manifest_payload` + `manifest_hash` +
  `manifest_id` + sealing metadata (`sealed_at_utc`, `seal_event_id`).
- The ID is never inside the hashed payload, so there is no self-reference.

**Recomputation.** Rebuilding `manifest_payload` from the stored
append-only facts must reproduce `manifest_hash` exactly. Any mismatch is an
integrity failure: the period gets an `integrity_stop` event and the label
`shadow_stopped_integrity_violation`.

## D. Deterministic identities

**Construction rules:**
- **Field order.** Fields are listed in fixed order.
- **Separator and encoding.** Fields are joined with `:`, encoded as UTF-8
  ASCII, dates as `YYYY-MM-DD`, integers in decimal, with no padding except
  where stated.
- **Content-addressed IDs.** Where an ID is content-addressed, it is the
  SHA-256 of canonical compact JSON.
- **What never enters an ID.** No timestamp, outcome value, or free text
  enters an identity.

| Identity | Construction | Uniqueness boundary | Collision behavior |
|---|---|---|---|
| Collection period | `SPY-VWAP-SHADOW:P<ordinal:03d>` | global PK | a second insert with different content → `identity_mismatch`, refused |
| Session | `<collection_period_id>:<session_date>` (a date belongs to at most one period) | PK; unique `session_date` | refused |
| Slot / observation | `SPY:<session_date>:<k>` (frozen) | PK; global | identical → no-op; different → `duplicate_rejected` event, refused |
| Event | `evt:` + SHA-256 of the canonical identity payload (§D.2); no ordinal allocation | PK `event_id`; UNIQUE (`event_type`, `scope`, `collection_period_id`, `session_date`, `slot_id`, `horizon`, `reason_code`, `occurrence_key`) | same payload → same ID; see §D.2 |
| Detector invocation | `<slot_id>:invoke` (one invocation identity per slot) | derived; not a separate row | — |
| Obligation | `<slot_id>:<horizon>` | PK | refused on difference |
| Reconciliation unit | `<slot_id>:reconciliation` | PK | refused; corrections only via `audit_correction` |
| Replay run | `<session_id>:replay:<attempt>`, where `attempt` = 1 + the count of existing replay runs for that session, computed inside the insert transaction by the single lock-holding writer | PK; at most one `succeeded` per session | PK collision → refuse, re-read, recompute |
| Manifest | `spy-vwap-shadow-manifest:` + SHA-256 of `manifest_payload` (§C.13; the ID and hash are excluded from the payload) | PK; UNIQUE `collection_period_id` | refused |
| Configuration | SHA-256 of canonical C.4 | — | any mismatch → `identity_mismatch` |

**Why replay reproduces identities.** Every input is a date, a slot index, a
horizon, a schedule-derived occurrence key, an operator-supplied period
ordinal, or frozen constants. None depends on wall-clock
time or data values, so re-running the same step computes the same
identity, and the PK turns a retry into a no-op.

### D.2 Event identity (content-based, retry-safe)

**Construction.**
- `event_id` = `evt:` + hex(SHA-256(canonical compact JSON of
  `{"v": "spy-vwap-shadow-event-id-1", "event_type", "scope",
  "collection_period_id", "session_date", "slot_id", "horizon",
  "reason_code", "occurrence_key"}`)).
- Unset fields are JSON `null`.
- There are no timestamps, outcome values, or free text in the payload.

**`occurrence_key`** is derived only from the schedule:
- `once`: at most one per scope, type, and reason. Examples:
  `missing_slot_recorded`, `observation_commit_confirmed`,
  `duplicate_rejected`, `population_excluded`, `session_activated`,
  `reconciliation_completed`, `session_admitted`. For
  `technical_error(reason)` this is the **first** occurrence per slot and
  reason.
- `pre_open` or `idle_after_<k>`: clock checks and clock-unhealthy events.
- `before_slot_<k>`: `recorder_started`, `recorder_restarted`,
  `pause_recorded`, `resume_recorded`. Several restarts before the same slot
  collapse into one event; that is documented, and restart counts are not a
  protocol metric.
- `rev_of_<j>`: `provider_revision_observed` for an earlier slot *j*.
- `replay_<attempt>`: replay events.
- `of_<audited_event_id>`: `audit_correction` (one correction per audited
  event and correction code).

**Single writer.** Allocation is not needed. Exactly one process may write
shadow tables at a time: a dedicated fail-closed lock file following the
existing `orchestration/lock.py` pattern (atomic `O_CREAT | O_EXCL`, never
auto-broken). The live recorder and the reconciliation job are mutually
exclusive.

**Retries and conflicts.**
- **Retry.** Recompute the same payload, then the same `event_id`, then
  INSERT. On a PK conflict, the repository compares the stored row's
  identity-payload fields:
  - equal → `already_present_identical`. The **original** row, including its
    original `event_utc`, is kept and returned; the retry's timestamps are
    discarded.
  - different → `refused_conflict`, which can only mean a SHA-256 collision
    or a bug, and is treated as an integrity error.
- **Two writers.** Prevented by the lock. If the lock were bypassed, the PK
  and UNIQUE constraint still allow at most one row per identity.

## E. Time and clock design

1. **Canonical time.** Everything is persisted in UTC (`TIMESTAMP`, naive
   UTC, as in the existing tables). America/New_York via `zoneinfo` is used
   only to derive session dates, slot times, and deadlines.
2. **Scheduled close.** `scheduled_close_utc(k)` =
   `(session_date 09:30 America/New_York + 5·k min + 5 min) → UTC`.
   - It is computed purely from the date. DST is handled by `zoneinfo` per
     date.
   - US DST changes fall on Sundays (for example 2027-03-14 is inside a
     period starting on S0 = 2026-12-07), so no session straddles one.
     Tests cover sessions immediately before and after each change.
3. **Wall-clock and monotonic readings.** Every recorded wall-clock instant
   (`first_observed_utc`, `pre_commit_utc`, `event_utc`) is paired with a
   `time.monotonic_ns()` reading. Elapsed intervals come from the
   monotonic differences.
4. **Durable-write timestamp (two transactions).** A row can't contain its
   own commit instant, so the write happens in two transactions:
   1. The observation transaction (observation plus obligations) commits.
   2. **Immediately after COMMIT returns successfully**, the process
      captures `post_commit_utc` (wall clock) and `post_commit_mono_ns`.
   3. A second, append-only transaction writes
      `observation_commit_confirmed`, referencing the `slot_id` and storing
      those **captured** values in `post_commit_utc` and
      `post_commit_mono_ns`.

   **Rules:**
   - **Endpoint.** The latency endpoint is `post_commit_utc`, i.e. the time
     captured after the observation commit returned. It is **not** the
     confirmation event's own `event_utc` or commit time. `pre_commit_utc`
     is never the durable-write timestamp.
   - **Latency** = `post_commit_utc − scheduled_close_utc` (§F.2).
   - **`timely`** ⇔ the confirmation exists, latency ≤ 240 s,
     `post_commit_utc ≥ pre_commit_utc`, and
     |Δwall − Δmono| between `first_observed` and post-commit ≤ 1000 ms.
   - **`late`** ⇔ latency > 240 s.
   - **`unproven`** ⇔ no valid confirmation. It is treated exactly as not
     timely (P2 miss, population-excluded with `timeliness_unproven`).
   - **Missing confirmation.** If the confirmation can't be written (or the
     process dies before it), the observation exists but can never qualify
     as timely. Recovery may append `observation_commit_confirmation_missing`
     (occurrence key `once`), but it **may not invent** a post-commit
     timestamp. A later process never writes `observation_commit_confirmed`,
     because only the process holding the captured values may.
5. **Documented time source (proposed).** The host's OS time service is
   synchronized to one documented NTP source. On the Windows host that is
   Windows Time (`w32time`), configured to a named NIST or NTP-pool server
   recorded in the collection-period authorization.
   - The recorder reads the OS-reported offset and last-sync status through
     a local status query.
   - A direct NTP comparison (a network request to the time source, **not**
     to the market-data provider) is optional and needs its own review.
6. **Tolerance.** A check is healthy iff |offset| ≤ 1000 ms and the last
   successful sync is ≤ 60 min old.
7. **When checks run.**
   - One check at session activation (before 09:30 ET).
   - Then one check per slot, run **only** in the idle window
     (scheduled close + 240 s, next scheduled close).
   - Clock checks therefore never overlap a latency-measurement window and
     never contaminate provider-latency timing.
8. **"Clock health known" for an observation** (P5 condition 6) means all of
   the following hold at `pre_commit_utc`:
   - the most recent check is healthy and ≤ 300 s old;
   - no unhealthy check came after it;
   - |Δwall − Δmono| since that check ≤ 1000 ms.

   If any fails, the writer **refuses to commit** and emits
   `technical_error(clock_unhealthy)`. That is a fail-closed exclusion, not
   P5. Accepting an observation without these conditions **is** P5.
9. **Fail closed and restart.**
   - An unhealthy clock refuses activation or observation for as long as it
     persists.
   - On restart, the recorder writes `recorder_restarted` and requires a new
     healthy check before its next slot.
   - Slots whose close + 240 s has already passed are **never** given
     missing-slot events retroactively; reconciliation classifies them.
10. **Retention.** Clock checks are rows in `shadow_clock_checks`, kept for
    the life of the period and included in the manifest counts.

## F. Proposed DuckDB storage — a future migration (NOT created)

**Migration-number housekeeping (2026-10-02).** This design earlier
reserved `0010`. That was only a proposed future number: no such migration
or schema exists, and `0010` has since been assigned to the offline evidence
and setup-card store (PROJECT_STATE item 63). This migration will use **the
next available migration number at implementation**, written `NNNN` below
(`NNNN_create_spy_vwap_shadow_tables.sql`). Nothing else in this design, and
nothing in the frozen shadow protocol, changes. It follows existing
conventions:
- a header comment block
- `CHECK`-enumerated values
- no DuckDB foreign keys (the rationale from migrations `0004` and `0007`:
  FKs block `ALTER`), so references are enforced in the repository layer
- naive-UTC `TIMESTAMP`
- `DECIMAL(18,6)` prices

### F.1 Tables

All tables share `schema_version VARCHAR NOT NULL` with a `CHECK` equal to
the contract string. Every table is insert-only.

| Table | Columns (type) | PK / unique | Checks |
|---|---|---|---|
| `shadow_collection_periods` | the C.5 fields; dates as `DATE`, instants as `TIMESTAMP`, SHAs as `VARCHAR` | PK `collection_period_id`; UNIQUE `period_ordinal` | SHA lengths; `start_session_date >= DATE '2026-12-07'`; the protocol SHA literal |
| `shadow_session_authorizations` | C.6 | PK `session_id`; UNIQUE `session_date` | `authorized_before_open = TRUE`; `prior_day_status` in enum |
| `shadow_slot_observations` | C.8; `DECIMAL(18,6)` close/VWAP/extension, `DECIMAL(18,4)` bps; `structurally_available_horizons` as three `BOOLEAN` columns | PK `slot_id`; UNIQUE (`session_date`, `slot_index`) | `slot_index BETWEEN 0 AND 77`; provenance literals; eligibility/side/null consistency; `first_observed_utc >= scheduled_close_utc`; `pre_commit_utc >= first_observed_utc`; horizon booleans consistent with `slot_index` and eligibility |
| `shadow_outcome_obligations` | C.10 | PK `obligation_id`; UNIQUE (`slot_id`, `horizon`) | horizon in 3 values; `deadline_utc > availability_utc` |
| `shadow_outcome_records` | C.10 record | PK `obligation_id` | terminal state in enum; values null ⇔ technical terminal |
| `shadow_replay_runs` | `replay_run_id`, `session_id`, `session_date`, `status` (`succeeded`/`failed`), `session_classification` (complete / `SessionExclusionReason`), `ingestion_run_id` (the `market_bars` run), `bars_regular_count`, `replay_code_commit_sha`, `created_at_utc` | PK `replay_run_id` | status enum; at most one `succeeded` per session (enforced by the repository plus a partial-uniqueness check query) |
| `shadow_reconciliation_units` | C.11 | PK `unit_id`; UNIQUE `slot_id` | class/code/evidence consistency |
| `shadow_events` | C.9 | PK `event_id`; UNIQUE (identity-payload columns, §D.2) | `event_type` / `reason_code` / `scope` consistency; `source` enum; `post_commit_*` not null ⇔ `observation_commit_confirmed` |
| `shadow_clock_checks` | `clock_check_id` (`<session_id>:clock:<occurrence_key>`), `collection_period_id`, `session_date`, `occurrence_key`, `checked_at_utc`, `checked_mono_ns`, `offset_ms` (INTEGER), `last_sync_age_s` (INTEGER), `healthy` (BOOLEAN), `time_source_token` | PK | `healthy = (abs(offset_ms) <= 1000 AND last_sync_age_s <= 3600)` |
| `shadow_locked_review_manifests` | `manifest_id`, `manifest_hash`, `collection_period_id`, `manifest_payload_json` (canonical `VARCHAR`, ≤ 1 MB, no outcome values), `sealed_at_utc`, `seal_event_id`, `schema_version` | PK `manifest_id`; UNIQUE `collection_period_id`, UNIQUE `manifest_hash` | `length(manifest_hash) = 64`; `manifest_id = 'spy-vwap-shadow-manifest:' \|\| manifest_hash` |

**Indexes:** `(session_date)` on observations, events, obligations, and
units; `(event_type, session_date)` on events.

### F.2 Append-only enforcement

DuckDB has no triggers, so append-only is enforced in layers:
1. **Insert-only repositories.** Repositories expose only insert and
   read methods.
2. **Static test.** A test scans the shadow repository modules and fails on
   any `UPDATE`, `DELETE`, `TRUNCATE`, `DROP`, `ALTER`, or `INSERT OR
   REPLACE`.
3. **Per-session digest.** Each session's `reconciliation_completed` event
   records a SHA-256 digest over that session's canonical rows. The
   manifest binds all 60 digests, so any later mutation is detectable.
4. **Least-privilege files.** The database file and backups are readable
   and writable only by the operator account.

This detects tampering; it cannot prevent a privileged user from editing the
file. That is the same authenticity boundary as the confirmation study.

### F.3 Transactional boundaries

| Operation | Single transaction contains | Retry |
|---|---|---|
| Observation write | the observation row plus its 0–3 obligation rows | identical → no-op; differing → refused plus `duplicate_rejected` (separate transaction) |
| Commit confirmation | the `observation_commit_confirmed` event carrying the post-commit timestamps captured after the observation COMMIT returned | only the capturing process may write it; retry with the same captured values → same ID → no-op |
| Missing-slot event | the `missing_slot_recorded` event (at close + 240 s, live only) | idempotent |
| Technical/clock events | the event, or the clock-check row | idempotent |
| Replay run | the `market_bars` ingestion runs through the existing `BarRepository` transaction (**separate**); then the `shadow_replay_runs` row | a failed run gets a new attempt ordinal |
| Outcome recording | all outcome records for one session's obligations | all-or-nothing per session; a retry inserts only missing records |
| Reconciliation write | all 78+ units for a session plus `population_excluded` events | all-or-nothing |
| Session completion | the `reconciliation_completed` event (with digest), after verifying every obligation is terminal and every unit exists | idempotent |
| Admission | the `session_admitted` event (ordinal *n*), after verifying all earlier collected sessions are reconciliation_complete | idempotent |
| Manifest | the manifest row (payload + hash + ID + sealing metadata) plus the `manifest_sealed` event, after recomputing the payload from the facts | idempotent: same payload → same hash → same ID |

### F.4 Derived summaries

Session summaries and metrics are either **views** (`CREATE VIEW` in the
migration) or recomputed in code from the tables above. They are never
stored as authoritative rows. A stored metrics snapshot, if any, carries
`computed_from_digest_sha256` and must equal its recomputation.

### F.5 Preconditions, backup, recovery, and health

- **Preconditions.**
  - The migration's own separately authorized implementation review.
  - The database is healthy at the migration immediately before `NNNN`.
  - No running orchestration or recorder process.
  - A verified backup copy (`data/market_intelligence-before-NNNN-<UTC>.duckdb`,
    compared with `cmp`).
- **Recovery.** DuckDB migrations run transactionally. If one fails,
  nothing is applied; restore from the backup if any doubt remains. No
  down-migration is planned; rollback means restoring the backup.
- **Health check.** Add the ten tables and their key columns to
  `REQUIRED_TABLES` / `REQUIRED_COLUMNS`, but only in the implementation
  change that adds `NNNN`.
- **Migration tests (future):**
  - applies on a temporary database from the preceding migration
  - checksum and ordering
  - every `CHECK` rejects invalid rows
  - every PK or UNIQUE rejects duplicates
  - views reproduce derived summaries
- **No migration is created or applied in this stage.**

### F.6 Fallback event journal

This covers explained code 9 when DuckDB itself is unavailable.
- **What it is.** An append-only, fsync'd, gitignored local JSON-lines file
  of `ShadowEvent` records, `source = fallback_journal_import` on import.
- **Integrity.** Each line carries the SHA-256 of the previous line, forming
  a hash chain.
- **Import.** At reconciliation, lines are imported verbatim with their
  original timestamps. A broken chain means the imported events are **not**
  contemporaneous evidence, so the affected units are unexplained.
- **Needs review.** This is a design decision for implementation review.

## G. Repository interfaces (no code)

**Common rules:**
- Every repository owns its transactions.
- Every method takes typed contracts, never dictionaries.
- Every method returns a bounded status enum:
  `inserted` / `already_present_identical` / `refused_conflict` /
  `refused_invalid`.
- Every method raises only a sanitized `ShadowStorageError` with a fixed
  category, never a path, SQL, value, or raw exception.
- Logs carry only IDs, statuses, and categories.

| Repository | Operations | Notes |
|---|---|---|
| `ShadowPeriodRepository` | `insert_period(ShadowCollectionPeriod)`, `insert_session_authorization(ShadowSessionAuthorization)`, `get_period(id)`, `get_session_authorization(date)` | refuses dates inside the holdout window and dates on or after `hard_stop` |
| `ShadowObservationRepository` | `insert_observation_with_obligations(obs, obligations) -> status`, `get_observation(slot_id)`, `list_session_observations(date)` (decision-time fields only) | identical retry → `already_present_identical`; different → `refused_conflict`, and the caller emits `duplicate_rejected` |
| `ShadowEventRepository` | `append_event(ShadowEvent)`, `append_clock_check(ShadowClockCheck)`, `list_events(session_date, types)`, `import_fallback_journal(path_in_allowed_dir)` | the fallback import verifies the hash chain; there is no update method |
| `ShadowOutcomeRepository` | `insert_session_outcomes(records)`, `count_terminal(session_date) -> counts` (**no values**), `read_locked_outcomes(manifest_id) -> values` | `read_locked_outcomes` refuses unless a sealed manifest exists, the time is ≥ `review_not_before`, and the period status is `locked`; it is the only value-reading path |
| `ShadowReconciliationRepository` | `insert_replay_run(run)`, `insert_session_units(units, exclusion_events)`, `append_completion(event)`, `list_units(date)` | one unit per slot; the completion precondition is verified in the same transaction |
| `ShadowManifestRepository` | `seal_manifest(manifest)`, `get_manifest(period_id)` | refuses unless 60 admitted sessions exist, all reconciliation_complete, and their digests recompute identically |

**Prohibited raw output:** bars, prices beyond the decision-time fields,
provider bodies, headers, credentials, SQL, paths, or exception text.

## H. Slot execution sequence (one slot *k*)

**The sequence:**
1. **Authorization.** Verify the period is `active` and the session has a
   `ShadowSessionAuthorization` written before 09:30 ET. Otherwise do
   nothing for this slot (reconciliation assigns
   `authorized_session_not_active` from the authorization records).
2. **Identity.** Derive the `ShadowSlotIdentity` from the date and *k*, and
   confirm the configuration and protocol hashes match the period. On a
   mismatch, write `technical_error(identity_mismatch)` and refuse.
3. **Clock.** Verify clock health is "known" (§E.8). If not, write
   `technical_error(clock_unhealthy)` and stop this slot.
4. **Wait.** Wait until `now ≥ scheduled_close_utc`. Never request before
   the close.
5. **Retrieve.** Request the bounded range `start` = the session's 09:30 ET
   bar start, `end` = slot *k*'s bar start + 1 s.
   - The existing connector requires `start < end`, so `end` equal to the
     bar start would be refused for *k* = 0. The +1 s range excludes the
     in-progress next bar, which starts at the scheduled close.
   - Poll on the 5-second grid aligned to 09:30:00 ET, from the scheduled
     close through close + 240 s inclusive, and stop at first presence.
   - One session-wide loop serves all slots. Slot windows never overlap
     (240 s < 300 s), and a returned bar satisfies only the slot with its
     exact timestamp.
6. **First observation.** Capture `first_observed_utc` and its monotonic
   pair when the bar is first present.
7. **Validate.**
   - Check provenance (E1). On failure: `technical_error(wrong_provenance)`
     and refuse.
   - Validate E2/E3 and prefix completeness.
   - If a bar returned for an earlier slot differs by hash from its earlier
     retrieval, append `provider_revision_observed` (audit only) and use the
     current prefix.
8. **Evaluate.** Run the frozen evaluator adapter and discard all outcome
   fields.
9. **Identify.** Derive `slot_id` and the obligation IDs.
10. **Write.** Write one immutable observation plus its obligations in one
    transaction (after the §E.8 checks), with
    `pre_commit_after_latency_bound` computed from `pre_commit_utc`.
11. **Obligations.** The structurally available obligations are created in
    the same transaction (eligible observations only).
12. **Confirm and record.** Immediately after COMMIT returns, capture
    post-commit wall and monotonic times, then append
    `observation_commit_confirmed` carrying them (§E.4); if
    applicable, `population_excluded(prefix_incomplete)`; plus any bounded
    technical events.
13. **No interpretation.** Emit nothing but IDs and status to logs. Nothing
    is displayed, alerted, or interpreted.

| Situation | Behavior |
|---|---|
| Starts late (after 09:30 ET) | The session is not activated. Every slot is missing, and reconciliation classifies them `authorized_session_not_active` |
| Restarts mid-session | `recorder_restarted`; a new clock check; resume at the next slot whose close hasn't passed. Missed slots get **no** retroactive events and become unexplained unless a prior `pause_recorded` exists. The session is not covered |
| Misses one slot while running | At close + 240 s, write `missing_slot_recorded` with the last technical reason. Reconciliation explains it with the matching code 4–9 if a contemporaneous event exists |
| Stale data (bar absent) | `technical_error(stale_input)` on the first failed poll; keep polling until close + 240 s; then `missing_slot_recorded` |
| Revised bar | The current prefix is used; `provider_revision_observed` is audit only. Reconciliation compares prefix hashes, and a mismatch without code 7 or 8 evidence is **unexplained** (protocol §I.2) |
| Write failure | Observation transaction fails: roll back; `technical_error(storage_error)` goes to DuckDB or the fallback journal; at close + 240 s, write `missing_slot_recorded`. Code 9 applies. Confirmation write fails after a successful observation commit: the observation stands, is `unproven` (never timely), and recovery appends `observation_commit_confirmation_missing` |
| Duplicate | Identical → no-op; different → refused, plus `duplicate_rejected`. Never two rows (P4) |
| Loses clock health | Refuse, plus `technical_error(clock_unhealthy)`; missing-slot at close + 240 s; code 5 |
| Bar retrieved after 240 s | Writing is still permitted, with `pre_commit_after_latency_bound = true` (the protocol's `observed_late`). It is always population-excluded (`late_detection`) and never timely |
| Earlier prefix bar missing | Evaluate with `prefix_complete = false` (the evaluator permits gaps). Population-excluded `prefix_incomplete`. Reconciliation may explain the field mismatch by code 7 only if that bar's slot has a live `stale_input` event |

**Nothing is ever created from replay data or represented as live
detection.**

## I. Outcome obligations

**Obligations.**
- For each eligible observation, create obligations for exactly the
  structurally available horizons: 30m if *k* ≤ 71, 2h if *k* ≤ 53, close if
  *k* ≤ 76.
- `availability_utc` is decision + 30 min, decision + 120 min, or 16:00 ET.
- `deadline_utc` follows §C.10.
- Structurally unavailable horizons get no obligation row. They are recorded
  in `structurally_available_horizons` (explained code 10 is implicit and
  obligation-level).

**Computing outcomes.**
- After the post-session replay succeeds, the outcome finalizer computes
  each outcome by calling the frozen evaluator's own window and outcome
  functions, `_horizon_window_bars` and `_compute_horizon_outcome`,
  imported unmodified.
- It uses the live observation's frozen `signal_vwap`, `signal_close`, and
  side, together with the replay bars after the decision.
- A future test proves equality with `evaluate_spy_vwap_reversion` output
  for exact-match slots.

**Terminal states.**
- `outcome_recorded` when the window exists.
- If the source bars can't be obtained, or a required window doesn't exist
  (for example on a structurally incomplete day): `replay_source_unavailable`.
- If the outcome write failed and the retry window closed:
  `transactional_write_failure_recorded_live`.

**Retries and late recording.**
- Outcome insertion retries (same IDs) until success or the reconciliation
  pass ends.
- A record inserted after `deadline_utc` is terminal but not successfully
  finalized, so it counts against P6.
- On restart, the finalizer inserts only the missing records.

**Isolation from the observation.** Outcome records never modify the
observation, its eligibility, or its obligations. No outcome value affects
terminal status.

**Session reconciliation_complete** requires that every obligation of the
session has a record, and every slot has a unit.

## J. Reconciliation algorithm

- **Replay provenance.** `shadow_replay_runs` records:
  - the `market_bars` ingestion run
  - the regular-bar count
  - the session classification (the builder's rules)
  - the replay code commit

  Replay decision points come from the unmodified evaluator over the
  builder's one-session input.
- **Universe.** **U** = every slot identity present in the live observations
  or the replay, over all collection days. **X** = 78 × the replay-complete
  sessions. **M_rc** = the `exact_match` units in replay-complete sessions.
- **Classification of each unit (pure, in this order):**
  1. **P5 check.** If any P5 condition 1–6 holds, the class is
     `timing_violation`, with the condition number recorded.
  2. **Exact match.** If both sides are present and the decision-time
     comparable fields are equal, the class is `exact_match`. The comparable
     fields are:
     - `prefix_sha256` equal to the replay prefix hash
     - contract status, eligibility, and side
     - `signal_close`, `signal_vwap`, distance, and normalized extension
     - bucket, regime, `scenario_horizon`, and `time_of_day_bucket`
     - `prefix_complete` and `prior_day_status`
  3. **Kind.** Determine the kind:
     - live absent, replay present → `live_missing`
     - live present, replay absent → `extra_live`
     - both present but different → `field_mismatch`
  4. **Explained codes.** For code in 1 → 9, if the code applies to the kind
     (protocol §I.2 table) and its evidence exists, the class is `explained`
     with that code and its evidence event. Evidence lookup reads only
     `shadow_events` and `shadow_clock_checks` created **before** the
     reconciliation run started, plus authorization rows and the replay
     classification for codes 1–3. Any event created after the slot's close
     + 240 s is not live evidence for codes 4–9, except `pause_recorded`,
     which must precede the slot.
  5. **Otherwise** the class is `unexplained_<kind>`.
- **One class per unit.** The PK on `slot_id` plus a count check:
  Σ classes = U.
- **Specific cases.**
  - **Provider revisions** (hash mismatch without code-7 or code-8 evidence)
    are **unexplained**.
  - **Structurally incomplete sessions:** every unit that day gets code 1,
    and the session is outside X.
- **Corrections** are `audit_correction` events only. They never change a
  unit row and never create "explained" status without §I.2 evidence.
- **Reports** are sanitized counts per class, per code, and per session.
- **Determinism.** Re-running the classifier over the stored facts must
  reproduce every unit exactly; a future test enforces this.

## K. Metrics and sample lock

All metrics are computed from stored facts by pure functions. Exact
`Fraction` comparisons are made against C.4 thresholds; a zero denominator
is `not_evaluable`, which fails at the locked review and is shown as "not
assessed" at the interim check. Reproducibility: the metrics are recomputed
from a fresh read, and equality of `computed_from_digest_sha256` is
required.

| Metric | Numerator query | Denominator query | Scope |
|---|---|---|---|
| P1 | covered sessions: replay-complete sessions where all 78 slots have an observation or a live `missing_slot_recorded` event, with no `pause_recorded` active during the session | replay-complete sessions (latest succeeded replay run classified complete) | collection days (interim: to date) |
| P2 | slots in replay-complete sessions whose derived timeliness is `timely` | X | same |
| P3 | M_rc | X | same |
| P4 | `count(observation rows) − count(distinct slot_id)`, over all rows (structurally 0 by PK; checked anyway) | — | all |
| P5 | units classified `timing_violation`, plus any P5 condition detected outside a unit | — | all; checked continuously → immediate `integrity_stop` |
| P6 | successfully finalized obligations in H | \|H\| | locked sample (interim: admitted sessions to date) |
| P7 | the three unexplained classes | U | all collection days |
| P8 | slots in replay-complete sessions with at least one `technical_error(stale_input)` or other `technical_error` event | X | same |

**H in P6** covers only the obligations of behavioral-population candidates,
i.e. candidates that are:
- eligible
- timely
- `prefix_complete`
- `exact_match`
- not population-excluded
- in an admitted session

**Sample lock:**
- **Provisional count** = admitted sessions + collected sessions that aren't
  reconciliation_complete yet. When it first reaches 60, the recorder
  appends `collection_paused_provisional_limit` and stops activating
  sessions.
- **Admission.**
  - In date order, a reconciliation_complete session that is replay-complete
    and covered is admitted with ordinal *n*, and only after every earlier
    collected session is reconciliation_complete.
  - Admitted sessions are immutable.
  - A non-qualifying session never enters.
- **Resume.** If fewer than 60 admitted sessions remain possible, append
  `collection_resumed` and activate the next full session, but only if the
  session date is before `hard_stop`.
- **Hard stop.** At the `hard_stop` instant (S0 + 182 days, 00:00
  America/New_York), append `hard_stop_reached`. No session dated on or
  after it may be activated. If, once reconciliation has finished, fewer
  than 60 sessions are admitted, the period is `closed_insufficient_sessions`.
- **Overflow.** Sessions collected beyond the 60th admission are
  `overflow_not_inspected`. They are excluded from every locked-review
  query, and their outcome records are never read.
- **Sealing.** When 60 sessions are admitted and reconciliation_complete:
  1. `manifest_payload` is built from the append-only facts (§C.13);
  2. `manifest_hash` and `manifest_id` are computed;
  3. `seal_manifest` recomputes the payload independently and requires an
     identical hash;
  4. it inserts the manifest row and `manifest_sealed`.

  Any later recomputation mismatch is an integrity failure.
- **Outcome inspection.** It is possible only through
  `read_locked_outcomes` after sealing **and** once `review_not_before` has
  passed. Metric and summary paths never select outcome-value columns, and a
  future static test enforces that.

## L. Security and privacy

- **Secrets.** No credentials, API keys, headers, URLs, provider bodies,
  exception text, or tracebacks in any contract, row, log, export, or
  journal. Settings are read only by the existing connector.
- **Errors and redaction.** All errors map to bounded enums. Unexpected
  exceptions become `unexpected_error` with no message; nothing is echoed.
- **Files and backups.** The local database, the fallback journal, and
  backups live under gitignored `data/`, with operator-only permissions.
  Backups are never committed.
- **No execution surface.** No order, account, or position endpoint is
  touched. The only provider access is the existing read-only bars
  connector.
- **No outward exposure.** No public dashboard, no outbound alerting, no
  notifications, and no model or agent calls.

## M. Future test plan (not created)

**Unit tests (pure):**
- contract validation, bounds, and forbidden fields
- canonical serialization byte-stability
- every deterministic identity, plus replay reproduction
- slot scheduling, including DST dates (sessions around 2026-11-01 and
  2027-03-14)
- structural-horizon sets at *k* = 53/54, 71/72, 76/77
- deadline computation, including weekend crossing
- the timeliness derivation: 239.999 s, 240 s, 240.001 s; missing and
  inconsistent confirmation; negative latency
- the clock-known rules and fail-closed behavior
- the evaluator adapter equals `evaluate_spy_vwap_reversion` and carries no
  outcome fields
- outcome equality with the evaluator for exact-match slots
- the reconciliation classifier: every P5 condition, exact match, each of
  codes 1–9 applicable and not applicable per kind, evidence-timing cutoffs,
  unexplained fallbacks, and precedence ties
- P1–P8, including zero denominators, exact thresholds (99/100 and the
  fraction just below), and P2/P3/P8 overlaps
- sample lock: provisional 59/60/61, a demoted provisional session, resume
  before and at `hard_stop`, admission order, overflow exclusion
- the manifest's canonical hash

**Repository integration tests (temporary DuckDB):**
- the migration applies from the preceding migration
- CHECK, PK, and UNIQUE refusals
- insert-only static scan
- transaction rollback on injected failures (observation plus obligations
  atomicity, session outcome batch, units batch)
- duplicate identical/different behavior
- `read_locked_outcomes` refusals before sealing or before
  `review_not_before`
- fallback-journal hash-chain verification and import
- views equal to recomputation

**Synthetic end-to-end tests (fake connector and fake clock, no network):**
- full sessions with injected stale data, outages, revisions, clock
  failures, restarts, late writes, and duplicates
- replay and reconciliation, including every class
- 60-session lock and sealing
- no look-ahead: decision fields identical whether or not later bars exist
- secret sanitization: no credential, URL, header, or exception text in any
  output

**Authorized connectivity tests:** only the separately authorized latency
test (its own plan); none in the implementation stage.

## N. Latency test

See [SPY_VWAP_IEX_LATENCY_TEST_PLAN.md](SPY_VWAP_IEX_LATENCY_TEST_PLAN.md).

## O. Decisions and open issues

**Fixed by the frozen protocol:**
- E1–E8, P1–P8, L = 240 s, the ±1 s clock tolerance intent
- structural availability, the 60 / 84 / 182 lock
- explained codes and precedence, labels, the ladder
- the collection start conditions, and the holdout seal

**Made in this design:**
- **Components and pure/side-effecting split:** the §B component split.
- **Adapter:** evaluating via the public evaluator on a truncated one-session
  input; outcome computation via the frozen evaluator's window/outcome
  functions.
- **Commit timing:** two transactions. The latency endpoint is the
  post-commit timestamp captured after the observation COMMIT returns,
  stored in a confirmation event. It is never invented on recovery.
- **Event identity:** content-based `event_id` from a schedule-derived
  occurrence key, a single-writer lock, and identical-retry recovery of the
  original row.
- **Manifest:** `manifest_payload`, hashed separately from its ID and
  sealing metadata; a reproducible snapshot, not an independent fact
  source.
- **Clock:** the "clock health known" rule (§E.8), with clock checks only in
  idle windows.
- **Storage:** ten insert-only tables with no FKs; outcome values in a
  separate concealed table; derived summaries as views.
- **Replay source:** bounded post-session ingestion through the existing
  `BarRepository`. The live recorder never writes `market_bars`.
- **Resilience:** the fallback event journal, and the vocabulary
  `ShadowEventType` / `ShadowTechnicalError` /
  `ShadowPopulationExclusionReason`, which are audit and operational only.

**Deferred to implementation review:**
- the exact time-source server and local status query
- whether a direct NTP comparison is used
- the fallback-journal location and format
- view definitions versus in-code recomputation
- DuckDB file permissions on the host
- polling cadence tuning within the frozen L

**Discovered issues (resolved within the protocol's text; no amendment
needed):**
1. The durable-write timestamp can't live on the row it commits. Resolved by
   §E.4.
2. The protocol has no event proving "clock health known". Resolved by the
   §E.8 operational rule plus stored clock checks.
3. **Latency-test sequencing** (decided at review, see below). The test
   can't use holdout-window dates.

**Stage status and Stage 3 decision (recorded).**
- **Stage 2 design** has been reviewed and merged. Stage 2 is
  **`design_complete_test_pending`, not complete.**
- **Stage 3 is not authorized** while the frozen Stage-2 latency test is
  pending. The frozen authorization ladder will **not** be amended merely to
  start implementation earlier.
- **Latency test:**
  - It can't execute during the sealed holdout, nor before 2026-12-07.
  - The holdout must first be evaluated exactly once and immutably recorded
    under its own authorization.
  - After that, the next permitted action is the latency test's own separate
    authorization and execution
    ([plan](SPY_VWAP_IEX_LATENCY_TEST_PLAN.md)).
- **If `latency_feasible`,** Stage 2 may be closed, and Stage 3 may then be
  considered under a new authorization.
- **If `latency_infeasible` or `latency_insufficient_data`,** stop and review
  the result under the frozen protocol. L is not changed automatically.

**Blockers:** none that require a protocol change. The only pending item is
the Stage-2 latency test, which is blocked by the holdout until on or after
2026-12-07.

**Authorization Stage 3 would eventually need** (only after
`latency_feasible` closes Stage 2): an explicit instruction authorizing
offline implementation, with synthetic tests only, of the §C contracts, the
§B pure components, and the §G repositories against a **temporary** DuckDB.
That includes writing (but not applying to the project database) migration
`NNNN` (the next available migration number), with no provider or network
requests, no project-database writes, no
scheduling, and no collection.
