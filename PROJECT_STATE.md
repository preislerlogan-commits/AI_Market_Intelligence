# Project State

This document is the **authoritative source of truth** for the current status
of AI Market Intelligence. It must be read before beginning any work in this
repository, and updated whenever the project's status materially changes.

Last updated: 2026-08-25

## Current Phase

**Phase 0 — Infrastructure Foundation**

The project is in initial scaffolding. Python environment and dependency
configuration are in place. Read-only Alpaca market-data provider
connectivity has been verified (a single read-only snapshot request — see
Status below). Read-only FRED macroeconomic-data provider connectivity has
also been verified (a single read-only latest-observation request — see
Status below). Read-only Alpaca news provider connectivity has also been
verified (a single read-only SPY-news request — see Status below), and one
explicitly authorized live SPY news ingestion has succeeded through the
storage pipeline (10 received, 10 inserted, 0 failed — see Status below).
A read-only Alpaca historical stock-bars connector now also exists
(`AlpacaBarsClient`). Its first authorized live connectivity check reached
Alpaca but was configured yet unsuccessful (sanitized status
`configured=True, success=False, status_category=4xx`) under the prior,
implicit-SIP default request; the connector was then hardened to
explicitly request the IEX feed, and a second authorized live connectivity
check against the hardened, explicit-IEX connector has now succeeded
(sanitized status `configured=True, success=True, status_category=2xx`,
single-symbol SPY, `5Min` timeframe, `feed=iex`, 5 bars returned — see
Status below). This confirms live connectivity and response normalization
on the IEX feed only; it does not confirm SIP connectivity, and IEX's
narrower single-exchange coverage still applies. **The explicit-IEX bars
connector remains live connectivity-verified as described above** — it has
since also been hardened to explicitly send fixed `adjustment=raw` and
`currency=USD` provenance (alongside the existing `feed=iex`). The earlier
five-bar connectivity check predated this additional hardening, but the
subsequent authorized 2026-08-21 ingestion (see Status below) exercised the
hardened `feed=iex`, `adjustment=raw`, `currency=USD` request combination
live. Connectivity/one successful ingestion run is not the same as a
validated, cataloged data pipeline: no complete, gap-free, bulk,
catalog-validated, or research-validated provider dataset exists yet. A
market-bar storage schema and
repository (`market_bars`, migration `0005`, `BarRepository`) now also
exist as schema/storage-capability infrastructure, originally covered by
tests using temporary databases only. **A separately authorized real-database
migration and ingestion run has since applied migration `0005` to the real
local database and stored a first batch of real bars** (see Status below)
— this is one controlled ingestion run, not a complete, gap-free, or
validated historical dataset. A local DuckDB storage foundation exists; the
real local database was backed up and then upgraded to schema version
`0005` (5 migrations applied) via that authorized run, and a subsequent
read-only health check reported it healthy (see Status below). **One stored
news ingestion also still exists** (10 SPY articles, see below). A
FRED historical-observations connector method, macro-observations schema
(migration `0006`), and repository were added as infrastructure for a
future Macro Analyst agent, initially in repository code and tests only;
a read-only health check against the still-`0005` real database at that
time reported `healthy=False` (behind the latest available migration —
see Status below, preserved as an honest diagnostic record and not
retracted). **On 2026-08-21, a separately authorized run backed up the
real database and applied migration `0006`**, after which a health check
reported the database healthy at schema version `0006` (see Status
below). **A separately authorized first live FRED historical-observations
ingestion (FEDFUNDS, 2025-08-01 through 2026-07-31) then also succeeded**
(see Status below) — this confirms one bounded historical fetch, response
normalization, transactional storage, and local retrieval; it is not a
complete, gap-free, broadly cataloged, or research-validated macro
dataset. **A conservative ingestion-orchestration layer was then added
(2026-08-23) covering exactly the three existing reviewed jobs (Alpaca
news, Alpaca bars, FRED observations), and a first authorized live
orchestration run then succeeded the same day** (see Status below) —
this confirms one controlled, explicitly authorized orchestrated
ingestion run; it does not confirm scheduling, continuous or unattended
operation, dataset completeness, prediction, agent intelligence, options
analysis, or trading execution. A first authorized live OpenAI
structured-output provider connectivity check has also since succeeded
(2026-08-24, see Status below, superseding the earlier "no live OpenAI
request" status) — this confirms only that the existing OpenAI provider
boundary can reach OpenAI, authenticate, and receive/parse one minimal
structured-output response; it is not an agent, prediction, recommendation,
or market-analysis capability. A narrow, single-turn Market Evidence Agent
has also since been added (2026-08-24, code/tests/docs only — see Status
below and
[docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md)): it
summarizes and organizes already-stored evidence behind a deterministic
preflight gate. **A first authorized live run has since been made (also
2026-08-24, see Status below): a dry run against the real database
succeeded (eligible, 20 evidence items), and one authorized live
`--execute` attempt failed structured-output validation
(`OpenAIParseFailureError`) — no analysis was accepted from that attempt.**
**A separately authorized follow-up `--execute` attempt, made the same day
after the PR #20 structured-output hardening described below, then
succeeded: one live analysis was accepted end to end (see the "First
completed live Market Evidence Agent run" Status entry below and
[docs/MARKET_EVIDENCE_EVALUATIONS.md](docs/MARKET_EVIDENCE_EVALUATIONS.md)
for the sanitized record and a manual quality read of that one output —
that manual read is one example, not a validated evaluation methodology or
a claim of factual accuracy).** It is not integrated into
`market_intelligence/orchestration/`. `directional_assessment`/
`trade_recommendation` on every report it produces are always the fixed
value `"not_performed"` — the model-facing schema does not even include
those fields, so this restriction is absolute. Its free-text fields are
additionally screened by a deterministic, fail-closed post-response content
policy check (added 2026-08-24, code/tests/docs only, see
[docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md)) that rejects
known directional-prediction, bullish/bearish-bias, trade-recommendation/
action, and options-related language — a conservative, bounded filter and
defense-in-depth on top of its developer instructions, not proof that every
possible semantic violation is detectable. A second narrow agent, the News
Analyst (`market_intelligence/agents/news_analyst.py`), has also since been
added (2026-08-24, code/tests/docs only — see Status below and
[docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)): it extracts and organizes
provider-reported event claims and conditional market-transmission
mechanisms from already-stored news, built on the existing
`NewsEvidenceBuilder` snapshot behind its own deterministic preflight gate.
It shares the Market Evidence Agent's non-directional guarantee
(`directional_assessment`/`trade_recommendation` always `"not_performed"`,
the model-facing schema excludes those fields entirely) and reuses the same
extracted, shared post-response content-policy matcher
(`market_intelligence/agents/non_directional_output_policy.py`). **A
separately authorized live `--execute` attempt has since been made (also
2026-08-24, symbol SPY, limit=5, see Status below): the deterministic
preflight passed and exactly one live OpenAI request was sent, but that
request failed structured-output validation
(`response_validation_failed`) — no analysis was accepted, and no retry was
made.** Offline diagnosis and recurrence-reduction hardening (conservative
advisory output budgets, added to `AGENT_INSTRUCTIONS` with margin below
every corresponding hard Pydantic maximum) followed the same pattern
already used for the Market Evidence Agent's own 2026-08-24
`response_validation_failed` failure — see the "News Analyst live execute
attempt" Status entry below and
[docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)/[docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)
for full detail. No hard schema bound, citation validation, content-basis
validation, output-policy validation, or the zero-retry behavior was
weakened. **This one-failed-attempt status has since been superseded: three
further separately authorized live attempts completed the sequence
(incomplete at `max_output_tokens=2048`, a local timeout at the then-default
30-second timeout, then a completed run with `max_output_tokens=4096`/
`timeout=120s`) — see the "News Analyst live run sequence completed" Status
entry below. A manual quality read of that completed run's event claims
found one weak, speculative claim built from an article that should not
have been turned into a claim at all, which motivated a market-relevance
hardening change (a required `relevance` classification/rationale per event
claim, new post-response relevance validation, a truthful — reason-free —
deterministic limitation noting how many supplied articles were not
included in retained claims, a safe code-controlled abstained outcome for
when no supplied article is sufficiently relevant, and `Settings`' own
`openai_max_output_tokens`/`openai_request_timeout_seconds` defaults
corrected to 4096/120) — see the same Status entry and
[docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md).** Beyond these two narrow
agents, no other AI analysis or agent orchestration (in the AI-agent sense)
exists yet. A read-only Macro Evidence Snapshot layer
(`market_intelligence/market_features/macro_evidence.py`,
`scripts/build_macro_evidence.py`) has also since been added (2026-08-24,
code/tests/docs only -- see Status below and
[docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md)),
mirroring `MarketContextBuilder`'s/`NewsEvidenceBuilder`'s pattern for
already-stored FRED macro observations. **This is infrastructure for a
future Macro Analyst agent, not an agent itself** -- it makes no model
request, no FRED request, and no prediction, market-regime label, or
transmission-mechanism inference of any kind, and it has not been run
against the real local database as part of this change. A narrow FRED
series-*metadata* pipeline (as distinct from the series *observations*
pipeline above) has also since been added
(2026-08-24, code/tests/docs only -- see Status below and
[docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)/
[docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md)):
`FredMacroDataClient.get_series_metadata()`, migration `0008`
(`macro_series_metadata`), `MacroSeriesMetadataRepository`,
`scripts/ingest_fred_series_metadata.py`, and a read-only
`MacroEvidenceBuilder` update (`metadata_available` plus `title`,
`frequency`, `units`, `seasonal_adjustment` per series, plus an aggregate
`missing_metadata_series` flag) exist so a future Macro Analyst never
interprets an unlabeled number. **This is infrastructure only, exists in
code and tests only, and has not been run live or against the real local
database as part of this change** -- migration `0008` has not been applied
to the real database, which remains at migration `0007`. A bounded Macro
Analyst agent (`market_intelligence/agents/macro_analyst.py`,
`scripts/run_macro_analyst.py`) has also since been added (2026-08-24,
code/tests/docs only -- see Status below and
[docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)), built directly on
`MacroEvidenceBuilder` above, mirroring the Market Evidence Agent's/News
Analyst's single-turn, no-tools pattern. **This is explicitly a first,
bounded, factual macro-evidence analyst -- not a regime classifier,
predictor, directional market model, or trading agent.** Its deterministic
preflight gate is all-or-nothing across every requested series (default
`FEDFUNDS`): it makes zero OpenAI requests unless every requested series has
a stored observation, has stored official metadata, is not stale, is not
future-dated, does not have `latest_is_missing=True`, and has a stable
evidence ID. Every macro claim it can produce is scoped to a single stored
observation and its official metadata only (`content_basis` is a fixed
literal the agent sets itself, never sent to or received from the model);
`directional_assessment`/`trade_recommendation` are always the fixed
`"not_performed"` value, exactly as for the Market Evidence Agent and News
Analyst. A dedicated, deterministic post-response content-scope check
(distinct from the shared non-directional output policy the other two
agents also use) rejects any claim describing a trend, change, acceleration/
deceleration, surprise, historical extreme, correlation, causation, policy
change, or market regime, since a single snapshot observation with no
comparison value or consensus expectation can never support such a
statement. **This is infrastructure/agent code added in code, tests, and
docs only -- it has not been run against the real local database, and no
live OpenAI request has been made using it, as part of this change.** A
first authorized live run has since succeeded (2026-08-24, FEDFUNDS,
`gpt-5-mini`, stored value 3.63 percent, observation date 2026-07-01,
monthly), and a manual review of that one completed output found two
wording/scope weaknesses -- point-in-time phrasing that could mislead about
a stored monthly observation, and a listed transmission channel
(inflation) not actually explained by `conditional_mechanism` -- which
motivated a bounded hardening change (a deterministic, bounded
`recent_observations` excerpt plus one precisely supported
`latest_change_from_previous` comparison added to `MacroEvidenceBuilder`;
frequency-aware wording, a gated two-observation comparison, and
per-channel addressing validation added to `MacroAnalyst`). **One completed
run and one manual review is not a validated evaluation methodology.** See
item 21 below for the full sanitized record of both the live run and this
hardening change. **A second live `--execute` run made after that hardening
(FEDFUNDS, `recent_observations_limit=6`) then failed differently: the
deterministic preflight passed and exactly one OpenAI request was sent, but
the response was rejected by the post-response content-scope check at
`limitations[0]` -- no report was accepted and no retry was made.** Offline
analysis found that this check's fixed denylist could reject a
model-authored limitation merely for using words like "trend"/"regime"/
"correlation"/"causation" even when clearly negated (e.g. "insufficient to
establish a trend"), which are desirable, honest limitations rather than
prohibited claims; a narrow, fail-closed negation allowance for
`limitations` only (never `claim_summary`/`conditional_mechanism`) was then
added to address this false-positive class. See item 22 below for the full
sanitized record of both this second live failure and the fix. A narrow,
committed **Core Macro Basket** configuration and dry-run-first batch
ingestion script have also since been added (2026-08-24, code/tests/docs
only -- see item 23 below and
[docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md)): a fixed, reviewed
list of exactly seven approved FRED series
(`FEDFUNDS`, `DGS10`, `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1`), each
with a strictly validated category/lookback/limit contract, plus
`scripts/ingest_core_macro_basket.py`, which reuses the existing FRED
metadata/observations connectors, repositories, and the existing
orchestration run lock unmodified. **This is explicitly a first, bounded
basket, not a complete macro model and not proof of predictive
usefulness.** As of this change it has **not** been run live or against the
real local database -- it exists in code, tests, and docs only, and does not
modify `market_intelligence/orchestration/` (no new job type,
`jobs.json` change, migration, or schema change of any kind). **This
`DGS10`-inclusive list has since been superseded: a separately authorized
first live run of this basket succeeded for six series but failed for
`DGS10` observations only (sanitized category `provider_error`; `DGS10`
metadata succeeded), and `DGS10` was then replaced in the committed
configuration by the monthly FRED series `GS10`, a proposed bounded
replacement not yet requested live -- see item 24 below for the full
record. **This "not yet requested live" status for `GS10` has since been
superseded: a separately authorized live `--execute` run of
`scripts/ingest_core_macro_basket.py` targeting `GS10` (2026-08-25)
succeeded (`metadata_status=succeeded`, `observation_status=succeeded`, 13
observations received, 13 inserted, 0 existing/updated, 0 failed), and a
subsequent read-only verification confirmed the real local database
healthy at schema version `0008` (8 migrations applied) with 1 stored
`GS10` metadata row and 13 stored `GS10` observation rows covering
2025-07-01 through 2026-07-01 (0 missing) -- see item 25 below for the
full record.**

## Status

- Repository initialized. Directory scaffold and governing documents
  (README, CLAUDE.md, AGENTS.md, DATA_CATALOG.md, SOURCE_POLICY.md,
  DECISION_RULES.md) are in place.
- A local Python 3.13 virtual environment (`.venv`) has been created.
  `pyproject.toml` defines the runtime dependency set (duckdb, pandas,
  pyarrow, httpx, pydantic, pydantic-settings, python-dotenv) and a `dev`
  optional-dependency group (pytest, pytest-cov, ruff). The project is
  installed into `.venv` in editable mode. See
  [docs/ENVIRONMENT_SETUP.md](docs/ENVIRONMENT_SETUP.md).
- A settings layer (`market_intelligence/config/settings.py`) has been
  added using pydantic-settings. It reads an optional local `.env` file,
  defines optional Alpaca/FRED/OpenAI/Anthropic credential fields protected
  with `SecretStr`, and exposes a `provider_status()` method that reports
  only booleans, never secret values. Settings can be instantiated without
  any credentials present.
- A read-only Alpaca market-data connector
  (`market_intelligence/data_connectors/alpaca_market_data.py`,
  `AlpacaMarketDataClient`) has been added, covering only Alpaca's
  market-data API (`https://data.alpaca.markets`) — no order, account, or
  execution functionality. It validates that both Alpaca credentials are
  configured before requesting, uses explicit timeouts, and returns only
  sanitized results (never headers, keys, secrets, or the raw response). All
  symbols are normalized/validated (trimmed, uppercased, restricted to a
  conservative U.S. ticker character set) before any request is built, so
  invalid or malicious input never reaches the network; malformed or
  non-object JSON responses are also rejected with a sanitized error/status
  rather than surfaced raw. A companion script,
  `scripts/check_alpaca_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  symbol, timestamp). On 2026-08-20 one live, read-only SPY snapshot
  connection check was run using local `.env` credentials and succeeded
  (2xx, market timestamp returned). This confirms connectivity only; it is
  not the same as a validated data pipeline.
- A read-only FRED macroeconomic-data connector
  (`market_intelligence/data_connectors/fred_macro_data.py`,
  `FredMacroDataClient`) has been added, covering only FRED's official API
  (`https://api.stlouisfed.org`) — no methods beyond fetching published
  series observations. It validates that a FRED API key is configured
  before requesting, uses explicit timeouts, and returns only sanitized
  results (never the API key, request URL, query parameters, raw response,
  or observation value). All series IDs are normalized/validated (trimmed,
  uppercased, restricted to a conservative alphanumeric/underscore
  character set) before any request is built, so invalid or malicious
  input never reaches the network; malformed or non-object JSON responses,
  FRED-reported API error payloads, and missing/malformed observations are
  also rejected with a sanitized error/status rather than surfaced raw. A
  companion script, `scripts/check_fred_connection.py`, reports a
  sanitized connection status (configured, success, status category,
  series ID, latest observation date — never the observation value). On
  2026-08-20 one live, read-only FEDFUNDS latest-observation connection
  check was run using local `.env` credentials and succeeded (2xx,
  observation date returned). This confirms connectivity only; it is not
  the same as a validated data pipeline. A separate, later authorized
  historical-observations ingestion (2026-08-21, see the "Historical FRED
  observations" bullet below) has since stored 12 FEDFUNDS observations;
  that is one bounded ingestion run, not a validated dataset — see
  `DATA_CATALOG.md`.
- A read-only Alpaca news connector
  (`market_intelligence/data_connectors/alpaca_news.py`,
  `AlpacaNewsClient`) has been added, covering only Alpaca's read-only
  data host (`https://data.alpaca.markets`) and only its news endpoint
  (`/v1beta1/news`) — no order, account, or execution functionality, and
  it does not write to DuckDB. It validates that Alpaca credentials are
  configured before requesting, uses explicit timeouts, and returns only
  sanitized results (never headers, keys, secrets, or the complete raw
  response). Symbols, result limits, sort direction, and optional
  start/end timestamps are all strictly normalized/validated before any
  request is built, so invalid or malicious input never reaches the
  network; malformed or non-object JSON responses, and individual articles
  missing required fields, are rejected/skipped with a sanitized
  error/status rather than surfaced raw, and duplicate articles (by
  provider article ID) within a single response are deduplicated. The
  normalized news-item model contains only provider-reported metadata
  (provider article ID, headline, source, URL, summary when available,
  publication/update timestamps when available, related symbols, a UTC
  retrieval timestamp kept distinct from publication time, and provider
  name) — no sentiment, impact, or direction is inferred. A companion
  script, `scripts/check_alpaca_news.py`, reports a sanitized connection
  status (configured, success, status category, requested symbol, article
  count, newest publication timestamp) and never prints headlines, URLs,
  summaries, or raw payloads. On 2026-08-20 one live, read-only SPY-news
  connection check was run using local `.env` credentials and succeeded
  (2xx, 10 articles, newest publication timestamp returned). This confirms
  connectivity only; it is not the same as a validated data pipeline.
  Separately, one explicitly authorized SPY ingestion stored 10 normalized
  news articles, as described below. That verifies one successful ingestion
  run; it is not yet a complete or validated news dataset — see
  `DATA_CATALOG.md`.
- A read-only Alpaca historical stock-bars connector
  (`market_intelligence/data_connectors/alpaca_bars.py`, `AlpacaBarsClient`)
  has been added, covering only Alpaca's read-only data host
  (`https://data.alpaca.markets`) and only its single-symbol historical
  bars endpoint (`/v2/stocks/{symbol}/bars`) — no order, account, or
  execution functionality, and it does not write to DuckDB. It supports
  exactly one symbol per request and only the three project-approved
  timeframes (`1Min`, `5Min`, `1Day`; case/spacing variants are normalized
  to those exact values). `start`/`end` must be strict RFC3339 timestamps
  with an explicit UTC offset, are normalized to UTC, and `start` must be
  strictly before `end`. The per-page limit and page count are both
  strictly bounded, so a malformed or endless provider pagination sequence
  cannot loop indefinitely. The normalized `Bar` model contains only
  provider, symbol, timeframe, feed, bar timestamp (UTC, kept distinct from
  local retrieval time), open/high/low/close (as `Decimal`, to avoid
  binary-float rounding artifacts in values intended for reproducible
  analysis), volume, trade_count (nullable), vwap (nullable), and
  retrieved_at — no indicators, returns, labels, sentiment, predictions, or
  trade directions. Numeric fields reject booleans, non-numeric types, and
  non-finite values (NaN/infinity); candles failing basic OHLC consistency
  are rejected. If a non-empty provider bars list contains any malformed
  bar, the entire request fails with a sanitized error rather than
  returning a misleading partial series; exact duplicate bars (by symbol,
  timeframe, feed, timestamp) are deduplicated, while conflicting
  duplicates fail the request. Results are returned in chronological
  order. A companion script, `scripts/check_alpaca_bars.py`, and a
  `check_connection` method report only sanitized connection status
  (configured, success, status category, symbol, timeframe, feed,
  adjustment, currency, bar count, oldest/newest bar timestamp) — never
  OHLCV values, credentials, URLs, raw responses, or page tokens.

  **First authorized live check and feed hardening (2026-08-20):** the
  first authorized live connectivity check (single-symbol SPY, using the
  connector's then-default request, which sent no explicit `feed`
  parameter) reached Alpaca and returned a sanitized status of
  `configured=True, success=False, status_category=4xx` — only this
  sanitized status was recorded; the raw response body, headers, and
  credentials were never printed or stored. Per Alpaca's official
  documentation, the historical single-symbol bars endpoint defaults to
  the SIP feed when no `feed` parameter is sent, and SIP access requires a
  market-data subscription; the likely cause of the observed 4xx is that
  default SIP routing combined with this project's Alpaca subscription not
  covering SIP (Alpaca returns HTTP 403 in that case), not a credentials or
  code defect. In response, the connector was hardened: it now explicitly
  sends `feed=iex` (a fixed constant, `DATA_FEED`, never a caller-supplied
  argument) on every request, including every paginated page and
  `check_connection`, with no automatic fallback between feeds, and `feed`
  is now recorded on every normalized `Bar` and `BarsConnectionStatus` for
  explicit data provenance. **Known limitation:** IEX is a single
  exchange's feed, not the consolidated SIP tape, so it reflects narrower
  market coverage (fewer trades, potentially different prices/volume) than
  SIP. The 4xx above was observed under the prior, pre-hardening default
  (SIP) request; that failed check is preserved here as an honest
  diagnostic record and is not being retracted or overwritten.

  **Second authorized live check, on the hardened explicit-IEX connector
  (2026-08-20):** a separately authorized live connectivity check was run
  against the now-hardened, explicit-IEX connector (single-symbol SPY,
  `5Min` timeframe) and succeeded, returning a sanitized status of
  `configured=True, success=True, status_category=2xx, symbol=SPY,
  timeframe=5Min, feed=iex, bar_count=5, oldest_bar_timestamp=
  2026-08-17T12:25:00Z, newest_bar_timestamp=2026-08-17T13:30:00Z`. Only
  this sanitized status was recorded — no OHLCV values, credentials, URLs,
  raw response body, or page tokens were printed or stored. **This
  confirms only that the hardened, explicit-IEX connector can reach
  Alpaca, authenticate, and normalize a small live response — it verifies
  connectivity and response normalization only.** It does not confirm SIP
  connectivity (SIP remains unverified and is not requested by this
  connector), and it is not a stored, complete, or validated historical
  bars dataset: no bars from this check were written to DuckDB (this
  connector still does not store bars — bars storage is future, separately
  reviewed work), and no coverage, gap, or quality analysis has been
  performed. As of this 2026-08-20 check, no historical bars dataset had
  been retrieved, stored, or validated (see the first authorized live bars
  ingestion below, 2026-08-21, for the first stored batch).

  **Adjustment/currency provenance hardening (2026-08-20):** alongside
  `feed=iex`, every request — including every paginated page and
  `check_connection` — now also explicitly sends `adjustment=raw` (fixed
  constant `DATA_ADJUSTMENT`; split/dividend-unadjusted prices as
  originally reported, matching this project's "no corporate-action
  adjustment applied" policy) and `currency=USD` (fixed constant
  `DATA_CURRENCY`). Neither is ever accepted as a caller-supplied argument
  anywhere in the connector, and both are recorded on every normalized
  `Bar` and `BarsConnectionStatus`, including failed/unconfigured/
  invalid-input statuses. **The explicit-IEX bars connector remains live
  connectivity-verified** as described in the two authorized checks above;
  no new live check was run against this additional adjustment/currency
  hardening as part of this 2026-08-20 entry, so as of that entry it was
  verified only by unit tests (mocked HTTP transport). This hardening has
  since been exercised live by the first authorized bars ingestion
  (2026-08-21, see below), which used `feed=iex`, `adjustment=raw`, and
  `currency=USD` throughout — one controlled ingestion run, not complete
  dataset validation.
- A market-bar storage schema and repository exist. Migration `0005`
  (`market_intelligence/storage/migrations/0005_create_market_bars.sql`)
  defines a `market_bars` table, and
  `market_intelligence/storage/bar_repository.py` (`BarRepository`) accepts
  already-normalized `Bar` objects from `AlpacaBarsClient` and writes them
  transactionally, mirroring `NewsArticleRepository`'s pattern; a manual
  ingestion script, `scripts/ingest_alpaca_bars.py`, also now exists. The
  table stores only provider-reported OHLCV/vwap data plus provenance
  (`provider`, `symbol`, `timeframe`, `feed`, `adjustment`, `currency`,
  `bar_timestamp`, `open`/`high`/`low`/`close`/`vwap` as `DECIMAL(18,6)`,
  `volume`/`trade_count` as `BIGINT`, `retrieved_at`, `first_ingested_at`,
  `last_seen_at`, `ingestion_run_id`) — no indicator, return, label,
  sentiment, prediction, recommendation, option-contract, order, or
  execution field exists. Idempotency is enforced via a `(provider, symbol,
  timeframe, feed, adjustment, currency, bar_timestamp)` primary key; an
  already-known bar identity whose OHLCV/trade_count/vwap values still
  match has only its retrieval/last-seen/run provenance refreshed, while an
  already-known bar identity whose values conflict aborts the entire batch
  (nothing partially persists) and the corresponding `ingestion_runs` row
  is recorded `failed` with a sanitized error category. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  detail. This capability was originally built and tested against
  temporary databases only (mocked bars, no live Alpaca requests).

  **First authorized real-database migration and live bars ingestion
  (2026-08-21):** `data/market_intelligence.duckdb` was backed up, and a
  separately authorized initialization run then applied migration `0005`
  to the real local database (see the local DuckDB storage foundation
  bullet below for the resulting schema version and health check).
  `scripts/ingest_alpaca_bars.py` was then run once, live, against the
  real database with an explicit, bounded request: single-symbol SPY,
  `5Min` timeframe, `feed=iex`, `adjustment=raw`, `currency=USD`,
  requested interval 2026-08-15T00:00:00Z through 2026-08-20T00:00:00Z,
  `max_pages=1`, `limit=500`. The run received 248 bars, inserted 248, had
  0 existing/updated and 0 failed, and the corresponding `ingestion_runs`
  row was recorded `succeeded`; the latest `ingestion_runs` record for
  this dataset is `('alpaca', 'bars', 'succeeded', 248, None)`. A
  subsequent read-only query of `market_bars` verified 248 stored rows
  for this symbol/timeframe/feed, covering 2026-08-17T12:25:00Z through
  2026-08-19T20:00:00Z. **This confirms one controlled ingestion run,
  transactional storage, and successful local retrieval.** It does not
  establish a complete, gap-free, consolidated, or research-validated
  historical dataset; coverage is IEX only, which is narrower than SIP;
  and it carries no claim of predictive value, strategy validity,
  production readiness, or options-trading capability. Only sanitized
  counts, status, and the verified row count/coverage window are recorded
  here — no OHLCV values are reproduced in this document.
- A local DuckDB storage foundation has been initialized
  (`market_intelligence/storage/`, `DuckDBManager` in
  `market_intelligence/storage/database.py`). It provides a versioned,
  checksum-verified, transactional migration runner and the local
  database file — no forecasting/trading tables exist. The migration code
  now defines five tables: `schema_migrations` (tracks applied migrations
  and their checksums), `ingestion_runs` (records
  provider/dataset/timing/status/record-count/sanitized-error-category/
  code-version/schema-version metadata for ingestion runs), `news_articles`
  (migration `0004`), and `market_bars` (migration `0005`, now applied to
  the real database — see below). Migration `0003` added a separate
  `schema_version` column to `ingestion_runs`, distinct from
  `code_version`. The database file defaults to
  `data/market_intelligence.duckdb` (inside this repository's own `data/`
  directory, per `Settings.project_data_path`) and is excluded from
  version control via `.gitignore`. `scripts/initialize_database.py`
  applies pending migrations and prints only the database path, schema
  version, and applied migration count; `scripts/check_database.py`
  performs a read-only health check that also verifies required columns,
  that the applied migration history matches the migration directory (no
  missing files, no checksum mismatches, no gaps/out-of-order versions),
  and that the database is at the latest available migration —
  `healthy` is false if any of these fail. On 2026-08-20 the local
  database was first initialized (schema version `0002`, 2 migrations
  applied), was upgraded to schema version `0003` (1 additional migration
  applied, 3 total) after migration `0003` was added, and — after the
  authorized live news ingestion described below — was upgraded again to
  schema version `0004` (4 migrations applied). A read-only health check
  on 2026-08-20 reported the real local database healthy at `0004`
  (required tables/columns present, migration history and checksums
  valid, at the latest available migration). After migration `0005`
  (`market_bars`) was added to this repository's migration code, a
  read-only health check against the still-`0004` real database reported
  `schema_version=0004, applied_migration_count=4, healthy=False` (`False`
  only because the database was then behind the latest available
  migration — every other health check, including migration-history
  validity and checksums, still passed); this diagnostic record is
  preserved and not retracted.

  **Migration `0005` applied to the real database (2026-08-21):** as part
  of the first authorized bars ingestion described above,
  `data/market_intelligence.duckdb` was backed up, then
  `scripts/initialize_database.py` was run and applied migration `0005`
  to the real local database. A subsequent read-only health check
  reported: `schema_version=0005`, `applied_migration_count=5`,
  `required_tables_present=True`, `required_columns_present=True`,
  `migration_history_valid=True`, `checksums_valid=True`,
  `is_current=True` (database at latest migration), `healthy=True`. **The
  real database is now at migration `0005` and reports healthy.** See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md).

  **Migration `0006` added to repository code only (2026-08-21, not
  applied):** after migration `0006` (`macro_observations`, see the
  "Historical FRED observations" bullet below) was added to this
  repository's migration code, a read-only health check against the
  still-`0005` real database reports `schema_version=0005,
  applied_migration_count=5, healthy=False` (`False` only because the
  database is now behind the latest available migration — every other
  health check, including migration-history validity and checksums, still
  passes, and every previously stored row — 248 SPY bars, 10 SPY news
  articles — remains intact and untouched). This diagnostic record is
  preserved and not retracted, mirroring how the analogous `0004`-behind-
  `0005` entry was handled above.

  **Migration `0006` applied to the real database (2026-08-21):** as part
  of the first authorized FRED historical-observations ingestion described
  below, `data/market_intelligence.duckdb` was backed up, then
  `scripts/initialize_database.py` was run and applied migration `0006`
  to the real local database. A subsequent read-only health check
  reported: `schema_version=0006`, `applied_migration_count=6`,
  `required_tables_present=True`, `required_columns_present=True`,
  `migration_history_valid=True`, `checksums_valid=True`,
  `is_current=True` (database at latest migration), `healthy=True`. **The
  real database is now at migration `0006` and reports healthy.** The
  previously stored 248 SPY bars and 10 SPY news articles remain intact
  and untouched. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md).
- A first persistent news-storage table, `news_articles`, has been added
  via migration `0004`
  (`market_intelligence/storage/migrations/0004_create_news_articles.sql`),
  along with a storage service
  (`market_intelligence/storage/news_repository.py`,
  `NewsArticleRepository`) that accepts already-normalized `NewsItem`
  objects from `AlpacaNewsClient` and writes them transactionally, and a
  manual ingestion script (`scripts/ingest_alpaca_news.py`). The table
  stores only provider-reported article metadata plus provenance
  (`provider`, `provider_article_id`, `headline`, `source`, `article_url`,
  `summary`, `created_at`/`updated_at` as reported by the provider,
  `related_symbols` stored sorted/deduplicated for determinism,
  `retrieved_at`, `first_ingested_at`, `last_seen_at`,
  `ingestion_run_id`) — no sentiment, impact, direction, confidence, model
  output, recommendation, or option-contract field exists. Idempotency is
  enforced via a `(provider, provider_article_id)` primary key; an
  already-known article with matching stable content (headline, source,
  URL, publication time) has its mutable fields and provenance refreshed,
  while an already-known article whose stable content conflicts aborts the
  entire batch (nothing partially persists) and the corresponding
  `ingestion_runs` row is recorded `failed` with a sanitized error
  category. See [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md)
  for full detail. On 2026-08-20, one explicitly authorized live SPY news
  ingestion was run via `scripts/ingest_alpaca_news.py` against the real
  local database and succeeded: 10 articles received, 10 inserted, 0
  updated, 0 failed, and the corresponding `ingestion_runs` row recorded
  status `succeeded`. This run upgraded the real local database file to
  schema version `0004`. Only sanitized counts and status are recorded
  here — no article headline, URL, or summary content is reproduced in
  this document. This confirms the storage pipeline succeeded for one
  ingestion run; it is not the same as a validated, cataloged news
  dataset — see `DATA_CATALOG.md` for the dataset-level record and
  required-fields status.
- The `market_bars` table exists (migration `0005`, applied to the real
  database — see above) and now holds one authorized ingestion's worth of
  SPY bars (see above). A macro-observation table, connector method, and
  repository also exist (migration `0006`,
  `market_intelligence/storage/migrations/0006_create_macro_observations.sql`,
  `MacroObservationRepository`, `scripts/ingest_fred_observations.py`) — see
  the "Historical FRED observations" bullet below. **Migration `0006` has
  since been applied to the real local database (2026-08-21, see above),
  and a first authorized live FRED observations ingestion has succeeded**
  (see below). This is one bounded ingestion run, not a complete or
  research-validated macro dataset.
- **Historical FRED observations (2026-08-21, added in code/tests, then
  exercised live — see below).** `FredMacroDataClient` (unchanged single-latest-observation
  connectivity check preserved) now also exposes `get_observations()`: a
  strictly validated, bounded, paginated fetch of historical observations
  for one series over an explicit `observation_start`/`observation_end`
  calendar-date range. `series_id`, `observation_start`, and
  `observation_end` are validated before any request is built (calendar-date
  shape, real calendar date, `observation_start <= observation_end`); the
  per-page limit and page count are both strictly bounded (no caller can
  raise the fixed ceiling), sorted ascending. Every page also explicitly
  sends fixed, non-overridable `realtime_start=1776-07-04`,
  `realtime_end=9999-12-31`, `output_type=1`, and `units=lin`: FRED
  documents that an omitted realtime_start/realtime_end defaults both to
  *today's date* rather than the observation's actual reported revision
  window, so requesting the complete real-time period explicitly is what
  makes `realtime_start`/`realtime_end` describe FRED's real
  revision/vintage window and keeps the storage identity (below) stable and
  meaningful across ingestion runs on different retrieval days, rather than
  merely reflecting the retrieval date. Pagination metadata is hardened:
  every page's `offset` must be a plain nonnegative integer exactly equal
  to the offset requested, every page's `count` must be a plain nonnegative
  integer identical across all pages, and a short/empty page is only
  accepted as complete once `offset + returned` has actually reached
  `count` — any offset/count inconsistency or mismatch, or a short/empty
  page while records remain outstanding, fails the whole request with a
  sanitized error instead of silently returning an incomplete series, and
  exceeding the hard page-count ceiling likewise fails safely. FRED's `"."`
  missing-observation marker is preserved as `value=None, is_missing=True`;
  every other value is parsed as a finite `Decimal` from FRED's own string
  representation (non-finite/malformed values are rejected). Any malformed
  observation in a non-empty response fails the whole fetch rather than
  returning a misleading partial series; exact duplicate observations
  (matched on series_id, observation_date, and the full realtime_start/
  realtime_end vintage) are deduplicated, conflicting duplicates fail the
  fetch, and results are returned in deterministic chronological order.
  Errors and statuses never include the API key, request URL/query
  parameters, raw responses, or observation values. A macro-observations
  schema (migration `0006`, `macro_observations` table), a hardened,
  transactional `MacroObservationRepository` (mirroring `BarRepository`'s
  validate-before-write, single-transaction, conflict-rollback pattern, with
  an identity of `(provider, series_id, observation_date, realtime_start,
  realtime_end)` so FRED revisions/vintages are preserved rather than
  collapsed), and a one-shot manual ingestion script
  (`scripts/ingest_fred_observations.py`) now all exist, covered by tests
  using temporary DuckDB files and mocked HTTP transports only. The
  repository also now strictly enforces `provider == "fred"` (the fixed
  `DEFAULT_PROVIDER` constant): any alternate, blank, malformed, or
  non-string `provider` argument is rejected before any connection is
  opened, any `ingestion_runs` row is written, or any observation is
  written, and the rejected value is never echoed. As of 2026-08-21 (prior
  to the live run below), none of this had been run live: migration `0006`
  had not been applied to the real database (which remained at `0005`,
  healthy for everything already applied, but no longer at the latest
  available migration — see above), no FRED observation had been fetched
  from the live API using this new method, and no observation had been
  stored. FRED connectivity itself was previously verified only via the
  pre-existing single-latest-observation check (2026-08-20, see below).

  **First authorized live FRED historical-observations ingestion
  (2026-08-21):** `data/market_intelligence.duckdb` was backed up, migration
  `0006` was applied (see above), and `scripts/ingest_fred_observations.py`
  was then run once, live, against the real database with an explicit,
  bounded request: series `FEDFUNDS`, requested observation range
  2025-08-01 through 2026-07-31, fixed request provenance
  (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`, `output_type=1`,
  `units=lin`), `limit=1000`, `max_pages=1`. The run received 12
  observations, inserted 12, had 0 existing/updated and 0 failed, and the
  corresponding `ingestion_runs` row was recorded `succeeded`; the latest
  `ingestion_runs` record for this dataset is `('fred', 'macro_observations',
  'succeeded', 12, None)`. A subsequent read-only query of
  `macro_observations` verified 12 stored rows for this series, covering
  2025-08-01 through 2026-07-01, with 0 missing observations. **This
  confirms one bounded historical fetch, response normalization,
  transactional storage, and local retrieval.** It does not establish a
  complete, gap-free, broadly cataloged, or research-validated macro
  dataset. Requesting the complete real-time period
  (`realtime_start=1776-07-04`, `realtime_end=9999-12-31`) means this run's
  vintage window is FRED's actual revision window, not merely today's
  retrieval date — it does not mean all of FEDFUNDS's revision history has
  been retrieved for every observation date outside the requested range.
  `DECIMAL(20,6)` remains a deliberately bounded supported range for this
  project's currently-ingested series, not a claim of universal support
  for every FRED series. Only sanitized counts, status, and the verified
  row count/coverage window are recorded here — no observation values are
  reproduced in this document. The previously stored 248 SPY bars and 10
  SPY news articles remain intact and untouched. See
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) and
  `DATA_CATALOG.md`.
- No trading execution connected. No brokerage integration exists or is
  planned; Robinhood is used manually, outside this system.
- No validated predictive model. No forecasting, scoring, or evaluation
  logic has been built or tested.
- **This is an independent project.** It does not depend on, read from, or
  otherwise access the separate ORB_Project. Read-only Alpaca provider
  connectivity has been verified; one authorized ingestion run stored 10
  normalized SPY news articles, and a separate authorized ingestion run
  stored 248 normalized SPY bars (IEX, `5Min`, see above). A third
  authorized ingestion run stored 12 normalized FEDFUNDS macro observations
  (see above). No complete, gap-free, or validated provider dataset has
  been cataloged yet. The settings layer's only
  data-path configuration is `project_data_path`, which defaults to this
  repository's own `data/` directory. No code in this repository may
  access files outside the repository unless the user explicitly
  authorizes a specific source.
- **Ingestion-orchestration layer added (2026-08-23, code/tests only, not
  run live at the time it was added).** A new package, `market_intelligence/orchestration/`, adds
  immutable, strictly validated job contracts for exactly the three
  existing reviewed job types (`alpaca_news`, `alpaca_bars`,
  `fred_observations`), a small committed JSON configuration file
  (`market_intelligence/orchestration/jobs.json`) defining the three
  initial reviewed jobs (SPY news, SPY 5-minute IEX/raw/USD bars, FEDFUNDS
  observations), a dry-run-first CLI entry point
  (`scripts/run_ingestion_pipeline.py`), narrow job adapters that call the
  existing reviewed connectors/repositories directly (never a shell
  command, subprocess, or the manual `scripts/ingest_*.py` scripts), a
  conservative fail-closed local run lock, and a persistent orchestration
  audit trail (migration `0007`, `orchestration_runs`/
  `orchestration_job_runs`). See
  [docs/INGESTION_ORCHESTRATION.md](docs/INGESTION_ORCHESTRATION.md) for
  full detail. As initially added, this orchestration layer, including
  migration `0007`, existed in code and tests only — it had not been run
  with `--execute` against the real database, and migration `0007` had not
  been applied to the real database, which remained at migration `0006`. A
  read-only health check at that time reported the real database
  `schema_version=0006, applied_migration_count=6, healthy=False`
  (`False` only because the database was behind the latest available
  migration in the repository's code — every other health check,
  including migration-history validity and checksums, still passed),
  mirroring the same honest-diagnostic pattern already used for the
  `0004`→`0005` and `0005`→`0006` transitions above; this diagnostic
  record is preserved and not retracted. The previously stored 10 SPY
  news articles, 248 SPY bars, and 12 FEDFUNDS observations remained
  intact and unchanged — verified via read-only queries as part of that
  change. No AI agent, analysis, prediction, recommendation, scheduling,
  or brokerage/Robinhood integration was built as part of that change.
  **This code/tests-only state has since been superseded by a first
  authorized live orchestration run — see the entry immediately below.**

- **First authorized live orchestration run (2026-08-23).**
  `data/market_intelligence.duckdb` was backed up, migration `0007`
  (`orchestration_runs`/`orchestration_job_runs`) was applied to the real
  local database, and `scripts/run_ingestion_pipeline.py` was then run
  once, live, with `--all --execute` against the real database, selecting
  all three existing reviewed jobs (`alpaca_news_spy`,
  `alpaca_bars_spy_5min`, `fred_fedfunds_observations`) in one
  orchestrated run.

  A subsequent read-only health check reported: `schema_version=0007`,
  `applied_migration_count=7`, `required_tables_present=True`,
  `required_columns_present=True`, `migration_history_valid=True`,
  `checksums_valid=True`, `is_current=True`, `healthy=True`. **The real
  database is now at migration `0007` and reports healthy.**

  The orchestration run
  (`orchestration_run_id=e63d931e-8957-4357-93ee-ba7076b079d8`) completed
  with overall status `succeeded`. Per-job sanitized results, each
  recorded `succeeded` in `orchestration_job_runs`:

  - `alpaca_news_spy` (Alpaca news, SPY): 10 received, 10 inserted, 0
    existing/updated, 0 failed.
  - `alpaca_bars_spy_5min` (Alpaca bars, SPY, `5Min`, `feed=iex`,
    `adjustment=raw`, `currency=USD`): 334 received, 169 inserted, 165
    existing/updated, 0 failed.
  - `fred_fedfunds_observations` (FRED, FEDFUNDS): 3 received, 0
    inserted, 3 existing/updated, 0 failed.

  A subsequent read-only query confirmed `orchestration_runs` contains
  exactly 1 run and all three `orchestration_job_runs` rows for it are
  recorded `succeeded`. Only sanitized counts and status are recorded
  here — no headline, URL, summary, OHLCV, or observation value from this
  run is reproduced in this document.

  **This confirms one controlled, explicitly authorized orchestration run
  across all three existing reviewed jobs, transactional per-job storage,
  and a persistent orchestration audit trail.** It does not establish
  scheduling, continuous or unattended operation, dataset completeness or
  gap-freedom for any of the three underlying datasets, prediction, agent
  intelligence, options analysis, or trading execution — none of that
  exists or was exercised by this run. The `alpaca_bars_spy_5min` job's
  165 existing/updated bars and the `fred_fedfunds_observations` job's 3
  existing/updated, 0 inserted result reflect idempotent overlap with
  previously stored bars/observations within each job's own bounded
  request window — not a claim of complete or gap-free coverage for
  either dataset. See
  [docs/INGESTION_ORCHESTRATION.md](docs/INGESTION_ORCHESTRATION.md) and
  [docs/STORAGE_ARCHITECTURE.md](docs/STORAGE_ARCHITECTURE.md) for full
  detail.

- **Read-only market-context snapshot layer added (2026-08-23, code/tests
  only; not run against the real database as part of this change).** A new
  package, `market_intelligence/market_features/`, adds
  `MarketContextBuilder` — a strictly validated, deterministic, read-only
  builder that assembles exactly one JSON-ready snapshot dict from data
  already stored in the local DuckDB database (latest stored bar plus a
  bounded recent-bar summary, a short-period price return computed only
  when enough stored bars exist, bounded recent news metadata, the latest
  stored macro observation per a bounded set of configured series, source
  provenance, and explicit missing/stale-data flags) — and
  `scripts/build_market_context.py`, a one-shot CLI that prints one
  sanitized snapshot to stdout. It makes no network request of any kind,
  opens the database only via `duckdb.connect(path, read_only=True)`,
  never writes a row or applies a migration, and does not modify any
  connector, ingestion repository, orchestration code, or
  `orchestration/jobs.json`. Symbol, the two result-count limits, and the
  requested macro series IDs are all strictly validated (rejecting
  booleans, zero, negatives, excessive limits, and malformed input) before
  any DuckDB connection is opened. No snapshot is persisted anywhere. See
  [docs/MARKET_CONTEXT_SNAPSHOT.md](docs/MARKET_CONTEXT_SNAPSHOT.md) for
  the full field contract, the fixed staleness thresholds used, and known
  limitations. This adds no sentiment, prediction, trading bias,
  confidence score, options recommendation, or other agent conclusion —
  only already-stored provider data plus this module's own
  provenance/coverage/staleness bookkeeping about it.

- **OpenAI structured-output provider boundary added (2026-08-23, code/tests
  only; no live OpenAI request or connectivity check made as part of this
  change).** A new package,
  `market_intelligence/model_clients/`, adds `OpenAIStructuredClient`
  (`market_intelligence/model_clients/openai_structured.py`) — a minimal,
  defensive wrapper around the official OpenAI Python SDK's Responses API
  (`client.responses.parse`) using native Pydantic Structured Outputs. The
  official `openai` package was added as a runtime dependency
  (`pyproject.toml`, `openai>=1.99.0` as a compatible lower bound; installed
  version at the time of writing is `3.3.1`, which depends on `httpx2`, the
  official SDK's current HTTP-layer dependency per PyPI's published package
  metadata for `openai`).

  `OpenAIStructuredClient.generate()` accepts exactly three inputs — fixed
  developer instructions (a trusted string authored by calling code, never
  derived from untrusted data), one bounded JSON-ready evidence dict, and
  one explicitly supplied Pydantic output model — and makes one request
  with a fixed, non-caller-overridable shape: the single model configured
  via the new `Settings.openai_model` (default `"gpt-5-mini"`), `store=False`,
  no tools (no function calling, web search, file search, or code
  execution), no conversation persistence, and no caller-supplied
  `base_url`/organization/project/headers. The API key comes only from the
  existing `Settings.openai_api_key` (`SecretStr`). All limits and evidence
  size/shape are validated before the OpenAI SDK client is constructed or
  any request is made; evidence is always serialized deterministically and
  wrapped with a fixed, module-owned label and safety appendix instructing
  the model not to treat it as overriding the developer instructions,
  including when it contains news headlines or other third-party text --
  a defense-in-depth mitigation, not a guaranteed prevention of prompt
  injection. The normalized
  `StructuredOutputResult` never raises for a model refusal or an
  incomplete response (both are reported via a `status` category, never
  with refusal text); a fixed, sanitized `OpenAIStructuredError` subclass is
  raised for missing configuration, timeout, connection failure, rate
  limit, authentication failure, parse failure, and any other unexpected
  SDK failure or unrecognized response shape — the entire SDK call and
  response-normalization step is wrapped in one sanitized exception
  boundary, so any exception type not already mapped to a specific
  category (including a malformed response with missing/non-iterable
  output, an unexpected status shape, or a parsed object of the wrong
  type) becomes a fixed `OpenAIUnexpectedError` with no raw
  type/message/body/path/header/evidence attached. Provider-reported
  metadata on the result is also sanitized rather than passed through
  as-is: `response_id` is returned only if it matches OpenAI's bounded
  `resp_...` ID shape (otherwise `None`), token counts are accepted only
  as plain nonnegative integers (otherwise `None`), and
  `incomplete_reason` is mapped only from OpenAI's known fixed categories
  (otherwise `"other"`/`None`) — no raised error or result field ever
  includes the API key, request body, evidence, headline, raw model
  output, raw SDK exception message, URL, or header. Two new non-secret
  `Settings`
  fields (`openai_request_timeout_seconds`, default 30s at the time this was
  added, bounded to `(0, 120]`; `openai_max_output_tokens`, default 2048 at
  the time this was added, bounded to `[1, 16000]` — **both defaults were
  later raised to 120s/4096 tokens, see the "Settings defaults corrected to
  match the News Analyst's live run sequence" Status entry below; the bounds
  themselves are unchanged**) plus `openai_model` are documented in `.env.example`
  (`.env` itself was not touched). The SDK client is injectable
  (`sdk_client=`) so tests never construct a real `openai.OpenAI` client or
  make a network call. See
  [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md) for
  the full contract. This adds no agent prompt, market bias, prediction,
  recommendation, brokerage integration, persistence, migration, dashboard,
  scheduler, Anthropic client, retry framework, or general agent
  framework — it is a single, narrow provider boundary intended as
  groundwork for a future, separately reviewed agent.

  **This "code and mocked tests only, no live OpenAI request" status
  reflects the state as of 2026-08-23. It has since been superseded — see
  the "First authorized live OpenAI connectivity check" entry immediately
  below** — but the underlying claims that remain true (no agent, prompt,
  market bias, forecast, recommendation, brokerage integration, or general
  agent framework exists on top of this boundary) are not retracted by that
  check.

- **First authorized live OpenAI connectivity check (2026-08-24).** A
  separately authorized, minimal live connectivity check was run against
  the real OpenAI API using the existing `OpenAIStructuredClient` and real
  local `.env` credentials, via a minimal fixed instructions string and an
  evidence dict containing only `{"test_type": "provider_connectivity",
  "contains_market_data": false}`. No market data, news, credentials,
  prompts from providers, predictions, recommendations, or agent analysis
  were sent in the request or produced in the response, and no code, test,
  configuration, `.env`, or migration was changed to run it.

  Sanitized results: `configured=True`, connection outcome
  `status="completed"`, `model="gpt-5-mini"`, parsed structured output
  present (`True`), a sanitized `response_id` matching OpenAI's bounded
  `resp_...` ID shape was returned (present, but the value itself is not
  reproduced in this document, consistent with this client's sanitization
  contract), `input_tokens=143`, `output_tokens=63`, `total_tokens=206`.

  **This confirms only that the existing OpenAI provider boundary can reach
  the OpenAI API, authenticate with the configured API key, and receive and
  parse one minimal structured-output response end to end.** It does not
  confirm model output quality, latency under load, rate-limit behavior,
  cost at scale, or any agent, forecast, recommendation, or market-analysis
  capability — none of that was exercised by this check, and none of it
  exists in this repository. See
  [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md) for
  full detail.

- **Market Evidence Agent added (2026-08-24, code/tests/docs only; no live
  database access or live OpenAI request made as part of this change).** A
  new package, `market_intelligence/agents/`, adds `MarketEvidenceAgent`
  (`market_intelligence/agents/market_evidence_agent.py`) and a
  dry-run-first CLI, `scripts/run_market_evidence_agent.py`. This is a
  **single-turn, no-tools analysis component** — not an autonomous or
  multi-agent system — built entirely on three existing, already-reviewed
  pieces of infrastructure: `MarketContextBuilder`, `SessionQualityBuilder`,
  and `OpenAIStructuredClient`. Given a symbol and an optional session date,
  it builds a bounded, deterministic evidence package from the two builders
  (every fact assigned a stable, code-generated evidence ID), evaluates a
  fixed deterministic preflight gate, and — only if that gate passes — makes
  **exactly one** structured-output request asking the model to summarize
  and organize the evidence.

  The preflight gate requires the requested symbol to match both builders'
  reported symbol, and requires `bars_missing=False`, `bars_stale=False`,
  `completeness.complete=True`, `partial_session=False`,
  `missing_data=False`, and an empty
  `unexpected_or_duplicate_timestamps_utc`; if any of these fail, the agent
  returns a deterministic `status="abstained"` report with fixed reason
  categories and makes **zero OpenAI requests** (zero model tokens spent).
  Missing/stale news or macro data does not block execution — it is instead
  surfaced as a deterministic limitation on the final report. Two strict
  Pydantic models (`extra="forbid"`, every field bounded) govern the
  contract: `MarketEvidenceModelAnalysis` (the only schema sent to OpenAI —
  `evidence_quality`, `evidence_summary`, 1–6 `observations` each citing 1–5
  evidence IDs, 0–6 `limitations`) and `MarketEvidenceReport` (the final
  report, adding `status`, `symbol`, `session_date_et`, and two **fixed**
  literal fields, `directional_assessment` and `trade_recommendation`, both
  always `"not_performed"` — the model-facing schema does not even include
  these fields, so the model cannot set them; that restriction is absolute).
  The model's free-text fields (`evidence_summary`, every observation
  `statement`, every model-supplied `limitation`) are additionally screened
  by a deterministic, fail-closed post-response content policy check
  (`MarketEvidencePolicyError`, added 2026-08-24) that rejects known
  directional-prediction, bullish/bearish-bias, trade-recommendation/action,
  and options-related (strikes/contracts/premiums) language before
  `MarketEvidenceReport` is constructed — a conservative, bounded filter and
  defense-in-depth on top of the model's developer instructions, not proof
  that every possible semantic violation is detectable. The rejected text is
  never echoed in the raised error.

  Every evidence ID the model cites in its response is validated after the
  fact against the exact evidence package sent for that request; a missing,
  fabricated, duplicated, or excessive citation raises a sanitized
  `MarketEvidenceCitationError` rather than being accepted. A model refusal
  or an incomplete response is never silently converted into a completed
  analysis — both raise a distinct, sanitized error
  (`MarketEvidenceRefusalError`/`MarketEvidenceIncompleteError`) instead of
  a report claiming `status="completed"`. The CLI's default mode is a dry
  run: it builds the local evidence package and evaluates preflight only,
  making zero OpenAI requests, and prints only eligibility, the fixed reason
  categories, symbol, session date, an evidence-item count, and data flags —
  never the evidence contents themselves. `--execute` is required for a
  paid model request (and only makes one if preflight passes); execute
  output prints the validated structured report plus sanitized model/token
  metadata (model name and token counts only) — never the raw provider
  response, a response ID, the full evidence payload, credentials, or a
  database path.

  Covered by 47 tests using an injected fake `OpenAIStructuredClient`
  (mirroring `test_openai_structured.py`'s injection pattern — no real SDK
  client is ever constructed and no network call is ever made) and fake
  `MarketContextBuilder`/`SessionQualityBuilder` stand-ins returning fixed,
  hand-authored dicts matching each builder's documented contract shape (so
  these tests never open a real database either), plus a small local eval
  fixture set (typical/missing-data/stale-data/partial-session/
  adversarial-headline scenarios — ordinary local pytest tests, **not** the
  OpenAI Evals API). **Passing this test suite demonstrates the
  deterministic scaffolding around the model call is correct — it does not
  validate the model's analytical accuracy, and no claim of validated
  analytical accuracy is made.** As of this entry, `MarketEvidenceAgent` has
  not been run against the real local database, and no live OpenAI request
  has been made using it — both remain separate, future, and not yet
  authorized. It is not integrated into
  `market_intelligence/orchestration/`, adds no persistence or migration,
  and adds no dashboard, alerting, or brokerage/Robinhood integration. See
  [docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md) for full
  detail.

- **First authorized live Market Evidence Agent run: dry run succeeded, one
  authorized execute attempt failed structured-output validation
  (2026-08-24).** A dry run of `MarketEvidenceAgent` was run against the real
  local database and reported `eligible=true`, `evidence_item_count=20`, with
  every blocking deterministic preflight quality flag passing (`bars_missing`,
  `bars_stale`, session completeness/partial-session/missing-data, and
  unexpected-or-duplicate-timestamps all clear) — zero OpenAI requests were
  made for this dry run, as designed. One separately authorized `--execute`
  attempt was then made: the deterministic preflight passed and **exactly
  one** live OpenAI request was sent (tokens were spent; no exact count was
  recorded — see the diagnostic finding below on why this client cannot
  capture token/response metadata for this particular failure). That request
  did not produce an accepted analysis: it failed with a sanitized
  `{"error": "agent_error", "detail": "OpenAI response failed
  structured-output validation."}` — an `OpenAIParseFailureError` raised
  inside `OpenAIStructuredClient.generate()` and propagated unchanged through
  `MarketEvidenceAgent.run()`. **No analysis was accepted, and per this
  task's explicit instruction, no retry or second live request was made** —
  the failure was diagnosed entirely offline, from the sanitized error
  category alone, using no raw model output (this client never captures or
  logs it).

  **Offline diagnosis (2026-08-24, no live request made to investigate).**
  Reading `OpenAIStructuredClient.generate()`
  (`market_intelligence/model_clients/openai_structured.py`) and the
  installed OpenAI SDK's own source (`openai==3.3.1`,
  `openai/lib/_parsing/_responses.py`, `openai/lib/_pydantic.py`) confirms
  that `pydantic.ValidationError` inside `generate()` can *only* originate
  from the SDK's own client-side re-validation of the model's response text
  against `output_model` (`parse_text()` → `model_validate_json()`) — never
  from strict-schema *construction*, which instead raises `TypeError`/
  `ValueError`/`pydantic.PydanticInvalidForJsonSchema` (already mapped to a
  different, generic code path prior to this change). This structurally
  proves the live failure occurred *after* a request was sent and a response
  was received — this was a response-content validation failure, not a
  request-construction failure.

  Locally regenerating the real, production strict JSON schema for
  `MarketEvidenceModelAnalysis` via the installed SDK's own builder
  (`openai.lib._pydantic.to_strict_json_schema`) confirms the schema itself
  is structurally valid and buildable under the installed SDK — correct
  `additionalProperties: false` throughout, every property required, enums,
  nested arrays, and `$defs`/`$ref` all resolve correctly. **The schema is
  not incompatible with OpenAI's strict Structured Outputs subset, so no
  schema restructuring was needed or made.** That same inspection also shows
  the schema sent to OpenAI includes `minLength`/`maxLength`/`minItems`/
  `maxItems` bound keywords (on `evidence_summary`, every observation
  `statement`, every `limitation`, and every `evidence_ids` list) — keywords
  the SDK's schema builder accepts and forwards, and which only this
  client's own Pydantic re-validation of the response is confirmed to
  enforce. **Because this client never captures or logs raw model output (by
  design — see `docs/OPENAI_PROVIDER_BOUNDARY.md`), the exact field/value
  that violated a bound in this one live attempt is unavailable and cannot
  be proven from local evidence alone, and that limitation is stated here
  honestly rather than guessed at.** Exceeding one of these Pydantic-only
  bounds is one plausible, locally reproducible failure mode for a response
  that was otherwise type/enum/shape-conformant — it is **not** established
  as the proven cause of this specific live attempt, and OpenAI has not
  published official documentation establishing that its Structured Outputs
  generation leaves these bound keywords unenforced specifically for the
  non-fine-tuned `gpt-5-mini` model this project uses. No strict structured
  output, citation validation, output-policy validation, or field bound was
  removed or weakened to work around this — the instructions given for this
  diagnosis explicitly required preserving all of them, and the
  schema-buildability check above showed no structural incompatibility
  existed to correct.

  **Fix applied (2026-08-24, code/tests only — no live request made): sanitized
  failure classification hardened, regression tests added.** Every
  `OpenAIStructuredError` (`market_intelligence/model_clients/openai_structured.py`)
  and `MarketEvidenceAgentError`
  (`market_intelligence/agents/market_evidence_agent.py`) subclass now
  exposes a fixed, sanitized `category` string attribute (e.g.
  `request_schema_invalid`, `response_validation_failed`, `refusal`,
  `incomplete`, `citation_invalid`, `policy_violation`) so a failure's class
  can be identified programmatically without parsing message text. A new
  `OpenAIRequestSchemaError` (category `request_schema_invalid`) is now
  raised — before any SDK client is built or network call is made — whenever
  `output_model` itself cannot be converted into a valid strict JSON schema
  by the installed SDK; this is proven offline with a synthetic
  schema-incompatible Pydantic model (zero SDK calls recorded). **This
  initial implementation of the `OpenAIRequestSchemaError` pre-check used the
  installed OpenAI SDK's own private `openai.lib._pydantic.to_strict_json_schema`
  helper — this has since been superseded the same day, see the follow-up
  entry immediately below, to remove that private-SDK production
  dependency.** This is distinct from and never confused with the existing
  `OpenAIParseFailureError`/`response_validation_failed` category, which now
  unambiguously means a request was sent and a response was received but its
  content failed validation. `scripts/run_market_evidence_agent.py` now also
  prints this sanitized `category` alongside its existing sanitized `detail`
  message for any `agent_error` result.

  New, focused offline regression tests were added to
  `market_intelligence/tests/test_openai_structured.py` using the real,
  production `MarketEvidenceModelAnalysis` schema (not only that file's
  pre-existing generic toy model): one proves the schema passes production's
  own schema preflight (`_validate_output_model`, public Pydantic API only);
  one proves a synthetic, schema-and-bound-conformant response round-trips
  through `generate()` unchanged; and one proves a synthetic response
  violating one of the schema's Pydantic-only length bounds reproduces the
  same sanitized error class, category, and message text this client raises
  for `response_validation_failed` in general — a plausible, locally
  reproducible failure signature consistent with the live failure, not proof
  of that attempt's exact cause — entirely offline, no network, no
  credentials. `market_intelligence/tests/test_run_market_evidence_agent.py`
  gained matching CLI-level regression tests confirming the new `category`
  field. No live OpenAI request, real-database access, dependency addition,
  or orchestration integration was made as part of this diagnostic/hardening
  change; the full test suite and `ruff check` were run and pass.

- **Follow-up hardening (2026-08-24, same day, code/tests only — no live
  request made): recurrence-reduction budgets added, private SDK dependency
  removed.** Two remaining gaps in the fix above were addressed:

  1. **Recurrence reduction.** The prior fix diagnosed and classified the
     2026-08-24 live failure but did not reduce its likelihood. `AGENT_INSTRUCTIONS`
     (`market_intelligence/agents/market_evidence_agent.py`) now includes
     explicit, conservative advisory output budgets — `evidence_summary` at
     most 600 characters, each observation `statement` at most 300
     characters, each `limitation` at most 200 characters, and 1-4
     observations preferred (only more if genuinely necessary), using
     concise, factual wording only. Each budget carries deliberate margin
     below its corresponding hard Pydantic maximum (800 / 400 / 300 / 6
     respectively — `MAX_SUMMARY_LENGTH`/`MAX_STATEMENT_LENGTH`/
     `MAX_LIMITATION_LENGTH`/`MAX_OBSERVATIONS`, all unchanged). **This is
     instruction-level guidance only: no bound was changed, and the agent
     still performs zero truncation, silent modification, retry, or
     acceptance of invalid output** — a response that ignores this guidance
     and still violates a hard bound still fails schema validation exactly
     as before. Seven new focused tests in
     `market_intelligence/tests/test_market_evidence_agent.py` prove each
     advisory budget is present in `AGENT_INSTRUCTIONS` and is numerically
     strictly below its corresponding enforced schema maximum, and that the
     schema maxima themselves are unchanged (800/400/6).

  2. **Private OpenAI SDK dependency removed from production code.** The
     `OpenAIRequestSchemaError` pre-check in
     `market_intelligence/model_clients/openai_structured.py` no longer
     imports or calls `openai.lib._pydantic` (or any other
     underscore-prefixed OpenAI SDK module) — that was a same-day
     regression introduced by the fix above, corrected before any commit.
     `_validate_output_model()` now uses only public Pydantic v2 API
     (`BaseModel.model_json_schema()`) to catch an `output_model` that is
     fundamentally unrepresentable as JSON Schema at all (e.g. a
     `Callable`-typed field), with zero tokens spent. This is a *basic*
     preflight, not an exact replica of OpenAI's stricter Structured
     Outputs subset — an `output_model` that passes this basic check but is
     still incompatible with OpenAI's stricter rules would only be
     discovered later, inside `generate()`'s existing sanitized exception
     boundary, as `OpenAIUnexpectedError`. A grep of `market_intelligence/`
     and `scripts/` confirms zero remaining production references to
     `openai.lib` (only explanatory prose/comments naming it, and no
     `import`). **A version-specific SDK-compatibility test
     (`test_real_market_evidence_schema_builds_a_valid_strict_json_schema`
     in `market_intelligence/tests/test_openai_structured.py`) originally
     added here still imported `openai.lib._pydantic` directly. Its
     docstring named `openai==3.3.1` as the installed version it was written
     against, but `pyproject.toml` only declares `openai>=1.99.0` — no exact
     version is actually pinned, so that docstring overstated the guarantee
     the test provided. This has since been superseded in the PR #20 review
     response below: that test was replaced with
     `test_real_market_evidence_schema_passes_the_production_schema_preflight`,
     which exercises production's own `_validate_output_model` preflight and
     `MarketEvidenceModelAnalysis.model_json_schema()` (public Pydantic API
     only), eliminating the private-SDK test dependency entirely.**

  `docs/OPENAI_PROVIDER_BOUNDARY.md` and `docs/MARKET_EVIDENCE_AGENT.md`
  were updated to match. All existing schema bounds, citation validation,
  output-policy validation, zero-automatic-retry behavior, and sanitized
  error categories were preserved unchanged. No live OpenAI request,
  DuckDB access/modification, or dependency addition was made. Full test
  suite: 1412 passed (up from 1405). `ruff check .` and `git diff --check`
  both pass.

- **PR #20 review response (2026-08-24, same day, docs/tests-only — no live
  request, DuckDB access, or dependency change made).** Two review findings
  on the diagnosis/hardening above were addressed, with zero runtime
  behavior change (no schema bound, advisory budget, error category, retry
  behavior, dependency, or the recorded 2026-08-24 live failure itself was
  altered):

  1. **Overclaiming corrected.** Every claim in code, tests, and docs stating
     or implying that OpenAI's Structured Outputs generation is *documented*
     not to enforce `minLength`/`maxLength`/`minItems`/`maxItems`, or that
     exceeding one of these bounds was the *proven* cause of the one
     authorized 2026-08-24 live failure, has been corrected
     (`market_intelligence/model_clients/openai_structured.py`'s
     `OpenAIParseFailureError` docstring,
     `market_intelligence/agents/market_evidence_agent.py`'s advisory-budget
     comment, `docs/OPENAI_PROVIDER_BOUNDARY.md`, this file, and the
     `test_openai_structured.py` regression-test docstrings/comments) to
     instead state plainly: the exact violated response field/value from
     that live attempt was, and remains, unavailable (this client never
     captures or logs raw model output); exceeding a Pydantic-only bound is
     one plausible, locally reproducible failure mode for that attempt, not
     its established/proven cause; the advisory prompt budgets added above
     reduce that plausible risk but do not guarantee any future request will
     pass validation; and no claim is made that official OpenAI
     documentation establishes this non-enforcement behavior specifically
     for the non-fine-tuned `gpt-5-mini` model this project uses.
  2. **Version-specific private-SDK test removed.** The one remaining
     `openai.lib._pydantic`-importing test (see item 2 immediately above)
     has been replaced with
     `test_real_market_evidence_schema_passes_the_production_schema_preflight`,
     which exercises production's own `_validate_output_model` preflight and
     the real `MarketEvidenceModelAnalysis.model_json_schema()` output (both
     public Pydantic v2 API only). `market_intelligence/tests/
     test_openai_structured.py` now has zero imports of any
     private/underscore-prefixed OpenAI SDK module, and this test suite no
     longer depends on the installed OpenAI SDK version at all (previously
     the test's docstring named `openai==3.3.1` as a version it assumed,
     even though `pyproject.toml` never pinned an exact version).

  Full test suite: 1412 passed (unchanged from the count recorded above —
  one test was replaced, not added or removed). `ruff check .` and
  `git diff --check` both pass.

- **First completed live Market Evidence Agent run (2026-08-24, same day,
  after the PR #20 hardening above).** A separately authorized follow-up
  `--execute` attempt was made against the real database (symbol `SPY`,
  `session_date_et=2026-08-21`; preflight `eligible=true`,
  `evidence_item_count=20`, matching the earlier dry run). This attempt
  completed: **one live model response was accepted end to end** — schema
  validation, evidence-ID citation validation, and the post-response content
  policy check all passed — the first time this agent has produced an
  accepted `status="completed"` `MarketEvidenceReport` from a live run.
  `evidence_quality="sufficient"`, 5 observations were returned,
  `directional_assessment`/`trade_recommendation` were both the fixed
  `"not_performed"` value as always, model `gpt-5-mini`,
  `input_tokens=1648`, `output_tokens=1535`, `total_tokens=3183`. No
  persistence and no automatic retry occurred — this remains a single-turn,
  no-tools, no-storage component exactly as documented above.

  A sanitized record of this run, plus a manual (human) quality read of the
  one accepted report, is kept in
  [docs/MARKET_EVIDENCE_EVALUATIONS.md](docs/MARKET_EVIDENCE_EVALUATIONS.md)
  as evaluation example #1: structured output, citations, and the output
  policy all passed; the latest stored close and the regular-session close
  were kept distinct in the model's summary; the stated five-bar return was
  internally consistent with its own stated endpoints; news/macro/
  session-scope limitations were correctly surfaced; and one minor citation-
  relevance issue was observed (the data-quality observation cited
  `session_same_date_bars` without actually discussing its premarket/
  after-hours counts — a valid evidence ID, but not clearly necessary to the
  statement it supported). **This is one manually read example, not an
  automated evaluation, not a validated evaluation methodology, and not
  proof of factual accuracy, prediction quality, analytical reliability, or
  trading usefulness** — see `docs/MARKET_EVIDENCE_EVALUATIONS.md` for the
  full caveats. No raw model output, response ID, full evidence payload, or
  credential is reproduced in either document. No code, test, schema bound,
  or agent behavior was changed to produce or record this run.

- **News Evidence Snapshot layer added (2026-08-24, code/tests/docs only;
  read-only, no database write, no live provider request, no OpenAI/
  Anthropic call).** A new module,
  `market_intelligence/market_features/news_evidence.py`
  (`NewsEvidenceBuilder`), and a companion CLI,
  `scripts/build_news_evidence.py`, add a strictly validated, deterministic,
  read-only builder that assembles exactly one JSON-ready snapshot dict from
  data already stored in `news_articles` (migration `0004`) -- infrastructure
  for a future News Analyst agent, not an AI agent or model request itself.
  It makes no network request of any kind, opens the database only via
  `duckdb.connect(path, read_only=True)`, never writes a row or applies a
  migration, and imports only one small, already-reviewed read-only
  validation helper from `data_connectors/` (`normalize_symbol`) -- no
  connector HTTP client, storage-repository write path, or model client is
  imported. Symbol is validated via the same `normalize_symbol` used
  throughout this project; the result-count `limit` is strictly bounded
  `[1, 20]` (default `10`), rejecting booleans, non-integers, and
  out-of-range values before any DuckDB connection is opened. Articles are
  returned newest-first by publication timestamp, tie-broken by the full
  remaining stored identity (`provider` ascending, then
  `provider_article_id` ascending), so two articles from different
  providers sharing the same `created_at` (and even the same
  `provider_article_id`, unique only per provider) still sort
  deterministically. A latest stored `created_at` more than a fixed,
  documented 5-minute clock-skew tolerance ahead of `as_of` sets
  `freshness.future_timestamp_detected`/`freshness.stale` both `true`
  without discarding or rewriting the stored timestamp; at or within the
  tolerance it feeds the normal elapsed-time freshness calculation. Every
  article's `headline`/`provider_summary` is preserved exactly as stored,
  including an empty or whitespace-only summary -- this module performs no
  HTML stripping, prompt-injection filtering, truncation, whitespace
  normalization, or other interpretation of that text, since it is
  explicitly treated as untrusted, third-party provider content throughout.
  `content_scope` (`"headline_only"`/`"headline_and_provider_summary"`)
  classifies a `None`, empty, or whitespace-only summary the same as "no
  summary" for that purpose, without altering the stored value itself.
  Each article carries a stable, code-generated
  `evidence_id` (a truncated SHA-256 hash of
  `(provider, provider_article_id)`, never derived from headline/summary
  text), so the same stored article always produces the same ID across
  snapshots. Article URLs are deliberately excluded from the per-article
  evidence fields; they appear only in a separate top-level
  `audit_provenance` field (keyed by the same `evidence_id`), which itself
  states in the snapshot that a future model-facing consumer (e.g. a News
  Analyst) must exclude it from any payload sent to a model. A missing
  database file, a missing `news_articles` table, or a symbol with no stored
  articles all produce a valid, non-crashing snapshot with
  `freshness.missing`/`freshness.stale` both `true`, mirroring
  `MarketContextBuilder`'s/`SessionQualityBuilder`'s established behavior.
  The database connection is always closed on every code path, and a
  close() failure never masks an earlier, already-sanitized read failure.
  See [docs/NEWS_EVIDENCE_SNAPSHOT.md](docs/NEWS_EVIDENCE_SNAPSHOT.md) for
  the full field contract and known limitations.

  Covered by 41 tests (temporary DuckDB databases only; no live network
  access; no access to the real repository database) covering: input
  validation before any DuckDB access, missing database/table/rows,
  populated ordering and tie-breaking (including same-`created_at`,
  same-`provider_article_id` rows from different providers),
  limit bounds, `content_scope` for present/absent/blank (empty and
  whitespace-only) summaries, freshness/staleness around the fixed
  168-hour threshold, future-publication-timestamp detection at and beyond
  the fixed 5-minute clock-skew tolerance (including that the stored
  timestamp and article are preserved, never discarded or rewritten),
  exact preservation of adversarial headline/summary text, connection-close
  success/failure behavior (including that a close failure never masks an
  already-sanitized read failure), sanitized CLI errors, and that neither
  module imports a network or model library.

  As a read-only sanity check (no separate authorization sought, mirroring
  the same reasoning already documented for `MarketContextBuilder`/
  `SessionQualityBuilder` -- a read-only operation has nothing to roll
  back), `scripts/build_news_evidence.py --symbol SPY --limit 3` was run
  once against the real local database and returned a valid snapshot
  reflecting the 20 already-stored SPY articles
  (`total_stored_article_count_for_symbol=20`, matching the evidence-item
  count already recorded for the Market Evidence Agent's dry run above) --
  no row was written, no migration was applied, and no article content is
  reproduced in this document. This is not a live provider request, an AI
  analysis, or a validated news dataset -- see `DATA_CATALOG.md` for the
  underlying news dataset's own status.

- **News Analyst added (2026-08-24, code/tests/docs only; no live database
  access or live OpenAI request made as part of this change).** A new
  agent, `NewsAnalyst` (`market_intelligence/agents/news_analyst.py`), and a
  dry-run-first CLI, `scripts/run_news_analyst.py`, exist, mirroring the
  Market Evidence Agent's pattern but built on `NewsEvidenceBuilder` (see
  above) instead of `MarketContextBuilder`/`SessionQualityBuilder`. This is
  a **single-turn, no-tools analysis component** -- not an autonomous or
  multi-agent system -- that extracts and organizes provider-reported event
  claims and conditional market-transmission mechanisms from already-stored
  news. It **never predicts SPY (or any symbol's) direction, never states or
  implies a bullish/bearish bias, never recommends a trade, and never
  discusses options** -- `directional_assessment`/`trade_recommendation` on
  every report it produces are always the fixed value `"not_performed"`, and
  the model-facing schema does not even include those fields, so this
  restriction is absolute, exactly as for the Market Evidence Agent.

  A fixed, deterministic preflight gate (symbol match, `freshness.missing`/
  `freshness.stale`/`freshness.future_timestamp_detected` all `False`, and
  at least one article returned) must pass before any OpenAI request is
  made; any failure returns a truthful `status="abstained"` report with
  fixed reason categories and makes **zero OpenAI requests**. The
  model-facing evidence package is built from the snapshot's `articles`
  only -- `audit_provenance` and every article URL are never read or
  referenced anywhere in that construction -- and every headline/
  provider_summary is labeled directly in the evidence payload itself as
  untrusted, provider-reported text, never independently verified fact.
  Every model-authored event claim must cite 1-5 of the exact evidence IDs
  supplied (validated post-response against fabrication, duplication, and
  excess), and a claim asserting the richer
  `content_basis="headline_and_provider_summary"` must actually cite an
  article with that content scope in the evidence sent, or the response is
  rejected (`NewsAnalystContentBasisError`) rather than accepted with an
  overstated evidentiary basis.

  The Market Evidence Agent's post-response content policy check (rejecting
  known directional-prediction, bullish/bearish-bias, trade-recommendation/
  action, and options-related language) was extracted into a small shared
  module, `market_intelligence/agents/non_directional_output_policy.py`,
  exposing exactly one function
  (`find_prohibited_content_category(text) -> str | None`); the Market
  Evidence Agent's own `MarketEvidencePolicyError` and behavior are
  unchanged (its existing 95-test suite -- `test_market_evidence_agent.py`,
  its eval-fixtures file, `test_run_market_evidence_agent.py`, and
  `test_news_evidence.py` -- was re-run after this extraction and still
  passes unchanged), and the News Analyst applies the same shared matcher
  to every event claim's `claim_summary`/`conditional_mechanism` and every
  model-supplied `limitation`, raising its own sanitized
  `NewsAnalystPolicyError` on a match. **This remains a conservative,
  bounded filter and defense-in-depth on top of developer instructions for
  both agents -- not proof that every possible semantic violation is
  detectable.**

  Covered by 75 tests (`test_news_analyst.py`,
  `test_news_analyst_eval_fixtures.py`, `test_run_news_analyst.py`,
  `test_non_directional_output_policy.py`) against fake evidence-builder/
  model-client stand-ins -- no real database or network access in tests.
  The CLI's default mode is a dry run (zero OpenAI requests, prints only
  eligibility, reasons, symbol, article count, freshness flags, and
  headline-only/summary-available counts); `--execute` is required for one
  billed OpenAI request, and execute output never prints a response ID,
  article URLs, `audit_provenance`, the full evidence payload, credentials,
  a database path, the raw provider response, or a traceback. See
  [docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md) for the full contract. As of
  this entry, `NewsAnalyst` has not been run against the real local
  database, and no live OpenAI request has been made using it -- both
  remain separate, future, and not yet authorized. It is not integrated
  into `market_intelligence/orchestration/`, adds no persistence or
  migration, and adds no dashboard, alerting, or brokerage/Robinhood
  integration. Full test suite: 1528 passed. `ruff check .` and
  `git diff --check` both pass.

- **First authorized live News Analyst run: one execute attempt failed
  structured-output validation; offline diagnosis and recurrence-reduction
  hardening applied (2026-08-24, same day).** One separately authorized
  `--execute` attempt was made against the real local database (symbol
  `SPY`, `limit=5`). The deterministic preflight passed and **exactly one**
  live OpenAI request was sent (tokens were spent; no exact count was
  recorded — this client never captures token/response metadata for a
  failed request). That request did not produce an accepted analysis: it
  failed with a sanitized `{"error": "agent_error", "detail": "OpenAI
  response failed structured-output validation.", "category":
  "response_validation_failed"}` — an `OpenAIParseFailureError` raised
  inside `OpenAIStructuredClient.generate()` and propagated unchanged
  through `NewsAnalyst.run()`. **No analysis was accepted, and per this
  task's explicit instruction, no retry or second live request was made** —
  the failure was diagnosed entirely offline, from the sanitized error
  category alone, using no raw model output (this client never captures or
  logs it).

  **Offline diagnosis (2026-08-24, no live request made to investigate).**
  `NewsAnalyst` calls the exact same `OpenAIStructuredClient.generate()`
  used by the Market Evidence Agent, whose own 2026-08-24
  `response_validation_failed` failure was already diagnosed in detail (see
  above and [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)).
  Reading `generate()`'s exception-mapping code
  (`market_intelligence/model_clients/openai_structured.py`) proves the same
  three-way distinction holds for this attempt: (1) **not** a
  request-schema construction failure — `output_model`
  (`NewsAnalystModelAnalysis`) is validated by `_validate_output_model()`
  before any SDK client is built or request sent, which raises
  `OpenAIRequestSchemaError`/`TypeError`/`ValueError`, never
  `pydantic.ValidationError`; a new offline regression test
  (`test_real_news_analyst_schema_passes_the_production_schema_preflight`)
  confirms the real schema passes this preflight and is not structurally
  incompatible, so no schema restructuring was needed or made; (2) **not** a
  refusal or incomplete response — both are reported via
  `StructuredOutputResult.status`, mapped explicitly to
  `NewsAnalystRefusalError`/`NewsAnalystIncompleteError`, neither of which
  was raised; (3) **is** a received-response content validation failure —
  `pydantic.ValidationError` inside `generate()` can only originate from the
  installed OpenAI SDK's own client-side re-validation of an actually
  received response against `output_model`, which structurally proves a
  request was sent and a response was received before validation failed.
  **Because this client never captures or logs raw model output, the exact
  field/value that violated a bound in this one live attempt is unavailable
  and cannot be proven from local evidence alone.** Exceeding one of
  `NewsAnalystModelAnalysis`'s Pydantic-only `minLength`/`maxLength`/
  `minItems`/`maxItems` bounds (e.g. `claim_summary`'s 400-character
  maximum, or `event_claims`' 6-item maximum) is one plausible, locally
  reproducible failure mode for a response that was otherwise
  type/enum/shape-conformant — reproduced offline in two new regression
  tests — **not** established as the proven cause of this specific live
  attempt. OpenAI has not published official documentation establishing
  that its Structured Outputs generation leaves these bound keywords
  unenforced specifically for the non-fine-tuned `gpt-5-mini` model this
  project uses. No strict structured output, citation validation,
  content-basis validation, output-policy validation, or the zero-retry
  behavior was removed or weakened to work around this.

  **Fix applied (2026-08-24, code/tests/docs only — no further live
  request made): advisory output budgets added.** `AGENT_INSTRUCTIONS`
  (`market_intelligence/agents/news_analyst.py`) now includes explicit,
  conservative advisory output budgets — `claim_summary` at most 300
  characters, `conditional_mechanism` at most 200 characters, each
  `limitation` at most 200 characters, and 1-4 event claims preferred (only
  more if genuinely necessary) — mirroring the mitigation already applied to
  the Market Evidence Agent. Each budget carries deliberate margin below its
  corresponding hard Pydantic maximum (400 / 300 / 300 / 6 respectively —
  `MAX_CLAIM_SUMMARY_LENGTH`/`MAX_CONDITIONAL_MECHANISM_LENGTH`/
  `MAX_LIMITATION_LENGTH`/`MAX_EVENT_CLAIMS`, all unchanged). **This is
  instruction-level guidance only: no bound was changed, and the agent still
  performs zero truncation, silent modification, retry, or acceptance of
  invalid output** — a response that ignores this guidance and still
  violates a hard bound still fails schema validation exactly as before.

  Thirteen new focused offline tests were added (no network, no
  credentials): four in `market_intelligence/tests/test_news_analyst.py`
  prove each advisory budget is present in `AGENT_INSTRUCTIONS` and is
  numerically strictly below its corresponding enforced schema maximum, one
  proves `OpenAIParseFailureError`/`response_validation_failed` propagates
  unchanged through `NewsAnalyst.run()` without leaking evidence text; six
  in `market_intelligence/tests/test_openai_structured.py` use the real,
  production `NewsAnalystModelAnalysis` schema — one proves the schema
  passes production's own schema preflight, one proves a synthetic,
  schema-and-bound-conformant response round-trips through `generate()`
  unchanged, two prove distinct representative oversized/invalid responses
  (an oversized `claim_summary`, an excessive `event_claims` count) each
  reproduce the same sanitized `response_validation_failed` category and
  message, and two prove no automatic retry occurs (the constructed SDK
  client is always built with `max_retries=0`, and a failing SDK call is
  made exactly once by `generate()`, whatever the failure category — this
  latter pair covers `OpenAIStructuredClient` generally, not only the News
  Analyst's schema). `docs/NEWS_ANALYST.md` and
  `docs/OPENAI_PROVIDER_BOUNDARY.md` were updated to match. All existing
  schema bounds, citation validation, content-basis validation,
  output-policy validation, and zero-automatic-retry behavior were preserved
  unchanged. No live OpenAI request, DuckDB access/modification, or
  dependency addition was made as part of this diagnostic/hardening change.
  Full test suite: 1541 passed (up from 1528). `ruff check .` and
  `git diff --check` both pass.

- **News Analyst live run sequence completed; market-relevance hardening
  applied and then corrected per review (2026-08-24, same day).** The News
  Analyst's live SPY/`limit=5` execute attempt was retried three more times
  under separate authorization, completing the full, truthful sequence
  documented in [docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)'s "Live run
  sequence and manual quality review" section: (1) `response_validation_failed`
  (already recorded above); (2) the model's response incomplete at
  `max_output_tokens=2048` (`NewsAnalystIncompleteError`,
  `incomplete_reason="max_output_tokens"`); (3) a local request timeout at
  the then-default 30-second timeout after `OPENAI_MAX_OUTPUT_TOKENS` was
  raised to 4096 locally; (4) a **completed** run with
  `OPENAI_MAX_OUTPUT_TOKENS=4096` and `OPENAI_REQUEST_TIMEOUT_SECONDS=120`
  both set locally -- model `gpt-5-mini`, `input_tokens=1575`,
  `output_tokens=2474`, `total_tokens=4049`, 4 cited event claims,
  `directional_assessment`/`trade_recommendation` fixed at
  `"not_performed"` as always. **No automatic retries occurred at any
  point** -- each of the four attempts was a separate, manually authorized
  invocation.

  A manual (human) quality read of the 4 accepted event claims found: (1) an
  article about the expected resignation of the U.S. Army Secretary had been
  converted into a weak, speculative SPY-relevance mechanism involving
  defense procurement -- a connection that should generally not have been
  turned into a claim at all; (2) a Baker Hughes rig-count headline was
  classified `economic_data` with `supply_chain`/`growth` channels --
  defensible, but energy/commodity relevance should not be overstated beyond
  the supplied headline; (3) the remaining PMI and investor-flow claims were
  reasonably grounded in their cited evidence. **This is one manually read
  example from one live run, not an automated evaluation, not a validated
  evaluation methodology, and not proof of extraction quality or reliability
  across other symbols/articles.**

  Finding (1) directly motivated a hardening change (code/tests/docs only --
  no further live OpenAI request or DuckDB access was made to apply it),
  which a same-day review then found two remaining gaps in and one further
  correction for -- all three are described together here, reflecting only
  the final, corrected state (code/tests/docs only throughout; no live
  request or DuckDB access at any point in this process):

  1. **Relevance classification and rationale.** Every event claim
     (`EventClaim`, `market_intelligence/agents/news_analyst.py`) now
     requires a strict, model-authored `relevance` classification
     (`"direct"|"broad_market"|"sector_or_industry"` -- deliberately no
     `"unknown"` value that could permit an unsupported claim) and a
     required, bounded `relevance_rationale` (hard max 300 characters,
     advisory budget 200). A post-response validation step,
     `_validate_relevance` (raising `NewsAnalystRelevanceError`, category
     `relevance_invalid`), rejects a blank/whitespace-only rationale, an
     oversized rationale (defense-in-depth alongside the hard Pydantic
     bound), a rationale matching a fixed, deterministic denylist for a bare
     "could affect markets"-style mechanism with no named channel, and a
     `"broad_market"`/`"sector_or_industry"` claim asserted with zero
     `transmission_channels` (internally incompatible, since those two
     relevance values are only meaningful with at least one supporting
     channel; `"direct"` carries no such requirement). `AGENT_INSTRUCTIONS`
     directs the model to omit an article entirely -- write no event claim
     about it -- whenever its connection to the requested symbol would
     require inventing unstated facts, only a generic mechanism can be
     given, or no recognized transmission channel applies, and states
     explicitly that producing claims for fewer articles than supplied is
     valid and often correct.

  2. **Truthful omission wording (review finding: the deterministic code
     cannot know *why* the model left an article uncited).** When at least
     one supplied article's evidence ID was not cited by any event claim,
     the agent itself (never the model) prepends one fixed, deterministic
     limitation to the final report's `limitations` -- but its wording now
     states only the observable fact, e.g. `"1 of 2 supplied articles were
     not included in retained claims."`, never a claimed reason (e.g.
     "insufficiently relevant") this code cannot prove
     (`_build_report_limitations`). It never names the uncited article, its
     headline, or its audit URL, and truncates the combined limitations list
     to the existing `MAX_LIMITATIONS` bound (6) if necessary. (The original
     version of this change used the wording `"...were omitted as
     insufficiently relevant..."`, which attributed a reason the code cannot
     actually verify -- corrected here before commit.)

  3. **All-irrelevant-evidence path (review finding: a hard minimum of one
     event claim could force the model to fabricate a claim even when
     nothing supplied was relevant).** `MIN_EVENT_CLAIMS` was changed from 1
     to **0**: `event_claims` may now be structurally empty.
     `MAX_EVENT_CLAIMS` (6) and the 1-4 preferred advisory range are
     unchanged -- one claim per article was never required and still is not.
     A new post-response check, `_validate_claims_quality_consistency`
     (also raising `NewsAnalystRelevanceError`), enforces a strict
     biconditional: `event_claims` is empty **if and only if**
     `evidence_quality == "insufficient"`. Empty claims paired with
     `"sufficient"`/`"limited"` are rejected (self-contradictory); nonempty
     claims paired with `"insufficient"` are also rejected as an
     incompatible abstention state -- which additionally closes off using a
     low-effort claim as a fabricated placeholder while still flagging the
     evidence as insufficient. When `event_claims` is empty this way,
     `NewsAnalyst.run()` builds a **code-controlled** `status="abstained"`
     report (the model never sets `status` itself) with the new fixed reason
     `ABSTAIN_REASON_NO_SUFFICIENTLY_RELEVANT_ARTICLES` =
     `"no_sufficiently_relevant_articles"`; `evidence_quality` is still
     recorded (`"insufficient"`) and `model_metadata` is still populated
     (tokens were spent, unlike a preflight abstention). The truthful
     omission limitation from (2) applies here too, correctly reporting that
     all supplied articles were not included in retained claims.

  4. **Settings defaults corrected to match the live run
     (`market_intelligence/config/settings.py`).** `openai_max_output_tokens`'
     default was raised from 2048 to **4096**, and
     `openai_request_timeout_seconds`'s default was raised from 30.0 to
     **120.0** -- the exact values live evidence showed were required for a
     completed run (see the four-attempt sequence above). Both fields' upper
     bounds (`le=16000`/`le=120`) are unchanged, and both remain overridable
     via `.env`/the environment. `.env.example` documents the same values
     explicitly for visibility (unchanged from the prior entry, since the
     values themselves were already correct there -- only the code default
     was previously left at its old, now-insufficient value).

  No existing hard schema bound (other than the deliberate `MIN_EVENT_CLAIMS`
  relaxation in (3), which is itself gated by the new consistency check),
  citation validation, content-basis validation, output-policy validation,
  or zero-automatic-retry behavior was weakened.

  Test coverage: `market_intelligence/tests/test_news_analyst.py` covers the
  full relevance contract (all three relevance values accepted when
  grounded; a `"broad_market"`/`"sector_or_industry"` claim rejected with
  zero transmission channels; blank/whitespace-only/oversized/generic
  rationales rejected; a specific, channel-naming rationale accepted; an
  adversarial, self-asserting-relevance headline unable to force a generic
  rationale past validation; `NewsAnalystRelevanceError` never echoing
  rejected text), the truthful uncited-articles limitation (present with the
  correct wording, absent when every article is cited, never leaking a
  headline/URL, correctly truncated at capacity), and the all-irrelevant-
  evidence path (the schema permitting a structurally empty `event_claims`;
  a code-controlled abstained report with the fixed reason and populated
  token metadata when evidence is genuinely insufficient; the biconditional
  rejecting empty claims paired with `"sufficient"`/`"limited"` evidence
  quality, and rejecting nonempty claims paired with `"insufficient"`).
  `market_intelligence/tests/test_openai_structured.py`'s real-schema
  regression tests were updated for the now-required `relevance`/
  `relevance_rationale` fields and the schema's new `minItems=0` on
  `event_claims`. `market_intelligence/tests/test_run_news_analyst.py`'s CLI
  fixture was updated the same way. `market_intelligence/tests/test_settings.py`
  now asserts the corrected 120.0/4096 defaults. The Market Evidence Agent's
  own test suite was re-run unchanged and still passes -- this change
  touches only `market_intelligence/agents/news_analyst.py`,
  `market_intelligence/config/settings.py`, and their own tests.
  `docs/NEWS_ANALYST.md`, `docs/OPENAI_PROVIDER_BOUNDARY.md`, and
  `.env.example` were updated to match. Full test suite: 1567 passed (up
  from 1541 before this change; up from 1560 after the three review
  corrections above). `ruff check .` and `git diff --check` both pass. No
  live OpenAI request, DuckDB access/modification, commit, or push was made
  as part of this change.

- **Macro Evidence Snapshot layer added (2026-08-24, code/tests/docs only;
  read-only, no database write, no live provider request, no OpenAI/
  Anthropic call).** A new module,
  `market_intelligence/market_features/macro_evidence.py`
  (`MacroEvidenceBuilder`), and a companion CLI,
  `scripts/build_macro_evidence.py`, add a strictly validated,
  deterministic, read-only builder that assembles exactly one JSON-ready
  snapshot dict from data already stored in `macro_observations` (migration
  `0006`) -- infrastructure for a future Macro Analyst agent, not an AI
  agent or model request itself. It makes no network request of any kind,
  opens the database only via `duckdb.connect(path, read_only=True)`, never
  writes a row or applies a migration, and imports only one small,
  already-reviewed read-only validation pair from `data_connectors/`
  (`normalize_series_id`/`FredInvalidSeriesIdError`) -- no connector HTTP
  client, storage-repository write path, or model client is imported. It
  never labels a series bullish/bearish, never classifies a market regime,
  never infers a rate-cut/hike direction, never predicts, never describes a
  transmission mechanism, and never recommends anything (including options
  language); it performs no transformation, interpolation, forward-filling,
  seasonal adjustment, or derived-change calculation of any kind.

  The requested FRED series IDs are strictly validated and normalized
  before any DuckDB connection is opened: a bare string or boolean passed
  as the series collection is rejected (not treated as a sequence), an
  empty selection is rejected, more than `MAX_SERIES_IDS` (`10`) series is
  rejected, each entry is validated via the same `normalize_series_id`
  used by `FredMacroDataClient`, and -- unlike `MarketContextBuilder`'s
  macro-series handling -- a duplicate series ID *after* normalization
  (e.g. `"fedfunds"` and `"FEDFUNDS"`) is rejected outright rather than
  silently deduplicated. The normalized result preserves the caller's
  requested order (never sorted), so the snapshot's `series` array always
  reflects the order actually requested. Default series: `FEDFUNDS`.

  **Vintage handling:** a series' full stored identity is `(provider,
  series_id, observation_date, realtime_start, realtime_end)`. For each
  requested series, exactly one row is selected via a fixed, documented
  ordering -- `ORDER BY observation_date DESC, realtime_start DESC,
  realtime_end DESC LIMIT 1` -- i.e. the latest observation date on file,
  then the most recently reported revision of that date. Every field on
  the resulting entry comes from that single chosen row; fields from a
  different vintage are never mixed in, and `realtime_start`/
  `realtime_end` are always reported explicitly so a future consumer can
  audit exactly which revision window was selected.

  **Freshness:** a fixed, documented `stale_after_days` threshold (`90`
  elapsed days, chosen as a conservative default suitable for monthly
  macro observations, mirroring `MarketContextBuilder`'s `MACRO_STALE_AFTER`
  rationale) is reported on every series entry; a series under this
  threshold is not thereby claimed to be economically current, only "not
  yet flagged stale by this fixed clock." A future-dated observation is
  flagged (`future_date_detected`, which also forces `stale: true`) only
  beyond a small, fixed, documented one-day tolerance
  (`FUTURE_DATE_TOLERANCE_DAYS`) -- absorbing ordinary date/timezone
  rounding around a calendar-only `observation_date` without hiding a
  genuinely implausible future-dated observation -- and the implausible
  date itself is always preserved exactly, never discarded or rewritten.
  FRED's own `"."` missing-observation marker is preserved as
  `latest_value: null`, `latest_is_missing: true`, distinct from
  `has_stored_observation: false` (no row at all).

  Every entry's stable `evidence_id` (prefixed `macro_`, a truncated
  SHA-256 hash) is derived only from the chosen row's full stored identity
  -- provider, series ID, observation date, and realtime window --
  **never from its value**, so re-selecting the same stored vintage always
  produces the same ID and a genuinely different vintage always produces a
  different one. A missing database file, a missing `macro_observations`
  table, or a requested series with no stored observation all produce a
  valid, non-crashing snapshot (`has_stored_observation: false`,
  `freshness.missing`/`freshness.stale` both `true`), mirroring
  `MarketContextBuilder`'s/`NewsEvidenceBuilder`'s established behavior.
  The database connection is always closed on every code path, and a
  close() failure never masks an earlier, already-sanitized read failure.
  Aggregate `flags.missing_series`/`flags.stale_series`/
  `flags.future_dated_series` summarize the per-series flags across the
  whole requested set. See
  [docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md) for
  the full field contract and known limitations.

  Covered by 40 tests (temporary DuckDB databases only; no live network
  access; no access to the real repository database) covering: input
  validation before any DuckDB access (non-sequence, boolean, empty,
  excessive count, malformed ID, boolean entry, duplicate-after-
  normalization), missing database/table/series, deterministic requested
  ordering, partial coverage across a mixed requested set, latest-
  observation/latest-vintage selection (including that vintages are never
  combined), missing-value preservation, staleness at/beyond the 90-day
  threshold, future-date detection at/beyond the one-day tolerance,
  evidence-ID stability and identity-not-value derivation, aggregate flag
  correctness, coverage/missing-observation counts, Decimal/date
  serialization, connection closure/error masking, sanitized CLI failures
  (including a repeatable `--series` argument and rejected duplicates), and
  proof of no network/model imports. Full test suite: 1607 passed (up from
  1567). `ruff check .` and `git diff --check` both pass. This has **not**
  been run against the real local database as part of this change (a
  read-only operation with no separate authorization sought as part of
  this task). No migration, dependency, agent, prompt, OpenAI/FRED call,
  scheduling, persistence, direction, prediction, or trading functionality
  was added.

- **FRED series-metadata pipeline added (2026-08-24, code/tests/docs only;
  no live FRED request, no write to the real database, migration `0008`
  not applied to the real database).** As distinct from the existing
  series-*observations* pipeline (`get_observations()`/
  `macro_observations`/`MacroObservationRepository`, see above), this adds
  a narrow pipeline for FRED series-level *metadata* -- title, units,
  frequency, seasonal adjustment, popularity, notes, observation date
  range, and last-updated timestamp -- so a future Macro Analyst never
  interprets an unlabeled number.

  `FredMacroDataClient.get_series_metadata()`
  (`market_intelligence/data_connectors/fred_macro_data.py`) fetches one
  series' metadata via FRED's official series endpoint
  (`https://api.stlouisfed.org/fred/series`), reusing the same
  `normalize_series_id` validation as every other connector method. It
  validates before any HTTP request is constructed, requests exactly one
  series per call, uses an explicit timeout, reads the API key only from
  `Settings`, and never exposes the request URL, query parameters, the API
  key, the raw response body, or provider-reported free text (`title`,
  `notes`) in any exception or sanitized status output. The response must
  contain exactly one matching series (matched against the requested,
  normalized series ID); every required string field
  (`title`/`frequency`/`frequency_short`/`units`/`units_short`/
  `seasonal_adjustment`/`seasonal_adjustment_short`) must be nonblank;
  `observation_start`/`observation_end` must be strict `YYYY-MM-DD` dates;
  `last_updated` must be a strict, timezone-aware timestamp in FRED's
  documented `"YYYY-MM-DD HH:MM:SS±HH[:MM]"` shape and is normalized to
  UTC; `popularity` must be a plain nonnegative integer (booleans
  explicitly rejected); and `notes` is preserved exactly as FRED reported
  it (or `None` when FRED reports none) -- provider text is never
  interpreted or summarized. Any malformed or mismatched payload fails the
  whole request; no partial `FredSeriesMetadata` object is ever returned.

  Migration `0008`
  (`market_intelligence/storage/migrations/0008_create_macro_series_metadata.sql`)
  defines `macro_series_metadata` with primary key `(provider, series_id)`
  and the exact normalized fields above, plus `first_ingested_at`,
  `last_seen_at`, and `ingestion_run_id`. Unlike `macro_observations`,
  series metadata has no revision/vintage window to preserve as part of
  its identity -- FRED's series endpoint always reports current metadata --
  so `MacroSeriesMetadataRepository`
  (`market_intelligence/storage/macro_series_metadata_repository.py`)
  always refreshes every mutable metadata/provenance column in place on a
  repeat ingestion of an already-known series, rather than treating it as
  a conflict. It requires `provider` to be exactly `"fred"`, validates the
  entire item before opening any connection or creating an ingestion run,
  writes the metadata plus the final `succeeded` `ingestion_runs` status
  update inside one atomic transaction, and mirrors the existing
  repositories' sanitized-failure/rollback behavior (a
  `MacroSeriesMetadataStorageValidationError` for invalid input with zero
  writes; a `failed` `ingestion_runs` row with a sanitized
  `error_category` for a storage failure; a sanitized
  `MacroSeriesMetadataStorageError`, never a raw exception, path, SQL, or
  credential, if rollback or failure-recording itself fails).

  A one-shot script, `scripts/ingest_fred_series_metadata.py --series-id
  FEDFUNDS`, makes at most one bounded, read-only request and stores the
  result; it prints only `configured`, fetch outcome, series ID, and a
  storage outcome/status (inserted vs. updated, ingestion-run status) --
  never the title, units, frequency, seasonal adjustment, notes,
  popularity, last-updated timestamp, database rows, or credentials. **It
  was not run live as part of this change.**

  `MacroEvidenceBuilder`
  (`market_intelligence/market_features/macro_evidence.py`) was updated to
  read this table read-only, alongside `macro_observations`: each series
  entry now also reports `metadata_available` plus (when available)
  `title`, `frequency`, `units`, and `seasonal_adjustment`, read exactly as
  stored -- never inferred from the series ID itself -- and the snapshot
  adds an aggregate `flags.missing_metadata_series` list. Existing
  observation-derived fields (`latest_value`, etc.) are unchanged. A
  missing `macro_series_metadata` table or a series with no stored
  metadata is valid, non-error input, mirroring the builder's existing
  behavior for missing observations; metadata and observations are read
  and reported independently, so either can be present without the other.

  Covered by focused tests reusing existing helpers/patterns (not a full
  matrix): the connector's success/normalization path, exactly-one-series
  matching, per-field validation failures (blank required strings,
  malformed dates, malformed/naive `last_updated`, invalid `popularity`,
  invalid `notes` type), and sanitized-error/no-credential-leak behavior
  (`market_intelligence/tests/test_fred_macro_data.py`); the repository's
  insert/refresh-on-repeat behavior, provider enforcement, field
  validation, atomic-write/rollback/failure-status behavior, and
  no-leakage checks
  (`market_intelligence/tests/test_macro_series_metadata_repository.py`);
  the script's invalid-input/not-configured/success/failure paths and
  sanitized output
  (`market_intelligence/tests/test_ingest_fred_series_metadata.py`); the
  updated `MacroEvidenceBuilder` behavior (metadata available/unavailable,
  independent of observation presence, missing table, partial coverage
  across multiple series, aggregate flag, no inference from series ID,
  values unchanged) added to
  `market_intelligence/tests/test_macro_evidence.py`; and updated
  migration/health-check tests in
  `market_intelligence/tests/test_database.py` (migration `0008` schema,
  primary key, `NULL`-notes support, and a `0007`→`0008` upgrade test
  preserving existing rows). Full test suite passes; `ruff check .` and
  `git diff --check` both pass. **Migrations `0001`–`0007` are unchanged,
  migration `0008` has not been applied to the real database (which
  remains at schema version `0007`), and no live FRED request or real
  database write was made as part of this change.** No agent, prediction,
  regime label, change/delta calculation, trade recommendation, or
  options/execution logic was added.

## Next Planned Work

1. Data connector design — read-only Alpaca market-data, Alpaca news,
   Alpaca historical bars, and FRED connectors now exist (see above).
   Verified schema/provenance details belong in `DATA_CATALOG.md` once
   bulk data is actually pulled and inspected, not just a connectivity
   check.
2. Provider configuration — Alpaca and FRED credential handling (via
   `.env`, never committed) is in place.
3. First connection tests — done for Alpaca market data (read-only
   snapshot connectivity check) and FRED (read-only latest-observation
   connectivity check), recorded in `DATA_CATALOG.md`/`PROJECT_STATE.md`.
   Done live for Alpaca news (one authorized SPY-news request, see above).
   Alpaca historical bars was checked twice with separate authorization:
   the first implicit-SIP check failed with a sanitized 4xx, and the second
   explicit-IEX check succeeded with a sanitized 2xx. Connectivity and
   response normalization are verified on IEX only. No bars from either of
   those two connectivity checks were stored. A separate, later authorized
   2026-08-21 ingestion (see item 7 below) subsequently stored 248 bars;
   that is one controlled ingestion run, not a complete or validated bars
   dataset.
4. Database initialization — done (see above): the local DuckDB storage
   foundation is initialized under `data/`, now at schema version `0005`
   (`market_bars` applied) after the authorized real-database migration
   run described above; the database was backed up before that migration
   was applied.
5. News storage — done (see above): `news_articles` schema, storage
   service, and manual ingestion script exist, and one authorized live
   ingestion has succeeded (10 received, 10 inserted, 0 failed). This
   confirms the storage pipeline for one run; a validated, cataloged news
   dataset (per `DATA_CATALOG.md`'s Required Fields) is still separate,
   future work.
6. Historical bars connector — done (see above): a read-only,
   single-symbol historical-bars connector exists and is unit-tested
   (`AlpacaBarsClient`) and does not store bars in DuckDB. Its first
   authorized live check reached Alpaca but failed (sanitized 4xx) under
   the connector's prior default (implicit SIP) request — preserved above
   as an honest diagnostic record; the connector was then hardened to
   explicitly request the IEX feed on every request, and a second
   authorized live check against the hardened connector has now succeeded
   (sanitized 2xx, SPY, `5Min`, `feed=iex`, 5 bars). This verifies live
   connectivity and response normalization on IEX only — not SIP. The
   connector was also hardened to explicitly fix `adjustment=raw`/
   `currency=USD` (see above); this hardening has since been exercised by
   a live ingestion run (see item 7 below), in addition to unit tests.
7. Market-bar storage — done (see above): `market_bars` schema (migration
   `0005`), `BarRepository`, and `scripts/ingest_alpaca_bars.py` exist,
   are covered by tests using temporary databases and mocked bars, and
   migration `0005` has now been applied to the real database. A first
   authorized live ingestion has succeeded (SPY, `5Min`, `iex`, `raw`,
   `USD`: 248 received, 248 inserted, 0 failed; 248 rows verified stored,
   covering 2026-08-17T12:25:00Z through 2026-08-19T20:00:00Z). This
   confirms one controlled ingestion run and transactional storage; it is
   not a complete, gap-free, or research-validated historical bars
   dataset. Remaining future work: a validated, cataloged historical bars
   dataset per `DATA_CATALOG.md`'s Required Fields (broader coverage,
   direct inspection of stored data, gap/quality analysis).
8. Reviewed data contracts for actual provider data — versioned migrations
   and storage/repositories exist for both market bars (see above) and
   macro observations (migration `0006`, `MacroObservationRepository`, see
   the "Historical FRED observations" bullet above). Migration `0006` has
   been applied to the real database and a first authorized live FRED
   observations ingestion has succeeded (12 FEDFUNDS observations, see
   above). Remaining future work: a validated, cataloged macro-observations
   dataset per `DATA_CATALOG.md`'s Required Fields (broader series/date
   coverage, direct inspection of stored data, gap/quality analysis).
9. Macro-analyst agent groundwork — the FRED historical-observations
   connector and storage pipeline (see above) now include one authorized
   live ingestion run, in addition to existing infrastructure. No AI agent,
   prediction, sentiment analysis, options logic, or trading execution has
   been built on top of it, and none is planned as part of this
   infrastructure change.
10. Ingestion-orchestration layer — done, including a first authorized live
    run (see above): job contracts, committed configuration, a
    dry-run-first CLI (`scripts/run_ingestion_pipeline.py`), job adapters,
    a fail-closed run lock, and a persistent audit trail (migration
    `0007`) all exist and remain tested against temporary databases and
    mocked HTTP transports. Migration `0007` has now been applied to the
    real database, and one authorized `--all --execute` run has succeeded
    across all three reviewed jobs (see above). Remaining future work: any
    decision on scheduling, unattended/repeated operation, or additional
    reviewed job types remains separate, future, and not yet authorized;
    this orchestration layer is still groundwork for future specialized
    agents, not an agent itself, and this one run is not evidence of
    unattended reliability.
11. Market-context snapshot layer — done, code/tests only (see above):
    `MarketContextBuilder`
    (`market_intelligence/market_features/market_context.py`) and
    `scripts/build_market_context.py` exist, are read-only end to end, and
    are covered by tests against temporary DuckDB databases only. Not yet
    exercised against the real database as part of this change (a
    read-only operation, so nothing to authorize or roll back). Remaining
    future work: any decision to have an actual AI agent consume this
    snapshot, add derived features beyond this bounded set, or persist
    snapshots remains separate, future, and not yet authorized.
12. Market-context snapshot fixes (2026-08-23, code/tests/docs only, not run
    against the real database as part of this change). Two targeted fixes
    were made to the market-context snapshot layer (item 11):
    - **Weekend false-staleness fixed:** `BARS_STALE_AFTER` was changed from
      a 24-hour to a 72-hour elapsed-time threshold, so a Friday-afternoon
      bar is no longer falsely reported `bars_stale: true` over a normal
      weekend. This remains a plain elapsed-time threshold, not an
      exchange-calendar or holiday-aware one — it is deliberately
      weekend-tolerant, not weekend-*aware*. The actual latest stored bar
      timestamp remains exposed (`price.latest_bar_timestamp_utc`,
      `coverage.bars.latest_bar_timestamp_utc`) so a future, stricter,
      calendar-aware consumer can still make its own decision from the raw
      timestamp. Focused tests prove a Friday bar is not stale on Saturday
      and that a bar older than 72 hours is still correctly flagged stale.
    - **Session provenance added:** `bars_provenance` now includes a fixed,
      deterministic `session_scope: "provider_returned_unfiltered"` field.
      Stored bars are not restricted to regular trading hours (no RTH
      filter is applied anywhere in this project's ingestion or this
      snapshot layer) and may include pre-market/after-hours observations,
      so the latest stored bar — and `price.latest_close` — is never
      silently implied to be an official regular-session market close.
    No dependency, market-calendar library, migration, network call,
    persistence, prediction, or agent logic was added as part of this
    change. See [docs/MARKET_CONTEXT_SNAPSHOT.md](docs/MARKET_CONTEXT_SNAPSHOT.md).

13. **Session-quality feature layer added (2026-08-23, code/tests/docs
    only; not run against the real database as part of this change).** A
    new module, `market_intelligence/market_features/session_quality.py`
    (`SessionQualityBuilder`), and a companion CLI,
    `scripts/build_session_quality.py`, add a strictly validated,
    deterministic, read-only report on whether stored `5Min` bars for one
    symbol/session date represent a complete regular trading session --
    intended so a future agent can check data quality before analyzing
    stored price evidence. It makes no network request of any kind, opens
    the database only via `duckdb.connect(path, read_only=True)`, never
    writes a row or applies a migration, and adds no prediction,
    recommendation, or trading-signal logic of any kind.

    Regular session is defined using Python `zoneinfo`
    (`America/New_York`) as weekdays (Monday-Friday) with 78 five-minute
    slots from `09:30` through the slot beginning `15:55` inclusive. **This
    is explicitly weekday/time-window logic only** -- it is not an
    exchange-holiday or early-close calendar, and a stored date that
    happens to be a U.S. market holiday is evaluated with the same rule and
    reported incomplete, never flagged as "no session expected." Symbol and
    an optional explicit session date are strictly validated before any
    DuckDB connection is opened. Exactly one bar identity
    (`provider, symbol, timeframe='5Min', feed, adjustment, currency`) is
    selected per report (whichever identity produced the most recently
    stored `5Min` bar for the symbol) and never mixed with any other
    identity. If no session date is supplied, the most recent stored date
    (under that identity) containing at least one regular-session bar is
    used automatically.

    The report includes: bar provenance; session-definition metadata
    (including the weekday/time-window-only limitation, in the output
    itself); the fixed expected slot count (`78`); the observed
    regular-session slot count; missing expected UTC timestamps; any
    unexpected/off-grid or duplicate timestamps (if detectable -- true
    duplicates are already prevented by `market_bars`'s primary key);
    `complete`/`partial_session`/`missing_data` flags; first/last
    regular-session timestamps; regular-session open (only from an actual
    `09:30` bar) and latest close (explicitly flagged
    `latest_close_is_full_session_close: false` whenever the session is not
    complete, so a partial session's latest observed close is never
    described as an official close); session return (only when both open
    and latest close are available); session high/low/range/total volume
    and a volume-weighted VWAP (only when every observed regular-session
    bar has a stored `vwap`); and same-date premarket/after-hours stored
    bar counts. A missing database file, empty database, or symbol/date
    with no stored regular-session bars all produce a truthful,
    non-crashing report rather than an error. See
    [docs/SESSION_QUALITY.md](docs/SESSION_QUALITY.md) for the full field
    contract and known limitations. No dependency, market-calendar
    library, migration, network call, persistence, prediction, or agent
    logic was added as part of this change; it was not run against the
    real repository database (a read-only operation, so nothing to
    authorize or roll back).

14. **OpenAI structured-output provider boundary added (2026-08-23,
    code/tests/docs only; no live OpenAI request or connectivity check made
    as part of this change).** `OpenAIStructuredClient`
    (`market_intelligence/model_clients/openai_structured.py`) and two new
    non-secret `Settings` fields exist (see above and
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)),
    covered by 37 mocked tests using an injected fake SDK client (no real
    `openai.OpenAI` client is ever constructed in tests, no network call is
    ever made). As of 2026-08-23, remaining future work included any live
    connectivity check. **This has since been superseded: a first
    authorized live OpenAI connectivity check succeeded on 2026-08-24 (see
    Status above and
    [docs/OPENAI_PROVIDER_BOUNDARY.md](docs/OPENAI_PROVIDER_BOUNDARY.md)) —
    this confirms connectivity and response normalization only.** Remaining
    future work: any agent that actually calls this client with real
    developer instructions and evidence, and any decision to build
    forecast/recommendation logic on top of it, all remain separate,
    future, and not yet authorized.

15. **Market Evidence Agent added (2026-08-24, code/tests/docs only; no live
    database access or live OpenAI request made as part of this change).**
    `MarketEvidenceAgent` (`market_intelligence/agents/market_evidence_agent.py`)
    and `scripts/run_market_evidence_agent.py` exist (see Status above and
    [docs/MARKET_EVIDENCE_AGENT.md](docs/MARKET_EVIDENCE_AGENT.md)), covered
    by 47 tests against fake builder/model-client stand-ins (no real
    database or network access in tests). **This "not yet run live" status
    has since been superseded — see the "First authorized live Market
    Evidence Agent run" entry in Status above:** a dry run against the real
    database succeeded (eligible, 20 evidence items), and one authorized
    live `--execute` attempt failed structured-output validation
    (`OpenAIParseFailureError`); no analysis was accepted and no retry was
    made. That entry also records the resulting offline diagnosis and the
    sanitized failure-classification hardening (`category` on every
    `OpenAIStructuredError`/`MarketEvidenceAgentError`, a new
    `OpenAIRequestSchemaError`) and new regression tests added against the
    real `MarketEvidenceModelAnalysis` schema. **A successful live
    end-to-end completed run has since been achieved — see the "First
    completed live Market Evidence Agent run" Status entry above and
    [docs/MARKET_EVIDENCE_EVALUATIONS.md](docs/MARKET_EVIDENCE_EVALUATIONS.md)
    — that entry is one accepted example, not repeated or statistically
    characterized reliability.** Remaining future work: any orchestration
    integration, any repeated/broader evaluation of this agent's outputs,
    and any decision to build further agents (e.g. a Macro Analyst agent)
    on this same pattern all remain separate, future, and not yet
    authorized.

16. **News Evidence Snapshot layer** -- done, code/tests/docs only (see
    above): `NewsEvidenceBuilder`
    (`market_intelligence/market_features/news_evidence.py`) and
    `scripts/build_news_evidence.py` exist, are read-only end to end, and
    are covered by 41 tests against temporary DuckDB databases only. A
    read-only sanity check against the real database succeeded (see
    above). Remaining future work: any actual News Analyst agent that
    consumes this snapshot (mirroring `MarketEvidenceAgent`'s pattern), and
    any decision to expand this snapshot's scope, remain separate, future,
    and not yet authorized. **This has since been superseded -- see item 17
    below.**

17. **News Analyst added** -- done, code/tests/docs only (see above):
    `NewsAnalyst` (`market_intelligence/agents/news_analyst.py`) and
    `scripts/run_news_analyst.py` exist (see Status above and
    [docs/NEWS_ANALYST.md](docs/NEWS_ANALYST.md)), covered by 75 tests
    against fake evidence-builder/model-client stand-ins (no real database
    or network access in tests). **This "not yet run live" status has since
    been superseded — see the "News Analyst live run sequence completed"
    Status entry above:** the SPY/`limit=5` live sequence completed (one
    `response_validation_failed` failure, one incomplete response, one local
    timeout, then one accepted `status="completed"` report), and a manual
    quality read of that accepted report's event claims motivated a
    market-relevance hardening change (required `relevance` classification/
    rationale per claim, new post-response relevance validation, a
    truthful/reason-free deterministic limitation for uncited articles, a
    safe code-controlled abstained outcome when no article is sufficiently
    relevant, and corrected `Settings` defaults of 4096 tokens/120s) — see
    the same Status entry.
    Remaining future work: any orchestration integration, any repeated/
    broader evaluation of this agent's outputs (mirroring
    `docs/MARKET_EVIDENCE_EVALUATIONS.md`), and any decision to build
    further agents on this same pattern all remain separate, future, and
    not yet authorized.

18. **Macro Evidence Snapshot layer** -- done, code/tests/docs only (see
    above): `MacroEvidenceBuilder`
    (`market_intelligence/market_features/macro_evidence.py`) and
    `scripts/build_macro_evidence.py` exist, are read-only end to end, and
    are covered by 40 tests against temporary DuckDB databases only. Not
    yet run against the real database as part of this change (a read-only
    operation, so nothing to authorize or roll back, but no separate
    authorization was sought as part of this task either). Remaining
    future work: any actual Macro Analyst agent that consumes this
    snapshot (mirroring `MarketEvidenceAgent`'s/`NewsAnalyst`'s pattern),
    any decision to add derived macro features (e.g. period-over-period
    change) beyond this bounded set, and any decision to expand this
    snapshot's scope all remain separate, future, and not yet authorized.

19. **FRED series-metadata pipeline** -- done, code/tests/docs only (see
    above): `FredMacroDataClient.get_series_metadata()`, migration `0008`
    (`macro_series_metadata`), `MacroSeriesMetadataRepository`,
    `scripts/ingest_fred_series_metadata.py`, and the corresponding
    `MacroEvidenceBuilder` update all exist and are covered by focused
    tests against temporary DuckDB databases and mocked HTTP transports
    only. Migration `0008` has not been applied to the real database
    (still at `0007`), and no live FRED request has been made. Remaining
    future work: separately authorized application of migration `0008` to
    the real database and a first live ingestion run; extending this
    pattern to additional series beyond FEDFUNDS; and any actual Macro
    Analyst agent that consumes the now-labeled evidence (mirroring
    `MarketEvidenceAgent`'s/`NewsAnalyst`'s pattern) all remain separate,
    future, and not yet authorized. **This last item has since been
    superseded -- see item 20 below.**

20. **Macro Analyst added (2026-08-24, code/tests/docs only; no live
    database access or live OpenAI request made as part of this change).**
    `MacroAnalyst` (`market_intelligence/agents/macro_analyst.py`) and
    `scripts/run_macro_analyst.py` exist (see the intro paragraph above and
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md)), covered by 86 tests
    against fake evidence-builder/model-client stand-ins (no real database
    or network access in tests): `test_macro_analyst.py`,
    `test_run_macro_analyst.py`, `test_macro_analyst_eval_fixtures.py`. This
    is a single-turn, no-tools agent built directly on
    `MacroEvidenceBuilder` (item 18 above), explicitly **not** a regime
    classifier, predictor, directional market model, or trading agent. Its
    deterministic preflight gate is all-or-nothing across every requested
    series -- it makes zero OpenAI requests if any requested series is
    missing, lacks official stored metadata, is stale, is future-dated, has
    `latest_is_missing=True`, or has no stable evidence ID, or if the
    snapshot's echoed request does not match the normalized requested
    series list. When eligible, it sends only official stored metadata and
    the single latest stored observation per series (never a database path,
    SQL text, ingestion ID, credential, or raw audit/internal field) and
    makes exactly one OpenAI request. Every macro claim's `content_basis` is
    a fixed literal (`"stored_observation_and_official_metadata"`) the
    agent sets itself -- excluded from the model-facing schema entirely, so
    the model cannot set it to anything else. A dedicated post-response
    content-scope check (`MacroAnalystContentScopeError`, distinct from the
    shared `non_directional_output_policy` denylist the Market Evidence
    Agent and News Analyst also use) rejects trend/change/acceleration/
    deceleration/surprise/historical-extreme/correlation/causation/policy-
    change/market-regime language in every model-authored free-text field,
    since a single snapshot observation with no comparison value or
    consensus expectation can never support such a statement.
    `directional_assessment`/`trade_recommendation` are always the fixed
    `"not_performed"` value, exactly as for the other two agents; no
    sentiment, probability, confidence score, forecast, or options-detail
    field exists anywhere in its schema. **As of this entry, `MacroAnalyst`
    has not been run against the real local database, and no live OpenAI
    request has been made using it** -- both remain separate, future, and
    not yet authorized. It is not integrated into
    `market_intelligence/orchestration/`, adds no persistence or migration,
    and adds no dashboard, alerting, or brokerage/Robinhood integration.
    `python -m pytest` (1873 passed), `python -m ruff check .`, and
    `git diff --check` were all run and pass. See
    [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md) for full detail.
    **This "not yet run live" status has since been superseded -- see item
    21 below.**

21. **First live Macro Analyst run (2026-08-24), and a manual-review-driven
    hardening change that followed it.**

    A separately authorized live `--execute` run was made against the real
    local database and the real OpenAI API, requesting the default series
    (`FEDFUNDS`). The deterministic preflight passed and exactly one OpenAI
    request was sent; it completed successfully with `status="completed"`.
    Sanitized results: stored value `3.63` percent, observation date
    `2026-07-01`, frequency `monthly`, model `gpt-5-mini`, `input_tokens=
    1390`, `output_tokens=1356`, `total_tokens=2746`. As required by this
    agent's absolute, model-excluded fields, the report's
    `directional_assessment` and `trade_recommendation` were both
    `"not_performed"` -- no direction or recommendation of any kind was
    produced or could have been produced. Only this sanitized status is
    recorded here -- no raw provider response, response ID, or full
    evidence payload is reproduced.

    **A manual quality review of that one completed output found two
    issues, neither a schema, citation, or policy violation (both passed
    every check that existed at the time), but both genuine wording/scope
    weaknesses:**

    1. Phrasing along the lines of "at 3.63 percent on 2026-07-01" can
       misleadingly imply a live, point-in-time reading of a stored,
       already-published monthly observation. It should instead say
       something like "the stored monthly observation dated 2026-07-01".
    2. The report listed a transmission channel (inflation) among
       `transmission_channels` that `conditional_mechanism` did not
       actually explain -- the channel was named but never addressed.

    The agent **correctly avoided** any change/trend/comparison claim on
    this run, since at that time the evidence package supplied only the
    single latest stored observation with no comparison value -- exactly as
    designed and documented.

    **This one manual review of one completed output is not a validated
    evaluation methodology, and finding two issues in one output is not
    itself proof that only two issues exist or that any future response
    will be free of similar issues.** It motivated a bounded, deterministic
    hardening change (code/tests/docs only, made the same day, not yet
    exercised live as part of this change -- see below): `MacroEvidenceBuilder`
    (`market_intelligence/market_features/macro_evidence.py`) now adds a
    bounded, deterministic, read-only `recent_observations` excerpt (default
    6, bounded 2-24 observations, newest first, one deterministically
    chosen vintage per date, validated before any DuckDB access) and one
    precisely supported `latest_change_from_previous` comparison (an exact
    `Decimal` absolute difference and `"increased"`/`"decreased"`/
    `"unchanged"` direction between the latest and immediately preceding
    stored observation, available only when both are non-missing and both
    are the series' currently valid, non-superseded vintage -- never a
    percentage, annualized, or basis-point change, and two observations are
    never called a trend). It also now exposes each series' official
    `frequency_short` code. `MacroAnalyst`
    (`market_intelligence/agents/macro_analyst.py`) was correspondingly
    hardened: every claim must now use frequency-aware wording ("the stored
    `<frequency>` observation dated ...", using that series' own official
    frequency -- never assumed "monthly" -- and never point-in-time "at ...
    on `<date>`" phrasing), validated by a new
    `_validate_frequency_wording`/`MacroAnalystFrequencyWordingError`
    check; a series whose `frequency_short` is not one of a small,
    recognized set now fails preflight instead
    (`series_frequency_unrecognized`) rather than let the model guess.
    Increase/decrease/unchanged language is now conditionally permitted,
    but only for a fully validated two-observation comparison claim citing
    exactly the supplied latest/previous evidence IDs, stating both exact
    dates and both exact values, and stating a direction matching the
    supplied evidence exactly -- checked by a new
    `_validate_comparison_claims`/`MacroAnalystComparisonError`; change
    words anywhere else (a single-evidence claim, `conditional_mechanism`,
    or a `limitation`) are still always rejected, and every previously
    forbidden category (acceleration/deceleration, surprise, historical
    extreme, trend, correlation, causation, policy change, market regime)
    remains always rejected. A new
    `_validate_transmission_channels`/`MacroAnalystTransmissionChannelError`
    check now requires every listed `transmission_channels` entry to be
    explicitly and verifiably addressed by `conditional_mechanism` (a
    deterministic, word-boundary token check per channel), directly
    addressing finding (2) above; `"other"` can never be verified this way
    and is always rejected if listed. Zero-claim insufficient abstention,
    citation validation, the shared non-directional output policy,
    code-controlled final fields (`directional_assessment`/
    `trade_recommendation` always `"not_performed"`, `content_basis` always
    the fixed literal), the one-OpenAI-request maximum, and the dry-run
    default were all preserved unchanged. `python -m pytest` (1927 passed),
    `python -m ruff check .`, and `git diff --check` were all run and pass.
    **As of this item, none of this hardening has been exercised against a
    live OpenAI response** -- it has been validated only by tests using a
    fake, hand-authored model client and a fake evidence builder; no live
    database access or live OpenAI request was made as part of this change.
    See [docs/MACRO_EVIDENCE_SNAPSHOT.md](docs/MACRO_EVIDENCE_SNAPSHOT.md)
    and [docs/MACRO_ANALYST.md](docs/MACRO_ANALYST.md) for full detail.

22. **Second live Macro Analyst run failed on content-scope validation
    (2026-08-24, after the item 21 hardening merged as PR #29), and a
    scope-boundary fix that followed it.**

    A separately authorized live `--execute` run was made against the real
    local database and the real OpenAI API, requesting `FEDFUNDS` with
    `recent_observations_limit=6`. The deterministic preflight passed and
    exactly one OpenAI request was sent. The response was **not** accepted:
    it failed the post-response content-scope check
    (`MacroAnalystContentScopeError`, `category=content_scope_invalid`) at
    `field=limitations[0]`, sanitized as:

    ```json
    {"error": "agent_error", "detail": "Model-authored output described a
    trend, change, comparison, correlation, causation, policy change, or
    market regime not supported by a single-snapshot observation
    (field=limitations[0]). The rejected text is never included in this
    error.", "category": "content_scope_invalid"}
    ```

    **No report was accepted from this attempt, and no retry was made** --
    this agent has always made at most one OpenAI request per `run()` call,
    and a rejected response is never retried, truncated, or silently
    modified. Per this agent's own sanitization contract, the model-authored
    text that triggered the rejection is never recorded anywhere, including
    in this document, so **the exact live wording that tripped the check is
    not available and is not reproduced here or claimed to be known.**

    Offline root-cause analysis (no live/network/database access) found a
    plausible, locally reproducible false-positive class in the fixed
    content-scope denylist that existed at the time of this run: the
    denylist matched the bare presence of words like "trend", "regime",
    "correlation", or "causation" in any model-authored free-text field,
    including a `limitation` where those words are used to **negate** an
    unsupported claim -- e.g. "Six observations are insufficient to
    establish a trend," "No regime conclusion can be drawn from this
    bounded excerpt," or "The supplied evidence does not establish
    causation" are desirable, honest limitations, not prohibited claims,
    but the denylist rejected them identically to an affirmative unsupported
    claim. **This is offline analysis of a plausible failure class
    reproduced with locally authored test fixtures, not a claim to know the
    exact live-rejected wording, and not the only possible explanation for
    the observed rejection.**

    A bounded, deterministic fix (code/tests/docs only, not yet exercised
    live as part of this change) was then made to
    `market_intelligence/agents/macro_analyst.py`'s `_validate_content_scope`:

    - `claim_summary` and `conditional_mechanism` are **unchanged and still
      strictly denylisted with no exemption of any kind** -- any prohibited
      trend/change/comparison/correlation/causation/policy-change/regime
      language in either field is still rejected outright, exactly as
      before.
    - For model-supplied `limitations` **only**, a new, narrow, fail-closed
      allowance (`_limitation_content_scope_violation`) permits a
      prohibited-term match when it is immediately adjacent (within a small,
      bounded word gap, in the same clause) to one of a fixed set of
      negation/insufficiency cues: `no`/`not`, `cannot`/`can't`,
      `insufficient to`, `does not`/`do not`, `unavailable`,
      `limited evidence for`, and `cannot be inferred/established/
      determined/assessed`. Text is first split into clauses (on sentence
      terminators and on a comma before a coordinating conjunction) so a
      negated disclaimer clause never shields a separate, unnegated
      affirmative claim elsewhere in the same limitation (e.g.
      "insufficient data to draw conclusions, but the rate is clearly
      following an accelerating trend" is still rejected, for the second
      clause). Every prohibited match in a limitation must be individually
      negated; a single unnegated match anywhere still rejects the whole
      limitation.
    - The shared non-directional output policy check
      (`_enforce_output_policy`), the comparison-claim validator, the
      frequency-wording validator, the transmission-channel validator, all
      citation/series/quality-consistency checks, the one-OpenAI-request
      maximum, and the dry-run default were all preserved unchanged and
      still run on `limitations` exactly as before -- a limitation that
      passes the new negation allowance is still screened by every other
      existing check.
    - The content-scope error message's stale wording ("not supported by a
      single-snapshot observation") was corrected to "not supported by the
      bounded stored evidence," since the evidence package has, since item
      21, included a bounded `recent_observations` history excerpt, not
      only a single snapshot value. The error remains fully sanitized: it
      never includes the rejected text, only a fixed field name.

    14 new focused tests were added to
    `market_intelligence/tests/test_macro_analyst.py` covering: an accepted
    negated-trend limitation, an accepted no-regime limitation, an accepted
    no-causation/no-correlation limitation, an accepted insufficient-history
    limitation, an accepted limitation with multiple independently negated
    clauses, that an accepted negated limitation still makes exactly one
    model call, a parametrized sweep of affirmative (unnegated)
    trend/regime/causation limitations still rejected, a disclaimer-then-
    affirmative-claim limitation still rejected (with a check that no retry
    followed the rejection), that the rejection error never echoes the
    rejected text, that the identical negated wording is still rejected
    outright in `claim_summary` and in `conditional_mechanism` (no
    exemption), and that the shared non-directional output policy still
    fires on a limitation whose negated scope language separately passes
    the new content-scope allowance. `python -m pytest` (1941 passed),
    `python -m ruff check .`, and `git diff --check` were all run and pass.

    **This fix addresses a locally reproducible false-positive class in the
    content-scope denylist. It is not proof of the exact wording that was
    rejected in the live run above, and it does not weaken the comparison
    citation checks, frequency-wording validation, transmission-channel
    validation, any schema bound, the zero-retry behavior, or any other
    safety boundary** -- all of those were re-run unchanged and still pass.
    As of this item, this fix has **not** been exercised against a live
    OpenAI response; no live database access or live OpenAI request was made
    as part of this change.

23. **Core Macro Basket configuration and dry-run-first batch ingestion
    script added (2026-08-24, code/tests/docs only; not run live or against
    the real database as part of this change).** See
    [docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md) for full detail.

    A new committed configuration file,
    `market_intelligence/config/core_macro_series.json`, and its loader/
    validator, `market_intelligence/config/macro_basket.py`
    (`load_core_macro_series`), define a fixed, reviewed universe of exactly
    seven approved FRED series -- `FEDFUNDS` (`policy_rate`), `DGS10`
    (`long_term_rate`), `CPIAUCSL` (`inflation`), `PCEPI` (`inflation`),
    `UNRATE` (`labor`), `INDPRO` (`growth`), `GDPC1` (`growth`) -- each with
    a strictly validated `enabled`/`observation_lookback_days`/
    `recent_observations_limit` contract. Validation enforces: exact
    root/entry field sets (an unknown field anywhere is rejected); normalized,
    unique series IDs; only the seven approved series IDs; that each series'
    `category` exactly matches this module's own fixed, committed
    `APPROVED_SERIES_CATEGORY` mapping (never accepted as arbitrary
    configuration-file text); plain (non-boolean) integers for
    `observation_lookback_days`/`recent_observations_limit`, each within
    fixed bounds -- a conservative, per-series lookback ceiling
    (`FEDFUNDS`/`CPIAUCSL`/`PCEPI`/`UNRATE`/`INDPRO`: 400 days;
    `GDPC1`: 1,100 days; `DGS10`: 180 days) that exists specifically to
    prevent an unbounded historical request, and `2`-`24` for
    `recent_observations_limit` (mirroring `MacroEvidenceBuilder`'s own
    bounds, fixed locally to avoid a layering dependency onto
    `market_intelligence/market_features/`). Entries are always returned in
    the committed file's own deterministic order. Loading performs no
    network I/O and constructs no `Settings`, client, database connection,
    or lock. **No series title, unit, frequency, seasonal adjustment, note,
    or observation value is hardcoded anywhere in this configuration or its
    loader** -- those come only from the existing, reviewed FRED metadata
    endpoint and local storage, at ingestion time.

    A new dry-run-first CLI, `scripts/ingest_core_macro_basket.py`, reuses
    the existing `FredMacroDataClient`, `MacroSeriesMetadataRepository`,
    `MacroObservationRepository`, and the existing orchestration run lock
    (`market_intelligence/orchestration/lock.py`'s `RunLock`, at its usual
    fixed path) unmodified. `--series SERIES_ID` (repeatable) and `--all`
    are mutually exclusive and one is always required; selection is
    validated purely against the already-loaded committed configuration --
    before `Settings`, any client, the network, the database, or the run
    lock are ever constructed -- and selected series are always processed in
    the committed file's own deterministic order. Default behavior is a dry
    run: it resolves one shared, injected UTC "as-of" instant (called
    exactly once for the whole run), computes each selected series' bounded
    observation window (`start = as_of_date - observation_lookback_days`,
    `end = as_of_date`, via the FRED connector's own strict calendar-date
    normalization), and prints a sanitized plan with **zero `Settings`
    construction, zero network requests, and zero database activity of any
    kind**. `--execute` is required for real activity: it acquires the
    existing run lock, then requires the real local database to **already
    be healthy at exactly schema version `0008`** (checked read-only via the
    existing `DuckDBManager.check_health()`, before any network request --
    this script deliberately never applies a migration itself), then
    requires the FRED client to be configured, then processes each selected
    series **sequentially**: exactly one `get_series_metadata` request and
    one bounded `get_observations` request per series (an empty
    observations result is reported `skipped_empty`, mirroring
    `scripts/ingest_fred_observations.py`'s existing convention), with **no
    automatic retry of any request**. One series' failure is recorded
    truthfully but never prevents a later selected series from being
    attempted; the overall run status is only `succeeded` if every selected
    series' metadata request/storage succeeded and its observations
    request/storage either succeeded or was validly empty. This script does
    **not** write to `orchestration_runs`/`orchestration_job_runs` and does
    **not** modify `market_intelligence/orchestration/jobs.json` or any
    migration/schema -- it is a separate, narrower, manual batch tool, not
    scheduling. All output, in both modes, is limited to series IDs, the
    fixed category, configured bounds, computed request-window calendar
    dates, per-series/overall status, and sanitized counts -- never a
    series title, unit, frequency, seasonal adjustment, note, observation
    value, URL, query parameter, raw exception text, SQL, a database path,
    or a credential.

    44 new focused tests were added
    (`market_intelligence/tests/test_macro_basket_config.py`,
    `market_intelligence/tests/test_ingest_core_macro_basket.py`), covering:
    exact root/entry field validation; approved-series-ID and fixed-category
    enforcement; plain-integer/bounded-value enforcement for both numeric
    fields (including the per-series lookback ceiling); deterministic file
    ordering; invalid/duplicate/disabled/unknown series selection rejected
    before any `Settings`/network/database/lock activity; the shared clock
    called exactly once; correctly computed bounded windows for monthly-,
    quarterly-, and daily-lookback series; a dry run making zero
    `Settings`/network/database activity; execute-mode sequential order and
    per-series failure isolation (one series' provider failure does not
    prevent a later series from being attempted, with no automatic retry);
    an unhealthy or wrong-schema-version database failing before any network
    request; lock contention; an empty-observations result reported
    `skipped_empty` rather than failed; and sanitized output (no title,
    unit, value, note, credential, or internal ever leaked) across both
    success and failure paths. `python -m pytest` (1985 passed),
    `python -m ruff check .`, and `git diff --check` were all run and pass;
    the existing `scripts/ingest_fred_observations.py`/
    `scripts/ingest_fred_series_metadata.py` scripts, and every other
    existing test, were left unmodified and re-verified passing as part of
    the same full-suite run.

    **As of this item, this configuration and script exist in code, tests,
    and docs only.** No live FRED request has been made using this script,
    migration `0008` has not been (newly) applied as part of this change
    (the real database's migration state is unchanged by this item), and no
    row has been written to `macro_series_metadata`/`macro_observations` by
    this script. This does not establish a complete, gap-free, or
    research-validated macro dataset for any of the seven series, and it
    implies no forecast, regime classification, or trading signal of any
    kind.

24. **`DGS10` → `GS10` replacement in the Core Macro Basket (2026-08-24,
    code/tests/docs only; no live FRED request or real-database write as
    part of this change).** A separately authorized first live run of
    `scripts/ingest_core_macro_basket.py --all --execute` (following item 23
    above) succeeded for six of the seven approved series -- `FEDFUNDS`,
    `CPIAUCSL`, `PCEPI`, `UNRATE`, `INDPRO`, `GDPC1` -- but failed for
    `DGS10`'s **observations** request only, recorded with sanitized error
    category `provider_error`; `DGS10`'s **metadata** request succeeded.
    Only this sanitized category was recorded -- no raw exception text, URL,
    query parameter, or credential was ever printed or stored. **The exact
    provider-side cause of the `DGS10` observations failure is therefore not
    known, and this entry does not claim otherwise.** This diagnostic record
    is preserved here as an honest record and is not rewritten or deleted.

    In response, `DGS10` (daily, `long_term_rate`) was replaced everywhere
    in the committed Core Macro Basket contract by the official monthly
    FRED series `GS10` ("Market Yield on U.S. Treasury Securities at
    10-Year Constant Maturity, Quoted on an Investment Basis" -- monthly,
    percent, not seasonally adjusted), retained under the same
    `long_term_rate` category. `market_intelligence/config/macro_basket.py`'s
    `APPROVED_SERIES_CATEGORY` and `MAX_LOOKBACK_DAYS_BY_SERIES_ID` mappings,
    the committed `market_intelligence/config/core_macro_series.json` entry,
    `docs/CORE_MACRO_BASKET.md`, `DATA_CATALOG.md`, and the corresponding
    tests (`market_intelligence/tests/test_macro_basket_config.py`,
    `market_intelligence/tests/test_ingest_core_macro_basket.py`) were all
    updated accordingly. Because `GS10` is monthly rather than daily, it now
    uses the same existing conservative monthly `observation_lookback_days`
    policy (400 days, the ceiling already used by `FEDFUNDS`/`CPIAUCSL`/
    `PCEPI`/`UNRATE`/`INDPRO`) instead of `DGS10`'s former 180-day daily
    ceiling; `recent_observations_limit` remains unchanged at `6`. No
    connector, repository, migration, dependency, `.env`, or the real
    DuckDB database was modified as part of this change, and `--series`/
    `--all` selection, dry-run planning, execute-mode ordering/failure
    isolation, and sanitized output remain otherwise unchanged.

    **`GS10` is a proposed, bounded, monthly replacement only -- as of this
    item, it has not yet been requested live.** No live FRED request has
    been made for `GS10`, and no row for it has been written to
    `macro_series_metadata`/`macro_observations`. `python -m pytest` (1985
    passed), `python -m ruff check .` (all checks passed), and
    `git diff --check` (no whitespace errors) were all run as part of this
    change and pass. This implies no forecast, regime classification, or
    trading signal of any kind, and does not establish a complete,
    gap-free, or research-validated macro dataset for any of the seven
    series.

25. **First authorized live `GS10` ingestion and read-only verification
    (2026-08-25, documentation update only -- no code, test, migration,
    configuration, dependency, `.env`, or database change was made as part
    of recording this entry).** Following item 24 above, `GS10` was
    requested live for the first time via a separately authorized
    `scripts/ingest_core_macro_basket.py --execute` run targeting `GS10`.

    The run's reported outcome: mode `execute`, overall status
    `succeeded`, `series_id: GS10`, `metadata_status: succeeded`,
    `observation_status: succeeded`, 13 observations received, 13
    inserted, 0 existing/updated, 0 failed.

    A separate, subsequent read-only verification then reported: the real
    local database at schema version `0008` (8 migrations applied),
    `healthy=True`; 1 stored `macro_series_metadata` row for `GS10`; 13
    stored `macro_observations` rows for `GS10`, covering 2025-07-01
    through 2026-07-01, with 0 missing observations; and the latest
    `macro_series_metadata`/`macro_observations` ingestion runs for `GS10`
    both recorded `succeeded` with no `error_category`. The working tree
    was clean before this documentation update was made, and no live
    request of any kind (FRED, OpenAI, or otherwise) was made as part of
    producing this documentation entry itself.

    **Migration `0008` status superseded:** earlier entries in this
    document (see the "Macro series-metadata pipeline" item above and the
    Status section) correctly recorded that migration `0008` had not been
    applied and the real database remained at schema version `0007` as of
    2026-08-24. Since `scripts/ingest_core_macro_basket.py --execute`
    requires the real database to already be healthy at exactly schema
    version `0008` before making any network request (see
    [docs/CORE_MACRO_BASKET.md](docs/CORE_MACRO_BASKET.md)), and the
    `GS10` execute run above succeeded, migration `0008` must have been
    applied to the real database prior to that run; the read-only
    verification above independently confirms the database is now at
    schema version `0008`, healthy. Those earlier `0007`/"not applied"
    statements are preserved above as honest, correctly time-scoped
    diagnostic records and are not retracted -- they describe the state
    truthfully as of 2026-08-24, before this change.

    **This confirms one bounded, controlled live ingestion for one series
    (`GS10`) and its corresponding read-only storage verification. It does
    not establish a complete, gap-free, or research-validated macro
    dataset for `GS10` or for any other series in the Core Macro Basket**
    -- the other six series' live status is recorded separately above
    (items 23-24) and is unchanged by this entry. Stored coverage and a
    zero missing-observation count describe what is present in local
    storage; they do not establish economic-data correctness or
    predictive/analytical usefulness of any kind. **No forecasting,
    market-direction assessment, options recommendation, or trading
    execution was performed or implied as part of this run or this
    documentation update, and no live request of any kind was made while
    producing this documentation update itself.**

## Notes

- This file should be updated as phases progress. Treat entries here as
  ground truth over any assumptions embedded in code comments, prompts, or
  prior conversations.
