"""Read-only Alpaca market-data connection check.

Performs at most one live, read-only market-data request (a single-symbol
snapshot) and prints only sanitized status information:

- configured (True/False)
- connection success (True/False)
- HTTP/status category
- requested symbol
- response/market timestamp, when safely available

It never prints headers, keys, secrets, or the complete raw response.
"""

from __future__ import annotations

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import AlpacaMarketDataClient


def main(symbol: str = "SPY") -> int:
    settings = Settings()
    client = AlpacaMarketDataClient(settings=settings)
    status = client.check_connection(symbol)

    print(f"configured: {status.configured}")
    print(f"connection success: {status.success}")
    print(f"status category: {status.status_category}")
    print(f"symbol: {status.symbol}")
    print(f"timestamp: {status.timestamp}")

    return 0 if status.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
