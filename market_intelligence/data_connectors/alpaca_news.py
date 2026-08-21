"""Read-only Alpaca news connector.

This module talks only to Alpaca's official read-only market-data host
(``https://data.alpaca.markets``) and only to its news endpoint. It must
never import, implement, or reference brokerage order, account, or
execution functionality, and must never connect to Robinhood. Credential
values are read from ``Settings`` and used only in request headers — they
are never printed, logged, or included in exception messages.

This connector does not infer sentiment, impact, or trade direction from
news content — it only fetches and normalizes provider-reported article
metadata. Publication time (``created_at``/``updated_at``, as reported by
Alpaca) is always kept separate from retrieval time (``retrieved_at``, set
locally in UTC when this connector processes the response).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)

NEWS_BASE_URL = "https://data.alpaca.markets"
NEWS_PATH = "/v1beta1/news"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

MAX_SYMBOLS = 20
MIN_LIMIT = 1
MAX_LIMIT = 50
MAX_TIMESTAMP_LENGTH = 40
_VALID_SORT_DIRECTIONS = ("asc", "desc")

# Strict RFC3339 date-time: a complete calendar date and time-of-day with an
# explicit "Z" or numeric UTC offset. Deliberately rejects date-only values,
# naive (offset-free) timestamps, and anything with trailing/embedded junk —
# the pattern is fully anchored, so nothing outside this shape can match.
_RFC3339_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])"
    r"[Tt](?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)(?:\.\d+)?"
    r"(?P<offset>[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


class AlpacaNewsCredentialsMissingError(RuntimeError):
    """Raised when a news request is attempted without configured credentials."""


class AlpacaNewsError(RuntimeError):
    """Raised for a sanitized Alpaca news request failure.

    The message never includes request headers, credential values, or the
    raw response body — only a status code or exception type.
    """


class AlpacaNewsInvalidInputError(AlpacaNewsError):
    """Raised when an input fails normalization/validation before any request is made.

    Validation happens before any HTTP request is constructed, so invalid
    or malicious input never reaches the network. The message never echoes
    raw, unvalidated input.
    """


def normalize_symbols(symbols: str | Iterable[str]) -> tuple[str, ...]:
    """Normalize and validate one or more ticker symbols for a news request.

    Accepts either a single comma-separated string or an iterable of
    strings. Each symbol is validated with the same conservative
    ticker-symbol rules used by the market-data connector. Raises
    ``AlpacaNewsInvalidInputError`` for anything other than a string or
    iterable of strings, for zero symbols, for more than ``MAX_SYMBOLS``
    distinct symbols, or if any individual symbol fails validation.
    Duplicate symbols are collapsed, preserving first-seen order.
    """
    if isinstance(symbols, str):
        raw_symbols: list[str] = symbols.split(",")
    elif isinstance(symbols, Iterable):
        raw_symbols = list(symbols)
    else:
        raise AlpacaNewsInvalidInputError(
            "Invalid symbols: expected a string or iterable of strings."
        )

    if not raw_symbols:
        raise AlpacaNewsInvalidInputError("Invalid symbols: at least one symbol is required.")

    normalized: list[str] = []
    for raw in raw_symbols:
        try:
            normalized_symbol = normalize_symbol(raw)
        except AlpacaInvalidSymbolError as exc:
            raise AlpacaNewsInvalidInputError(f"Invalid symbols: {exc}") from None
        if normalized_symbol not in normalized:
            normalized.append(normalized_symbol)

    if len(normalized) > MAX_SYMBOLS:
        raise AlpacaNewsInvalidInputError(
            f"Invalid symbols: at most {MAX_SYMBOLS} symbols are allowed."
        )

    return tuple(normalized)


def normalize_limit(limit: int) -> int:
    """Normalize and validate a result-count limit.

    Raises ``AlpacaNewsInvalidInputError`` unless ``limit`` is a plain
    ``int`` (booleans rejected) within ``[MIN_LIMIT, MAX_LIMIT]``.
    """
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AlpacaNewsInvalidInputError("Invalid limit: expected an integer.")
    if not (MIN_LIMIT <= limit <= MAX_LIMIT):
        raise AlpacaNewsInvalidInputError(
            f"Invalid limit: must be between {MIN_LIMIT} and {MAX_LIMIT}."
        )
    return limit


def normalize_sort(sort: str) -> str:
    """Normalize and validate a sort direction.

    Raises ``AlpacaNewsInvalidInputError`` unless ``sort`` normalizes to
    ``"asc"`` or ``"desc"``.
    """
    if not isinstance(sort, str):
        raise AlpacaNewsInvalidInputError("Invalid sort: expected a string.")
    normalized = sort.strip().lower()
    if normalized not in _VALID_SORT_DIRECTIONS:
        raise AlpacaNewsInvalidInputError("Invalid sort: must be 'asc' or 'desc'.")
    return normalized


def normalize_timestamp(value: Any, *, field_name: str) -> str:
    """Normalize and validate a required start/end RFC3339 timestamp.

    Raises ``AlpacaNewsInvalidInputError`` unless ``value`` is a string
    (booleans and other non-string types are rejected) that, once
    trimmed, is a strict, fully-specified RFC3339 date-time: a complete
    calendar date and time-of-day with an explicit "Z" or numeric UTC
    offset. Naive (offset-free) timestamps, date-only values, malformed
    timestamps, and embedded junk are all rejected before any request is
    built. The calendar date and UTC offset are validated for real
    values (not just shape), and the result is always returned as one
    consistent normalized representation: UTC with a "Z" suffix. The
    error message never echoes the untrusted input.
    """
    if not isinstance(value, str):
        raise AlpacaNewsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_TIMESTAMP_LENGTH:
        raise AlpacaNewsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    match = _RFC3339_PATTERN.match(trimmed)
    if match is None:
        raise AlpacaNewsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    offset = match.group("offset")
    candidate = trimmed[:-1] + "+00:00" if offset in ("Z", "z") else trimmed

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise AlpacaNewsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        ) from None

    if parsed.tzinfo is None:
        raise AlpacaNewsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_normalized_timestamp(value: str) -> datetime:
    """Parse a value already produced by ``normalize_timestamp`` back into a datetime.

    Only ever called on this connector's own normalized output (UTC, "Z"
    suffix), never on raw user input.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class NewsItem:
    """A normalized Alpaca news article.

    Deliberately contains only provider-reported metadata — no inferred
    sentiment, impact, or direction. ``created_at``/``updated_at`` are
    Alpaca's reported publication/update timestamps; ``retrieved_at`` is
    this connector's own retrieval timestamp (UTC, set when the response
    was processed) and is always distinct from them.
    """

    provider: str
    provider_article_id: str
    headline: str
    source: str
    url: str
    summary: str | None
    created_at: str | None
    updated_at: str | None
    related_symbols: tuple[str, ...]
    retrieved_at: str


@dataclass(frozen=True)
class NewsConnectionStatus:
    """Sanitized result of a single read-only news connection check."""

    configured: bool
    success: bool
    status_category: str
    symbol: str
    article_count: int
    newest_publication_timestamp: str | None


def _status_category(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _normalize_article(raw: Any, *, retrieved_at: str) -> NewsItem | None:
    """Normalize a single raw article, or return ``None`` if it is unusable.

    An article is skipped (never raises) if it is not an object, or if any
    of its required fields (id, headline, source, url) is missing or
    malformed. This lets a single bad entry in a response be dropped
    without discarding the rest of a valid response.
    """
    if not isinstance(raw, dict):
        return None

    article_id = raw.get("id")
    if isinstance(article_id, bool) or not isinstance(article_id, (int, str)):
        return None
    provider_article_id = str(article_id).strip()
    if not provider_article_id:
        return None

    headline = raw.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        return None

    source = raw.get("source")
    if not isinstance(source, str) or not source.strip():
        return None

    url = raw.get("url")
    if not isinstance(url, str) or not url.strip():
        return None

    related_symbols_raw = raw.get("symbols")
    related_symbols: tuple[str, ...] = ()
    if isinstance(related_symbols_raw, list):
        related_symbols = tuple(
            item.strip().upper()
            for item in related_symbols_raw
            if isinstance(item, str) and item.strip()
        )

    return NewsItem(
        provider="alpaca",
        provider_article_id=provider_article_id,
        headline=headline.strip(),
        source=source.strip(),
        url=url.strip(),
        summary=_optional_str(raw.get("summary")),
        created_at=_optional_str(raw.get("created_at")),
        updated_at=_optional_str(raw.get("updated_at")),
        related_symbols=related_symbols,
        retrieved_at=retrieved_at,
    )


def _normalize_articles(raw_articles: list[Any], *, retrieved_at: str) -> list[NewsItem]:
    """Normalize a list of raw articles, skipping unusable entries.

    Deduplicates by provider article ID, keeping the first occurrence.
    """
    seen: set[str] = set()
    items: list[NewsItem] = []
    for raw in raw_articles:
        item = _normalize_article(raw, retrieved_at=retrieved_at)
        if item is None or item.provider_article_id in seen:
            continue
        seen.add(item.provider_article_id)
        items.append(item)
    return items


def _newest_publication_timestamp(items: list[NewsItem]) -> str | None:
    timestamps = [item.created_at for item in items if item.created_at]
    return max(timestamps) if timestamps else None


def _build_params(
    symbols: tuple[str, ...],
    limit: int,
    sort: str,
    start: str | None,
    end: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "symbols": ",".join(symbols),
        "limit": limit,
        "sort": sort,
    }
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return params


class AlpacaNewsClient:
    """Minimal read-only client for Alpaca's news endpoint.

    News only: this client has no methods for placing, modifying, or
    cancelling orders, and none touch account/trading endpoints. It does
    not write to any database.
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
            raise AlpacaNewsCredentialsMissingError("Alpaca credentials are not configured.")
        assert self._settings.alpaca_api_key is not None
        assert self._settings.alpaca_api_secret is not None
        return {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.alpaca_api_secret.get_secret_value(),
        }

    def get_news(
        self,
        symbols: str | Iterable[str],
        *,
        limit: int = 10,
        sort: str = "desc",
        start: str | None = None,
        end: str | None = None,
        client: httpx.Client | None = None,
    ) -> list[NewsItem]:
        """Fetch recent ticker-linked news. Read-only.

        All inputs are strictly validated and normalized before any HTTP
        request is constructed, so malformed or malicious input never
        reaches the network. Raises ``AlpacaNewsInvalidInputError`` for
        invalid ``symbols``/``limit``/``sort``/``start``/``end``, or if
        both ``start`` and ``end`` are provided and ``start`` is after
        ``end`` (equal values are valid),
        ``AlpacaNewsCredentialsMissingError`` if credentials are not
        configured, or ``AlpacaNewsError`` (sanitized) on request failure,
        malformed JSON, an unusable response shape, or a non-empty
        provider news list in which every article was rejected as
        malformed (an empty provider news list is a valid, successful
        zero-article response).
        """
        normalized_symbols = normalize_symbols(symbols)
        normalized_limit = normalize_limit(limit)
        normalized_sort = normalize_sort(sort)
        normalized_start = (
            normalize_timestamp(start, field_name="start") if start is not None else None
        )
        normalized_end = normalize_timestamp(end, field_name="end") if end is not None else None

        if normalized_start is not None and normalized_end is not None:
            start_dt = _parse_normalized_timestamp(normalized_start)
            end_dt = _parse_normalized_timestamp(normalized_end)
            if start_dt > end_dt:
                raise AlpacaNewsInvalidInputError("Invalid start/end: start must not be after end.")

        headers = self._auth_headers()
        params = _build_params(
            normalized_symbols, normalized_limit, normalized_sort, normalized_start, normalized_end
        )

        owns_client = client is None
        http_client = client or httpx.Client(base_url=NEWS_BASE_URL, timeout=self._timeout)
        try:
            response = http_client.get(NEWS_PATH, headers=headers, params=params)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise AlpacaNewsError(
                f"Alpaca news request failed with status {exc.response.status_code}."
            ) from None
        except httpx.RequestError as exc:
            raise AlpacaNewsError(f"Alpaca news request failed: {type(exc).__name__}.") from None
        except ValueError:
            raise AlpacaNewsError("Alpaca news response was not valid JSON.") from None
        finally:
            if owns_client:
                http_client.close()

        if not isinstance(payload, dict):
            raise AlpacaNewsError("Alpaca news response payload was not a JSON object.")

        raw_articles = payload.get("news")
        if not isinstance(raw_articles, list):
            raise AlpacaNewsError("Alpaca news response payload did not include a news list.")

        retrieved_at = datetime.now(UTC).isoformat()
        items = _normalize_articles(raw_articles, retrieved_at=retrieved_at)

        if raw_articles and not items:
            raise AlpacaNewsError(
                "Alpaca news response contained articles but none were usable."
            )

        return items

    def check_connection(
        self,
        symbol: str = "SPY",
        *,
        limit: int = 10,
        client: httpx.Client | None = None,
    ) -> NewsConnectionStatus:
        """Perform one read-only connection check against the news endpoint.

        Returns a sanitized ``NewsConnectionStatus``; never raises for a
        failed request, missing credentials, or invalid input. Invalid
        input is rejected before any request is made and never echoed back
        in the returned status. Never includes headlines, URLs, summaries,
        or the raw response. If the provider returns a non-empty news list
        in which every article is rejected as malformed, this reports
        ``success=False`` with ``status_category="invalid_response"``
        rather than a false-successful zero-article result (an empty
        provider news list remains a valid successful zero-article
        result).
        """
        try:
            normalized_symbols = normalize_symbols(symbol)
        except AlpacaNewsInvalidInputError:
            return NewsConnectionStatus(
                configured=self.is_configured(),
                success=False,
                status_category="invalid_input",
                symbol="",
                article_count=0,
                newest_publication_timestamp=None,
            )

        symbol_display = ",".join(normalized_symbols)

        if not self.is_configured():
            return NewsConnectionStatus(
                configured=False,
                success=False,
                status_category="not_configured",
                symbol=symbol_display,
                article_count=0,
                newest_publication_timestamp=None,
            )

        try:
            normalized_limit = normalize_limit(limit)
        except AlpacaNewsInvalidInputError:
            return NewsConnectionStatus(
                configured=True,
                success=False,
                status_category="invalid_input",
                symbol=symbol_display,
                article_count=0,
                newest_publication_timestamp=None,
            )

        headers = self._auth_headers()
        params = _build_params(normalized_symbols, normalized_limit, "desc", None, None)
        owns_client = client is None
        http_client = client or httpx.Client(base_url=NEWS_BASE_URL, timeout=self._timeout)
        try:
            try:
                response = http_client.get(NEWS_PATH, headers=headers, params=params)
            except httpx.RequestError:
                return NewsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="network_error",
                    symbol=symbol_display,
                    article_count=0,
                    newest_publication_timestamp=None,
                )

            if not response.is_success:
                return NewsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category=_status_category(response.status_code),
                    symbol=symbol_display,
                    article_count=0,
                    newest_publication_timestamp=None,
                )

            try:
                payload = response.json()
            except ValueError:
                payload = None

            if not isinstance(payload, dict):
                return NewsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    symbol=symbol_display,
                    article_count=0,
                    newest_publication_timestamp=None,
                )

            raw_articles = payload.get("news")
            if not isinstance(raw_articles, list):
                return NewsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    symbol=symbol_display,
                    article_count=0,
                    newest_publication_timestamp=None,
                )

            retrieved_at = datetime.now(UTC).isoformat()
            items = _normalize_articles(raw_articles, retrieved_at=retrieved_at)

            if raw_articles and not items:
                return NewsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    symbol=symbol_display,
                    article_count=0,
                    newest_publication_timestamp=None,
                )

            return NewsConnectionStatus(
                configured=True,
                success=True,
                status_category=_status_category(response.status_code),
                symbol=symbol_display,
                article_count=len(items),
                newest_publication_timestamp=_newest_publication_timestamp(items),
            )
        finally:
            if owns_client:
                http_client.close()
