"""Read-only FRED macroeconomic-data connection check.

Performs at most one live, read-only request (a single-series latest
observation lookup) and prints only sanitized status information:

- configured (True/False)
- connection success (True/False)
- status category
- requested series ID
- latest observation date, when safely available

It never prints the API key, the complete request URL, query parameters,
the raw response, or the observation value.
"""

from __future__ import annotations

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import FredMacroDataClient


def main(series_id: str = "FEDFUNDS") -> int:
    settings = Settings()
    client = FredMacroDataClient(settings=settings)
    status = client.check_connection(series_id)

    print(f"configured: {status.configured}")
    print(f"connection success: {status.success}")
    print(f"status category: {status.status_category}")
    print(f"series ID: {status.series_id}")
    print(f"latest observation date: {status.latest_observation_date}")

    return 0 if status.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
