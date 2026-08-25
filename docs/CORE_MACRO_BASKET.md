# Core Macro Basket

This document describes the Core Macro Basket configuration
(`market_intelligence/config/core_macro_series.json`,
`market_intelligence/config/macro_basket.py`) and its dry-run-first batch
ingestion script (`scripts/ingest_core_macro_basket.py`). It covers
infrastructure only -- see [PROJECT_STATE.md](../PROJECT_STATE.md) and
[DATA_CATALOG.md](../DATA_CATALOG.md) for what has (and has not) actually
been ingested.

## Purpose and scope

This is a **first, narrow, bounded basket** of seven FRED macro series this
project has explicitly reviewed and approved -- not a general FRED catalog,
not a complete macro model, and not proof of predictive usefulness of any
kind. It exists to give the existing, already-reviewed FRED metadata and
observations pipelines
([`FredMacroDataClient`](../market_intelligence/data_connectors/fred_macro_data.py),
[`MacroSeriesMetadataRepository`](../market_intelligence/storage/macro_series_metadata_repository.py),
[`MacroObservationRepository`](../market_intelligence/storage/macro_observation_repository.py))
a single, committed, reviewed list of series to ingest together, plus a
manual batch script to run them -- **it is not scheduling, and it does not
modify `market_intelligence/orchestration/` in any way** (no new job type,
no change to `jobs.json`, no orchestration audit-table writes).

The seven approved series and their fixed, committed categories:

| series_id | category | reporting frequency (official, per FRED) |
| --- | --- | --- |
| `FEDFUNDS` | `policy_rate` | Monthly |
| `GS10` | `long_term_rate` | Monthly |
| `CPIAUCSL` | `inflation` | Monthly |
| `PCEPI` | `inflation` | Monthly |
| `UNRATE` | `labor` | Monthly |
| `INDPRO` | `growth` | Monthly |
| `GDPC1` | `growth` | Quarterly |

No other series ID may ever appear in the committed configuration, and no
series may be filed under any category other than the one shown above --
both are enforced by `market_intelligence/config/macro_basket.py`, never by
the configuration file alone.

### `DGS10` → `GS10` replacement (2026-08-24)

The `long_term_rate` slot originally held the daily series `DGS10`. **The
first authorized live core-macro-basket run succeeded for the other six
series (`FEDFUNDS`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`) but
failed for `DGS10` observations only, with sanitized error category
`provider_error`; `DGS10`'s metadata request succeeded.** Only that
sanitized category was recorded -- no raw exception text, URL, query
parameter, or credential was ever printed or stored, so **the exact
provider-side cause of the `DGS10` observations failure is not known** and
this document does not claim otherwise. This diagnostic record is preserved
here and in [PROJECT_STATE.md](../PROJECT_STATE.md) and is not rewritten or
deleted.

In response, `DGS10` was replaced in the committed configuration by the
official monthly FRED series `GS10` ("Market Yield on U.S. Treasury
Securities at 10-Year Constant Maturity, Quoted on an Investment Basis") --
monthly, percent, not seasonally adjusted -- kept under the same
`long_term_rate` category. Because `GS10` is monthly rather than daily, it
now uses the same conservative monthly `observation_lookback_days` policy
(400 days) as the other five monthly series, rather than `DGS10`'s former
180-day daily ceiling; `recent_observations_limit` remains `6`, unchanged.
**`GS10` is a proposed, bounded, monthly replacement only -- as of
2026-08-24, it had not yet been requested live** (no live FRED request, no
live database write) -- see PROJECT_STATE.md for the authoritative status.

**`GS10` first authorized live ingestion (2026-08-25).** The "not yet
requested live" status above has since been superseded: a separately
authorized live `--execute` run of `scripts/ingest_core_macro_basket.py`
targeting `GS10` succeeded (overall status `succeeded`,
`metadata_status: succeeded`, `observation_status: succeeded`, 13
observations received, 13 inserted, 0 existing/updated, 0 failed). A
subsequent read-only verification confirmed the real local database
healthy at schema version `0008` with 1 stored `GS10` metadata row and 13
stored `GS10` observation rows covering 2025-07-01 through 2026-07-01 (0
missing). This confirms one bounded, controlled live ingestion for
`GS10` only -- it does not establish a complete, gap-free, or
research-validated macro dataset for `GS10` or any other series in this
basket. See [PROJECT_STATE.md](../PROJECT_STATE.md) (item 25) and
[DATA_CATALOG.md](../DATA_CATALOG.md) for the full record.

**No title, unit, frequency, seasonal adjustment, note, or observation value
is ever hardcoded anywhere in this configuration, the loader, or the
ingestion script.** Those come only from the reviewed FRED metadata
endpoint (`FredMacroDataClient.get_series_metadata`) and already-stored
local data, at request/read time -- exactly as for every other FRED
pipeline in this project.

## Configuration contract

`market_intelligence/config/core_macro_series.json` is a small, committed,
human-reviewed JSON file: `{"series": [...]}` -- no other root field is
permitted. Each entry has **exactly** five fields, no more and no fewer:

```json
{
  "series_id": "FEDFUNDS",
  "category": "policy_rate",
  "enabled": true,
  "observation_lookback_days": 400,
  "recent_observations_limit": 6
}
```

`market_intelligence/config/macro_basket.py`'s `load_core_macro_series()`
validates the file and returns a tuple of `CoreMacroSeriesConfig` objects,
strictly enforcing, in this order:

- **Exact root/entry field sets.** An unknown root field, a missing
  `series` list, a missing required entry field, or an unknown entry field
  is rejected outright.
- **Normalized, unique series IDs.** Every `series_id` must already be
  normalized (the same `normalize_series_id` the FRED connector itself
  uses -- trimmed, uppercased, 1-64 characters, letters/digits/underscores
  only); a `series_id` that is valid but not already in that exact form is
  rejected (mirroring the "must already be normalized" convention already
  used by `market_intelligence/orchestration/contracts.py`). A duplicate
  `series_id` across entries is rejected.
- **Only the seven approved series IDs.** Any other series ID -- even a
  syntactically valid FRED series ID -- is rejected.
- **Fixed category enforcement.** `category` must exactly equal this
  module's own committed `APPROVED_SERIES_CATEGORY[series_id]` mapping (see
  the table above); the configuration file itself can never assign a
  series to a different or invalid category.
- **`enabled` must be a plain boolean** -- `1`, `0`, `"true"`, and similar
  non-boolean values are rejected.
- **`observation_lookback_days` and `recent_observations_limit` must be
  plain integers** (a Python `bool`, despite being an `int` subclass, is
  explicitly rejected), each within fixed bounds -- see "Conservative
  lookback bounds" below.
- **Validation happens before `Settings`, the network, the run lock, or the
  database** -- `load_core_macro_series()` performs no I/O beyond reading
  this one committed file, and constructs no `Settings`, client, database
  connection, or lock, mirroring
  `market_intelligence/orchestration/config.py`'s `load_job_contracts()`.

Entries are always returned in the **same order they appear in the
committed file** -- deterministic because the file itself is committed,
static input, never sorted or otherwise reordered.

### Conservative lookback bounds

`observation_lookback_days` is bounded by a fixed, per-series ceiling
(`MAX_LOOKBACK_DAYS_BY_SERIES_ID`) that exists specifically to prevent an
unbounded historical request -- this is a hard bound enforced by the loader,
not merely a documented convention the configuration file could ignore:

- `FEDFUNDS`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GS10` (monthly
  series): up to 400 days.
- `GDPC1` (quarterly): up to 1,100 days.

The committed configuration currently sets every series'
`observation_lookback_days` exactly at its ceiling. `recent_observations_limit`
is bounded to `2`-`24` (`MIN_RECENT_OBSERVATIONS_LIMIT`/
`MAX_RECENT_OBSERVATIONS_LIMIT`), mirroring
[`MacroEvidenceBuilder`](MACRO_EVIDENCE_SNAPSHOT.md)'s own bounds (fixed
locally in `macro_basket.py`, not imported from
`market_intelligence/market_features/`, to avoid a layering dependency from
configuration onto that package).

**`recent_observations_limit` is forward-looking, committed configuration --
this ingestion script does not itself use it.** It exists so a future
basket-level evidence consumer (mirroring `MacroEvidenceBuilder`'s own
`recent_observations_limit` parameter) has a validated, bounded value to
read per series without inventing a second configuration format later.

## Ingestion script (`scripts/ingest_core_macro_basket.py`)

This is a **manual, controlled batch tool** -- not scheduling, not
continuous or unattended operation. It reuses, unmodified: the existing
`FredMacroDataClient`, `MacroSeriesMetadataRepository`,
`MacroObservationRepository`, their existing validation, and the existing
orchestration run lock
(`market_intelligence/orchestration/lock.py`'s `RunLock`, at the exact same
fixed lock path) for overlap protection with any other orchestration/manual
run. It does **not** write to `orchestration_runs`/`orchestration_job_runs`
(the orchestration audit trail) -- only the existing
`ingestion_runs` bookkeeping each repository already records itself, exactly
as the standalone `scripts/ingest_fred_observations.py`/
`scripts/ingest_fred_series_metadata.py` scripts already do.

### CLI usage

```
python scripts/ingest_core_macro_basket.py --all
python scripts/ingest_core_macro_basket.py --series FEDFUNDS
python scripts/ingest_core_macro_basket.py --series FEDFUNDS --series UNRATE
python scripts/ingest_core_macro_basket.py --all --execute
```

Exactly one of `--series SERIES_ID` (repeatable) or `--all` is always
required (a mutually exclusive, required `argparse` group). `--all` selects
every **enabled** configured series; an unknown, malformed, disabled, or
duplicate `--series` selection is rejected with a fixed, sanitized
selection-outcome category (`invalid_series_id`, `unknown_series_id`,
`disabled_series_id`, `duplicate_series_id`, `no_enabled_series`) -- never
echoing the raw requested value. This validation happens purely against the
already-loaded committed configuration, **before** `Settings`, any client,
the network, the database, or the run lock are ever constructed. Selected
series are always processed in the committed file's own deterministic
order, regardless of the order `--series` flags were given in.

### Dry run (default)

Without `--execute`, the script resolves one shared, injected UTC "as-of"
instant (`market_intelligence.orchestration.clock.resolve_as_of`, called
exactly once for the whole run and reused for every selected series'
window), computes each selected series' bounded observation window, and
prints a sanitized plan. **Zero `Settings` construction, zero network
requests, zero database activity of any kind** -- building the plan never
touches `Settings`, a client, a database connection, or the run lock.

```
mode: dry_run
selected series count: 1
- series_id: FEDFUNDS
  category: policy_rate
  enabled: True
  observation_lookback_days: 400
  recent_observations_limit: 6
  planned_metadata_request: true
  planned_observation_window_start: 2025-07-20
  planned_observation_window_end: 2026-08-24
```

Only sanitized series IDs, the fixed category, configured bounds, and
computed calendar dates are ever printed -- never a title, unit, value,
credential, URL, or query parameter.

### `--execute`

`--execute` is required for any real network/database activity. In order:

1. The existing `RunLock` is acquired at its usual fixed path
   (`Settings.project_data_path / "cache" / "orchestration.lock"`) --
   contention with a concurrent orchestration or basket-ingestion run fails
   immediately and sanitized (`execution outcome: lock_contention`).
2. **The real local database must already be healthy at exactly schema
   version `0008`**, checked read-only via the existing
   `DuckDBManager.check_health()` -- **before any network request is
   made.** This script deliberately **never applies a migration itself**
   (`DuckDBManager.initialize()` is never called here); an unhealthy or
   behind-schema database fails closed
   (`execution outcome: database_not_healthy`) with zero network requests
   made. Bringing the database up to `0008` is separate, already-reviewed
   work (see [docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)).
3. `FredMacroDataClient.is_configured()` is checked; an unconfigured FRED
   API key fails closed (`execution outcome: not_configured`) with zero
   network requests made.
4. Each selected series is then processed **sequentially, in the committed
   file's order**: exactly one `get_series_metadata` request (stored via
   `MacroSeriesMetadataRepository.store_metadata`) and exactly one bounded
   `get_observations` request over that series' computed window (stored via
   `MacroObservationRepository.store_observations`, unless the fetch
   returned zero observations, which is reported `skipped_empty` rather
   than a failure -- mirroring `scripts/ingest_fred_observations.py`'s
   existing convention). **There is no automatic retry of any request.**
5. **One series' failure never prevents a later selected series from being
   attempted** -- every series is wrapped independently, and an unexpected
   exception anywhere in one series' handling is caught at the
   per-series boundary and recorded as a sanitized `unexpected_error`
   category, never propagated. The **overall run status is only
   `succeeded` if every selected series' metadata request/storage succeeded
   and its observations request/storage either succeeded or was validly
   empty** -- any per-series failure makes the whole run's reported status
   `failed`, even when other series in the same run succeeded.
6. The run lock is always released in a `finally` block, regardless of
   outcome.

```
mode: execute
overall status: succeeded
- series_id: FEDFUNDS
  status: succeeded
  metadata_status: succeeded
  observation_status: succeeded
  observation_received: 2
  observation_inserted: 2
  observation_existing_or_updated: 0
  observation_failed: 0
```

Sanitized error categories mirror the existing orchestration layer's fixed
set (`market_intelligence.orchestration.results`):
`not_configured`, `provider_error`, `storage_error`, `unexpected_error`.

### Date windows

For each selected series: `start = as_of_date - observation_lookback_days`,
`end = as_of_date` (the shared as-of instant's UTC calendar date -- not the
day before, unlike `market_intelligence/orchestration/windows.py`'s
`fred_observations_window`, per this script's own explicit, narrower
contract). Both bounds are produced through the FRED connector's own strict
calendar-date normalization (`normalize_observation_date`). **A single
bounded window ending at the shared as-of date does not guarantee complete
or gap-free coverage of a series' full published history** -- it only
covers whatever FRED has published within that specific bounded window as
of the run's as-of instant. A still-possibly-revised or not-yet-published
"today" observation for a daily/weekly series may be requested by a window
whose end is the as-of date itself; FRED's own `get_observations` behavior
(returning whatever is currently published, if anything, for that date) is
unchanged by this script.

### Sanitized output contract

In both modes, output is limited to: series IDs (from the fixed approved
set), the fixed category, the configured `enabled`/lookback/limit values,
computed request-window calendar dates, per-series/overall status strings,
and sanitized counts. **Never** a series title, unit, frequency, seasonal
adjustment, note, or observation value; never a URL or query parameter;
never raw exception text, SQL, a database path, or a credential.

## Known limitations

- **This is a first, bounded basket of seven series -- not a complete macro
  model, and not proof of predictive usefulness of any kind.** No forecast,
  regime classification, or trading signal is produced or implied anywhere
  in this configuration or script.
- A single bounded window per series does not guarantee complete or
  gap-free historical coverage -- see "Date windows" above.
- This script does not apply migrations and does not modify
  `market_intelligence/orchestration/` (no new job type, no `jobs.json`
  change, no orchestration audit-table write) -- it is a separate, narrower,
  manual batch tool that reuses the same reviewed connectors/repositories
  and the same run lock.
- No automatic retry exists anywhere in this script; a transient provider
  failure for one series must be retried manually, by re-running the script
  for that series.
- `recent_observations_limit` is validated and stored in the committed
  configuration but is not read or used by this ingestion script itself --
  see "Configuration contract" above.

## Components

- `market_intelligence/config/core_macro_series.json` -- the committed,
  reviewed configuration file.
- `market_intelligence/config/macro_basket.py` -- `CoreMacroSeriesConfig`
  and `load_core_macro_series()`.
- `scripts/ingest_core_macro_basket.py` -- the dry-run-first batch ingestion
  CLI entry point.
- `market_intelligence/tests/test_macro_basket_config.py` -- configuration
  loading/validation tests.
- `market_intelligence/tests/test_ingest_core_macro_basket.py` -- CLI tests
  (selection validation, dry-run planning, execute-mode order/failure
  isolation/sanitization), against temporary databases and mocked HTTP
  transports only -- no live network access, no access to the real
  repository database.
