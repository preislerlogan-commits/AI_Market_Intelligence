# Market Context Snapshot

This document describes the read-only market-context snapshot layer added
under `market_intelligence/market_features/` and
`scripts/build_market_context.py`. It covers infrastructure only — see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Purpose and scope

`MarketContextBuilder`
(`market_intelligence/market_features/market_context.py`) builds exactly
one deterministic, JSON-ready snapshot dict from data **already stored** in
the local DuckDB database. It is read-only end to end:

- It makes **no network request** of any kind — no Alpaca, FRED, OpenAI, or
  Anthropic call. It does not import `httpx` or any connector's HTTP
  client.
- It opens the local database with `duckdb.connect(path, read_only=True)`
  and never writes a row, never applies a migration, and never touches
  `ingestion_runs` or any orchestration table.
- It does not import or call anything from
  `market_intelligence/data_connectors/`, `market_intelligence/storage/*_repository.py`,
  or `market_intelligence/orchestration/` beyond two small, already-reviewed
  validation helpers it reuses read-only (`normalize_symbol` from
  `alpaca_market_data.py`, `normalize_series_id` from `fred_macro_data.py`)
  and the existing `DuckDBManager`/`Clock` infrastructure for safe path
  resolution and an injectable "as of" instant.
- It never invents sentiment, a prediction, trading bias, a confidence
  score, an options recommendation, a support/resistance level, or any
  other agent conclusion. Every value in the snapshot is either
  provider-reported data already stored by an existing, reviewed ingestion
  pipeline, or this module's own provenance/coverage/staleness bookkeeping
  about that stored data.
- No snapshot is persisted anywhere. `scripts/build_market_context.py`
  prints one sanitized JSON snapshot to stdout and exits; nothing is
  written to the database, a file, or any other store.

This is intentionally a thin, single-purpose read layer for future AI
agents to consume — not a general feature-store framework, not a
dashboard, and not a scheduler.

## Command-line usage

```
python scripts/build_market_context.py --symbol SPY
python scripts/build_market_context.py --symbol SPY --recent-bars-limit 10 --recent-news-limit 3
python scripts/build_market_context.py --symbol SPY --macro-series FEDFUNDS --macro-series UNRATE
```

An unrecognized flag is rejected by `argparse` itself before anything else
runs. An invalid `--symbol`/`--recent-bars-limit`/`--recent-news-limit`/
`--macro-series` is rejected by `MarketContextBuilder`'s own validation
(see "Input validation" below) before any DuckDB connection is opened, and
is reported as a small sanitized JSON object
(`{"error": "invalid_input", "detail": "..."}`) with exit code `2`. A
database read failure is similarly reported
(`{"error": "storage_error", "detail": "..."}`) with exit code `1`,
never as a raw traceback, database path, or SQL text. A missing database
file is **not** treated as an error — see "Behavior with no stored data"
below.

## Input validation (before any DuckDB connection is opened)

- **`symbol`** — validated via the same `normalize_symbol` used by the
  existing Alpaca connectors: 1–10 characters, starts with a letter or
  digit, only letters/digits/`.`/`-`.
- **`recent_bars_limit`** / **`recent_news_limit`** — must be a plain
  `int` (`bool` is explicitly rejected, since `bool` is a subclass of
  `int` in Python), and must be within `[1, MAX_RECENT_BARS_LIMIT]` /
  `[1, MAX_RECENT_NEWS_LIMIT]` (each currently `50`). Zero, negative, and
  excessively large values are all rejected.
- **`macro_series_ids`** — must be a sequence of strings (a bare string is
  explicitly rejected, since a `str` is itself iterable character-by-
  character), at most `MAX_MACRO_SERIES_IDS` (`10`) entries, each
  validated via the same `normalize_series_id` used by
  `FredMacroDataClient`. The result is deduplicated and sorted, so
  snapshot ordering never depends on caller-supplied order.

## Snapshot field contract

All Decimal, date, and datetime values are serialized as plain strings so
the returned dict is directly `json.dumps`-able, and the same input always
produces the same output shape/ordering (aside from
`snapshot_created_at_utc`, which reflects the current instant, or an
injected clock in tests).

```
{
  "snapshot_created_at_utc": "2026-08-23T12:00:00Z",
  "symbol": "SPY",
  "request": {
    "symbol": "SPY",
    "recent_bars_limit": 5,
    "recent_news_limit": 5,
    "macro_series_ids": ["FEDFUNDS"]
  },
  "price": {
    "latest_bar_timestamp_utc": "2026-08-19T20:00:00Z" | null,
    "latest_close": "551.234500" | null,
    "short_return": {
      "period_bars": 5,
      "start_bar_timestamp_utc": "...",
      "start_close": "...",
      "end_bar_timestamp_utc": "...",
      "end_close": "...",
      "return_pct": "0.012345"
    } | null,
    "recent_bars": [
      {
        "bar_timestamp_utc": "...", "open": "...", "high": "...",
        "low": "...", "close": "...", "volume": 12345,
        "trade_count": 10 | null, "vwap": "..." | null
      }, ...
    ]
  },
  "bars_provenance": {
    "provider": "alpaca" | null, "symbol": "SPY", "timeframe": "5Min" | null,
    "feed": "iex" | null, "adjustment": "raw" | null, "currency": "USD" | null,
    "session_scope": "provider_returned_unfiltered"
  },
  "news": {
    "recent_articles": [
      {
        "provider_article_id": "...", "headline": "...", "source": "...",
        "created_at_utc": "..." | null, "related_symbols": ["SPY", ...]
      }, ...
    ]
  },
  "macro": {
    "series": [
      {
        "series_id": "FEDFUNDS", "has_stored_observation": true,
        "observation_date": "2026-07-01" | null,
        "is_missing": false | null, "value": "5.330000" | null,
        "coverage": {"row_count": 12, "earliest_observation_date": "...",
                     "latest_observation_date": "..."},
        "stale": false
      }, ...
    ]
  },
  "coverage": {
    "bars": {"row_count": 248, "earliest_bar_timestamp_utc": "...", "latest_bar_timestamp_utc": "..."},
    "news": {"row_count": 10, "earliest_created_at_utc": "...", "latest_created_at_utc": "..."}
  },
  "flags": {
    "bars_missing": false, "bars_stale": false,
    "news_missing": false, "news_stale": false,
    "macro_missing_series": [], "macro_stale_series": []
  }
}
```

### Price / bars

The snapshot picks exactly one bar **identity**
(`provider, symbol, timeframe, feed, adjustment, currency`) — whichever
identity produced the single most recent stored bar for the requested
symbol — and reports `recent_bars`, `latest_close`, `short_return`, and
`coverage.bars` only from that one identity. If the same symbol has bars
stored under more than one identity (for example, both `1Min` and `5Min`
IEX bars), bars from any *other* identity are not mixed into the same
series; `bars_provenance` records exactly which identity was used.

`short_return` is only present when at least `RETURN_PERIOD_BARS + 1`
bars (currently 6) exist for that identity; otherwise it is `null`. It is
a plain, already-stored-data percentage change between the latest close
and the close `RETURN_PERIOD_BARS` bars earlier — never a forecast,
never annualized, and not adjusted for corporate actions beyond whatever
adjustment the stored bars already carry.

`coverage.bars` reports the full stored row count and earliest/latest
timestamp for the chosen identity — which may cover more history than the
bounded `recent_bars` list — so a consumer can tell how much data actually
backs the snapshot.

`price.latest_close` is simply the `close` value of whichever bar happens
to be the most recently stored one for the chosen identity — it is **not**
labeled or guaranteed to be an official regular-session market close.
`bars_provenance.session_scope` (always the fixed value
`"provider_returned_unfiltered"`) exists precisely to flag this: bars are
stored exactly as the provider (Alpaca) returns them for the requested
feed, with no regular-trading-hours (RTH) filter applied anywhere in this
project's ingestion or this snapshot layer, so the "latest" bar may reflect
a pre-market or after-hours observation. A consumer that specifically
needs an RTH-only closing price must filter for that itself; this module
makes no such determination.

### News

`news.recent_articles` matches on `related_symbols` containing the
requested symbol, ordered newest-first by `created_at` (nulls last, then
`provider_article_id` for a fully deterministic tie-break), bounded by
`recent_news_limit`. Only provider-reported metadata is included — no
headline sentiment, sourced summary text, or full article body.

### Macro

`macro.series` reports exactly one entry per requested (deduplicated,
sorted) series ID — even a series with no stored observations at all still
gets an entry, with `has_stored_observation: false`. For a series that
does have a stored observation, the entry reflects the single most recent
`(observation_date, realtime_start, realtime_end)` vintage on file — not
necessarily the most recently *ingested* row, if FRED has reported a
revision. `is_missing`/`value` mirror FRED's own `"."` missing-observation
marker as already stored (see
[docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)); this is a
distinct concept from `has_stored_observation`, which instead means "no
row exists in `macro_observations` for this series at all."

## Missing / stale-data flags

- **`bars_missing`** / **`news_missing`** — true if no stored row matches
  the requested symbol at all (including when the underlying table itself
  does not exist yet, e.g. before migration `0005`/`0004` has been
  applied, or before the database file exists).
- **`macro_missing_series`** — the subset of requested series IDs with no
  stored observation at all (`has_stored_observation: false`).
- **`bars_stale`** / **`news_stale`** / each macro series's `stale` — a
  simple, fixed-threshold data-freshness signal, comparing the snapshot's
  "as of" instant against the latest stored timestamp/date for that
  section:
  - Bars: stale if the latest stored bar is more than `BARS_STALE_AFTER`
    (72 hours) old. This is a plain elapsed-time threshold, not an
    exchange-calendar or holiday-aware one. It is deliberately
    weekend-tolerant: 72 hours comfortably spans a normal Friday-close-to-
    Monday-open gap, so a stored 5-minute bar from Friday afternoon is
    correctly reported `bars_stale: false` on Saturday (and, in most
    cases, still on Sunday), instead of being falsely flagged stale
    simply because no *new* bar was expected over the weekend. The
    tradeoff is that it is slower to flag staleness caused by a multi-day
    market holiday or a real ingestion gap. The actual latest stored bar
    timestamp remains available in `price.latest_bar_timestamp_utc` and
    `coverage.bars.latest_bar_timestamp_utc` regardless of this flag, so a
    future consumer that needs a stricter, calendar-aware staleness
    decision (e.g. one that knows which days are actual trading days) can
    compute it from that raw timestamp itself.
  - News: stale if there are no articles, the newest article has no
    `created_at`, or the newest `created_at` is more than
    `NEWS_STALE_AFTER` (7 days) old.
  - Macro: stale if the latest stored observation date is more than
    `MACRO_STALE_AFTER` (90 days) old (chosen to comfortably exceed
    FEDFUNDS's monthly reporting cadence without flagging normal
    inter-release gaps as stale).

  These thresholds are fixed, documented constants, not a market-hours- or
  series-cadence-aware calendar. This is a known, deliberate limitation:
  each flag answers "is this data fresh by a fixed clock," not "is this
  data unexpectedly stale given the instrument's own trading schedule."

## Behavior with no stored data

A missing database file, an existing-but-empty database, or a database
that has not yet had a given migration applied are all treated as **valid,
non-error** input: `build_snapshot()`/the CLI still return/print a
complete snapshot with every section's `missing`/`stale` flags `true`,
empty `recent_bars`/`recent_articles` lists, and every requested macro
series reported `has_stored_observation: false`. This is deliberate: this
snapshot layer must also work correctly before any ingestion has ever run,
so a future agent calling it early in the project's life gets an honest
"nothing stored yet" answer instead of a crash.

## Known limitations

- This snapshot only ever reflects whatever has already been ingested and
  stored by the existing, separately reviewed Alpaca bars/news and FRED
  observations pipelines (see
  [DATA_CATALOG.md](../DATA_CATALOG.md)/[PROJECT_STATE.md](../PROJECT_STATE.md)).
  It cannot backfill gaps, and a snapshot immediately after a single
  bounded ingestion run will only ever cover that run's bounded window —
  it is not a claim of complete or gap-free market coverage.
- The short-period return and staleness thresholds are simple, fixed,
  documented heuristics for data bookkeeping — they are not trading
  signals, are not validated forecasts, and must not be treated as such
  (see [DECISION_RULES.md](../DECISION_RULES.md)).
- `DEFAULT_MACRO_SERIES_IDS` currently contains only `FEDFUNDS`, mirroring
  the one macro series this project currently ingests
  (`market_intelligence/orchestration/jobs.json`). Requesting any other
  series ID is supported, but will simply report
  `has_stored_observation: false` unless/until that series is separately
  ingested.
- This module is intentionally not a general feature store: it has no
  plugin system, no caching layer, and no support for arbitrary derived
  indicators. Any additional derived feature is separate, future,
  reviewed work.

## Components

- `market_intelligence/market_features/market_context.py` —
  `MarketContextBuilder` and its validation helpers (see above).
- `scripts/build_market_context.py` — the one-shot, read-only CLI entry
  point; prints one sanitized JSON snapshot to stdout per invocation.
- `market_intelligence/tests/test_market_context.py` and
  `market_intelligence/tests/test_build_market_context.py` — tests against
  mocked/temporary DuckDB databases only; no live network access, no
  access to the real repository database.
