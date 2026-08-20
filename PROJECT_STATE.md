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
- No live APIs connected. Alpaca and FRED are planned providers but no
  credentials, clients, or connection tests exist yet. The settings layer
  only reads locally supplied `.env` values; it makes no network calls.
- No database initialized. DuckDB is included as a runtime dependency but
  no database file has been created and no schema exists yet.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- Historical ORB data remains external at `C:\ORB_Project\data`. This data
  has **not** been copied, moved, or ingested into this repository. It must
  be treated as **read-only** until a deliberate, reviewed decision is made
  to reference or import it. The settings layer's default external-data
  path points at this location but does not read or write to it.

## Next Planned Work

1. Data catalog validation — inspecting actual schemas of external ORB data
   and recording verified findings in `DATA_CATALOG.md`.
2. Provider configuration — Alpaca and FRED credential handling (via
   `.env`, never committed).
3. First connection tests — minimal, read-only checks that provider
   credentials and connectivity work, with results recorded (including
   failures).
4. Database initialization — local DuckDB/Parquet storage layout under
   `data/`, once a schema has been designed.

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
