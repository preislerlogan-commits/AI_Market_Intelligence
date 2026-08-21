-- Adds a `schema_version` column to `ingestion_runs`, distinct from the
-- existing `code_version` column: `code_version` records which version of
-- the ingestion code performed the run, while `schema_version` will record
-- which database schema version was active in this database at run time.
-- Added as a nullable column (DuckDB's ALTER TABLE ADD COLUMN does not
-- support attaching a NOT NULL constraint) so this migration applies
-- cleanly to an existing version-0002 `ingestion_runs` table regardless of
-- whether it is empty or already holds rows.
ALTER TABLE ingestion_runs ADD COLUMN schema_version VARCHAR;
