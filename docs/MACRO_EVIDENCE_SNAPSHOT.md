# Macro Evidence Snapshot

This document describes the read-only macro-evidence snapshot layer added
under `market_intelligence/market_features/macro_evidence.py` and
`scripts/build_macro_evidence.py`. It covers infrastructure only -- see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Purpose and scope

`MacroEvidenceBuilder` builds exactly one deterministic, JSON-ready snapshot
dict from data **already stored** in the `macro_observations` table
(migration `0006`, see
[docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)). It is
**infrastructure for a future Macro Analyst agent** -- it is not an AI
agent itself, not a prediction, not a market-regime label, and building a
snapshot never makes a model request of any kind.

It is read-only end to end:

- It makes **no network request** of any kind -- no Alpaca, FRED, OpenAI, or
  Anthropic call. It imports no HTTP client and no model client.
- It opens the local database only with `duckdb.connect(path, read_only=True)`
  and never writes a row, never applies a migration, and never touches
  `ingestion_runs` or any orchestration table.
- The only thing it reuses from `market_intelligence/data_connectors/` is
  `normalize_series_id`/`FredInvalidSeriesIdError` -- the same small,
  already-reviewed, read-only validation helpers used by
  `MarketContextBuilder` -- and the existing `DuckDBManager`/`Clock`
  infrastructure for safe path resolution and an injectable "as of"
  instant. It never imports a connector's HTTP client, a storage
  repository's write path, or any model client.
- It never labels a series bullish/bearish, never classifies a market
  regime, never infers a rate-cut/hike direction, never predicts, never
  describes a transmission mechanism, and never recommends anything
  (including options language). It performs no transformation,
  interpolation, forward-filling, seasonal adjustment, or derived-change
  calculation -- every value is exactly what FRED reported and this
  project already stored, plus this module's own
  provenance/coverage/staleness bookkeeping about it.
- It also reads locally stored series-*metadata* (migration `0008`,
  `macro_series_metadata` table -- see
  [docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)), when present,
  and reports it verbatim alongside each series' observation data -- never
  inferred from the series ID itself. This exists so a future Macro Analyst
  never interprets an unlabeled number.

## Command-line usage

```
python scripts/build_macro_evidence.py
python scripts/build_macro_evidence.py --series FEDFUNDS
python scripts/build_macro_evidence.py --series FEDFUNDS --series UNRATE
```

An unrecognized flag is rejected by `argparse` itself before anything else
runs. An invalid `--series` selection is rejected by
`MacroEvidenceBuilder`'s own validation (see "Input validation" below)
before any DuckDB connection is opened, and is reported as a small
sanitized JSON object (`{"error": "invalid_input", "detail": "..."}`) with
exit code `2`. A database read failure is similarly reported
(`{"error": "storage_error", "detail": "..."}`) with exit code `1`, never
as a raw traceback, database path, SQL, or credential. A missing database
file is **not** treated as an error -- see "Behavior with no stored data"
below.

## Input validation (before any DuckDB connection is opened)

`normalize_series_ids` validates the full requested set of FRED series IDs
before any storage access, raising `MacroEvidenceValidationError` for:

- **Not a sequence** -- a bare string (rejected even though it is
  technically iterable character by character), a boolean, or any other
  non-sequence value.
- **Empty selection** -- at least one series ID must be requested.
- **Excessive count** -- at most `MAX_SERIES_IDS` (`10`) series may be
  requested.
- **A non-string or malformed entry** -- every entry is validated via the
  same `normalize_series_id` used by `FredMacroDataClient` (1-64
  characters, starting with a letter or digit, letters/digits/underscores
  only); a boolean entry is explicitly rejected even though `bool` is a
  subclass of `int`, not `str`, so it would otherwise slip past a naive
  string check.
- **Duplicates after normalization** -- e.g. `"fedfunds"` and `"FEDFUNDS"`
  collide once both are uppercased. Duplicates are **rejected**, not
  silently deduplicated, so a caller's mistaken repeated request is never
  silently accepted.

The normalized result **preserves the caller's requested order** -- it is
never sorted -- so the snapshot's `series` array always reflects the order
actually requested. Default: `("FEDFUNDS",)`.

## Snapshot field contract

All Decimal and date/datetime values are serialized as plain strings so the
returned dict is directly `json.dumps`-able, and the same stored data
always produces the same output shape/ordering (aside from
`snapshot_created_at_utc`, which reflects the current instant, or an
injected clock in tests).

```json
{
  "snapshot_created_at_utc": "2026-08-24T12:00:00Z",
  "request": {"series_ids": ["FEDFUNDS"]},
  "series": [
    {
      "series_id": "FEDFUNDS",
      "provider": "fred",
      "has_stored_observation": true,
      "latest_observation_date": "2026-07-01",
      "latest_value": "5.330000",
      "latest_is_missing": false,
      "realtime_start": "1776-07-04",
      "realtime_end": "9999-12-31",
      "retrieved_at_utc": "2026-08-21T00:00:00Z",
      "evidence_id": "macro_3f2a9c1d4e5b6789",
      "coverage": {
        "row_count": 12,
        "earliest_observation_date": "2025-08-01",
        "latest_observation_date": "2026-07-01",
        "missing_observation_count": 0
      },
      "freshness": {
        "missing": false,
        "stale": false,
        "future_date_detected": false,
        "stale_after_days": 90
      },
      "metadata_available": true,
      "title": "Federal Funds Effective Rate",
      "frequency": "Monthly",
      "units": "Percent",
      "seasonal_adjustment": "Not Seasonally Adjusted"
    }
  ],
  "flags": {
    "missing_series": [],
    "stale_series": [],
    "future_dated_series": [],
    "missing_metadata_series": []
  }
}
```

### `series[]`

Every entry reflects a single, deterministically chosen stored observation
row for that series -- see "Vintage selection" below. No entry ever
combines fields from more than one stored row.

- **`has_stored_observation`** -- `false` if no row exists in
  `macro_observations` for this series at all (including when the table
  itself does not exist yet, or before the database file exists). This is
  a distinct concept from `latest_is_missing`, which instead mirrors
  FRED's own `"."` missing-observation marker for a value that *is*
  stored.
- **`latest_value`** -- a decimal string, or `null` when
  `latest_is_missing` is `true` (FRED's own missing-observation marker,
  preserved exactly, never invented or interpolated) or when
  `has_stored_observation` is `false`.
- **`evidence_id`** -- a stable, code-generated identifier: the first 16
  hex characters of
  `sha256(f"{provider}:{series_id}:{observation_date}:{realtime_start}:{realtime_end}")`,
  prefixed `macro_`. It is derived only from the chosen row's full stored
  identity -- **never from its value** -- so re-selecting the same stored
  vintage always produces the same `evidence_id` regardless of what value
  it holds, and a genuinely different vintage of the same series/date
  always produces a different ID.
- **`coverage`** -- the full stored row count, earliest/latest stored
  `observation_date`, and the count of stored rows for this series whose
  `is_missing` is `true`, across *all* stored vintages for the series --
  not just the one chosen row.

### Vintage selection

A series' identity in `macro_observations` is
`(provider, series_id, observation_date, realtime_start, realtime_end)`
(see [docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)) -- FRED may
report more than one revision ("vintage") of the same
`series_id`/`observation_date`, each under a different
`realtime_start`/`realtime_end` window. This module always selects exactly
**one** row per requested series, using this fixed, deterministic
ordering:

```sql
ORDER BY observation_date DESC, realtime_start DESC, realtime_end DESC
LIMIT 1
```

That is: the **latest observation date** on file, and among any rows
sharing that date, the row with the **latest `realtime_start`**, breaking
any further tie with the **latest `realtime_end`** -- i.e. the most
recently reported revision of the most recent observation. Every field on
the resulting `series[]` entry (`latest_value`, `latest_is_missing`,
`realtime_start`, `realtime_end`, `retrieved_at_utc`, `evidence_id`) comes
from this single chosen row; fields from a different vintage are never
mixed in. `realtime_start`/`realtime_end` are always reported explicitly on
the entry so a future consumer can audit exactly which revision window was
selected, rather than having to assume it.

### Freshness

- **`freshness.stale_after_days`** -- a fixed, documented threshold (`90`
  elapsed days), chosen as a conservative default suitable for monthly
  macro observations: it comfortably exceeds a series like FEDFUNDS's
  monthly reporting cadence without flagging a normal inter-release gap as
  stale. This is a plain elapsed-time signal, not an economic judgment --
  **a series reported under this threshold is not thereby claimed to be
  economically current**, only "not yet flagged stale by this fixed
  clock."
- **`freshness.stale`** -- `true` if `missing` is `true`, if
  `future_date_detected` is `true`, or if the chosen row's
  `observation_date` is more than `stale_after_days` before the snapshot's
  `as_of` date.
- **`freshness.future_date_detected`** -- `true` only when the chosen row's
  `observation_date` is more than a small, fixed, documented tolerance
  (`FUTURE_DATE_TOLERANCE_DAYS`, `1` day) ahead of the snapshot's `as_of`
  date. `observation_date` is a calendar date with no time component, so
  comparing it directly against `as_of`'s own UTC calendar date could
  otherwise falsely flag a genuinely same-day observation as "future"
  purely from timezone rounding; this tolerance absorbs that without
  hiding a genuinely implausible future-dated observation (e.g. a data
  error reporting a date months ahead). When `true`, `stale` is also
  forced `true`, but the observation itself and its `latest_observation_date`
  are **never** discarded, dropped, or rewritten -- the implausible date is
  reported exactly as stored.

These thresholds are fixed, documented constants, not a FRED-release-
calendar-aware system that knows any individual series' actual publication
schedule.

### Series metadata

- **`metadata_available`** -- `true` only if a row exists in
  `macro_series_metadata` (migration `0008`, see
  [docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)) for this
  `(provider, series_id)`. Read independently of the observation data --
  a series can have `has_stored_observation: true` with
  `metadata_available: false` (or the reverse), since the two tables are
  populated by separate, independently authorized ingestion runs.
- **`title`**, **`frequency`**, **`units`**, **`seasonal_adjustment`** --
  read exactly as stored in `macro_series_metadata`, verbatim, when
  `metadata_available` is `true`; otherwise `null`. These are **never
  inferred from the series ID itself** -- a well-known series ID (e.g.
  `FEDFUNDS`) with no stored metadata row always reports `null` for all
  four fields, never a guessed or hard-coded label.
- **`flags.missing_metadata_series`** -- the requested series IDs (in
  request order) for which `metadata_available` is `false`.

This metadata is read-only bookkeeping about what has already been
ingested and stored by the separate, reviewed FRED series-metadata pipeline
(`FredMacroDataClient.get_series_metadata()`,
`MacroSeriesMetadataRepository`, `scripts/ingest_fred_series_metadata.py`)
-- it is never interpreted, summarized, or used to derive any economic
judgment by this module.

## Behavior with no stored data

A missing database file, an existing-but-empty database, a database that
has not yet had migration `0006`/`0008` applied, or a requested series with
no stored observation and/or no stored metadata at all are all treated as
**valid, non-error** input: `build_snapshot()`/the CLI still return/print a
complete snapshot, with that series' entry reporting
`has_stored_observation: false` and/or `metadata_available: false` as
appropriate (`freshness.missing`/`freshness.stale` both `true` in the
observation case). This mirrors `MarketContextBuilder`'s and
`NewsEvidenceBuilder`'s established behavior: this snapshot layer must also
work correctly before any macro-observation or series-metadata ingestion
has ever run.

## Errors and sanitization

`MacroEvidenceError` (and its `MacroEvidenceValidationError` subclass)
never include the database path, SQL text, observation values, or a raw
underlying exception -- only a fixed, non-input-derived description. A
close() failure after an otherwise-successful read raises a sanitized
`MacroEvidenceError` distinct from a read failure; a close() failure that
follows an already-failed, already-sanitized read never replaces or masks
that original error -- the close failure is swallowed and the original
sanitized error is what the caller sees. The connection opened by
`build_snapshot()` is always closed exactly once, on every code path
(successful read, failed read, or open failure).

## Known limitations

- This snapshot only ever reflects whatever has already been ingested and
  stored by the existing, separately reviewed FRED observations and
  series-metadata pipelines
  (see [DATA_CATALOG.md](../DATA_CATALOG.md)/[PROJECT_STATE.md](../PROJECT_STATE.md)).
  It cannot backfill gaps, and a snapshot immediately after a single
  bounded ingestion run only ever covers that run's bounded window. As of
  this writing, the series-metadata pipeline (migration `0008`) exists in
  code and tests only and has never been run live, so every series entry's
  `metadata_available` currently reports `false` against the real database.
- The freshness/future-date thresholds are simple, fixed, documented
  heuristics for data bookkeeping -- they are not trading signals, are not
  validated forecasts, and must not be treated as such (see
  [DECISION_RULES.md](../DECISION_RULES.md)).
- This module performs **no** transformation, interpolation,
  forward-filling, seasonal adjustment, or derived-change (e.g.
  period-over-period delta) calculation of any kind. It reports exactly
  the single chosen stored row's value, unmodified. Any such derived
  feature is separate, future, reviewed work.
- This module is intentionally not a general feature store or a Macro
  Analyst: it has no plugin system, no caching layer, and makes no model
  request. Building an actual Macro Analyst agent on top of this snapshot
  is separate, future, reviewed work -- see
  [docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md) and
  [docs/NEWS_ANALYST.md](NEWS_ANALYST.md) for the equivalent pattern
  already used for market/session and news evidence.

## Components

- `market_intelligence/market_features/macro_evidence.py` --
  `MacroEvidenceBuilder` and its validation helpers.
- `scripts/build_macro_evidence.py` -- the one-shot, read-only CLI entry
  point; prints one sanitized JSON snapshot to stdout per invocation.
- `market_intelligence/tests/test_macro_evidence.py` and
  `market_intelligence/tests/test_build_macro_evidence.py` -- tests against
  temporary DuckDB databases only; no live network access, no access to the
  real repository database.
