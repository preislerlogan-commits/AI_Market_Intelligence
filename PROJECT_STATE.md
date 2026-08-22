# Project State

This document is the **authoritative source of truth** for the current status
of AI Market Intelligence. It must be read before beginning any work in this
repository, and updated whenever the project's status materially changes.

Last updated: 2026-08-21

## Current Phase

**Phase 0 — Infrastructure Foundation**

The project is in initial scaffolding. Python environment and dependency
configuration are in place. Read-only Alpaca market-data provider
connectivity has been verified (a single read-only snapshot request — see
Status below). Read-only FRED macroeconomic-data provider connectivity has
also been verified (a single read-only latest-observation request — see
Status below). Read-only Alpaca news provider connectivity has also been
verified (a single read-only SPY-news request — see Status below), and one
explicitly authorized live SPY news ingestion has succeeded through the
storage pipeline (10 received, 10 inserted, 0 failed — see Status below).
A read-only Alpaca historical stock-bars connector now also exists
(`AlpacaBarsClient`). Its first authorized live connectivity check reached
Alpaca but was configured yet unsuccessful (sanitized status
`configured=True, success=False, status_category=4xx`) under the prior,
implicit-SIP default request; the connector was then hardened to
explicitly request the IEX feed, and a second authorized live connectivity
check against the hardened, explicit-IEX connector has now succeeded
(sanitized status `configured=True, success=True, status_category=2xx`,
single-symbol SPY, `5Min` timeframe, `feed=iex`, 5 bars returned — see
Status below). This confirms live connectivity and response normalization
on the IEX feed only; it does not confirm SIP connectivity, and IEX's
narrower single-exchange coverage still applies. **The explicit-IEX bars
connector remains live connectivity-verified as described above** — it has
since also been hardened to explicitly send fixed `adjustment=raw` and
`currency=USD` provenance (alongside the existing `feed=iex`). The earlier
five-bar connectivity check predated this additional hardening, but the
subsequent authorized 2026-08-21 ingestion (see Status below) exercised the
hardened `feed=iex`, `adjustment=raw`, `currency=USD` request combination
live. Connectivity/one successful ingestion run is not the same as a
validated, cataloged data pipeline: no complete, gap-free, bulk,
catalog-validated, or research-validated provider dataset exists yet. A
market-bar storage schema and
repository (`market_bars`, migration `0005`, `BarRepository`) now also
exist as schema/storage-capability infrastructure, originally covered by
tests using temporary databases only. **A separately authorized real-database
migration and ingestion run has since applied migration `0005` to the real
local database and stored a first batch of real bars** (see Status below)
— this is one controlled ingestion run, not a complete, gap-free, or
validated historical dataset. A local DuckDB storage foundation exists; the
real local database was backed up and then upgraded to schema version
`0005` (5 migrations applied) via that authorized run, and a subsequent
read-only health check reported it healthy (see Status below). **One stored
news ingestion also still exists** (10 SPY articles, see below). A
FRED historical-observations connector method, macro-observations schema
(migration `0006`), and repository now also exist as infrastructure for a
future Macro Analyst agent — in repository code and tests only. Migration
`0006` has not been applied to the real database (which remains at `0005`,
healthy for what has been applied), and no FRED observation has been
fetched from the live API or stored (see Status below). No AI analysis or
agent orchestration exists yet.

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
  the same as a validated data pipeline. No FRED observations have been
  stored or validated as a dataset yet — see `DATA_CATALOG.md`.
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
  connectivity only; it is not the same as a validated data pipeline.
  Separately, one explicitly authorized SPY ingestion stored 10 normalized
  news articles, as described below. That verifies one successful ingestion
  run; it is not yet a complete or validated news dataset — see
  `DATA_CATALOG.md`.
- A read-only Alpaca historical stock-bars connector
  (`market_intelligence/data_connectors/alpaca_bars.py`, `AlpacaBarsClient`)
  has been added, covering only Alpaca's read-only data host
  (`https://data.alpaca.markets`) and only its single-symbol historical
  bars endpoint (`/v2/stocks/{symbol}/bars`) — no order, account, or
  execution functionality, and it does not write to DuckDB. It supports
  exactly one symbol per request and only the three project-approved
  timeframes (`1Min`, `5Min`, `1Day`; case/spacing variants are normalized
  to those exact values). `start`/`end` must be strict RFC3339 timestamps
  with an explicit UTC offset, are normalized to UTC, and `start` must be
  strictly before `end`. The per-page limit and page count are both
  strictly bounded, so a malformed or endless provider pagination sequence
  cannot loop indefinitely. The normalized `Bar` model contains only
  provider, symbol, timeframe, feed, bar timestamp (UTC, kept distinct from
  local retrieval time), open/high/low/close (as `Decimal`, to avoid
  binary-float rounding artifacts in values intended for reproducible
  analysis), volume, trade_count (nullable), vwap (nullable), and
  retrieved_at — no indicators, returns, labels, sentiment, predictions, or
  trade directions. Numeric fields reject booleans, non-numeric types, and
  non-finite values (NaN/infinity); candles failing basic OHLC consistency
  are rejected. If a non-empty provider bars list contains any malformed
  bar, the entire request fails with a sanitized error rather than
  returning a misleading partial series; exact duplicate bars (by symbol,
  timeframe, feed, timestamp) are deduplicated, while conflicting
  duplicates fail the request. Results are returned in chronological
  order. A companion script, `scripts/check_alpaca_bars.py`, and a
  `check_connection` method report only sanitized connection status
  (configured, success, status category, symbol, timeframe, feed,
  adjustment, currency, bar count, oldest/newest bar timestamp) — never
  OHLCV values, credentials, URLs, raw responses, or page tokens.

  **First authorized live check and feed hardening (2026-08-20):** the
  first authorized live connectivity check (single-symbol SPY, using the
  connector's then-default request, which sent no explicit `feed`
  parameter) reached Alpaca and returned a sanitized status of
  `configured=True, success=False, status_category=4xx` — only this
  sanitized status was recorded; the raw response body, headers, and
  credentials were never printed or stored. Per Alpaca's official
  documentation, the historical single-symbol bars endpoint defaults to
  the SIP feed when no `feed` parameter is sent, and SIP access requires a
  market-data subscription; the likely cause of the observed 4xx is that
  default SIP routing combined with this project's Alpaca subscription not
  covering SIP (Alpaca returns HTTP 403 in that case), not a credentials or
  code defect. In response, the connector was hardened: it now explicitly
  sends `feed=iex` (a fixed constant, `DATA_FEED`, never a caller-supplied
  argument) on every request, including every paginated page and
  `check_connection`, with no automatic fallback between feeds, and `feed`
  is now recorded on every normalized `Bar` and `BarsConnectionStatus` for
  explicit data provenance. **Known limitation:** IEX is a single
  exchange's feed, not the consolidated SIP tape, so it reflects narrower
  market coverage (fewer trades, potentially different prices/volume) than
  SIP. The 4xx above was observed under the prior, pre-hardening default
  (SIP) request; that failed check is preserved here as an honest
  diagnostic record and is not being retracted or overwritten.

  **Second authorized live check, on the hardened explicit-IEX connector
  (2026-08-20):** a separately authorized live connectivity check was run
  against the now-hardened, explicit-IEX connector (single-symbol SPY,
  `5Min` timeframe) and succeeded, returning a sanitized status of
  `configured=True, success=True, status_category=2xx, symbol=SPY,
  timeframe=5Min, feed=iex, bar_count=5, oldest_bar_timestamp=
  2026-08-17T12:25:00Z, newest_bar_timestamp=2026-08-17T13:30:00Z`. Only
  this sanitized status was recorded — no OHLCV values, credentials, URLs,
  raw response body, or page tokens were printed or stored. **This
  confirms only that the hardened, explicit-IEX connector can reach
  Alpaca, authenticate, and normalize a small live response — it verifies
  connectivity and response normalization only.** It does not confirm SIP
  connectivity (SIP remains unverified and is not requested by this
  connector), and it is not a stored, complete, or validated historical
  bars dataset: no bars from this check were written to DuckDB (this
  connector still does not store bars — bars storage is future, separately
  reviewed work), and no coverage, gap, or quality analysis has been
  performed. As of this 2026-08-20 check, no historical bars dataset had
  been retrieved, stored, or validated (see the first authorized live bars
  ingestion below, 2026-08-21, for the first stored batch).

  **Adjustment/currency provenance hardening (2026-08-20):** alongside
  `feed=iex`, every request — including every paginated page and
  `check_connection` — now also explicitly sends `adjustment=raw` (fixed
  constant `DATA_ADJUSTMENT`; split/dividend-unadjusted prices as
  originally reported, matching this project's "no corporate-action
  adjustment applied" policy) and `currency=USD` (fixed constant
  `DATA_CURRENCY`). Neither is ever accepted as a caller-supplied argument
  anywhere in the connector, and both are recorded on every normalized
  `Bar` and `BarsConnectionStatus`, including failed/unconfigured/
  invalid-input statuses. **The explicit-IEX bars connector remains live
  connectivity-verified** as described in the two authorized checks above;
  no new live check was run against this additional adjustment/currency
  hardening as part of this 2026-08-20 entry, so as of that entry it was
  verified only by unit tests (mocked HTTP transport). This hardening has
  since been exercised live by the first authorized bars ingestion
  (2026-08-21, see below), which used `feed=iex`, `adjustment=raw`, and
  `currency=USD` throughout — one controlled ingestion run, not complete
  dataset validation.
- A market-bar storage schema and repository exist. Migration `0005`
  (`market_intelligence/storage/migrations/0005_create_market_bars.sql`)
  defines a `market_bars` table, and
  `market_intelligence/storage/bar_repository.py` (`BarRepository`) accepts
  already-normalized `Bar` objects from `AlpacaBarsClient` and writes them
  transactionally, mirroring `NewsArticleRepository`'s pattern; a manual
  ingestion script, `scripts/ingest_alpaca_bars.py`, also now exists. The
  table stores only provider-reported OHLCV/vwap data plus provenance
  (`provider`, `symbol`, `timeframe`, `feed`, `adjustment`, `currency`,
  `bar_timestamp`, `open`/`high`/`low`/`close`/`vwap` as `DECIMAL(18,6)`,
  `volume`/`trade_count` as `BIGINT`, `retrieved_at`, `first_ingested_at`,
  `last_seen_at`, `ingestion_run_id`) — no indicator, return, label,
  sentiment, prediction, recommendation, option-contract, order, or
  execution field exists. Idempotency is enforced via a `(provider, symbol,
  timeframe, feed, adjustment, currency, bar_timestamp)` primary key; an
  already-known bar identity whose OHLCV/trade_count/vwap values still
  match has only its retrieval/last-seen/run provenance refreshed, while an
  already-known bar identity whose values conflict aborts the entire batch
  (nothing partially persists) and the corresponding `ingestion_runs` row
  is recorded `failed` with a sanitized error category. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  detail. This capability was originally built and tested against
  temporary databases only (mocked bars, no live Alpaca requests).

  **First authorized real-database migration and live bars ingestion
  (2026-08-21):** `data/market_intelligence.duckdb` was backed up, and a
  separately authorized initialization run then applied migration `0005`
  to the real local database (see the local DuckDB storage foundation
  bullet below for the resulting schema version and health check).
  `scripts/ingest_alpaca_bars.py` was then run once, live, against the
  real database with an explicit, bounded request: single-symbol SPY,
  `5Min` timeframe, `feed=iex`, `adjustment=raw`, `currency=USD`,
  requested interval 2026-08-15T00:00:00Z through 2026-08-20T00:00:00Z,
  `max_pages=1`, `limit=500`. The run received 248 bars, inserted 248, had
  0 existing/updated and 0 failed, and the corresponding `ingestion_runs`
  row was recorded `succeeded`; the latest `ingestion_runs` record for
  this dataset is `('alpaca', 'bars', 'succeeded', 248, None)`. A
  subsequent read-only query of `market_bars` verified 248 stored rows
  for this symbol/timeframe/feed, covering 2026-08-17T12:25:00Z through
  2026-08-19T20:00:00Z. **This confirms one controlled ingestion run,
  transactional storage, and successful local retrieval.** It does not
  establish a complete, gap-free, consolidated, or research-validated
  historical dataset; coverage is IEX only, which is narrower than SIP;
  and it carries no claim of predictive value, strategy validity,
  production readiness, or options-trading capability. Only sanitized
  counts, status, and the verified row count/coverage window are recorded
  here — no OHLCV values are reproduced in this document.
- A local DuckDB storage foundation has been initialized
  (`market_intelligence/storage/`, `DuckDBManager` in
  `market_intelligence/storage/database.py`). It provides a versioned,
  checksum-verified, transactional migration runner and the local
  database file — no forecasting/trading tables exist. The migration code
  now defines five tables: `schema_migrations` (tracks applied migrations
  and their checksums), `ingestion_runs` (records
  provider/dataset/timing/status/record-count/sanitized-error-category/
  code-version/schema-version metadata for ingestion runs), `news_articles`
  (migration `0004`), and `market_bars` (migration `0005`, now applied to
  the real database — see below). Migration `0003` added a separate
  `schema_version` column to `ingestion_runs`, distinct from
  `code_version`. The database file defaults to
  `data/market_intelligence.duckdb` (inside this repository's own `data/`
  directory, per `Settings.project_data_path`) and is excluded from
  version control via `.gitignore`. `scripts/initialize_database.py`
  applies pending migrations and prints only the database path, schema
  version, and applied migration count; `scripts/check_database.py`
  performs a read-only health check that also verifies required columns,
  that the applied migration history matches the migration directory (no
  missing files, no checksum mismatches, no gaps/out-of-order versions),
  and that the database is at the latest available migration —
  `healthy` is false if any of these fail. On 2026-08-20 the local
  database was first initialized (schema version `0002`, 2 migrations
  applied), was upgraded to schema version `0003` (1 additional migration
  applied, 3 total) after migration `0003` was added, and — after the
  authorized live news ingestion described below — was upgraded again to
  schema version `0004` (4 migrations applied). A read-only health check
  on 2026-08-20 reported the real local database healthy at `0004`
  (required tables/columns present, migration history and checksums
  valid, at the latest available migration). After migration `0005`
  (`market_bars`) was added to this repository's migration code, a
  read-only health check against the still-`0004` real database reported
  `schema_version=0004, applied_migration_count=4, healthy=False` (`False`
  only because the database was then behind the latest available
  migration — every other health check, including migration-history
  validity and checksums, still passed); this diagnostic record is
  preserved and not retracted.

  **Migration `0005` applied to the real database (2026-08-21):** as part
  of the first authorized bars ingestion described above,
  `data/market_intelligence.duckdb` was backed up, then
  `scripts/initialize_database.py` was run and applied migration `0005`
  to the real local database. A subsequent read-only health check
  reported: `schema_version=0005`, `applied_migration_count=5`,
  `required_tables_present=True`, `required_columns_present=True`,
  `migration_history_valid=True`, `checksums_valid=True`,
  `is_current=True` (database at latest migration), `healthy=True`. **The
  real database is now at migration `0005` and reports healthy.** See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md).

  **Migration `0006` added to repository code only (2026-08-21, not
  applied):** after migration `0006` (`macro_observations`, see the
  "Historical FRED observations" bullet below) was added to this
  repository's migration code, a read-only health check against the
  still-`0005` real database reports `schema_version=0005,
  applied_migration_count=5, healthy=False` (`False` only because the
  database is now behind the latest available migration — every other
  health check, including migration-history validity and checksums, still
  passes, and every previously stored row — 248 SPY bars, 10 SPY news
  articles — remains intact and untouched). This diagnostic record is
  preserved and not retracted, mirroring how the analogous `0004`-behind-
  `0005` entry was handled above. Migration `0006` has **not** been applied
  to the real database as part of this change.
- A first persistent news-storage table, `news_articles`, has been added
  via migration `0004`
  (`market_intelligence/storage/migrations/0004_create_news_articles.sql`),
  along with a storage service
  (`market_intelligence/storage/news_repository.py`,
  `NewsArticleRepository`) that accepts already-normalized `NewsItem`
  objects from `AlpacaNewsClient` and writes them transactionally, and a
  manual ingestion script (`scripts/ingest_alpaca_news.py`). The table
  stores only provider-reported article metadata plus provenance
  (`provider`, `provider_article_id`, `headline`, `source`, `article_url`,
  `summary`, `created_at`/`updated_at` as reported by the provider,
  `related_symbols` stored sorted/deduplicated for determinism,
  `retrieved_at`, `first_ingested_at`, `last_seen_at`,
  `ingestion_run_id`) — no sentiment, impact, direction, confidence, model
  output, recommendation, or option-contract field exists. Idempotency is
  enforced via a `(provider, provider_article_id)` primary key; an
  already-known article with matching stable content (headline, source,
  URL, publication time) has its mutable fields and provenance refreshed,
  while an already-known article whose stable content conflicts aborts the
  entire batch (nothing partially persists) and the corresponding
  `ingestion_runs` row is recorded `failed` with a sanitized error
  category. See [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)
  for full detail. On 2026-08-20, one explicitly authorized live SPY news
  ingestion was run via `scripts/ingest_alpaca_news.py` against the real
  local database and succeeded: 10 articles received, 10 inserted, 0
  updated, 0 failed, and the corresponding `ingestion_runs` row recorded
  status `succeeded`. This run upgraded the real local database file to
  schema version `0004`. Only sanitized counts and status are recorded
  here — no article headline, URL, or summary content is reproduced in
  this document. This confirms the storage pipeline succeeded for one
  ingestion run; it is not the same as a validated, cataloged news
  dataset — see `DATA_CATALOG.md` for the dataset-level record and
  required-fields status.
- The `market_bars` table exists (migration `0005`, applied to the real
  database — see above) and now holds one authorized ingestion's worth of
  SPY bars (see above). A macro-observation table, connector method, and
  repository now also exist in repository code and tests only (migration
  `0006`, `market_intelligence/storage/migrations/0006_create_macro_observations.sql`,
  `MacroObservationRepository`, `scripts/ingest_fred_observations.py`) — see
  the "Historical FRED observations" bullet below. Migration `0006` has
  **not** been applied to the real local database, which remains at schema
  version `0005`; no FRED observation has been fetched or stored live, and
  no complete or validated macro dataset exists.
- **Historical FRED observations (2026-08-21, code/tests only — not run
  live).** `FredMacroDataClient` (unchanged single-latest-observation
  connectivity check preserved) now also exposes `get_observations()`: a
  strictly validated, bounded, paginated fetch of historical observations
  for one series over an explicit `observation_start`/`observation_end`
  calendar-date range. `series_id`, `observation_start`, and
  `observation_end` are validated before any request is built (calendar-date
  shape, real calendar date, `observation_start <= observation_end`); the
  per-page limit and page count are both strictly bounded (no caller can
  raise the fixed ceiling), sorted ascending. Every page also explicitly
  sends fixed, non-overridable `realtime_start=1776-07-04`,
  `realtime_end=9999-12-31`, `output_type=1`, and `units=lin`: FRED
  documents that an omitted realtime_start/realtime_end defaults both to
  *today's date* rather than the observation's actual reported revision
  window, so requesting the complete real-time period explicitly is what
  makes `realtime_start`/`realtime_end` describe FRED's real
  revision/vintage window and keeps the storage identity (below) stable and
  meaningful across ingestion runs on different retrieval days, rather than
  merely reflecting the retrieval date. Pagination metadata is hardened:
  every page's `offset` must be a plain nonnegative integer exactly equal
  to the offset requested, every page's `count` must be a plain nonnegative
  integer identical across all pages, and a short/empty page is only
  accepted as complete once `offset + returned` has actually reached
  `count` — any offset/count inconsistency or mismatch, or a short/empty
  page while records remain outstanding, fails the whole request with a
  sanitized error instead of silently returning an incomplete series, and
  exceeding the hard page-count ceiling likewise fails safely. FRED's `"."`
  missing-observation marker is preserved as `value=None, is_missing=True`;
  every other value is parsed as a finite `Decimal` from FRED's own string
  representation (non-finite/malformed values are rejected). Any malformed
  observation in a non-empty response fails the whole fetch rather than
  returning a misleading partial series; exact duplicate observations
  (matched on series_id, observation_date, and the full realtime_start/
  realtime_end vintage) are deduplicated, conflicting duplicates fail the
  fetch, and results are returned in deterministic chronological order.
  Errors and statuses never include the API key, request URL/query
  parameters, raw responses, or observation values. A macro-observations
  schema (migration `0006`, `macro_observations` table), a hardened,
  transactional `MacroObservationRepository` (mirroring `BarRepository`'s
  validate-before-write, single-transaction, conflict-rollback pattern, with
  an identity of `(provider, series_id, observation_date, realtime_start,
  realtime_end)` so FRED revisions/vintages are preserved rather than
  collapsed), and a one-shot manual ingestion script
  (`scripts/ingest_fred_observations.py`) now all exist, covered by tests
  using temporary DuckDB files and mocked HTTP transports only. The
  repository also now strictly enforces `provider == "fred"` (the fixed
  `DEFAULT_PROVIDER` constant): any alternate, blank, malformed, or
  non-string `provider` argument is rejected before any connection is
  opened, any `ingestion_runs` row is written, or any observation is
  written, and the rejected value is never echoed. **None of
  this has been run live**: migration `0006` has not been applied to the
  real database (which remains at `0005`, healthy for everything already
  applied, but no longer at the latest available migration — see below), no
  FRED observation has been fetched from the live API using this new
  method, and no observation has been stored. FRED connectivity itself was
  previously verified via the pre-existing single-latest-observation check
  (2026-08-20, see below); that remains the only live-verified FRED
  interaction. Historical-observation fetching and macro storage are
  code/test-verified only.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- **This is an independent project.** It does not depend on, read from, or
  otherwise access the separate ORB_Project. Read-only Alpaca provider
  connectivity has been verified; one authorized ingestion run stored 10
  normalized SPY news articles, and a separate authorized ingestion run
  stored 248 normalized SPY bars (IEX, `5Min`, see above). No FRED
  observations have been stored, and no complete, gap-free, or validated
  provider dataset has been cataloged yet. The settings layer's only
  data-path configuration is `project_data_path`, which defaults to this
  repository's own `data/` directory. No code in this repository may
  access files outside the repository unless the user explicitly
  authorizes a specific source.

## Next Planned Work

1. Data connector design — read-only Alpaca market-data, Alpaca news,
   Alpaca historical bars, and FRED connectors now exist (see above).
   Verified schema/provenance details belong in `DATA_CATALOG.md` once
   bulk data is actually pulled and inspected, not just a connectivity
   check.
2. Provider configuration — Alpaca and FRED credential handling (via
   `.env`, never committed) is in place.
3. First connection tests — done for Alpaca market data (read-only
   snapshot connectivity check) and FRED (read-only latest-observation
   connectivity check), recorded in `DATA_CATALOG.md`/`PROJECT_STATE.md`.
   Done live for Alpaca news (one authorized SPY-news request, see above).
   Alpaca historical bars was checked twice with separate authorization:
   the first implicit-SIP check failed with a sanitized 4xx, and the second
   explicit-IEX check succeeded with a sanitized 2xx. Connectivity and
   response normalization are verified on IEX only. No bars from either of
   those two connectivity checks were stored. A separate, later authorized
   2026-08-21 ingestion (see item 7 below) subsequently stored 248 bars;
   that is one controlled ingestion run, not a complete or validated bars
   dataset.
4. Database initialization — done (see above): the local DuckDB storage
   foundation is initialized under `data/`, now at schema version `0005`
   (`market_bars` applied) after the authorized real-database migration
   run described above; the database was backed up before that migration
   was applied.
5. News storage — done (see above): `news_articles` schema, storage
   service, and manual ingestion script exist, and one authorized live
   ingestion has succeeded (10 received, 10 inserted, 0 failed). This
   confirms the storage pipeline for one run; a validated, cataloged news
   dataset (per `DATA_CATALOG.md`'s Required Fields) is still separate,
   future work.
6. Historical bars connector — done (see above): a read-only,
   single-symbol historical-bars connector exists and is unit-tested
   (`AlpacaBarsClient`) and does not store bars in DuckDB. Its first
   authorized live check reached Alpaca but failed (sanitized 4xx) under
   the connector's prior default (implicit SIP) request — preserved above
   as an honest diagnostic record; the connector was then hardened to
   explicitly request the IEX feed on every request, and a second
   authorized live check against the hardened connector has now succeeded
   (sanitized 2xx, SPY, `5Min`, `feed=iex`, 5 bars). This verifies live
   connectivity and response normalization on IEX only — not SIP. The
   connector was also hardened to explicitly fix `adjustment=raw`/
   `currency=USD` (see above); this hardening has since been exercised by
   a live ingestion run (see item 7 below), in addition to unit tests.
7. Market-bar storage — done (see above): `market_bars` schema (migration
   `0005`), `BarRepository`, and `scripts/ingest_alpaca_bars.py` exist,
   are covered by tests using temporary databases and mocked bars, and
   migration `0005` has now been applied to the real database. A first
   authorized live ingestion has succeeded (SPY, `5Min`, `iex`, `raw`,
   `USD`: 248 received, 248 inserted, 0 failed; 248 rows verified stored,
   covering 2026-08-17T12:25:00Z through 2026-08-19T20:00:00Z). This
   confirms one controlled ingestion run and transactional storage; it is
   not a complete, gap-free, or research-validated historical bars
   dataset. Remaining future work: a validated, cataloged historical bars
   dataset per `DATA_CATALOG.md`'s Required Fields (broader coverage,
   direct inspection of stored data, gap/quality analysis).
8. Reviewed data contracts for actual provider data — versioned migrations
   and storage/repositories now exist for both market bars (see above) and
   macro observations (migration `0006`, `MacroObservationRepository`, see
   the "Historical FRED observations" bullet above) in repository code and
   tests. Applying migration `0006` to the real database and running a
   first authorized live FRED observations ingestion both remain future,
   separately authorized work.
9. Macro-analyst agent groundwork — the FRED historical-observations
   connector and storage pipeline (see above) exist as infrastructure only.
   No AI agent, prediction, sentiment analysis, options logic, or trading
   execution has been built on top of it, and none is planned as part of
   this infrastructure change.

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
