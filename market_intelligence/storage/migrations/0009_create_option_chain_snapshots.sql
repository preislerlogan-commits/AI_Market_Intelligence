-- Stores normalized SPY option-chain snapshot data returned by the read-only
-- Alpaca option-chain snapshot connector (AlpacaOptionsChainClient; see
-- market_intelligence/data_connectors/alpaca_options_chain.py), across two
-- append-only tables:
--
--   * option_chain_snapshot_batches -- exactly one row per successful executed
--     chain retrieval (one AlpacaOptionsChainClient.get_chain_snapshot call
--     that was then stored), INCLUDING a retrieval that returned zero
--     contracts. This is where the request-level provenance lives: the
--     ingestion-run id, provider, underlying, the explicitly requested feed,
--     the bounded expiration/strike window, the optional call/put filter, the
--     UTC retrieval instant, and the contract count (which may be zero). A
--     successful empty chain still produces exactly one batch row -- with
--     contract_count = 0 and outcome = 'skipped_empty' -- so its feed, bounds
--     and retrieved_at are durably recorded even though it writes no
--     option_chain_snapshots rows.
--   * option_chain_snapshots -- one row per normalized contract snapshot in a
--     batch. Request-level fields are deliberately NOT duplicated here: they
--     live once on the batch row that this row's ingestion_run_id references.
--
-- Neither table stores a raw JSON payload, a directional forecast, a contract
-- ranking, a strategy recommendation, an order, an execution field,
-- credentials, request headers, request URLs/query parameters, page tokens,
-- or a provider response body.
--
-- SPY only: `underlying` is always 'SPY' in this milestone (the connector
-- rejects every other underlying); it is stored explicitly as provenance.
--
-- OPRA vs indicative are never merged. On option_chain_snapshots, `feed` is
-- part of the primary key: an 'opra' observation and an 'indicative'
-- observation of the same contract at the same instant are genuinely
-- different value series (the indicative feed may be delayed or modified by
-- the provider and must not be described as live OPRA data) and are stored as
-- separate rows. On option_chain_snapshot_batches the requested feed is
-- recorded verbatim as `requested_feed`, and every retrieval has its own
-- ingestion-run id, so batches for different feeds are always distinct rows.
--
-- Idempotency:
--   * option_chain_snapshot_batches keys on ingestion_run_id (one fresh id per
--     store call), so each stored retrieval is exactly one row. A batch row is
--     only ever inserted, never updated in place, and a failed or rolled-back
--     store leaves no batch row at all.
--   * option_chain_snapshots identity is
--     (provider, underlying, feed, contract_symbol, retrieved_at), where
--     retrieved_at is the single UTC instant the connector stamped on every
--     snapshot in one get_chain_snapshot call. Re-storing the same connector
--     result (same retrieved_at, same values) refreshes only last_seen_at /
--     ingestion_run_id; re-storing the same identity with conflicting values
--     aborts the whole batch (see
--     market_intelligence/storage/option_chain_snapshot_repository.py). A
--     later, separate ingestion run produces a new retrieved_at and a new
--     batch row, and is stored as new observation rows, overwriting nothing.
--
-- The batch row and all of its snapshot rows are written in one transaction:
-- a validation or storage failure leaves neither a batch row nor any partial
-- snapshot rows. The ingestion_runs row is still recorded (as 'failed' with a
-- sanitized error category) so the attempt stays auditable.
--
-- Time columns are kept strictly separate, mirroring the other data tables:
--   * quote_timestamp / trade_timestamp -- the provider-reported timestamps of
--     the latest quote and latest trade (UTC); nullable because a snapshot may
--     carry neither.
--   * retrieved_at       -- the UTC instant the connector fetched the response
--     (on the batch row, and copied onto each snapshot row as part of its
--     identity).
--   * first_ingested_at  -- when this exact row was first stored; never
--     changed after the initial insert.
--   * last_seen_at       -- when this row was most recently re-observed by an
--     ingestion run; refreshed on every re-ingestion.
--
-- Nullability: on option_chain_snapshots, quote_timestamp, bid/ask price and
-- size, trade_timestamp, trade price and size, implied_volatility, and every
-- Greek are nullable. The snapshot endpoint legitimately omits any of them,
-- and a missing value is stored as NULL -- never as zero.
--
-- OPEN INTEREST IS NOT STORED. Alpaca's option-chain *snapshot* endpoint does
-- not supply open interest, so there is deliberately no open_interest column
-- on either table. It is documented as unavailable for this milestone in
-- DATA_CATALOG.md / docs/OPTIONS_DECISION_WORKFLOW.md and must not be added as
-- populated data by inference or default.
--
-- Numeric types: prices and strike use DECIMAL(18,6); implied_volatility and
-- the Greeks use DECIMAL(20,10) (Alpaca reports these at higher fractional
-- precision than cent-level prices). Both scales are validated on the
-- repository side before any write, so an over-precise or out-of-range value
-- is rejected rather than silently rounded/overflowed by DuckDB. Sizes are
-- BIGINT (whole contracts). contract_count is BIGINT and constrained
-- non-negative.
--
-- ingestion_run_id records the ingestion_runs.run_id of the run that wrote the
-- batch (and, on a snapshot row, the run that most recently wrote that row).
-- Deliberately not a DuckDB foreign key, for the same reason as
-- news_articles.ingestion_run_id (migration 0004), market_bars.ingestion_run_id
-- (0005), macro_observations.ingestion_run_id (0006), and
-- macro_series_metadata.ingestion_run_id (0008): a hard FK on
-- ingestion_runs(run_id) would block otherwise-legitimate future schema
-- evolution of ingestion_runs. The reference is enforced at the application
-- layer instead. The snapshot -> batch reference (a shared ingestion_run_id)
-- is likewise application-enforced.
CREATE TABLE option_chain_snapshot_batches (
    ingestion_run_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    underlying VARCHAR NOT NULL,
    requested_feed VARCHAR NOT NULL,
    requested_expiration_date_gte DATE NOT NULL,
    requested_expiration_date_lte DATE NOT NULL,
    requested_strike_price_gte DECIMAL(18,6) NOT NULL,
    requested_strike_price_lte DECIMAL(18,6) NOT NULL,
    requested_option_type VARCHAR,
    retrieved_at TIMESTAMP NOT NULL,
    contract_count BIGINT NOT NULL,
    outcome VARCHAR NOT NULL,
    first_ingested_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    PRIMARY KEY (ingestion_run_id),
    CHECK (requested_feed IN ('opra', 'indicative')),
    CHECK (requested_option_type IS NULL OR requested_option_type IN ('call', 'put')),
    CHECK (contract_count >= 0),
    CHECK (outcome IN ('succeeded', 'skipped_empty'))
);

CREATE TABLE option_chain_snapshots (
    provider VARCHAR NOT NULL,
    underlying VARCHAR NOT NULL,
    feed VARCHAR NOT NULL,
    contract_symbol VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    expiration_date DATE NOT NULL,
    option_type VARCHAR NOT NULL,
    strike_price DECIMAL(18,6) NOT NULL,
    quote_timestamp TIMESTAMP,
    bid_price DECIMAL(18,6),
    bid_size BIGINT,
    ask_price DECIMAL(18,6),
    ask_size BIGINT,
    trade_timestamp TIMESTAMP,
    trade_price DECIMAL(18,6),
    trade_size BIGINT,
    implied_volatility DECIMAL(20,10),
    delta DECIMAL(20,10),
    gamma DECIMAL(20,10),
    theta DECIMAL(20,10),
    vega DECIMAL(20,10),
    rho DECIMAL(20,10),
    first_ingested_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    ingestion_run_id VARCHAR NOT NULL,
    PRIMARY KEY (provider, underlying, feed, contract_symbol, retrieved_at),
    CHECK (option_type IN ('call', 'put')),
    CHECK (feed IN ('opra', 'indicative'))
);
