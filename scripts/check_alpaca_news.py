"""Read-only Alpaca news connection check.

Performs at most one live, read-only news request (a single-symbol SPY
news query) and prints only sanitized status information:

- configured (True/False)
- connection success (True/False)
- HTTP/status category
- requested symbol
- article count
- newest publication timestamp, when safely available

It never prints headlines, URLs, summaries, raw payloads, or credentials.
"""

from __future__ import annotations

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_news import AlpacaNewsClient


def main(symbol: str = "SPY") -> int:
    settings = Settings()
    client = AlpacaNewsClient(settings=settings)
    status = client.check_connection(symbol)

    print(f"configured: {status.configured}")
    print(f"connection success: {status.success}")
    print(f"status category: {status.status_category}")
    print(f"requested symbol: {status.symbol}")
    print(f"article count: {status.article_count}")
    print(f"newest publication timestamp: {status.newest_publication_timestamp}")

    return 0 if status.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
