"""Read-only, derived-from-storage market features for AI Market Intelligence.

Every module in this package only reads data already stored in the local
DuckDB database (see ``market_intelligence/storage/``) -- none makes a
network request, writes to the database, or accepts credentials. See
``market_intelligence.market_features.market_context`` and
``docs/MARKET_CONTEXT_SNAPSHOT.md``.
"""
