"""Read-only FRED macroeconomic-data connector.

This module talks only to the official FRED API
(``https://api.stlouisfed.org``). It is read-only: it only fetches
published economic-data series observations and never modifies remote
state. FRED requires the API key as a query parameter (not a header), so
every exception and status result produced here is built only from
sanitized components — status codes, exception type names, and
FRED-reported error categories — and never from a raw exception message,
request, or response object, any of which could embed the key or the full
query string.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import httpx

from market_intelligence.config.settings import Settings

FRED_BASE_URL = "https://api.stlouisfed.org"
OBSERVATIONS_PATH = "/fred/series/observations"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

MAX_SERIES_ID_LENGTH = 64
_SERIES_ID_PATTERN = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9_]{{0,{MAX_SERIES_ID_LENGTH - 1}}}$")


class FredCredentialsMissingError(RuntimeError):
    """Raised when a FRED request is attempted without a configured API key."""


class FredMacroDataError(RuntimeError):
    """Raised for a sanitized FRED request failure.

    The message never includes the API key, the request URL/query string,
    or the raw response body — only a status code, exception type, or a
    generic FRED-error category.
    """


class FredInvalidSeriesIdError(FredMacroDataError):
    """Raised when a series ID fails normalization/validation before any request is made.

    Validation happens before any HTTP request is constructed, so an
    invalid series ID never reaches the network. The message never echoes
    the raw, unvalidated input.
    """


def normalize_series_id(series_id: str) -> str:
    """Normalize and validate a FRED series ID for use in a request.

    Trims surrounding whitespace and uppercases the result. Raises
    ``FredInvalidSeriesIdError`` for anything other than a conservative
    token: 1-64 characters, starting with a letter or digit, containing
    only letters, digits, and underscores. This rejects empty/whitespace-only
    input, embedded whitespace, path separators, query-string/URL-shaped
    input, and control characters.
    """
    if not isinstance(series_id, str):
        raise FredInvalidSeriesIdError("Invalid series ID: expected a string.")

    normalized = series_id.strip().upper()

    if not _SERIES_ID_PATTERN.match(normalized):
        raise FredInvalidSeriesIdError(
            "Invalid series ID: must be 1-64 characters, start with a letter or "
            "digit, and contain only letters, digits, and underscores."
        )

    return normalized


@dataclass(frozen=True)
class ConnectionStatus:
    """Sanitized result of a single read-only connection check.

    Deliberately excludes the observation value — only the observation
    *date* is ever surfaced here.
    """

    configured: bool
    success: bool
    status_category: str
    series_id: str
    latest_observation_date: str | None


def _status_category(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _is_fred_error_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and ("error_code" in payload or "error_message" in payload)


def _extract_latest_observation_date(payload: dict[str, Any]) -> str | None:
    """Pull the latest observation's date out of an observations payload, if present.

    Only the observation ``date`` is ever extracted — the observation
    ``value`` itself is never read or returned by this function, per this
    connector's sanitized-output contract.
    """
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        return None

    latest = observations[0]
    if not isinstance(latest, dict):
        return None

    date = latest.get("date")
    if not isinstance(date, str) or not date:
        return None

    return date


def _build_params(series_id: str, api_key: str) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 1,
    }


class FredMacroDataClient:
    """Minimal read-only client for FRED's economic-data API.

    Read-only: this client only fetches published series/observations data;
    it has no methods that create, modify, or delete anything.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._settings = settings or Settings()
        self._timeout = timeout

    def is_configured(self) -> bool:
        return self._settings.provider_status()["fred"]

    def _api_key(self) -> str:
        if not self.is_configured():
            raise FredCredentialsMissingError("FRED API key is not configured.")
        assert self._settings.fred_api_key is not None
        return self._settings.fred_api_key.get_secret_value()

    def get_latest_observation(
        self, series_id: str, *, client: httpx.Client | None = None
    ) -> dict[str, Any]:
        """Fetch the latest observation payload for a FRED series. Read-only.

        Raises ``FredInvalidSeriesIdError`` (before any request is made) if
        the series ID fails normalization/validation,
        ``FredCredentialsMissingError`` if no API key is configured, or
        ``FredMacroDataError`` (sanitized) on request/network failure,
        malformed JSON, a non-object JSON payload, a FRED-reported error
        payload, or a missing/malformed observation.
        """
        normalized_series_id = normalize_series_id(series_id)
        api_key = self._api_key()
        params = _build_params(normalized_series_id, api_key)
        owns_client = client is None
        http_client = client or httpx.Client(base_url=FRED_BASE_URL, timeout=self._timeout)
        try:
            try:
                response = http_client.get(OBSERVATIONS_PATH, params=params)
            except httpx.RequestError as exc:
                raise FredMacroDataError(
                    f"FRED observations request failed: {type(exc).__name__}."
                ) from None

            try:
                payload = response.json()
            except ValueError:
                payload = None

            if _is_fred_error_payload(payload):
                raise FredMacroDataError("FRED reported an API error for this request.")

            if not response.is_success:
                raise FredMacroDataError(
                    f"FRED observations request failed with status {response.status_code}."
                )

            if not isinstance(payload, dict):
                raise FredMacroDataError(
                    "FRED observations response payload was not a JSON object."
                )
        finally:
            if owns_client:
                http_client.close()

        if _extract_latest_observation_date(payload) is None:
            raise FredMacroDataError(
                "FRED observations response did not include a usable observation."
            )

        return payload

    def check_connection(
        self, series_id: str = "FEDFUNDS", *, client: httpx.Client | None = None
    ) -> ConnectionStatus:
        """Perform one read-only connection check against the observations endpoint.

        Returns a sanitized ``ConnectionStatus``; never raises for a failed
        request, missing credentials, or an invalid series ID. An invalid
        series ID is rejected before any request is made and never echoed
        back in the returned status. The observation *value* is never
        included in the returned status — only the observation *date*, when
        safely available.
        """
        try:
            normalized_series_id = normalize_series_id(series_id)
        except FredInvalidSeriesIdError:
            return ConnectionStatus(
                configured=self.is_configured(),
                success=False,
                status_category="invalid_series_id",
                series_id="",
                latest_observation_date=None,
            )

        if not self.is_configured():
            return ConnectionStatus(
                configured=False,
                success=False,
                status_category="not_configured",
                series_id=normalized_series_id,
                latest_observation_date=None,
            )

        params = _build_params(normalized_series_id, self._api_key())
        owns_client = client is None
        http_client = client or httpx.Client(base_url=FRED_BASE_URL, timeout=self._timeout)
        try:
            try:
                response = http_client.get(OBSERVATIONS_PATH, params=params)
            except httpx.RequestError:
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="network_error",
                    series_id=normalized_series_id,
                    latest_observation_date=None,
                )

            try:
                payload = response.json()
            except ValueError:
                payload = None

            if _is_fred_error_payload(payload):
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="fred_error",
                    series_id=normalized_series_id,
                    latest_observation_date=None,
                )

            if not response.is_success:
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category=_status_category(response.status_code),
                    series_id=normalized_series_id,
                    latest_observation_date=None,
                )

            if not isinstance(payload, dict):
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    series_id=normalized_series_id,
                    latest_observation_date=None,
                )

            latest_observation_date = _extract_latest_observation_date(payload)
            if latest_observation_date is None:
                return ConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="missing_observation",
                    series_id=normalized_series_id,
                    latest_observation_date=None,
                )

            return ConnectionStatus(
                configured=True,
                success=True,
                status_category=_status_category(response.status_code),
                series_id=normalized_series_id,
                latest_observation_date=latest_observation_date,
            )
        finally:
            if owns_client:
                http_client.close()
