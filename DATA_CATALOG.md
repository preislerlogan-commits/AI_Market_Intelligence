# Data Catalog

This document catalogs the data sources known to this project. It is an
initial catalog based on what is currently known; it does **not** assert
verified schemas for data that has not yet been inspected. As datasets are
inspected and validated, this file should be updated with confirmed
details, and any unverified claims below should be corrected or removed.

## External Historical Data

- **Location:** `C:\ORB_Project\data`
- **Status:** External to this repository. Read-only (see
  [PROJECT_STATE.md](PROJECT_STATE.md) and [CLAUDE.md](CLAUDE.md) /
  [AGENTS.md](AGENTS.md)).
- **Known contents (unverified schema):**
  - 1-minute OHLCV/VWAP data, approximately 2019 through August 2026.
  - Daily OHLCV data.
- **Note:** The exact schema (column names, types, timezone conventions,
  adjustment methodology, gaps/quality issues) has not yet been inspected
  as part of this project and must not be assumed. Schema details should be
  recorded here only after direct inspection.

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

- **Alpaca** — planned initial live market-data provider. Not yet
  connected; no credentials configured (see `.env.example`).
- **FRED** — planned macroeconomic data provider. Not yet connected; no
  credentials configured (see `.env.example`).

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
