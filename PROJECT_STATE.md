# Project State

This document is the **authoritative source of truth** for the current status
of AI Market Intelligence. It must be read before beginning any work in this
repository, and updated whenever the project's status materially changes.

Last updated: 2026-08-20

## Current Phase

**Phase 0 — Infrastructure Foundation**

The project is in initial scaffolding. Python environment and dependency
configuration are in place; no functional data pipelines or integrations
exist yet.

## Status

- Repository initialized. Directory scaffold and governing documents
  (README, CLAUDE.md, AGENTS.md, DATA_CATALOG.md, SOURCE_POLICY.md,
  DECISION_RULES.md) are in place.
- A local Python 3.13 virtual environment (`.venv`) has been created.
  `pyproject.toml` defines the runtime dependency set (duckdb, pandas,
  pyarrow, httpx, pydantic, pydantic-settings, python-dotenv) and a `dev`
  optional-dependency group (pytest, pytest-cov, ruff). The project is
  installed into `.venv` in editable mode. See
  [docs/ENVIRONMENT_SETUP.md](docs/ENVIRONMENT_SETUP.md).
- A settings layer (`market_intelligence/config/settings.py`) has been
  added using pydantic-settings. It reads an optional local `.env` file,
  defines optional Alpaca/FRED/OpenAI/Anthropic credential fields protected
  with `SecretStr`, and exposes a `provider_status()` method that reports
  only booleans, never secret values. Settings can be instantiated without
  any credentials present.
- A read-only Alpaca market-data connector
  (`market_intelligence/data_connectors/alpaca_market_data.py`,
  `AlpacaMarketDataClient`) has been added, covering only Alpaca's
  market-data API (`https://data.alpaca.markets`) — no order, account, or
  execution functionality. It validates that both Alpaca credentials are
  configured before requesting, uses explicit timeouts, and returns only
  sanitized results (never headers, keys, secrets, or the raw response). A
  companion script, `scripts/check_alpaca_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  symbol, timestamp). On 2026-08-20 one live, read-only SPY snapshot
  connection check was run using local `.env` credentials and succeeded
  (2xx, market timestamp returned). FRED remains a planned provider with no
  credentials, client, or connection test yet. No bulk or historical market
  data has been pulled or stored, and no dataset has been validated — see
  `DATA_CATALOG.md`.
- No database initialized. DuckDB is included as a runtime dependency but
  no database file has been created and no schema exists yet.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- **This is an independent project.** It does not depend on, read from, or
  otherwise access the separate ORB_Project. No historical or live datasets
  are connected yet. The settings layer's only data-path configuration is
  `project_data_path`, which defaults to this repository's own `data/`
  directory. No code in this repository may access files outside the
  repository unless the user explicitly authorizes a specific source.

## Next Planned Work

1. Data connector design — an Alpaca read-only market-data connector now
   exists (see above); a FRED connector is still to be designed. Verified
   schema/provenance details belong in `DATA_CATALOG.md` once bulk data is
   actually pulled and inspected, not just a connectivity check.
2. Provider configuration — Alpaca credential handling is in place; FRED
   credential handling (via `.env`, never committed) is still planned.
3. First connection tests — done for Alpaca (read-only snapshot
   connectivity check, recorded in `DATA_CATALOG.md`/`PROJECT_STATE.md`);
   still planned for FRED.
4. Database initialization — local DuckDB/Parquet storage layout under
   `data/`, once a schema has been designed.

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
