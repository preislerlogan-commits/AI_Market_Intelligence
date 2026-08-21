# Data Catalog

This document catalogs the data sources known to this project. It is an
initial catalog based on what is currently known; it does **not** assert
verified schemas for data that has not yet been inspected. As datasets are
inspected and validated, this file should be updated with confirmed
details, and any unverified claims below should be corrected or removed.

## Data Independence

This is an independent project. It does not depend on or access the
separate ORB_Project, or any other data outside this repository.
Read-only Alpaca provider connectivity has been verified, but no
historical or live dataset has been stored, cataloged, or validated yet —
connectivity is not the same as a validated data pipeline. No code in
this repository may access files outside the repository unless the user
explicitly authorizes a specific source. Future data will come through
this project's own reviewed connectors under
`market_intelligence/data_connectors/`, with
verified details recorded below only after direct inspection.

## Known Universe

The following tickers are the known initial universe of interest:

- SPY
- QQQ
- IWM
- DIA
- NVDA
- TSLA
- AAPL
- MSFT
- AMZN
- META
- AMD
- GOOGL

## Planned Data Providers

- **Alpaca** — market-data provider. A read-only connector,
  `AlpacaMarketDataClient` in
  `market_intelligence/data_connectors/alpaca_market_data.py`, exists and
  talks only to Alpaca's market-data API (`https://data.alpaca.markets`);
  it has no methods for orders, accounts, or execution. Status:
  connection-verified (read-only). On 2026-08-20, one live, read-only
  single-symbol (SPY) snapshot request was made via
  `scripts/check_alpaca_connection.py` using locally configured `.env`
  credentials and returned a successful (2xx) response with a market
  timestamp. Only sanitized connection-status metadata was recorded
  (configured, success, status category, symbol, timestamp) — no raw
  snapshot payload, schema, or bulk/historical market data has been
  captured, stored, or inspected, so no dataset entry with verified
  Schema/Coverage/Known limitations exists yet. This is a connectivity
  check only and must not be described as a validated data pipeline.
- **FRED** — macroeconomic data provider. A read-only connector,
  `FredMacroDataClient` in
  `market_intelligence/data_connectors/fred_macro_data.py`, exists and
  talks only to FRED's official API (`https://api.stlouisfed.org`); it has
  no methods for anything beyond fetching published series observations.
  Status: connection-verified (read-only). On 2026-08-20, one live,
  read-only latest-observation request for the FEDFUNDS series was made
  via `scripts/check_fred_connection.py` using locally configured `.env`
  credentials and returned a successful (2xx) response with an observation
  date. Only sanitized connection-status metadata was recorded (configured,
  success, status category, series ID, observation date) — the observation
  value itself was never printed or recorded, and no raw response payload,
  schema, or bulk/historical series data has been captured, stored, or
  inspected, so no dataset entry with verified Schema/Coverage/Known
  limitations exists yet. This is a connectivity check only and must not be
  described as a validated data pipeline.
- **Alpaca News** — news provider. A read-only connector,
  `AlpacaNewsClient` in `market_intelligence/data_connectors/alpaca_news.py`,
  exists and talks only to Alpaca's read-only data host
  (`https://data.alpaca.markets`), and only to its news endpoint
  (`/v1beta1/news`); it has no methods for orders, accounts, or execution,
  and does not write to DuckDB. All symbols, result limits, sort
  direction, and optional start/end timestamps are strictly
  validated/normalized before any request is built, so malformed or
  malicious input never reaches the network. Normalized news items contain
  only provider-reported metadata (provider article ID, headline, source,
  URL, summary when available, publication/update timestamps when
  available, related symbols, retrieval timestamp in UTC, provider name) —
  no sentiment, impact, or direction is inferred. Status:
  connection-verified (read-only). On 2026-08-20, one live, read-only
  single-symbol (SPY) news request was made via
  `scripts/check_alpaca_news.py` using locally configured `.env`
  credentials and returned a successful (2xx) response with 10 articles
  and a newest publication timestamp. Only sanitized connection-status
  metadata was recorded (configured, success, status category, requested
  symbol, article count, newest publication timestamp) — no headline, URL,
  summary, or raw response payload was printed or recorded, and no
  historical/bulk news data has been captured, stored, or inspected, so no
  dataset entry with verified Schema/Coverage/Known limitations exists
  yet. This is a connectivity check only and must not be described as a
  validated data pipeline.

## Local Storage

A local DuckDB storage foundation exists at `data/market_intelligence.duckdb`
(`market_intelligence/storage/`, documented in
[docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)), currently at
schema version `0003`. Its schema currently defines only two
infrastructure-metadata tables:

- `schema_migrations` — tracks which versioned migrations have been
  applied.
- `ingestion_runs` — records ingestion-run metadata (provider, dataset
  name, start/completion timestamps, status, records received, a
  sanitized error category, `code_version`, and — since migration `0003`
  — a separate `schema_version`). No ingestion code writes to this table
  yet.

No provider dataset has been ingested or stored in this database, and no
forecast or trade data exists here. No market-bar, macro-observation,
news, forecast, or trade table has been created — each requires its own
reviewed data contract and a corresponding versioned migration before it
is added.

## Required Fields for Every Future Dataset

Every dataset added to this catalog in the future must record:

- **Source** — where the data originates (provider name, file path, or
  system).
- **Status** — e.g. planned, connected-untested, validated, deprecated.
- **Provenance** — how the data was obtained (API pull, manual export,
  vendor file) and when.
- **Schema** — verified column names, types, and units, recorded only
  after direct inspection.
- **Coverage** — verified date range and instrument coverage.
- **Known limitations** — gaps, quality issues, adjustments applied (or
  not applied), timezone conventions.

No dataset should be described as validated or complete without this
information recorded.
