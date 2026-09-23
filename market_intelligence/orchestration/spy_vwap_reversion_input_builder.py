"""Read-only builder: stored SPY 5-minute bars -> ``SpyVwapReversionEvaluationInput``.

This module converts bars already stored in the local ``market_bars`` table
into the exact input contract the existing offline VWAP-extension/reversion
evaluator consumes (``market_intelligence.evaluation.
spy_vwap_reversion_contracts.SpyVwapReversionEvaluationInput``, read by
``scripts/evaluate_spy_vwap_reversion.py``). **It builds an evaluation
input only.** It runs no evaluation, computes no feature, regime, outcome,
or statistic, and nothing it produces is an evaluation result or evidence of
any edge -- see ``DECISION_RULES.md`` and ``docs/OPTIONS_DECISION_WORKFLOW.md``.

Read-only end to end:

- It makes **no network request** of any kind and imports no data
  connector, model client, agent, or other orchestration module.
- It lives in ``orchestration/`` (not ``market_features/``) because it
  touches storage: the step-c regime/feature modules under
  ``market_features/`` stay pure and offline, with no DuckDB, storage,
  connector, or orchestration import (see
  ``market_intelligence/tests/test_spy_regime_engine_offline.py``). It
  consumes their contracts one way only; they never import this module.
- It opens the local database with ``duckdb.connect(path, read_only=True)``,
  reads only ``market_bars``, and never writes a row or applies a
  migration. A missing database file is an error, never created.
  ``DuckDBManager`` is reused only to resolve and path-safety-check the
  database location (its constructor opens no connection).
  ``BarRepository`` is deliberately not used: it is a write-only storage
  path with no read method.

**Exact provenance.** Only rows whose full bar identity is ``provider=alpaca``,
``symbol=SPY``, ``timeframe=5Min``, ``feed=iex``, ``adjustment=raw``,
``currency=USD`` are read -- the canonical stored SPY 5-minute dataset the
step-c regime engine is specified against (see ``DATA_CATALOG.md``,
"SPY 5-minute IEX market bars"). Rows of any other provider, symbol,
timeframe, feed, adjustment, or currency are never mixed in.

**Session filtering (weekday/time-window only, no exchange calendar).** A
session date is an America/New_York Monday-Friday calendar date. Only bars
whose America/New_York time falls in the regular ``09:30``-``16:00`` window
are kept; premarket, after-hours, and weekend bars are ignored and counted
(``outside_regular_session_bar_count``), never reproduced. A session is
included only when **all** of the following hold, checked in this fixed
order, with the first failure recorded as its single exclusion reason:

1. at least one regular-window bar is stored (``no_regular_session_bars``);
2. every regular-window bar sits exactly on the canonical 5-minute grid
   with zero seconds (``off_grid_bar``);
3. every price is present, finite, positive, and within the regime
   engine's magnitude bound, and every volume is a present, bounded,
   non-negative integer (``invalid_price_or_volume``);
4. every bar is OHLC-consistent (``ohlc_inconsistent``);
5. exactly the 78 expected slots ``09:30``-``15:55`` are present -- a
   complete, gapless session (``incomplete_session``);
6. the bars are accepted by the evaluator's own ``SessionBars`` contract
   (``session_contract_rejected`` -- a defensive backstop that the checks
   above should make unreachable).

A market holiday or early-close day is not specially recognized: it is
simply reported as ``no_regular_session_bars`` or ``incomplete_session``.
Exclusions are reported **only** as fixed-reason counts -- never prices,
timestamps, or bar contents.

**Prior-day levels are never invented.** For an included session ``D``,
``prior_day`` (high = max high, low = min low, close = last close of that
session's 78 regular bars) is supplied only when the **immediately
preceding weekday** ``P`` is itself a stored, included-quality session by
the rules above -- ``P`` may lie before ``start_date`` (it is read for this
purpose only and never emitted as a session). Without an exchange calendar
this builder cannot tell a holiday from a data gap, so it never skips back
further: if ``P`` has no stored regular bars, or fails any rule above,
``prior_day`` is ``None`` and the reason is counted.

**Point-in-time context is set honestly.** Every session carries
``same_time_historical_volume_baseline=None``, ``catalyst_state=unknown``,
``breadth_state=unavailable`` -- exactly the narrowed values the evaluator's
``SessionBars`` contract currently requires. No baseline, catalyst, or
breadth value is fabricated.

**Deterministic.** Output ordering depends only on the stored rows and the
requested dates; serializing the result with
``spy_vwap_reversion_serialization.input_to_json_str`` is byte-stable.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
from pydantic import ValidationError

from market_intelligence.config.settings import Settings
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    MAX_SESSIONS,
    SessionBars,
    SpyVwapReversionEvaluationInput,
)
from market_intelligence.market_features.spy_regime_contracts import (
    BAR_INTERVAL_MINUTES,
    EASTERN,
    REGULAR_SESSION_END,
    REGULAR_SESSION_START,
    BreadthState,
    CatalystState,
    IntradayBar,
    PriorDayLevels,
)
from market_intelligence.storage.database import DuckDBManager

# The one exact bar identity this builder reads. Mirrors the fixed
# provenance of the stored ``alpaca_bars_spy_5min`` dataset
# (``bar_repository.DEFAULT_PROVIDER`` and ``alpaca_bars.DATA_FEED`` /
# ``DATA_ADJUSTMENT`` / ``DATA_CURRENCY``); restated here, not imported, so
# this read-only module never imports a networked connector module. A test
# asserts the two stay identical.
PROVIDER = "alpaca"
SYMBOL = "SPY"
TIMEFRAME = "5Min"
FEED = "iex"
ADJUSTMENT = "raw"
CURRENCY = "USD"

EXPECTED_SESSION_BAR_COUNT = 78  # 09:30 through 15:55 inclusive, every 5 minutes

# Bounds one request's calendar span (and therefore the rows read). The
# evaluator contract separately caps the number of emitted sessions.
MAX_REQUEST_CALENDAR_DAYS = 366

# Mirrors spy_regime_contracts' own price/volume bounds, so a row the regime
# engine would reject is excluded here with a named reason instead.
_MAX_PRICE = Decimal("1000000")
_MAX_VOLUME = 10_000_000_000


class SpyVwapInputBuildError(RuntimeError):
    """Sanitized builder failure. The message is always a fixed string --
    never a database path, SQL text, raw exception, price, or timestamp."""


class SpyVwapInputValidationError(SpyVwapInputBuildError):
    """Raised for an invalid request before any database connection opens."""


class SessionExclusionReason(StrEnum):
    """Fixed reasons a weekday in the requested range is not emitted as a
    session. Each excluded weekday is counted under exactly one reason --
    the first failing rule, in the order documented in the module docstring."""

    NO_REGULAR_SESSION_BARS = "no_regular_session_bars"
    OFF_GRID_BAR = "off_grid_bar"
    INVALID_PRICE_OR_VOLUME = "invalid_price_or_volume"
    OHLC_INCONSISTENT = "ohlc_inconsistent"
    INCOMPLETE_SESSION = "incomplete_session"
    SESSION_CONTRACT_REJECTED = "session_contract_rejected"


class PriorDayStatus(StrEnum):
    """Why an included session does or does not carry ``prior_day``."""

    AVAILABLE = "available"
    PREVIOUS_WEEKDAY_NOT_STORED = "previous_weekday_not_stored"
    PREVIOUS_WEEKDAY_SESSION_EXCLUDED = "previous_weekday_session_excluded"


@dataclass(frozen=True)
class StoredBarRow:
    """One stored ``market_bars`` row, already restricted to the exact
    provenance above. Price/volume fields are nullable here only so that a
    malformed stored row is excluded with a named reason rather than crashing."""

    timestamp_utc: datetime
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None


@dataclass(frozen=True)
class InputBuildSummary:
    """Sanitized, count-only description of one build. Contains no price,
    volume, bar timestamp, database path, or bar contents."""

    start_date: date
    end_date: date
    weekdays_in_range: int
    included_session_count: int
    first_included_session: date | None
    last_included_session: date | None
    excluded_session_counts: dict[SessionExclusionReason, int]
    prior_day_counts: dict[PriorDayStatus, int]
    outside_regular_session_bar_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": SYMBOL,
            "provenance": {
                "provider": PROVIDER,
                "timeframe": TIMEFRAME,
                "feed": FEED,
                "adjustment": ADJUSTMENT,
                "currency": CURRENCY,
            },
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "weekdays_in_range": self.weekdays_in_range,
            "included_session_count": self.included_session_count,
            "first_included_session": _iso_or_none(self.first_included_session),
            "last_included_session": _iso_or_none(self.last_included_session),
            "excluded_session_counts": {
                reason.value: self.excluded_session_counts[reason]
                for reason in SessionExclusionReason
            },
            "prior_day_counts": {
                status.value: self.prior_day_counts[status] for status in PriorDayStatus
            },
            "outside_regular_session_bar_count": self.outside_regular_session_bar_count,
            "point_in_time_context": {
                "same_time_historical_volume_baseline": None,
                "catalyst_state": CatalystState.UNKNOWN.value,
                "breadth_state": BreadthState.UNAVAILABLE.value,
            },
        }


@dataclass(frozen=True)
class InputBuildResult:
    """``evaluation_input`` is ``None`` exactly when no session qualified --
    the evaluator contract requires at least one session, and none is ever
    fabricated to satisfy it."""

    evaluation_input: SpyVwapReversionEvaluationInput | None
    summary: InputBuildSummary


# --- Pure assembly -------------------------------------------------------------------


def _iso_or_none(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def previous_weekday(day: date) -> date:
    candidate = day - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def validate_date_range(start_date: date, end_date: date) -> None:
    if start_date > end_date:
        raise SpyVwapInputValidationError("start_date must not be after end_date")
    if (end_date - start_date).days + 1 > MAX_REQUEST_CALENDAR_DAYS:
        raise SpyVwapInputValidationError("requested date range is too long")


def _expected_slot_times() -> frozenset[time]:
    start = datetime.combine(date(2000, 1, 3), REGULAR_SESSION_START)
    return frozenset(
        (start + timedelta(minutes=BAR_INTERVAL_MINUTES * i)).time()
        for i in range(EXPECTED_SESSION_BAR_COUNT)
    )


_EXPECTED_SLOT_TIMES = _expected_slot_times()


def _eastern(row: StoredBarRow) -> datetime:
    return row.timestamp_utc.astimezone(EASTERN)


def _is_regular_window(et_dt: datetime) -> bool:
    return et_dt.weekday() < 5 and REGULAR_SESSION_START <= et_dt.time() < REGULAR_SESSION_END


def _is_on_grid(et_dt: datetime) -> bool:
    return (
        et_dt.minute % BAR_INTERVAL_MINUTES == 0
        and et_dt.second == 0
        and et_dt.microsecond == 0
    )


def _valid_price(value: object) -> bool:
    return (
        isinstance(value, Decimal)
        and value.is_finite()
        and Decimal(0) < value <= _MAX_PRICE
    )


def _valid_volume(value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= _MAX_VOLUME
    )


def _ohlc_consistent(row: StoredBarRow) -> bool:
    high, low, open_, close = row.high, row.low, row.open, row.close
    return high >= low and high >= open_ and high >= close and low <= open_ and low <= close


def _qualify_session(
    session_date: date, regular_rows: Sequence[StoredBarRow]
) -> SessionExclusionReason | list[IntradayBar]:
    """Apply the fixed, ordered session rules to one weekday's regular-window
    rows. Returns the validated bars, or the first failing reason."""
    if not regular_rows:
        return SessionExclusionReason.NO_REGULAR_SESSION_BARS
    if not all(_is_on_grid(_eastern(row)) for row in regular_rows):
        return SessionExclusionReason.OFF_GRID_BAR
    if not all(
        _valid_price(row.open)
        and _valid_price(row.high)
        and _valid_price(row.low)
        and _valid_price(row.close)
        and _valid_volume(row.volume)
        for row in regular_rows
    ):
        return SessionExclusionReason.INVALID_PRICE_OR_VOLUME
    if not all(_ohlc_consistent(row) for row in regular_rows):
        return SessionExclusionReason.OHLC_INCONSISTENT
    slot_times = [_eastern(row).time() for row in regular_rows]
    if len(slot_times) != EXPECTED_SESSION_BAR_COUNT or set(slot_times) != _EXPECTED_SLOT_TIMES:
        return SessionExclusionReason.INCOMPLETE_SESSION
    try:
        bars = [
            IntradayBar(
                timestamp=row.timestamp_utc,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in sorted(regular_rows, key=lambda r: r.timestamp_utc)
        ]
        SessionBars(session_date=session_date, bars=bars)
    except ValidationError:
        return SessionExclusionReason.SESSION_CONTRACT_REJECTED
    return bars


def _prior_day_levels(bars: Sequence[IntradayBar]) -> PriorDayLevels:
    return PriorDayLevels(
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=bars[-1].close,
    )


def assemble_evaluation_input(
    rows: Sequence[StoredBarRow], *, start_date: date, end_date: date
) -> InputBuildResult:
    """Pure: turn exact-provenance stored rows into an evaluation input.

    ``rows`` may include rows outside ``[start_date, end_date]`` (notably the
    immediately preceding weekday, used only for ``prior_day``); only
    weekdays inside the range are emitted or counted.
    """
    validate_date_range(start_date, end_date)

    regular_by_date: dict[date, list[StoredBarRow]] = defaultdict(list)
    outside_regular = 0
    for row in rows:
        et_dt = _eastern(row)
        if _is_regular_window(et_dt):
            regular_by_date[et_dt.date()].append(row)
        elif start_date <= et_dt.date() <= end_date:
            outside_regular += 1

    qualified: dict[date, SessionExclusionReason | list[IntradayBar]] = {}

    def qualify(day: date) -> SessionExclusionReason | list[IntradayBar]:
        if day not in qualified:
            qualified[day] = _qualify_session(day, regular_by_date.get(day, []))
        return qualified[day]

    sessions: list[SessionBars] = []
    exclusions: Counter[SessionExclusionReason] = Counter()
    prior_day_counts: Counter[PriorDayStatus] = Counter()
    weekdays_in_range = 0

    day = start_date
    while day <= end_date:
        if day.weekday() < 5:
            weekdays_in_range += 1
            outcome = qualify(day)
            if isinstance(outcome, SessionExclusionReason):
                exclusions[outcome] += 1
            else:
                predecessor = qualify(previous_weekday(day))
                if isinstance(predecessor, list):
                    prior_day = _prior_day_levels(predecessor)
                    prior_day_counts[PriorDayStatus.AVAILABLE] += 1
                elif predecessor == SessionExclusionReason.NO_REGULAR_SESSION_BARS:
                    prior_day = None
                    prior_day_counts[PriorDayStatus.PREVIOUS_WEEKDAY_NOT_STORED] += 1
                else:
                    prior_day = None
                    prior_day_counts[PriorDayStatus.PREVIOUS_WEEKDAY_SESSION_EXCLUDED] += 1
                sessions.append(
                    SessionBars(
                        session_date=day,
                        bars=outcome,
                        prior_day=prior_day,
                        same_time_historical_volume_baseline=None,
                        catalyst_state=CatalystState.UNKNOWN,
                        breadth_state=BreadthState.UNAVAILABLE,
                    )
                )
        day += timedelta(days=1)

    if len(sessions) > MAX_SESSIONS:
        raise SpyVwapInputValidationError(
            "requested range contains more complete sessions than the evaluation input allows"
        )

    summary = InputBuildSummary(
        start_date=start_date,
        end_date=end_date,
        weekdays_in_range=weekdays_in_range,
        included_session_count=len(sessions),
        first_included_session=sessions[0].session_date if sessions else None,
        last_included_session=sessions[-1].session_date if sessions else None,
        excluded_session_counts={r: exclusions[r] for r in SessionExclusionReason},
        prior_day_counts={s: prior_day_counts[s] for s in PriorDayStatus},
        outside_regular_session_bar_count=outside_regular,
    )
    evaluation_input = (
        SpyVwapReversionEvaluationInput(symbol=SYMBOL, sessions=sessions) if sessions else None
    )
    return InputBuildResult(evaluation_input=evaluation_input, summary=summary)


# --- Read-only storage access ------------------------------------------------------


def _utc_naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None)


def read_stored_bar_rows(
    database_path: Path, *, start_date: date, end_date: date
) -> list[StoredBarRow]:
    """Read exact-provenance rows covering the immediately preceding weekday
    of ``start_date`` through ``end_date`` (America/New_York), read-only.

    The UTC bounds are deliberately wider than any America/New_York offset
    can shift a calendar date; ``assemble_evaluation_input`` filters
    precisely by Eastern date.
    """
    validate_date_range(start_date, end_date)
    if not database_path.is_file():
        raise SpyVwapInputBuildError("local database does not exist")

    first_day = previous_weekday(start_date)
    range_start = datetime.combine(first_day, time.min, tzinfo=UTC) - timedelta(hours=6)
    range_end = datetime.combine(end_date, time.min, tzinfo=UTC) + timedelta(hours=30)

    try:
        connection = duckdb.connect(str(database_path), read_only=True)
    except Exception:
        raise SpyVwapInputBuildError("failed to open local storage for reading") from None
    try:
        try:
            table_exists = connection.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name = 'market_bars'"
            ).fetchone()[0]
            if not table_exists:
                raise SpyVwapInputBuildError("market_bars table is not available")
            fetched = connection.execute(
                """
                SELECT bar_timestamp, open, high, low, close, volume
                FROM market_bars
                WHERE provider = ? AND symbol = ? AND timeframe = ? AND feed = ?
                  AND adjustment = ? AND currency = ?
                  AND bar_timestamp >= ? AND bar_timestamp < ?
                ORDER BY bar_timestamp ASC
                """,
                [
                    PROVIDER,
                    SYMBOL,
                    TIMEFRAME,
                    FEED,
                    ADJUSTMENT,
                    CURRENCY,
                    _utc_naive(range_start),
                    _utc_naive(range_end),
                ],
            ).fetchall()
        except SpyVwapInputBuildError:
            raise
        except Exception:
            raise SpyVwapInputBuildError("failed to read stored bars") from None
    finally:
        try:
            connection.close()
        except Exception:
            pass

    rows = []
    for bar_timestamp, open_, high, low, close, volume in fetched:
        # market_bars.bar_timestamp is a naive TIMESTAMP holding UTC.
        if bar_timestamp.tzinfo is None:
            bar_timestamp = bar_timestamp.replace(tzinfo=UTC)
        rows.append(
            StoredBarRow(
                timestamp_utc=bar_timestamp.astimezone(UTC),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
    return rows


def build_spy_vwap_reversion_input(
    *, start_date: date, end_date: date, settings: Settings | None = None
) -> InputBuildResult:
    """Read the stored SPY 5-minute bars for ``[start_date, end_date]``
    read-only and assemble the evaluation input. See the module docstring."""
    validate_date_range(start_date, end_date)
    database_path = DuckDBManager(settings=settings or Settings()).database_path
    rows = read_stored_bar_rows(database_path, start_date=start_date, end_date=end_date)
    return assemble_evaluation_input(rows, start_date=start_date, end_date=end_date)
