"""Read-only health check for the local DuckDB storage foundation.

Verifies the database file exists and that the required infrastructure
tables (``schema_migrations``, ``ingestion_runs``) are present, without
writing anything. Prints only sanitized status information — never
credentials.
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
    print(f"healthy: {health.healthy}")

    return 0 if health.healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
