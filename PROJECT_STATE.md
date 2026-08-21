# Project State

This document is the **authoritative source of truth** for the current status
of AI Market Intelligence. It must be read before beginning any work in this
repository, and updated whenever the project's status materially changes.

Last updated: 2026-08-20

## Current Phase

**Phase 0 — Infrastructure Foundation**

The project is in initial scaffolding. Python environment and dependency
configuration are in place. Read-only Alpaca market-data provider
connectivity has been verified (a single read-only snapshot request — see
Status below). Read-only FRED macroeconomic-data provider connectivity has
also been verified (a single read-only latest-observation request — see
Status below). Read-only Alpaca news provider connectivity has also been
verified (a single read-only SPY-news request — see Status below).
Connectivity is not the same as a validated data pipeline: no historical
or live dataset has been stored, cataloged, or validated yet. A local
DuckDB storage foundation now exists (infrastructure metadata only — see
Status below), but no forecasting or trading logic exists yet.

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
  sanitized results (never headers, keys, secrets, or the raw response). All
  symbols are normalized/validated (trimmed, uppercased, restricted to a
  conservative U.S. ticker character set) before any request is built, so
  invalid or malicious input never reaches the network; malformed or
  non-object JSON responses are also rejected with a sanitized error/status
  rather than surfaced raw. A companion script,
  `scripts/check_alpaca_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  symbol, timestamp). On 2026-08-20 one live, read-only SPY snapshot
  connection check was run using local `.env` credentials and succeeded
  (2xx, market timestamp returned). This confirms connectivity only; it is
  not the same as a validated data pipeline.
- A read-only FRED macroeconomic-data connector
  (`market_intelligence/data_connectors/fred_macro_data.py`,
  `FredMacroDataClient`) has been added, covering only FRED's official API
  (`https://api.stlouisfed.org`) — no methods beyond fetching published
  series observations. It validates that a FRED API key is configured
  before requesting, uses explicit timeouts, and returns only sanitized
  results (never the API key, request URL, query parameters, raw response,
  or observation value). All series IDs are normalized/validated (trimmed,
  uppercased, restricted to a conservative alphanumeric/underscore
  character set) before any request is built, so invalid or malicious
  input never reaches the network; malformed or non-object JSON responses,
  FRED-reported API error payloads, and missing/malformed observations are
  also rejected with a sanitized error/status rather than surfaced raw. A
  companion script, `scripts/check_fred_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  series ID, latest observation date — never the observation value). On
  2026-08-20 one live, read-only FEDFUNDS latest-observation connection
  check was run using local `.env` credentials and succeeded (2xx,
  observation date returned). This confirms connectivity only; it is not
  the same as a validated data pipeline. No historical or live dataset has
  been stored, cataloged, or validated yet — see `DATA_CATALOG.md`.
- A read-only Alpaca news connector
  (`market_intelligence/data_connectors/alpaca_news.py`,
  `AlpacaNewsClient`) has been added, covering only Alpaca's read-only
  data host (`https://data.alpaca.markets`) and only its news endpoint
  (`/v1beta1/news`) — no order, account, or execution functionality, and
  it does not write to DuckDB. It validates that Alpaca credentials are
  configured before requesting, uses explicit timeouts, and returns only
  sanitized results (never headers, keys, secrets, or the complete raw
  response). Symbols, result limits, sort direction, and optional
  start/end timestamps are all strictly normalized/validated before any
  request is built, so invalid or malicious input never reaches the
  network; malformed or non-object JSON responses, and individual articles
  missing required fields, are rejected/skipped with a sanitized
  error/status rather than surfaced raw, and duplicate articles (by
  provider article ID) within a single response are deduplicated. The
  normalized news-item model contains only provider-reported metadata
  (provider article ID, headline, source, URL, summary when available,
  publication/update timestamps when available, related symbols, a UTC
  retrieval timestamp kept distinct from publication time, and provider
  name) — no sentiment, impact, or direction is inferred. A companion
  script, `scripts/check_alpaca_news.py`, reports a sanitized connection
  status (configured, success, status category, requested symbol, article
  count, newest publication timestamp) and never prints headlines, URLs,
  summaries, or raw payloads. On 2026-08-20 one live, read-only SPY-news
  connection check was run using local `.env` credentials and succeeded
  (2xx, 10 articles, newest publication timestamp returned). This confirms
  connectivity only; it is not the same as a validated data pipeline. No
  news data has been stored, cataloged, or validated yet — see
  `DATA_CATALOG.md`.
- A local DuckDB storage foundation has been initialized
  (`market_intelligence/storage/`, `DuckDBManager` in
  `market_intelligence/storage/database.py`). It provides only a
  versioned, checksum-verified, transactional migration runner and the
  local database file — no provider data has been ingested or stored, and
  no forecasting/trading tables exist. The schema currently defines only
  two infrastructure-metadata tables: `schema_migrations` (tracks applied
  migrations and their checksums) and `ingestion_runs` (records
  provider/dataset/timing/status/record-count/sanitized-error-category/
  code-version/schema-version metadata for future ingestion runs — the
  table exists but nothing has written to it yet, since no ingestion code
  exists). Migration `0003` added a separate `schema_version` column to
  `ingestion_runs`, distinct from `code_version`. The database file
  defaults to `data/market_intelligence.duckdb` (inside this repository's
  own `data/` directory, per `Settings.project_data_path`) and is excluded
  from version control via `.gitignore`. `scripts/initialize_database.py`
  applies pending migrations and prints only the database path, schema
  version, and applied migration count; `scripts/check_database.py`
  performs a read-only health check that also verifies required columns,
  that the applied migration history matches the migration directory (no
  missing files, no checksum mismatches, no gaps/out-of-order versions),
  and that the database is at the latest available migration —
  `healthy` is false if any of these fail. On 2026-08-20 the local
  database was first initialized (schema version `0002`, 2 migrations
  applied), and on the same day was upgraded to schema version `0003` (1
  additional migration applied, 3 total) after migration `0003` was added;
  the health check reported healthy at `0003`. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md). No market
  bar, macro observation, news, forecast, or trade tables exist yet —
  those require separate, reviewed data contracts.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- **This is an independent project.** It does not depend on, read from, or
  otherwise access the separate ORB_Project. Read-only Alpaca provider
  connectivity has been verified, but no historical or live dataset has
  been stored, cataloged, or validated yet. The settings layer's only
  data-path configuration is `project_data_path`, which defaults to this
  repository's own `data/`
  directory. No code in this repository may access files outside the
  repository unless the user explicitly authorizes a specific source.

## Next Planned Work

1. Data connector design — read-only Alpaca market-data, Alpaca news, and
   FRED connectors now exist (see above). Verified schema/provenance
   details belong in `DATA_CATALOG.md` once bulk data is actually pulled
   and inspected, not just a connectivity check.
2. Provider configuration — Alpaca and FRED credential handling (via
   `.env`, never committed) is in place.
3. First connection tests — done for Alpaca market data (read-only
   snapshot connectivity check), Alpaca news (read-only SPY-news
   connectivity check), and FRED (read-only latest-observation
   connectivity check), recorded in `DATA_CATALOG.md`/`PROJECT_STATE.md`.
4. Database initialization — done (see above): the local DuckDB storage
   foundation (infrastructure-metadata schema only) is initialized under
   `data/`.
5. Reviewed data contracts for actual provider data — design and add
   versioned migrations for market-bar and macro-observation tables (and
   the ingestion code that writes to `ingestion_runs`), each as its own
   reviewed change, before any provider data is stored.

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
