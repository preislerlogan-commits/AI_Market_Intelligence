# SPY IEX Bar-Availability Latency Test — Plan (not executed)

**Status: PLAN ONLY.** It is part of Stage 2 of the frozen shadow protocol
([SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md](SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md),
merge `f79d37e6c762e35da3d575c35276a0be5adbc66a`, §F.2 and §K stage 2).

- **Nothing is run yet.** This document makes no request and authorizes no
  request.
- **It is not collection.** It is not shadow collection, it writes no shadow
  observation, and it does not modify DuckDB.
- **Recorder context.** The recorder design is in
  [SPY_VWAP_SHADOW_RECORDER_DESIGN.md](SPY_VWAP_SHADOW_RECORDER_DESIGN.md).

## 1. Purpose

Determine whether the existing read-only Alpaca IEX bars source can make a
newly closed SPY 5-minute regular-session bar **durably observable** within
the frozen **L = 240 s** of its scheduled close.

That is a necessary, not sufficient, condition for P2. The test measures
transport and availability only, never prices, returns, VWAP, or outcomes.

## 2. Holdout and timing (decisive)

- **Not during the holdout.** The test cannot execute during the sealed
  holdout (2026-09-23 → 2026-12-04). Any SPY request for those dates would
  bring holdout prices into process memory, and preregistration §4 forbids
  using the holdout for implementation debugging.
- **Not before 2026-12-07.** It may execute no earlier than the 2026-12-07
  session. Every request is date-guarded: any `start` or `end` on a date
  from 2026-09-23 through 2026-12-04 is refused before network I/O.
- **Holdout first.** Before execution, the holdout must have been evaluated
  exactly once and immutably recorded, under its own separate
  authorization.
- **Its own authorization.** The latency test needs its own separate
  authorization.
- **Transport only.** Test observations are transport measurements only,
  never shadow observations. They can never enter the 60-session shadow
  sample, and the first shadow session must be dated after the last test
  session.
- **No values.** No price, volume, return, VWAP, or directional value is
  retained or reported.
- **No collection authority.** Executing the test does not authorize shadow
  collection or any later stage.

## 3. Session-wide polling loop (deterministic)

A **single** session-wide loop replaces per-slot loops.

- **Windows never overlap.** Slot *k*'s measurement window is
  [c_k, c_k + 240 s], where c_k = 09:35 ET + 5·*k* min. The next window opens
  at c_k + 300 s, so windows never overlap, and at most one slot is ever
  outstanding.
- **Why one loop anyway.** It gives one provider-request stream and one
  schedule.

**Grid and window.**
- Poll instants lie on the 5-second grid aligned to 09:30:00 America/New_York.
- The loop issues a request at a grid instant *t* only if some slot is
  outstanding at *t*, i.e. c_k ≤ *t* ≤ c_k + 240 s and not yet observed.
- The first poll is at **09:35:00 ET** (c_0). The final permitted poll is at
  **16:04:00 ET** (c_77 + 240 s), and only if slot 77 is still outstanding.
- There are no polls between windows. The idle intervals
  (c_k + 245 … c_k + 295) are reserved for clock checks.

**Outstanding and satisfaction rules.**
- A slot becomes outstanding at its scheduled close.
- It stops being outstanding when the first of these happens:
  - the exact closed bar is observed;
  - c_k + 240 s passes; or
  - a terminal test error applies (§5).
- Each response is evaluated against the currently outstanding slot only. A
  bar satisfies a slot **only** if its canonical timestamp equals that slot's
  bar start (c_k − 5 min). A later bar can never satisfy an earlier slot.

**Bounded request.**
- `start` = the outstanding slot's bar start; `end` = bar start + 1 s;
  `limit=1`; `max_pages=1`.
- `end` must exceed `start` under the existing connector's
  `start < end` rule. The half-open range excludes every other bar,
  including the in-progress next bar (which starts at c_k).
- Any returned bar with a different timestamp is recorded only as
  `unexpected_bar_returned = true`, and never satisfies a slot.

**Per-slot results kept.** Each slot keeps its scheduled close,
`first_observed_utc`, `durable_utc`, latency, and result code.

**Completeness check.** One request per session at **16:10 ET**:
`start` = 09:30 ET, `end` = 15:55 bar start + 1 s, `limit=100`,
`max_pages=1`. It counts regular bars by timestamp only, to classify the
session as structurally complete (exactly 78 on-grid regular bars) or not.

### Request cap (exact)

| Quantity | Value |
|---|---:|
| Polling opportunities per slot window (c, c + 5, …, c + 240 inclusive) | 49 |
| Polling opportunities per session (78 × 49) | 3,822 |
| Completeness check per session | 1 |
| **Maximum requests per session** | **3,823** |
| Five admitted sessions (5 × 3,823) | 19,115 |
| **Hard cap**: at most 10 attempted weekdays (§4), 10 × 3,823 | **38,230** |

- **Why not 4,680.** 4,680 (= 23,400 s / 5 s) would apply only to
  continuous polling through the whole session. That isn't used, because
  polling happens only while a window is active.
- **Enforcement.** The hard cap of 38,230 requests is enforced by a counter
  that refuses request 38,231 before any I/O.
- **Peak rate.** One request per 5 s, i.e. 12 per minute.
- **Rate limit unverified.** This repository documents no Alpaca
  rate limit. The provider's applicable rate boundary must be verified from
  the provider's own documentation and recorded as part of the test's
  authorization, before execution. If 12 per minute would exceed it, the
  test is not run as written; a reviewed plan revision is needed.

## 4. Session admission and preregistered parameters

| Parameter | Value |
|---|---|
| Source | Existing `AlpacaBarsClient.get_bars` only; `SPY`, `5Min`, `iex`, `raw`, `USD` (fixed connector constants) |
| Attempted weekdays | Consecutive weekdays from the first authorized test date (≥ 2026-12-07), at most **10** |
| Admission | Uses only outcome-free criteria. An attempted weekday is **admitted** iff all of the following hold: (a) a healthy clock check before 09:30 ET; (b) the loop ran for the whole session, meaning every one of the 78 windows was entered; (c) the 16:10 ET completeness check found exactly 78 regular bars; (d) no clock check during the session was unhealthy |
| Test sample | The **first five admitted sessions** in date order. The test stops after the fifth admission |
| Timeout | 4 s per request |
| Retry rule | None beyond the next grid poll |
| Clock | Healthy iff |offset| ≤ 1000 ms and last sync ≤ 60 min; checked before 09:30 ET and in every idle interval |
| Durability emulation | On first observation, append one sanitized line to a gitignored local JSON-lines file and `fsync` it; `durable_utc` = the post-`fsync` wall clock, paired with a monotonic reading. The project DuckDB is never written |

**Non-admitted weekdays** are recorded by reason: `structurally_incomplete`
(holiday or early close, e.g. 2026-12-24), `clock_unhealthy`,
`loop_incomplete`, or `test_stopped`. They are **excluded** from the
denominator but reported by count.

## 5. Errors

- **Terminal for the whole test:** HTTP 401/403, missing credentials, the
  date guard triggering, or the hard cap reached. The test stops; the label
  is `latency_insufficient_data` unless five sessions were already admitted.
- **Non-terminal:** 429, 5xx, timeouts, and invalid responses count as a
  failed poll. The slot stays outstanding until its window ends.
- **Errors never leave the denominator.** In an admitted session, a slot
  whose bar was not observed by c_k + 240 s, whatever the cause, is a
  **failed slot** and stays in the denominator.

## 6. Measurement fields (sanitized)

Per slot:
- session date, *k*, `scheduled_close_utc`
- the poll count, and per-poll sent/received times (UTC plus monotonic)
- the per-poll outcome category (`ok`, `rate_limited`, `server_error`,
  `timeout`, `auth_failure`, `invalid_response`)
- `target_observed`, `unexpected_bar_returned`
- `first_observed_utc`, `durable_utc`, `latency_ms`
- the Δwall − Δmono consistency value, and `clock_check_id`

**Never recorded:** prices, volumes, VWAP, trade counts, returns, bodies,
headers, URLs, or credentials. Values are dropped immediately after the
timestamp check.

## 7. Label (exact)

- **Denominator** D = 78 × (the number of admitted sessions used). With the
  required five admitted sessions, **D = 390**.
- **Numerator** N = admitted-session slots whose target bar was observed and
  durably recorded with latency ≤ 240 s, under a consistent clock.
- **Pass condition (exact fraction):** N / D ≥ 99/100 ⇔ 100·N ≥ 99·390 =
  38,610 ⇔ N ≥ 386.1. So the **minimum passing numerator is N = 387 of
  390**, meaning at most 3 failed slots.

**Labels:**
- **`latency_feasible`**: five sessions admitted, N ≥ 387, and no poll
  returned `unexpected_bar_returned = true`.
- **`latency_infeasible`**: five sessions admitted, and either N ≤ 386 or
  any `unexpected_bar_returned = true`.
- **`latency_insufficient_data`**: fewer than five admitted sessions within
  10 attempted weekdays, or the test stopped under §5. This includes D = 0.

**Descriptive only:** the latency p50, p95, p99, and max; error rates by
category; the `end`-semantics observations.

## 7a. After the result

- **Feasible:** Stage 2 may be closed. Stage 3 may then be considered under
  a new authorization.
- **Infeasible or insufficient:** stop and review under the frozen protocol.
  L is **not** changed automatically.
- **No changes after results.** The test values (L, 99%, cadence, sessions,
  cap, labels) are never changed after results. Any change is a new,
  reviewed plan version and a fresh run.

## 8. Prohibitions

- No holdout-window request.
- No retained values, and no market-direction, return, VWAP, or outcome
  analysis.
- No shadow tables, observations, or collection, and no locked-sample
  effect.
- No DuckDB modification.
- No secrets or raw payloads in any output.

## 9. Authorization needed to run

An explicit instruction that:
- names this plan version;
- confirms the holdout result is recorded;
- gives the start date (≥ 2026-12-07);
- records the verified provider rate boundary; and
- permits ≤ 38,230 read-only requests and one gitignored local measurement
  file.
