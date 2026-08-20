"""Read-only Alpaca market-data connector.

This module talks only to Alpaca's market-data API
(``https://data.alpaca.markets``). It must never import, implement, or
reference brokerage order, account, or execution functionality, and must
never connect to Robinhood. Credential values are read from ``Settings``
and used only in request headers — they are never printed, logged, or
included in exception messages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from market_intelligence.config.settings import Settings

MARKET_DATA_BASE_URL = "https://data.alpaca.markets"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

_SNAPSHOT_TIMESTAMP_FIELDS = ("latestTrade", "latestQuote", "minuteBar", "dailyBar")

MAX_SYMBOL_LENGTH = 10
_SYMBOL_PATTERN = re.compile(rf"^[A-Z0-9][A-Z0-9.\-]{{0,{MAX_SYMBOL_LENGTH - 1}}}$")


class AlpacaCredentialsMissingError(RuntimeError):
    """Raised when an Alpaca request is attempted without configured credentials."""


class AlpacaMarketDataError(RuntimeError):
    """Raised for a sanitized Alpaca market-data request failure.

    The message never includes request headers, credential values, or the
    raw response body — only a status code or exception type.
    """


class AlpacaInvalidSymbolError(AlpacaMarketDataError):
    """Raised when a symbol fails normalization/validation before any request is made.

    Validation happens before any HTTP request is constructed, so an
    invalid symbol never reaches the network. The message never echoes the
    raw, unvalidated input.
    """


def normalize_symbol(symbol: str) -> str:
    """Normalize and validate a ticker symbol for use in an Alpaca request.

    Trims surrounding whitespace and uppercases the result. Raises
    ``AlpacaInvalidSymbolError`` for anything other than a conservative
    U.S.-ticker-style token: 1-10 characters, starting with a letter or
    digit, containing only letters, digits, ``.``, and ``-``. This rejects
    empty/whitespace-only input, embedded whitespace, path separators,
    query-string/URL-shaped input, and control characters.
    """
    if not isinstance(symbol, str):
        raise AlpacaInvalidSymbolError("Invalid symbol: expected a string.")

    normalized = symbol.strip().upper()

    if not _SYMBOL_PATTERN.match(normalized):
        raise AlpacaInvalidSymbolError(
            "Invalid symbol: must be 1-10 characters, start with a letter or "
            "digit, and contain only letters, digits, '.', and '-'."
        )

    return normalized


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

        Raises ``AlpacaInvalidSymbolError`` (before any request is made) if
        the symbol fails normalization/validation,
        ``AlpacaCredentialsMissingError`` if credentials are not configured,
        or ``AlpacaMarketDataError`` (sanitized) on request failure,
        malformed JSON, or a non-object JSON payload.
        """
        normalized_symbol = normalize_symbol(symbol)
        headers = self._auth_headers()
        owns_client = client is None
        http_client = client or httpx.Client(base_url=MARKET_DATA_BASE_URL, timeout=self._timeout)
        try:
            response = http_client.get(f"/v2/stocks/{normalized_symbol}/snapshot", headers=headers)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise AlpacaMarketDataError(
                f"Alpaca snapshot request failed with status {exc.response.status_code}."
            ) from None
        except httpx.RequestError as exc:
            raise AlpacaMarketDataError(
                f"Alpaca snapshot request failed: {type(exc).__name__}."
            ) from None
        except ValueError:
            raise AlpacaMarketDataError("Alpaca snapshot response was not valid JSON.") from None
        finally:
            if owns_client:
                http_client.close()

        if not isinstance(payload, dict):
            raise AlpacaMarketDataError("Alpaca snapshot response payload was not a JSON object.")

        return payload

    def check_connection(
        self, symbol: str = "SPY", *, client: httpx.Client | None = None
    ) -> ConnectionStatus:
        """Perform one read-only connection check against the snapshot endpoint.

        Returns a sanitized ``ConnectionStatus``; never raises for a failed
        request, missing credentials, or an invalid symbol. An invalid
        symbol is rejected before any request is made and never echoed back
        in the returned status.
        """
        try:
            normalized_symbol = normalize_symbol(symbol)
        except AlpacaInvalidSymbolError:
            return ConnectionStatus(
                configured=self.is_configured(),
                success=False,
                status_category="invalid_symbol",
                symbol="",
                timestamp=None,
            )

        if not self.is_configured():
            return ConnectionStatus(
                configured=False,
                success=False,
                status_category="not_configured",
                symbol=normalized_symbol,
                timestamp=None,
            )

        headers = self._auth_headers()
        owns_client = client is None
        http_client = client or httpx.Client(base_url=MARKET_DATA_BASE_URL, timeout=self._timeout)
        try:
            try:
                response = http_client.get(
                    f"/v2/stocks/{normalized_symbol}/snapshot", headers=headers
                )
            except httpx.RequestError:
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="network_error",
                    symbol=normalized_symbol,
                    timestamp=None,
                )

            if not response.is_success:
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category=_status_category(response.status_code),
                    symbol=normalized_symbol,
                    timestamp=None,
                )

            try:
                payload = response.json()
            except ValueError:
                payload = None

            if not isinstance(payload, dict):
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    symbol=normalized_symbol,
                    timestamp=None,
                )

            return ConnectionStatus(
                configured=True,
                success=True,
                status_category=_status_category(response.status_code),
                symbol=normalized_symbol,
                timestamp=_extract_timestamp(payload),
            )
        finally:
            if owns_client:
                http_client.close()
