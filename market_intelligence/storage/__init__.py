"""Local DuckDB storage foundation for AI Market Intelligence.

This package provides only the infrastructure layer: a versioned,
checksum-verified migration runner (``market_intelligence.storage.database``)
and the SQL migrations under ``market_intelligence/storage/migrations/``.
The initial schema defines only infrastructure metadata tables
(``schema_migrations``, ``ingestion_runs``) — no market-bar, macro,
news, forecast, or trade tables exist yet. Those require separate,
reviewed data contracts. See docs/STORAGE_ARCHITECTURE.md.
"""
