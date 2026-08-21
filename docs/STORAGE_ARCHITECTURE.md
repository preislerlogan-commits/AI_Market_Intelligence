# Storage Architecture

This document describes the local DuckDB storage foundation added under
`market_intelligence/storage/`. It covers infrastructure only — see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Scope

This foundation defines and applies four tables:

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

No market-bar, macroeconomic-observation, forecast, or trade tables exist
yet. Those each require a separate, reviewed data contract before they are
added as their own versioned migration.

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

## Components

- `market_intelligence/storage/database.py` — `DuckDBManager`, the
  migration runner, and the path-safety check.
- `market_intelligence/storage/migrations/*.sql` — versioned schema
  migrations, applied in ascending filename order.
- `market_intelligence/storage/news_repository.py` — `NewsArticleRepository`,
  the news-article storage service (see "News article storage" above).
- `scripts/initialize_database.py` — applies pending migrations to the
  configured local database; prints only the database path, schema
  version, and applied migration count.
- `scripts/check_database.py` — read-only health check; verifies the
  database file exists and the required tables are present without
  writing anything.
- `scripts/ingest_alpaca_news.py` — one-shot manual news ingestion (see
  "News article storage" above); not run live as part of this change.

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
