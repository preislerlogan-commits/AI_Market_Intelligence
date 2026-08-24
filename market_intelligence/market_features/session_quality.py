"""Read-only regular-trading-hours session-quality report for stored 5Min bars.

``SessionQualityBuilder`` produces exactly one deterministic, JSON-ready
report dict describing whether a *single, exact bar identity's* stored
``5Min`` bars for one symbol/session-date cover the regular trading session
completely -- so a future agent can decide whether stored price evidence is
safe to analyze before doing so. It is read-only end to end:

- It makes **no network request** of any kind.
- It opens the local database with ``duckdb.connect(path, read_only=True)``
  and never writes a row, never applies a migration.
- It never predicts, scores, recommends, or infers a trading signal --
  every value is either a stored value already written by an existing,
  reviewed ingestion pipeline, or this module's own weekday/time-window
  bookkeeping about that stored data.

**Regular session definition (weekday/time-window only):** a stored ``5Min``
bar is a "regular session" bar if its bar timestamp, converted to
``America/New_York``, falls on a Monday-Friday calendar date and its
time-of-day is one of the 78 five-minute slots from ``09:30`` through
``15:55`` inclusive (the last regular slot *begins* at ``15:55`` and covers
``15:55``-``16:00``). **This is weekday/time-window logic only** -- it has
no U.S. exchange-holiday or early-close calendar, so a stored date that
happens to be a market holiday or an early-close day is evaluated with the
exact same rule and will simply be reported incomplete, never flagged as
"no session expected." See ``docs/SESSION_QUALITY.md`` for full detail and
known limitations.

Every public input (symbol, optional session date) is strictly validated
before any DuckDB connection is opened, so malformed or malicious input
never reaches storage. A missing database file, an empty database, or a
symbol/date with no stored regular-session bars are all valid, non-error
input: this module always returns a truthful report, never a crash.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)
from market_intelligence.storage.database import DuckDBManager

# This module reports on exactly one, fixed timeframe. A different timeframe
# is out of scope -- see docs/SESSION_QUALITY.md.
TIMEFRAME = "5Min"

EASTERN = ZoneInfo("America/New_York")
REGULAR_SESSION_START = time(9, 30)
REGULAR_SESSION_LAST_SLOT_START = time(15, 55)
SLOT_INTERVAL_MINUTES = 5
EXPECTED_SLOT_COUNT = 78  # (15:55 - 09:30) / 5 minutes + 1

_SESSION_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_RETURN_DECIMAL_EXPONENT = Decimal("0.000001")


class SessionQualityError(RuntimeError):
    """Sanitized error for the session-quality builder.

    Never includes the database path, SQL text, or a raw underlying
    exception -- only a fixed, non-input-derived description.
    """


class SessionQualityValidationError(SessionQualityError):
    """Raised when an input fails validation before any DuckDB connection is opened.

    The message never echoes raw, unvalidated input.
    """


def normalize_session_date(value: str | None) -> date | None:
    """Normalize and validate an optional explicit session date.

    ``None`` means "auto-select the most recent stored date with at least
    one regular-session bar" and is returned unchanged. A non-``None`` value
    must be a plain ``YYYY-MM-DD`` string naming a real calendar date;
    anything else raises ``SessionQualityValidationError`` before any
    database access. A syntactically valid weekend date is accepted (it is
    not rejected here) -- see the module docstring: this is weekday/time-
    window logic applied uniformly, not a calendar-aware rejection.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not _SESSION_DATE_PATTERN.match(value):
        raise SessionQualityValidationError(
            "Invalid session date: expected a 'YYYY-MM-DD' string."
        )
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SessionQualityValidationError(
            "Invalid session date: not a real calendar date."
        ) from None


def _expected_slot_times() -> list[time]:
    slots = []
    total_minutes = REGULAR_SESSION_START.hour * 60 + REGULAR_SESSION_START.minute
    last_minutes = (
        REGULAR_SESSION_LAST_SLOT_START.hour * 60 + REGULAR_SESSION_LAST_SLOT_START.minute
    )
    while total_minutes <= last_minutes:
        hour, minute = divmod(total_minutes, 60)
        slots.append(time(hour, minute))
        total_minutes += SLOT_INTERVAL_MINUTES
    return slots


_EXPECTED_SLOT_TIMES = _expected_slot_times()
_EXPECTED_SLOT_TIMES_SET = frozenset(_EXPECTED_SLOT_TIMES)
assert len(_EXPECTED_SLOT_TIMES) == EXPECTED_SLOT_COUNT


def _decimal_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _utc_timestamp_str(value: datetime) -> str:
    """Format a UTC instant (naive or tz-aware) as a single-suffix RFC3339 UTC string."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat() + "Z"


def _format_as_of(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class _Identity:
    provider: str
    symbol: str
    feed: str
    adjustment: str
    currency: str


@dataclass(frozen=True)
class _CategorizedBar:
    bar_timestamp_utc: datetime
    et_datetime: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None


def _categorize(
    bar_timestamp_utc: datetime, session_date: date
) -> str:
    """Classify one bar's ET-local time relative to ``session_date``.

    Returns ``"premarket"``, ``"regular"``, ``"after_hours"``, or
    ``"unexpected"`` (in-window time but not a valid regular-session slot --
    e.g. a non-weekday date, or a time that does not align to the 5-minute
    slot grid). Only bars whose ET calendar date equals ``session_date`` are
    ever categorized by this function; the caller filters by ET date first.
    """
    et_dt = bar_timestamp_utc.replace(tzinfo=UTC).astimezone(EASTERN)
    t = et_dt.time()
    if t < REGULAR_SESSION_START:
        return "premarket"
    if t > REGULAR_SESSION_LAST_SLOT_START:
        return "after_hours"
    if et_dt.weekday() >= 5:
        return "unexpected"
    if t in _EXPECTED_SLOT_TIMES_SET:
        return "regular"
    return "unexpected"


class SessionQualityBuilder:
    """Builds one deterministic, read-only session-quality report from local DuckDB storage.

    See the module docstring and ``docs/SESSION_QUALITY.md`` for the full
    field contract and known limitations.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or Settings()
        # DuckDBManager's constructor only resolves and safety-checks the
        # database path; it never touches the filesystem or opens a
        # connection, and it never applies a migration.
        self._database_path: Path = DuckDBManager(settings=self._settings).database_path

    def build_report(
        self, symbol: str, *, session_date: str | None = None
    ) -> dict[str, Any]:
        """Build one sanitized, JSON-ready session-quality report dict.

        ``symbol`` and ``session_date`` are strictly validated and
        normalized before any DuckDB connection is opened. Raises
        ``SessionQualityValidationError`` for an invalid input, or
        ``SessionQualityError`` (sanitized) if the local database exists but
        cannot be read. A missing database file, empty database, or a
        symbol/date with no stored regular-session bars are all valid,
        non-error input -- the report's flags describe this truthfully.
        """
        normalized_symbol = self._normalize_symbol(symbol)
        requested_session_date = normalize_session_date(session_date)
        generated_at = datetime.now(UTC)

        if not self._database_path.exists():
            return _empty_report(normalized_symbol, requested_session_date, generated_at)

        try:
            connection = duckdb.connect(str(self._database_path), read_only=True)
        except duckdb.Error:
            raise SessionQualityError("Failed to open local storage for reading.") from None

        try:
            try:
                table_exists = connection.execute(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_name = 'market_bars'"
                ).fetchone()[0]
                if not table_exists:
                    result = _empty_report(normalized_symbol, requested_session_date, generated_at)
                else:
                    identity = self._resolve_identity(connection, normalized_symbol)
                    if identity is None:
                        result = _empty_report(
                            normalized_symbol, requested_session_date, generated_at
                        )
                    else:
                        effective_session_date, session_date_source = self._resolve_session_date(
                            connection, identity, requested_session_date
                        )
                        if effective_session_date is None:
                            result = _empty_report(
                                normalized_symbol,
                                requested_session_date,
                                generated_at,
                                identity=identity,
                            )
                        else:
                            bars = self._fetch_same_date_bars(
                                connection, identity, effective_session_date
                            )
                            result = _build_report_dict(
                                symbol=normalized_symbol,
                                identity=identity,
                                session_date=effective_session_date,
                                session_date_source=session_date_source,
                                bars=bars,
                                generated_at=generated_at,
                            )
            except duckdb.Error:
                raise SessionQualityError(
                    "Failed to read session quality data from local storage."
                ) from None
        except Exception:
            try:
                connection.close()
            except Exception:
                pass
            raise
        else:
            try:
                connection.close()
            except Exception:
                raise SessionQualityError(
                    "Failed to close local storage connection."
                ) from None

        return result

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        try:
            return normalize_symbol(symbol)
        except AlpacaInvalidSymbolError as exc:
            raise SessionQualityValidationError(f"Invalid symbol: {exc}") from None

    @staticmethod
    def _resolve_identity(
        connection: duckdb.DuckDBPyConnection, symbol: str
    ) -> _Identity | None:
        """Pick exactly one bar identity: whichever produced the most recent stored bar.

        Restricted to ``timeframe = '5Min'`` (this module's fixed scope).
        Never mixes bars from more than one identity in the rest of the
        report.
        """
        row = connection.execute(
            """
            SELECT provider, feed, adjustment, currency
            FROM market_bars
            WHERE symbol = ? AND timeframe = ?
            ORDER BY bar_timestamp DESC, provider, feed, adjustment, currency
            LIMIT 1
            """,
            [symbol, TIMEFRAME],
        ).fetchone()
        if row is None:
            return None
        provider, feed, adjustment, currency = row
        return _Identity(
            provider=provider, symbol=symbol, feed=feed, adjustment=adjustment, currency=currency
        )

    @staticmethod
    def _resolve_session_date(
        connection: duckdb.DuckDBPyConnection,
        identity: _Identity,
        requested_session_date: date | None,
    ) -> tuple[date | None, str]:
        if requested_session_date is not None:
            return requested_session_date, "explicit"

        rows = connection.execute(
            """
            SELECT bar_timestamp
            FROM market_bars
            WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ?
              AND adjustment = ? AND currency = ?
            """,
            [
                identity.provider,
                identity.symbol,
                TIMEFRAME,
                identity.feed,
                identity.adjustment,
                identity.currency,
            ],
        ).fetchall()

        candidate_dates: set[date] = set()
        for (bar_timestamp,) in rows:
            et_dt = bar_timestamp.replace(tzinfo=UTC).astimezone(EASTERN)
            t = et_dt.time()
            if (
                et_dt.weekday() < 5
                and REGULAR_SESSION_START <= t <= REGULAR_SESSION_LAST_SLOT_START
                and t in _EXPECTED_SLOT_TIMES_SET
            ):
                candidate_dates.add(et_dt.date())

        if not candidate_dates:
            return None, "none_available"
        return max(candidate_dates), "auto_most_recent_with_regular_bars"

    @staticmethod
    def _fetch_same_date_bars(
        connection: duckdb.DuckDBPyConnection, identity: _Identity, session_date: date
    ) -> list[_CategorizedBar]:
        """Fetch every stored bar (this identity) whose ET calendar date equals ``session_date``.

        Bounded by a UTC range comfortably wide enough to cover every
        possible America/New_York UTC offset (UTC-4 or UTC-5) for the
        requested ET calendar date, then filtered precisely in Python.
        """
        range_start = datetime.combine(session_date, time.min, tzinfo=UTC) - timedelta(hours=6)
        range_end = datetime.combine(session_date, time.min, tzinfo=UTC) + timedelta(hours=30)

        rows = connection.execute(
            """
            SELECT bar_timestamp, open, high, low, close, volume, vwap
            FROM market_bars
            WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ?
              AND adjustment = ? AND currency = ?
              AND bar_timestamp >= ? AND bar_timestamp < ?
            ORDER BY bar_timestamp ASC
            """,
            [
                identity.provider,
                identity.symbol,
                TIMEFRAME,
                identity.feed,
                identity.adjustment,
                identity.currency,
                range_start.replace(tzinfo=None),
                range_end.replace(tzinfo=None),
            ],
        ).fetchall()

        result = []
        for bar_timestamp, open_, high, low, close, volume, vwap in rows:
            et_dt = bar_timestamp.replace(tzinfo=UTC).astimezone(EASTERN)
            if et_dt.date() != session_date:
                continue
            result.append(
                _CategorizedBar(
                    bar_timestamp_utc=bar_timestamp.replace(tzinfo=UTC),
                    et_datetime=et_dt,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    volume=volume,
                    vwap=vwap,
                )
            )
        return result


def _empty_report(
    symbol: str,
    requested_session_date: date | None,
    generated_at: datetime,
    *,
    identity: _Identity | None = None,
) -> dict[str, Any]:
    source = "explicit" if requested_session_date is not None else "none_available"
    return _build_report_dict(
        symbol=symbol,
        identity=identity,
        session_date=requested_session_date,
        session_date_source=source,
        bars=[],
        generated_at=generated_at,
    )


def _session_definition() -> dict[str, Any]:
    return {
        "timezone": "America/New_York",
        "regular_session_start_et": REGULAR_SESSION_START.isoformat(timespec="minutes"),
        "regular_session_last_slot_start_et": REGULAR_SESSION_LAST_SLOT_START.isoformat(
            timespec="minutes"
        ),
        "slot_interval_minutes": SLOT_INTERVAL_MINUTES,
        "expected_slot_count": EXPECTED_SLOT_COUNT,
        "definition_type": "weekday_time_window_only",
        "limitations": (
            "Regular session is defined purely as weekday calendar dates "
            "(Monday-Friday) with 5-minute slots from 09:30 through the "
            "slot beginning 15:55, America/New_York. This is weekday/time-"
            "window logic only -- it is NOT an exchange-holiday or early-"
            "close calendar. A stored date that happens to be a U.S. "
            "market holiday or an early-close day is evaluated with this "
            "same rule and will be reported incomplete, not flagged as "
            "'no session expected.'"
        ),
    }


def _build_report_dict(
    *,
    symbol: str,
    identity: _Identity | None,
    session_date: date | None,
    session_date_source: str,
    bars: list[_CategorizedBar],
    generated_at: datetime,
) -> dict[str, Any]:
    premarket_count = 0
    after_hours_count = 0
    regular_bars: list[_CategorizedBar] = []
    unexpected_or_duplicate: list[str] = []

    if session_date is not None:
        seen_timestamps: Counter[datetime] = Counter()
        for bar in bars:
            seen_timestamps[bar.bar_timestamp_utc] += 1

        for bar in bars:
            if seen_timestamps[bar.bar_timestamp_utc] > 1:
                unexpected_or_duplicate.append(_utc_timestamp_str(bar.bar_timestamp_utc))
                continue
            category = _categorize(bar.bar_timestamp_utc, session_date)
            if category == "premarket":
                premarket_count += 1
            elif category == "after_hours":
                after_hours_count += 1
            elif category == "regular":
                regular_bars.append(bar)
            else:
                unexpected_or_duplicate.append(_utc_timestamp_str(bar.bar_timestamp_utc))

    observed_slot_times = {bar.et_datetime.time() for bar in regular_bars}
    missing_expected_timestamps_utc: list[str] = []
    if session_date is not None:
        for slot_time in _EXPECTED_SLOT_TIMES:
            if slot_time not in observed_slot_times:
                expected_et = datetime.combine(session_date, slot_time, tzinfo=EASTERN)
                expected_utc = expected_et.astimezone(UTC).replace(tzinfo=None)
                missing_expected_timestamps_utc.append(_utc_timestamp_str(expected_utc))

    observed_count = len(regular_bars)
    complete = (
        session_date is not None
        and observed_count == EXPECTED_SLOT_COUNT
        and not unexpected_or_duplicate
    )
    missing_data = observed_count == 0
    partial_session = 0 < observed_count < EXPECTED_SLOT_COUNT

    first_ts = regular_bars[0].bar_timestamp_utc if regular_bars else None
    last_ts = regular_bars[-1].bar_timestamp_utc if regular_bars else None

    session_open = None
    if regular_bars and regular_bars[0].et_datetime.time() == REGULAR_SESSION_START:
        session_open = regular_bars[0].open
    latest_close = regular_bars[-1].close if regular_bars else None

    return_pct = None
    if session_open is not None and latest_close is not None and session_open != 0:
        return_pct = ((latest_close - session_open) / session_open).quantize(
            _RETURN_DECIMAL_EXPONENT
        )

    high = max((bar.high for bar in regular_bars), default=None)
    low = min((bar.low for bar in regular_bars), default=None)
    session_range = (high - low) if (high is not None and low is not None) else None
    total_volume = sum((bar.volume for bar in regular_bars), 0) if regular_bars else None

    vwap = None
    if regular_bars and all(bar.vwap is not None for bar in regular_bars) and total_volume:
        weighted_sum = sum((bar.vwap * bar.volume for bar in regular_bars), Decimal(0))
        vwap = (weighted_sum / Decimal(total_volume)).quantize(Decimal("0.000001"))

    return {
        "generated_at_utc": _format_as_of(generated_at),
        "symbol": symbol,
        "session_date_et": session_date.isoformat() if session_date is not None else None,
        "session_date_source": session_date_source,
        "bar_provenance": {
            "provider": identity.provider if identity else None,
            "symbol": symbol,
            "timeframe": TIMEFRAME,
            "feed": identity.feed if identity else None,
            "adjustment": identity.adjustment if identity else None,
            "currency": identity.currency if identity else None,
        },
        "session_definition": _session_definition(),
        "completeness": {
            "expected_slot_count": EXPECTED_SLOT_COUNT,
            "observed_regular_session_slot_count": observed_count,
            "missing_expected_timestamps_utc": missing_expected_timestamps_utc,
            "unexpected_or_duplicate_timestamps_utc": sorted(set(unexpected_or_duplicate)),
            "complete": complete,
            "partial_session": partial_session,
            "missing_data": missing_data,
        },
        "regular_session": {
            "first_timestamp_utc": _utc_timestamp_str(first_ts) if first_ts else None,
            "last_timestamp_utc": _utc_timestamp_str(last_ts) if last_ts else None,
            "open": _decimal_str(session_open),
            "latest_close": _decimal_str(latest_close),
            "latest_close_is_full_session_close": complete,
            "return_pct": _decimal_str(return_pct),
            "high": _decimal_str(high),
            "low": _decimal_str(low),
            "range": _decimal_str(session_range),
            "total_volume": total_volume,
            "vwap": _decimal_str(vwap),
        },
        "same_date_bars": {
            "premarket_count": premarket_count,
            "after_hours_count": after_hours_count,
        },
    }
