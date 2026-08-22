# Data Catalog

This document catalogs the data sources known to this project. It is an
initial catalog based on what is currently known; it does **not** assert
verified schemas for data that has not yet been inspected. As datasets are
inspected and validated, this file should be updated with confirmed
details, and any unverified claims below should be corrected or removed.

## Data Independence

This is an independent project. It does not depend on or access the
separate ORB_Project, or any other data outside this repository.
Read-only Alpaca provider connectivity has been verified. One authorized
ingestion run stored 10 normalized SPY news articles, and a separate
authorized ingestion run stored 248 normalized SPY historical bars (IEX,
`5Min`, `raw`, `USD` — see "Local Storage" below). Each collection
verifies one ingestion run; neither is a complete, gap-free, or validated
dataset. FRED observations have not been stored, and no complete provider
dataset has been cataloged or validated yet — connectivity, or a single
ingestion run, is not the same as a validated data pipeline. No code in
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
  summary, or raw response payload from that connectivity check was printed
  or recorded. No historical/bulk or complete news dataset has been captured
  or validated, so no dataset entry with verified Schema/Coverage/Known
  limitations exists yet. This is a connectivity check only and must not be
  described as a validated data pipeline. Separately, on 2026-08-20, one
  explicitly authorized live SPY news ingestion was run via
  `scripts/ingest_alpaca_news.py` and succeeded: 10 articles received, 10
  inserted, 0 updated, 0 failed, recorded via the `news_articles` table and
  storage service described under "Local Storage" below. This confirms
  the storage pipeline succeeded for one ingestion run — only sanitized
  counts and status are recorded, no article content — and is still not a
  validated, cataloged news dataset (Schema/Coverage/Known limitations
  below still require direct inspection of stored data before they can be
  recorded).
- **Alpaca Bars** — historical stock-bars provider. A read-only connector,
  `AlpacaBarsClient` in `market_intelligence/data_connectors/alpaca_bars.py`,
  exists and talks only to Alpaca's read-only data host
  (`https://data.alpaca.markets`), and only to its single-symbol historical
  bars endpoint (`/v2/stocks/{symbol}/bars`); it has no methods for orders,
  accounts, or execution, and does not write to DuckDB. It supports exactly
  one symbol per request and only the three project-approved timeframes
  (`1Min`, `5Min`, `1Day`). `start`/`end` are strictly validated RFC3339
  timestamps normalized to UTC, with `start` required to be strictly before
  `end`; the per-page limit and page count are both strictly bounded. The
  normalized `Bar` model contains only provider, symbol, timeframe, feed,
  bar timestamp (UTC), OHLC (as `Decimal`), volume, trade_count (nullable),
  vwap (nullable), and a UTC retrieval timestamp kept distinct from the bar
  timestamp — no indicators, returns, labels, sentiment, predictions, or
  trade directions are inferred. A non-empty provider bars list containing
  any malformed bar fails the entire request rather than returning a
  misleading partial series; exact duplicate bars (matched on symbol,
  timeframe, feed, and timestamp) are deduplicated, conflicting duplicates
  fail the request, and results are returned in chronological order.

  **Feed:** every request — including every paginated page and
  `check_connection` — explicitly sends `feed=iex`, via a fixed module
  constant (`DATA_FEED`) that is never accepted as a caller-supplied
  argument anywhere in this connector, so no unsupported or different feed
  can be injected by a caller, and this connector never falls back
  automatically between feeds. `feed` is recorded on every normalized `Bar`
  and on every `BarsConnectionStatus` (including failed/unconfigured/
  invalid-input statuses) so data provenance is always explicit. **Why IEX,
  not the default:** on 2026-08-20, the first authorized live connectivity
  check (`scripts/check_alpaca_bars.py`, single-symbol SPY, via the
  connector's then-default request with no `feed` parameter) reached
  Alpaca and returned `configured=True, success=False, status_category=4xx`
  — only this sanitized status was recorded, never the raw response body,
  headers, or credentials. Per Alpaca's official documentation, the
  historical single-symbol bars endpoint defaults to the SIP feed when no
  `feed` parameter is sent, and SIP requires a market-data subscription;
  the likely cause of the observed 4xx is that default SIP routing hitting
  insufficient SIP subscription access (Alpaca returns HTTP 403 in that
  case), not a credentials or code defect. The connector was hardened in
  response to explicitly request the free, always-available IEX feed on
  every request instead of relying on the endpoint's SIP default. This
  failed 4xx check is preserved here as an honest diagnostic record and is
  not retracted by the successful check recorded below. **Known
  limitation:** IEX is a single exchange's feed, not the consolidated SIP
  tape — it reflects trades on IEX only, not the full U.S. market, and so
  has narrower coverage (fewer trades, potentially different prices/volume)
  than SIP.

  **Live IEX connectivity verified (2026-08-20):** a second, separately
  authorized live connectivity check was run against the now-hardened,
  explicit-IEX connector (`scripts/check_alpaca_bars.py`, single-symbol
  SPY, `5Min` timeframe) and succeeded, returning a sanitized status of
  `configured=True, success=True, status_category=2xx, symbol=SPY,
  timeframe=5Min, feed=iex, bar_count=5, oldest_bar_timestamp=
  2026-08-17T12:25:00Z, newest_bar_timestamp=2026-08-17T13:30:00Z`. Only
  this sanitized status was recorded — no OHLCV values, credentials, URLs,
  raw response body, or page tokens were printed or stored. **This
  confirms connectivity and response normalization only** — it does not
  confirm SIP connectivity (SIP is not requested by this connector), IEX's
  narrower coverage still applies, and this is not a stored, complete, or
  validated historical bars dataset: no bars from this check were written
  to DuckDB (this connector does not store bars), and no coverage, gap, or
  quality analysis has been performed.

  Status: **implemented, live-connectivity-verified on the explicit IEX
  feed only (not SIP).** A companion script, `scripts/check_alpaca_bars.py`,
  and a `check_connection` method exist and report only sanitized
  connection status (configured, success, status category, symbol,
  timeframe, feed, adjustment, currency, bar count, oldest/newest bar
  timestamp — never OHLCV values, credentials, URLs, raw responses, or page
  tokens); this connector itself makes no network requests beyond a single
  bounded page fetch and does not write to DuckDB. Separately, one
  authorized live ingestion run has used this connector's output to store
  248 SPY bars via `BarRepository` — see "Local Storage" below for that
  run's details. That is one controlled ingestion run, not a complete,
  gap-free, or validated historical bars dataset, so no dataset entry with
  verified Schema/Coverage/Known limitations exists yet in this catalog,
  and this connector's output must not be described as validated or
  research-ready — only connectivity, response normalization, and one
  successful storage run have been verified.

  **Adjustment/currency provenance hardening:** alongside `feed=iex`, every
  request — including every paginated page and `check_connection` — now
  also explicitly sends `adjustment=raw` and `currency=USD`, via fixed
  module constants (`DATA_ADJUSTMENT`, `DATA_CURRENCY`) that are never
  accepted as caller-supplied arguments anywhere in this connector, for the
  same explicit-provenance reasoning as the `feed` hardening above. "raw"
  means split/dividend-unadjusted prices as originally reported; this
  project applies no corporate-action adjustment. Both values are recorded
  on every normalized `Bar` and `BarsConnectionStatus` and cannot be
  overridden by a provider response. The 2026-08-20 connectivity check
  recorded above was made before this hardening and remains valid evidence
  of IEX connectivity/normalization only; the hardening has since been
  exercised by the first live bars ingestion (2026-08-21, see "Local
  Storage" below), which used `feed=iex`, `adjustment=raw`, and
  `currency=USD` throughout.

## Local Storage

A local DuckDB storage foundation exists at `data/market_intelligence.duckdb`
(`market_intelligence/storage/`, documented in
[docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)). Migration
`0004` (a `news_articles` table, plus `market_intelligence/storage/news_repository.py`
and `scripts/ingest_alpaca_news.py`) has been added to this repository's
migration/storage code and is covered by tests using temporary DuckDB
files. As of 2026-08-20, the real local database file had been upgraded to
and was healthy at schema version `0004` (4 migrations applied), following
the authorized live news ingestion described below.

Migration `0005` (a `market_bars` table, plus
`market_intelligence/storage/bar_repository.py` and
`scripts/ingest_alpaca_bars.py`) was also added to this repository's
migration/storage code and is covered by tests using temporary DuckDB
files. **On 2026-08-21, `data/market_intelligence.duckdb` was backed up,
and a separately authorized initialization run then applied migration
`0005` to the real local database.** A subsequent read-only health check
reported: schema version `0005`, 5 migrations applied, required tables
present, required columns present, migration history valid, checksums
valid, database at the latest available migration, and `healthy=True`.
**The real database is now at migration `0005` and reports healthy.** Its
migration code defines five tables:

- `schema_migrations` — tracks which versioned migrations have been
  applied.
- `ingestion_runs` — records ingestion-run metadata (provider, dataset
  name, start/completion timestamps, status, records received, a
  sanitized error category, `code_version`, and — since migration `0003`
  — a separate `schema_version`).
- `news_articles` (migration `0004`) — stores normalized news-article
  metadata (provider, provider article ID, headline, source, URL, summary,
  publication/update timestamps, related symbols, retrieval/ingestion
  provenance timestamps, and the writing ingestion run's ID) with
  `(provider, provider_article_id)` idempotency. No sentiment, impact,
  direction, confidence, model output, recommendation, or option-contract
  field exists on this table. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  column/behavior detail.
- `market_bars` (migration `0005`) — stores normalized historical stock-bar
  data (provider, symbol, timeframe, feed, adjustment, currency, bar
  timestamp, OHLCV, trade count and vwap as `DECIMAL(18,6)`/nullable where
  the connector allows, retrieval/ingestion provenance timestamps, and the
  writing ingestion run's ID) with `(provider, symbol, timeframe, feed,
  adjustment, currency, bar_timestamp)` idempotency. No indicator, return,
  label, sentiment, prediction, recommendation, option-contract, order, or
  execution field exists on this table. **Now applied to the real local
  database, and a first batch of real bars has been stored through it** —
  see below. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  column/behavior detail.

**News: one authorized ingestion run, not yet a validated, cataloged
dataset.** `scripts/ingest_alpaca_news.py` performs at most one explicit,
read-only, strictly-validated request per run. On 2026-08-20, one
explicitly authorized live run (single-symbol SPY, default limit)
succeeded: 10 articles received, 10 inserted, 0 updated, 0 failed,
recorded as a `succeeded` `ingestion_runs` row — this is the run that
upgraded the real database to schema `0004` above. Only sanitized counts
and status are recorded in this catalog, never article content. This
confirms the storage pipeline succeeded for one ingestion run; it is not
yet a validated, cataloged news dataset — a full dataset record (Source,
Status, Provenance, Schema, Coverage, Known limitations) per the "Required
Fields" section below still requires direct inspection of the stored rows,
which has not been done as part of this entry.

**Market bars: first authorized live ingestion succeeded (2026-08-21).**
The `AlpacaBarsClient` connector (see "Planned Data Providers" above)
explicitly requests and records `feed=iex`, `adjustment=raw`, and
`currency=USD` on every request/page, as fixed project constants, so every
normalized `Bar` carries complete, explicit IEX/raw/USD provenance.
`market_intelligence/storage/bar_repository.py` (`BarRepository`) and
`scripts/ingest_alpaca_bars.py` mirror the news-storage pattern: strict
pre-write validation (including that every bar's
feed/adjustment/currency/timeframe matches this project's fixed values),
atomic batch writes tied to an `ingestion_runs` row, and conflict rollback
for any existing bar identity whose OHLCV/trade_count/vwap values would be
overwritten.

`data/market_intelligence.duckdb` was backed up, migration `0005` was
applied (see "Local Storage" above), and `scripts/ingest_alpaca_bars.py`
was then run once, live, against the real database with an explicit,
bounded request: single-symbol SPY, `5Min` timeframe, `feed=iex`,
`adjustment=raw`, `currency=USD`, requested interval
2026-08-15T00:00:00Z through 2026-08-20T00:00:00Z, `max_pages=1`,
`limit=500`. The run received 248 bars, inserted 248, had 0
existing/updated and 0 failed, and the corresponding `ingestion_runs` row
was recorded `succeeded`; the latest `ingestion_runs` record for this
dataset is `('alpaca', 'bars', 'succeeded', 248, None)`. A subsequent
read-only query of `market_bars` verified 248 stored rows for this
symbol/timeframe/feed, covering 2026-08-17T12:25:00Z through
2026-08-19T20:00:00Z.

**This confirms one controlled ingestion run, transactional storage, and
successful local retrieval.** It does not establish a complete,
gap-free, consolidated, or research-validated historical bars dataset;
coverage is IEX only, which is narrower than SIP; and it carries no claim
of predictive value, strategy validity, production readiness, or
options-trading capability. Only sanitized counts, status, and the
verified row count/coverage window are recorded in this catalog — no
OHLCV values are reproduced here. A full dataset record (Source, Status,
Provenance, Schema, Coverage, Known limitations) per the "Required
Fields" section below still requires broader coverage and direct
gap/quality inspection of the stored data, which has not been done as
part of this entry.

No macro-observation, forecast, or trade table has been created — each
requires its own reviewed data contract and a corresponding versioned
migration before it is added.

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
