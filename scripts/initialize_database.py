"""Initialize the local DuckDB storage foundation.

Applies any pending versioned migrations (see
``market_intelligence/storage/migrations/``) to the configured local
database and prints only the database path, schema version, and applied
migration count. Never prints credentials — this script does not touch any
credential-bearing connector.
"""

from __future__ import annotations

from market_intelligence.storage.database import DuckDBManager


def main() -> int:
    manager = DuckDBManager()
    result = manager.initialize()

    print(f"database path: {result.database_path}")
    print(f"schema version: {result.schema_version}")
    print(f"applied migration count: {result.applied_migration_count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
