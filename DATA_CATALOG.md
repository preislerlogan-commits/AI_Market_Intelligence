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
run, not scheduling, continuous operation, or dataset completeness. A
separately authorized live Core Macro Basket run (2026-08-25) has since
stored 1 `GS10` metadata row and 13 `GS10` observation rows via
`scripts/ingest_core_macro_basket.py` (see "Local Storage" below); this
confirms one bounded, controlled ingestion for one series, not dataset
completeness. Each
collection verifies one ingestion run; none is a complete, gap-free, or
validated dataset — connectivity, or a single ingestion run, is not the
same as a validated data pipeline. No code in
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

**Macro series metadata: code/tests only, not yet run live (2026-08-24).** A
narrow, reviewed data contract now also exists for FRED series-level
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
Macro Analyst never interprets an unlabeled number. **As of this entry, this
capability exists in code and tests only** (temporary DuckDB files, mocked
HTTP transports -- no live FRED request, no write to the real database).
Migration `0008` has not been applied to the real local database, which
remains at migration `0007` and unchanged. See
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

No forecast or trade table has been created — each requires its own
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
