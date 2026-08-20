"""Read-only Alpaca market-data connector.

This module talks only to Alpaca's market-data API
(``https://data.alpaca.markets``). It must never import, implement, or
reference brokerage order, account, or execution functionality, and must
never connect to Robinhood. Credential values are read from ``Settings``
and used only in request headers — they are never printed, logged, or
included in exception messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from market_intelligence.config.settings import Settings

MARKET_DATA_BASE_URL = "https://data.alpaca.markets"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

_SNAPSHOT_TIMESTAMP_FIELDS = ("latestTrade", "latestQuote", "minuteBar", "dailyBar")


class AlpacaCredentialsMissingError(RuntimeError):
    """Raised when an Alpaca request is attempted without configured credentials."""


class AlpacaMarketDataError(RuntimeError):
    """Raised for a sanitized Alpaca market-data request failure.

    The message never includes request headers, credential values, or the
    raw response body — only a status code or exception type.
    """


@dataclass(frozen=True)
class ConnectionStatus:
    """Sanitized result of a single read-only connection check."""

    configured: bool
    success: bool
    status_category: str
    symbol: str
    timestamp: str | None


def _status_category(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _extract_timestamp(payload: dict[str, Any]) -> str | None:
    """Pull a market/response timestamp out of a snapshot payload, if present."""
    for field in _SNAPSHOT_TIMESTAMP_FIELDS:
        entry = payload.get(field)
        if isinstance(entry, dict):
            timestamp = entry.get("t")
            if isinstance(timestamp, str) and timestamp:
                return timestamp
    return None


class AlpacaMarketDataClient:
    """Minimal read-only client for Alpaca's market-data API.

    Market data only: this client has no methods for placing, modifying, or
    cancelling orders, and none touch account/trading endpoints.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings or Settings()
        self._timeout = timeout

    def is_configured(self) -> bool:
        return self._settings.provider_status()["alpaca"]

    def _auth_headers(self) -> dict[str, str]:
        if not self.is_configured():
            raise AlpacaCredentialsMissingError("Alpaca credentials are not configured.")
        assert self._settings.alpaca_api_key is not None
        assert self._settings.alpaca_api_secret is not None
        return {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.alpaca_api_secret.get_secret_value(),
        }

    def get_snapshot(
        self, symbol: str, *, client: httpx.Client | None = None
    ) -> dict[str, Any]:
        """Fetch a single-symbol market-data snapshot. Read-only.

        Raises ``AlpacaCredentialsMissingError`` if credentials are not
        configured, or ``AlpacaMarketDataError`` (sanitized) on request
        failure.
        """
        headers = self._auth_headers()
        owns_client = client is None
        http_client = client or httpx.Client(base_url=MARKET_DATA_BASE_URL, timeout=self._timeout)
        try:
            response = http_client.get(f"/v2/stocks/{symbol}/snapshot", headers=headers)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise AlpacaMarketDataError(
                f"Alpaca snapshot request failed with status {exc.response.status_code}."
            ) from None
        except httpx.RequestError as exc:
            raise AlpacaMarketDataError(
                f"Alpaca snapshot request failed: {type(exc).__name__}."
            ) from None
        finally:
            if owns_client:
                http_client.close()

    def check_connection(
        self, symbol: str = "SPY", *, client: httpx.Client | None = None
    ) -> ConnectionStatus:
        """Perform one read-only connection check against the snapshot endpoint.

        Returns a sanitized ``ConnectionStatus``; never raises for a failed
        request or missing credentials.
        """
        if not self.is_configured():
            return ConnectionStatus(
                configured=False,
                success=False,
                status_category="not_configured",
                symbol=symbol,
                timestamp=None,
            )

        headers = self._auth_headers()
        owns_client = client is None
        http_client = client or httpx.Client(base_url=MARKET_DATA_BASE_URL, timeout=self._timeout)
        try:
            try:
                response = http_client.get(f"/v2/stocks/{symbol}/snapshot", headers=headers)
            except httpx.RequestError:
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="network_error",
                    symbol=symbol,
                    timestamp=None,
                )

            success = response.is_success
            timestamp = None
            if success:
                try:
                    timestamp = _extract_timestamp(response.json())
                except ValueError:
                    timestamp = None
            return ConnectionStatus(
                configured=True,
                success=success,
                status_category=_status_category(response.status_code),
                symbol=symbol,
                timestamp=timestamp,
            )
        finally:
            if owns_client:
                http_client.close()
