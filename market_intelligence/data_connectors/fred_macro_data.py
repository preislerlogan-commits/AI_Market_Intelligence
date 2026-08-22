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

Alongside the original single-latest-observation check
(``get_latest_observation``/``check_connection``, unchanged), this module
also exposes ``get_observations`` for fetching a bounded, paginated range of
historical observations for one series. It returns normalized
``FredObservation`` records containing only FRED's own reviewed fields
(provider, series_id, observation_date, value, is_missing,
realtime_start/realtime_end, retrieved_at) — no prediction, sentiment, or
derived analysis. This module does not write to DuckDB; storage is handled
separately by ``market_intelligence/storage/macro_observation_repository.py``.

**Real-time period and units are explicit, fixed request parameters for
``get_observations`` only.** FRED's documented default behavior, when a
request omits ``realtime_start``/``realtime_end``, is to report each
observation under *today's* date as its revision/vintage window — not the
observation's actual reported revision window. Because
``MacroObservationRepository``'s identity is
``(provider, series_id, observation_date, realtime_start, realtime_end)``,
relying on that default would make the identity drift on every retrieval
day even for a value FRED has not actually revised, which would falsely
describe distinct retrieval dates as distinct revisions and could silently
duplicate rows on re-ingestion. ``get_observations`` therefore always
requests the complete real-time period explicitly
(``realtime_start=1776-07-04``, ``realtime_end=9999-12-31``), so FRED
reports each observation's actual real-time/revision period instead, making
the stored identity stable and meaningful across repeated runs. It also
always requests ``units=lin`` explicitly, so stored values are unambiguously
untransformed levels, never a percent-change/index/other FRED-side
transformation. All four (``realtime_start``, ``realtime_end``,
``output_type``, ``units``) are fixed project constants, sent on every page
of every ``get_observations`` request, and are never caller-overridable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from market_intelligence.config.settings import Settings

FRED_BASE_URL = "https://api.stlouisfed.org"
OBSERVATIONS_PATH = "/fred/series/observations"
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)

MAX_SERIES_ID_LENGTH = 64
_SERIES_ID_PATTERN = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9_]{{0,{MAX_SERIES_ID_LENGTH - 1}}}$")

# Strict ISO calendar date: exactly YYYY-MM-DD, no time component. This
# deliberately rejects datetime strings (e.g. "2026-08-01T00:00:00Z"), which
# would otherwise silently truncate to a date and hide a caller's mistaken
# assumption about what the value represents.
MAX_DATE_LENGTH = 10
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Bounds for the historical-observations endpoint's own limit/offset
# pagination. FRED's API accepts a per-page limit up to 100000; this project
# conservatively caps it well below that to bound response size and
# per-request cost, mirroring AlpacaBarsClient's MAX_LIMIT/MAX_PAGES
# reasoning.
MIN_OBSERVATIONS_LIMIT = 1
MAX_OBSERVATIONS_LIMIT = 1000
DEFAULT_OBSERVATIONS_LIMIT = 1000

# Bounds the number of pages a single get_observations() call will follow,
# so a malformed or endless provider pagination sequence cannot loop
# indefinitely. No caller may raise this ceiling.
MAX_OBSERVATION_PAGES = 50

# Fixed request provenance for get_observations() only -- never sent by
# get_latest_observation()/check_connection(), and never caller-overridable.
# See the module docstring: omitting realtime_start/realtime_end makes FRED
# default both to today, which would make the
# (series_id, observation_date, realtime_start, realtime_end) storage
# identity drift across retrieval days for values FRED has not actually
# revised. Requesting the complete real-time period explicitly makes FRED
# report each observation's actual real-time/revision period instead, so the
# identity is stable and meaningful. units=lin is requested explicitly so
# stored values are unambiguously untransformed levels.
OBSERVATIONS_REALTIME_START = "1776-07-04"
OBSERVATIONS_REALTIME_END = "9999-12-31"
OBSERVATIONS_OUTPUT_TYPE = 1
OBSERVATIONS_UNITS = "lin"


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


class FredInvalidObservationRequestError(FredMacroDataError):
    """Raised when a get_observations() input fails validation before any request is made.

    Covers observation_start/observation_end (calendar-date shape, real
    calendar date, and start <= end), and the limit/max_pages pagination
    bounds. Validation happens before any HTTP request is constructed, so
    invalid input never reaches the network. The message never echoes the
    raw, unvalidated input.
    """


class _MalformedObservationError(Exception):
    """Internal signal that a single raw observation failed normalization.

    Never raised across the public API -- callers only ever see the
    sanitized ``FredMacroDataError`` raised when this is caught.
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


def _parse_response_date(value: Any) -> str:
    """Parse a provider-reported calendar date. Raises ``_MalformedObservationError`` if unusable.

    Used for a raw observation's ``date``/``realtime_start``/``realtime_end``
    fields -- an invalid value here marks the enclosing observation as
    malformed rather than being an invalid *caller input* (see
    ``normalize_observation_date`` for the caller-input equivalent). Requires
    an exact ``YYYY-MM-DD`` shape and a real calendar date (rejects, e.g.,
    a 13th month or a February 30th).
    """
    if isinstance(value, bool) or not isinstance(value, str):
        raise _MalformedObservationError("date invalid")
    trimmed = value.strip()
    if not trimmed or len(trimmed) != MAX_DATE_LENGTH or not _DATE_PATTERN.match(trimmed):
        raise _MalformedObservationError("date invalid")
    try:
        parsed = date.fromisoformat(trimmed)
    except ValueError:
        raise _MalformedObservationError("date invalid") from None
    return parsed.isoformat()


def normalize_observation_date(value: Any, *, field_name: str) -> str:
    """Normalize and validate a required observation_start/observation_end date.

    Raises ``FredInvalidObservationRequestError`` unless ``value`` is a
    string (booleans and other non-string types are rejected) that, once
    trimmed, is a strict, fully-specified ISO calendar date: exactly
    ``YYYY-MM-DD``, representing a real calendar date. Datetime strings,
    malformed dates, invalid calendar dates, and blank values are all
    rejected before any request is built. The error message never echoes
    the untrusted input.
    """
    try:
        return _parse_response_date(value)
    except _MalformedObservationError:
        raise FredInvalidObservationRequestError(
            f"Invalid {field_name}: must be a calendar date in YYYY-MM-DD format."
        ) from None


def normalize_observations_limit(value: Any) -> int:
    """Normalize and validate a per-page result-count limit for get_observations().

    Raises ``FredInvalidObservationRequestError`` unless ``limit`` is a plain
    ``int`` (booleans rejected) within
    ``[MIN_OBSERVATIONS_LIMIT, MAX_OBSERVATIONS_LIMIT]``.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise FredInvalidObservationRequestError("Invalid limit: expected an integer.")
    if not (MIN_OBSERVATIONS_LIMIT <= value <= MAX_OBSERVATIONS_LIMIT):
        raise FredInvalidObservationRequestError(
            f"Invalid limit: must be between {MIN_OBSERVATIONS_LIMIT} and "
            f"{MAX_OBSERVATIONS_LIMIT}."
        )
    return value


def normalize_observation_max_pages(value: Any) -> int:
    """Normalize and validate the ``max_pages`` pagination bound for get_observations().

    Raises ``FredInvalidObservationRequestError`` unless ``max_pages`` is a
    plain ``int`` (booleans rejected) within ``[1, MAX_OBSERVATION_PAGES]``.
    This enforces ``MAX_OBSERVATION_PAGES`` as a hard ceiling that no caller
    can raise.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise FredInvalidObservationRequestError("Invalid max_pages: expected an integer.")
    if not (1 <= value <= MAX_OBSERVATION_PAGES):
        raise FredInvalidObservationRequestError(
            f"Invalid max_pages: must be between 1 and {MAX_OBSERVATION_PAGES}."
        )
    return value


def _parse_observation_value(raw_value: Any) -> tuple[Decimal | None, bool]:
    """Parse a raw observation's ``value`` field into ``(value, is_missing)``.

    FRED's official convention for a missing observation is the literal
    string ``"."`` -- this is a legitimate, expected value, never treated as
    malformed. Every other value must be a string parseable as a finite
    ``Decimal`` (built directly from FRED's string representation, so no
    IEEE-754 binary-float rounding is ever introduced); non-string values,
    unparseable strings, and non-finite values (``NaN``, ``Infinity``) all
    raise ``_MalformedObservationError``.
    """
    if not isinstance(raw_value, str):
        raise _MalformedObservationError("value invalid")
    if raw_value == ".":
        return None, True
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        raise _MalformedObservationError("value invalid") from None
    if not value.is_finite():
        raise _MalformedObservationError("value invalid")
    return value, False


@dataclass(frozen=True)
class FredObservation:
    """A single normalized FRED historical observation.

    Deliberately contains only FRED's own reviewed fields plus this
    project's retrieval provenance -- no prediction, sentiment, or derived
    analysis. ``provider`` is always ``"fred"``. ``value`` is ``None`` and
    ``is_missing`` is ``True`` for FRED's own missing-observation marker
    (``"."``); otherwise ``value`` is a finite ``Decimal`` and ``is_missing``
    is ``False`` -- the two are always mutually consistent. ``realtime_start``
    and ``realtime_end`` are FRED's reported revision/vintage window for this
    observation, preserved as normalized calendar dates rather than
    discarded, so a later revision of the same ``series_id``/
    ``observation_date`` is never silently conflated with this one. This is
    only a stable, meaningful identity because ``get_observations`` always
    requests the complete real-time period explicitly (see the module
    docstring and ``OBSERVATIONS_REALTIME_START``/``OBSERVATIONS_REALTIME_END``)
    -- a request that omitted those parameters would have FRED default both
    to today's date, making these fields describe the *retrieval* day rather
    than an actual revision. ``retrieved_at`` is this connector's own UTC
    retrieval timestamp, kept distinct from every FRED-reported date.
    """

    provider: str
    series_id: str
    observation_date: str
    value: Decimal | None
    is_missing: bool
    realtime_start: str
    realtime_end: str
    retrieved_at: str


def _normalize_observation(raw: Any, *, series_id: str, retrieved_at: str) -> FredObservation:
    """Normalize a single raw observation. Raises ``_MalformedObservationError`` if unusable."""
    if not isinstance(raw, dict):
        raise _MalformedObservationError("observation is not an object")

    observation_date = _parse_response_date(raw.get("date"))
    realtime_start = _parse_response_date(raw.get("realtime_start"))
    realtime_end = _parse_response_date(raw.get("realtime_end"))
    value, is_missing = _parse_observation_value(raw.get("value"))

    return FredObservation(
        provider="fred",
        series_id=series_id,
        observation_date=observation_date,
        value=value,
        is_missing=is_missing,
        realtime_start=realtime_start,
        realtime_end=realtime_end,
        retrieved_at=retrieved_at,
    )


def _observations_match(a: FredObservation, b: FredObservation) -> bool:
    """Return True if two observations sharing the same identity agree on value/is_missing."""
    return a.value == b.value and a.is_missing == b.is_missing


def _build_observations_params(
    series_id: str,
    api_key: str,
    observation_start: str,
    observation_end: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    return {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
        "observation_start": observation_start,
        "observation_end": observation_end,
        "limit": limit,
        "offset": offset,
        "realtime_start": OBSERVATIONS_REALTIME_START,
        "realtime_end": OBSERVATIONS_REALTIME_END,
        "output_type": OBSERVATIONS_OUTPUT_TYPE,
        "units": OBSERVATIONS_UNITS,
    }


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

    def get_observations(
        self,
        series_id: str,
        observation_start: str,
        observation_end: str,
        *,
        limit: int = DEFAULT_OBSERVATIONS_LIMIT,
        max_pages: int = MAX_OBSERVATION_PAGES,
        client: httpx.Client | None = None,
    ) -> list[FredObservation]:
        """Fetch historical observations for a single FRED series. Read-only.

        Every page of this request always explicitly sends
        ``realtime_start=1776-07-04``, ``realtime_end=9999-12-31``,
        ``output_type=1``, and ``units=lin`` (``OBSERVATIONS_REALTIME_START``/
        ``OBSERVATIONS_REALTIME_END``/``OBSERVATIONS_OUTPUT_TYPE``/
        ``OBSERVATIONS_UNITS`` -- fixed project constants, never
        caller-overridable). This makes FRED report each observation's
        actual real-time/revision period rather than defaulting it to
        today's date, and makes stored values unambiguous untransformed
        levels -- see the module docstring.

        All inputs are strictly validated and normalized before any HTTP
        request is constructed, so malformed or malicious input never
        reaches the network. Raises ``FredInvalidSeriesIdError`` for an
        invalid ``series_id``; ``FredInvalidObservationRequestError`` for an
        invalid ``observation_start``/``observation_end``/``limit``/
        ``max_pages``, or if ``observation_start`` is after
        ``observation_end``; ``FredCredentialsMissingError`` if no API key is
        configured; or ``FredMacroDataError`` (sanitized) on request/network
        failure, malformed JSON, a FRED-reported error payload, an unusable
        response shape, a non-empty observations list containing any
        malformed observation, conflicting duplicate observations, invalid or
        mismatched pagination offset/count metadata (see below), or
        pagination exceeding ``max_pages``. An empty observations list is a
        valid, successful result. On any failure -- including a later-page
        failure -- no partial result is ever returned; exact duplicate
        observations (matched on series_id, observation_date,
        realtime_start, realtime_end) are deduplicated, and results are
        returned in chronological, deterministic order (observation_date,
        then realtime_start, then realtime_end).

        Every page's pagination metadata is strictly validated before its
        observations are trusted: the response's ``offset`` must be a plain
        nonnegative integer exactly equal to the offset that was requested
        (never inferred or silently substituted); the response's ``count``
        must be a plain nonnegative integer and must be identical on every
        page of the same request; and a page that returns fewer observations
        than the requested ``limit`` is only accepted as the final page if
        ``offset + returned observations`` has actually reached ``count`` --
        an empty or short page returned while ``count`` indicates more
        records remain is treated as a failure, never as a successful
        (silently incomplete) result. Any of these inconsistencies raises a
        sanitized ``FredMacroDataError`` immediately, before any of that
        page's observations are merged into the result. If the hard
        ``max_pages`` bound is reached without ``count`` being satisfied,
        the request fails safely rather than returning a partial series.
        """
        normalized_series_id = normalize_series_id(series_id)
        normalized_start = normalize_observation_date(
            observation_start, field_name="observation_start"
        )
        normalized_end = normalize_observation_date(observation_end, field_name="observation_end")
        if date.fromisoformat(normalized_start) > date.fromisoformat(normalized_end):
            raise FredInvalidObservationRequestError(
                "Invalid observation_start/observation_end: observation_start must not be "
                "after observation_end."
            )
        normalized_limit = normalize_observations_limit(limit)
        normalized_max_pages = normalize_observation_max_pages(max_pages)
        api_key = self._api_key()

        owns_client = client is None
        http_client = client or httpx.Client(base_url=FRED_BASE_URL, timeout=self._timeout)

        retrieved_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        observations_by_identity: dict[tuple[str, str, str, str], FredObservation] = {}
        offset = 0
        total_count: int | None = None

        try:
            for _ in range(normalized_max_pages):
                params = _build_observations_params(
                    normalized_series_id,
                    api_key,
                    normalized_start,
                    normalized_end,
                    normalized_limit,
                    offset,
                )
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

                # Hardened pagination-metadata validation: a misbehaving or
                # non-compliant response (e.g. one that ignores the
                # requested offset, or that reports an inconsistent count)
                # must fail the whole request rather than silently returning
                # an incomplete series. Nothing from this page is trusted or
                # merged into the result until its metadata passes.
                raw_offset = payload.get("offset")
                if (
                    isinstance(raw_offset, bool)
                    or not isinstance(raw_offset, int)
                    or raw_offset < 0
                ):
                    raise FredMacroDataError(
                        "FRED observations response included an invalid page offset."
                    )
                if raw_offset != offset:
                    raise FredMacroDataError(
                        "FRED observations response offset did not match the requested "
                        "page offset."
                    )

                raw_count = payload.get("count")
                if (
                    isinstance(raw_count, bool)
                    or not isinstance(raw_count, int)
                    or raw_count < 0
                ):
                    raise FredMacroDataError(
                        "FRED observations response included an invalid result count."
                    )
                if total_count is None:
                    total_count = raw_count
                elif raw_count != total_count:
                    raise FredMacroDataError(
                        "FRED observations response reported an inconsistent result count "
                        "across pages."
                    )

                raw_observations = payload.get("observations")
                if not isinstance(raw_observations, list):
                    raise FredMacroDataError(
                        "FRED observations response payload did not include an observations "
                        "list."
                    )

                for raw_observation in raw_observations:
                    try:
                        observation = _normalize_observation(
                            raw_observation,
                            series_id=normalized_series_id,
                            retrieved_at=retrieved_at,
                        )
                    except _MalformedObservationError:
                        raise FredMacroDataError(
                            "FRED observations response contained a malformed observation."
                        ) from None

                    identity = (
                        observation.series_id,
                        observation.observation_date,
                        observation.realtime_start,
                        observation.realtime_end,
                    )
                    existing = observations_by_identity.get(identity)
                    if existing is not None:
                        if not _observations_match(existing, observation):
                            raise FredMacroDataError(
                                "FRED observations response contained conflicting duplicate "
                                "observations."
                            )
                        continue
                    observations_by_identity[identity] = observation

                returned = len(raw_observations)
                completed_through = offset + returned

                if returned < normalized_limit:
                    # A short (possibly empty) page is only a legitimate
                    # final page if count confirms nothing is outstanding;
                    # otherwise this is a silently incomplete result and
                    # must fail rather than be returned as if successful.
                    if completed_through < total_count:
                        raise FredMacroDataError(
                            "FRED observations pagination returned a short page while "
                            "records remained outstanding."
                        )
                    break

                offset = completed_through
                if offset >= total_count:
                    break
            else:
                raise FredMacroDataError(
                    "FRED observations pagination exceeded the maximum page count."
                )
        finally:
            if owns_client:
                http_client.close()

        return sorted(
            observations_by_identity.values(),
            key=lambda o: (o.observation_date, o.realtime_start, o.realtime_end),
        )

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
