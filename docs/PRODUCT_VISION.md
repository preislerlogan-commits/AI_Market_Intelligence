# Product Vision — AI Market Intelligence

**Status: permanent product north star.** This document describes the
intended finished product. It authorizes no implementation.

- **Nothing is authorized by this vision.** Every capability below needs its
  own separately reviewed design and authorization.
- **Governing documents win.** [DECISION_RULES.md](../DECISION_RULES.md),
  [PROJECT_STATE.md](../PROJECT_STATE.md), frozen research protocols (such
  as [SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md](SPY_VWAP_REVERSION_SHADOW_PROTOCOL.md)),
  and recorded authorization boundaries always govern. Where this vision
  appears to conflict with them, they win.
- **Current status.** For what is built today, see `PROJECT_STATE.md`.

## 1. Mission

Build a real-time, SPY-focused market-intelligence copilot. It combines
market behavior, macro conditions, news, and historical research in order
to:

- classify the market environment;
- identify qualified setups;
- rank the evidence, and the option contracts for manual review; and
- explain every conclusion, through a dashboard, a conversational
  assistant, and selective SMS alerts.

**The user makes every final trading decision manually.**

This is **not** a single-strategy VWAP bot. VWAP reversion is one researched
evidence module within a broader copilot.

## 2. Core product questions

The system continually answers:

1. What market environment are we in?
2. What evidence supports, and what contradicts, each active scenario?
3. Is the market favoring reversion, trend continuation, or neither?
4. Has a setup actually qualified under its researched, frozen criteria?
5. Which option contracts satisfy the reviewed deterministic filters?
6. What would invalidate the current interpretation?
7. Is any required evidence stale, missing, or conflicting?

## 3. Product interfaces

### Dashboard (the main operating interface)

- a pre-market briefing
- a live market-state display
- macro, news, and market-evidence panels
- scenario and setup cards, in qualified, developing, or invalidated states
  (§7)
- contract candidates, from the deterministic selector only
- data freshness and availability
- shadow-research status
- an audit trail

### Conversational assistant

- **What it answers:** questions about market sentiment, market state,
  directional scenarios, and macro evidence.
- **How it explains:** why evidence supports or contradicts a scenario, and
  why setups and contracts rank where they do.
- **Kinds of statement.** It always distinguishes **confirmed fact**,
  **deterministic calculation**, **historical research finding**, and
  **current inference**.
- **Honesty.** It states uncertainty and missing evidence, and cites the
  structured evidence it used.
- **It never invents** unavailable facts, probabilities, or research results
  (DECISION_RULES.md, "No fabricated probabilities").
- **Directional questions.** It may describe directional *scenarios* and
  their evidence. It never presents a guaranteed direction.
- **Forecasts.** Any output framed as a forecast must meet
  DECISION_RULES.md "Forecast Requirements".
- **Directional components are separate.** The existing analysis agents are
  non-directional by design. Directional reasoning belongs only to
  separately authorized, bounded components.

### SMS (selective alerts only)

Alerts fire only for:
- a setup that has qualified;
- a meaningful regime change;
- a material catalyst change;
- an invalidation;
- stale or unavailable critical data.

Every alert directs the user back to the dashboard. There is no alert spam,
and no recommendation-style alert until it is separately authorized.

## 4. Behavioral lanes

| Lane | Meaning |
|---|---|
| **VWAP / reversion** | Researched, preregistered reversion behavior. Today this is the SPY VWAP-extension module: confirmed on the underlying, with shadow research pending |
| **Trend continuation** | Strong trend-day conditions. **Not yet researched**, so no trend signal exists |
| **Mixed / no qualified setup** | Neither lane qualifies. This is a valid and important output |

- **Lanes don't substitute for each other.** Failure of a reversion setup
  does **not** create a trend setup, and failure of a trend setup does
  **not** create a reversion setup.
- **Each lane needs its own frozen criteria.** Each lane requires its own
  researched and frozen criteria. A future trend detector must go through
  its own discovery, preregistration, confirmation, and shadow process.
- **"No qualified setup" is a result, not a failure.**

## 5. Evidence architecture

| Producer | Kind |
|---|---|
| Market Evidence Agent | bounded model agent over stored market evidence (non-directional) |
| News Agent (News Analyst) | bounded model agent over stored news (non-directional) |
| Macro Agent (Macro Analyst) | bounded model agent over stored macro data (non-directional) |
| Regime Engine | deterministic |
| VWAP Reversion Research Engine | deterministic evaluator plus frozen research protocols |
| Trend-Day Research Engine | **future**; requires its own research program |
| Contract Selector | deterministic eligibility filters |
| Explanation / Conversation Layer | consumes structured outputs; **never silently replaces** a deterministic calculation |

Every conclusion labels each input as exactly one of:
1. confirmed fact;
2. deterministic calculation;
3. historical research result, cited to its recorded result;
4. current inference;
5. missing or stale evidence.

## 6. Decision boundary

The system may **eventually** do the following, each only when separately
authorized:
- present evidence
- rank setups
- explain its reasoning
- identify invalidations
- suggest contracts **for manual review**
- send selective alerts

It is **not** authorized to:
- trade autonomously
- route orders
- manage positions
- make guaranteed directional claims
- hide uncertainty
- run the Options Strategy Agent under the current project authorization

**Manual final decision authority remains with the user.** All the
existing DECISION_RULES.md boundaries remain in force: manual execution
only, no Robinhood credentials, and deterministic contract eligibility
before any AI ranking.

## 7. Setup-card standard

Every setup card contains:
- **Identity and time:** symbol and timestamp.
- **Classification:** the setup lane (§4); the lifecycle state
  (`developing` / `qualified` / `invalidated`, or `no_qualified_setup`); the
  research horizon; and the market regime.
- **Evidence:**
  - supporting evidence and contradicting evidence, each labeled by kind
    (§5);
  - historical research context, cited, with its caveats;
  - the current inference, labeled as inference;
  - invalidation conditions;
  - data freshness and any missing evidence.
- **Ranking:** a setup rank, together with the reason for that rank.
- **Contracts:** eligible, or research-only, contract candidates from the
  deterministic selector. Research-only candidates are never presented as
  eligible.
- **Caveats:** the fixed caveats of the underlying research. For example,
  the VWAP confirmation's `underlying_setup_only_no_options_or_pnl`.
- **Label:** a **manual-decision label** on every card.

## 8. User preference record

Recorded from the user (2026-09-28):

- **Highest-value times:** pre-market preparation, and live intraday
  monitoring.
- **Desired outputs:** evidence presentation; ranked setups with reasoning;
  option contracts suggested for manual review.
- **Interfaces:** a trading dashboard, a conversational assistant, and
  phone/SMS alerts.
- **Assistant topics:** market sentiment, directional scenarios, macro
  evidence, and why a setup is or isn't supported.
- **Scanner scope:** researched VWAP-reversion opportunities and,
  separately, supported strong trend-day conditions.
- **VWAP is an important module, not the entire product.**

## 9. Non-goals

- a single-strategy VWAP bot
- constant trade generation
- treating every market day as directional
- converting weak evidence into confident recommendations
- automated execution
- retrospective tuning disguised as live validation
