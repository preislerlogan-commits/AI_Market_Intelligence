"""Read-only Alpaca historical stock-bars connection check.

Performs at most one live, read-only, bounded bars request (a single-symbol,
small historical window, conservative-limit query, no pagination) and
prints only sanitized status information:

- configured (True/False)
- connection success (True/False)
- HTTP/status category
- requested symbol
- requested timeframe
- bar count
- oldest bar timestamp
- newest bar timestamp

It never prints OHLCV values, headers, keys, secrets, raw payloads, or page
tokens. It performs no storage or database writes. This script is not run
live as part of implementing this connector; it requires separate,
explicit authorization before use.
"""

from __future__ import annotations

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import AlpacaBarsClient


def main(symbol: str = "SPY", timeframe: str = "5Min") -> int:
    settings = Settings()
    client = AlpacaBarsClient(settings=settings)
    status = client.check_connection(symbol, timeframe=timeframe)

    print(f"configured: {status.configured}")
    print(f"connection success: {status.success}")
    print(f"status category: {status.status_category}")
    print(f"symbol: {status.symbol}")
    print(f"timeframe: {status.timeframe}")
    print(f"bar count: {status.bar_count}")
    print(f"oldest bar timestamp: {status.oldest_bar_timestamp}")
    print(f"newest bar timestamp: {status.newest_bar_timestamp}")

    return 0 if status.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
