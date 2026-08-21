"""Manual, one-shot Alpaca news ingestion into the local news_articles table.

Makes at most one explicit, read-only Alpaca news request (via
``AlpacaNewsClient.get_news``) for a single supplied symbol, then stores the
normalized results through ``NewsArticleRepository``. This script performs
no sentiment, prediction, or trading logic -- storage only.

The command-line ``--symbol`` and ``--limit`` are strictly validated before
any network request is constructed: an invalid ``--limit`` is rejected by
argparse itself (before ``Settings``/the news client are ever created), and
an invalid ``--symbol`` is rejected by the same normalization the connector
uses internally, with zero HTTP requests made in either case.

Only sanitized metadata is ever printed: configured, fetch outcome, the
requested symbol, and received/inserted/existing-or-updated/failed counts
plus the ingestion-run status. Never headlines, summaries, URLs, raw
responses, database rows, headers, keys, or credentials.

This script is not run as part of implementing this storage layer -- live
ingestion requires separate, explicit authorization.
"""

from __future__ import annotations

import argparse

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_news import (
    AlpacaNewsClient,
    AlpacaNewsCredentialsMissingError,
    AlpacaNewsError,
    AlpacaNewsInvalidInputError,
    normalize_limit,
    normalize_symbols,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.news_repository import NewsArticleRepository

DEFAULT_SYMBOL = "SPY"
DEFAULT_LIMIT = 10


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-shot read-only Alpaca news ingestion.")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run one ingestion pass. ``settings`` is an injection point for tests only.

    Real usage never passes ``settings`` -- it always defaults to a fresh
    ``Settings()`` reading the process environment/local ``.env``.
    """
    args = _parse_args(argv)

    # Validate before constructing Settings/any client, so malformed input
    # never reaches the network.
    try:
        symbol = ",".join(normalize_symbols(args.symbol))
        limit = normalize_limit(args.limit)
    except AlpacaNewsInvalidInputError as exc:
        print("configured: unknown")
        print("fetch outcome: invalid_input")
        print("symbol: (invalid)")
        print(f"error: {exc}")
        return 2

    settings = settings or Settings()
    client = AlpacaNewsClient(settings=settings)
    configured = client.is_configured()
    print(f"configured: {configured}")

    if not configured:
        print("fetch outcome: not_configured")
        print(f"symbol: {symbol}")
        return 1

    try:
        items = client.get_news(symbol, limit=limit)
    except AlpacaNewsCredentialsMissingError:
        print("fetch outcome: not_configured")
        print(f"symbol: {symbol}")
        return 1
    except AlpacaNewsError as exc:
        print("fetch outcome: failed")
        print(f"symbol: {symbol}")
        print(f"error category: {type(exc).__name__}")
        return 1

    print("fetch outcome: success")
    print(f"symbol: {symbol}")
    print(f"received count: {len(items)}")

    DuckDBManager(settings=settings).initialize()
    repository = NewsArticleRepository(settings=settings)
    result = repository.store_news_items(items, provider="alpaca")

    print(f"inserted count: {result.inserted}")
    print(f"existing/updated count: {result.updated}")
    print(f"failed count: {result.failed}")
    print(f"ingestion run status: {result.ingestion_run_status}")

    return 0 if result.ingestion_run_status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
