-- Stores normalized SPY option-chain snapshot data returned by the read-only
-- Alpaca option-chain snapshot connector (AlpacaOptionsChainClient; see
-- market_intelligence/data_connectors/alpaca_options_chain.py), across three
-- tables:
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
--     option_chain_snapshots or option_chain_snapshot_batch_items rows. A
--     batch row is only ever inserted, never updated in place.
--   * option_chain_snapshots -- one row per *immutable* normalized contract
--     observation: the identity (provider, underlying, feed, contract_symbol,
--     retrieved_at) is a point-in-time fact that, once stored, is never
--     relinked or reassigned to a different batch. Request-level fields are
--     deliberately NOT duplicated here: they live once on whichever batch
--     row(s) reference this observation through option_chain_snapshot_batch_items.
--     `ingestion_run_id` on this table records only the run that *first*
--     inserted the row -- it is provenance about creation, not current
--     ownership, and it is never updated by a later re-observation. Which
--     batch(es) a given observation belongs to is answered exclusively by
--     option_chain_snapshot_batch_items, never by this column.
--   * option_chain_snapshot_batch_items -- the normalized batch-membership
--     table: exactly one row per (ingestion_run_id, snapshot identity) pair,
--     written for every contract returned by a successfully stored, non-empty
--     retrieval. This is what makes batch membership truthful even when the
--     same underlying observation is re-ingested: re-storing an identical
--     snapshot never mutates or relinks the option_chain_snapshots row, but it
--     does insert a *new* option_chain_snapshot_batch_items row for the new
--     batch, while the original batch's membership rows are left completely
--     untouched. A single immutable option_chain_snapshots row may therefore
--     be legitimately referenced by more than one batch's membership rows.
--     option_chain_snapshot_batches.contract_count for any non-empty
--     successful batch always equals the number of
--     option_chain_snapshot_batch_items rows carrying that batch's
--     ingestion_run_id -- this table is the source of truth for "what did
--     batch X actually contain," not any mutable column on the snapshot row.
--
-- None of the three tables stores a raw JSON payload, a directional forecast,
-- a contract ranking, a strategy recommendation, an order, an execution
-- field, credentials, request headers, request URLs/query parameters, page
-- tokens, or a provider response body.
--
-- SPY only: `underlying` is always 'SPY' in this milestone (the connector
-- rejects every other underlying); it is stored explicitly as provenance.
--
-- OPRA vs indicative are never merged. `feed` is part of the identity on both
-- option_chain_snapshots and option_chain_snapshot_batch_items: an 'opra'
-- observation and an 'indicative' observation of the same contract at the
-- same instant are genuinely different value series (the indicative feed may
-- be delayed or modified by the provider and must not be described as live
-- OPRA data) and are stored, and counted toward batch membership, as separate
-- rows. On option_chain_snapshot_batches the requested feed is recorded
-- verbatim as `requested_feed`, and every retrieval has its own ingestion-run
-- id, so batches for different feeds are always distinct rows.
--
-- Idempotency and immutability:
--   * option_chain_snapshot_batches keys on ingestion_run_id (one fresh id per
--     store call), so each stored retrieval is exactly one row. A batch row is
--     only ever inserted, never updated in place, and a failed or rolled-back
--     store leaves no batch row at all.
--   * option_chain_snapshots identity is
--     (provider, underlying, feed, contract_symbol, retrieved_at), where
--     retrieved_at is the single UTC instant the connector stamped on every
--     snapshot in one get_chain_snapshot call. Re-storing the same connector
--     result (same retrieved_at, same values) leaves the stored row's
--     ownership/provenance columns untouched -- at most refreshing
--     non-historical observation bookkeeping (`last_seen_at`); it is never
--     relinked to the new ingestion run. Re-storing the same identity with
--     conflicting values aborts the whole batch (see
--     market_intelligence/storage/option_chain_snapshot_repository.py),
--     rolling back any new batch row and any new
--     option_chain_snapshot_batch_items rows without touching the
--     already-stored snapshot row. A later, separate ingestion run produces a
--     new retrieved_at and is stored as a new observation row (a new batch
--     row and new batch-item rows), overwriting nothing.
--   * option_chain_snapshot_batch_items keys on
--     (ingestion_run_id, provider, underlying, feed, contract_symbol,
--     retrieved_at), so it is always a plain insert -- one fresh row per
--     contract per store call, never updated or deduplicated against a prior
--     batch's membership rows.
--
-- The batch row, every snapshot row, every batch-item row, and the final
-- ingestion_runs status update are written in one transaction: a validation
-- or storage failure leaves none of them (no batch row, no partial snapshot
-- rows, no partial batch-item rows). The ingestion_runs row is still recorded
-- (as 'failed' with a sanitized error category) so the attempt stays
-- auditable.
--
-- Time columns are kept strictly separate, mirroring the other data tables:
--   * quote_timestamp / trade_timestamp -- the provider-reported timestamps of
--     the latest quote and latest trade (UTC); nullable because a snapshot may
--     carry neither.
--   * retrieved_at       -- the UTC instant the connector fetched the response
--     (on the batch row, and copied onto each snapshot row and each
--     batch-item row as part of its identity).
--   * first_ingested_at  -- when this exact row was first stored; never
--     changed after the initial insert.
--   * last_seen_at       -- when this row was most recently re-observed by an
--     ingestion run; refreshed on every re-ingestion. option_chain_snapshots
--     is the only table where this can change after insert: it is
--     non-historical bookkeeping about observation freshness, never
--     ownership/provenance.
--
-- Nullability: on option_chain_snapshots, quote_timestamp, bid/ask price and
-- size, trade_timestamp, trade price and size, implied_volatility, and every
-- Greek are nullable. The snapshot endpoint legitimately omits any of them,
-- and a missing value is stored as NULL -- never as zero.
--
-- OPEN INTEREST IS NOT STORED. Alpaca's option-chain *snapshot* endpoint does
-- not supply open interest, so there is deliberately no open_interest column
-- on any of the three tables. It is documented as unavailable for this
-- milestone in DATA_CATALOG.md / docs/OPTIONS_DECISION_WORKFLOW.md and must
-- not be added as populated data by inference or default.
--
-- Numeric types: prices and strike use DECIMAL(18,6); implied_volatility and
-- the Greeks use DECIMAL(20,10) (Alpaca reports these at higher fractional
-- precision than cent-level prices). Both scales are validated on the
-- repository side before any write, so an over-precise or out-of-range value
-- is rejected rather than silently rounded/overflowed by DuckDB. Sizes are
-- BIGINT (whole contracts). contract_count is BIGINT and constrained
-- non-negative.
--
-- ingestion_run_id records the ingestion_runs.run_id of the run that wrote a
-- given row. Deliberately not a DuckDB foreign key, for the same reason as
-- news_articles.ingestion_run_id (migration 0004), market_bars.ingestion_run_id
-- (0005), macro_observations.ingestion_run_id (0006), and
-- macro_series_metadata.ingestion_run_id (0008): a hard FK on
-- ingestion_runs(run_id) would block otherwise-legitimate future schema
-- evolution of ingestion_runs. The reference is enforced at the application
-- layer instead. The batch-item -> batch reference (a shared ingestion_run_id)
-- and the batch-item -> snapshot reference (a shared snapshot identity) are
-- likewise application-enforced, not DuckDB foreign keys.
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

-- Normalized batch-membership table. One row per contract actually returned
-- by one successfully stored, non-empty retrieval -- the truthful record of
-- "which contracts were in batch X," independent of whether the underlying
-- option_chain_snapshots row was newly inserted or already existed from an
-- earlier batch. Deliberately carries only identity columns, never any
-- quote/trade/Greek value: the observation itself lives exactly once on
-- option_chain_snapshots, referenced here by its identity.
CREATE TABLE option_chain_snapshot_batch_items (
    ingestion_run_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    underlying VARCHAR NOT NULL,
    feed VARCHAR NOT NULL,
    contract_symbol VARCHAR NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    PRIMARY KEY (ingestion_run_id, provider, underlying, feed, contract_symbol, retrieved_at),
    CHECK (feed IN ('opra', 'indicative'))
);
