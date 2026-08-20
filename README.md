# AI Market Intelligence

## Purpose

AI Market Intelligence is a decision-support system for equity and options
research. It combines AI-assisted research (ChatGPT/Codex, Claude) with
deterministic Python analysis to help a human trader form better-informed,
evidence-backed views on market direction and options positioning. It does
**not** generate guaranteed predictions, does not provide financial advice,
and does not execute trades.

## Planned Architecture

- **Data connectors** (`market_intelligence/data_connectors/`) — integrations
  with market and macroeconomic data providers. Alpaca is the planned initial
  live market-data provider; FRED is the planned macroeconomic data provider.
- **News pipeline** (`market_intelligence/news_pipeline/`) — ingestion and
  processing of news and other qualitative inputs, subject to the sourcing
  rules in [SOURCE_POLICY.md](SOURCE_POLICY.md).
- **Market features** (`market_intelligence/market_features/`) — deterministic,
  reproducible feature computation from raw market data.
- **Prompts** (`market_intelligence/prompts/`) — versioned prompt templates
  used for AI-assisted research and forecast generation.
- **Forecasts** (`market_intelligence/forecasts/`) — generated forecasts and
  their supporting evidence, recorded per the rules in
  [DECISION_RULES.md](DECISION_RULES.md).
- **Trade journal** (`market_intelligence/trade_journal/`) — a record of
  manually executed trades and their outcomes, for evaluation and learning.
- **Dashboard** (`market_intelligence/dashboard/`) — a future Streamlit
  application for visualizing data, forecasts, and journal history.
- **Data storage** (`data/`) — local DuckDB/Parquet-based storage for raw,
  processed, and cached datasets.

Data is stored locally using DuckDB and Parquet. No cloud database or hosted
service is part of the current design.

## Setup Status

This repository has a Python environment, its runtime/dev dependencies, and
a settings layer (`market_intelligence/config/settings.py`) in place. No
APIs are connected, no databases are initialized, no data pipelines exist,
and no predictive models exist yet. See
[PROJECT_STATE.md](PROJECT_STATE.md) for the authoritative, up-to-date status
of the project.

## Project Independence

This is an independent project. It does not depend on or access the
separate ORB_Project. No historical or live datasets are connected yet.
Future data will come through this project's own reviewed connectors, and
no code in this repository may access files outside the repository unless
the user explicitly authorizes a specific source.

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
