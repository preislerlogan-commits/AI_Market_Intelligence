# AI Market Intelligence

## Purpose

AI Market Intelligence is a decision-support system for equity and options
research. It combines AI-assisted research (ChatGPT/Codex, Claude) with
deterministic Python analysis to help a human trader form better-informed,
evidence-backed views on market direction and options positioning. It does
**not** generate guaranteed predictions, does not provide financial advice,
and does not execute trades.

## Product Vision

The intended finished product is a real-time, SPY-focused
market-intelligence copilot. It combines:
- pre-market preparation and live intraday monitoring;
- market-state classification;
- macro, news, and price evidence;
- separately researched reversion and trend-continuation setups;
- ranked setups with supporting and contradicting evidence;
- option contracts suggested for manual review;
- conversational explanations;
- selective SMS alerts;
- a dashboard as the main interface.

**Final trading decisions are always manual.** VWAP reversion is one
researched module, not the whole product. See
[docs/PRODUCT_VISION.md](docs/PRODUCT_VISION.md) and
[docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md). Neither authorizes any
implementation; [PROJECT_STATE.md](PROJECT_STATE.md) records what is built
and authorized.

**Evidence Envelope (reviewed design; offline core implemented).** The
shared evidence contract every producer would emit is designed in
[docs/EVIDENCE_ENVELOPE_DESIGN.md](docs/EVIDENCE_ENVELOPE_DESIGN.md), with
its producer registry in [docs/EVIDENCE_REGISTRY.md](docs/EVIDENCE_REGISTRY.md)
and consumer rules in
[docs/EVIDENCE_CONSUMER_RULES.md](docs/EVIDENCE_CONSUMER_RULES.md). Its
offline core lives in `market_intelligence/evidence/`. An offline storage
and registry-loading core (`market_intelligence/evidence_store/`, migration
`0010`) is reviewed and implemented. It is tested only on temporary databases,
and `0010` is not applied to the real database, which stays at `0009` until
separately authorized with a verified backup. No producer adapter, real
evidence store, registry activation, setup definition, or consumer exists yet.

**Dashboard (reviewed design; offline synthetic prototype only).** The
dashboard's information architecture is a reviewed design in
[docs/DASHBOARD_INFORMATION_ARCHITECTURE.md](docs/DASHBOARD_INFORMATION_ARCHITECTURE.md),
and the setup-card contract a reviewed design in
[docs/SETUP_CARD_CONTRACT.md](docs/SETUP_CARD_CONTRACT.md). An offline
setup-card core is implemented in `market_intelligence/setup_cards/`, but
the production setup-definition registry is empty, so no live card can be
produced.

A local, read-only **synthetic prototype** of the dashboard exists in
`market_intelligence/dashboard/` (reviewed and implemented through its
merge). It renders deterministic synthetic fixtures only and is connected to
no data:

- no real evidence, database, provider, registry activation, model or
  network;
- no persistence;
- the assistant is a disabled placeholder;
- no order, quantity, sizing, brokerage or execution control.

A dashboard connected to real evidence, every other card consumer, live
setup qualification, ranking, scenarios, notifications, SMS,
machine-decision mode and trading remain unauthorized. To view the prototype locally:

```bash
pip install -e ".[dashboard]"   # or ".[dev]", which also includes Streamlit
streamlit run market_intelligence/dashboard/app.py
```

`.streamlit/config.toml` binds the app to `127.0.0.1` and turns off
Streamlit's usage statistics.

## Architecture

**Implemented:**

- **Data connectors** (`market_intelligence/data_connectors/`) — read-only
  Alpaca (market-data snapshot, historical bars, news, and — code/tests only,
  never run live — a bounded SPY option-chain snapshot connector) and FRED
  (series observations, series metadata). No order, account, position,
  exercise, or execution method exists anywhere in this layer.
- **Storage** (`market_intelligence/storage/`, `data/`) — local DuckDB with a
  versioned, checksum-verified, transactional migration runner and per-dataset
  repositories. The real local database is at schema version `0009` (migration
  `0009`, `option_chain_snapshot_batches` + `option_chain_snapshots` +
  `option_chain_snapshot_batch_items`, is applied). No Parquet layer is in
  use.
- **Market features** (`market_intelligence/market_features/`) — deterministic,
  read-only snapshot builders (market context, session quality, news evidence,
  macro evidence) computed only from already-stored data.
- **Model-client boundary** (`market_intelligence/model_clients/`) — a single
  narrow OpenAI structured-output client: one bounded request at a time, no
  tools, no automatic retry, no server-side conversation persistence.
- **Agents** (`market_intelligence/agents/`) — three bounded, single-turn,
  non-directional analysis agents (Market Evidence, News, Macro Analyst), each
  behind a deterministic preflight gate and deterministic post-response
  validators. None predicts direction, recommends a trade, or discusses
  options.
- **Ingestion orchestration** (`market_intelligence/orchestration/`) — a
  manual, dry-run-first CLI that runs the reviewed ingestion jobs through
  explicit, strictly validated contracts, with per-job failure isolation and a
  persistent audit trail. Not a scheduler.

- **Evidence Envelope core** (`market_intelligence/evidence/`) — pure,
  offline contracts, canonical IDs, validators, freshness evaluation,
  point-in-time bundle construction, the permanent Contract Selector
  boundary, and the SPY holdout guard. It opens no database and makes no
  request. Nothing emits or consumes it yet: producer adapters, storage, a
  registry file, and every consumer remain unbuilt and unauthorized.
- **Setup-card core** (`market_intelligence/setup_cards/`) — pure, offline
  `setup-card-1` contracts, identity, bundle-bound validation, supersession
  and a builder over in-memory evidence. The production setup-definition
  registry is empty, so no live card of either kind can be produced. Only
  the synthetic dashboard prototype renders cards, from synthetic fixtures.
- **Dashboard prototype** (`market_intelligence/dashboard/`; offline,
  synthetic; reviewed and implemented) — a Streamlit shell for the six reviewed
  pages, plus a disabled assistant placeholder. It is built from committed
  synthetic fixtures. A fail-closed presentation adapter validates every
  Evidence Bundle and setup card before anything renders. Rendering is
  kept separate from fixtures, view derivation, trust labels, navigation
  and validation.
- **Evidence store core** (`market_intelligence/evidence_store/`, migration
  `0010`; reviewed and implemented offline) — insert-only storage of
  evidence, conflicts, bundles and setup cards with a hash-chained commit
  log, point-in-time reconstruction, strict registry-file loading,
  registration and activation services, an HMAC-authenticated anti-rollback
  checkpoint, recovery and integrity verification. It refuses the real
  database path and is exercised only on temporary databases. Because `0010`
  exists while the real database is at `0009`, the real database
  intentionally fails the current-schema health check until applying `0010`
  is separately authorized, with a verified backup first.

**Planned — directory placeholder only, no code yet:**

- **News pipeline** (`market_intelligence/news_pipeline/`) — future news
  ingestion/processing, subject to [SOURCE_POLICY.md](SOURCE_POLICY.md).
- **Prompts** (`market_intelligence/prompts/`) — future versioned prompt
  templates (agent instructions currently live as inline module constants).
- **Forecasts** (`market_intelligence/forecasts/`) — future forecast records
  per [DECISION_RULES.md](DECISION_RULES.md).
- **Trade journal** (`market_intelligence/trade_journal/`) — future record of
  manually executed trades and their outcomes.

Data is stored locally in DuckDB. No cloud database or hosted service is part
of the current design.

## Roadmap

Phase 0 (Infrastructure Foundation) is **closed as of 2026-08-28**. Its final
criterion (P0-7) was met when the first real, human-reviewed offline Macro
Analyst characterization was completed and recorded (14 claim/citation pairs,
14 human adjudications, rubric complete, all pairs `partially_supported`; see
[docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md)). Closure means the required
infrastructure and the required agent-evaluation methodology exist and have each
been exercised and recorded — it is **not** a claim that any agent is
validated, universally accurate, repeatable, or profitable, and only the Macro
Analyst has been characterized so far.

**Phase 1 (design recorded, in progress): an automated, evidence-based SPY
options decision-support workflow** — deterministic evidence gathering and
intraday regime classification, deterministic option-contract eligibility
filtering, and then a bounded strategy agent that consumes only the
validated structured outputs of the upstream stages plus the deterministic
selector's eligible contract set and can return `no_trade`, followed by a
recorded evaluation before any claim of usefulness. With Phase 0 closed, the
first implementation step — **read-only SPY option-chain snapshot ingestion
and local storage** — is done, including a live run: a bounded
`AlpacaOptionsChainClient` (expiration span ≤ 60 days, strike width ≤ $500,
≤ 10 pages, ≤ 5,000 contracts), migration `0009`
(`option_chain_snapshot_batches` + `option_chain_snapshots` +
`option_chain_snapshot_batch_items`), a repository, and a dry-run-first
ingestion script. **Migration `0009` is applied to the real database, and one
authorized live, `indicative`-feed ingestion has succeeded** (SPY, one
expiration, strikes 740–790, 102 contracts received/inserted), followed by a
read-only structural audit finding zero integrity or malformed-data issues —
see [PROJECT_STATE.md](PROJECT_STATE.md) and
[DATA_CATALOG.md](DATA_CATALOG.md) for full sanitized detail and binding
caveats (structural consistency only — not pricing accuracy, usefulness, or
profitability). No regime engine, contract selector, or Options Strategy
Agent exists; the next planned step is the deterministic SPY intraday
feature/regime engine. Design:
[docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md).
Manual-only execution is unchanged. The three existing analysis agents
(Market Evidence, News, Macro) stay non-directional; the future Options
Strategy Agent is a separately bounded directional decision-support agent.

## Setup Status

The Python environment, runtime/dev dependencies, and settings layer
(`market_intelligence/config/settings.py`) are in place. Beyond that:

- **Read-only data pipelines exist**, each with at least one authorized live
  ingestion into local storage: Alpaca historical bars, Alpaca news, FRED
  series observations, and FRED series metadata.
- **The local DuckDB database is initialized at schema version `0009`.**
  Migration `0009` (`option_chain_snapshot_batches` + `option_chain_snapshots`
  + `option_chain_snapshot_batch_items`) is applied to the real database, and
  one authorized live SPY option-chain ingestion (102 contracts, `indicative`
  feed) has been stored through it — see [DATA_CATALOG.md](DATA_CATALOG.md).
- **A deterministic, manually invoked ingestion path exists.** The
  dry-run-first CLI (`scripts/run_ingestion_pipeline.py`) runs the three
  reviewed jobs through explicit contracts, with per-job failure isolation, a
  fail-closed overlap lock, and a persistent audit trail; it has completed
  one authorized `--execute` run. Scheduling, unattended operation, automatic
  stale-lock recovery, and freshness monitoring are deferred to a later
  operational phase and do not exist. The current path is not production-ready,
  continuously reliable, or fully validated.
- **A single OpenAI structured-output provider boundary is
  live-connectivity-verified.**
- **Three bounded, non-directional analysis agents** (Market Evidence, News,
  Macro Analyst) exist. Each has produced exactly one accepted live run. That
  proves bounded execution works once; it does not establish factual accuracy,
  repeatability, or predictive value.
- **A repeatable agent-evaluation methodology** (deterministic
  factual-transcription harness + human citation-support rubric) exists and has
  been exercised once on real output: the first offline Macro Analyst
  characterization (2026-08-28) — 14 claim/citation pairs, 14 human
  adjudications, rubric complete, all pairs `partially_supported`. This is not
  agent validation; only the Macro Analyst has been characterized. The real
  artifacts are kept local and gitignored.

None of the following exists: a predictive/forecasting model, a
forecast-recording system, an agent orchestrator or combined market-intelligence
brief, a dashboard, a trade journal, an intraday regime engine, a
deterministic contract selector, an Options Strategy Agent, a scheduler, or
any brokerage-execution integration. (The SPY option-chain connector and
storage now exist and hold one live-ingested batch — see "Roadmap" above —
but no selector, agent, or execution consumes it.)

See [PROJECT_STATE.md](PROJECT_STATE.md) for the authoritative, up-to-date
status and [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md) for the Phase 0 exit
criteria.

## Project Independence

This is an independent project. It does not depend on, read from, or access
the separate ORB_Project — or any other data or files outside this repository
— unless the user explicitly authorizes a specific source. That prohibition is
unchanged.

Initial project-owned datasets have been ingested through this repository's
own reviewed connectors: SPY 5-minute IEX bars, SPY news, and seven FRED macro
series (observations and metadata). Each is one bounded ingestion run with
limited coverage — not a complete, gap-free, or research-validated dataset.
See [DATA_CATALOG.md](DATA_CATALOG.md).

## Manual-Execution Safety Boundary

**This system never executes trades.** All trade execution is performed
manually by the user through Robinhood. No component of this repository is
permitted to hold brokerage credentials, place orders, or otherwise connect
to any brokerage execution API. See [DECISION_RULES.md](DECISION_RULES.md)
for the full set of decision-support boundaries.

## Not Financial Advice

This system is a research and decision-support tool. Its outputs — forecasts,
probabilities, feature analyses, and AI-generated commentary — are informational
only, are not guaranteed to be accurate, and do not constitute financial
advice. All trading decisions and their consequences are solely the
responsibility of the user.
