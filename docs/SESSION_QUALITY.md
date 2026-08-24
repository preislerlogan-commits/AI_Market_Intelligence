# Session Quality

This document describes the read-only session-quality report layer added
under `market_intelligence/market_features/session_quality.py` and
`scripts/build_session_quality.py`. It covers infrastructure only -- see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Purpose and scope

`SessionQualityBuilder` builds exactly one deterministic, JSON-ready report
telling a future agent whether stored `5Min` bars for one symbol/session
date represent the regular trading session, and whether that session is
complete enough to analyze -- **before** any forecast, feature, or decision
logic touches that data. It is read-only end to end:

- No network request of any kind.
- Opens the local database only via `duckdb.connect(path, read_only=True)`;
  never writes a row, never applies a migration.
- No prediction, recommendation, trading signal, or confidence score --
  every value is either a stored value from an existing, reviewed ingestion
  pipeline, or this module's own weekday/time-window bookkeeping about it.

This is intentionally a thin, single-purpose read layer -- not a general
indicator engine, not an exchange-calendar framework, not a scheduler, and
not a dashboard.

## Regular session definition (weekday/time-window only)

A stored `5Min` bar is a **regular-session bar** if, converted to
`America/New_York` via Python `zoneinfo`, it falls on a Monday-Friday
calendar date and its time-of-day is one of the 78 five-minute slots from
`09:30` through `15:55` inclusive (the last slot *begins* at `15:55` and
covers `15:55`-`16:00`).

**This is weekday/time-window logic only.** It is **not** an exchange-
holiday or early-close calendar: U.S. market holidays (e.g. Thanksgiving,
Christmas) and early-close days (e.g. the day after Thanksgiving) are not
detected or excluded. A stored date that happens to be a holiday is
evaluated with this exact same rule and will be reported incomplete/missing
-- never flagged as "no session was expected that day." A future,
calendar-aware consumer must apply that logic itself.

## Bar identity

Exactly one bar identity -- `(provider, symbol, timeframe='5Min', feed,
adjustment, currency)` -- is used for the entire report, selected as
whichever identity produced the most recently stored `5Min` bar for the
requested symbol. Bars from any other identity are never mixed into the
same report; `bar_provenance` records exactly which identity was used. If
the selected identity's coverage does not include the requested/resolved
session date, the report simply shows that date as fully missing for that
identity -- it does not fall back to a different identity.

## Command-line usage

```
python scripts/build_session_quality.py --symbol SPY
python scripts/build_session_quality.py --symbol SPY --session-date 2026-08-19
```

Omitting `--session-date` auto-selects the most recent stored date (under
the selected identity) with at least one regular-session bar. An
unrecognized flag is rejected by `argparse` before anything else runs. An
invalid `--symbol`/`--session-date` is rejected by `SessionQualityBuilder`
before any DuckDB connection is opened, reported as
`{"error": "invalid_input", "detail": "..."}` with exit code `2`. A
database read failure is reported as `{"error": "storage_error", ...}`
with exit code `1`, never as a raw traceback, path, or SQL. Any other
unexpected failure prints only `{"error": "unexpected_error"}`.

## Report field contract

All Decimal and timestamp values are serialized as plain strings. Every
bar-derived timestamp is UTC (`Z` suffix, matching `market_bars.bar_timestamp`'s
stored convention); `session_date_et` is an explicit America/New_York
calendar date (`YYYY-MM-DD`), never conflated with a UTC date.

```
{
  "generated_at_utc": "2026-08-23T12:00:00Z",
  "symbol": "SPY",
  "session_date_et": "2026-08-19" | null,
  "session_date_source": "explicit" | "auto_most_recent_with_regular_bars" | "none_available",
  "bar_provenance": {
    "provider": "alpaca" | null, "symbol": "SPY", "timeframe": "5Min",
    "feed": "iex" | null, "adjustment": "raw" | null, "currency": "USD" | null
  },
  "session_definition": {
    "timezone": "America/New_York",
    "regular_session_start_et": "09:30",
    "regular_session_last_slot_start_et": "15:55",
    "slot_interval_minutes": 5,
    "expected_slot_count": 78,
    "definition_type": "weekday_time_window_only",
    "limitations": "..."
  },
  "completeness": {
    "expected_slot_count": 78,
    "observed_regular_session_slot_count": 0,
    "missing_expected_timestamps_utc": ["2026-08-19T13:30:00Z", ...],
    "unexpected_or_duplicate_timestamps_utc": [],
    "complete": false,
    "partial_session": false,
    "missing_data": true
  },
  "regular_session": {
    "first_timestamp_utc": "..." | null,
    "last_timestamp_utc": "..." | null,
    "open": "551.230000" | null,
    "latest_close": "552.100000" | null,
    "latest_close_is_full_session_close": false,
    "return_pct": "0.001580" | null,
    "high": "..." | null, "low": "..." | null, "range": "..." | null,
    "total_volume": 123456 | null,
    "vwap": "..." | null
  },
  "same_date_bars": {
    "premarket_count": 0,
    "after_hours_count": 0
  }
}
```

### Completeness

`expected_slot_count` is the fixed constant `78`. `missing_expected_timestamps_utc`
lists, in order, the exact UTC bar timestamps that would need to appear in
`market_bars` (under the selected identity) to fill each missing slot.
`unexpected_or_duplicate_timestamps_utc` lists any same-date, in-window
stored timestamp that does **not** correspond to a valid regular-session
slot -- e.g. a non-weekday date, or a bar whose time-of-day is not aligned
to the 5-minute grid -- plus (defensively) any exact-duplicate timestamp,
though the `market_bars` primary key already makes a true duplicate
impossible in practice. `complete` is `true` only when all 78 slots are
observed; `partial_session` is `true` when some but not all are observed;
`missing_data` is `true` when zero regular-session bars were found at all.

### Regular session

`open` is populated only from a bar observed at the exact `09:30` slot;
`latest_close` is the close of whichever regular-session bar is
chronologically last among the *observed* bars (which may not be the
`15:55` slot if the session is partial). **`latest_close_is_full_session_close`
is `true` only when `completeness.complete` is `true`** -- this report never
implies a partial session's latest observed close is the official regular-
session close. `return_pct` is present only when both `open` and
`latest_close` are available; it is a plain historical percentage change,
never a forecast or annualized figure. `high`/`low`/`range`/`total_volume`
are computed only from observed regular-session bars. `vwap` is computed as
a volume-weighted average of each observed regular-session bar's own stored
`vwap` (`sum(vwap * volume) / sum(volume)`) and is `null` whenever any
observed regular-session bar has a `null` stored `vwap`, since a partial
aggregate would misrepresent the actual weighted price.

### Same-date bars

`premarket_count` / `after_hours_count` count same-(ET)-date stored bars
(same identity) strictly before `09:30` or strictly after `15:55`,
respectively -- context only, never included in any regular-session
computation.

## Behavior with no stored data

A missing database file, an existing-but-empty database, a database before
migration `0005` has been applied, a symbol with no stored `5Min` bars, or
a resolved/requested date with zero regular-session bars are all valid,
non-error input: the report is still returned, with `bar_provenance`
fields `null` where nothing was found, `session_date_source` set
appropriately (`"none_available"` when auto-selection found no candidate
date), and `completeness.missing_data: true`. This module never crashes on
absent data -- see the module docstring in `session_quality.py`.

## Known limitations

- **Weekday/time-window only, not an exchange calendar** -- see "Regular
  session definition" above. This is the single most important limitation:
  a genuine market holiday is reported exactly like a real data gap.
- **Single fixed timeframe (`5Min`).** A different timeframe is out of
  scope for this module.
- **Single fixed identity per report.** If a symbol's most-recently-stored
  identity does not cover the requested/resolved date, the report shows
  that date as missing for that identity rather than searching other
  identities.
- This module makes no claim about market-hours accuracy beyond the fixed
  weekday/time-window rule above; it is a data-quality/coverage report, not
  a trading signal (see [DECISION_RULES.md](../DECISION_RULES.md)).

## Components

- `market_intelligence/market_features/session_quality.py` --
  `SessionQualityBuilder` and its validation helpers.
- `scripts/build_session_quality.py` -- the one-shot, read-only CLI entry
  point; prints one sanitized JSON report to stdout per invocation.
- `market_intelligence/tests/test_session_quality.py` and
  `market_intelligence/tests/test_build_session_quality.py` -- tests
  against temporary DuckDB databases only; no live network access, no
  access to the real repository database.
