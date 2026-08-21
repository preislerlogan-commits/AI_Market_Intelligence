-- Records metadata about each attempted data-ingestion run: which
-- provider/dataset, when it started/completed (UTC), its outcome status,
-- how many records were received, and a sanitized error category. This
-- table stores run metadata only — never raw provider data, and never a
-- raw exception message that might embed a credential.
CREATE TABLE ingestion_runs (
    run_id VARCHAR PRIMARY KEY,
    provider VARCHAR NOT NULL,
    dataset_name VARCHAR NOT NULL,
    started_at_utc TIMESTAMP NOT NULL,
    completed_at_utc TIMESTAMP,
    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    records_received BIGINT,
    error_category VARCHAR,
    code_version VARCHAR NOT NULL
);
