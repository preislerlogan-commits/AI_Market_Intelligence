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
ingestion run stored 10 normalized SPY news articles, a separate
authorized ingestion run stored 248 normalized SPY historical bars (IEX,
`5Min`, `raw`, `USD`), and a third authorized ingestion run stored 12
normalized FEDFUNDS macro observations (see "Local Storage" below). A
first authorized live ingestion-orchestration run (2026-08-23) has since
run all three of these pipelines together through explicit job contracts
(see "Local Storage" below); this confirms one controlled orchestrated
run, not scheduling, continuous operation, or dataset completeness. Separately
authorized live Core Macro Basket runs (`scripts/ingest_core_macro_basket.py`,
2026-08-24 and 2026-08-25) have since stored observations and series metadata
for all seven approved FRED series (`FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`,
`UNRATE`, `INDPRO`, `GDPC1`); the per-dataset detail, actual coverage, and
freshness limitations are recorded under "Dataset Records" below. Each
collection verifies one or a small number of bounded ingestion runs; none is
a complete, gap-free, or validated dataset — connectivity, or a single
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
  described as a validated data pipeline. `get_observations()`
  (2026-08-21, code/tests only): the same client now also exposes a
  strictly validated, bounded, paginated historical-observations fetch for
  one series over an explicit calendar-date range, returning normalized
  `FredObservation` records (provider, series_id, observation_date, value,
  is_missing, realtime_start/realtime_end, retrieved_at). Every page of
  this request explicitly sends fixed, non-overridable
  `realtime_start=1776-07-04`, `realtime_end=9999-12-31`, `output_type=1`,
  and `units=lin`. FRED documents that an omitted realtime_start/
  realtime_end defaults to *today's date*, not to an observation's actual
  reported revision window; requesting the complete real-time period
  explicitly is what makes `realtime_start`/`realtime_end` describe FRED's
  actual revision/vintage window and keeps the storage identity (see
  "Local Storage" below) stable and meaningful across repeated ingestion
  runs on different retrieval days, rather than merely reflecting the
  retrieval date. **This method has since been exercised against the live
  API by a first authorized historical-observations ingestion (2026-08-21,
  FEDFUNDS, 12 observations received/stored) — see "Local Storage" below
  for the corresponding macro-observations storage capability and that
  run's sanitized counts/coverage.** This is one bounded ingestion run for
  one series/date-range, not a validated or cataloged dataset.
  `get_series_metadata()` (2026-08-24, code/tests only, not yet run live):
  the same client now also exposes a strictly validated fetch of FRED's
  series-level metadata (title, units, frequency, seasonal adjustment,
  popularity, notes, observation date range, last-updated timestamp) for a
  single series via FRED's official series endpoint
  (`https://api.stlouisfed.org/fred/series`) -- distinct from
  `get_observations()`, which fetches a series' *values*. This exists so a
  future Macro Analyst never interprets an unlabeled number. The response
  must contain exactly one matching series (matched by FRED's own reported
  `id` against the requested, normalized series ID); every required string
  field must be nonblank; `observation_start`/`observation_end` must be
  strict `YYYY-MM-DD` dates; `last_updated` must be a strict,
  timezone-aware timestamp (FRED's documented
  `"YYYY-MM-DD HH:MM:SS+/-HH[:MM]"` shape) and is normalized to UTC;
  `popularity` must be a plain nonnegative integer (booleans rejected); and
  `notes` is preserved exactly as FRED reported it (or `null` when FRED
  reports none) -- never interpreted or summarized. Any malformed or
  mismatched payload fails the whole request rather than returning a
  partial object. Errors and sanitized status output never include the API
  key, request URL/query parameters, raw response body, or the
  provider-reported `title`/`notes` text. As of 2026-08-24 this had not
  been exercised against the live API. **It has since been exercised
  live for one series (`GS10`), as part of the authorized Core Macro
  Basket `GS10` execute run (2026-08-25) -- see "Local Storage" below.**
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

- **Alpaca Options (SPY option-chain snapshots)** — read-only option-chain
  snapshot provider. A read-only connector, `AlpacaOptionsChainClient` in
  `market_intelligence/data_connectors/alpaca_options_chain.py`, exists and
  talks only to Alpaca's read-only data host
  (`https://data.alpaca.markets`), and only to its option-chain snapshot
  endpoint (`GET /v1beta1/options/snapshots/{underlying}`). Scope is **SPY
  only** (the underlying is a fixed constant, never caller-supplied); it has
  no account, order, position, portfolio, exercise, or execution method, and
  it never touches `paper-api.alpaca.markets` or the option-contract/trading
  API. Every request requires a fully bounded, strictly validated input: an
  explicit `opra` or `indicative` feed (no default, no fallback), an
  expiration-date window (`expiration_date_gte`/`_lte`), a strike-price
  window (`strike_price_gte`/`_lte`), an optional `call`/`put` filter, and
  bounded page-limit / max-pages / max-total-contracts values enforced by
  local constants. **Conservative Phase 1 hard ceilings** (safety ceilings,
  not contract-selection rules — callers must still supply explicit ranges):
  expiration span ≤ 60 calendar days, strike-window width ≤ $500, per-page
  limit ≤ 1,000 (the provider's own maximum), max pages ≤ 10, max total
  contracts ≤ 5,000. Pagination follows `next_page_token` deterministically
  with loop detection and hard bounds, and there is no automatic retry. Each
  snapshot's OCC-style contract symbol is parsed into underlying / expiration
  / type / strike and cross-checked against the requested filters.
  Normalized snapshots carry only the fields the endpoint supplies —
  contract symbol, underlying, expiration, option type, strike, feed; latest
  quote timestamp and bid/ask price and size; latest trade timestamp, price
  and size; implied volatility; delta/gamma/theta/vega/rho where supplied;
  and a UTC retrieval timestamp. Missing optional quote/trade/Greek fields
  remain null — never zero. Errors are sanitized to a fixed category (no
  response body, headers, URL, query parameters, credentials, or contract
  payload). **Open interest is not supplied by this snapshot endpoint and is
  not produced, inferred, or stored — it is unavailable in this milestone.**
  **`indicative`-feed data may be delayed or modified by the provider and
  must never be described as live OPRA data.** Status (as originally
  written): implemented in code and tests only (mocked HTTP transports); no
  live option-chain request had been made and no real option data had been
  stored. A dry-run-first ingestion script, `scripts/ingest_alpaca_options_chain.py`,
  and migration `0009` (`option_chain_snapshot_batches`, `option_chain_snapshots`,
  and `option_chain_snapshot_batch_items`) plus `OptionChainSnapshotRepository`
  exist. **A successful retrieval that returns zero contracts still persists
  exactly one `option_chain_snapshot_batches` row** (with `contract_count = 0`,
  `outcome = skipped_empty`) recording its feed, request bounds, and
  retrieval instant, even though it writes no `option_chain_snapshots` or
  `option_chain_snapshot_batch_items` rows — see "Option-chain snapshot
  storage" below.

  **Superseding update (2026-09-14): first authorized live ingestion
  succeeded.** Migration `0009` has been applied to the real local database.
  `scripts/ingest_alpaca_options_chain.py --execute` was run once, live:
  provider `alpaca`, underlying `SPY`, requested feed `indicative`
  (explicitly not OPRA), one expiration (2026-09-18), strikes 740–790. 102
  contracts were received and inserted (51 calls, 51 puts); the batch and
  the corresponding `ingestion_runs` row both recorded `succeeded`; exactly
  one request was made, with no retry. A subsequent read-only structural
  audit found zero duplicate/orphan membership rows, zero malformed OCC
  symbols, zero out-of-range contracts, zero negative/nonfinite values,
  zero crossed quotes, and zero feed mismatches — see the "SPY option-chain
  snapshots (indicative)" dataset record below for full sanitized detail.
  This confirms one controlled live ingestion and its structural integrity;
  it does **not** establish pricing accuracy, timeliness, usefulness,
  predictive edge, strategy validity, or profitability, and open interest
  remains unavailable from this endpoint.

## Local Storage

**Storage foundation status (2026-09-14): the real local database is healthy
at schema version `0009` (9 migrations applied)**, following the live
option-chain ingestion described under "Option-chain snapshot storage"
below. **Update (2026-09-23):** after the bounded SPY bars ingestion
recorded under "SPY 5-minute IEX market bars" (run 3), a post-run health
check again reported schema version `0009` (9 migrations applied), required
tables/columns present, valid migration history and checksums, latest
migration applied, and `healthy=True`; the schema version is unchanged.

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

**Macro observations: first authorized live ingestion succeeded
(2026-08-21).** A reviewed data contract and versioned migration exist
for macro observations: migration `0006`
(`market_intelligence/storage/migrations/0006_create_macro_observations.sql`)
defines a `macro_observations` table, and
`market_intelligence/storage/macro_observation_repository.py`
(`MacroObservationRepository`) accepts already-normalized `FredObservation`
objects from `FredMacroDataClient.get_observations()` and writes them
transactionally, mirroring `BarRepository`'s pattern; a manual ingestion
script, `scripts/ingest_fred_observations.py`, also now exists. The table
stores only FRED's own reviewed observation fields plus provenance
(`provider` fixed `"fred"`, `series_id`, `observation_date`,
`realtime_start`, `realtime_end`, `value` as `DECIMAL(20,6)` (nullable),
`is_missing`, `retrieved_at`, `first_ingested_at`, `last_seen_at`,
`ingestion_run_id`) — no prediction, direction, sentiment, impact,
recommendation, option-contract, order, or execution field exists.
Idempotency is enforced via a `(provider, series_id, observation_date,
realtime_start, realtime_end)` primary key, deliberately including FRED's
own revision/vintage window rather than collapsing on `(series_id,
observation_date)` alone, so a later revision of an already-stored
observation is preserved as its own row rather than silently overwriting
an earlier vintage. An already-known observation identity whose
value/is_missing still matches has only its retrieval/last-seen/run
provenance refreshed, while a conflicting value aborts the entire batch
(nothing partially persists) and the corresponding `ingestion_runs` row is
recorded `failed` with a sanitized error category. **This capability was
originally built and tested against temporary databases only** (temporary
DuckDB files, mocked HTTP transports — no live requests, no real-database
writes).

**Migration `0006` applied and first authorized live FEDFUNDS ingestion
(2026-08-21):** `data/market_intelligence.duckdb` was backed up, and a
separately authorized initialization run then applied migration `0006` to
the real local database (schema version `0006`, 6 migrations applied,
health check reported `healthy=True` — see
[docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)).
`scripts/ingest_fred_observations.py` was then run once, live, against the
real database with an explicit, bounded request: series `FEDFUNDS`,
requested observation range 2025-08-01 through 2026-07-31, fixed request
provenance (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`,
`output_type=1`, `units=lin`), `limit=1000`, `max_pages=1`. The run
received 12 observations, inserted 12, had 0 existing/updated and 0
failed, and the corresponding `ingestion_runs` row was recorded
`succeeded`; the latest `ingestion_runs` record for this dataset is
`('fred', 'macro_observations', 'succeeded', 12, None)`. A subsequent
read-only query of `macro_observations` verified 12 stored rows for this
series, covering 2025-08-01 through 2026-07-01, with 0 missing
observations.

**This confirms one bounded historical fetch, response normalization,
transactional storage, and local retrieval.** It does not establish a
complete, gap-free, broadly cataloged, or research-validated macro
dataset: requesting the complete real-time period
(`realtime_start=1776-07-04`, `realtime_end=9999-12-31`) makes this run's
vintage window describe FRED's actual revision window for the requested
observations; it is not a claim that all of FEDFUNDS's revision history
has been retrieved, nor that any other series or date range has been
covered. `DECIMAL(20,6)` remains a deliberately bounded supported range
for this project's currently-ingested series, not a claim of universal
support for every FRED series. Only sanitized counts, status, and the
verified row count/coverage window are recorded here — no observation
values are reproduced in this catalog. The previously stored 248 SPY bars
and 10 SPY news articles remain intact and unchanged. FRED connectivity
was also previously, separately verified via the pre-existing
single-latest-observation check (2026-08-20, see "Planned Data Providers"
above). A full dataset record (Source, Status, Provenance, Schema,
Coverage, Known limitations) per the "Required Fields" section below
still requires broader series/date coverage and direct gap/quality
inspection of the stored data, which has not been done as part of this
entry.

**Ingestion orchestration: first authorized live run (2026-08-23).** A
committed job-configuration file (`market_intelligence/orchestration/jobs.json`)
defines three initial reviewed orchestration jobs — `alpaca_news_spy`,
`alpaca_bars_spy_5min`, `fred_fedfunds_observations` — each wrapping one of
the connector/repository pipelines documented above. Migration `0007`
(`orchestration_runs`/`orchestration_job_runs`, a persistent audit trail
independent of the existing `ingestion_runs` table) was applied to the
real local database (backed up beforehand), and
`scripts/run_ingestion_pipeline.py` was then run once, live, with
`--all --execute`, selecting all three jobs in one orchestrated run
(`orchestration_run_id=e63d931e-8957-4357-93ee-ba7076b079d8`). A
subsequent read-only health check reported `schema_version=0007`,
`applied_migration_count=7`, `healthy=True`.

The run completed with overall status `succeeded`; a read-only query
confirmed `orchestration_runs` contains exactly 1 run and all three
`orchestration_job_runs` rows for it are recorded `succeeded`. Per-job
sanitized counts:

- `alpaca_news_spy`: 10 received, 10 inserted, 0 existing/updated, 0
  failed.
- `alpaca_bars_spy_5min` (`feed=iex`, `adjustment=raw`, `currency=USD`):
  334 received, 169 inserted, 165 existing/updated, 0 failed.
- `fred_fedfunds_observations`: 3 received, 0 inserted, 3
  existing/updated, 0 failed.

**This confirms one controlled, explicitly authorized orchestration run
across all three existing reviewed jobs, transactional per-job storage
(reusing the same repositories described above), and a persistent
orchestration audit trail.** The 165 existing/updated bars and the 3
existing/updated FRED observations reflect idempotent overlap with
previously stored rows within each job's own bounded request window, not
new distinct dataset coverage. This run does not establish scheduling,
continuous or unattended operation, or dataset completeness/gap-freedom
for any of the three underlying datasets — none of that is claimed by
this entry. Only sanitized counts, status, and identifiers are recorded
here — no headline, URL, summary, OHLCV, or observation value from this
run is reproduced in this catalog. See
[docs/INGESTION_ORCHESTRATION.md](docs/INGESTION_ORCHESTRATION.md) for
full detail.

**Macro series metadata: migration `0008` applied, seven-series metadata
ingested live (superseding the 2026-08-24 "code/tests only" status below).**
A narrow, reviewed data contract exists for FRED series-level
*metadata* (title, units, frequency, seasonal adjustment, popularity, notes,
observation date range, last-updated timestamp) -- distinct from
`macro_observations`, which stores a series' *values*. Migration `0008`
(`market_intelligence/storage/migrations/0008_create_macro_series_metadata.sql`)
defines a `macro_series_metadata` table, and
`market_intelligence/storage/macro_series_metadata_repository.py`
(`MacroSeriesMetadataRepository`) accepts an already-normalized
`FredSeriesMetadata` object from
`FredMacroDataClient.get_series_metadata()` and writes it transactionally; a
manual ingestion script, `scripts/ingest_fred_series_metadata.py`, also now
exists. The table stores only FRED's own reviewed series-metadata fields
plus provenance (`provider` fixed `"fred"`, `series_id`, `title`,
`observation_start`, `observation_end`, `frequency`, `frequency_short`,
`units`, `units_short`, `seasonal_adjustment`, `seasonal_adjustment_short`,
`last_updated`, `popularity`, `notes` (nullable), `retrieved_at_utc`,
`first_ingested_at`, `last_seen_at`, `ingestion_run_id`) -- no prediction,
direction, sentiment, impact, recommendation, option-contract, order, or
execution field exists. Idempotency is enforced via a
`(provider, series_id)` primary key; unlike `macro_observations`, series
metadata has no revision/vintage window of its own, so a repeat ingestion of
an already-known series always refreshes every mutable metadata/provenance
column in place -- it is never treated as a conflict, since a series'
title, units, popularity, or notes may legitimately change over time from
FRED's own perspective. `MacroEvidenceBuilder`
(`market_intelligence/market_features/macro_evidence.py`) now also reads
this table (read-only) and adds `metadata_available` plus `title`,
`frequency`, `units`, `seasonal_adjustment` to each series entry in its
snapshot, plus an aggregate `missing_metadata_series` flag -- so a future
Macro Analyst never interprets an unlabeled number.

**Superseded history (accurate only as of 2026-08-24):** when this
subsection was first written, this capability existed in code and tests only
(temporary DuckDB files, mocked HTTP transports -- no live FRED request, no
write to the real database), and migration `0008` had not been applied to
the real local database, which remained at migration `0007`. That statement
is preserved here as an honest, time-scoped diagnostic record and is **not**
retracted.

**Current status:** migration `0008` has since been applied to the real
local database (independently confirmed by a 2026-08-25 read-only health
check reporting schema version `0008`, `healthy=True`), and metadata rows
for all seven Core Macro Basket series have been ingested live through
`MacroSeriesMetadataRepository` (see the "Seven-series macro series
metadata" dataset record below and item 25 of `PROJECT_STATE.md`). The
standalone `scripts/ingest_fred_series_metadata.py` has still never been run
live -- the metadata rows were written via `scripts/ingest_core_macro_basket.py`,
which uses the same repository. See
[docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) and
[docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md) for full
detail.

**Core Macro Basket: committed configuration and dry-run-first batch
ingestion script, code/tests only, not yet run live (2026-08-24).** A new
committed configuration file,
`market_intelligence/config/core_macro_series.json`, and its loader/
validator, `market_intelligence/config/macro_basket.py`, define a fixed,
reviewed universe of exactly seven approved FRED series -- `FEDFUNDS`
(`policy_rate`), `GS10` (`long_term_rate`), `CPIAUCSL` (`inflation`),
`PCEPI` (`inflation`), `UNRATE` (`labor`), `INDPRO` (`growth`), `GDPC1`
(`growth`) -- each with a strictly validated `enabled`/
`observation_lookback_days`/`recent_observations_limit` contract and a
conservative, per-series lookback ceiling (`FEDFUNDS`/`CPIAUCSL`/`PCEPI`/
`UNRATE`/`INDPRO`/`GS10`: 400 days; `GDPC1`: 1,100 days) that
prevents an unbounded historical request. **No series title, unit,
frequency, seasonal adjustment, note, or observation value is hardcoded
anywhere in this configuration or its loader** -- only the series ID,
category, and bounded lookback/limit values are committed; titles, units,
and values come only from the existing, reviewed FRED metadata/observations
pipelines described above, at ingestion time.

A new dry-run-first CLI, `scripts/ingest_core_macro_basket.py`, reuses the
existing `FredMacroDataClient`, `MacroSeriesMetadataRepository`,
`MacroObservationRepository`, and the existing orchestration run lock
(`market_intelligence/orchestration/lock.py`) unmodified -- it does not
write to `orchestration_runs`/`orchestration_job_runs` and does not modify
`market_intelligence/orchestration/jobs.json` or any migration/schema; it is
a separate, narrower, manual batch tool, not scheduling. Default behavior is
a dry run: zero `Settings` construction, zero network requests, zero
database activity. `--execute` requires the real local database to already
be healthy at exactly schema version `0008` (checked read-only, before any
network request -- this script never applies a migration itself), then
processes each selected series sequentially (one bounded metadata request
plus one bounded observations request per series, no automatic retry); one
series' failure never prevents a later selected series from being
attempted, and the overall run status is only `succeeded` if every selected
series' metadata and observations requests/storage succeeded or were
validly empty. See [docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md)
for the full contract.

**As of this entry, this configuration and script exist in code, tests, and
docs only** -- no live FRED request has been made using this script, and no
row has been written to `macro_series_metadata`/`macro_observations` by it.
This is explicitly a first, bounded basket of seven series, not a complete
macro model and not proof of predictive usefulness of any kind.

**`DGS10` → `GS10` replacement (2026-08-24).** The first authorized live
core-macro-basket run succeeded for the other six series (`FEDFUNDS`,
`CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`) but failed for `DGS10`
observations only, sanitized error category `provider_error`; `DGS10`'s
metadata request succeeded. Only that sanitized category was recorded, so
the exact provider-side cause is not known -- this diagnostic record is
preserved, not rewritten or deleted. `DGS10` was then replaced in the
committed configuration by the official monthly FRED series `GS10`
("Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity,
Quoted on an Investment Basis" -- monthly, percent, not seasonally
adjusted), kept under the same `long_term_rate` category and now using the
existing monthly `observation_lookback_days` policy (400 days) with
`recent_observations_limit` unchanged at `6`. **`GS10` is a proposed,
bounded, monthly replacement only -- it has not yet been requested live.**
See [docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md) and
[PROJECT_STATE.md](PROJECT_STATE.md) for full detail.

**`GS10` first authorized live ingestion and read-only verification
(2026-08-25).** The "not yet requested live" status above has since been
superseded. A separately authorized live `--execute` run of
`scripts/ingest_core_macro_basket.py` targeting `GS10` succeeded: mode
`execute`, overall status `succeeded`, `series_id: GS10`,
`metadata_status: succeeded`, `observation_status: succeeded`, 13
observations received, 13 inserted, 0 existing/updated, 0 failed.

A separate, subsequent read-only verification reported: the real local
database at schema version `0008` (8 migrations applied), `healthy=True`;
1 stored `macro_series_metadata` row for `GS10`; 13 stored
`macro_observations` rows for `GS10`, covering 2025-07-01 through
2026-07-01, with 0 missing observations; and the latest
`macro_series_metadata`/`macro_observations` ingestion runs for `GS10`
both recorded `succeeded` with no `error_category`. Only sanitized
counts, status, schema version, and the verified row counts/coverage
window are recorded here -- no `GS10` title, unit, value, or note is
reproduced in this catalog.

Because `--execute` requires the real database to already be healthy at
exactly schema version `0008` before any network request (see
[docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md)), migration `0008`
(`macro_series_metadata`, see "Macro series metadata" above) must have
been applied to the real database prior to this run; the read-only
verification independently confirms this. The earlier "migration `0008`
has not been applied ... remains at migration `0007`" statement recorded
under "Macro series metadata" above was accurate as of 2026-08-24 and is
preserved as an honest, time-scoped diagnostic record, not retracted.

**This confirms one bounded, controlled live ingestion for one series
(`GS10`) and its corresponding read-only storage verification. It does
not establish a complete, gap-free, or research-validated macro dataset
for `GS10` or for any other series in the Core Macro Basket** -- the
other six series' live status is recorded separately above and is
unchanged by this entry. Stored coverage and a zero missing-observation
count describe what is present in local storage; they do not establish
economic-data correctness or predictive/analytical usefulness of any
kind. No forecasting, market-direction assessment, options
recommendation, or trading execution was performed or implied as part of
this run or this documentation update. See
[PROJECT_STATE.md](PROJECT_STATE.md) (item 25) for the full record.

**Option-chain snapshot storage: applied to the real database and first
authorized live ingestion succeeded (2026-09-14, three-table normalized
design — see "Why a third table" below).** Migration `0009`
(`market_intelligence/storage/migrations/0009_create_option_chain_snapshots.sql`)
defines three tables, and
`market_intelligence/storage/option_chain_snapshot_repository.py`
(`OptionChainSnapshotRepository`) writes all three, transactionally,
mirroring `BarRepository`:

- `option_chain_snapshot_batches` — exactly one row per successfully stored
  chain retrieval, **including a retrieval that returned zero contracts**:
  ingestion-run id (primary key), provider, underlying, requested feed,
  requested expiration window, requested strike window, requested option
  type, the UTC retrieval instant, and the contract count (`0` for an empty
  chain, with `outcome = skipped_empty`; `outcome = succeeded` otherwise).
  This is the durable run-level provenance record — request bounds are
  stored **only** here, not duplicated per snapshot or batch-item row. For
  every non-empty successful batch, `contract_count` always equals the
  number of `option_chain_snapshot_batch_items` rows for that batch.
- `option_chain_snapshots` — one row per **immutable** normalized contract
  observation (provider, underlying, feed, contract symbol, retrieved-at,
  parsed expiration/type/strike, quote timestamp/bid-ask price and size,
  trade timestamp/price and size, implied volatility,
  delta/gamma/theta/vega/rho). `ingestion_run_id` here records only the run
  that first inserted the row — it is never reassigned by a later
  re-observation of the same identity.
- `option_chain_snapshot_batch_items` — the normalized batch-membership
  table: one row per `(ingestion_run_id, provider, underlying, feed,
  contract_symbol, retrieved_at)`, written for every contract a successful
  non-empty batch actually returned, whether or not the underlying
  observation already existed from an earlier batch. This is what makes
  batch membership truthful under repeated ingestion of the same snapshot
  identity — one immutable observation may legitimately be referenced by
  more than one batch's membership rows.

**Why a third table.** The original two-table design allowed
`option_chain_snapshots.ingestion_run_id` to be updated in place on every
re-store of an identical snapshot, which silently moved that historical
snapshot into the newer batch while the original batch's `contract_count`
kept reporting its original count — batch membership was not truthfully
reconstructable from stored data. The batch-membership table removes that
mutation: `ingestion_run_id` on `option_chain_snapshots` is now
write-once, and truthful membership is read from
`option_chain_snapshot_batch_items` instead.

**There is no open-interest column on any of the three tables** — the
snapshot endpoint does not supply open interest. Idempotency and
immutability: `option_chain_snapshot_batches` keys on `ingestion_run_id` (a
fresh id per store call, so always an insert); `option_chain_snapshots`
keys on `(provider, underlying, feed, contract_symbol, retrieved_at)` —
`feed` is part of the identity, so OPRA and indicative observations are
never merged; a repeat of the same connector result never mutates or
relinks that row (at most refreshing non-historical `last_seen_at`
bookkeeping) while still inserting a fresh `option_chain_snapshot_batch_items`
row for the new batch; a conflicting value aborts the whole batch (no new
batch row, no partial snapshot rows, no partial batch-item rows, and the
existing snapshot row is untouched); and a later ingestion run (new
`retrieved_at`) writes a new batch row and new observation/batch-item rows.
The repository also re-normalizes every field of a supplied
`OptionChainRequest` through `normalize_option_chain_request` and rejects it
if it does not match its own canonical form, so a hand-constructed request
cannot bypass the connector's request ceilings. **As originally written,
migration `0009` had not been applied to the real local database (which
remained at schema version `0008`), no live option-chain request had been
made, and no option-chain row had been written anywhere but temporary test
databases; that statement is preserved here as an honest, time-scoped
diagnostic record and is not retracted.**

**Current status (2026-09-14): migration `0009` has been applied to the real
local database** (backed up beforehand); a subsequent read-only health check
reported schema version `0009` (9 migrations applied), `healthy=True`. **One
authorized live ingestion has since succeeded** — see the "SPY option-chain
snapshots (indicative)" dataset record below for the full sanitized
provenance, structural-audit results, and binding caveats.

**Phase 1 request ceilings (safety ceilings, not contract-selection rules):**
expiration span ≤ 60 calendar days, strike-window width ≤ $500, per-page
limit ≤ 1,000, max pages ≤ 10, max total contracts ≤ 5,000 — enforced by
`market_intelligence/data_connectors/alpaca_options_chain.py` before any
HTTP request is built.

No forecast or trade table has been created — each requires its own
reviewed data contract and a corresponding versioned migration before it
is added.

## Dataset Records

These are the datasets that have actually been ingested into local storage,
recorded in the format required by "Required Fields for Every Future Dataset"
below. **None of these is complete, gap-free, or research-validated.** Each is
one or a small number of bounded, explicitly authorized ingestion runs with
deliberately limited coverage. "Validation status" below always means
*pipeline* validation (connectivity, response normalization, transactional
storage) — never validation of the data's economic correctness, completeness,
or analytical usefulness.

OpenAI is **not** a data provider and is not cataloged here; it is a
model-provider boundary only, documented in
[docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md).

### SPY 5-minute IEX market bars

- **Source** — Alpaca market-data API (`https://data.alpaca.markets`,
  `/v2/stocks/SPY/bars`), via `AlpacaBarsClient`. Fixed request provenance:
  `feed=iex`, `adjustment=raw`, `currency=USD`.
- **Status** — connected; ten controlled ingestion runs (runs 4–10 on
  2026-09-25); **not validated as a dataset**.
- **Provenance** — (1) 2026-08-21 standalone run
  (`scripts/ingest_alpaca_bars.py`), requested interval
  2026-08-15T00:00:00Z–2026-08-20T00:00:00Z, `limit=500`, `max_pages=1`: 248
  received, 248 inserted, 0 failed. (2) 2026-08-23 orchestrated run
  (`alpaca_bars_spy_5min` job, `lookback_days=5`): 334 received, 169 inserted,
  165 existing/updated, 0 failed. (3) 2026-09-23 standalone run
  (`scripts/ingest_alpaca_bars.py --execute`, database backed up
  beforehand), requested interval 2026-08-24T00:00:00Z–2026-09-23T00:00:00Z,
  `limit=1000`, `max_pages=5`: 1,731 received, 1,731 inserted, 0
  existing/updated, 0 failed; ingestion-run status `succeeded`; post-run
  health check `healthy=True` at schema version `0009` (9 migrations). See
  `PROJECT_STATE.md`, Completed Work Log item 52.
  - **Runs (4)–(10), 2026-09-25.** Seven bounded standalone runs
    (`scripts/ingest_alpaca_bars.py --execute`, database backed up
    beforehand) covered the preregistered SPY VWAP confirmation fetch
    window 2026-02-20T00:00:00Z–2026-08-15T00:00:00Z, split by calendar
    month, with `limit=1000` and `max_pages=5`. Run IDs and bars received
    and inserted:
    - `0f18d2ae-13fc-4091-b473-8ff9fde740c7`: 545
    - `4f568365-56d1-4bdc-b2e3-91c923cbd678`: 2,015
    - `c05e0348-46d3-4ad6-b571-7b204bc3b320`: 1,847
    - `25bc38e1-d602-4f54-8965-ef2fca2395a9`: 1,739
    - `4b4839ad-4a27-43e3-9ef7-268a1f531290`: 1,772
    - `8427d52d-da63-4875-b88a-4f688b573c52`: 1,800
    - `de061400-9282-4048-a645-188ae8bb421e`: 825

    Total: 10,543 received and inserted; 0 existing/updated, 0 failed, and
    every run `succeeded`. The post-run health check reported
    `healthy=True` at schema version `0009`. See `PROJECT_STATE.md`,
    Completed Work Log item 54.
- **Schema / table** — `market_bars` (migration `0005`): `provider`, `symbol`,
  `timeframe`, `feed`, `adjustment`, `currency`, `bar_timestamp` (UTC),
  `open`/`high`/`low`/`close`/`vwap` (`DECIMAL(18,6)`, `vwap` nullable),
  `volume`/`trade_count` (`BIGINT`, `trade_count` nullable), `retrieved_at`,
  `first_ingested_at`, `last_seen_at`, `ingestion_run_id`. Primary key
  `(provider, symbol, timeframe, feed, adjustment, currency, bar_timestamp)`.
  No indicator, return, label, sentiment, prediction, or order field.
- **Coverage** — SPY only; `5Min` only; IEX feed only. The first run's 248
  rows were verified by read-only query to cover 2026-08-17T12:25:00Z through
  2026-08-19T20:00:00Z. The later orchestrated run added 169 further bars
  within its own bounded 5-day window; the combined stored set has not had an
  end-to-end coverage/gap inspection. Roughly 3–4 trading days total. No other
  symbol, timeframe, or date range is stored. **Superseding update
  (2026-09-23):** after run (3), the read-only SPY VWAP-reversion input
  builder (below) inspected 2026-08-17 through 2026-09-22 and found 27
  weekdays: 26 complete, gapless, grid-aligned 78-bar regular sessions
  (including the original five), 1 weekday with no regular-session bars
  (holiday vs. data gap not distinguished — no exchange calendar), zero
  off-grid / invalid-price-or-volume / OHLC-inconsistent / incomplete /
  contract-rejected sessions, and 120 outside-regular-session bars in that
  range. That is a regular-session completeness check for that range only,
  not an end-to-end gap inspection of every stored row or a validation of
  the data. Still SPY / `5Min` / IEX only. **Superseding update
  (2026-09-25):**
  - **Stored rows:** 12,691 in one provenance group, spanning
    2026-02-20T13:20:00Z to 2026-09-22T20:55:00Z, with zero duplicate bar
    identities. That is 2,148 unchanged discovery rows plus 10,543 new rows
    from runs (4)–(10).
  - **Nothing outside the window:** zero rows exist in the 2026-08-15/16
    gap or on or after 2026-09-23. The prospective holdout was not
    ingested.
  - **Confirmation-window builder result** (2026-02-23 to 2026-08-14,
    read-only):
    - 125 weekdays, 121 complete 78-bar regular sessions.
    - 4 weekdays with no regular-session bars; all four are market
      holidays (2026-04-03, 05-25, 06-19, 07-03).
    - Zero off-grid, invalid-price/volume, OHLC-inconsistent, incomplete,
      or contract-rejected sessions.
    - Prior-day context for 117 sessions; none for the 4 post-holiday
      sessions.
    - 1,018 outside-regular-session bars.

  This is again a regular-session completeness check for that range only,
  not a validation of the data.
- **Known limitations** — IEX is a single exchange's feed, not the
  consolidated SIP tape: narrower coverage (fewer trades, potentially
  different prices/volume). SIP connectivity has never been verified. Bars are
  provider-returned unfiltered and may include pre-market/after-hours
  observations (no regular-trading-hours filter is applied anywhere). Single
  vendor, no cross-source check. **Freshness:** this data is a static snapshot
  from mid-August 2026 and is now well past the market-context snapshot
  layer's 72-hour bars-staleness threshold — the Market Evidence Agent's
  preflight would currently abstain with `bars_stale` until a fresh ingestion.
  (Superseded in part 2026-09-23: run (3) extended coverage through the
  2026-09-22 session; it remains a static, manually refreshed snapshot that
  becomes stale again without further ingestion.)
- **Validation status** — connectivity and response normalization verified on
  IEX; ten controlled runs verified transactional storage (the second also
  idempotent overlap). No full-dataset gap analysis beyond the input
  builder's regular-session checks above, no completeness claim, no economic
  cross-check. The data feeds the SPY VWAP confirmation result
  ([docs/SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md](docs/SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md)),
  which is a research result, not a validation of this dataset.

### SPY news

- **Source** — Alpaca news endpoint (`https://data.alpaca.markets`,
  `/v1beta1/news`), via `AlpacaNewsClient`.
- **Status** — connected; two controlled ingestion runs; **not validated as a
  dataset**.
- **Provenance** — (1) 2026-08-20 standalone run
  (`scripts/ingest_alpaca_news.py`, single-symbol SPY, default limit): 10
  received, 10 inserted, 0 failed. (2) 2026-08-23 orchestrated run
  (`alpaca_news_spy` job): 10 received, 10 inserted, 0 existing/updated, 0
  failed. A 2026-08-24 read-only sanity check reported 20 stored articles for
  SPY.
- **Schema / table** — `news_articles` (migration `0004`): `provider`,
  `provider_article_id`, `headline`, `source`, `article_url`, `summary`
  (nullable), `created_at`/`updated_at` (nullable — provider
  publication/update timestamps), `related_symbols` (`VARCHAR[]`, stored
  sorted/deduplicated), `retrieved_at`, `first_ingested_at`, `last_seen_at`,
  `ingestion_run_id`. Primary key `(provider, provider_article_id)`. No
  sentiment, impact, direction, confidence, or model-output field.
- **Coverage** — SPY only; approximately 20 articles with publication
  timestamps in mid-to-late August 2026 (the exact publication-date range has
  not been inspected or recorded in this catalog). No historical archive.
- **Known limitations** — rolling recent-news window only; a single vendor's
  editorial selection; article content has never been inspected or recorded
  here. **Freshness:** static snapshot from ~2026-08-23; the News Evidence
  snapshot layer uses a 168-hour (7-day) staleness threshold, so this data is
  now stale and the News Analyst's preflight would currently abstain with
  `news_stale`. **Uncertainty rating:** `news_articles` retains source
  provenance (provider name, `article_url`, `retrieved_at`, and provider
  publication timestamps kept distinct from retrieval time), but there is no
  explicit uncertainty/verification-confidence field — a known gap against
  [SOURCE_POLICY.md](SOURCE_POLICY.md)'s "Required Fields for Every Sourced
  Item", not something already solved.
- **Validation status** — connectivity and response normalization verified;
  two controlled runs verified transactional, idempotent storage. No
  dedup-at-scale validation, no completeness claim, and this system never
  assesses whether a provider-reported claim is itself true.

### Seven-series macro observations

- **Source** — FRED series-observations API
  (`https://api.stlouisfed.org`), via `FredMacroDataClient.get_observations()`.
  Every page sends fixed `realtime_start=1776-07-04`,
  `realtime_end=9999-12-31`, `output_type=1`, `units=lin`.
- **Series** — `FEDFUNDS`, `GS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`,
  `GDPC1` (the committed Core Macro Basket).
- **Status** — connected; one to two controlled ingestion runs per series;
  **not validated as a dataset**.
- **Provenance** — `FEDFUNDS`: 2026-08-21 standalone
  (`scripts/ingest_fred_observations.py`, range 2025-08-01–2026-07-31): 12
  received, 12 inserted; plus 2026-08-23 orchestrated (`fred_fedfunds_observations`):
  3 received, 0 inserted, 3 existing/updated. `CPIAUCSL`, `PCEPI`, `UNRATE`,
  `INDPRO`, `GDPC1`: first Core Macro Basket `--execute` run (2026-08-24,
  `scripts/ingest_core_macro_basket.py`) — succeeded for these five (the same
  run's `DGS10` observations request failed with sanitized category
  `provider_error`, which led to `DGS10` being replaced by `GS10`). `GS10`:
  2026-08-25 Core Macro Basket `--execute` run: 13 received, 13 inserted, 0
  failed.
- **Schema / table** — `macro_observations` (migration `0006`): `provider`
  (fixed `"fred"`), `series_id`, `observation_date` (`DATE`),
  `realtime_start`/`realtime_end` (`DATE`), `value` (`DECIMAL(20,6)`,
  nullable), `is_missing` (`BOOLEAN`), `retrieved_at`, `first_ingested_at`,
  `last_seen_at`, `ingestion_run_id`. Primary key
  `(provider, series_id, observation_date, realtime_start, realtime_end)`. A
  `CHECK` enforces missing ⟺ (`value IS NULL` and `is_missing = TRUE`). No
  prediction, direction, or recommendation field.
- **Coverage** — verified by read-only query only for `FEDFUNDS` (12 rows,
  2025-08-01 through 2026-07-01, 0 missing; plus 3 idempotent-overlap rows
  from orchestration) and `GS10` (13 rows, 2025-07-01 through 2026-07-01, 0
  missing). The other five series were ingested in a single Core Macro Basket
  run bounded by per-series lookback ceilings (400 days for the monthly
  series; 1,100 days for the quarterly `GDPC1`); this catalog has **not**
  recorded their per-series verified row counts or coverage windows. A
  2026-08-25 seven-series Macro Analyst dry run passing preflight confirms
  each of the seven has a stored, non-stale, non-future observation with
  metadata, but that is a preflight pass, not a coverage inspection.
- **Known limitations** — roughly 12 months of monthly history per monthly
  series; `GDPC1` is quarterly. Only one vintage per observation date is
  stored, even though the primary key is vintage-aware and FRED routinely
  revises published values. No gap/quality analysis. Economic correctness
  never independently checked.
- **Validation status** — bounded fetch, response normalization, and
  transactional storage verified for at least one run per series; `FEDFUNDS`
  and `GS10` additionally re-verified by read-only query.

### Seven-series macro series metadata

- **Source** — FRED series endpoint
  (`https://api.stlouisfed.org/fred/series`), via
  `FredMacroDataClient.get_series_metadata()`.
- **Series** — the same seven Core Macro Basket series.
- **Status** — connected; ingested via the Core Macro Basket runs; **not
  validated as a dataset**.
- **Provenance** — metadata rows written through `MacroSeriesMetadataRepository`
  by `scripts/ingest_core_macro_basket.py` (six series incl. the then-`DGS10`
  metadata on 2026-08-24; `GS10` on 2026-08-25). The standalone
  `scripts/ingest_fred_series_metadata.py` has never been run live.
- **Schema / table** — `macro_series_metadata` (migration `0008`): `provider`
  (fixed `"fred"`), `series_id`, `title`, `observation_start`/`observation_end`
  (`DATE`), `frequency`/`frequency_short`, `units`/`units_short`,
  `seasonal_adjustment`/`seasonal_adjustment_short`, `last_updated`
  (`TIMESTAMP`, UTC), `popularity` (`BIGINT`), `notes` (`VARCHAR`, nullable),
  `retrieved_at_utc`, `first_ingested_at`, `last_seen_at`, `ingestion_run_id`.
  Primary key `(provider, series_id)`; a repeat ingestion refreshes every
  mutable column in place (no vintage history).
- **Coverage** — one metadata row per current basket series (all seven
  present, as implied by the passing seven-series preflight, which requires
  `metadata_available` for every requested series). Only `GS10`'s row has been
  independently verified by a read-only query (2026-08-25). A `DGS10` metadata
  row may also remain from the 2026-08-24 run that preceded its replacement by
  `GS10`; this catalog does not confirm its presence or absence.
- **Known limitations** — FRED's series endpoint always reports *current*
  metadata; this table is not a historical record of past metadata states.
  `last_updated` reflects FRED's own metadata-revision timestamp, not
  observation recency.
- **Validation status** — fetch, normalization, and transactional storage
  verified for at least one run; `GS10`'s row re-verified by read-only query.
  Not a complete or validated metadata set.

### SPY option-chain snapshots (indicative)

- **Source** — Alpaca option-chain snapshot endpoint
  (`https://data.alpaca.markets`, `GET /v1beta1/options/snapshots/SPY`), via
  `AlpacaOptionsChainClient`. Requested feed `indicative` (explicitly not
  OPRA).
- **Status** — connected; one controlled live ingestion run; **not validated
  as a dataset**.
- **Provenance** — 2026-09-14, `scripts/ingest_alpaca_options_chain.py
  --execute`: underlying `SPY`, requested feed `indicative`, one expiration
  (2026-09-18), strikes 740–790, no `call`/`put` filter. 102 contracts
  received, 102 inserted, 0 failed; one `option_chain_snapshot_batches` row
  (`outcome=succeeded`); the corresponding `ingestion_runs` row recorded
  `succeeded`. Exactly one request was made, with no retry.
- **Schema / table** — three tables, migration `0009`:
  `option_chain_snapshot_batches` (run-level provenance: requested feed,
  requested expiration/strike window, retrieval instant, `contract_count`,
  `outcome`), `option_chain_snapshots` (one row per immutable normalized
  contract observation: parsed expiration/type/strike, latest quote,
  latest trade, implied volatility, delta/gamma/theta/vega/rho), and
  `option_chain_snapshot_batch_items` (normalized batch-membership rows).
  No open-interest column on any of the three tables. See "Option-chain
  snapshot storage" above for full column/behavior detail.
- **Coverage** — SPY only; one expiration (2026-09-18); strikes 740–790; one
  retrieval instant (2026-09-14). A read-only structural audit the same day
  verified, as aggregate counts only: 102 batch-membership rows and 102
  snapshot rows, zero duplicate membership identities, every membership
  resolving to exactly one snapshot, zero orphan memberships,
  `contract_count` equal to the membership count; 51 calls / 51 puts, 1
  distinct expiration, strikes 740.000000–790.000000, zero contracts outside
  the requested bounds, zero malformed OCC symbols; quote and trade data
  present for all 102; implied volatility and each of
  delta/gamma/theta/vega/rho present for 78 and unavailable (`NULL`, never
  zero) for 24; zero negative or non-finite numeric values, zero crossed
  quotes (bid > ask), zero feed mismatches between the batch and its
  snapshots, and exactly one distinct retrieval timestamp.
- **Known limitations** — `indicative`-feed data may be delayed or modified
  by the provider and must never be described as live OPRA data. Open
  interest is not supplied by this endpoint and is not produced, inferred,
  or stored. Only one batch exists (one expiration, one 50-point strike
  window, one instant), so recurring reliability of this connector/storage
  path is not established. A future deterministic contract selector that
  requires implied volatility or Greeks must reject or omit the 24
  contracts with missing values — never substitute zero.
- **Validation status** — connectivity, response normalization, and
  transactional storage verified for one controlled live run; a read-only
  audit independently verified internal relational integrity and aggregate
  data quality for that run's stored rows. This establishes **internal
  structural consistency only** — it does not establish pricing accuracy,
  timeliness, usefulness, predictive edge, strategy validity, or
  profitability. No recommendation, selector, agent, alert, dashboard, or
  execution action has consumed this data.

### SPY intraday regime/setup features (not a stored dataset)

Phase 1 step c
([docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md)) adds
a deterministic, offline SPY intraday feature/regime engine
(`market_intelligence/market_features/spy_regime_contracts.py`,
`spy_regime_features.py`, `spy_regime_classifier.py`). It is listed here only
because it defines a new **structured feature contract** — it is
deliberately **not a dataset record** in the sense above: it makes no
network request, opens no database connection, reads no table, and writes
no row anywhere. It is a pure function over caller-supplied, already-
validated regular-session bars at the canonical 5-minute cadence — the same
`5Min` interval as the stored `alpaca_bars_spy_5min` dataset above; a bar
off the 5-minute grid is rejected, though grid alignment alone cannot
prove continuity (a 10-minute-spaced feed is just as grid-aligned as true
continuous data), so `RegimeFeatures` also reports
`session_bars_complete`/`missing_interval_count`, and the classifier
refuses to classify an incomplete session
(`RegimeEngineInput` -> `RegimeFeatures` ->
`RegimeClassificationResult`, all strict Pydantic v2 models,
`extra="forbid"`), producing the regime (`trend_continuation` /
`vwap_mean_reversion` / `range` / `event_driven` / `indeterminate`) and
scenario-horizon (`intraday_30m` / `intraday_2h` / `to_session_close` /
`next_session` / `indeterminate`) values consumed later in the Phase 1
workflow. **Implemented offline with synthetic fixtures/tests only — no
real SPY session has been classified by this engine, its thresholds are
provisional hypotheses (not evaluated against real history), and no
predictive accuracy or mean-reversion edge has been established.** See
Completed Work Log item 41 in `PROJECT_STATE.md` and
`docs/OPTIONS_DECISION_WORKFLOW.md` for full detail.

### SPY VWAP-reversion evaluation input builder (not a stored dataset)

Added 2026-09-23 (Completed Work Log item 50 in `PROJECT_STATE.md`):
`market_intelligence/orchestration/spy_vwap_reversion_input_builder.py`
(in `orchestration/`, keeping the pure `market_features/` regime modules
storage-free) plus the dry-run-first `scripts/build_spy_vwap_reversion_input.py`. Listed
here only because it derives the step-d evaluator's input contract
(`SpyVwapReversionEvaluationInput`) from the stored **SPY 5-minute IEX
market bars** above — it is **not a new dataset** and adds no table,
column, or migration. It reads `market_bars` over a read-only DuckDB
connection, restricted to the exact identity `alpaca` / `SPY` / `5Min` /
`iex` / `raw` / `USD`, for an explicit date range; keeps only complete,
gapless, grid-aligned, OHLC-consistent 78-bar regular sessions (excluded
weekdays are reported as fixed-reason counts only); supplies `prior_day`
only from a qualifying stored immediately-preceding weekday session, never
invented; and sets `same_time_historical_volume_baseline=None`,
`catalyst_state=unknown`, `breadth_state=unavailable`. Its only persisted
output is an optional local JSON file under gitignored
`data/evaluations/local/` (`--write`), never committed. **It is an
input-building tool only — its output is not an evaluation result and not
evidence of any edge. It has not yet been run against the real local
database.** **Superseding update (2026-09-23, `PROJECT_STATE.md` Completed
Work Log item 52):** it has since been run once, read-only, for 2026-08-17
through 2026-09-22 — 26 complete sessions included, 1 weekday with no
regular-session bars, no other exclusions, `prior_day` available for 24
sessions and unavailable for 2, 120 outside-regular-session bars excluded;
its input and the resulting evaluation output remain gitignored under
`data/evaluations/local/`. The same limitations as the source dataset apply (IEX-only,
no exchange-holiday/early-close calendar, limited coverage).

### SPY deterministic contract-eligibility selector (not a stored dataset)

Phase 1 step e
([docs/OPTIONS_DECISION_WORKFLOW.md](docs/OPTIONS_DECISION_WORKFLOW.md)) adds
a deterministic, offline SPY options-contract eligibility selector
(`market_intelligence/contract_selection/contracts.py`, `selector.py`,
`serialization.py`) plus a dry-run-first offline CLI
(`scripts/select_spy_option_contracts.py`). Listed here only because it
defines a new **structured input/output contract** — like the regime engine
above, it is deliberately **not a dataset record**: it makes no network
request, opens no database connection, reads no table, and writes no row
anywhere. It runs **without any AI model** and before any future strategy
agent.

It consumes exactly: SPY; one already-validated `ScenarioHorizon` from the
step-c regime engine (or `indeterminate`, which always produces an empty
eligible set before any other filter runs); one bounded option-chain batch
with one retrieval instant (`OptionChainBatch`/`OptionContractQuote`,
deliberately independent of
`data_connectors.alpaca_options_chain.OptionChainSnapshot` — this package
never imports a data connector); the underlying price and as-of time; an
optional explicit directional side (call/put); and bounded, centralized,
documented `SelectorConfig` thresholds (all provisional hypotheses, never
tuned against the step-d VWAP-reversion evaluation or the single stored
option batch). Before any per-contract filter runs, three **batch-level
gates** decide the run's fate — feed governance, then two freshness checks
— because every contract in one batch shares the same feed and the same
`retrieved_at`, so all three are batch-wide facts, not per-contract ones
(see "Feed-safety boundary" below). The freshness checks run without
`abs()`: if `retrieved_at > as_of_timestamp`, the batch is rejected under
`RejectionReason.SNAPSHOT_FROM_FUTURE`; otherwise `age =
as_of_timestamp - retrieved_at`, and if `age` exceeds
`max_snapshot_age_seconds` the batch is rejected under
`RejectionReason.SNAPSHOT_STALE`. The remaining nine filters then run per
contract, in this fixed published order: expiration/DTE; option type (only
when a directional side is explicitly supplied); strike/moneyness; delta
range; required implied volatility and Greeks; positive bid/ask;
non-crossed quote; maximum absolute and percentage spread; minimum quote
size where available. The selector produces eligible/research-only
contracts in deterministic order, bounded rejection counts by a fixed
reason enum, and one of four statuses (`eligible` / `no_eligible_contracts`
/ `research_only` / `indeterminate`). **No recommendation, ranking, score,
prediction, or trade action is produced anywhere in this package** — there
is no field for one (see `DECISION_RULES.md`, "AI does not choose an
unrestricted options contract").

**Feed-safety boundary (hardened 2026-09-16, Completed Work Log item 46).**
Operational eligibility (`SelectorStatus.ELIGIBLE`) defaults to **OPRA
only** — `SelectorConfig.allow_indicative_for_research` defaults to
`False`. An indicative-feed batch can **never** reach `ELIGIBLE`: without
explicit research permission it is rejected in full at the batch-level feed
gate (`RejectionReason.FEED_NOT_ALLOWED`, status
`no_eligible_contracts`); with `allow_indicative_for_research=True` it may
be processed, but the result status is always `research_only` and any
passing contracts go into a **separate** `research_only_contracts` field —
never `eligible_contracts`, which stays empty whenever `status != eligible`.
This separation is enforced at the schema level (`ContractSelectorResult`
validators refuse to construct an `eligible` result on a non-OPRA feed, or a
`research_only` result on a non-indicative feed), not just by selector
logic, so it is structurally impossible for a `research_only` result to
satisfy whatever eligible-set contract a future strategy agent consumes.
The `scripts/select_spy_option_contracts.py` CLI requires its own explicit
`--allow-indicative-research` flag before processing indicative data at
all, and that flag **always overrides** whatever `allow_indicative_for_research`
value the `--input` JSON file itself carries — the CLI, not the input file,
is the trust boundary.

**Open interest is not available from the current option-chain endpoint**
(see "SPY option-chain snapshots (indicative)" above) and is **never
invented, inferred, or replaced with volume anywhere in this package** — it
has no open-interest field and no open-interest-based filter. A contract
missing required implied volatility or any required Greek is rejected, not
filled with zero. A `research_only` result always carries
`feed_is_live_opra=False` and a fixed non-live/non-OPRA note plus a fixed
`research_only_not_operationally_eligible` note — it must never be
described as live OPRA data or used to support an execution claim.

**Implemented and tested offline only, with synthetic fixtures — no real
selector run has been performed against the real local database or the one
stored SPY option-chain batch, every filter threshold in `SelectorConfig` is
a provisional hypothesis, and no contract recommendation, usefulness,
pricing-accuracy, execution, or profitability claim has been made.** The
Options Strategy Agent (Phase 1 step f) does not exist and is not built by
this step, and this feed-safety fix does not authorize starting it. See
Completed Work Log items 45–46 in `PROJECT_STATE.md` and
`docs/OPTIONS_DECISION_WORKFLOW.md` for full detail.

### SPY synchronized selector-capture coordinator (not a stored dataset)

Added 2026-09-17 (Completed Work Log item 48):
`market_intelligence/orchestration/spy_contract_capture.py`
(`capture_contract_selector_input`) plus the dry-run-first
`scripts/capture_spy_contract_selector_input.py`. Listed here only because
it produces the selector's own input contract — like the selector above,
it is deliberately **not a dataset record** in the sense of a DuckDB table:
the coordinator itself opens no database connection and writes no row by
default.

It composes three already-cataloged, read-only Alpaca boundaries — SPY
5-minute bars (`AlpacaBarsClient`), an underlying-price snapshot
(`AlpacaMarketDataClient`), and one bounded `indicative`-feed option-chain
retrieval (`AlpacaOptionsChainClient`, requested only once the step-c regime
engine's regime and scenario horizon both resolve) — into exactly one
`ContractSelectorInput`, gated to `10:00`–`16:00` America/New_York on a
completed six-bar minimum. It lives in `orchestration/`, never
`market_features/`, which remains untouched and fully pure/offline. Every
client is dependency-injected, and every test exercising this module
(`test_spy_contract_capture.py`) uses fakes/mocks only — **no live request
has ever been made by importing or testing this module, and no synchronized
live capture has occurred.**

**Synchronized capture output is not yet a stored dataset.** The CLI's
`--write` flag (only meaningful with `--execute`, and only when the capture
status is `resolved`) is the only way a real, live-captured
`ContractSelectorInput` can ever be persisted, and it writes to gitignored
`data/evaluations/local/` only — the same directory step d's evaluation
outputs and step e's selector inputs/outputs already use, via the new
`contract_selection.serialization.write_input` (same no-overwrite/
no-symlink/atomic-publish guarantees as `write_record`). No real capture has
been written there as part of this change; if and when one is, it remains a
local, gitignored artifact, not a committed or cataloged dataset, and is not
by itself a selector result — a separate, explicit
`scripts/select_spy_option_contracts.py` run is still required to apply the
eligibility filters. **Implemented and tested offline only, against
mocked/fake providers — no contract recommendation, usefulness,
pricing-accuracy, execution, or profitability claim has been made, and the
Options Strategy Agent (Phase 1 step f) is not started and not authorized by
this addition.** See Completed Work Log item 48 in `PROJECT_STATE.md` and
`docs/OPTIONS_DECISION_WORKFLOW.md` for full detail.

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
