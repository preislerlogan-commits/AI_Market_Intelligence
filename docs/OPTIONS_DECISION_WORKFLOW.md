# Options Decision Workflow — Phase 1 Roadmap

**Status: design, plus implementation steps b, c, d, and e done. Step b
includes a live run; step c is offline-only with synthetic tests; step d's
tooling is offline-only with synthetic tests, plus one completed real,
read-only evaluation run against the stored SPY bars (2026-09-15) — a
single small-sample run that establishes no edge, accuracy, usefulness,
strategy validity, or profitability. Step e (the deterministic Contract
Selector) is now also done, offline-only with synthetic tests — no real
selector run has been performed, and no contract recommendation exists.**
**A feed-safety-boundary fix was applied to step e the same day it landed
(2026-09-16, Completed Work Log item 46): operational eligibility
(`SelectorStatus.ELIGIBLE`) now defaults to OPRA only, an indicative-feed
batch can never reach `ELIGIBLE`, an explicitly-opted-in indicative batch
instead returns a new, structurally separate `RESEARCH_ONLY` status, and
the CLI requires its own explicit `--allow-indicative-research` flag
(always overriding the input file) before processing indicative data at
all.** A freshness-boundary fix followed the same day, pre-merge (2026-09-16,
Completed Work Log item 47), distinguishing a future-dated snapshot
(`SNAPSHOT_FROM_FUTURE`) from a merely stale one (`SNAPSHOT_STALE`). **A
synchronized selector-capture coordinator was then added (2026-09-17,
Completed Work Log item 48), implemented and verified entirely offline
against mocked/fake providers — no synchronized live capture and no real
selector run has occurred.** Step f (the Options Strategy Agent) has NOT
begun and none of this — the selector, its fixes, or the capture
coordinator — authorizes starting it. As of
2026-08-28,
step b's **read-only SPY option-chain snapshot connector and local DuckDB
storage** existed in code and tests only — mocked HTTP transports and
temporary databases, no live option-chain request had been made, no real
option data had been stored, and migration `0009` had not been applied to
the real local database. That status is preserved here as an honest,
time-scoped diagnostic record and is not retracted. **Superseding update
(2026-09-14): migration `0009` has been applied to the real local database,
and one authorized live, bounded, `indicative`-feed ingestion has succeeded
(SPY, expiration 2026-09-18, strikes 740–790, 102 contracts received/
inserted — 51 calls, 51 puts), with a same-day read-only structural audit
finding zero integrity or malformed-data issues.** See `PROJECT_STATE.md`
(Completed Work Log item 40) and `DATA_CATALOG.md` ("SPY option-chain
snapshots (indicative)") for full sanitized detail and binding caveats —
this confirms internal structural consistency only, never pricing accuracy,
timeliness, usefulness, predictive edge, strategy validity, or
profitability.

**Step c — the deterministic SPY intraday feature/regime engine — is now
done (2026-09-15), implemented offline with synthetic fixtures and tests
only.** `market_intelligence/market_features/spy_regime_contracts.py`
(strict Pydantic v2 input/output contracts, `extra="forbid"`, bounded
collections), `spy_regime_features.py` (pure feature computation), and
`spy_regime_classifier.py` (the deterministic classifier and pure batch
interface) exist and are covered by contract, feature, classifier, and
offline-import-boundary tests. **No real SPY session has been classified by
this engine. Every threshold in the classifier is a provisional hypothesis,
not a validated value — none has been evaluated against real SPY history.
No predictive accuracy or mean-reversion edge has been established.** See
"Intraday Regime/Setup Engine — implementation (step c)" below for the
exact contracts, formulas, and decision order, and `PROJECT_STATE.md`
(Completed Work Log item 41).

**Step d — the offline SPY VWAP-extension/reversion evaluation of the
step-c engine — is now done, including its first real, read-only
evaluation run against the stored SPY bars (2026-09-15).**
`market_intelligence/evaluation/spy_vwap_reversion_contracts.py` /
`spy_vwap_reversion_evaluator.py` / `spy_vwap_reversion_serialization.py`
and the dry-run-first `scripts/evaluate_spy_vwap_reversion.py` exist and are
covered by contract, evaluator-formula, serialization, offline-import-
boundary, and CLI tests. **The tooling's first real run (2026-09-15,
documentation-only branch `docs/record-spy-vwap-reversion-first-
evaluation`)** used the stored `alpaca_bars_spy_5min` dataset, read-only
(filtered to the regular 09:30–16:00 America/New_York session, which left
exactly 5 gapless, grid-aligned, contiguous Monday–Friday regular sessions —
2026-08-17 through 2026-08-21 — 390 candidate decision points, all
eligible). No classifier threshold was tuned (the evaluation always used
the existing, unmodified `RegimeThresholds` from step c, recorded — never
re-derived — in the run's output). Observation-level sample-size thresholds
(≥ 50) were met for both extension sides on three of the four horizons
(`intraday_30m`, `intraday_2h`, `to_session_close`); `next_session` remains
unavailable for every decision point by fixed design. Session-level
thresholds (≥ 20 sessions) were met for **no** horizon or side, since only
5 sessions are stored. **This one small, non-independent-sample run
establishes no edge, accuracy, usefulness, strategy validity, or
profitability for the underlying setup or for any options overlay, and
must not be read as evidence for or against the VWAP-reversion
hypothesis.** See "Implementation
order" below (step d) for the exact contracts, outcome formulas, and
sample-size rules, and `PROJECT_STATE.md` (Completed Work Log item 44).
**Step d input builder (2026-09-23, Completed Work Log item 50):** the
evaluator's input can now be built reproducibly from stored bars by the
read-only, dry-run-first `scripts/build_spy_vwap_reversion_input.py`
(`orchestration/spy_vwap_reversion_input_builder.py`) instead of a local
one-off script — exact `alpaca`/`SPY`/`5Min`/`iex`/`raw`/`USD` provenance,
complete 78-bar regular sessions only, fixed-reason exclusion counts,
`prior_day` only from a qualifying immediately-preceding stored weekday,
narrowed point-in-time context, byte-stable JSON written only inside
`data/evaluations/local/`. **This is an input-building tool, not an
evaluation result or evidence of an edge; it has not been run against the
real database as part of that change.**
**Step d expanded run (2026-09-23, `PROJECT_STATE.md` Completed Work Log
item 52):** after one bounded SPY bars ingestion, the input builder and
evaluator were run read-only over 2026-08-17 through 2026-09-22 — 26
complete sessions, 2,028 candidate / 2,027 eligible decision points
(`above_vwap` 975, `below_vwap` 1,052). All six available session-level
cells (both sides × `intraday_30m` / `intraday_2h` / `to_session_close`)
passed the fixed 20-session threshold; `next_session` remains unavailable
by fixed design. Above-VWAP signed return toward VWAP was positive at every
evaluated horizon and both aggregation levels; below-VWAP results were
mixed — positive at the shorter horizons, weaker, and adverse at
observation level by session close. **This is an asymmetry worth further
research, not a validated strategy, edge, or directional signal** (only
25–26 sessions from about five weeks; overlapping, non-independent
observation-level points; no significance test or uncertainty interval;
thresholds not tuned; relative-volume, catalyst, and breadth context not
evaluated; underlying setup only, no option return or P&L). It does **not**
authorize step f; the next research step is a preregistered
evaluation-hardening and sample-expansion plan (see step d below), now
drafted as
[SPY_VWAP_REVERSION_PREREGISTRATION.md](SPY_VWAP_REVERSION_PREREGISTRATION.md)
and not yet implemented or run. (Superseding update, 2026-09-25: the plan
was implemented and its single confirmation run completed and verified,
with primary label `supported_for_further_shadow_research`. See
[SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md](SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md).
It is not validation and does not authorize step f; the holdout remains
sealed.)
**Step e — the deterministic Contract Selector — is now also done
(2026-09-16), implemented and tested entirely offline with synthetic
fixtures only.**
`market_intelligence/contract_selection/contracts.py` /
`selector.py` / `serialization.py` and the dry-run-first
`scripts/select_spy_option_contracts.py` exist and are covered by contract,
filter, serialization, offline-import-boundary, and CLI tests. It runs
**without any AI model** and before any future strategy agent, consuming
exactly SPY, one already-validated `ScenarioHorizon` from the step-c regime
engine (or `indeterminate`, which always yields an empty eligible set
before any other filter runs), one bounded option-chain batch with one
retrieval instant, the underlying price/as-of time, an optional explicit
directional side, and bounded, centralized, documented `SelectorConfig`
thresholds. Three **batch-level gates** — feed governance, then two
freshness checks (future-dated, then stale) — run before any per-contract
filter, since every contract in one batch shares the same feed and the
same `retrieved_at`; the remaining nine
filters then run per contract (expiration/DTE; option type when a
directional side is explicitly supplied; strike/moneyness; delta range;
required implied volatility and Greeks — never filled with zero when
missing; positive bid/ask; non-crossed quote; maximum absolute and
percentage spread; minimum quote size where available). It produces
eligible/research-only contracts in deterministic order, bounded rejection
counts by a fixed reason enum, and one of four statuses (`eligible` /
`no_eligible_contracts` / `research_only` / `indeterminate`) — **no
recommendation, ranking, score, prediction, or trade action of any kind.**

**Feed-safety boundary, hardened same-day (2026-09-16, Completed Work Log
item 46): operational eligibility (`SelectorStatus.ELIGIBLE`) defaults to
OPRA only.** `SelectorConfig.allow_indicative_for_research` defaults to
`False`; an indicative-feed batch can **never** reach `ELIGIBLE` — without
explicit research permission the whole batch is rejected at the feed gate
(`RejectionReason.FEED_NOT_ALLOWED`, status `no_eligible_contracts`); with
`allow_indicative_for_research=True` it is processed, but the result status
is always `research_only` and any passing contracts land in a **separate**
`research_only_contracts` field, never `eligible_contracts` (which stays
empty unless `status == eligible`). This is enforced at the schema level —
`ContractSelectorResult` refuses to construct an `eligible` result on a
non-OPRA feed or a `research_only` result on a non-indicative feed — so it
is structurally impossible for a `research_only` result to satisfy
whatever eligible-set contract the future Options Strategy Agent consumes.
`scripts/select_spy_option_contracts.py` requires its own explicit
`--allow-indicative-research` flag before processing indicative data at
all, and that flag **always overrides** whatever `allow_indicative_for_research`
the `--input` JSON file itself requests — the CLI, not the input file, is
the trust boundary. Open interest remains unavailable from the
option-chain endpoint and is never invented, inferred, or replaced with
volume; a `research_only` result always carries `feed_is_live_opra=False`
and fixed non-live/non-OPRA and research-only notes. **No real selector run
has been performed against the real local database or the one stored SPY
option-chain batch, and every filter threshold in `SelectorConfig` is a
provisional hypothesis — none has been tuned against the step-d
VWAP-reversion evaluation or the single stored option batch.** See
"Implementation order" below (step e) for the exact filter/gate order and
thresholds, and `PROJECT_STATE.md` (Completed Work Log items 45–46).
**Step f, the Options Strategy Agent, has NOT begun — it requires its own
separate authorization, and does not begin automatically
from step e's completion.**

Everything else in this document — the Options Strategy Agent and the
shadow-evaluation stage — remains **design only: no code, schema, or agent
exists, and nothing is validated. Step f, the Options Strategy Agent, is
not the current next implementation step: step e's offline implementation
is complete (see above), but starting step f requires its own separate
authorization and has not occurred.** This document
records the intended shape of Phase 1 so the design is agreed before
implementation begins. It is subordinate to
[PROJECT_STATE.md](../PROJECT_STATE.md) (authoritative status),
[DECISION_RULES.md](../DECISION_RULES.md) (binding boundaries), and
[CLAUDE.md](../CLAUDE.md) / [AGENTS.md](../AGENTS.md).

## Relationship to Phase 0

Phase 0 (Infrastructure Foundation) is **closed as of 2026-08-28**. Its final
requirement was met:

- one real, human-reviewed offline agent characterization — the Macro Analyst
  characterization of 2026-08-28, covering every claim, with findings preserved
  (criterion P0-7 — see [docs/PHASE_0_EXIT.md](PHASE_0_EXIT.md) and
  [docs/AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md)).

Phase 0 closure means the required infrastructure and the required
agent-evaluation methodology exist and have each been exercised and recorded
once. It is **not** a claim that any agent — the future Options Strategy Agent
included — is validated, accurate, repeatable, or profitable, and **no options
component in this document is implemented or validated**. With Phase 0 closed,
**Phase 1 has begun; step b (read-only SPY option-chain ingestion and local
storage) is done, including one live ingestion and a structural audit
(2026-09-14), and step c (the deterministic SPY intraday feature/regime
engine) is done offline with synthetic tests only (2026-09-15). Step d's
offline VWAP-extension/reversion evaluation tooling is done, including its
first real, read-only evaluation run against the stored SPY bars
(2026-09-15) — 5 gapless regular sessions, 390 candidate decision points,
observation-level sample-size thresholds met for three of four horizons,
session-level thresholds met for none (only 5 sessions stored), no
threshold tuned, and no edge, accuracy, usefulness, strategy validity, or
profitability established. This one small-sample run must not be read as
evidence for or against the VWAP-reversion hypothesis. (Superseding update,
2026-09-23: an expanded 26-session run passed the fixed session-level
threshold for all six available horizon/side cells and recorded an
above-/below-VWAP asymmetry worth further research — still no edge,
accuracy, strategy validity, or profitability established; see
`PROJECT_STATE.md` Completed Work Log item 52.) Step e (the
deterministic Contract Selector) is also now done (2026-09-16), offline
with synthetic tests only, including a same-day feed-safety-boundary fix
(operational eligibility defaults to OPRA only, indicative data requires
explicit research opt-in and an explicit CLI flag and then yields a
structurally separate `RESEARCH_ONLY` status, never `ELIGIBLE`) — no real
selector run has been performed and no contract recommendation exists.
Step f (the Options Strategy Agent) has NOT
begun — it requires its own separate authorization and is not the current
next implementation step.**

## Phase 1 objective

Build an **automated, evidence-based SPY options decision-support
workflow** that is measurably more disciplined than asking a general chatbot
for a market prediction: deterministic evidence gathering, deterministic
regime classification, deterministic contract-eligibility filtering, and
then a bounded directional strategy agent that consumes only the validated
structured outputs of the upstream stages and the deterministic selector's
validated eligible contract set, followed by a recorded evaluation before
any claim of usefulness. SPY only. Decision support only — every trade
decision and its execution stay manual, exactly as today.

## Architecture

Each stage produces a **validated, structured output**. A downstream stage
consumes only the validated output of upstream stages — never free text,
never raw model output, never provider payloads, never a raw option chain.
Every agent stays single-turn, no-tools, behind a deterministic preflight
gate and deterministic post-response validators, mirroring the structural
guarantees of the three existing agents.

The three existing agents (Market Evidence, News, Macro) remain strictly
non-directional. The future **Options Strategy Agent is not one of them**:
it is a separately bounded, directional decision-support agent, and the
non-directional output policy that governs the three existing agents does
not apply to it. It is bounded instead by a fixed-shape validated output,
by consuming only validated upstream outputs plus the deterministic
selector's eligible contract set, by the mandatory `no_trade` outcome, and
by the manual-execution boundary — all described below.

The scenario horizon originates with the deterministic Intraday
Regime/Setup Engine, not with the Options Strategy Agent. The deterministic
Contract Selector consumes that validated upstream horizon when constructing
the eligible contract set, and it runs **before** the Options Strategy
Agent. The agent never receives a raw option chain; it receives only the
selector's validated eligible contract set and may only rank and explain
contracts already in it, and it may repeat or reference the supplied horizon
but cannot invent, extend, or override it.

| Stage | Type | Responsibility |
|---|---|---|
| **Market Evidence Agent** | agent (extends today's) | Price, volume, candlestick structure, VWAP and VWAP extension, opening range, realized/known volatility, and key prior-day/session levels — described factually, no direction. |
| **News Analyst** | agent (extends today's) | Catalysts present in stored news: relevance to SPY, timing relative to the session, affected symbols/sectors. No direction. |
| **Macro Analyst** | agent (today's, unchanged scope) | Economic and market-regime *context* from stored FRED observations and official metadata. Factual only; not a regime predictor. |
| **Intraday Regime/Setup Engine** | deterministic (no model) | Produces two validated values from the structured features below, by fixed published rules: (1) a regime classification — exactly one of `trend_continuation`, `vwap_mean_reversion`, `range`, `event_driven`, `indeterminate`; and (2) exactly one scenario-horizon bucket from the fixed enum `intraday_30m`, `intraday_2h`, `to_session_close`, `next_session`, or `indeterminate` when no bounded horizon is supported. Both are validated before any downstream stage consumes them. |
| **Deterministic Contract Selector** | deterministic (no model) | Filters the option chain to an **eligible set** by expiration window, strike, delta, other Greeks, IV, liquidity/open interest, bid-ask spread, and the validated scenario-horizon bucket produced upstream by the Intraday Regime/Setup Engine. Runs **before** the Options Strategy Agent and independently of it. |
| **Options Strategy Agent** | agent (directional decision-support; **not** one of the three non-directional agents) | Consumes only (a) the validated structured outputs of the Market Evidence, News, Macro, and Intraday Regime/Setup stages (including the regime classification and the scenario-horizon bucket) and (b) the Deterministic Contract Selector's validated eligible contract set. Emits a bounded strategy recommendation **or `no_trade`**. Never consumes a raw option chain, raw evidence, or free text, cannot introduce a contract that is absent from the eligible set, and may repeat or reference the supplied scenario horizon but cannot invent, extend, or override it. |

## Terminology

- **Do not** describe distance from VWAP as intrinsic "overvaluation" or
  "undervaluation." VWAP is an intraday execution-benchmark statistic, not a
  valuation of the underlying.
- Call it **"statistical intraday extension from VWAP"** (or another
  explicitly defined intraday fair-value-proxy term, defined where used).
  Every use must state that it is a session-relative, mechanical measure.
- **A large VWAP extension alone is not a trade signal.** Extension is one
  conditioning feature among several. A directional or mean-reversion thesis
  requires the regime classification plus corroborating features (trend
  strength, slope, volume, time of day, catalyst state), not extension by
  itself. This is binding — see [DECISION_RULES.md](../DECISION_RULES.md).

## Required VWAP / reversion features

The Intraday Regime/Setup Engine is deterministic and consumes a fixed
feature set, computed from a single, explicit bar cadence — the
implementation (step c, below) fixes this to the canonical SPY 5-minute
bar, matching the stored `alpaca_bars_spy_5min` dataset; a lookback or
window defined below is always an explicit elapsed-time span in multiples
of that bar interval, never an ambiguous count of "recent bars." At
minimum:

- **Normalized VWAP distance** — extension scaled by a volatility or ATR
  measure so it is comparable across sessions.
- **VWAP slope** — direction and magnitude of the VWAP trend.
- **Opening gap and opening range** — gap vs. prior close; opening-range
  high/low and whether price holds inside or breaks out.
- **Trend strength** — a bounded measure (e.g. directional movement,
  consecutive-bar structure) distinguishing a trending session from a
  balanced one.
- **Relative volume** — session volume vs. a rolling baseline for the same
  time of day.
- **Time of day** — session phase (open / mid / power hour / close), since
  reversion and continuation behave differently by phase.
- **Market breadth** — where available (e.g. advancers/decliners, a breadth
  proxy); explicitly marked unavailable when not ingested.
- **Prior-day and session levels** — prior high/low/close, overnight range,
  current-session high/low.
- **Catalyst / news state** — from the News Analyst: is a scheduled or
  breaking catalyst active in this session?
- **Forward reversion outcomes over fixed horizons** — for evaluation only:
  realized move back toward (or away from) VWAP over fixed forward windows,
  used to characterize the hypothesis, never as a live input.

**The engine must distinguish likely trend days from mean-reversion
conditions.** On a classified trend day, a large VWAP extension is expected
behavior and must not be read as a reversion setup.

**Engine outputs.** From this feature set the engine emits, by fixed
published rules, exactly two validated values: the regime classification and
one scenario-horizon bucket from the fixed enum (`intraday_30m`,
`intraday_2h`, `to_session_close`, `next_session`, or `indeterminate` when
no bounded horizon is supported). These are the only horizon and regime
values in the workflow: the Deterministic Contract Selector consumes the
validated horizon bucket when it builds the eligible set, and no downstream
stage — including the Options Strategy Agent — may invent, extend, or
override either value.

## Options Strategy Agent — bounded output

The Options Strategy Agent is a bounded **directional** decision-support
agent — distinct from the three non-directional analysis agents (Market
Evidence, News, Macro), which are unchanged. Its inputs are exactly two:

- **a.** the validated structured outputs of the evidence, news, macro, and
  regime stages; and
- **b.** the deterministic Contract Selector's validated eligible contract
  set.

It never consumes a raw option chain and cannot introduce a contract that
is absent from the eligible set. Every trade decision and its execution
remain manual, exactly as today.

The agent's structured output is fixed-shape, strictly validated, and
bounded. It contains exactly:

- **Underlying** (`SPY`) and **timestamp** (UTC).
- **Scenario and horizon** — the classified regime and the scenario-horizon
  bucket, both exactly as produced upstream by the Intraday Regime/Setup
  Engine. The agent may repeat or reference the supplied horizon but cannot
  invent, extend, or override it; the horizon it reports must equal the one
  it was given (and `no_trade` applies when that horizon is `indeterminate`).
- **Directional or mean-reversion thesis** — a bounded statement of the
  expected behavior, tied to the regime; or none, for `no_trade`.
- **Entry condition, invalidation, and target** — explicit, checkable
  conditions (mirrors the forecast requirements in
  [DECISION_RULES.md](../DECISION_RULES.md)).
- **Strategy type** — from a fixed enum (e.g. long call / long put / debit
  vertical / credit vertical / … ) — no free-form structures.
- **Expiration window and target delta** — ranges, not a specific contract.
- **IV / Greek / liquidity considerations** — bounded notes the human can
  check against the already-filtered eligible set.
- **Maximum acceptable bid-ask spread** — an explicit cap.
- **Up to three ranked candidate contracts** — chosen **only** from the
  Contract Selector's eligible set (see below).
- **Reasons for ranking** — why each candidate is ordered as it is.
- **Cancellation conditions** — what would void the recommendation before
  entry.
- **`no_trade`** — a first-class, fully valid outcome, not a failure. The
  agent must return `no_trade` when the regime or the scenario horizon is
  `indeterminate`, when no eligible contract exists, or when the evidence
  does not support a thesis.

## AI does not choose an unrestricted contract

- The **Deterministic Contract Selector** builds the eligible contract set
  from fixed safety and liquidity rules (expiration window, strike/delta
  bounds, Greek bounds, IV bounds, minimum liquidity/open interest, maximum
  bid-ask spread, and the validated scenario-horizon bucket produced
  upstream by the Intraday Regime/Setup Engine). This runs without any
  model, and it runs **before** the Options Strategy Agent, which receives
  only the validated eligible set — never the raw chain.
- The Options Strategy Agent may only **rank and explain** contracts that
  are already in that eligible set. It cannot introduce, widen, or override
  the set, and it cannot name a contract the deterministic rules excluded.
- **No brokerage integration, automatic execution, order placement, or
  Robinhood automation is authorized** — for this workflow or any other. See
  [DECISION_RULES.md](../DECISION_RULES.md), "Execution Boundaries." All
  execution remains manual, by the user.

## Evaluation stages

Nothing in this workflow may be called useful, validated, or profitable
without recorded evidence.

1. **Backtest the underlying SPY setup first** — does the deterministic
   regime/setup classification have an edge on the underlying, separately
   from any options overlay?
2. **Evaluate option performance separately** — IV, theta, gamma, and
   spreads change outcomes; underlying-direction accuracy and
   options-contract P&L are distinct questions and are reported separately
   (this is already binding — see [DECISION_RULES.md](../DECISION_RULES.md),
   "Analytical Integrity").
3. **Use realistic bid-ask / slippage assumptions** — model fills at
   conservative prices within the spread, not at the mid.
4. **Shadow-test recommendations for at least 30–50 sessions** — record
   each recommendation live (no execution) and score it after the fact.
5. **Measure, and record:** setup classification accuracy, option P&L,
   maximum adverse excursion, confidence calibration, per-regime
   performance, and `no_trade` quality (how often `no_trade` avoided a loss
   vs. missed a gain).
6. **One successful run is not validation.** No profitability or validation
   claim may rest on a single session or a single good outcome.

## Implementation order

Each step is a separate, individually reviewed change. Do not begin a step
before the previous one is complete and recorded.

- **a.** Close Phase 0 by completing and recording the first real agent
  characterization (P0-7). **Done — the Macro Analyst characterization of
  2026-08-28; Phase 0 is closed.**
- **b.** *(first Phase 1 step — partially built offline, 2026-08-28)* Add
  **read-only** option-chain ingestion and local storage (SPY only) — its
  own reviewed connector, sanitized, no execution surface, following the
  existing connector/storage patterns.
  - **Done in code and tests only** (2026-08-28; request ceilings tightened
    and provenance normalized to two tables 2026-09-01 after pre-commit
    review; revised again 2026-09-14 to a three-table design after a
    pre-commit review found the two-table design let a mutable
    `ingestion_run_id` silently move a historical snapshot's batch
    membership on re-ingestion — see "Option-chain snapshot storage" in
    `docs/STORAGE_ARCHITECTURE.md`): `AlpacaOptionsChainClient`
    (`market_intelligence/data_connectors/alpaca_options_chain.py`) talks
    only to `data.alpaca.markets` and only to
    `GET /v1beta1/options/snapshots/SPY`; it requires a fully bounded,
    validated request (SPY only; explicit `opra`/`indicative` feed;
    expiration-date and strike-price windows; optional `call`/`put`; bounded
    page limit, max pages, and max total contracts). **Conservative Phase 1
    hard ceilings** (safety ceilings, not contract-selection rules — callers
    must still supply explicit ranges): expiration span ≤ 60 calendar days,
    strike-window width ≤ $500, per-page limit ≤ 1,000 (the provider's own
    maximum), max pages ≤ 10, max total contracts ≤ 5,000 — all enforced
    before any HTTP request is built. It follows `next_page_token`
    pagination deterministically with loop/bound guards and no retry, parses
    and cross-checks the OCC contract symbol against the request filters, and
    normalizes only the fields the endpoint supplies (contract symbol,
    underlying, expiration, type, strike, feed; latest quote timestamp /
    bid-ask price and size; latest trade timestamp / price and size; implied
    volatility; delta/gamma/theta/vega/rho where supplied; retrieval
    timestamp). Missing optional fields stay null; errors are sanitized to a
    fixed category (no response body, headers, URL, query parameters,
    credentials, or contract payload). Migration `0009` adds a normalized,
    **three-table** design: `option_chain_snapshot_batches` records exactly
    one row per successfully stored chain retrieval — **including a
    retrieval that returned zero contracts** (run-level provenance:
    requested feed, requested expiration/strike window, requested option
    type, retrieval instant, contract count with zero allowed);
    `option_chain_snapshots` stores one row per **immutable** normalized
    contract observation, whose `ingestion_run_id` records only the run that
    first inserted it (never reassigned by a later re-observation); and
    `option_chain_snapshot_batch_items` is the normalized batch-membership
    table, with one row per `(ingestion_run_id, snapshot identity)` pair for
    every contract a non-empty successful batch actually returned — this is
    what keeps a batch's recorded `contract_count` and its truthful
    membership from drifting apart when the same snapshot identity is
    re-ingested into a later batch; one immutable observation may
    legitimately be referenced by more than one batch's membership rows.
    `OptionChainSnapshotRepository` and the dry-run-first
    `scripts/ingest_alpaca_options_chain.py` store all three transactionally
    (the batch row, every snapshot row, every batch-item row, and the
    ingestion-run status update all in one transaction), keying
    `option_chain_snapshots` on
    `(provider, underlying, feed, contract_symbol, retrieved_at)` so OPRA
    and indicative observations are never merged, keying
    `option_chain_snapshot_batches` on `ingestion_run_id` (always an insert),
    and keying `option_chain_snapshot_batch_items` on `(ingestion_run_id,
    provider, underlying, feed, contract_symbol, retrieved_at)` (also always
    an insert). The repository re-normalizes every field of a supplied
    request through `normalize_option_chain_request` and rejects it unless
    it matches its own canonical form, so a hand-constructed
    `OptionChainRequest` cannot bypass the connector's request ceilings.
  - **As originally written (2026-08-28 through 2026-09-01): not done** — no
    live option-chain request had occurred, no real option data had been
    stored, and migration `0009` had not been applied to the real local
    database. That statement is preserved here as an honest, time-scoped
    diagnostic record and is not retracted.
  - **Superseded (2026-09-14): done, including one live run.** Migration
    `0009` was applied to the real local database (backed up beforehand;
    read-only health check reported schema version `0009`, `healthy=True`).
    `scripts/ingest_alpaca_options_chain.py --execute` was then run once,
    live: provider `alpaca`, underlying `SPY`, requested feed `indicative`
    (explicitly not OPRA), one expiration (2026-09-18), strikes 740–790. 102
    contracts were received and inserted (51 calls, 51 puts); one batch
    (`outcome=succeeded`) and one `ingestion_runs` row (`succeeded`) were
    recorded; exactly one request was made, with no retry. A subsequent
    read-only structural audit (no network/provider request, no rerun, no
    database modification, no contract symbol/price/quote/Greek/timestamp
    printed) confirmed, as aggregate counts only: 102 batch-membership rows
    and 102 snapshot rows, zero duplicate or orphan memberships,
    `contract_count` equal to the membership count, zero malformed OCC
    symbols or out-of-range contracts, quote/trade data present for all 102,
    implied volatility and each Greek present for 78 and unavailable
    (`NULL`, never zero) for 24, and zero negative/nonfinite values, crossed
    quotes, or feed mismatches. `indicative`-feed data may be delayed or
    modified by the provider and must never be described as live OPRA data.
    This confirms **internal structural consistency only** — not pricing
    accuracy, timeliness, usefulness, predictive edge, strategy validity, or
    profitability; only one batch exists, so recurring reliability is not
    established. **Open interest is not available** from this endpoint and
    is not stored, inferred, or defaulted on any of the three tables; a
    later milestone would need a different source for it. See
    `PROJECT_STATE.md` (Completed Work Log item 40) and `DATA_CATALOG.md`
    for full sanitized detail.
- **c.** *(third Phase 1 step — done offline, 2026-09-15, synthetic tests
  only)* Build the deterministic SPY intraday feature/regime engine (the
  feature set and classifier above). No model. **Done:**
  `market_intelligence/market_features/spy_regime_contracts.py`
  (`IntradayBar`, `PriorDayLevels`, `RegimeEngineInput`, `RegimeFeatures`,
  `RegimeClassificationResult`, and the fixed `CatalystState` /
  `BreadthState` / `OpeningRangePosition` / `PriorDayRangePosition` /
  `TimeOfDayBucket` / `Regime` / `ScenarioHorizon` enums — all strict
  Pydantic v2, `extra="forbid"`, bounded collections),
  `spy_regime_features.py` (`compute_features`: a pure function operating
  only on the canonical SPY 5-minute bar cadence — the same `5Min`
  interval as the stored `alpaca_bars_spy_5min` dataset; every bar's
  America/New_York timestamp must land on the 5-minute grid, and a bar off
  that grid is rejected by `RegimeEngineInput` itself, before any feature
  is computed — **grid alignment alone does not prove continuity**: a
  10-minute-spaced feed (`09:30`, `09:40`, `09:50`, ...) is just as
  grid-aligned as true continuous 5-minute data, so `compute_features`
  separately compares the supplied timestamps against every expected
  5-minute slot from `09:30` through the latest bar and reports
  `session_bars_complete` / `missing_interval_count` on every
  `RegimeFeatures` — computing cumulative session VWAP, close-to-VWAP
  distance in bps, a volatility-normalized VWAP extension
  (`close_to_vwap_distance_bps / realized_intraday_volatility_bps` — both
  terms already in basis points, so the ratio is dimensionally consistent;
  `None` for a zero or non-finite volatility denominator, never divided
  into), VWAP slope over a lookback that is exactly 6 bars **and** exactly
  30 elapsed minutes between the two endpoint bars (an internal gap that
  would make 6 bars span more or less than 30 real minutes yields `None`
  rather than a slope computed over the wrong elapsed time), opening gap
  vs. prior close, an opening range defined as exactly the three completed
  09:30/09:35/09:40 bars (unavailable until all three specific bars are
  present — a gap at any one of them fails closed rather than
  approximating from the others), session return, realized intraday
  volatility, a signed trend-strength efficiency ratio, relative volume
  (only when a same-time historical baseline is supplied, itself required
  to be on the same 5-minute cadence and elapsed-session position),
  prior-day high/low/close relationship, and a fixed time-of-day bucket —
  **every "as of now" field (`as_of_timestamp`, the time-of-day bucket, and
  minutes remaining in session) is derived from the completed latest bar's
  *end*, `bars[-1].timestamp + 5 minutes`, never from its start timestamp**,
  because a canonical Alpaca 5-minute bar timestamp identifies when the bar
  opened and the bar is only actually complete and observable 5 minutes
  later (a bar stamped `15:30` is only available at `15:35`; at the
  `15:55` bar, that end time is `16:00` and minutes remaining is `0`) —
  grid/completeness checks and the opening-range bars remain keyed to each
  bar's own start timestamp, unaffected by this),
  and
  `spy_regime_classifier.py` (`classify_regime` / `classify_horizon` /
  `classify` / `classify_batch`: the fixed, published, centralized-threshold
  decision order — **an incomplete session (`session_bars_complete is
  False`) forces both `regime` and `scenario_horizon` to `indeterminate`
  before every other rule, including event-driven, so a missing 5-minute
  interval can never be classified even with an active catalyst**;
  otherwise, event-driven only from an explicit `catalyst_state ==
  ACTIVE` input, never from price; trend-continuation requires aligned
  trend/VWAP-slope/opening-range/extension-direction evidence;
  mean-reversion requires the extension threshold **plus** at least two of
  four independent corroborators and a not-a-trend-day check, so a large
  extension alone can never produce a reversion call; range requires both
  low trend strength and a contained extension; anything else is
  `indeterminate`). Every unavailable/insufficient input (missing prior-day
  levels, missing volume baseline, unknown catalyst/breadth state,
  insufficient bar history) is `None`/an explicit "unavailable" enum value,
  never a substituted zero. No lookahead is possible by construction — the
  engine only ever sees `bars[-1]` as "now." A pure `classify_batch`
  interface supports offline historical evaluation, preserving input order
  and producing deterministic output. **Implemented and tested offline
  with synthetic fixtures only — no real SPY session has been classified,
  every classifier threshold is a provisional hypothesis, and no predictive
  accuracy or mean-reversion edge has been established.** See
  `PROJECT_STATE.md` (Completed Work Log item 41).
- **d.** Test the VWAP-extension / reversion hypothesis on the underlying
  using the forward-outcome features — record the result whether positive,
  null, or negative. **Tooling implemented and verified offline (2026-09-15)
  with synthetic fixtures and tests, and now also run once, read-only,
  against the stored SPY bars (2026-09-15) — see the result summary at the
  end of this item and `PROJECT_STATE.md` Completed Work Log item 44.
  Step d is now DONE.**
  `market_intelligence/evaluation/spy_vwap_reversion_contracts.py`
  (strict Pydantic v2 contracts, `extra="forbid"`, bounded collections;
  `SessionBars` reuses the step-c engine's own `RegimeEngineInput` validation
  so every bar-prefix the evaluator builds is independently valid),
  `spy_vwap_reversion_evaluator.py` (`evaluate_spy_vwap_reversion`: a pure
  function that, for every candidate bar in every supplied session, builds
  a no-lookahead bar-prefix signal exactly as the step-c engine would, using
  the *same* `compute_features` / `classify` calls — regime, horizon,
  session-completeness, and the session VWAP are frozen at signal time and
  never recomputed later), and `spy_vwap_reversion_serialization.py` (the
  same symlink-refusing / no-overwrite / atomic / bounded local JSON round
  trip as the existing evaluation foundation, for both the input and the
  output record), plus the dry-run-first `scripts/evaluate_spy_vwap_reversion.py`.
  A decision point is **eligible** only when the frozen signal-time VWAP is
  available and the extension is non-zero; every other candidate bar is
  still recorded (never silently dropped) with its exclusion reason.
  **Point-in-time context is narrowed to the safest boundary
  (2026-09-15 integrity fix, same day as the tooling above).**
  `SessionBars.same_time_historical_volume_baseline` / `catalyst_state` /
  `breadth_state` are supplied once per session and would otherwise be
  reused, unchanged, by every bar-prefix signal in that session — a real
  same-time volume baseline, catalyst state, or breadth state is a function
  of elapsed session time, not a single full-session value, so reusing one
  risks lookahead or an elapsed-time mismatch. `SessionBars` now requires
  (by validator, not convention) `same_time_historical_volume_baseline=None`,
  `catalyst_state=unknown`, and `breadth_state=unavailable`, rejecting any
  other value; every candidate decision point is therefore classified
  without relative-volume, catalyst, or breadth evidence.
  **Relative-volume-aware, event-driven, and breadth-aware evaluation are
  not performed by this evaluator and require a future point-in-time
  context contract.** For each eligible point, four fixed forward horizons
  (`intraday_30m` / `intraday_2h` / `to_session_close` / `next_session`) are
  each scored **only** when the full horizon exists gaplessly in the
  supplied data (a horizon is never shortened or approximated) — whether
  price touches/crosses the frozen VWAP, time to first touch, percentage of
  the original extension retraced, signed return toward/away from VWAP, and
  maximum favorable/adverse excursion. **`next_session` is always
  unavailable in this milestone, for every decision point, with no
  exception (same 2026-09-15 fix)** — this repository has no exchange
  calendar, so the next entry supplied in `SpyVwapReversionEvaluationInput
  .sessions` is never treated as a verified next trading session (not even
  across an apparently contiguous Friday-to-Monday boundary); enforced both
  in the evaluator (the `next_session` window is never built) and by a
  `HorizonOutcome` validator that rejects an available `next_session`
  outcome outright, so the guarantee cannot silently regress. Next-session
  scoring requires a future deterministic exchange-calendar/contiguity
  boundary. `30m`, `2h`, and `to_session_close` scoring are unaffected by
  this fix. Every summary is reported at two
  levels — `observation_level` (pooling every eligible five-minute
  observation directly, which overlap heavily and are explicitly *not*
  independent) and `session_level` (first reducing each session to its own
  mean, so a long session can never dominate a cross-session statistic) —
  and every summary is gated by a fixed, provisional (not tuned to any
  result) minimum sample size, reporting `insufficient_sample` rather than a
  value computed from too few points. **The evaluation always uses the
  step-c engine's existing, unmodified `RegimeThresholds` — this step never
  tunes, optimizes, or grid-searches any threshold** — and records (never
  re-derives) the exact threshold values a run used, for audit purposes
  only. No P&L, options return, win rate, profitability figure, or trade
  recommendation exists anywhere in these contracts. Tests cover exact
  forward-horizon boundaries, frozen-VWAP touch/no-touch, partial/full/no
  retracement and overshoot, favorable/adverse excursion, bullish/bearish
  symmetry, unavailable future horizons, no-lookahead, incomplete sessions,
  overlapping-observation accounting, session-level aggregation,
  insufficient samples, deterministic serialization, path safety, sanitized
  errors, and the offline import boundary — **plus, from the 2026-09-15
  integrity fix**: rejection of a non-null volume baseline, a
  scheduled/active catalyst, and an available breadth state (with defaults
  still accepted); no prefix ever receiving context from a later session;
  `next_session` unavailable even when another dated session follows,
  including across a Friday-to-Monday boundary (no weekend/holiday
  assumption is guessed); `missing_horizon_counts` correctly counting
  `next_session` for every eligible point; and sanitized errors that never
  reproduce a rejected point-in-time-context value. **First real run
  (2026-09-15, documentation-only branch
  `docs/record-spy-vwap-reversion-first-evaluation`):** the stored
  `alpaca_bars_spy_5min` dataset was inspected read-only and filtered to the
  regular 09:30–16:00 America/New_York session, yielding exactly 5 gapless,
  grid-aligned, contiguous Monday–Friday regular sessions (2026-08-17
  through 2026-08-21) — 390 candidate decision points, all eligible (zero
  VWAP-unavailable, zero zero-extension exclusions). `prior_day` was
  supplied only for the four sessions with a real stored predecessor
  (computed from that predecessor's own bars); the first session's
  `prior_day` is `None`. Regime counts (eligible, identical to all
  candidates here): `vwap_mean_reversion=151`, `range=141`,
  `indeterminate=98`, `trend_continuation=0`, `event_driven=0` (expected,
  since `catalyst_state` is `unknown` throughout). Extension sides:
  `above_vwap=122`, `below_vwap=268`. Observation-level sample-size
  thresholds (≥ 50) were met for both sides on `intraday_30m` (117 / 243
  available), `intraday_2h` (109 / 161 available), and `to_session_close`
  (122 / 263 available); `next_session` had 0 available on both sides, by
  fixed design. Session-level thresholds (≥ 20 sessions) were met for
  **no** horizon or side — only 5 sessions are stored, so every
  session-level statistic is `insufficient_sample` and none is reported.
  No threshold was tuned; the evaluation used step c's existing, unmodified
  `RegimeThresholds` throughout.

  **Regime breakdown, eligible decision points by extension side** (390
  total; `event_driven`/`trend_continuation` are `0` throughout — expected,
  since `catalyst_state=unknown` for every candidate):

  | Regime | above_vwap | below_vwap | Total |
  |---|---:|---:|---:|
  | `vwap_mean_reversion` | 33 | 118 | 151 |
  | `range` | 53 | 88 | 141 |
  | `indeterminate` | 36 | 62 | 98 |
  | `trend_continuation` | 0 | 0 | 0 |
  | `event_driven` | 0 | 0 | 0 |

  **Aggregate outcome statistics — observation_level** (the only level
  that cleared any sample-size threshold; these are 390 heavily
  *overlapping* five-minute observations from only 5 sessions, not
  independent trials). `pct_extension_retraced` is % of the original
  extension retraced toward VWAP; `signed_return_toward_vwap_bps` / MFE /
  MAE are in bps of signal close; positive = toward VWAP, negative =
  further extension away from VWAP. `next_session` is omitted — always `0`
  available by fixed design (no exchange calendar), not a small-sample
  result.

  | Horizon | Side | N avail (of eligible) | Touched (rate) | Median mins-to-touch | Pct retraced: median / mean | Signed return bps: median / mean | MFE bps: median / mean | MAE bps: median / mean |
  |---|---|---:|---:|---:|---:|---:|---:|---:|
  | `intraday_30m` | above_vwap | 117 (122) | 72 (61.5%) | 5 | 62.05 / 70.90 | 4.37 / 2.73 | 8.55 / 9.63 | 4.45 / 7.13 |
  | `intraday_30m` | below_vwap | 243 (268) | 117 (48.1%) | 5 | −8.25 / −2.95 | −1.04 / −0.82 | 4.90 / 6.11 | 5.68 / 6.78 |
  | `intraday_2h` | above_vwap | 109 (122) | 89 (81.7%) | 10 | 140.41 / 169.61 | 9.86 / 9.06 | 17.04 / 17.74 | 6.53 / 11.12 |
  | `intraday_2h` | below_vwap | 161 (268) | 124 (77.0%) | 10 | −74.16 / −299.61 | −5.78 / −5.55 | 8.20 / 11.75 | 11.84 / 14.75 |
  | `to_session_close` | above_vwap | 122 (122) | 117 (95.9%) | 15 | 232.25 / 956.87 | 16.59 / 20.45 | 23.29 / 28.02 | 8.42 / 11.30 |
  | `to_session_close` | below_vwap | 263 (268) | 159 (60.5%) | 10 | −125.77 / −743.29 | −11.40 / −15.55 | 6.19 / 9.59 | 17.77 / 22.09 |

  Minimum/maximum values (not shown per-row above) range widely —
  `pct_extension_retraced` spans roughly −27,300% to +30,918% (the formula
  divides by the original extension, so a few small-extension outliers
  dominate the mean far more than the median); `signed_return_toward_vwap_bps`
  spans roughly −57 bps to +69 bps; `max_favorable_excursion_bps` and
  `max_adverse_excursion_bps` both include negative minimums (e.g.
  `intraday_30m` above_vwap MFE min ≈ −1.30 bps) — by the published formula
  this happens when price never even revisited the signal close in the
  "favorable" direction, a real, preserved result, not an error.

  **Session-level, every horizon and side:** `n=5` sessions available
  (< 20 required) → `status=insufficient_sample` for every metric at every
  horizon and both sides — **no session-level mean/median/min/max exists
  anywhere in this run's output.**

  **Mixed / unfavorable results, preserved as-is:** the two extension
  sides disagree in direction. `above_vwap` observations show a positive
  median/mean `signed_return_toward_vwap_bps` at every available horizon
  (price moved toward VWAP, on average) — nominally favorable to the
  reversion hypothesis. `below_vwap` observations show a **negative**
  median/mean `signed_return_toward_vwap_bps` at every available horizon
  (price moved *further away* from VWAP, on average) — the **opposite** of
  the reversion hypothesis. Touch rates are also asymmetric (e.g.
  `to_session_close`: 95.9% above_vwap vs. 60.5% below_vwap). **This
  asymmetry is reported, not interpreted.**

  **Binding caveats:** the 390 observation-level values overlap heavily
  (five-minute snapshots from 5 sessions, not 390 independent trials);
  only 5 sessions exist in the stored dataset, so every session-level
  result is `insufficient_sample`; **this run establishes no edge,
  accuracy, strategy validity, or profitability** for the underlying setup
  or any options overlay — clearing the observation-level sample-size gate
  is a data-volume fact, not evidence of a real effect, and the
  above_vwap/below_vwap asymmetry must not be read as a directional
  finding, a strategy signal, or a basis for step e; no threshold was
  tuned to produce, explain, or improve any of the above numbers.

  The full, unredacted record (every decision point, every horizon
  outcome, and every descriptive statistic, including `minimum` and
  `maximum` for each metric)
  remains available locally, gitignored, under `data/evaluations/local/`
  — not committed. See `PROJECT_STATE.md` (Completed Work Log item 44).
  The 5-session record above is preserved unchanged as the historical
  first-run result.

  **Expanded run (2026-09-23, `PROJECT_STATE.md` Completed Work Log item
  52).** After one bounded, authorized SPY bars ingestion
  (2026-08-24T00:00:00Z through 2026-09-23T00:00:00Z; 1,731 bars inserted),
  the read-only input builder covered 2026-08-17 through 2026-09-22: 27
  weekdays, 26 complete sessions, 1 weekday with no regular-session bars,
  no other exclusions, `prior_day` available for 24 sessions and
  unavailable for 2, 120 outside-regular-session bars excluded. The
  evaluation found 2,028 candidate / 2,027 eligible decision points (0
  missing-VWAP, 1 zero-extension exclusion; `above_vwap` 975, `below_vwap`
  1,052; regimes `trend_continuation` 57, `vwap_mean_reversion` 757,
  `range` 599, `indeterminate` 614, `event_driven` 0; missing horizons
  `intraday_30m` 156, `intraday_2h` 624, `to_session_close` 26,
  `next_session` 2,027). All six available session-level cells passed the
  fixed 20-session threshold (26 above-VWAP sessions, 25 below-VWAP
  sessions, for `intraday_30m`, `intraday_2h`, `to_session_close`);
  `next_session` remained unavailable by fixed design. Selected aggregate
  statistics are tabulated in `PROJECT_STATE.md` item 52. **Interpretation,
  bounded:** above-VWAP signed return toward VWAP was positive at every
  evaluated horizon and both aggregation levels; below-VWAP results were
  mixed — positive at the shorter horizons, weaker, and adverse at
  observation level by session close. This is an asymmetry worth further
  research, **not a validated strategy**. Only 25–26 sessions from roughly
  five weeks; observation-level points overlap and are not independent; no
  significance test or uncertainty interval was performed;
  percentage-retraced means are unstable (tiny initial extensions produce
  extreme ratios) and are not robust evidence; minutes-to-touch figures
  derived during the audit from decision points are supplementary, not
  fields of the validated aggregate summary, and not canonical results.
  Thresholds were not tuned or changed; relative-volume, catalyst, and
  breadth context were not evaluated; no option return, P&L, selector
  recommendation, strategy-agent output, brokerage action, or accuracy,
  edge, or profitability claim exists.

  **Next research step (preregistration drafted 2026-09-23, under review;
  nothing implemented, ingested, or run).** The plan is
  [SPY_VWAP_REVERSION_PREREGISTRATION.md](SPY_VWAP_REVERSION_PREREGISTRATION.md).
  It treats the 26-session run above as the closed discovery sample and
  fixes a confirmation window (2026-02-23 through 2026-08-14), a reserved
  untouched holdout (2026-09-23 through 2026-12-04), the primary
  above-VWAP outcome over all eligible points, session-blocked bootstrap
  intervals with Holm correction, a 1.0-bps smallest effect of interest,
  and the decision labels, all before any new data is ingested. Dated
  clarification C1 (2026-09-25), appended to that document, adds
  holdout-specific sample gates (≥ 40), unrounded-`Decimal` decision
  arithmetic, provenance fields, and the separate result-contract
  architecture. It changes no window, threshold, or decision mapping.
  **Update (2026-09-25, `PROJECT_STATE.md` Completed Work Log item 53):
  the confirmation-analysis tooling is implemented and tested with
  synthetic fixtures only.** It consists of a separate result contract, a
  pure analysis over a validated evaluation record, its serialization, and
  a dry-run-first `scripts/run_spy_vwap_confirmation.py`, plus a pure
  recomputation verifier. Schema validation rejects malformed or internally
  inconsistent results but not coherent hand edits; the source hashes
  identify the claimed input and record bytes; the code commit is
  operator-supplied and not attested; statistical correctness is checked by
  recomputing from the referenced record; authenticity (custody or
  signatures) is out of scope. The existing
  evaluator and record schema are unchanged. The only bound change is the
  record read ceiling, raised to 64 MiB as approved by C1.4. No
  confirmation or holdout bar has been ingested, no real confirmation or
  holdout evaluation has run, and no result or edge claim exists. The next
  step is the separately authorized, bounded confirmation ingestion, not
  step f. Before inspecting
  any additional outcomes, write and review a preregistered
  evaluation-hardening and sample-expansion plan: session-blocked
  uncertainty estimates; robust distribution/quantile statistics; fixed,
  pre-declared minimum-extension buckets; regime and time-of-day
  breakdowns; explicitly separate above-VWAP and below-VWAP hypotheses; and
  an untouched holdout period. Later data must be treated as new evidence,
  never used to tune the existing five-week result. Implementing any of
  these additions requires its own separate review. **Step f (the Options
  Strategy Agent) is not authorized or begun by this result.**

  **Confirmation result (2026-09-25, `PROJECT_STATE.md` Completed Work Log
  item 54; report
  [SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md](SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md)).**
  - **Ingestion.** Seven bounded IEX ingestion runs added 10,543 bars.
  - **Sample.** 121 complete confirmation sessions from 2026-02-23 through
    2026-08-14; the 4 excluded weekdays are market holidays.
  - **Primary (above-VWAP, all eligible points).** Positive at all three
    horizons: 30m 4.0522 bps, 2h 12.8834, close 16.9561, each with Holm
    p 6/10001. Label: **`supported_for_further_shadow_research`**.
  - **Secondary (below-VWAP).** Positive at all three horizons. The
    close-minus-30m contrast is materially different, so the label is
    **`below_horizon_dependent`**: the magnitude changes with horizon, not
    the direction.
  - **Verification.** An independent recomputation was byte-identical.
  - **What it permits.** Only proposing a separately reviewed
    shadow-research stage for the underlying setup. It is not validation,
    an options edge, a recommendation, or a trading signal.
  - **Caveats.** IEX only. No relative-volume, catalyst, or breadth
    context. p-values sit at the 10,000-replicate resolution floor. The code
    SHA is operator-supplied. No options returns, costs, or P&L were
    measured.
  - **Holdout.** The prospective holdout (2026-09-23 → 2026-12-04) remains
    sealed. **Step f remains unauthorized.**

  **Shadow-research protocol (preregistered and frozen by its merge;
  `PROJECT_STATE.md` Completed Work Log item 55).**
  [SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md](SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md)
  defines prospective, paper-only process validation of the frozen
  underlying decision points. Its values are frozen by that merge, though
  their feasibility is untested.
  - **Every later stage needs its own authorization.** That covers stage-2
    design, implementation, migrations, scheduling, unattended operation,
    alerts, and data collection.
  - **Nothing has been done.** Nothing has been implemented or collected.
  - **Holdout.** The holdout remains sealed.
  - **Start conditions.** Collection can't start before every frozen
    prerequisite is satisfied: implementation readiness, the recorded
    holdout result, and a separate collection authorization, all complete
    before the first slot of a session no earlier than 2026-12-07.

  **Stage 2 design (drafted, awaiting review; `PROJECT_STATE.md` item
  56).** [SPY_VWAP_SHADOW_RECORDER_DESIGN.md](SPY_VWAP_SHADOW_RECORDER_DESIGN.md)
  and [SPY_VWAP_IEX_LATENCY_TEST_PLAN.md](SPY_VWAP_IEX_LATENCY_TEST_PLAN.md)
  cover the recorder's contracts, components, proposed storage, and
  reconciliation, plus the preregistered latency-test plan.
  - **After merge.** Stage 2 becomes `design_complete_test_pending`, not
    complete.
  - **Nothing authorized.** No implementation, migration, database write,
    provider request, latency test, scheduling, or collection has occurred,
    and none is authorized. **Stage 3 is not authorized** while the latency
    test is pending.
  - **Latency-test timing.** The test can't run during the sealed holdout or
    before 2026-12-07. It requires the holdout result recorded first and
    its own authorization.
  It does not authorize step f or any options, selector, recommendation,
  or execution work.
- **e.** *(fifth Phase 1 step — done offline, 2026-09-16, synthetic tests
  only; feed-safety-boundary fix applied the same day, Completed Work Log
  item 46)* Build the Deterministic Contract Selector (eligible-set
  filtering). **Done:** `market_intelligence/contract_selection/contracts.py`
  (`OptionContractQuote`, `OptionChainBatch`, `SelectorConfig`,
  `ContractSelectorInput`, `ContractSelectorResult`, and the fixed
  `OptionType` / `FeedProvenance` / `SelectorStatus` / `RejectionReason`
  enums — all strict Pydantic v2, `extra="forbid"`, bounded collections,
  timezone-aware timestamps, finite numerics), `selector.py`
  (`select_eligible_contracts`: a pure function, no model, no I/O, applying
  the fixed, published gate/filter order below to a bounded option-chain
  batch), and `serialization.py` (the same symlink-refusing /
  no-overwrite / atomic / bounded local JSON round trip pattern as the
  existing evaluation foundation), plus the dry-run-first
  `scripts/select_spy_option_contracts.py`.

  **Input.** Exactly: SPY; one already-validated `ScenarioHorizon` from the
  step-c regime engine; one bounded `OptionChainBatch` (one provider, one
  feed, one retrieval instant, up to 5,000 `OptionContractQuote` rows —
  deliberately independent of
  `data_connectors.alpaca_options_chain.OptionChainSnapshot`; this package
  never imports a data connector, storage, a model client, an agent, or
  orchestration); the underlying price and as-of time; an optional explicit
  `requested_option_type` (call/put) directional side; and bounded, centralized
  `SelectorConfig` thresholds. There is no field anywhere in this input for
  news text, model output, a credential, a database path, or brokerage
  data.

  **`indeterminate` short-circuits everything.** When `scenario_horizon` is
  `ScenarioHorizon.INDETERMINATE`, the result is
  `SelectorStatus.INDETERMINATE`, both `eligible_contracts` and
  `research_only_contracts` are always empty, and every candidate contract
  is counted under `RejectionReason.HORIZON_INDETERMINATE` — mirrors the
  step-c classifier's "incomplete session forces indeterminate before every
  other rule."

  **Feed-safety boundary: three batch-level gates run before any
  per-contract filter.** Every contract in one batch shares the same `feed`
  and the same `retrieved_at`, so all three are batch-wide facts, not
  per-contract ones — evaluating them once, first, keeps a stale,
  future-dated, or feed-disallowed batch from ever being disguised as a
  pile of individual contract-quality failures:

  1. **Feed governance** (the operational/research boundary).
     `SelectorConfig.allow_indicative_for_research` defaults to `False` —
     **operational eligibility defaults to OPRA only.**
     - `feed == opra`: always eligible-capable; proceed to the freshness gate.
     - `feed == indicative` and `allow_indicative_for_research == False`
       (default): the **entire batch** is rejected — every candidate
       counted under `RejectionReason.FEED_NOT_ALLOWED` — result status
       `NO_ELIGIBLE_CONTRACTS`. **Indicative-feed contracts can never reach
       `ELIGIBLE` this way or any other.**
     - `feed == indicative` and `allow_indicative_for_research == True`
       (explicit opt-in): proceed to the freshness gate in **research
       mode** — the eventual status will be `RESEARCH_ONLY`, never
       `ELIGIBLE`.
  2. **Freshness (two gates, in order)** — `age_seconds = (as_of_timestamp -
     batch.retrieved_at).total_seconds()`, computed **without** `abs()`:
     - if `age_seconds < 0` (the batch was retrieved after `as_of_timestamp`
       — clock skew or a caller error), the **entire batch** is rejected —
       every candidate counted under `RejectionReason.SNAPSHOT_FROM_FUTURE`
       — with status `NO_ELIGIBLE_CONTRACTS` (operational path) or
       `RESEARCH_ONLY` with zero research contracts (research path);
     - otherwise, if `age_seconds` exceeds `max_snapshot_age_seconds`
       (default `300`), the **entire batch** is rejected — every candidate
       counted under `RejectionReason.SNAPSHOT_STALE` — with the same two
       possible statuses as above.

  **Published per-contract filter order** (only reached once both batch
  gates pass; the first filter a contract fails is the one and only
  recorded reason):

  1. **Expiration / DTE** — `dte = (expiration_date - as_of_date).days`
     (`as_of_date` is `as_of_timestamp` converted to its America/New_York
     calendar date); rejected (`EXPIRATION_OUTSIDE_WINDOW`) unless `dte`
     falls within the fixed, per-horizon, inclusive `(min_dte, max_dte)`
     window in `SelectorConfig.horizon_expiration_windows` — provisional
     defaults `intraday_30m=(0,2)`, `intraday_2h=(0,3)`,
     `to_session_close=(0,1)`, `next_session=(1,5)`; every window is capped
     at 60 days (the option-chain connector's own expiration-range
     ceiling) and `indeterminate` can never appear in this mapping.
  2. **Option type** (only when a directional side is explicitly supplied)
     — rejected (`OPTION_TYPE_MISMATCH`) if `requested_option_type` is set
     and disagrees with the contract's own type; both types pass when no
     side is requested.
  3. **Strike / moneyness** — `moneyness = strike_price / underlying_price`;
     rejected (`STRIKE_OUTSIDE_MONEYNESS_BAND`) outside the inclusive
     `[min_moneyness, max_moneyness]` band (default `[0.85, 1.15]`).
  4. **Delta range** — rejected (`DELTA_OUTSIDE_RANGE`) if `delta` is
     missing (a missing delta can never be "in range") or `abs(delta)`
     falls outside `[min_abs_delta, max_abs_delta]` (default
     `[0.15, 0.65]`).
  5. **Required implied volatility and remaining Greeks** — rejected
     (`MISSING_IV_OR_GREEKS`) if `implied_volatility`, `gamma`, `theta`,
     `vega`, or `rho` is missing (delta was already confirmed present by
     filter 4). **A missing value is never filled with zero.**
  6. **Positive bid/ask** — rejected (`NON_POSITIVE_BID_ASK`) unless both
     `bid_price` and `ask_price` are present and strictly greater than
     zero.
  7. **Non-crossed quote** — rejected (`CROSSED_QUOTE`) if
     `bid_price > ask_price`.
  8. **Maximum absolute and percentage spread** — rejected
     (`SPREAD_TOO_WIDE`) if `ask_price - bid_price` exceeds
     `max_abs_spread` (default `$0.50`) or, relative to the mid price,
     exceeds `max_pct_spread` (default `15%`).
  9. **Minimum quote size, where available** — rejected
     (`QUOTE_SIZE_TOO_SMALL`) if a *present* `bid_size`/`ask_size` is below
     `min_quote_size` (default `1`); a missing size does not itself trigger
     rejection (bid/ask *price* presence is already required by filter 6).

  A contract that passes every filter is placed in `eligible_contracts`
  (operational path, `feed == opra`) or `research_only_contracts` (research
  path, `feed == indicative` with explicit opt-in) — **never both, and
  never the wrong one for the batch's feed**, enforced by
  `ContractSelectorResult` validators that refuse to construct an
  `eligible` result on a non-OPRA feed or a `research_only` result on a
  non-indicative feed, independent of `selector.py`'s own logic.

  **Open interest remains unavailable** from the option-chain endpoint (see
  `DATA_CATALOG.md`) and is never invented, inferred, or replaced with
  volume — there is no open-interest field and no open-interest filter
  anywhere in this step. **Output** carries eligible/research-only
  contracts sorted deterministically by `(expiration_date, option_type,
  strike_price, contract_symbol)` (the same ordering the option-chain
  connector already uses for its own snapshots), the complete
  `rejection_counts` by the fixed `RejectionReason` enum, one of four
  `SelectorStatus` values (`eligible` / `no_eligible_contracts` /
  `research_only` / `indeterminate`), a recorded (never re-derived)
  `SelectorConfigSnapshot` of the thresholds actually used, and
  `feed_is_live_opra` (always `False` for an `indicative` batch, with fixed
  non-live/non-OPRA and, for `research_only`, research-only notes) — **and
  nothing else: no recommendation, ranking, score, prediction, or trade
  action of any kind** (see `DECISION_RULES.md`, "AI does not choose an
  unrestricted options contract"). Every filter threshold in
  `SelectorConfig` is a provisional hypothesis, not a validated value, and
  none has been tuned against the step-d VWAP-reversion evaluation or the
  single stored option batch.

  **The CLI is the feed-safety trust boundary, not the input file.**
  `scripts/select_spy_option_contracts.py` requires its own explicit
  `--allow-indicative-research` flag before an indicative batch is
  processed at all; that flag **always overrides** whatever
  `allow_indicative_for_research` value the `--input` JSON file itself
  carries, in both directions — without the flag, indicative contracts are
  rejected/filtered deterministically regardless of what the input file
  requests; with the flag, the run proceeds in research mode. A JSON input
  file alone, however constructed, can therefore never smuggle
  indicative-feed contracts past the CLI into an operationally eligible
  set.

  Tests cover every per-contract filter at its exact boundary, deterministic
  ordering, the indeterminate short-circuit, missing-Greeks rejection
  (never zero-filled), the two batch-level gates and their precedence over
  per-contract rejection reasons (a stale or feed-disallowed batch is never
  diluted by unrelated per-contract reasons), a default OPRA batch reaching
  `ELIGIBLE`, a default indicative batch never reaching `ELIGIBLE`,
  explicit research mode returning `RESEARCH_ONLY` and never `ELIGIBLE`,
  a `research_only` result never being usable as an operational eligible
  set, the CLI's `--allow-indicative-research` flag (including that it
  overrides the input file in both directions), sanitized errors,
  serialization/path safety (symlink refusal, no overwrite, bounded read),
  and offline import isolation. **No real selector run has been performed
  against the real local database or the one stored SPY option-chain
  batch, and no contract recommendation, usefulness, pricing-accuracy,
  execution, or profitability claim exists anywhere in this step.** See
  `PROJECT_STATE.md` (Completed Work Log items 45–46).

  **Synchronized selector-capture coordinator (feeds step e; 2026-09-17,
  Completed Work Log item 48), implemented and verified entirely offline
  against mocked/fake providers.** Building one real `ContractSelectorInput`
  requires composing three independent, already-reviewed, read-only Alpaca
  boundaries — bars, an underlying-price snapshot, and an option-chain
  snapshot — into one point-in-time-consistent record. That composition is a
  coordinator, not a pure evidence builder, so it lives in
  `market_intelligence/orchestration/spy_contract_capture.py`
  (`capture_contract_selector_input`), **never** in `market_features/`,
  which stays exactly as pure/offline as before — untouched by this
  addition and still fully covered by its own offline-import-boundary
  tests. The coordinator itself is dependency-injected (every client is
  passed in; it constructs none of its own) and is exercised only against
  fakes/mocks in `test_spy_contract_capture.py` — no test importing it ever
  makes a network call. `scripts/capture_spy_contract_selector_input.py` is
  the dry-run-first CLI: the default mode makes zero requests and prints
  only the fixed capture plan; `--execute` runs the sequence live;
  `--write` (only with `--execute`, only when the result is `resolved`)
  persists the captured input under gitignored `data/evaluations/local/`.

  *Capture sequence.* Exactly three live calls, in order, never more: (1)
  `AlpacaBarsClient.get_bars` — SPY 5-minute bars from the session open
  through "now" (`capture_start`, resolved once at the very start of the
  sequence); (2) `AlpacaMarketDataClient.get_snapshot` — one
  underlying-price snapshot, whose *market-data* timestamp (the provider's
  own `latestTrade.t` / `latestQuote.t`, never request/wall-clock time) is
  carried as its provenance time; (3) `AlpacaOptionsChainClient
  .get_chain_snapshot` — made **only** when the regime and scenario horizon
  from step c's engine both resolve, never for an `indeterminate` result.
  **No single wall-clock timestamp governs this entire sequence:** the
  underlying price's own recency is validated against a second, later
  instant, `price_validation_time`, resolved immediately after the snapshot
  request returns — never against `capture_start` — so a live price request
  that takes a moment to complete is judged against when it was actually
  validated, not when the whole capture began.

  *10:00 ET earliest start.* Capture only runs from `10:00` through `16:00`
  America/New_York, Monday–Friday. `10:00` is not an arbitrary round number:
  it is the instant the sixth 09:30-grid 5-minute bar (09:55–10:00) actually
  completes — the minimum bar history the step-c regime engine's opening
  range and VWAP-slope features require. Outside that window, no client is
  called at all. A second, independent gate then drops any bar the bars
  client returns that would not yet be complete as of "now"
  (`bar.timestamp + 5min > now`) before it ever reaches the regime engine —
  defensive, and independent of trusting the provider's own request-boundary
  semantics; if fewer than 6 genuinely-completed bars remain after that,
  capture stops before any price or chain request.

  *Indeterminate early stop.* The underlying price is fetched **before**
  this gate (so a sanitized `INDETERMINATE` result still carries it), but if
  the regime or the scenario horizon resolves to `INDETERMINATE`, capture
  stops there — the option-chain request, the only one of the three calls
  with a real cost/rate-limit footprint, is never made.

  *Timestamp provenance fields and bounded lag rules.* `ContractSelectorInput`
  (and `ContractSelectorResult`) now carry two additional provenance
  timestamps beyond `as_of_timestamp`: `regime_as_of_timestamp` (the
  upstream regime engine's own last-bar completion time) and
  `underlying_price_timestamp` (the underlying price's own market-data
  timestamp). Together with `batch.retrieved_at` and `as_of_timestamp`,
  these four timestamps must satisfy, in order:

      regime_as_of_timestamp <= underlying_price_timestamp
          <= batch.retrieved_at <= as_of_timestamp

  Only the first two legs are hardened as a construction-time validator
  (`contract_selection.contracts._check_provenance_ordering_and_lag`), each
  bounded by its own provisional `SelectorConfig` threshold
  (`max_regime_to_price_gap_seconds`, default 300 seconds;
  `max_price_to_chain_gap_seconds`, default 60 seconds) — a
  `ContractSelectorInput` simply cannot be constructed if either ordering or
  either bound is violated. The third leg (`batch.retrieved_at` vs.
  `as_of_timestamp`) is deliberately left as the pre-existing, unchanged
  `selector.py` freshness gate (`SNAPSHOT_STALE` / `SNAPSHOT_FROM_FUTURE`,
  Completed Work Log item 47) — that gate already treats a stale or
  future-dated batch as a reportable selector outcome, not a caller error,
  and hardening it at construction time would make two already-tested
  selector outcomes unreachable. A separate, coordinator-only threshold,
  `max_quote_age_seconds` (default 300 seconds), governs how old the live
  underlying-price observation itself may be before the coordinator must
  treat it as unavailable — centralized in `SelectorConfig` with every
  other threshold, but never consumed by `selector.py`.

  *Complete moneyness-window request.* The option-chain request is sized
  from the selector configuration's own full moneyness band — strike
  bounds `floor(price * min_moneyness)` through `ceil(price * max_moneyness)`
  — never an arbitrary fixed-dollar window, and from the resolved scenario
  horizon's own existing `SelectorConfig.horizon_expiration_windows` DTE
  window — never a fallback window, and never computed for an
  `indeterminate` horizon. The request always asks for the option-chain
  connector's own already-reviewed `MAX_PAGES` / `MAX_TOTAL_CONTRACTS`
  ceilings — never a smaller, arbitrary one, and never a larger one.

  *Typed truncation failure.* `AlpacaOptionsChainClient.get_chain_snapshot`
  now raises a public, typed `AlpacaOptionsChainTruncatedError` (a subclass
  of the existing `AlpacaOptionsChainError`, carrying a fixed
  `OptionChainTruncationReason` — `MAX_PAGES_EXCEEDED` or
  `MAX_TOTAL_CONTRACTS_EXCEEDED`) when a bounded retrieval would need more
  pages or contracts than requested, in place of the previously generic
  `AlpacaOptionsChainError` at those two ceilings. The coordinator catches
  this specific type — never matching on exception text — and reports
  `CaptureStatus.CHAIN_TRUNCATED`, structurally distinct from any other
  chain failure (`CaptureStatus.CHAIN_UNAVAILABLE`), so a silently partial
  eligible set can never reach the selector and an unrelated chain failure
  can never be misreported as truncation.

  *Indicative feed and the `RESEARCH_ONLY` boundary.* The coordinator always
  requests the `indicative` feed (Alpaca does not offer live OPRA data on
  the plan this project uses) — it never requests or fabricates an `opra`
  feed. Because of the feed-safety boundary hardened in item 46 above, a
  `ContractSelectorInput` the coordinator produces can therefore never reach
  `SelectorStatus.ELIGIBLE` when later run through
  `scripts/select_spy_option_contracts.py`: without the CLI's own explicit
  `--allow-indicative-research` flag it is rejected in full at the feed gate
  (`no_eligible_contracts`); with the flag it can only ever reach
  `RESEARCH_ONLY` (or `NO_ELIGIBLE_CONTRACTS` / `INDETERMINATE`). The
  coordinator itself never calls the selector — it only ever produces the
  input for that later, separate, explicit invocation.

  **Not done.** No synchronized live capture has been run, and no real
  selector run has been performed against a live-captured input or the one
  stored SPY option-chain batch. No filter or lag threshold was tuned. No
  contract recommendation, usefulness, pricing-accuracy, execution, or
  profitability claim is made anywhere in this addition. See
  `PROJECT_STATE.md` (Completed Work Log item 48).
- **f.** Add the Options Strategy Agent (bounded output above), consuming
  only validated structured inputs and the eligible set. **Not started —
  requires its own separate authorization; does not begin automatically
  from step e's completion.**
- **g.** Run the shadow evaluation (30–50 sessions) and record the metrics
  above.
- **h.** Consider alerts / a dashboard **only after** the recorded evidence
  shows the workflow is useful.

## Practical constraints

- **No new framework infrastructure unless a concrete step above needs it.**
  Extend the existing agents, builders, connectors, storage, and evaluation
  harness rather than building a general orchestration or agent framework.
- **Favor an end-to-end usable SPY workflow** over breadth — one path that
  runs start to finish beats many partial capabilities.
- **Manual trading decisions are preserved** — the workflow informs; the
  user decides and executes.
- **Keep implemented / planned / validated status clearly separated**
  everywhere, as PROJECT_STATE.md does. As of 2026-09-14, step b's read-only
  connector and storage exist, migration `0009` is applied to the real
  database, and one live ingestion plus one read-only structural audit have
  succeeded (see "Implementation order" above) — but this establishes
  internal structural consistency only, not pricing accuracy, timeliness,
  usefulness, predictive edge, strategy validity, or profitability. As of
  2026-09-15, step c's deterministic intraday feature/regime engine also
  exists, implemented and tested offline with synthetic fixtures only — no
  real SPY session has been classified, every classifier threshold is a
  provisional hypothesis, and no predictive accuracy or mean-reversion edge
  has been established. Also as of 2026-09-15, step d's offline
  VWAP-extension/reversion evaluation tooling has been implemented and
  verified with synthetic fixtures, and — same day — run once, read-only,
  against the stored SPY bars: **step d is now complete.** That one run
  covered 5 gapless regular sessions / 390 candidate decision points;
  observation-level sample-size thresholds were met for three of four
  horizons on both extension sides, and session-level thresholds (which
  need 20 sessions) were met for none. No threshold was tuned (the
  evaluator always used step c's existing, unmodified thresholds), and this
  single small-sample run establishes no edge, accuracy, usefulness,
  strategy validity, or profitability — it must not be read as evidence for
  or against the VWAP-reversion hypothesis. **Step e, the deterministic
  Contract Selector, is also now done (2026-09-16) — implemented and tested
  entirely offline with synthetic fixtures, running without any AI model and
  before any future strategy agent.** Three batch-level gates (feed
  governance, then two freshness checks — future-dated, then stale) run
  before nine per-contract filters
  (expiration/DTE, option type when a directional side is explicitly
  supplied, strike/moneyness, delta range, required implied volatility and
  Greeks, positive bid/ask, non-crossed quote, maximum absolute/percentage
  spread, minimum quote size where available), producing eligible/
  research-only contracts in deterministic order, bounded rejection counts
  by a fixed reason enum, and one of four statuses (`eligible` /
  `no_eligible_contracts` / `research_only` / `indeterminate`) — no
  recommendation, ranking, score, prediction, or trade action. **Operational
  eligibility defaults to OPRA only (hardened same-day, Completed Work Log
  item 46): an indicative-feed batch can never reach `ELIGIBLE`; explicit
  opt-in (`allow_indicative_for_research=True`) instead yields
  `RESEARCH_ONLY`, with any passing contracts kept in a separate
  `research_only_contracts` field, enforced at the schema level so a
  `research_only` result can never satisfy the future strategy agent's
  eligible-set contract; and the CLI requires its own explicit
  `--allow-indicative-research` flag, which always overrides the input
  file.** Open interest remains unavailable and is never invented or
  replaced with volume; a contract missing required IV or Greeks is
  rejected, never zero-filled. **No real selector run has been performed
  against the real local database or the one stored SPY option-chain
  batch, and every filter threshold is a provisional hypothesis, not tuned
  against the step-d VWAP-reversion evaluation or the single stored option
  batch.** **A synchronized selector-capture coordinator was added
  2026-09-17 (Completed Work Log item 48)** —
  `market_intelligence/orchestration/spy_contract_capture.py` and
  `scripts/capture_spy_contract_selector_input.py` — composing the bars,
  underlying-price, and option-chain boundaries into one point-in-time-safe
  `ContractSelectorInput`, implemented and verified entirely offline against
  mocked/fake providers; `market_features/` remains untouched and fully
  pure/offline. **No synchronized live capture and no real selector run has
  occurred.** **Step f, the Options Strategy Agent, has NOT begun and requires
  its own separate authorization** — it does not begin automatically from
  step e's completion, and neither the feed-safety fix nor the capture
  coordinator authorizes starting it. Every other item in this
  document (steps f–h) is *planned* —
  not implemented — and **nothing is validated.**
