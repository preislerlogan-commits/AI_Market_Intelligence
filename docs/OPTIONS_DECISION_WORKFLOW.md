# Options Decision Workflow — Phase 1 Roadmap

**Status: design only. No code, schema, connector, or agent for any part of
this workflow exists yet. Nothing here is implemented, planned as in
progress, or validated.** This document records the intended shape of Phase 1
so the design is agreed before implementation begins. It is subordinate to
[PROJECT_STATE.md](../PROJECT_STATE.md) (authoritative status),
[DECISION_RULES.md](../DECISION_RULES.md) (binding boundaries), and
[CLAUDE.md](../CLAUDE.md) / [AGENTS.md](../AGENTS.md).

## Relationship to Phase 0

Phase 0 (Infrastructure Foundation) is **not closed**. Its single remaining
requirement is unchanged by this document:

- Complete and record **one real, human-reviewed offline agent
  characterization**, covering every claim, with findings, failures, and
  `unable_to_determine` results preserved (criterion P0-7 — see
  [docs/PHASE_0_EXIT.md](PHASE_0_EXIT.md) and
  [docs/AGENT_EVALUATION_HARNESS.md](AGENT_EVALUATION_HARNESS.md)).

**No options work begins until Phase 0 is closed.** This roadmap must not be
used to justify starting options implementation early, and Phase 0 closure
must not be delayed, rescoped, or blocked by anything below. The first
implementation step (below) is closing Phase 0.

## Phase 1 objective

Build an **automated, evidence-based SPY options decision-support
workflow** that is measurably more disciplined than asking a general chatbot
for a market prediction: deterministic evidence gathering, deterministic
regime classification, a bounded strategy agent that consumes only validated
structured inputs, deterministic contract eligibility filtering, and a
recorded evaluation before any claim of usefulness. SPY only. Decision
support only — every trade decision and its execution stay manual, exactly
as today.

## Architecture

Each stage produces a **validated, structured output**. A downstream stage
consumes only the validated output of upstream stages — never free text,
never raw model output, never provider payloads. Every agent stays
single-turn, no-tools, behind a deterministic preflight gate and
deterministic post-response validators, mirroring the three existing
non-directional agents.

| Stage | Type | Responsibility |
|---|---|---|
| **Market Evidence Agent** | agent (extends today's) | Price, volume, candlestick structure, VWAP and VWAP extension, opening range, realized/known volatility, and key prior-day/session levels — described factually, no direction. |
| **News Analyst** | agent (extends today's) | Catalysts present in stored news: relevance to SPY, timing relative to the session, affected symbols/sectors. No direction. |
| **Macro Analyst** | agent (today's, unchanged scope) | Economic and market-regime *context* from stored FRED observations and official metadata. Factual only; not a regime predictor. |
| **Intraday Regime/Setup Engine** | deterministic (no model) | Classifies the session into exactly one of: `trend_continuation`, `vwap_mean_reversion`, `range`, `event_driven`, `indeterminate` — from the structured features below, by fixed published rules. |
| **Options Strategy Agent** | agent | Consumes only the validated structured outputs of the four stages above. Emits a bounded strategy recommendation **or `no_trade`**. Never sees raw evidence, raw chains, or free text. |
| **Deterministic Contract Selector** | deterministic (no model) | Filters the option chain to an **eligible set** by expiration window, strike, delta, other Greeks, IV, liquidity/open interest, bid-ask spread, and scenario horizon. Runs before and independently of the agent. |

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

## Options Strategy Agent — bounded output

The agent's structured output is fixed-shape, strictly validated, and
bounded. It contains exactly:

- **Underlying** (`SPY`) and **timestamp** (UTC).
- **Scenario and horizon** — the classified regime and the forward time
  window the thesis applies to.
- **Directional or mean-reversion thesis** — a bounded statement of the
  expected behavior, tied to the regime; or none, for `no_trade`.
- **Entry condition, invalidation, and target** — explicit, checkable
  conditions (mirrors the forecast requirements in
  [DECISION_RULES.md](../DECISION_RULES.md)).
- **Strategy type** — from a fixed enum (e.g. long call / long put / debit
  vertical / credit vertical / … ) — no free-form structures.
- **Expiration window and target delta** — ranges, not a specific contract.
- **IV / Greek / liquidity considerations** — bounded notes the Contract
  Selector and the human can check.
- **Maximum acceptable bid-ask spread** — an explicit cap.
- **Up to three ranked candidate contracts** — chosen **only** from the
  Contract Selector's eligible set (see below).
- **Reasons for ranking** — why each candidate is ordered as it is.
- **Cancellation conditions** — what would void the recommendation before
  entry.
- **`no_trade`** — a first-class, fully valid outcome, not a failure. The
  agent must return `no_trade` when the regime is `indeterminate`, when no
  eligible contract exists, or when the evidence does not support a thesis.

## AI does not choose an unrestricted contract

- The **Deterministic Contract Selector** builds the eligible contract set
  from fixed safety and liquidity rules (expiration window, strike/delta
  bounds, Greek bounds, IV bounds, minimum liquidity/open interest, maximum
  bid-ask spread, scenario horizon). This runs without any model.
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
  characterization (P0-7).
- **b.** Add **read-only** option-chain ingestion and local storage (SPY
  only) — its own reviewed connector, sanitized, no execution surface,
  following the existing connector/storage patterns.
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
  everywhere, as PROJECT_STATE.md does. As of now, every item in this
  document is *planned* — none is implemented and none is validated.
