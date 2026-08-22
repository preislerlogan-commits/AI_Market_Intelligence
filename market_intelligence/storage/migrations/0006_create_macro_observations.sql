-- Stores normalized historical macroeconomic observations returned by a
-- read-only FRED connector (currently only FredMacroDataClient; see
-- market_intelligence/data_connectors/fred_macro_data.py). This table
-- stores only FRED's own reviewed observation fields plus this project's
-- own provenance/ingestion bookkeeping -- never prediction, direction,
-- sentiment, impact, recommendation, option-contract, order, execution, or
-- other derived-feature columns.
--
-- Identity: unlike a naive (series_id, observation_date) key, this table's
-- identity also includes realtime_start/realtime_end -- FRED's own
-- revision/vintage window for an observation. FRED routinely revises
-- published values for the same series_id/observation_date (e.g. GDP
-- revisions), and each revision is reported under a different
-- realtime_start/realtime_end window; collapsing on (series_id,
-- observation_date) alone would silently discard that revision history by
-- overwriting an earlier vintage's value with a later one. The primary key
-- is therefore (provider, series_id, observation_date, realtime_start,
-- realtime_end), so re-ingesting the same reviewed vintage of an
-- observation never creates a duplicate row, while a genuinely later
-- revision is stored as its own row.
--
-- Observation time is kept strictly separate from retrieval and ingestion
-- bookkeeping time, mirroring news_articles (migration 0004) and
-- market_bars (migration 0005):
--   * observation_date   -- the calendar date the observation itself
--     describes, as reported by FRED.
--   * realtime_start / realtime_end -- FRED's reported revision/vintage
--     window for this specific observation value (part of the identity,
--     see above).
--   * retrieved_at       -- when the connector fetched the specific API
--     response that produced the currently-stored values (refreshed on
--     every re-ingestion of an already-known observation).
--   * first_ingested_at  -- when this observation (this exact vintage) was
--     first stored in this database; never changed after the initial
--     insert.
--   * last_seen_at       -- when this observation was most recently
--     observed by any ingestion run; refreshed on every re-ingestion.
--
-- Numeric type: value uses DECIMAL(20,6) rather than DOUBLE so values are
-- stored exactly as decimal quantities (matching the connector's use of
-- Python Decimal, built directly from FRED's string representation to
-- avoid baking in IEEE-754 binary-float rounding artifacts -- see
-- fred_macro_data.py). 6 fractional digits comfortably covers FRED's
-- typically-reported precision; 14 integer digits leaves ample headroom
-- for any plausible macro series magnitude (e.g. nominal GDP in dollars).
-- Precision/scale are additionally validated on the repository side before
-- any write, so an out-of-range or over-precise value is rejected before
-- it ever reaches this column, rather than being silently rounded or
-- overflowed by DuckDB.
--
-- Missing/value consistency: FRED represents a missing observation with
-- the literal string "." rather than omitting the field. This table
-- preserves that as value = NULL, is_missing = TRUE; a present observation
-- always has value NOT NULL and is_missing = FALSE. The CHECK constraint
-- enforces this pairing is never violated at the database level, mirroring
-- the equivalent validation performed by the connector and repository.
--
-- ingestion_run_id records the ingestion_runs.run_id for the run that most
-- recently wrote this observation's stored values, tying every stored
-- observation back to an auditable ingestion run. Deliberately not declared
-- as a DuckDB foreign key, for the same reason as news_articles.
-- ingestion_run_id (migration 0004) and market_bars.ingestion_run_id
-- (migration 0005): a hard FK on ingestion_runs(run_id) would block
-- otherwise-legitimate future schema evolution of ingestion_runs, since
-- DuckDB refuses to ALTER a table that a FK depends on. This reference is
-- enforced at the application layer
-- (market_intelligence/storage/macro_observation_repository.py) instead.
CREATE TABLE macro_observations (
    provider VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    observation_date DATE NOT NULL,
    realtime_start DATE NOT NULL,
    realtime_end DATE NOT NULL,
    value DECIMAL(20,6),
    is_missing BOOLEAN NOT NULL,
    retrieved_at TIMESTAMP NOT NULL,
    first_ingested_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    ingestion_run_id VARCHAR NOT NULL,
    PRIMARY KEY (provider, series_id, observation_date, realtime_start, realtime_end),
    CHECK ((is_missing AND value IS NULL) OR (NOT is_missing AND value IS NOT NULL))
);
