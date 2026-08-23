# Ingestion Orchestration

This document describes the ingestion-orchestration layer added under
`market_intelligence/orchestration/`. It covers infrastructure only — see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

**Status: a first authorized live orchestration run has succeeded
(2026-08-23). This remains a single controlled run, not scheduled or
continuous/unattended operation.** As originally added, this layer
existed in code and tests only and had not been run with `--execute`
against the real database. On 2026-08-23, migration `0007` was applied to
the real database (backed up beforehand) and
`scripts/run_ingestion_pipeline.py` was run once, live, with
`--all --execute`, selecting all three existing reviewed jobs
(`alpaca_news_spy`, `alpaca_bars_spy_5min`, `fred_fedfunds_observations`)
in one run (`orchestration_run_id=e63d931e-8957-4357-93ee-ba7076b079d8`,
overall status `succeeded`). See "First authorized live orchestration
run" below for full sanitized results. No complete or validated provider
dataset exists as a result of this run. No AI agents or model APIs are
operational here, and Robinhood remains manual and unconnected — this
layer builds no AI agent, analysis, prediction, recommendation,
scheduling, or brokerage/Robinhood integration of any kind; it only runs
the same already-reviewed Alpaca news, Alpaca bars, and FRED observation
pipelines through explicit job contracts.

## Purpose

This is groundwork for future specialized agents: a conservative,
repeatable way to select and run one or more of the existing, already-
reviewed ingestion pipelines (Alpaca news, Alpaca bars, FRED observations)
through explicit, strictly validated job contracts, with dry-run-first
safety, per-job failure isolation, overlap protection, and a persistent
audit trail.

## A. Orchestration contracts

`market_intelligence/orchestration/contracts.py` defines immutable, frozen
`JobContract` dataclasses for exactly three reviewed job types:
`alpaca_news`, `alpaca_bars`, `fred_observations`. Each contract carries a
stable `job_id`, its `job_type`, an `enabled` boolean, the job type's fixed
`provider` and `dataset_name` (matching the existing repositories'
`DEFAULT_PROVIDER`/`DEFAULT_DATASET_NAME` constants), and job-specific
bounded `params`.

The contract schema has no field for an arbitrary Python import, a shell
command, an executable path, SQL, a URL, a caller-supplied API host, an
order/account/execution job, a Robinhood job, or an unreviewed provider
name — these are not merely rejected by validation, there is nowhere for
them to be expressed. `AlpacaBarsJobParams.feed`/`adjustment`/`currency`
must equal the bars connector's own fixed module constants
(`market_intelligence/data_connectors/alpaca_bars.py`); `FredObservationsJobParams`
never re-specifies the FRED connector's own fixed real-time-period/units
provenance — that remains enforced solely by the connector itself. Every
parameter reuses the same connector-level normalization functions already
used by `AlpacaNewsClient`/`AlpacaBarsClient`/`FredMacroDataClient`, and
every value must already be in its normalized form (a contract can never
silently coerce a sloppy or ambiguous value).

Job contracts never compute or store a date window directly:
`market_intelligence/orchestration/windows.py` computes a bars/FRED window
deterministically from a bounded `lookback_days` and one shared injected
UTC "as-of" clock (`market_intelligence/orchestration/clock.py`), so every
job in one run agrees on "now" and no window ever includes a partial,
still-in-progress current period (bars end at the start of the as-of UTC
day; FRED observations end the day before the as-of date).

### Configuration format

`market_intelligence/orchestration/jobs.json` is a small, committed JSON
file (parsed with only the Python standard library's `json` module — no
new dependency was added to parse it). `market_intelligence/orchestration/config.py`
loads and validates it into `JobContract` objects, always returning them
sorted by `job_id` for deterministic ordering regardless of the file's own
entry order, and rejects a duplicate `job_id` before any job ever runs.
Loading the config performs no network I/O and constructs no `Settings`,
client, database connection, or lock — it is safe to call unconditionally,
even for a dry run.

The three initial reviewed jobs:

| job_id | job_type | params |
| --- | --- | --- |
| `alpaca_news_spy` | `alpaca_news` | symbol `SPY`, limit `10` |
| `alpaca_bars_spy_5min` | `alpaca_bars` | symbol `SPY`, timeframe `5Min`, `feed=iex`/`adjustment=raw`/`currency=USD`, `lookback_days=5`, `limit=500`, `max_pages=1` |
| `fred_fedfunds_observations` | `fred_observations` | series `FEDFUNDS`, `lookback_days=90`, `limit=1000`, `max_pages=1` |

## B. Dry-run-first safety

`scripts/run_ingestion_pipeline.py` is the entry point. `--job JOB_ID` and
`--all` form a required, mutually exclusive selection group — exactly one
must always be given, which also trivially satisfies "reject `--execute`
without exactly one of `--job` or `--all`", since that condition can then
never occur.

Selection is validated purely against the already-loaded job
configuration — before `Settings`, any client, the network, the database,
or the run lock are ever constructed: an unknown, blank/malformed, or
disabled job ID is rejected (a duplicate `job_id` is rejected earlier
still, at config-load time, since the config itself must not contain one).

Default behavior (no `--execute`) is a dry run:
`market_intelligence/orchestration/runner.py`'s `build_plan` is pure — it
never constructs `Settings`, a client, a database connection, or a lock,
so a dry run makes zero network requests, zero database writes, zero
migrations, and zero credential reads. It prints only sanitized plan
metadata (job/run identifiers, enabled flag, provider, dataset name, and
the computed-but-not-yet-requested window boundaries) — never credentials,
provider values, headlines, URLs, OHLCV values, observation values, SQL,
or database rows.

`--execute` is required for real network/database execution. Job adapters
(`market_intelligence/orchestration/adapters.py`) call the reviewed
connector/repository Python classes directly — never a shell command,
subprocess, or one of the manual `scripts/ingest_*.py` entry points.
Execute-mode output is limited to job/run identifiers, status categories,
and sanitized counts.

## C. Failure isolation

Each job has one of five statuses: `planned`, `running`, `succeeded`,
`failed`, `skipped`. `skipped` is used specifically when a job's fetch
returns zero items: storage is skipped entirely (there is nothing to
store, and `MacroObservationRepository` explicitly rejects an empty
batch), applied uniformly across all three job types for consistency with
the existing `scripts/ingest_fred_observations.py`'s "skipped_empty"
convention.

`execute_run` runs every selected job in the deterministic order given,
inside its own `try`/`except` at the orchestration boundary (on top of
each adapter's own internal catch-all — see `adapters.py`'s module
docstring): one job's failure never prevents a later selected job from
running, and each job's own repository call keeps its own independent
DuckDB transaction (the existing repositories' own transaction/conflict
semantics are reused unmodified, never duplicated or weakened). Every
unexpected exception is converted to one of four fixed, sanitized error
categories (`not_configured`, `provider_error`, `storage_error`,
`unexpected_error`) — never a raw exception type or message.

The overall run status is always derived truthfully from the selected
jobs' own outcomes: `succeeded` only if every selected job's status is
`succeeded` or `skipped`; if any job's status is `failed`, the whole run
is reported `failed`, even if other jobs in the same run succeeded.

## D. Overlap protection

`market_intelligence/orchestration/lock.py`'s `RunLock` is a conservative,
fail-closed local lock at
`Settings.project_data_path / "cache" / "orchestration.lock"` — always
derived from `Settings`, never from a caller-supplied path. Acquisition is
atomic (`os.O_CREAT | os.O_EXCL`), so a second concurrent `--execute` run
fails immediately with a sanitized `OrchestrationLockContentionError`
rather than racing. A dry run never acquires this lock.

**Documented limitation — no stale-lock breaking.** Reliably determining
whether the process that created an existing lock file is still alive is
not something this project's existing dependencies (`duckdb`, `pandas`,
`pyarrow`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`) can do
in a cross-platform-correct way: there is no `psutil` here, and
`os.kill(pid, 0)` is not a safe, uniform liveness probe across POSIX and
Windows. Rather than guess — e.g. breaking a lock merely because it looks
"stale" by age, which risks two processes writing at once if the original
process is merely slow — this lock **fails closed**: it never breaks or
removes an existing lock file automatically, under any condition. A lock
left behind by a crashed process must be removed manually by the
operator, once they have independently confirmed no orchestration process
is actually still running.

The lock is always released in a `finally` block around the whole
execute path (`RunLock.release()` never raises — a failure to remove the
lock is sanitized and silently tolerated, so it can never mask or replace
a job's own result), including after a job failure, an audit-write
failure, or a database-initialization failure.

## E. Persistent orchestration audit

**Migration `0007`**
(`market_intelligence/storage/migrations/0007_create_orchestration_audit.sql`)
adds `orchestration_runs` and `orchestration_job_runs`, independent of the
existing `ingestion_runs` table (which already records dataset-level
storage bookkeeping per repository call). This exists specifically so a
run that fails or crashes before any `ingestion_runs` row can even exist
(e.g. a lock-acquisition failure, or a crash between two jobs) still
leaves a truthful, durable audit record — never falsely left or reported
as running/succeeded. Migrations `0001`–`0006` are unchanged (byte-
identical); this is the only new migration.

Stored: orchestration run ID, job ID/type, provider/dataset, started/
completed UTC timestamps, status, a sanitized error category, received/
inserted/existing/failed counts where available, the associated
`ingestion_run_id` where available, `code_version`, and `schema_version`.
**Never stored:** credentials, URLs/query parameters, raw responses/
errors, headlines/summaries, OHLCV values, macro values, prompts/model
outputs, recommendations, or order/execution data. Status is validated
with a DuckDB `CHECK` constraint on both tables.

`orchestration_job_runs.orchestration_job_run_id` is deliberately
`"<orchestration_run_id>:<job_id>"` rather than a fresh UUID — a `job_id`
is unique within one run (job selection has no duplicates), so this gives
a stable, idempotent identity for "this job, in this run" and doubles as
the natural key an operator would search for. Neither table declares a
DuckDB foreign key, for the same reason as `news_articles.ingestion_run_id`
(migration `0004`): a hard FK would block otherwise-legitimate future
schema evolution, since DuckDB refuses to `ALTER` a table that a FK
depends on. References are enforced at the application layer
(`market_intelligence/storage/orchestration_audit_repository.py`).

**Dry runs are never persisted.** Only an `--execute` run ever writes a
row to either table; a dry run's plan is ephemeral, printed output only.
This keeps the audit trail meaningful (every row represents a real
attempt to reach a provider or the database) and keeps dry-run planning
fully free of any database write, consistent with its "zero database
writes" guarantee.

Unlike the per-dataset repositories, this audit repository does not write
one atomic batch inside a single transaction: an orchestration run's
audit trail is written incrementally, in real time, as jobs are started
and completed, interleaved with each job's own separate repository
transaction. A failure while writing the audit trail itself (as opposed
to a job's own provider/storage failure) is treated as fatal to the whole
orchestration run — it indicates an infrastructure problem with the audit
trail, not a single job's failure, so it is not subject to the per-job
failure-isolation guarantee.

**This audit infrastructure was originally built and tested against
temporary databases only.** Migration `0007` has since been applied to
the real database (2026-08-23) and one authorized live orchestration run
has been recorded through it — see "First authorized live orchestration
run" below and `PROJECT_STATE.md` for the current real-database status.

## F. Job adapters

`market_intelligence/orchestration/adapters.py` defines one narrow adapter
per job type: `run_alpaca_news_job` (→ `AlpacaNewsClient` →
`NewsArticleRepository`), `run_alpaca_bars_job` (→ `AlpacaBarsClient` →
`BarRepository`), `run_fred_observations_job` (→ `FredMacroDataClient` →
`MacroObservationRepository`). Each reuses the existing connector
normalization and repository storage/transaction logic unmodified — no
adapter has any method that could place an order or call an account/
execution endpoint, since none of that functionality exists anywhere in
the connectors this module imports. `Settings`, the client, the
repository, and the clock are all constructor/call-time dependencies, so
every adapter is independently, fully testable with mocked HTTP transports
and temporary databases.

## G. First authorized live orchestration run (2026-08-23)

`data/market_intelligence.duckdb` was backed up, migration `0007` was
applied to the real local database, and `scripts/run_ingestion_pipeline.py`
was then run once, live, with `--all --execute`, selecting all three
existing reviewed jobs in one orchestrated run.

A subsequent read-only health check reported: `schema_version=0007`,
`applied_migration_count=7`, `required_tables_present=True`,
`required_columns_present=True`, `migration_history_valid=True`,
`checksums_valid=True`, `is_current=True`, `healthy=True`.

The orchestration run
(`orchestration_run_id=e63d931e-8957-4357-93ee-ba7076b079d8`) completed
with overall status `succeeded`. Per-job sanitized results, each recorded
`succeeded` in `orchestration_job_runs`:

| job_id | received | inserted | existing/updated | failed | status |
| --- | --- | --- | --- | --- | --- |
| `alpaca_news_spy` | 10 | 10 | 0 | 0 | succeeded |
| `alpaca_bars_spy_5min` | 334 | 169 | 165 | 0 | succeeded |
| `fred_fedfunds_observations` | 3 | 0 | 3 | 0 | succeeded |

A subsequent read-only query confirmed `orchestration_runs` contains
exactly 1 run and all three `orchestration_job_runs` rows for it are
recorded `succeeded`. Only sanitized counts and status are recorded here
— no headline, URL, summary, OHLCV, or observation value from this run
is reproduced in this document.

**This confirms one controlled, explicitly authorized orchestration run
across all three existing reviewed jobs, transactional per-job storage
(via the existing repositories, unmodified), and a persistent
orchestration audit trail written through `OrchestrationAuditRepository`.**
The `alpaca_bars_spy_5min` job's 165 existing/updated bars and the
`fred_fedfunds_observations` job's 3 existing/updated, 0 inserted result
reflect idempotent overlap with previously stored rows within each job's
own bounded request window (see `PROJECT_STATE.md`/`DATA_CATALOG.md` for
the prior standalone ingestion runs each job overlaps with) — not new
distinct dataset coverage. **This run does not establish scheduling,
continuous or unattended operation, dataset completeness or gap-freedom
for any of the three underlying datasets, prediction, agent intelligence,
options analysis, or trading execution** — none of that exists or was
exercised by this run. The run lock (section D) was acquired for the
duration of the run and released afterward; no concurrent `--execute` run
was attempted.

## Verification performed for this change

- Full `pytest` suite passes (existing tests plus new orchestration
  tests), using only temporary databases and mocked HTTP transports — zero
  live HTTP requests.
- `ruff check .` passes with no findings.
- Migrations `0001`–`0006` remain byte-identical; migration `0007` has
  since been applied to the real database as part of the first authorized
  live orchestration run described in section G above, which also
  verified the real database healthy at schema version `0007`. Previously
  stored row counts from the standalone ingestion runs (10 news articles,
  248 bars, 12 macro observations) were confirmed unchanged prior to the
  orchestrated run, via read-only count queries.
- Nothing was committed, pushed, or scheduled as part of adding this
  layer's code; the migration application and `--execute` run described
  in section G were separately, explicitly authorized live actions.
