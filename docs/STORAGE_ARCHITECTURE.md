# Storage Architecture

This document describes the local DuckDB storage foundation added under
`market_intelligence/storage/`. It covers infrastructure only — see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Scope

This foundation defines six tables (five currently applied to the real
database; the sixth exists in repository code and tests only -- see
below):

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
  idempotency. See "Macro-observation storage" below. **Migration `0006`
  exists in repository code and tests only** -- it has not been applied to
  the real local database, which remains at schema version `0005`. No FRED
  observation has been fetched from the live API or stored.

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

## Macro-observation storage

`macro_observations`
(`market_intelligence/storage/migrations/0006_create_macro_observations.sql`)
stores only FRED's own reviewed observation fields plus this project's own
provenance/ingestion bookkeeping -- never prediction, direction, sentiment,
impact, recommendation, option-contract, order, execution, credentials,
request headers, or raw API responses. **This table, its repository, and
its ingestion script exist in repository code and tests only** -- migration
`0006` has not been applied to the real local database (see "Scope" above).

Columns: `provider` (fixed `"fred"`), `series_id`, `observation_date`
(`DATE`, the calendar date FRED's observation itself describes),
`realtime_start`/`realtime_end` (`DATE`, FRED's own reported revision/
vintage window for this specific observation value), `value`
(`DECIMAL(20,6)`, nullable), `is_missing` (`BOOLEAN`), `retrieved_at` (when
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
rather than silently overwriting an earlier vintage's value. A `CHECK`
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
validates every item before any database write, rejecting: non-
`FredObservation` elements; a `provider` other than the requested provider;
an unnormalized `series_id`/`observation_date`/`realtime_start`/
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
batch. **This script has not been run live** -- live ingestion requires
separate, explicit authorization, and migration `0006` must first be
applied to the real database.

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
  "Macro-observation storage" above). Code/tests only -- migration `0006`
  has not been applied to the real database.
- `scripts/initialize_database.py` — applies pending migrations to the
  configured local database; prints only the database path, schema
  version, and applied migration count.
- `scripts/check_database.py` — read-only health check; verifies the
  database file exists and the required tables are present without
  writing anything.
- `scripts/ingest_fred_observations.py` — one-shot manual macro-observation
  ingestion (see "Macro-observation storage" above); not run live.
- `scripts/ingest_alpaca_news.py` — one-shot manual news ingestion (see
  "News article storage" above); not run live as part of this change.
- `scripts/ingest_alpaca_bars.py` — one-shot manual bars ingestion (see
  "Market-bar storage" above); first authorized live run succeeded
  2026-08-21 (see above).

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
