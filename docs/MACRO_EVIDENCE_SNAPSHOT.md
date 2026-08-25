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
  regime, never infers a rate-cut/hike direction, never predicts, and never
  describes a transmission mechanism or recommends anything (including
  options language). It performs no interpolation, forward-filling, or
  seasonal adjustment -- every value is exactly what FRED reported and this
  project already stored, plus this module's own
  provenance/coverage/staleness bookkeeping about it. Each series entry
  also reports a bounded, read-only `recent_observations` excerpt and, when
  precisely supported, one exact `latest_change_from_previous` comparison
  (an absolute difference and increased/decreased/unchanged direction
  between exactly two stored observations) -- the **only** derived
  quantities this module computes; it never computes a percentage,
  annualized, or basis-point change, and never labels anything a trend
  (see "`recent_observations[]`"/"`latest_change_from_previous`" below).
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
python scripts/build_macro_evidence.py --series FEDFUNDS --recent-observations-limit 12
```

An unrecognized flag is rejected by `argparse` itself before anything else
runs. An invalid `--series` selection, or a `--recent-observations-limit`
outside `2`-`24`, is rejected by `MacroEvidenceBuilder`'s own validation
(see "Input validation" below) before any DuckDB connection is opened, and
is reported as a small sanitized JSON object (`{"error": "invalid_input",
"detail": "..."}`) with exit code `2`. A database read failure is similarly
reported (`{"error": "storage_error", "detail": "..."}`) with exit code
`1`, never as a raw traceback, database path, SQL, or credential. A missing
database file is **not** treated as an error -- see "Behavior with no
stored data" below.

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

`normalize_recent_observations_limit` validates `recent_observations_limit`
in the same before-any-DuckDB-access way, raising
`MacroEvidenceValidationError` for: a non-`int` (a `bool` is explicitly
rejected even though `bool` is a subclass of `int`), or a value outside
`MIN_RECENT_OBSERVATIONS_LIMIT` (`2`) to `MAX_RECENT_OBSERVATIONS_LIMIT`
(`24`) inclusive. Default: `DEFAULT_RECENT_OBSERVATIONS_LIMIT` (`6`). The
minimum is `2` so that whenever at least two distinct observation dates are
stored, the excerpt always contains enough rows to also support
`latest_change_from_previous` (see below) -- a caller never needs a larger
request just to get the two-observation comparison. Both `series_ids` and
`recent_observations_limit` are validated before `build_snapshot` opens any
DuckDB connection, checks whether the database file exists, or checks
whether `macro_observations` exists.

## Snapshot field contract

All Decimal and date/datetime values are serialized as plain strings so the
returned dict is directly `json.dumps`-able, and the same stored data
always produces the same output shape/ordering (aside from
`snapshot_created_at_utc`, which reflects the current instant, or an
injected clock in tests).

```json
{
  "snapshot_created_at_utc": "2026-08-24T12:00:00Z",
  "request": {"series_ids": ["FEDFUNDS"], "recent_observations_limit": 6},
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
      "recent_observations": [
        {
          "observation_date": "2026-07-01",
          "value": "5.330000",
          "is_missing": false,
          "realtime_start": "1776-07-04",
          "realtime_end": "9999-12-31",
          "evidence_id": "macro_3f2a9c1d4e5b6789"
        },
        {
          "observation_date": "2026-06-01",
          "value": "5.000000",
          "is_missing": false,
          "realtime_start": "1776-07-04",
          "realtime_end": "9999-12-31",
          "evidence_id": "macro_7a1b2c3d4e5f6081"
        }
      ],
      "latest_change_from_previous": {
        "available": true,
        "unavailable_reason": null,
        "latest_observation_date": "2026-07-01",
        "latest_value": "5.330000",
        "latest_evidence_id": "macro_3f2a9c1d4e5b6789",
        "previous_observation_date": "2026-06-01",
        "previous_value": "5.000000",
        "previous_evidence_id": "macro_7a1b2c3d4e5f6081",
        "absolute_change_native_units": "0.330000",
        "direction": "increased"
      },
      "metadata_available": true,
      "title": "Federal Funds Effective Rate",
      "frequency": "Monthly",
      "frequency_short": "M",
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

The same tie-break rule (latest `realtime_start`, then latest
`realtime_end`) is applied **independently per `observation_date`** to
build `recent_observations` below -- every item in that bounded excerpt is
also a single, deterministically chosen vintage, never a mix of fields from
more than one stored row for its date.

### `recent_observations[]`

A bounded, read-only excerpt of up to `recent_observations_limit` most
recent **distinct** stored `observation_date` values for the series,
ordered **newest first**. Each item is exactly one deterministically chosen
vintage for its date (see "Vintage selection" above) and contains only:
`observation_date`, `value` (a decimal string, or `null` when
`is_missing` is `true`), `is_missing`, `realtime_start`, `realtime_end`,
and `evidence_id` (the same stable, code-generated ID scheme as the
series-level `evidence_id` -- the first item's `evidence_id` always equals
the entry's top-level `evidence_id`, since both are derived from the same
chosen latest row). This is a bounded excerpt of history, entirely separate
from `coverage` (below), which always reflects the **full** stored history
for the series regardless of `recent_observations_limit`. No
interpolation, forward-filling, seasonal adjustment, or value rewriting is
ever performed -- a gap between two stored dates (e.g. a missing monthly
release) is never filled in with a synthetic entry; only dates that are
actually stored appear.

### `latest_change_from_previous`

One precisely supported comparison between the latest and immediately
preceding chronological stored observation (i.e. the first two items of
`recent_observations`, when present):

```json
{
  "available": true,
  "unavailable_reason": null,
  "latest_observation_date": "2026-07-01",
  "latest_value": "5.330000",
  "latest_evidence_id": "macro_3f2a9c1d4e5b6789",
  "previous_observation_date": "2026-06-01",
  "previous_value": "5.000000",
  "previous_evidence_id": "macro_7a1b2c3d4e5f6081",
  "absolute_change_native_units": "0.330000",
  "direction": "increased"
}
```

- **`available`** -- `true` only when **all** of the following hold,
  checked in this order (the documented deterministic "comparable vintage
  series" rule):
  1. At least two distinct `observation_date` values are stored for the
     series (otherwise `unavailable_reason` is `"insufficient_history"`).
  2. The latest chosen row is not missing (otherwise
     `"latest_missing"`).
  3. The immediately preceding chosen row is not missing (otherwise
     `"previous_missing"`).
  4. Both chosen rows' `realtime_end` equal FRED's open-ended sentinel
     (`9999-12-31`) -- i.e. both are the series' **currently valid**
     revision for their date, not an already-superseded, bounded-window
     vintage (otherwise `"incomparable_vintage"`). Both rows were selected
     by the identical, fixed per-date vintage-selection rule described
     above, so "comparable" here means neither is stale, already-revised
     history.
- **`unavailable_reason`** -- `null` when `available` is `true`; otherwise
  exactly one of `"insufficient_history"`, `"latest_missing"`,
  `"previous_missing"`, or `"incomparable_vintage"` -- a small, fixed enum.
- **`absolute_change_native_units`** -- the **exact** `Decimal` subtraction
  `abs(latest_value - previous_value)`, formatted as a decimal string --
  never a float computation, so it never carries binary-float rounding
  artifacts. **Never** a percentage change, an annualized change, a
  basis-point change, or a "surprise" relative to a consensus expectation
  -- none of those are computed anywhere in this module.
- **`direction`** -- exactly one of `"increased"`, `"decreased"`, or
  `"unchanged"`, based only on comparing the two exact values. **Two
  observations are never described as a trend** -- this field describes
  one comparison between exactly two stored points, nothing more.
- When `available` is `false`, every other field is `null` -- no partial
  latest-only data is duplicated here (the entry's own top-level
  `latest_observation_date`/`latest_value` already report that).

### Freshness

- **`freshness.stale_after_days`** -- the fixed, documented elapsed-day
  threshold **actually applied** for this series, chosen deterministically
  from its stored `frequency_short` -- **never a single flat default across
  every series**:
  - `frequency_short == "M"` (monthly) -> `90` elapsed days (`STALE_AFTER_DAYS`),
    a conservative default suitable for monthly macro observations: it
    comfortably exceeds a series like FEDFUNDS's monthly reporting cadence
    without flagging a normal inter-release gap as stale.
  - `frequency_short == "Q"` (quarterly) -> `180` elapsed days
    (`STALE_AFTER_DAYS_QUARTERLY`), a separate, conservative threshold sized
    for quarterly release timing (e.g. GDPC1): it comfortably exceeds a
    quarterly series' ~3-month reporting cadence, including normal release
    lag, without flagging a normal inter-release gap as stale. A single flat
    (monthly-sized) threshold previously misclassified a legitimately fresh
    quarterly release as stale -- e.g. a GDPC1 observation dated the first
    day of the current calendar quarter, evaluated partway through the
    following quarter -- which motivated this split.
  - Any other case -- no stored metadata for the series
    (`metadata_available: false`), a stored metadata row whose
    `frequency_short` is `null`/blank, or any `frequency_short` value other
    than `"M"`/`"Q"` -- has **no defined threshold**: `stale_after_days` is
    `null` and the series **fails closed**, i.e. `freshness.stale` is always
    `true` for it, regardless of how recent its `observation_date` is. This
    is deliberate: the module never silently assigns a default threshold to
    a series whose actual official reporting frequency is unknown or
    unrecognized.

  This is a plain elapsed-time signal, not an economic judgment -- **a
  series reported under its threshold is not thereby claimed to be
  economically current**, only "not yet flagged stale by this fixed clock."
- **`freshness.stale`** -- `true` if `missing` is `true`, if
  `future_date_detected` is `true`, if `stale_after_days` is `null` (see
  above -- fail closed for missing/unrecognized frequency metadata), or if
  the chosen row's `observation_date` is more than `stale_after_days`
  before the snapshot's `as_of` date.
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
- **`title`**, **`frequency`**, **`frequency_short`**, **`units`**,
  **`seasonal_adjustment`** -- read exactly as stored in
  `macro_series_metadata`, verbatim, when `metadata_available` is `true`;
  otherwise `null`. These are **never inferred from the series ID itself**
  -- a well-known series ID (e.g. `FEDFUNDS`) with no stored metadata row
  always reports `null` for all five fields, never a guessed or hard-coded
  label. `frequency_short` is FRED's short, machine-stable frequency code
  (e.g. `"M"`, `"W"`, `"Q"`) -- distinct from the free-text `frequency`
  field, which can carry extra qualifiers (e.g. `"Weekly, Ending Friday"`).
  The Macro Analyst (see
  [docs/MACRO_ANALYST.md](MACRO_ANALYST.md)) uses `frequency_short`, not
  `frequency`, as the deterministic basis for its frequency-aware wording
  requirement.
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
  [DECISION_RULES.md](../DECISION_RULES.md)). The staleness threshold is
  frequency-aware but deliberately narrow: only `frequency_short` `"M"`
  (90 days) and `"Q"` (180 days) have a defined threshold; a series with no
  stored metadata or an unrecognized `frequency_short` fails closed
  (`stale_after_days: null`, `freshness.stale: true`) rather than reusing
  either threshold as a silent default. This is not a FRED-release-
  calendar-aware system for any other reporting frequency.
- This module performs **no** transformation, interpolation,
  forward-filling, or seasonal adjustment of any kind -- every value in
  `recent_observations` is reported exactly as stored, unmodified. The
  **one** derived quantity this module computes is
  `latest_change_from_previous`'s exact absolute difference and
  increased/decreased/unchanged direction between exactly two stored
  observations (see above) -- it never computes a percentage change, an
  annualized change, a basis-point change, a moving average, a
  period-over-period growth rate, or any comparison spanning more than two
  observations. Any such broader derived feature is separate, future,
  reviewed work.
- This module is intentionally not a general feature store: it has no
  plugin system, no caching layer, and makes no model request. A Macro
  Analyst agent (`market_intelligence/agents/macro_analyst.py`) is now
  built directly on this snapshot -- see
  [docs/MACRO_ANALYST.md](MACRO_ANALYST.md) for its own bounded contract,
  including how it uses `recent_observations` and
  `latest_change_from_previous`.

## Components

- `market_intelligence/market_features/macro_evidence.py` --
  `MacroEvidenceBuilder` and its validation helpers.
- `scripts/build_macro_evidence.py` -- the one-shot, read-only CLI entry
  point; prints one sanitized JSON snapshot to stdout per invocation.
- `market_intelligence/tests/test_macro_evidence.py` and
  `market_intelligence/tests/test_build_macro_evidence.py` -- tests against
  temporary DuckDB databases only; no live network access, no access to the
  real repository database.
