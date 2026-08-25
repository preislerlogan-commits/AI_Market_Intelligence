-- Stores normalized FRED series-level metadata (title, units, frequency,
-- seasonal adjustment, etc.) returned by a read-only FRED connector
-- (FredMacroDataClient.get_series_metadata(); see
-- market_intelligence/data_connectors/fred_macro_data.py). This is distinct
-- from macro_observations (migration 0006), which stores a series'
-- *values* -- this table stores the descriptive metadata that labels those
-- values, so a future Macro Analyst never interprets an unlabeled number.
--
-- This table stores only FRED's own reviewed series-metadata fields plus
-- this project's own provenance/ingestion bookkeeping -- never prediction,
-- direction, sentiment, impact, recommendation, option-contract, order,
-- execution, credentials, request headers, or raw API responses.
-- Provider-reported free text (title, notes) is preserved exactly as FRED
-- reported it -- never interpreted, summarized, or truncated.
--
-- Identity: (provider, series_id). Unlike macro_observations, series
-- metadata has no revision/vintage window of its own to preserve as part of
-- the identity -- FRED's series endpoint always reports the current
-- metadata for a series, and this table is not a historical record of every
-- past metadata state. A repeat ingestion of an already-known
-- (provider, series_id) always refreshes every mutable metadata/provenance
-- column in place (never rejected as a conflict), since a series' title,
-- units, popularity, or notes may legitimately change over time from
-- FRED's own perspective.
--
-- Provenance is kept strictly separate, mirroring macro_observations
-- (migration 0006):
--   * last_updated       -- FRED's own reported last-revision timestamp for
--     this series' metadata, normalized to UTC.
--   * retrieved_at_utc   -- when the connector fetched the specific API
--     response that produced the currently-stored values (refreshed on
--     every re-ingestion).
--   * first_ingested_at  -- when this (provider, series_id) was first
--     stored in this database; never changed after the initial insert.
--   * last_seen_at       -- when this series' metadata was most recently
--     observed by any ingestion run; refreshed on every re-ingestion.
--
-- ingestion_run_id records the ingestion_runs.run_id for the run that most
-- recently wrote this row, tying every stored row back to an auditable
-- ingestion run. Deliberately not declared as a DuckDB foreign key, for the
-- same reason as news_articles.ingestion_run_id (migration 0004),
-- market_bars.ingestion_run_id (migration 0005), and
-- macro_observations.ingestion_run_id (migration 0006): a hard FK on
-- ingestion_runs(run_id) would block otherwise-legitimate future schema
-- evolution of ingestion_runs, since DuckDB refuses to ALTER a table that a
-- FK depends on. This reference is enforced at the application layer
-- (market_intelligence/storage/macro_series_metadata_repository.py)
-- instead.
CREATE TABLE macro_series_metadata (
    provider VARCHAR NOT NULL,
    series_id VARCHAR NOT NULL,
    title VARCHAR NOT NULL,
    observation_start DATE NOT NULL,
    observation_end DATE NOT NULL,
    frequency VARCHAR NOT NULL,
    frequency_short VARCHAR NOT NULL,
    units VARCHAR NOT NULL,
    units_short VARCHAR NOT NULL,
    seasonal_adjustment VARCHAR NOT NULL,
    seasonal_adjustment_short VARCHAR NOT NULL,
    last_updated TIMESTAMP NOT NULL,
    popularity BIGINT NOT NULL,
    notes VARCHAR,
    retrieved_at_utc TIMESTAMP NOT NULL,
    first_ingested_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    ingestion_run_id VARCHAR NOT NULL,
    PRIMARY KEY (provider, series_id)
);
