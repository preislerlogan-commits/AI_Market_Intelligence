-- Stores normalized historical stock bars returned by a read-only bars
-- connector (currently only AlpacaBarsClient; see
-- market_intelligence/data_connectors/alpaca_bars.py). This table stores
-- only provider-reported OHLCV/vwap data plus this project's own
-- provenance/ingestion bookkeeping -- never indicators, returns, labels,
-- sentiment, predictions, recommendations, option-contract data, orders,
-- execution fields, credentials, request headers, or raw API responses.
--
-- Idempotency: a bar's identity is the tuple (provider, symbol, timeframe,
-- feed, adjustment, currency, bar_timestamp) -- the exact provenance and
-- point in time a bar describes -- and is the primary key, so re-ingesting
-- the same bar never creates a duplicate row. adjustment and currency are
-- part of the identity (not just descriptive columns) because a
-- differently adjusted or differently denominated bar for the same
-- timestamp is a genuinely different value series, not a duplicate of this
-- one -- even though this project's connector currently only ever produces
-- adjustment='raw', currency='USD' bars (see alpaca_bars.py).
--
-- Provider bar time is kept strictly separate from retrieval and ingestion
-- bookkeeping time, mirroring news_articles (migration 0004):
--   * bar_timestamp      -- the provider-reported timestamp the bar itself
--     describes (UTC).
--   * retrieved_at       -- when the connector fetched the specific API
--     response that produced the currently-stored values (refreshed on
--     every re-ingestion of an already-known bar).
--   * first_ingested_at  -- when this bar was first stored in this
--     database; never changed after the initial insert.
--   * last_seen_at       -- when this bar was most recently observed by any
--     ingestion run; refreshed on every re-ingestion.
--
-- Numeric types: open/high/low/close/vwap use DECIMAL(18,6) rather than
-- DOUBLE so values are stored exactly as decimal quantities (matching the
-- connector's use of Python Decimal, built via Decimal(str(value)) to avoid
-- baking in IEEE-754 binary-float rounding artifacts -- see
-- alpaca_bars.py). 6 fractional digits comfortably covers both standard
-- cent-level OHLC pricing and vwap, which Alpaca can report at
-- sub-cent precision; 12 integer digits leaves ample headroom for any
-- plausible price level. volume and trade_count are BIGINT (whole shares /
-- whole trades; no fractional quantities in a bar). trade_count and vwap
-- are nullable, consistent with the connector, which accepts either as
-- legitimately absent in some provider responses/subscription tiers.
--
-- ingestion_run_id records the ingestion_runs.run_id for the run that most
-- recently wrote this bar's stored values, tying every stored bar back to
-- an auditable ingestion run. Deliberately not declared as a DuckDB foreign
-- key, for the same reason as news_articles.ingestion_run_id (migration
-- 0004): a hard FK on ingestion_runs(run_id) would block otherwise-
-- legitimate future schema evolution of ingestion_runs, since DuckDB
-- refuses to ALTER a table that a FK depends on. This reference is enforced
-- at the application layer (market_intelligence/storage/bar_repository.py)
-- instead.
CREATE TABLE market_bars (
    provider VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    feed VARCHAR NOT NULL,
    adjustment VARCHAR NOT NULL,
    currency VARCHAR NOT NULL,
    bar_timestamp TIMESTAMP NOT NULL,
    open DECIMAL(18,6) NOT NULL,
    high DECIMAL(18,6) NOT NULL,
    low DECIMAL(18,6) NOT NULL,
    close DECIMAL(18,6) NOT NULL,
    volume BIGINT NOT NULL,
    trade_count BIGINT,
    vwap DECIMAL(18,6),
    retrieved_at TIMESTAMP NOT NULL,
    first_ingested_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    ingestion_run_id VARCHAR NOT NULL,
    PRIMARY KEY (provider, symbol, timeframe, feed, adjustment, currency, bar_timestamp)
);
