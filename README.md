# AI Market Intelligence

## Purpose

AI Market Intelligence is a decision-support system for equity and options
research. It combines AI-assisted research (ChatGPT/Codex, Claude) with
deterministic Python analysis to help a human trader form better-informed,
evidence-backed views on market direction and options positioning. It does
**not** generate guaranteed predictions, does not provide financial advice,
and does not execute trades.

## Architecture

**Implemented:**

- **Data connectors** (`market_intelligence/data_connectors/`) — read-only
  Alpaca (market-data snapshot, historical bars, news) and FRED (series
  observations, series metadata). No order, account, or execution method
  exists anywhere in this layer.
- **Storage** (`market_intelligence/storage/`, `data/`) — local DuckDB with a
  versioned, checksum-verified, transactional migration runner and per-dataset
  repositories. Currently at schema version `0008`. No Parquet layer is in use.
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

**Planned — directory placeholder only, no code yet:**

- **News pipeline** (`market_intelligence/news_pipeline/`) — future news
  ingestion/processing, subject to [SOURCE_POLICY.md](SOURCE_POLICY.md).
- **Prompts** (`market_intelligence/prompts/`) — future versioned prompt
  templates (agent instructions currently live as inline module constants).
- **Forecasts** (`market_intelligence/forecasts/`) — future forecast records
  per [DECISION_RULES.md](DECISION_RULES.md).
- **Trade journal** (`market_intelligence/trade_journal/`) — future record of
  manually executed trades and their outcomes.
- **Dashboard** (`market_intelligence/dashboard/`) — a future Streamlit
  application.

Data is stored locally in DuckDB. No cloud database or hosted service is part
of the current design.

## Roadmap

Phase 0 (Infrastructure Foundation) is **not closed**; its one remaining
requirement is a recorded first real, human-reviewed agent characterization
(see [docs/PHASE_0_EXIT.md](docs/PHASE_0_EXIT.md)).

**Phase 1 (design recorded, not started): an automated, evidence-based SPY
options decision-support workflow** — deterministic evidence gathering and
intraday regime classification, a bounded strategy agent that consumes only
validated structured inputs and can return `no_trade`, deterministic
option-contract eligibility filtering, and a recorded evaluation before any
claim of usefulness. No options code, connector, or agent exists yet, and no
options work begins until Phase 0 is closed. Design:
[docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md).
Manual-only execution and the non-directional boundary are unchanged.

## Setup Status

The Python environment, runtime/dev dependencies, and settings layer
(`market_intelligence/config/settings.py`) are in place. Beyond that:

- **Read-only data pipelines exist**, each with at least one authorized live
  ingestion into local storage: Alpaca historical bars, Alpaca news, FRED
  series observations, and FRED series metadata.
- **The local DuckDB database is initialized at schema version `0008`.**
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

None of the following exists: a predictive/forecasting model, a
forecast-recording system, an agent orchestrator or combined market-intelligence
brief, a dashboard, a trade journal, an options-data pipeline, a scheduler, or
any brokerage-execution integration.

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
