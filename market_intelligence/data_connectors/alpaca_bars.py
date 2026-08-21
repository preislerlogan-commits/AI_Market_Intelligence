"""Read-only Alpaca historical stock-bars connector.

This module talks only to Alpaca's official read-only market-data host
(``https://data.alpaca.markets``) and only to its single-symbol historical
bars endpoint. It must never import, implement, or reference brokerage
order, account, or execution functionality, and must never connect to
Robinhood. Credential values are read from ``Settings`` and used only in
request headers — they are never printed, logged, or included in
exception messages. This module does not write to DuckDB or any other
storage; it only retrieves and normalizes bars.

Feed: every request explicitly passes ``feed=DATA_FEED`` ("iex"). Alpaca's
historical single-symbol bars endpoint defaults to the SIP feed when no
``feed`` parameter is sent, and SIP requires a market-data subscription this
project does not have — an unauthorized request against the SIP default can
fail with HTTP 403. ``DATA_FEED`` is a fixed module constant; it is never
accepted as a caller-supplied argument anywhere in this module, and this
connector never falls back to any other feed automatically. IEX is a
single-exchange feed, not the consolidated (SIP) tape, so it has narrower
market coverage than SIP — see DATA_CATALOG.md/PROJECT_STATE.md.

Numeric representation: normalized OHLC/vwap values are stored as
``decimal.Decimal``, built from the provider's JSON numeric value via
``Decimal(str(value))`` rather than ``Decimal(value)``. Converting through
``str()`` avoids introducing IEEE-754 binary floating-point representation
error into values intended for reproducible historical analysis — a raw
``float`` such as ``0.1`` does not have an exact binary representation, and
constructing a ``Decimal`` directly from that float would silently bake in
the resulting rounding artifact. ``volume`` and ``trade_count`` are plain
``int`` (no fractional shares/trades in a bar).

Nullability: ``trade_count`` and ``vwap`` may legitimately be absent or
``null`` in Alpaca's bar payloads (for example, older bars or certain
feeds/subscription tiers may omit trade count and volume-weighted average
price), so both are modeled as optional on the normalized ``Bar`` and are
accepted as ``None`` without being treated as malformed. ``open``, ``high``,
``low``, ``close``, ``volume``, and the bar ``timestamp`` are always
required.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)

BARS_BASE_URL = "https://data.alpaca.markets"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

# Alpaca's historical single-symbol bars endpoint defaults to the SIP feed
# when no ``feed`` parameter is sent, and SIP requires a market-data
# subscription this project does not have (an unauthorized SIP default
# request returns HTTP 403). This project is explicitly scoped to the free,
# always-available IEX feed. DATA_FEED is a fixed, project-approved constant
# — it is never accepted as a caller-supplied parameter anywhere in this
# module, so no caller can inject an unsupported or different feed. See
# PROJECT_STATE.md/DATA_CATALOG.md for the coverage tradeoff this implies
# (IEX is a single exchange's data, not the consolidated SIP tape).
DATA_FEED = "iex"

# Project-approved timeframes only. Alpaca supports other timeframe strings
# (e.g. "1Hour", "1Week"), but this connector deliberately rejects anything
# outside this set.
_TIMEFRAME_MAP = {
    "1min": "1Min",
    "5min": "5Min",
    "1day": "1Day",
}

MIN_LIMIT = 1
# Alpaca's bars endpoint accepts a per-page limit up to 10,000. This project
# conservatively caps it at 1,000 to bound response size and per-request
# cost; callers needing more history should paginate rather than raise this.
MAX_LIMIT = 1000
DEFAULT_LIMIT = 1000

# Bounds the number of pages a single get_bars() call will follow, so a
# malformed or endless provider pagination sequence cannot loop indefinitely.
MAX_PAGES = 50

MAX_TIMESTAMP_LENGTH = 40

CHECK_CONNECTION_LIMIT = 5
CHECK_CONNECTION_LOOKBACK_DAYS = 5

# Strict RFC3339 date-time: a complete calendar date and time-of-day with an
# explicit "Z" or numeric UTC offset. Deliberately rejects date-only values,
# naive (offset-free) timestamps, and anything with trailing/embedded junk —
# the pattern is fully anchored, so nothing outside this shape can match.
_RFC3339_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>0[1-9]|1[0-2])-(?P<day>0[1-9]|[12]\d|3[01])"
    r"[Tt](?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)(?:\.\d+)?"
    r"(?P<offset>[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)


class AlpacaBarsCredentialsMissingError(RuntimeError):
    """Raised when a bars request is attempted without configured credentials."""


class AlpacaBarsError(RuntimeError):
    """Raised for a sanitized Alpaca bars request failure.

    The message never includes request headers, credential values, page
    tokens, or the raw response body — only a status code, exception type,
    or a fixed, non-input-derived description.
    """


class AlpacaBarsInvalidInputError(AlpacaBarsError):
    """Raised when an input fails normalization/validation before any request is made.

    Validation happens before any HTTP request is constructed, so invalid
    or malicious input never reaches the network. The message never echoes
    raw, unvalidated input.
    """


class _MalformedBarError(Exception):
    """Internal signal that a single raw bar failed normalization.

    Never raised across the public API — callers only ever see the
    sanitized ``AlpacaBarsError`` raised when this is caught.
    """


def normalize_timeframe(value: Any) -> str:
    """Normalize and validate a bar timeframe.

    Accepts case/whitespace variants of the three project-approved
    timeframes ("1Min", "5Min", "1Day") and normalizes them to those exact
    provider values. Raises ``AlpacaBarsInvalidInputError`` for anything
    else, including booleans and non-string input, before any request is
    built.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise AlpacaBarsInvalidInputError("Invalid timeframe: expected a string.")

    normalized_key = re.sub(r"\s+", "", value.strip().lower())
    if normalized_key not in _TIMEFRAME_MAP:
        raise AlpacaBarsInvalidInputError(
            "Invalid timeframe: must be one of '1Min', '5Min', '1Day'."
        )
    return _TIMEFRAME_MAP[normalized_key]


def normalize_limit(limit: Any) -> int:
    """Normalize and validate a per-page result-count limit.

    Raises ``AlpacaBarsInvalidInputError`` unless ``limit`` is a plain
    ``int`` (booleans rejected) within ``[MIN_LIMIT, MAX_LIMIT]``.
    """
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise AlpacaBarsInvalidInputError("Invalid limit: expected an integer.")
    if not (MIN_LIMIT <= limit <= MAX_LIMIT):
        raise AlpacaBarsInvalidInputError(
            f"Invalid limit: must be between {MIN_LIMIT} and {MAX_LIMIT}."
        )
    return limit


def normalize_max_pages(value: Any) -> int:
    """Normalize and validate the ``max_pages`` pagination bound.

    Raises ``AlpacaBarsInvalidInputError`` unless ``max_pages`` is a plain
    ``int`` (booleans rejected) within ``[1, MAX_PAGES]``. This enforces
    ``MAX_PAGES`` as a hard ceiling that no caller can raise, so a single
    ``get_bars()`` call can never be made to follow more than ``MAX_PAGES``
    pages. The error message never echoes the raw input.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise AlpacaBarsInvalidInputError("Invalid max_pages: expected an integer.")
    if not (1 <= value <= MAX_PAGES):
        raise AlpacaBarsInvalidInputError(
            f"Invalid max_pages: must be between 1 and {MAX_PAGES}."
        )
    return value


def normalize_timestamp(value: Any, *, field_name: str) -> str:
    """Normalize and validate a required start/end RFC3339 timestamp.

    Raises ``AlpacaBarsInvalidInputError`` unless ``value`` is a string
    (booleans and other non-string types are rejected) that, once
    trimmed, is a strict, fully-specified RFC3339 date-time: a complete
    calendar date and time-of-day with an explicit "Z" or numeric UTC
    offset. Naive (offset-free) timestamps, date-only values, malformed
    timestamps, and embedded junk are all rejected before any request is
    built. The calendar date and UTC offset are validated for real values
    (not just shape), and the result is always returned as one consistent
    normalized representation: UTC with a "Z" suffix. The error message
    never echoes the untrusted input.
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise AlpacaBarsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_TIMESTAMP_LENGTH:
        raise AlpacaBarsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    match = _RFC3339_PATTERN.match(trimmed)
    if match is None:
        raise AlpacaBarsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    offset = match.group("offset")
    candidate = trimmed[:-1] + "+00:00" if offset in ("Z", "z") else trimmed

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise AlpacaBarsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        ) from None

    if parsed.tzinfo is None:
        raise AlpacaBarsInvalidInputError(
            f"Invalid {field_name}: must be a valid RFC3339 timestamp."
        )

    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_normalized_timestamp(value: str) -> datetime:
    """Parse a value already produced by ``normalize_timestamp`` back into a datetime.

    Only ever called on this connector's own normalized output (UTC, "Z"
    suffix), never on raw user input.
    """
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _parse_bar_timestamp(value: Any) -> str:
    """Parse and normalize a provider-reported bar timestamp.

    Uses the same strict RFC3339 rules as ``normalize_timestamp``, but
    raises ``_MalformedBarError`` instead: an invalid bar timestamp marks
    the enclosing bar as malformed rather than being an invalid *input*.
    """
    if not isinstance(value, str):
        raise _MalformedBarError("bar timestamp invalid")

    trimmed = value.strip()
    if not trimmed or len(trimmed) > MAX_TIMESTAMP_LENGTH:
        raise _MalformedBarError("bar timestamp invalid")

    match = _RFC3339_PATTERN.match(trimmed)
    if match is None:
        raise _MalformedBarError("bar timestamp invalid")

    offset = match.group("offset")
    candidate = trimmed[:-1] + "+00:00" if offset in ("Z", "z") else trimmed

    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        raise _MalformedBarError("bar timestamp invalid") from None

    if parsed.tzinfo is None:
        raise _MalformedBarError("bar timestamp invalid")

    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _to_decimal(value: Any, *, field_name: str) -> Decimal:
    """Convert a provider numeric field to ``Decimal``, rejecting unsafe values.

    Rejects booleans, non-numeric types, and non-finite floats (NaN,
    infinity). See the module docstring for why ``Decimal(str(value))`` is
    used instead of ``Decimal(value)``.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _MalformedBarError(f"{field_name} invalid")
    if isinstance(value, float) and not math.isfinite(value):
        raise _MalformedBarError(f"{field_name} invalid")
    return Decimal(str(value))


def _to_optional_decimal(value: Any, *, field_name: str) -> Decimal | None:
    if value is None:
        return None
    return _to_decimal(value, field_name=field_name)


def _to_nonneg_int(value: Any, *, field_name: str, allow_none: bool) -> int | None:
    if value is None:
        if allow_none:
            return None
        raise _MalformedBarError(f"{field_name} invalid")
    if isinstance(value, bool) or not isinstance(value, int):
        raise _MalformedBarError(f"{field_name} invalid")
    if value < 0:
        raise _MalformedBarError(f"{field_name} invalid")
    return value


@dataclass(frozen=True)
class Bar:
    """A single normalized Alpaca historical stock bar.

    Deliberately contains only provider-reported OHLCV/vwap data plus
    provenance — no indicators, returns, labels, sentiment, predictions, or
    trade directions. ``feed`` is always ``DATA_FEED`` ("iex") — this
    connector never requests any other feed — and is recorded on every bar
    so data provenance (which exchange feed produced these values) is
    always explicit. ``timestamp`` is Alpaca's reported bar timestamp
    (UTC); ``retrieved_at`` is this connector's own retrieval timestamp
    (UTC, set once per request when the response was processed) and is
    always distinct from it.
    """

    provider: str
    symbol: str
    timeframe: str
    feed: str
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    trade_count: int | None
    vwap: Decimal | None
    retrieved_at: str


@dataclass(frozen=True)
class BarsConnectionStatus:
    """Sanitized result of a single read-only bars connection check.

    ``feed`` is always ``DATA_FEED`` ("iex") on every status returned by
    ``check_connection`` — it is a fixed, project-approved constant, never
    derived from the request outcome, so provenance is explicit even for a
    failed/unconfigured/invalid-input check.
    """

    configured: bool
    success: bool
    status_category: str
    symbol: str
    timeframe: str
    feed: str
    bar_count: int
    oldest_bar_timestamp: str | None
    newest_bar_timestamp: str | None


def _status_category(status_code: int) -> str:
    return f"{status_code // 100}xx"


def _bars_match(a: Bar, b: Bar) -> bool:
    """Return True if two bars sharing the same (symbol, timeframe, feed, timestamp) agree."""
    return (
        a.feed == b.feed
        and a.open == b.open
        and a.high == b.high
        and a.low == b.low
        and a.close == b.close
        and a.volume == b.volume
        and a.trade_count == b.trade_count
        and a.vwap == b.vwap
    )


def _normalize_bar(raw: Any, *, symbol: str, timeframe: str, retrieved_at: str) -> Bar:
    """Normalize a single raw bar. Raises ``_MalformedBarError`` if unusable."""
    if not isinstance(raw, dict):
        raise _MalformedBarError("bar is not an object")

    timestamp = _parse_bar_timestamp(raw.get("t"))

    open_ = _to_decimal(raw.get("o"), field_name="open")
    high = _to_decimal(raw.get("h"), field_name="high")
    low = _to_decimal(raw.get("l"), field_name="low")
    close = _to_decimal(raw.get("c"), field_name="close")

    if not (high >= low and high >= open_ and high >= close and low <= open_ and low <= close):
        raise _MalformedBarError("candle values are inconsistent")

    volume = _to_nonneg_int(raw.get("v"), field_name="volume", allow_none=False)
    trade_count = _to_nonneg_int(raw.get("n"), field_name="trade_count", allow_none=True)
    vwap = _to_optional_decimal(raw.get("vw"), field_name="vwap")

    assert volume is not None  # allow_none=False guarantees this
    return Bar(
        provider="alpaca",
        symbol=symbol,
        timeframe=timeframe,
        feed=DATA_FEED,
        timestamp=timestamp,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        trade_count=trade_count,
        vwap=vwap,
        retrieved_at=retrieved_at,
    )


def _build_params(
    timeframe: str,
    start: str,
    end: str,
    limit: int,
    page_token: str | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "limit": limit,
        "feed": DATA_FEED,
    }
    if page_token is not None:
        params["page_token"] = page_token
    return params


class AlpacaBarsClient:
    """Minimal read-only client for Alpaca's historical stock-bars endpoint.

    Bars only: this client has no methods for placing, modifying, or
    cancelling orders, and none touch account/trading endpoints. It does
    not write to any database. It supports exactly one symbol per request.
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
            raise AlpacaBarsCredentialsMissingError("Alpaca credentials are not configured.")
        assert self._settings.alpaca_api_key is not None
        assert self._settings.alpaca_api_secret is not None
        return {
            "APCA-API-KEY-ID": self._settings.alpaca_api_key.get_secret_value(),
            "APCA-API-SECRET-KEY": self._settings.alpaca_api_secret.get_secret_value(),
        }

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
        *,
        limit: int = DEFAULT_LIMIT,
        max_pages: int = MAX_PAGES,
        client: httpx.Client | None = None,
    ) -> list[Bar]:
        """Fetch historical bars for a single symbol. Read-only.

        All inputs are strictly validated and normalized before any HTTP
        request is constructed, so malformed or malicious input never
        reaches the network. Raises ``AlpacaBarsInvalidInputError`` for an
        invalid ``symbol``/``timeframe``/``start``/``end``/``limit``/
        ``max_pages``, or if ``start`` is not strictly before ``end``;
        ``AlpacaBarsCredentialsMissingError`` if credentials are not
        configured; or ``AlpacaBarsError`` (sanitized) on request failure,
        malformed JSON, an unusable response shape, a non-empty bars list
        containing any malformed bar, conflicting duplicate bars, an
        invalid or repeated pagination token, or pagination exceeding
        ``max_pages``. An empty bars list is a valid, successful result. On
        any failure — including a later-page failure — no partial result is
        returned.
        """
        try:
            normalized_symbol = normalize_symbol(symbol)
        except AlpacaInvalidSymbolError as exc:
            raise AlpacaBarsInvalidInputError(f"Invalid symbol: {exc}") from None
        normalized_timeframe = normalize_timeframe(timeframe)
        normalized_start = normalize_timestamp(start, field_name="start")
        normalized_end = normalize_timestamp(end, field_name="end")

        start_dt = _parse_normalized_timestamp(normalized_start)
        end_dt = _parse_normalized_timestamp(normalized_end)
        if start_dt >= end_dt:
            raise AlpacaBarsInvalidInputError(
                "Invalid start/end: start must be strictly before end."
            )

        normalized_limit = normalize_limit(limit)
        normalized_max_pages = normalize_max_pages(max_pages)

        headers = self._auth_headers()

        owns_client = client is None
        http_client = client or httpx.Client(base_url=BARS_BASE_URL, timeout=self._timeout)

        retrieved_at = datetime.now(UTC).isoformat()
        bars_by_identity: dict[tuple[str, str], Bar] = {}
        seen_tokens: set[str] = set()
        page_token: str | None = None

        try:
            for _ in range(normalized_max_pages):
                params = _build_params(
                    normalized_timeframe,
                    normalized_start,
                    normalized_end,
                    normalized_limit,
                    page_token,
                )
                try:
                    response = http_client.get(
                        f"/v2/stocks/{normalized_symbol}/bars", headers=headers, params=params
                    )
                    response.raise_for_status()
                    payload = response.json()
                except httpx.HTTPStatusError as exc:
                    raise AlpacaBarsError(
                        f"Alpaca bars request failed with status {exc.response.status_code}."
                    ) from None
                except httpx.RequestError as exc:
                    raise AlpacaBarsError(
                        f"Alpaca bars request failed: {type(exc).__name__}."
                    ) from None
                except ValueError:
                    raise AlpacaBarsError("Alpaca bars response was not valid JSON.") from None

                if not isinstance(payload, dict):
                    raise AlpacaBarsError("Alpaca bars response payload was not a JSON object.")

                raw_bars = payload.get("bars")
                if not isinstance(raw_bars, list):
                    raise AlpacaBarsError(
                        "Alpaca bars response payload did not include a bars list."
                    )

                for raw_bar in raw_bars:
                    try:
                        bar = _normalize_bar(
                            raw_bar,
                            symbol=normalized_symbol,
                            timeframe=normalized_timeframe,
                            retrieved_at=retrieved_at,
                        )
                    except _MalformedBarError:
                        raise AlpacaBarsError(
                            "Alpaca bars response contained a malformed bar."
                        ) from None

                    identity = (bar.feed, bar.timestamp)
                    existing = bars_by_identity.get(identity)
                    if existing is not None:
                        if not _bars_match(existing, bar):
                            raise AlpacaBarsError(
                                "Alpaca bars response contained conflicting duplicate bars."
                            )
                        continue
                    bars_by_identity[identity] = bar

                next_token = payload.get("next_page_token")
                if next_token is None:
                    break
                if not isinstance(next_token, str) or not next_token:
                    raise AlpacaBarsError(
                        "Alpaca bars response contained an invalid pagination token."
                    )
                if next_token in seen_tokens:
                    raise AlpacaBarsError("Alpaca bars pagination repeated a page token.")
                seen_tokens.add(next_token)
                page_token = next_token
            else:
                raise AlpacaBarsError("Alpaca bars pagination exceeded the maximum page count.")
        finally:
            if owns_client:
                http_client.close()

        return sorted(bars_by_identity.values(), key=lambda bar: bar.timestamp)

    def check_connection(
        self,
        symbol: str = "SPY",
        *,
        timeframe: str = "5Min",
        client: httpx.Client | None = None,
    ) -> BarsConnectionStatus:
        """Perform one read-only, bounded connection check against the bars endpoint.

        Returns a sanitized ``BarsConnectionStatus``; never raises for a
        failed request, missing credentials, or invalid input. Invalid
        input is rejected before any request is made and never echoed back
        in the returned status. Never includes OHLCV values, credentials,
        URLs, raw responses, or page tokens. Uses a small, fixed historical
        lookback window and a conservative limit, and makes at most one
        HTTP request (no pagination), always explicitly requesting the
        project-approved ``DATA_FEED`` ("iex") feed. Malformed provider bar
        data is reported as ``status_category="invalid_response"``. Bars are
        deduplicated by (feed, timestamp) using the same identity rules as
        ``get_bars()``: exact duplicate bars count once, and duplicate
        (feed, timestamp) pairs with conflicting OHLCV/trade_count/vwap are
        reported as ``status_category="invalid_response"`` rather than
        success.
        ``bar_count``/``oldest_bar_timestamp``/``newest_bar_timestamp``
        reflect the deduplicated set.
        """
        try:
            normalized_symbol = normalize_symbol(symbol)
        except AlpacaInvalidSymbolError:
            return BarsConnectionStatus(
                configured=self.is_configured(),
                success=False,
                status_category="invalid_symbol",
                symbol="",
                timeframe="",
                feed=DATA_FEED,
                bar_count=0,
                oldest_bar_timestamp=None,
                newest_bar_timestamp=None,
            )

        try:
            normalized_timeframe = normalize_timeframe(timeframe)
        except AlpacaBarsInvalidInputError:
            return BarsConnectionStatus(
                configured=self.is_configured(),
                success=False,
                status_category="invalid_input",
                symbol=normalized_symbol,
                timeframe="",
                feed=DATA_FEED,
                bar_count=0,
                oldest_bar_timestamp=None,
                newest_bar_timestamp=None,
            )

        if not self.is_configured():
            return BarsConnectionStatus(
                configured=False,
                success=False,
                status_category="not_configured",
                symbol=normalized_symbol,
                timeframe=normalized_timeframe,
                feed=DATA_FEED,
                bar_count=0,
                oldest_bar_timestamp=None,
                newest_bar_timestamp=None,
            )

        end_dt = datetime.now(UTC).replace(microsecond=0)
        start_dt = end_dt - timedelta(days=CHECK_CONNECTION_LOOKBACK_DAYS)
        start = start_dt.isoformat().replace("+00:00", "Z")
        end = end_dt.isoformat().replace("+00:00", "Z")

        headers = self._auth_headers()
        params = _build_params(normalized_timeframe, start, end, CHECK_CONNECTION_LIMIT, None)

        owns_client = client is None
        http_client = client or httpx.Client(base_url=BARS_BASE_URL, timeout=self._timeout)
        try:
            try:
                response = http_client.get(
                    f"/v2/stocks/{normalized_symbol}/bars", headers=headers, params=params
                )
            except httpx.RequestError:
                return BarsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="network_error",
                    symbol=normalized_symbol,
                    timeframe=normalized_timeframe,
                    feed=DATA_FEED,
                    bar_count=0,
                    oldest_bar_timestamp=None,
                    newest_bar_timestamp=None,
                )

            if not response.is_success:
                return BarsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category=_status_category(response.status_code),
                    symbol=normalized_symbol,
                    timeframe=normalized_timeframe,
                    feed=DATA_FEED,
                    bar_count=0,
                    oldest_bar_timestamp=None,
                    newest_bar_timestamp=None,
                )

            try:
                payload = response.json()
            except ValueError:
                payload = None

            if not isinstance(payload, dict):
                return BarsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    symbol=normalized_symbol,
                    timeframe=normalized_timeframe,
                    feed=DATA_FEED,
                    bar_count=0,
                    oldest_bar_timestamp=None,
                    newest_bar_timestamp=None,
                )

            raw_bars = payload.get("bars")
            if not isinstance(raw_bars, list):
                return BarsConnectionStatus(
                    configured=True,
                    success=False,
                    status_category="invalid_response",
                    symbol=normalized_symbol,
                    timeframe=normalized_timeframe,
                    feed=DATA_FEED,
                    bar_count=0,
                    oldest_bar_timestamp=None,
                    newest_bar_timestamp=None,
                )

            retrieved_at = datetime.now(UTC).isoformat()
            bars_by_identity: dict[tuple[str, str], Bar] = {}
            for raw_bar in raw_bars:
                try:
                    bar = _normalize_bar(
                        raw_bar,
                        symbol=normalized_symbol,
                        timeframe=normalized_timeframe,
                        retrieved_at=retrieved_at,
                    )
                except _MalformedBarError:
                    return BarsConnectionStatus(
                        configured=True,
                        success=False,
                        status_category="invalid_response",
                        symbol=normalized_symbol,
                        timeframe=normalized_timeframe,
                        feed=DATA_FEED,
                        bar_count=0,
                        oldest_bar_timestamp=None,
                        newest_bar_timestamp=None,
                    )

                identity = (bar.feed, bar.timestamp)
                existing = bars_by_identity.get(identity)
                if existing is not None:
                    if not _bars_match(existing, bar):
                        return BarsConnectionStatus(
                            configured=True,
                            success=False,
                            status_category="invalid_response",
                            symbol=normalized_symbol,
                            timeframe=normalized_timeframe,
                            feed=DATA_FEED,
                            bar_count=0,
                            oldest_bar_timestamp=None,
                            newest_bar_timestamp=None,
                        )
                    continue
                bars_by_identity[identity] = bar

            bars = sorted(bars_by_identity.values(), key=lambda bar: bar.timestamp)

            return BarsConnectionStatus(
                configured=True,
                success=True,
                status_category=_status_category(response.status_code),
                symbol=normalized_symbol,
                timeframe=normalized_timeframe,
                feed=DATA_FEED,
                bar_count=len(bars),
                oldest_bar_timestamp=bars[0].timestamp if bars else None,
                newest_bar_timestamp=bars[-1].timestamp if bars else None,
            )
        finally:
            if owns_client:
                http_client.close()
