"""Read-only health check for the local DuckDB storage foundation.

Verifies the database file exists, that the required infrastructure
tables and columns (``schema_migrations``, ``ingestion_runs``) are present,
that the database's applied migration history can be reproduced from the
current migration directory (no missing files, no checksum mismatches, no
gaps or out-of-order versions), and that the database is at the latest
available migration — without writing anything. Prints only sanitized
status information — never credentials.
"""

from __future__ import annotations

from market_intelligence.storage.database import DuckDBManager


def main() -> int:
    manager = DuckDBManager()
    health = manager.check_health()

    print(f"database path: {health.database_path}")
    print(f"database file exists: {health.database_exists}")
    print(f"schema version: {health.schema_version}")
    print(f"applied migration count: {health.applied_migration_count}")
    print(f"required tables present: {health.required_tables_present}")
    print(f"required columns present: {health.required_columns_present}")
    print(f"migration history valid: {health.migration_history_valid}")
    print(f"checksums valid: {health.checksums_valid}")
    print(f"database at latest migration: {health.is_current}")
    print(f"healthy: {health.healthy}")

    return 0 if health.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
