-- Tracks which versioned migrations have been applied to this database,
-- including a checksum of each migration file so a change to an
-- already-applied migration's content is detected rather than silently
-- ignored. This table is created by the migration runner itself (it is
-- migration 0001, applied like any other), which is why the runner must
-- tolerate this specific table not existing yet on a brand-new database.
CREATE TABLE schema_migrations (
    version VARCHAR PRIMARY KEY,
    filename VARCHAR NOT NULL,
    checksum VARCHAR NOT NULL,
    applied_at_utc TIMESTAMP NOT NULL
);
