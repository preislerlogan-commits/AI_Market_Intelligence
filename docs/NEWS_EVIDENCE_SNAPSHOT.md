# News Evidence Snapshot

This document describes the read-only news-evidence snapshot layer added
under `market_intelligence/market_features/news_evidence.py` and
`scripts/build_news_evidence.py`. It covers infrastructure only -- see
[PROJECT_STATE.md](../PROJECT_STATE.md) and [DATA_CATALOG.md](../DATA_CATALOG.md)
for what data has (and has not) actually been ingested.

## Purpose and scope

`NewsEvidenceBuilder` builds exactly one deterministic, JSON-ready snapshot
dict from data **already stored** in the `news_articles` table (migration
`0004`, see [docs/STORAGE_ARCHITECTURE.md](STORAGE_ARCHITECTURE.md)). It is
**infrastructure for a future News Analyst agent** -- it is not an AI agent
itself, and building a snapshot never makes a model request of any kind.

It is read-only end to end:

- It makes **no network request** of any kind -- no Alpaca, FRED, OpenAI, or
  Anthropic call. It imports no HTTP client and no model client.
- It opens the local database only with `duckdb.connect(path, read_only=True)`
  and never writes a row, never applies a migration, and never touches
  `ingestion_runs` or any orchestration table.
- The only thing it reuses from `market_intelligence/data_connectors/` is
  `normalize_symbol` -- a small, already-reviewed, read-only validation
  helper -- mirroring how `market_context.py`/`session_quality.py` reuse the
  same helper. It never imports a connector's HTTP client, a storage
  repository's write path, or any model client.
- It never interprets, sanitizes semantically, summarizes, classifies,
  scores sentiment, or infers market impact from a headline or provider
  summary. Every article's `headline` and `provider_summary` are preserved
  **exactly as stored** -- both are treated as untrusted, third-party
  provider text throughout.

## Command-line usage

```
python scripts/build_news_evidence.py --symbol SPY
python scripts/build_news_evidence.py --symbol SPY --limit 5
```

An unrecognized flag is rejected by `argparse` itself before anything else
runs. An invalid `--symbol`/`--limit` is rejected by `NewsEvidenceBuilder`'s
own validation (see "Input validation" below) before any DuckDB connection
is opened, and is reported as a small sanitized JSON object
(`{"error": "invalid_input", "detail": "..."}`) with exit code `2`. A
database read failure is similarly reported
(`{"error": "storage_error", "detail": "..."}`) with exit code `1`, never as
a raw traceback, database path, or SQL text. A missing database file is
**not** treated as an error -- see "Behavior with no stored data" below.

## Input validation (before any DuckDB connection is opened)

- **`symbol`** -- validated via the same `normalize_symbol` used by the
  existing Alpaca connectors and by `MarketContextBuilder`/
  `SessionQualityBuilder`: 1-10 characters, starts with a letter or digit,
  only letters/digits/`.`/`-`.
- **`limit`** -- must be a plain `int` (`bool` is explicitly rejected, since
  `bool` is a subclass of `int` in Python), within `[1, 20]`. Zero,
  negative, and excessively large values are all rejected. Default `10`.

## Snapshot field contract

All datetime values are serialized as plain RFC3339 UTC strings so the
returned dict is directly `json.dumps`-able, and the same stored data always
produces the same output shape/ordering (aside from
`snapshot_created_at_utc`, which reflects the current instant, or an
injected clock in tests).

```json
{
  "snapshot_created_at_utc": "2026-08-24T12:00:00Z",
  "symbol": "SPY",
  "request": {"symbol": "SPY", "limit": 10},
  "article_count_returned": 3,
  "total_stored_article_count_for_symbol": 12,
  "coverage": {
    "earliest_published_at_utc": "2026-08-01T09:00:00Z",
    "latest_published_at_utc": "2026-08-20T12:00:00Z"
  },
  "freshness": {
    "stale_after_hours": 168,
    "missing": false,
    "stale": false
  },
  "articles": [
    {
      "evidence_id": "news_3f2a9c1d4e5b6789",
      "provider": "alpaca",
      "provider_article_id": "12345",
      "source": "benzinga",
      "published_at_utc": "2026-08-20T12:00:00Z",
      "updated_at_utc": "2026-08-20T12:05:00Z",
      "retrieved_at_utc": "2026-08-20T12:10:00Z",
      "headline": "Fed signals rate pause",
      "provider_summary": "A summary." ,
      "related_symbols": ["SPY"],
      "content_scope": "headline_and_provider_summary"
    }
  ],
  "audit_provenance": {
    "note": "Provenance/audit only ... MUST exclude this field from any payload sent to a model.",
    "articles": [
      {"evidence_id": "news_3f2a9c1d4e5b6789", "article_url": "https://example.com/news/12345"}
    ]
  }
}
```

### Top-level fields

- **`article_count_returned`** -- the number of articles actually included
  in `articles` (bounded by `limit`).
- **`total_stored_article_count_for_symbol`** -- the full count of stored
  articles related to the requested symbol, regardless of `limit`. May be
  larger than `article_count_returned`.
- **`coverage`** -- the earliest/latest stored publication (`created_at`)
  timestamp across *all* stored articles for the symbol, not just the
  returned page.
- **`freshness`** -- a plain, fixed-threshold data-freshness signal (see
  below), not a market-relevance or sentiment judgment.

### `articles[]`

Every entry contains only provider-reported metadata plus this module's own
bookkeeping -- never a URL (see "Article URLs and `audit_provenance`"
below), never sentiment, never an inferred category, never a model output.

- **`evidence_id`** -- a **stable, code-generated** identifier: the first 16
  hex characters of `sha256(f"{provider}:{provider_article_id}")`, prefixed
  `news_`. It is derived only from `(provider, provider_article_id)` --
  never from headline/summary text -- so the same stored article always
  produces the same `evidence_id` across snapshots, calls, and processes,
  regardless of ordering or which other articles are returned in a given
  call.
- **`provider_summary`** -- `null` when the provider did not report a
  summary for this article; otherwise the provider's summary text preserved
  exactly as stored.
- **`content_scope`** -- `"headline_only"` when `provider_summary` is
  `null`, otherwise `"headline_and_provider_summary"`. This tells a future
  consumer how much text is actually available to reason about for this
  article, without it having to inspect `provider_summary` itself.
- **`headline`** / **`provider_summary`** -- preserved byte-for-byte as
  stored. This module performs **no** HTML stripping, prompt-injection
  filtering, truncation, whitespace normalization, or semantic
  interpretation of either field. Any future consumer (e.g. a News Analyst
  passing this data to a model) is responsible for treating this text as
  untrusted, exactly as `OpenAIStructuredClient` already treats evidence
  dicts passed to it (see
  [docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md) and
  [docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md)).

### Article URLs and `audit_provenance`

Per this project's requirement that a future model-facing evidence payload
must never include article URLs, `article_url` is **not** a field on any
entry in `articles[]`. It is available only in the separate top-level
`audit_provenance.articles[]` list, keyed by the same `evidence_id`, for
provenance/audit purposes (e.g. a human reviewing where a cited fact
actually came from).

**A future News Analyst (or any other model-facing consumer of this
snapshot) MUST exclude `audit_provenance` from whatever payload it sends to
a model.** `audit_provenance.note` states this directly in the snapshot
itself, so a consumer that only skims the JSON structure still encounters
the constraint. This module cannot enforce that exclusion by itself --
enforcement is the responsibility of the future model-facing component that
consumes this snapshot, the same way `OpenAIStructuredClient`'s evidence
labeling is a mitigation, not a guarantee (see
[docs/OPENAI_PROVIDER_BOUNDARY.md](OPENAI_PROVIDER_BOUNDARY.md)).

### Freshness

- **`freshness.missing`** -- `true` if no stored article matches the
  requested symbol at all (including when the underlying table itself does
  not exist yet, e.g. before migration `0004` has been applied, or before
  the database file exists).
- **`freshness.stale`** -- `true` if `missing` is `true`, if the latest
  stored article has no `created_at` (publication timestamp unknown), or if
  the latest stored `created_at` is more than `stale_after_hours` (168
  hours / 7 days, mirroring `MarketContextBuilder`'s `NEWS_STALE_AFTER`
  rationale) old. This is a plain elapsed-time threshold, not a
  market-calendar- or news-cadence-aware one -- it says "is this data fresh
  by a fixed clock," not "is this data unexpectedly stale given how often
  this symbol is normally covered."

## Ordering

Articles are returned **newest-first** by `published_at_utc` (`created_at`,
`NULLS LAST`), with `provider_article_id` (ascending) as a stable
tie-breaker for articles sharing the same `created_at` -- so the returned
order is fully deterministic and reproducible across repeated calls against
the same stored data.

## Behavior with no stored data

A missing database file, an existing-but-empty database, a database that
has not yet had migration `0004` applied, or a symbol with no matching
stored articles are all treated as **valid, non-error** input:
`build_snapshot()`/the CLI still return/print a complete snapshot with
`freshness.missing`/`freshness.stale` both `true`, an empty `articles` list,
an empty `audit_provenance.articles` list, and zeroed counts. This mirrors
`MarketContextBuilder`'s and `SessionQualityBuilder`'s behavior: this
snapshot layer must also work correctly before any news ingestion has ever
run.

## Errors and sanitization

`NewsEvidenceError` (and its `NewsEvidenceValidationError` subclass) never
include the database path, SQL text, credentials, or a raw underlying
exception -- only a fixed, non-input-derived description. A close() failure
after an otherwise-successful read raises a sanitized `NewsEvidenceError`
distinct from a read failure; a close() failure that follows an already
-failed, already-sanitized read never replaces or masks that original
error -- the close failure is swallowed and the original sanitized error is
what the caller sees. The connection opened by `build_snapshot()` is always
closed exactly once, on every code path (successful read, failed read, or
open failure).

## Known limitations

- This snapshot only ever reflects whatever has already been ingested and
  stored by the existing, separately reviewed Alpaca news pipeline (see
  [DATA_CATALOG.md](../DATA_CATALOG.md)/[PROJECT_STATE.md](../PROJECT_STATE.md)).
  It cannot backfill gaps, and a snapshot immediately after a single bounded
  ingestion run only ever covers that run's bounded window.
- The freshness threshold is a simple, fixed, documented heuristic for data
  bookkeeping -- it is not a trading signal, is not a validated forecast,
  and must not be treated as such (see
  [DECISION_RULES.md](../DECISION_RULES.md)).
- This module performs no sentiment, relevance, or quality scoring of
  articles beyond the `content_scope`/freshness bookkeeping described
  above. It also performs no deduplication of near-duplicate articles
  (e.g. two providers reporting the same underlying story) -- each stored
  `(provider, provider_article_id)` row is reported independently.
- This module is intentionally not a general feature store or a News
  Analyst: it has no plugin system, no caching layer, and makes no model
  request. Building an actual News Analyst agent on top of this snapshot is
  separate, future, reviewed work -- see
  [docs/MARKET_EVIDENCE_AGENT.md](MARKET_EVIDENCE_AGENT.md) for the
  equivalent pattern already used for market/session evidence.

## Components

- `market_intelligence/market_features/news_evidence.py` --
  `NewsEvidenceBuilder` and its validation helpers.
- `scripts/build_news_evidence.py` -- the one-shot, read-only CLI entry
  point; prints one sanitized JSON snapshot to stdout per invocation.
- `market_intelligence/tests/test_news_evidence.py` and
  `market_intelligence/tests/test_build_news_evidence.py` -- tests against
  temporary DuckDB databases only; no live network access, no access to the
  real repository database.
