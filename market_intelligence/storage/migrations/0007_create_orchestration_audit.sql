-- Persistent orchestration audit trail: tracks each orchestration run (one
-- invocation of scripts/run_ingestion_pipeline.py --execute) and each
-- selected job within it, independent of ingestion_runs (which already
-- records dataset-level storage bookkeeping per repository call). This
-- exists specifically so a run that fails or crashes before any
-- ingestion_runs row can even exist (e.g. a lock-acquisition failure, or a
-- crash between two jobs) still leaves a truthful, durable audit record --
-- never falsely left or reported as running/succeeded.
--
-- Stores only orchestration run/job identifiers, provider/dataset labels,
-- timing, status, a sanitized error category, and sanitized counts --
-- never credentials, URLs/query parameters, raw responses/errors,
-- headlines/summaries, OHLCV values, macro values, prompts/model outputs,
-- recommendations, or order/execution data. Dry runs are never persisted
-- here at all (see market_intelligence/orchestration/runner.py and
-- docs/INGESTION_ORCHESTRATION.md): only an --execute run ever writes a
-- row to either table.
--
-- orchestration_job_runs.orchestration_job_run_id is deliberately
-- "<orchestration_run_id>:<job_id>" rather than a fresh UUID: a job_id is
-- unique within one run (job selection has no duplicates), so this gives a
-- stable, idempotent identity for "this job, in this run" without a
-- separate lookup, and doubles as the natural key an operator would search
-- for.
--
-- Neither table declares a DuckDB foreign key to ingestion_runs, to each
-- other, or to itself, for the same reason as news_articles.ingestion_run_id
-- (migration 0004): a hard FK would block otherwise-legitimate future
-- schema evolution of any of these tables, since DuckDB refuses to ALTER a
-- table that a FK depends on. References are enforced at the application
-- layer (market_intelligence/storage/orchestration_audit_repository.py)
-- instead.
CREATE TABLE orchestration_runs (
    orchestration_run_id VARCHAR PRIMARY KEY,
    started_at_utc TIMESTAMP NOT NULL,
    completed_at_utc TIMESTAMP,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    code_version VARCHAR NOT NULL,
    schema_version VARCHAR
);

CREATE TABLE orchestration_job_runs (
    orchestration_job_run_id VARCHAR PRIMARY KEY,
    orchestration_run_id VARCHAR NOT NULL,
    job_id VARCHAR NOT NULL,
    job_type VARCHAR NOT NULL
        CHECK (job_type IN ('alpaca_news', 'alpaca_bars', 'fred_observations')),
    provider VARCHAR NOT NULL,
    dataset_name VARCHAR NOT NULL,
    started_at_utc TIMESTAMP,
    completed_at_utc TIMESTAMP,
    status VARCHAR NOT NULL
        CHECK (status IN ('planned', 'running', 'succeeded', 'failed', 'skipped')),
    error_category VARCHAR,
    records_received BIGINT,
    records_inserted BIGINT,
    records_existing BIGINT,
    records_failed BIGINT,
    ingestion_run_id VARCHAR,
    code_version VARCHAR NOT NULL,
    schema_version VARCHAR
);
