# Options Decision Workflow — Phase 1 Roadmap

**Status: design, plus the first implementation step (step b) partially
built offline.** As of 2026-08-28, step b's **read-only SPY option-chain
snapshot connector and local DuckDB storage** exist in code and tests only —
mocked HTTP transports and temporary databases, **no live option-chain
request has been made, no real option data has been stored, and migration
`0009` has not been applied to the real local database.** Everything else in
this document — the intraday regime engine, the deterministic contract
selector, the Options Strategy Agent, and every evaluation stage — remains
**design only: no code, schema, connector, or agent exists, and nothing is
validated.** This document records the intended shape of Phase 1 so the
design is agreed before implementation begins. It is subordinate to
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
**Phase 1 may begin; its first implementation step is step b below (read-only
SPY option-chain ingestion and local storage)**.

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
feature set. At minimum:

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
    review): `AlpacaOptionsChainClient`
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
    two-table design: `option_chain_snapshot_batches` records exactly one
    row per successfully stored chain retrieval — **including a retrieval
    that returned zero contracts** (run-level provenance: requested feed,
    requested expiration/strike window, requested option type, retrieval
    instant, contract count with zero allowed) — and `option_chain_snapshots`
    stores one row per normalized contract, referencing its batch by
    ingestion-run id without duplicating request-level fields.
    `OptionChainSnapshotRepository` and the dry-run-first
    `scripts/ingest_alpaca_options_chain.py` store both transactionally (the
    batch row, every snapshot row, and the ingestion-run status update all in
    one transaction), keying `option_chain_snapshots` on
    `(provider, underlying, feed, contract_symbol, retrieved_at)` so OPRA
    and indicative observations are never merged, and keying
    `option_chain_snapshot_batches` on `ingestion_run_id` (always an insert).
  - **Not done:** no live option-chain request has occurred, no real option
    data has been stored, and migration `0009` has not been applied to the
    real local database. `indicative`-feed data may be delayed or modified
    by the provider and must never be described as live OPRA data.
    **Open interest is not available** from this endpoint and is not stored,
    inferred, or defaulted on either table; a later milestone would need a
    different source for it.
- **c.** Build the deterministic SPY intraday feature/regime engine (the
  feature set and classifier above). No model.
- **d.** Test the VWAP-extension / reversion hypothesis on the underlying
  using the forward-outcome features — record the result whether positive,
  null, or negative.
- **e.** Build the Deterministic Contract Selector (eligible-set filtering).
- **f.** Add the Options Strategy Agent (bounded output above), consuming
  only validated structured inputs and the eligible set.
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
  everywhere, as PROJECT_STATE.md does. As of now, only step b's read-only
  connector and storage exist, and only in code and tests (no live request,
  no stored data, migration `0009` not applied). Every other item in this
  document is *planned* — not implemented — and **nothing is validated.**
