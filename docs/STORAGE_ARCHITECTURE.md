# Storage Architecture

This document describes the local DuckDB storage foundation added under
`market_intelligence/storage/`. It covers infrastructure only — see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Scope

This foundation defines eleven tables in code. **As of 2026-08-24, the first
seven were applied to the real database and the eighth
(`macro_series_metadata`, migration `0008`) existed in code and tests only,
with the real database remaining at migration `0007`.** That status has
since been superseded: a read-only verification performed 2026-08-25
(following an authorized live Core Macro Basket `GS10` ingestion, which
requires the database already healthy at exactly schema version `0008`
before making any network request) confirmed the real local database is
now at schema version `0008` (8 migrations applied), healthy=True. **The
first eight tables, including `macro_series_metadata`, are applied to the
real database** (see below). The ninth, tenth, and eleventh tables,
`option_chain_snapshot_batches`, `option_chain_snapshots`, and
`option_chain_snapshot_batch_items` (all migration `0009`), were added
2026-08-28 (normalized to a three-table design 2026-09-14, after a
pre-commit review found that a mutable `ingestion_run_id` on
`option_chain_snapshots` let re-ingestion silently move a historical
snapshot's batch membership without updating the original batch's
`contract_count`). **As originally written, migration `0009` had not been
applied to the real local database, which remained at migration `0008`, and
no option-chain data had been stored; that statement is preserved here as an
honest, time-scoped diagnostic record and is not retracted. Migration `0009`
has since been applied to the real local database (2026-09-14, backed up
beforehand), and one authorized live ingestion has stored one batch of 102
SPY option-chain contracts through it** — see “Option-chain snapshot
storage” below.

- **`schema_migrations`** — tracks which versioned migrations have been
  applied, with a checksum of each migration file's content.
- **`ingestion_runs`** — records metadata about each attempted
  data-ingestion run (provider, dataset, timing, status, record count, a
  sanitized error category, and both `code_version` — which version of the
  ingestion code ran — and `schema_version` — which database schema
  version was active at run time, added in migration `0003`). It does not
  store any provider data itself.
- **`news_articles`** (added in migration `0004`) — stores normalized news
  articles with provenance and idempotency. See "News article storage"
  below. This is the first table in this database to store actual provider
  data; it is schema/storage infrastructure only — no persistent news
  dataset is validated here until an explicitly authorized live ingestion
  succeeds and is recorded in `DATA_CATALOG.md`.
- **`market_bars`** (added in migration `0005`) — stores normalized
  historical stock bars with provenance and idempotency. See "Market-bar
  storage" below. Migration `0005` has been applied to the real local
  database (backed up beforehand), and one explicitly authorized live
  ingestion has succeeded and stored 248 SPY bars (IEX, `5Min`, `raw`,
  `USD`) through it — see "Status" in `PROJECT_STATE.md` and the
  bars entry in `DATA_CATALOG.md`. This confirms one controlled ingestion
  run and transactional storage; it is not a complete, gap-free, or
  research-validated bars dataset.
- **`macro_observations`** (added in migration `0006`) — stores normalized
  historical FRED macroeconomic observations with provenance and
  idempotency. See "Macro-observation storage" below. Migration `0006` has
  been applied to the real local database (backed up beforehand), and one
  explicitly authorized live ingestion has succeeded and stored 12
  FEDFUNDS observations through it — see "Status" in `PROJECT_STATE.md`
  and the macro-observations entry in `DATA_CATALOG.md`. This confirms one
  controlled ingestion run and transactional storage; it is not a
  complete, gap-free, or research-validated macro dataset.

- **`orchestration_runs` / `orchestration_job_runs`** (added in migration
  `0007`) — the persistent ingestion-orchestration audit trail. Originally
  built and tested against temporary databases only; migration `0007` has
  since been applied to the real local database (2026-08-23, backed up
  beforehand), and one authorized live orchestration run has been recorded
  through it (`orchestration_run_id=
  e63d931e-8957-4357-93ee-ba7076b079d8`, overall status `succeeded`,
  all three `orchestration_job_runs` rows `succeeded`). This confirms one
  controlled orchestration run and its persistent audit record; it is not
  scheduling or continuous/unattended operation. See
  [docs/INGESTION_ORCHESTRATION.md](INGESTION_ORCHESTRATION.md) for full
  detail.
- **`macro_series_metadata`** (added in migration `0008`) -- stores
  normalized FRED series-level metadata (title, units, frequency, seasonal
  adjustment, popularity, notes, observation date range, last-updated
  timestamp) with provenance and idempotency. See "Macro series-metadata
  storage" below. **As originally built, this table, its repository, and
  its ingestion script existed in code and tests only, with migration
  `0008` not yet applied to the real database (accurate as of
  2026-08-24). Migration `0008` has since been applied to the real local
  database, and one explicitly authorized live ingestion (`GS10`, via the
  Core Macro Basket, 2026-08-25) has succeeded and stored 1 metadata row
  through it** -- see "Status" in `PROJECT_STATE.md` (item 25) and the
  `GS10` entry in `DATA_CATALOG.md`. This confirms one controlled
  ingestion for one series; it is not a complete, gap-free, or
  research-validated metadata dataset.

- **`option_chain_snapshot_batches`**, **`option_chain_snapshots`**, and
  **`option_chain_snapshot_batch_items`** (all added in migration `0009`) — a
  normalized, three-table SPY option-chain design. `option_chain_snapshot_batches`
  stores exactly one row per successfully stored chain retrieval (run-level
  provenance: requested feed, requested expiration/strike window, requested
  option type, retrieval instant, contract count — including zero for a
  successful empty chain). `option_chain_snapshots` stores one row per
  *immutable* normalized contract observation (contract symbol, parsed
  expiration/type/strike, latest quote, latest trade, implied volatility, and
  Greeks) — its `ingestion_run_id` records only the run that first inserted
  the row and is never reassigned by a later re-observation.
  `option_chain_snapshot_batch_items` is the normalized batch-membership
  table: one row per `(ingestion_run_id, snapshot identity)` pair for every
  contract a successful non-empty batch actually returned, so a batch's
  recorded `contract_count` and its truthful membership can never drift apart
  even when the same snapshot identity is re-ingested into a later batch. See
  "Option-chain snapshot storage" below. **All three tables, the repository
  (`market_intelligence/storage/option_chain_snapshot_repository.py`), and
  the ingestion script (`scripts/ingest_alpaca_options_chain.py`) existed in
  code and tests only** (temporary DuckDB files, mocked HTTP transports — no
  live option-chain request, no write to the real database) as originally
  written; that statement is preserved here as an honest, time-scoped
  diagnostic record and is not retracted. **Migration `0009` has since been
  applied to the real local database (2026-09-14), and one authorized live
  ingestion has stored one batch of 102 SPY option-chain contracts through
  it** -- see “Option-chain snapshot storage” below. Open interest is **not** a column on any of the three
  tables: Alpaca's option-chain snapshot endpoint does not supply it, and it
  is not inferred.

No forecast or trade tables exist yet. Those each require a separate,
reviewed data contract before they are added as their own versioned
migration.

## News article storage

`news_articles` (`market_intelligence/storage/migrations/0004_create_news_articles.sql`)
stores only provider-reported article metadata plus this project's own
provenance/ingestion bookkeeping — never sentiment, impact, direction,
confidence, model output, recommendations, option-contract data, orders,
credentials, request headers, or raw API responses.

Columns: `provider`, `provider_article_id`, `headline`, `source`,
`article_url`, `summary` (nullable), `created_at`/`updated_at` (nullable —
the provider's reported publication/update timestamps, kept distinct from
retrieval/ingestion time), `related_symbols` (a `VARCHAR[]`, always stored
sorted and deduplicated so the on-disk representation is deterministic
regardless of the order the provider reported symbols in), `retrieved_at`
(when the connector fetched the response that produced the currently-stored
values), `first_ingested_at` (set once, on first insert, never changed
afterward), `last_seen_at` (refreshed every time the article is
re-ingested), and `ingestion_run_id` (the `ingestion_runs.run_id` of the
run that most recently wrote this row — recorded, not enforced as a
DuckDB foreign key, so that `ingestion_runs` can still evolve its own
schema independently). The primary key is `(provider, provider_article_id)`,
which is this table's idempotency mechanism: re-ingesting an already-known
article never creates a duplicate row.

`market_intelligence/storage/news_repository.py` (`NewsArticleRepository`)
is the only code that writes to this table. It accepts already-normalized
`NewsItem` objects (from
`market_intelligence/data_connectors/alpaca_news.py`) — it makes no network
requests itself and does not apply migrations; the database must already be
initialized to at least migration `0004`. Every call to
`store_news_items()` first requires `created_at`, `updated_at`, and
`retrieved_at` on every item to be valid RFC3339 timestamps (explicit `Z`
or numeric offset; `created_at`/`updated_at` may be `None`) before any
database write, rejecting naive, date-only, malformed, blank, or
non-string timestamps with a sanitized `NewsStorageValidationError` and
normalizing valid timestamps to UTC. It then records a `running`
`ingestion_runs` row, and writes the entire batch *plus* the final
`succeeded` `ingestion_runs` status update inside one DuckDB transaction:
an unseen article is inserted; an already-known article whose stable
identity fields (headline, source, URL, publication time) still match has
its mutable fields (summary, `updated_at`, `related_symbols`) and
provenance (`retrieved_at`, `last_seen_at`, `ingestion_run_id`) refreshed;
an already-known article whose stable fields *conflict* with the incoming
value, or a failure while recording the final `succeeded` status itself,
aborts the entire batch — nothing in that batch persists, and no article
changes are left associated with a `running` or `failed` run — and the
`ingestion_runs` row is separately recorded as `failed` with a sanitized
`error_category` (`content_conflict` or `storage_error`), never a raw
exception message or article content. If recording that `failed` status
itself also fails, a sanitized `NewsStorageError` is raised instead of
returning a result. The returned `NewsStorageResult` reports only
sanitized counts (`received`, `inserted`, `updated`, `failed`) and the
ingestion-run id/status — never article text, URLs, database internals, or
credentials.

`scripts/ingest_alpaca_news.py` is the one manual ingestion entry point: it
makes at most one explicit, read-only `AlpacaNewsClient.get_news()` request
for a single, strictly-validated command-line symbol/limit (default `SPY`,
limit `10`), then stores the results through `NewsArticleRepository`.
Invalid `--symbol`/`--limit` values are rejected before any network request
is constructed. It has not been run live as part of adding this storage
layer — see "Status" in `PROJECT_STATE.md`.

## Market-bar storage

`market_bars` (`market_intelligence/storage/migrations/0005_create_market_bars.sql`)
stores only provider-reported OHLCV/vwap data plus this project's own
provenance/ingestion bookkeeping — never indicators, returns, labels,
sentiment, predictions, recommendations, option-contract data, orders,
execution fields, credentials, request headers, or raw API responses.

Columns: `provider`, `symbol`, `timeframe`, `feed`, `adjustment`,
`currency`, `bar_timestamp` (the provider's reported bar timestamp, UTC,
kept distinct from retrieval/ingestion time), `open`/`high`/`low`/`close`
(each `DECIMAL(18,6)`, so values are stored exactly as decimal quantities
rather than binary-float approximations — matching the connector's use of
Python `Decimal`), `volume` (`BIGINT`), `trade_count` (`BIGINT`, nullable),
`vwap` (`DECIMAL(18,6)`, nullable — `trade_count`/`vwap` are nullable
consistent with `AlpacaBarsClient`, which accepts either as legitimately
absent for some provider responses/subscription tiers), `retrieved_at`
(when the connector fetched the response that produced the currently-stored
values), `first_ingested_at` (set once, on first insert, never changed
afterward), `last_seen_at` (refreshed every time the bar is re-ingested),
and `ingestion_run_id` (the `ingestion_runs.run_id` of the run that most
recently wrote this row — recorded, not enforced as a DuckDB foreign key,
for the same reason as `news_articles.ingestion_run_id`). The primary key is
`(provider, symbol, timeframe, feed, adjustment, currency, bar_timestamp)`
— this table's idempotency mechanism; `adjustment` and `currency` are part
of the identity, not just descriptive columns, since a differently adjusted
or differently denominated bar for the same timestamp is a genuinely
different value series, even though this project's connector currently only
ever produces `adjustment='raw', currency='USD'` bars.

`market_intelligence/storage/bar_repository.py` (`BarRepository`) is the
only code that writes to this table. It accepts already-normalized `Bar`
objects (from `market_intelligence/data_connectors/alpaca_bars.py`) — it
makes no network requests itself and does not apply migrations; the
database must already be initialized to at least migration `0005`. Every
call to `store_bars()` first strictly validates every item before any
database write, rejecting: non-`Bar` elements; a `feed`/`adjustment`/
`currency`/`timeframe` outside this project's fixed, project-approved
values (`iex`/`raw`/`USD`/one of `1Min`,`5Min`,`1Day`); a naive,
timezone-free, or otherwise malformed `timestamp`/`retrieved_at`; a
non-`Decimal`, non-finite `open`/`high`/`low`/`close`/`vwap`; a negative or
non-integer `volume`/`trade_count`; and a candle whose OHLC values are
internally inconsistent — with a sanitized `BarStorageValidationError` and
no `ingestion_runs` row created for this failure mode. It then records a
`running` `ingestion_runs` row, and writes the entire batch *plus* the final
`succeeded` `ingestion_runs` status update inside one DuckDB transaction: an
unseen bar identity is inserted; an already-known bar identity whose
OHLCV/`trade_count`/`vwap` values still match has its retrieval/last-seen/
run provenance (`retrieved_at`, `last_seen_at`, `ingestion_run_id`)
refreshed, never its price/volume values; an already-known bar identity
whose values *conflict* with the incoming value, or a failure while
recording the final `succeeded` status itself, aborts the entire batch —
nothing in that batch persists, and no bar changes are left associated with
a `running` or `failed` run — and the `ingestion_runs` row is separately
recorded as `failed` with a sanitized `error_category` (`content_conflict`
or `storage_error`), never a raw exception message or OHLCV content. If
recording that `failed` status itself also fails, a sanitized
`BarStorageError` is raised instead of returning a result. A duplicate `Bar`
within one incoming batch is handled deterministically without any
separate duplicate-tracking code: every bar is written through the same
DuckDB connection inside one transaction, so a second occurrence of the
same identity later in the same batch sees the first occurrence's
not-yet-committed row exactly as it would see an already-committed row from
a prior call. The returned `BarStorageResult` reports only sanitized counts
(`received`, `inserted`, `existing_or_updated`, `failed`) and the
ingestion-run id/status — never OHLCV values, individual bar timestamps,
database internals, or credentials.

`scripts/ingest_alpaca_bars.py` is the one manual ingestion entry point: it
makes at most one bounded, explicit, read-only `AlpacaBarsClient.get_bars()`
request for a single, strictly-validated command-line symbol/timeframe/
window (default `SPY`, `5Min`, and — if `--start`/`--end` are omitted — a
small, fully completed historical window ending at the start of the current
UTC day, so the default never includes a partial/live bar), then stores the
results through `BarRepository`. Invalid `--symbol`/`--timeframe`/`--start`/
`--end`/`--limit`/`--max-pages` values are rejected before any network
request is constructed or any database write occurs.

**Dry-run by default (2026-09-23 safety/operability correction).** A default
invocation only validates and normalizes its arguments (enforcing every
connector bound) and prints a sanitized request plan — `mode`, `configured:
not_checked`, symbol, timeframe, normalized start/end, `limit`, `max_pages`,
`max_total_rows` (`limit × max_pages`), the fixed `feed`/`adjustment`/
`currency`, and `request_planned: False`. It constructs no `Settings`, bars
client, `BarRepository`, or database connection, makes zero provider
requests, and writes nothing. Only an explicit `--execute` performs the
provider request and ingestion, with unchanged behavior (same pagination
ceilings, all-or-nothing batch transaction, sanitized errors, no retries).
Invalid input is rejected identically in both modes before any I/O. Example:

```
python scripts/ingest_alpaca_bars.py --symbol SPY --timeframe 5Min   --start 2026-08-24T00:00:00Z --end 2026-09-23T00:00:00Z   --limit 1000 --max-pages 5
```

prints the plan only; appending `--execute` to the same command performs the
live request and write.

The historical runs recorded below predate this flag; they ran the
then-default execute path, which today requires `--execute`.

**First authorized live run (2026-08-21):** `data/market_intelligence.duckdb`
was backed up, migration `0005` was applied to the real database, and this
script was then run once, live, against the real database: single-symbol
SPY, `5Min` timeframe, `feed=iex`, `adjustment=raw`, `currency=USD`,
requested interval 2026-08-15T00:00:00Z through 2026-08-20T00:00:00Z,
`max_pages=1`, `limit=500`. 248 bars were received, 248 inserted, 0
existing/updated, 0 failed, and the corresponding `ingestion_runs` row was
recorded `succeeded`; the latest `ingestion_runs` record for this dataset
is `('alpaca', 'bars', 'succeeded', 248, None)`. A subsequent read-only
query verified 248 stored rows covering 2026-08-17T12:25:00Z through
2026-08-19T20:00:00Z. This confirms one controlled ingestion run and
transactional storage; it is not a complete, gap-free, or
research-validated bars dataset — see "Status" in `PROJECT_STATE.md` and
the bars entry in `DATA_CATALOG.md`.

**First authorized live orchestration run (2026-08-23):** the
`alpaca_bars_spy_5min` orchestration job (see
[docs/INGESTION_ORCHESTRATION.md](INGESTION_ORCHESTRATION.md)) ran this
same `feed=iex`/`adjustment=raw`/`currency=USD` request pattern through
`BarRepository` as part of a broader, explicitly authorized orchestrated
run: 334 bars received, 169 inserted, 165 existing/updated, 0 failed. The
165 existing/updated rows reflect idempotent overlap with the 248 bars
already stored above, within this job's own bounded request window — not
new distinct dataset coverage.

**Authorized bounded run with `--execute` (2026-09-23):** the database was
backed up before execution, and this script was run once with `--execute`
against the real database: SPY, `5Min`, `feed=iex`, `adjustment=raw`,
`currency=USD`, requested interval 2026-08-24T00:00:00Z through
2026-09-23T00:00:00Z, `limit=1000`, `max_pages=5`. 1,731 bars were
received, 1,731 inserted, 0 existing/updated, 0 failed, and the
ingestion-run record was `succeeded`. A post-run health check reported
schema version `0009`, 9 migrations, required tables/columns present,
valid migration history and checksums, latest migration applied, and
`healthy=True`. The earlier stored sessions remain present. This is one
more bounded, controlled run — not a complete, gap-free, or
research-validated bars dataset; see `PROJECT_STATE.md` (Completed Work Log
item 52) and the bars entry in `DATA_CATALOG.md`.

**Seven bounded runs with `--execute` for the SPY VWAP confirmation window
(2026-09-25).**
- **Setup.** The database was backed up before execution. The script was
  then run seven times, one calendar-month chunk each, covering
  2026-02-20T00:00:00Z through 2026-08-15T00:00:00Z: SPY, `5Min`,
  `feed=iex`, `adjustment=raw`, `currency=USD`, `limit=1000`,
  `max_pages=5`.
- **Outcome.** 10,543 bars were received and inserted in total, with
  0 existing/updated and 0 failed. All seven ingestion-run records were
  `succeeded`; their run IDs are listed in `PROJECT_STATE.md` (Completed
  Work Log item 54) and `DATA_CATALOG.md`.
- **Health.** A post-run health check again reported schema version `0009`,
  9 migrations, and `healthy=True`. The earlier discovery-window rows
  (2026-08-17 → 2026-09-22) were unchanged, and nothing on or after
  2026-09-23 was ingested.
- **Scope.** These are bounded, controlled runs for a preregistered research
  sample, not a complete, gap-free, or research-validated bars dataset.

## Macro-observation storage

`macro_observations`
(`market_intelligence/storage/migrations/0006_create_macro_observations.sql`)
stores only FRED's own reviewed observation fields plus this project's own
provenance/ingestion bookkeeping -- never prediction, direction, sentiment,
impact, recommendation, option-contract, order, execution, credentials,
request headers, or raw API responses. **This table, its repository, and
its ingestion script were originally built and tested against temporary
databases only. Migration `0006` has since been applied to the real local
database, and one explicitly authorized live ingestion has succeeded**
(see "Scope" above and the "First authorized live run" note below).

Columns: `provider` (fixed `"fred"`), `series_id`, `observation_date`
(`DATE`, the calendar date FRED's observation itself describes),
`realtime_start`/`realtime_end` (`DATE`, FRED's own reported revision/
vintage window for this specific observation value), `value`
(`DECIMAL(20,6)`, nullable -- a deliberately chosen, bounded supported
range for this project's currently-ingested series, not a claim of
universal coverage of every value any FRED series could ever report),
`is_missing` (`BOOLEAN`), `retrieved_at` (when
the connector fetched the specific API response that produced the
currently-stored values), `first_ingested_at` (set once, on first insert,
never changed afterward), `last_seen_at` (refreshed every time the
observation is re-ingested), and `ingestion_run_id` (the
`ingestion_runs.run_id` of the run that most recently wrote this row --
recorded, not enforced as a DuckDB foreign key, for the same reason as
`news_articles.ingestion_run_id` and `market_bars.ingestion_run_id`). The
primary key is `(provider, series_id, observation_date, realtime_start,
realtime_end)` -- unlike a naive `(series_id, observation_date)` key, this
deliberately includes FRED's revision/vintage window as part of the
identity, so a later revision of an already-stored observation (FRED
routinely revises published values, e.g. GDP) is preserved as its own row
rather than silently overwriting an earlier vintage's value. **This
identity is only stable and meaningful because `FredMacroDataClient.
get_observations()` always explicitly requests FRED's complete real-time
period (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`,
`output_type=1`) and `units=lin` on every page, as fixed, non-overridable
request parameters.** FRED documents that an omitted realtime_start/
realtime_end defaults both to *today's date*, not to the observation's
actual reported revision window; without the explicit request, repeating
the same ingestion on a different day could create new rows for values
FRED has not actually revised, falsely describing distinct retrieval dates
as distinct revisions. A `CHECK`
constraint enforces that a missing observation always has `value IS NULL`
and `is_missing = TRUE`, and a present observation always has `value IS NOT
NULL` and `is_missing = FALSE`.

`market_intelligence/storage/macro_observation_repository.py`
(`MacroObservationRepository`) is the only code that writes to this table.
It accepts already-normalized `FredObservation` objects (from
`market_intelligence/data_connectors/fred_macro_data.py`'s
`get_observations()`) -- it makes no network requests itself and does not
apply migrations; the database must already be initialized to at least
migration `0006`. Every call to `store_observations()` first strictly
validates every item before any database write, rejecting: a `provider`
argument that is not exactly the fixed value `"fred"` (any alternate,
blank, malformed, or non-string value is rejected before any connection is
opened, any `ingestion_runs` row is written, or any observation is
written, and the rejected value is never echoed); non-
`FredObservation` elements; an item `provider` other than the requested
provider; an unnormalized `series_id`/`observation_date`/`realtime_start`/
`realtime_end`; a `value`/`is_missing` pairing that is inconsistent (a
missing observation with a non-`None` value, or a present observation with
a `None`, non-`Decimal`, or non-finite value); a `value` exceeding
`DECIMAL(20,6)`'s precision or range; and a naive, timezone-free, or
otherwise malformed `retrieved_at` -- with a sanitized
`MacroObservationStorageValidationError` and no `ingestion_runs` row
created for this failure mode. An empty batch is also rejected this way
(existing repository conventions do not clearly establish empty-batch
support, so the stricter default applies here). It then records a
`running` `ingestion_runs` row, and writes the entire batch *plus* the
final `succeeded` `ingestion_runs` status update inside one DuckDB
transaction: an unseen observation identity is inserted; an already-known
observation identity whose `value`/`is_missing` still match has its
retrieval/last-seen/run provenance refreshed, never its value; an
already-known observation identity whose values *conflict* with the
incoming value, or a failure while recording the final `succeeded` status
itself, aborts the entire batch -- nothing in that batch persists, and no
observation changes are left associated with a `running` or `failed` run --
and the `ingestion_runs` row is separately recorded as `failed` with a
sanitized `error_category` (`content_conflict` or `storage_error`), never a
raw exception message or observation content. If recording that `failed`
status itself also fails, a sanitized `MacroObservationStorageError` is
raised instead of returning a result. The returned
`MacroObservationStorageResult` reports only sanitized counts (`received`,
`inserted`, `existing_or_updated`, `failed`) and the ingestion-run
id/status -- never observation values, dates, database internals, or
credentials.

`scripts/ingest_fred_observations.py` is the one manual ingestion entry
point: it makes at most one bounded, explicit, read-only
`FredMacroDataClient.get_observations()` request for a single,
strictly-validated command-line series/date-range, then stores the results
through `MacroObservationRepository`. Invalid `--series-id`/`--start`/
`--end`/`--limit`/`--max-pages` values are rejected before any network
request is constructed or any database write occurs. An empty (but
successful) provider result is reported as a successful, no-op outcome
rather than an error, and the repository is never called with an empty
batch.

**First authorized live run (2026-08-21):** `data/market_intelligence.duckdb`
was backed up, migration `0006` was applied to the real database (see
"Scope" above), and this script was then run once, live, against the real
database: series `FEDFUNDS`, requested observation range 2025-08-01
through 2026-07-31, fixed request provenance
(`realtime_start=1776-07-04`, `realtime_end=9999-12-31`, `output_type=1`,
`units=lin`), `limit=1000`, `max_pages=1`. 12 observations were received,
12 inserted, 0 existing/updated, 0 failed, and the corresponding
`ingestion_runs` row was recorded `succeeded`; the latest `ingestion_runs`
record for this dataset is `('fred', 'macro_observations', 'succeeded',
12, None)`. A subsequent read-only query verified 12 stored rows covering
2025-08-01 through 2026-07-01, with 0 missing observations. This confirms
one controlled ingestion run and transactional storage; it is not a
complete, gap-free, or research-validated macro dataset -- see "Status" in
`PROJECT_STATE.md` and the macro-observations entry in `DATA_CATALOG.md`.

**First authorized live orchestration run (2026-08-23):** the
`fred_fedfunds_observations` orchestration job (see
[docs/INGESTION_ORCHESTRATION.md](INGESTION_ORCHESTRATION.md)) ran this
same FEDFUNDS request pattern through `MacroObservationRepository` as
part of a broader, explicitly authorized orchestrated run: 3 observations
received, 0 inserted, 3 existing/updated, 0 failed. The 3 existing/updated
rows reflect idempotent overlap with the 12 observations already stored
above, within this job's own bounded request window -- not new distinct
dataset coverage.

## Macro series-metadata storage

`macro_series_metadata`
(`market_intelligence/storage/migrations/0008_create_macro_series_metadata.sql`)
stores only FRED's own reviewed series-*metadata* fields plus this
project's own provenance/ingestion bookkeeping -- never prediction,
direction, sentiment, impact, recommendation, option-contract, order,
execution, credentials, request headers, or raw API responses. This is
distinct from `macro_observations` (migration `0006`), which stores a
series' *values* -- this table stores the descriptive metadata (title,
units, frequency, seasonal adjustment, popularity, notes, observation date
range, last-updated timestamp) that labels those values, so a future Macro
Analyst never interprets an unlabeled number. **As originally written
(2026-08-24), this table, its repository, and its ingestion script
existed in code and tests only** (temporary DuckDB files, mocked HTTP
transports -- no live FRED request, no write to the real database);
migration `0008` had not been applied to the real local database, which
remained at migration `0007`. **This has since been superseded: migration
`0008` has been applied to the real local database, and a subsequent
read-only verification (2026-08-25) reported schema version `0008` (8
migrations applied), `healthy=True`, with 1 stored row (`GS10`, via the
authorized Core Macro Basket live ingestion described in
`PROJECT_STATE.md` item 25 and `DATA_CATALOG.md`).** This confirms one
controlled ingestion for one series; it is not a complete, gap-free, or
research-validated metadata dataset for `GS10` or any other series.

Columns: `provider` (fixed `"fred"`), `series_id`, `title`,
`observation_start`/`observation_end` (`DATE`), `frequency`/
`frequency_short`, `units`/`units_short`, `seasonal_adjustment`/
`seasonal_adjustment_short`, `last_updated` (`TIMESTAMP`, FRED's own
reported last-revision timestamp for this series' metadata, normalized to
UTC), `popularity` (`BIGINT`), `notes` (`VARCHAR`, nullable -- FRED reports
no notes for some series), `retrieved_at_utc` (when the connector fetched
the specific API response that produced the currently-stored values),
`first_ingested_at` (set once, on first insert, never changed afterward),
`last_seen_at` (refreshed every time the row is re-ingested), and
`ingestion_run_id` (the `ingestion_runs.run_id` of the run that most
recently wrote this row -- recorded, not enforced as a DuckDB foreign key,
for the same reason as `news_articles.ingestion_run_id`,
`market_bars.ingestion_run_id`, and
`macro_observations.ingestion_run_id`). The primary key is
`(provider, series_id)`. **Unlike `macro_observations`, series metadata has
no revision/vintage window of its own to preserve as part of the identity**
-- FRED's series endpoint always reports the current metadata for a series,
and this table is not a historical record of every past metadata state.
Every required string field, `title` and `notes` included, is preserved
exactly as FRED reported it -- never interpreted, summarized, or truncated.

`market_intelligence/storage/macro_series_metadata_repository.py`
(`MacroSeriesMetadataRepository`) is the only code that writes to this
table. It accepts one already-normalized `FredSeriesMetadata` object (from
`market_intelligence/data_connectors/fred_macro_data.py`'s
`get_series_metadata()`) -- it makes no network requests itself and does
not apply migrations; the database must already be initialized to at least
migration `0008`. Every call to `store_metadata()` first strictly validates
the item before any database write, rejecting: a `provider` argument that
is not exactly the fixed value `"fred"` (any alternate, blank, malformed,
or non-string value is rejected before any connection is opened, any
`ingestion_runs` row is written, or any metadata is written, and the
rejected value is never echoed); a non-`FredSeriesMetadata` item; an item
`provider` other than the requested provider; an unnormalized `series_id`;
a blank required string field; a malformed `observation_start`/
`observation_end`/`last_updated`; an invalid `popularity` (a plain
nonnegative integer is required -- booleans rejected); an invalid `notes`
type (must be a string or `None`); and a naive, timezone-free, or otherwise
malformed `retrieved_at_utc` -- with a sanitized
`MacroSeriesMetadataStorageValidationError` and no `ingestion_runs` row
created for this failure mode. It then records a `running` `ingestion_runs`
row, and writes the item *plus* the final `succeeded` `ingestion_runs`
status update inside one DuckDB transaction: an unseen
`(provider, series_id)` identity is inserted; an already-known identity has
**every mutable metadata/provenance column refreshed in place** (never
rejected as a conflict -- series metadata is expected to change over time
from FRED's own perspective, unlike an observation's value). A failure
while recording the final `succeeded` status itself aborts the write --
nothing persists, and no metadata change is left associated with a
`running` or `failed` run -- and the `ingestion_runs` row is separately
recorded as `failed` with a sanitized `error_category`
(`"storage_error"`), never a raw exception message or metadata content. If
recording that `failed` status itself also fails, a sanitized
`MacroSeriesMetadataStorageError` is raised instead of returning a result.
The returned `MacroSeriesMetadataStorageResult` reports only a sanitized
`inserted` boolean and the ingestion-run id/status -- never provider-
reported free text (title, notes), other metadata field values, database
internals, or credentials.

`scripts/ingest_fred_series_metadata.py` is the one manual ingestion entry
point: it makes at most one bounded, explicit, read-only
`FredMacroDataClient.get_series_metadata()` request for a single,
strictly-validated command-line series ID, then stores the result through
`MacroSeriesMetadataRepository`. An invalid `--series-id` is rejected
before any network request is constructed or any database write occurs.
Only sanitized metadata is ever printed (configured, fetch outcome, series
ID, and a storage outcome/status) -- never the series title, units,
frequency, seasonal adjustment, notes, popularity, last-updated timestamp,
database rows, or credentials. **This script has not been run live as
part of adding this storage layer.**

`MacroEvidenceBuilder`
(`market_intelligence/market_features/macro_evidence.py`) reads this table
read-only, alongside `macro_observations`, when building a snapshot: each
series entry now also reports `metadata_available` plus (when available)
`title`, `frequency`, `units`, and `seasonal_adjustment`, read exactly as
stored -- never inferred from the series ID itself -- and the snapshot adds
an aggregate `flags.missing_metadata_series` list. A missing
`macro_series_metadata` table or a series with no stored metadata is valid,
non-error input, mirroring the builder's existing behavior for missing
observations. See
[docs/MACRO_EVIDENCE_SNAPSHOT.md](MACRO_EVIDENCE_SNAPSHOT.md) for the full
field contract.

## Option-chain snapshot storage

Migration `0009`
(`market_intelligence/storage/migrations/0009_create_option_chain_snapshots.sql`)
defines a normalized, **three-table** design so that (a) run-level request
provenance is durable even for a successful retrieval that returned zero
contracts, without duplicating request fields onto every snapshot row, and
(b) batch membership stays truthful even when the same snapshot identity is
re-ingested into a later batch. None of the three tables stores a raw JSON
payload, a directional forecast, a contract ranking, a strategy
recommendation, an order, an execution field, request headers/URLs, page
tokens, or a response body. **As originally written (2026-09-14, when
revised to the three-table design), all three tables, the repository, and
the ingestion script existed in code and tests only** (temporary DuckDB
files, mocked HTTP transports — no live request, no real-database write);
migration `0009` had not been applied to the real local database. That
statement is preserved here as an honest, time-scoped diagnostic record and
is not retracted.

**Current status (2026-09-14, later the same day): migration `0009` has been
applied to the real local database** (backed up beforehand; a subsequent
read-only health check reported schema version `0009`, 9 migrations
applied, `healthy=True`), **and one authorized live ingestion has
succeeded** through `scripts/ingest_alpaca_options_chain.py --execute`:
provider `alpaca`, underlying `SPY`, requested feed `indicative` (explicitly
not OPRA), one expiration (2026-09-18), strikes 740–790, 102 contracts
received and inserted (51 calls, 51 puts), one `option_chain_snapshot_batches`
row (`outcome=succeeded`), the corresponding `ingestion_runs` row
`succeeded`, exactly one request, no retry. A subsequent read-only
structural audit confirmed exactly 102 batch-membership rows and 102
snapshot rows, zero duplicate or orphan memberships, `contract_count` equal
to the membership count, zero malformed OCC symbols or out-of-range
contracts, quote/trade data present for all 102, implied volatility and each
Greek present for 78 and unavailable for 24, and zero negative/nonfinite
values, crossed quotes, or feed mismatches. `indicative`-feed data may be
delayed or modified and must not be described as live OPRA; this confirms
internal structural consistency only, not pricing accuracy, timeliness,
usefulness, predictive edge, strategy validity, or profitability; and only
one batch exists, so recurring reliability is not established. See
[DATA_CATALOG.md](../DATA_CATALOG.md) ("SPY option-chain snapshots
(indicative)") for full sanitized detail.

**Why a third table.** The original two-table design let
`option_chain_snapshots.ingestion_run_id` be updated in place whenever an
identical snapshot was re-stored, so a re-ingested observation silently
moved from its original batch to the new one while the original
`option_chain_snapshot_batches.contract_count` still reported the old count
— historical batch membership was therefore not reproducible from stored
data. `option_chain_snapshot_batch_items` fixes this: it is a normalized
membership table, written fresh for every batch, so which contracts a given
batch actually contained is always answered by counting its own membership
rows, never by a mutable column on the snapshot row.

### `option_chain_snapshot_batches`

Exactly one row per successfully stored chain retrieval (one
`AlpacaOptionsChainClient.get_chain_snapshot` call that was then stored),
**including a retrieval that returned zero contracts**. Columns:
`ingestion_run_id` (primary key — one fresh id per store call, so this is
always a plain insert, never an upsert), `provider` (fixed `"alpaca"`),
`underlying` (fixed `"SPY"` in this milestone), `requested_feed` (`opra` /
`indicative`, recorded verbatim), `requested_expiration_date_gte` / `_lte`,
`requested_strike_price_gte` / `_lte`, `requested_option_type` (nullable),
`retrieved_at` (the single UTC instant the connector stamped on this
retrieval), `contract_count` (`>= 0`; `0` for a successful empty chain),
`outcome` (`succeeded` or `skipped_empty`), `first_ingested_at`, and
`last_seen_at`. This is where request-level provenance lives — it is
**not** duplicated onto `option_chain_snapshots` or
`option_chain_snapshot_batch_items` rows. **For every non-empty successful
batch, `contract_count` always equals the number of
`option_chain_snapshot_batch_items` rows carrying that batch's
`ingestion_run_id`.**

### `option_chain_snapshots`

One row per **immutable**, point-in-time normalized contract observation.
Columns: `provider`, `underlying`, `feed` (`opra` / `indicative`),
`contract_symbol` (the OCC symbol as Alpaca reports it), `retrieved_at` (the
same UTC instant recorded on the batch row), `expiration_date` /
`option_type` / `strike_price` (parsed from the OCC symbol and confirmed to
agree with the request filters), `quote_timestamp`, `bid_price` /
`bid_size` / `ask_price` / `ask_size`, `trade_timestamp`, `trade_price` /
`trade_size`, `implied_volatility`, `delta` / `gamma` / `theta` / `vega` /
`rho`, `first_ingested_at`, `last_seen_at`, and `ingestion_run_id`. Unlike
the pre-2026-09-14 design, **`ingestion_run_id` here records only the run
that first inserted the row and is never reassigned by a later
re-observation** — it answers "who created this observation," never "which
batch(es) currently claim it"; that question is answered exclusively by
`option_chain_snapshot_batch_items`. Prices and strike are `DECIMAL(18,6)`;
implied volatility and the Greeks are `DECIMAL(20,10)`; sizes are `BIGINT`.
Every quote/trade/Greek column is nullable and stored as `NULL` when the
provider omits it — never as zero.

### `option_chain_snapshot_batch_items`

The normalized batch-membership table. One row per `(ingestion_run_id,
provider, underlying, feed, contract_symbol, retrieved_at)` pair (all six
columns together are the primary key), written for **every** contract
returned by a successful, non-empty `store_snapshots` call — unconditionally,
whether the referenced `option_chain_snapshots` row was newly inserted or
already existed from an earlier batch. It carries only identity columns,
never a quote/trade/Greek value: the observation itself lives exactly once
on `option_chain_snapshots`, referenced here by its identity. Because
`ingestion_run_id` is fresh per `store_snapshots` call, this table is always
a plain insert. A single immutable `option_chain_snapshots` row may
legitimately be referenced by more than one batch's membership rows — this
is expected and correct, not a duplication bug: it is exactly how
re-ingesting the same snapshot into a new batch is recorded without
disturbing the original batch's membership.

**Open interest is deliberately absent from all three tables.** Alpaca's
option-chain snapshot endpoint does not supply open interest, so there is no
`open_interest` column and the connector never produces one. It is
documented as unavailable for this milestone in `DATA_CATALOG.md` and
`docs/OPTIONS_DECISION_WORKFLOW.md` and must not be added as populated data
by inference or default.

**Idempotency, immutability, and OPRA vs. indicative separation.**
`option_chain_snapshot_batches` keys on `ingestion_run_id`, so each stored
retrieval is exactly one row; a failed or rolled-back store leaves no batch
row at all. An option snapshot is a point-in-time observation — re-requesting
the same contract later legitimately returns different quote/trade/Greek
values — so `option_chain_snapshots`' primary key is
`(provider, underlying, feed, contract_symbol, retrieved_at)`: re-storing
the *same* connector result (same `retrieved_at`) with the same values never
mutates or relinks that row — it may refresh non-historical `last_seen_at`
bookkeeping, but `ingestion_run_id` and every observed value column are left
exactly as first stored — while a fresh `option_chain_snapshot_batch_items`
row is still written to record the new batch's membership; re-storing the
same identity with *conflicting* values aborts the whole batch (no new batch
row, no partial snapshot rows, no partial batch-item rows, and the
already-stored snapshot row is untouched) and the `ingestion_runs` row is
recorded `failed` with a sanitized `content_conflict` category; a later,
separate ingestion run carries a new `retrieved_at`, writes a new batch row,
and is stored as new observation and batch-item rows, overwriting nothing.
`feed` is part of the snapshot and batch-item identity and is recorded
verbatim as `requested_feed` on the batch row, so an `opra` observation and
an `indicative` observation of the same contract at the same instant are
stored, and counted toward batch membership, as separate rows and never
merged. `indicative`-feed data may be delayed or modified by the provider
and must never be described as live OPRA data.

`market_intelligence/storage/option_chain_snapshot_repository.py`
(`OptionChainSnapshotRepository`) is the only code that writes to any of the
three tables. `store_snapshots(items, *, request, retrieved_at=None, ...)`
accepts already-normalized `OptionChainSnapshot` objects (possibly empty)
plus the `OptionChainRequest` they were retrieved under; `retrieved_at` is
required when `items` is empty (there is no item to derive it from) and,
when supplied, must match every item's `retrieved_at`. Before any database
write, `request` is re-normalized through `normalize_option_chain_request`
(the same function the connector uses, enforcing the 60-day/$500-width/
10-page/5,000-contract/per-page ceilings) and rejected outright if it does
not equal its own canonical form — a manually constructed `OptionChainRequest`
cannot bypass those ceilings. It also strictly validates every item
(rejecting a non-SPY underlying, an item feed that does not match the
request feed, a malformed or field-disagreeing OCC symbol, a contract
outside the requested strike/expiration/type window, a negative
price/IV/gamma/vega, a non-finite or over-precise decimal, a negative size,
or a duplicate contract symbol in the batch — an *empty* batch is allowed),
then records a `running` `ingestion_runs` row and writes -- inside one
DuckDB transaction -- exactly one `option_chain_snapshot_batches` row, every
snapshot row, one `option_chain_snapshot_batch_items` row per item, and the
final `succeeded` status update, mirroring `BarRepository`. The returned
`OptionChainSnapshotStorageResult` reports only sanitized counts, the run
id/status, and `batch_outcome` (`succeeded` / `skipped_empty` / `failed`).

`scripts/ingest_alpaca_options_chain.py` is the dry-run-first manual entry
point (see `docs/OPTIONS_DECISION_WORKFLOW.md`): the default dry run
validates arguments and provider configuration and prints the sanitized
request bounds without making any request or writing anything; `--execute`
performs exactly one bounded ingestion (paginated GET requests as required,
no retry) and one transactional local write — a successful empty chain
still writes its batch row and prints `batch outcome: skipped_empty`. **It
was run live once with `--execute` on 2026-09-14** (SPY, `indicative` feed,
expiration 2026-09-18, strikes 740–790): 102 contracts received/inserted,
`batch outcome: succeeded` — see "Option-chain snapshot storage" above.

**Phase 1 request ceilings** (safety ceilings, not contract-selection
rules — callers must still supply explicit ranges): expiration span ≤ 60
calendar days, strike-window width ≤ $500, per-page limit ≤ 1,000 (the
provider's own maximum), max pages ≤ 10, max total contracts ≤ 5,000.
Enforced twice: by `market_intelligence/data_connectors/alpaca_options_chain.py`
before any HTTP request is built, and independently by
`OptionChainSnapshotRepository.store_snapshots` (via re-normalization, see
above) before any database write — so a caller cannot bypass the ceilings by
hand-constructing an `OptionChainRequest` and calling the repository
directly.

## Components

- `market_intelligence/storage/database.py` — `DuckDBManager`, the
  migration runner, and the path-safety check.
- `market_intelligence/storage/migrations/*.sql` — versioned schema
  migrations, applied in ascending filename order.
- `market_intelligence/storage/news_repository.py` — `NewsArticleRepository`,
  the news-article storage service (see "News article storage" above).
- `market_intelligence/storage/bar_repository.py` — `BarRepository`, the
  market-bar storage service (see "Market-bar storage" above).
- `market_intelligence/storage/macro_observation_repository.py` —
  `MacroObservationRepository`, the macro-observation storage service (see
  "Macro-observation storage" above). Migration `0006` has been applied to
  the real database, and one authorized live ingestion has succeeded (see
  above).
- `market_intelligence/storage/orchestration_audit_repository.py` —
  `OrchestrationAuditRepository`, the ingestion-orchestration audit-trail
  storage service (migration `0007`, now applied to the real database,
  with one authorized live orchestration run recorded through it — see
  above). See [docs/INGESTION_ORCHESTRATION.md](INGESTION_ORCHESTRATION.md).
- `market_intelligence/storage/macro_series_metadata_repository.py` —
  `MacroSeriesMetadataRepository`, the macro series-metadata storage service
  (see "Macro series-metadata storage" above). Migration `0008` has been
  applied to the real database (2026-08-25 verification), and one
  authorized live ingestion (`GS10`) has succeeded through it — see above.
- `market_intelligence/storage/option_chain_snapshot_repository.py` —
  `OptionChainSnapshotRepository`, the SPY option-chain snapshot storage
  service, writing `option_chain_snapshot_batches` (run-level provenance),
  `option_chain_snapshots` (immutable observations), and
  `option_chain_snapshot_batch_items` (normalized batch membership; all
  migration `0009`). Migration `0009` is applied to the real database
  (2026-09-14); one live batch of 102 SPY option-chain contracts is stored.
- `scripts/initialize_database.py` — applies pending migrations to the
  configured local database; prints only the database path, schema
  version, and applied migration count.
- `scripts/check_database.py` — read-only health check; verifies the
  database file exists and the required tables are present without
  writing anything.
- `scripts/ingest_fred_observations.py` — one-shot manual macro-observation
  ingestion (see "Macro-observation storage" above); first authorized live
  run succeeded 2026-08-21 (see above).
- `scripts/ingest_alpaca_news.py` — one-shot manual news ingestion (see
  "News article storage" above); not run live as part of this change.
- `scripts/ingest_alpaca_bars.py` — one-shot manual bars ingestion (see
  "Market-bar storage" above); dry-run by default, `--execute` required for
  any provider request or database write; first authorized live run
  succeeded 2026-08-21 (see above).
- `scripts/ingest_fred_series_metadata.py` — one-shot manual macro
  series-metadata ingestion (see "Macro series-metadata storage" above).
  This standalone script itself has not been run live; the real database's
  one `GS10` metadata row was written via `scripts/ingest_core_macro_basket.py`
  instead (see above), which uses the same underlying
  `MacroSeriesMetadataRepository`.
- `scripts/ingest_alpaca_options_chain.py` — dry-run-first, one-shot manual
  SPY option-chain snapshot ingestion (see "Option-chain snapshot storage"
  above); first authorized live run succeeded 2026-09-14 (see above).

## Database location and path safety

The database file defaults to `<project_data_path>/market_intelligence.duckdb`,
where `project_data_path` comes from `Settings` (`market_intelligence/config/settings.py`)
and itself defaults to this repository's own `data/` directory. `*.duckdb`
files are excluded from version control via `.gitignore`.

`DuckDBManager` resolves the target database path and refuses to
initialize (raising `DatabasePathSafetyError`) if it falls outside the
configured `project_data_path`. There is no bypass flag: to point the
manager at a different location (for example, an isolated directory in
tests), construct `Settings` with a different `project_data_path` — the
manager's default resolution then naturally lands inside it. This keeps
the enforced boundary and the configured boundary the same thing, so a
test's "explicitly injected" location is never a special case in the
safety check itself.

## Migration runner semantics

Migration files live in `market_intelligence/storage/migrations/` and
must be named `NNNN_description.sql`, where `NNNN` is a zero-padded
version. On `initialize()`:

1. Migration files are loaded, checksummed (SHA-256), and sorted by
   filename (equivalent to ascending version order). A malformed
   filename, a duplicate version, or a non-ascending sequence raises
   `MigrationError` before anything is executed.
2. Already-applied versions are read from `schema_migrations` (treated as
   empty if that table does not exist yet — true only for a brand-new
   database, since `schema_migrations` itself is migration `0001`).
3. The applied history is verified against the available migration files
   before anything is executed further. `MigrationError` is raised if: an
   applied version has no corresponding migration file (the file was
   deleted or renamed after being applied); the applied versions, sorted,
   are not an exact, contiguous, correctly ordered prefix of the available
   migration versions (a gap or out-of-order history); or an applied
   version's current file checksum no longer matches its stored checksum.
   A database whose recorded history cannot be reproduced from the
   migration directory is never silently treated as current.
4. For each migration not yet applied, its SQL and the corresponding
   `schema_migrations` bookkeeping row are executed inside a single
   `BEGIN TRANSACTION` / `COMMIT`. Any failure triggers a `ROLLBACK`,
   so the migration's DDL and its bookkeeping row either both persist or
   neither does. The runner then raises `MigrationError` — it does not
   attempt to continue past a failed migration.

Re-running `initialize()` against an up-to-date database applies zero
migrations and does not error (idempotent).

### Why `schema_migrations` is migration `0001`, not runner-side bootstrap DDL

`schema_migrations` is created by an ordinary versioned migration file
(`0001_create_schema_migrations.sql`) rather than hardcoded bootstrap SQL
in `database.py`. The runner only special-cases *reading* that table
before it may exist (treating "table not found" as "zero migrations
applied so far"); it does not special-case *creating* it. This keeps the
full schema — including its own bookkeeping table — defined in one place
(the migrations directory) and covered by the same checksum/ordering
guarantees as every other migration.

## Health check

`DuckDBManager.check_health()` opens the database read-only
(`duckdb.connect(path, read_only=True)`) and never runs or applies
migrations. If the database file does not exist, it reports an unhealthy
result without creating one. Otherwise it reports, as sanitized
booleans/status values only:

- `database_exists` — the database file exists.
- `schema_version` / `applied_migration_count` — the latest applied
  version and how many migrations have been applied, from
  `schema_migrations`.
- `required_tables_present` — both `schema_migrations` and
  `ingestion_runs` exist.
- `required_columns_present` — every column each required table must have
  is present (catches an out-of-band schema change, e.g. a manually
  dropped column, that migration bookkeeping alone would not catch).
- `migration_history_valid` — the database's applied versions have no
  missing migration files and form a contiguous, correctly ordered prefix
  of the available migration files (no gaps, no out-of-order history).
- `checksums_valid` — every applied migration's stored checksum matches
  its current migration file content.
- `is_current` — the database's schema version equals the latest
  available migration version, and both `migration_history_valid` and
  `checksums_valid` are true (a database that is merely behind the latest
  migration is not current, even if its existing history is otherwise
  valid).
- `healthy` — true only if `required_tables_present`,
  `required_columns_present`, `migration_history_valid`,
  `checksums_valid`, and `is_current` are all true. A database whose
  migration history cannot be reproduced from the current migration
  directory — a missing file, a checksum mismatch, or a malformed
  version sequence — is always reported unhealthy, never silently
  treated as current.

If the migration directory itself cannot be loaded (e.g. a malformed
filename or duplicate version among the `.sql` files), `check_health()`
does not raise; `migration_history_valid`, `checksums_valid`, and
`is_current` are reported false and `healthy` is false.

## Errors and sanitization

`MigrationError` may include the underlying DuckDB exception text, since
migration SQL is authored in this repository and never contains
credentials. This is unlike the provider connectors
(`market_intelligence/data_connectors/`), whose errors are always built
from sanitized components (status codes, exception type names) because
those requests do carry credentials. The `ingestion_runs.error_category`
column is intended to hold a similarly sanitized category, never a raw
exception message, once ingestion code that writes to it is added.
