"""Strict Pydantic v2 contracts for the offline SPY intraday regime engine.

This module defines **only data shapes** -- the validated inputs the engine
consumes and the validated outputs it produces. It contains no feature
math (see ``spy_regime_features.py``) and no classification logic (see
``spy_regime_classifier.py``); see ``docs/OPTIONS_DECISION_WORKFLOW.md``
("Intraday Regime/Setup Engine") for the design this implements, step c of
Phase 1.

Every model uses ``extra="forbid"`` and every collection/string is bounded.
This module is deliberately self-contained: it defines its own bar shape
(``IntradayBar``) rather than importing the ``Bar`` dataclass from
``market_intelligence.data_connectors.alpaca_bars`` or anything from
``market_intelligence.storage`` / ``market_intelligence.market_features.
session_quality``, because those modules import ``httpx`` and/or ``duckdb``.
This module -- and the two others that make up the engine -- must never
import a data connector, a model client, storage, an agent, orchestration,
or any networked/database dependency. See
``market_intelligence/tests/test_spy_regime_engine_offline.py`` for the
static and fresh-interpreter proof of that boundary.

**Regular session only.** Every bar accepted by ``RegimeEngineInput`` must
fall within ``09:30``-``16:00`` ``America/New_York`` on a Monday-Friday
calendar date equal to the supplied ``session_date`` -- a premarket,
after-hours, weekend, or cross-date bar is rejected by validation before
any feature is computed, never silently dropped. Like
``market_intelligence/market_features/session_quality.py``, this is a
weekday/time-window check only -- there is no U.S. exchange-holiday or
early-close calendar, so a ``session_date`` that happens to be a market
holiday is not specially rejected; it will simply be a session with no
plausible real bars.

**Canonical SPY 5-minute bars only -- grid alignment, not continuity.**
This engine consumes exactly one bar interval: ``BAR_INTERVAL_MINUTES`` (5
minutes), matching the stored ``alpaca_bars_spy_5min`` dataset (see
``DATA_CATALOG.md``). Every bar's America/New_York wall-clock timestamp
must land exactly on the regular-session 5-minute grid (``09:30``,
``09:35``, ..., ``15:55``; zero seconds/microseconds) -- a bar at any other
offset (a stray 1-minute bar, a timestamp with nonzero seconds, and so on)
is rejected here, before any feature is computed. Grid alignment is checked
on the America/New_York wall-clock time (after UTC-to-Eastern conversion),
so it is unaffected by the underlying UTC offset and gives the same answer
on either side of a DST transition.

Grid alignment is **not** a continuity guarantee, and this module makes no
claim that it rejects "mixed cadence" in general: ``09:30``, ``09:40``,
``09:45`` -- or a feed that only ever emits every other 5-minute slot -- is
just as grid-aligned as a truly continuous 5-minute series, because every
one of those timestamps individually sits on the grid. Only a bar whose own
offset is *not* a multiple of 5 minutes (a 1-minute or 10-minute-offset
stray bar, for example) is caught here. **Missing 5-minute slots are a
separate, real-world condition (a halt, a gap in upstream ingestion) that
this validator does not and cannot detect from one bar at a time, and it is
not rejected at this layer** -- rejecting the whole input would make one
missing bar invalidate an entire session, and an in-progress session is
expected to have fewer bars than a full day. Instead, ``compute_features``
(``spy_regime_features.py``) explicitly compares the supplied timestamps
against every expected 5-minute grid slot from ``09:30`` through the latest
bar and reports ``session_bars_complete`` / ``missing_interval_count`` on
every ``RegimeFeatures``; the classifier (``spy_regime_classifier.py``)
then forces both ``regime`` and ``scenario_horizon`` to ``indeterminate``
whenever ``session_bars_complete`` is ``False``, before every other
decision-order rule (including ``event_driven``) -- see both modules for
detail. Descriptive features are still computed where the underlying data
allows it; only classification refuses to run over an incomplete session.

**Unavailable is never zero.** ``prior_day``,
``same_time_historical_volume_baseline``, ``catalyst_state``, and
``breadth_state`` are the upstream-validated inputs this engine cannot
derive from price alone. When the caller cannot supply real evidence, it
must supply the explicit "unavailable"/"unknown" value (``None`` for the
first two, ``CatalystState.UNKNOWN`` / ``BreadthState.UNAVAILABLE`` for the
enums) -- never a fabricated numeric default. Every downstream feature and
classification rule that depends on one of these treats the unavailable
case as indeterminate, never as zero. ``same_time_historical_volume_baseline``
in particular must itself be computed from the same canonical 5-minute
cadence, as cumulative volume through the same elapsed-session position
(from ``09:30`` through the same wall-clock 5-minute mark on the historical
comparison day/days) as the session being classified -- this engine cannot
verify that upstream, so a baseline computed on a different cadence or a
different elapsed-session position would silently corrupt
``relative_volume`` without tripping any validator here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "spy-regime-engine-1"

# This engine has exactly one supported underlying, enforced in the type
# system (not just a runtime check) everywhere a symbol field appears.
SUPPORTED_SYMBOL = "SPY"

# The engine's one supported bar interval -- see the module docstring
# ("Canonical SPY 5-minute bars only"). Shared by this module's grid-
# alignment validation and by spy_regime_features.py's elapsed-time-based
# lookback/opening-range logic, so the two can never disagree about what
# one bar "covers".
BAR_INTERVAL_MINUTES = 5

# Bounds how many bars a single RegimeEngineInput may carry -- a full
# 09:30-16:00 regular session at the canonical 5-minute granularity is
# exactly 78 bars (390 minutes / 5); this leaves modest headroom while
# still bounding worst-case computation cost.
MAX_BARS_PER_INPUT = 90

# Regular session window, America/New_York, half-open [START, END). Shared
# by this module's own input validation and by spy_regime_features.py's
# feature math, so the two can never disagree about what "regular session"
# means. ZoneInfo resolves EST/EDT (and the DST transition dates)
# correctly for any calendar date -- there is no manual offset arithmetic.
EASTERN = ZoneInfo("America/New_York")
REGULAR_SESSION_START = time(9, 30)
REGULAR_SESSION_END = time(16, 0)

_MAX_VOLUME = 10_000_000_000
_MAX_PRICE = Decimal("1000000")


# --- Small shared field validators -----------------------------------------------


def _require_finite(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("value must be a finite number")
    return value


def _require_finite_positive(value: Decimal) -> Decimal:
    _require_finite(value)
    if value <= 0:
        raise ValueError("value must be greater than zero")
    if value > _MAX_PRICE:
        raise ValueError("value exceeds the maximum supported magnitude")
    return value


def _require_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


_FinitePositiveDecimal = Annotated[Decimal, AfterValidator(_require_finite_positive)]
_AwareUtcTimestamp = Annotated[datetime, AfterValidator(_require_utc_aware)]
_ShortToken = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]


# --- Upstream-validated enums (never inferred from price) -----------------------


class CatalystState(StrEnum):
    """Catalyst state for the session, as validated upstream (e.g. by a News
    Analyst stage). This engine never infers a catalyst from price action --
    ``ACTIVE`` is the only value that can drive an ``event_driven``
    classification, and it must come from this explicit field."""

    NONE = "none"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    UNKNOWN = "unknown"


class BreadthState(StrEnum):
    """Market-breadth context for the session, as validated upstream.
    ``UNAVAILABLE`` is first-class -- it is never defaulted to ``MIXED`` or
    any other value when breadth data was not ingested."""

    RISK_ON_BROAD = "risk_on_broad"
    RISK_OFF_BROAD = "risk_off_broad"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


# --- Fixed feature-value enums ---------------------------------------------------


class OpeningRangePosition(StrEnum):
    """Current close's position relative to the first-15-minute opening
    range. ``INDETERMINATE`` covers both "not yet established" (fewer than
    15 minutes of the session have elapsed) and "no opening-range bars
    observed"."""

    ABOVE = "above"
    INSIDE = "inside"
    BELOW = "below"
    INDETERMINATE = "indeterminate"


class PriorDayRangePosition(StrEnum):
    """Current close's position relative to the prior regular session's
    high/low. ``UNAVAILABLE`` when no validated prior-day levels were
    supplied -- never inferred or defaulted to ``INSIDE_PRIOR_RANGE``."""

    ABOVE_PRIOR_HIGH = "above_prior_high"
    BELOW_PRIOR_LOW = "below_prior_low"
    INSIDE_PRIOR_RANGE = "inside_prior_range"
    UNAVAILABLE = "unavailable"


class TimeOfDayBucket(StrEnum):
    """Fixed, provisional regular-session time-of-day phase, evaluated at
    the current (last observed) bar's America/New_York time. See
    ``spy_regime_features.py`` for the exact boundaries."""

    OPEN = "open"
    MID_MORNING = "mid_morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    POWER_HOUR = "power_hour"


# --- Fixed engine-output enums (Regime/ScenarioHorizon) -------------------------


class Regime(StrEnum):
    """The engine's sole regime classification. Exactly one value is
    produced per classification; see ``spy_regime_classifier.py`` for the
    published decision order and thresholds."""

    TREND_CONTINUATION = "trend_continuation"
    VWAP_MEAN_REVERSION = "vwap_mean_reversion"
    RANGE = "range"
    EVENT_DRIVEN = "event_driven"
    INDETERMINATE = "indeterminate"


class ScenarioHorizon(StrEnum):
    """The engine's sole scenario-horizon bucket. This is the only place in
    the Phase 1 workflow that produces a horizon -- no downstream stage may
    invent, extend, or override it (see ``DECISION_RULES.md`` and
    ``docs/OPTIONS_DECISION_WORKFLOW.md``)."""

    INTRADAY_30M = "intraday_30m"
    INTRADAY_2H = "intraday_2h"
    TO_SESSION_CLOSE = "to_session_close"
    NEXT_SESSION = "next_session"
    INDETERMINATE = "indeterminate"


# --- Input contracts --------------------------------------------------------------


class IntradayBar(BaseModel):
    """One validated, already-normalized intraday price/volume observation.

    Deliberately narrower than ``alpaca_bars.Bar``: no provider, feed,
    adjustment, currency, or retrieval provenance -- this engine consumes
    only the price/volume shape it needs, already validated by an upstream
    stage. ``volume=0`` is a legitimate, valid observation (a bar with no
    trades); it is never rejected, and this engine never treats a
    legitimately-zero volume the same as a *missing* volume (there is no
    way to omit ``volume`` at all -- it is a required field).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    timestamp: _AwareUtcTimestamp
    open: _FinitePositiveDecimal
    high: _FinitePositiveDecimal
    low: _FinitePositiveDecimal
    close: _FinitePositiveDecimal
    volume: Annotated[int, Field(ge=0, le=_MAX_VOLUME)]

    @model_validator(mode="after")
    def _check_candle_consistency(self) -> IntradayBar:
        if not (
            self.high >= self.low
            and self.high >= self.open
            and self.high >= self.close
            and self.low <= self.open
            and self.low <= self.close
        ):
            raise ValueError("bar OHLC values are inconsistent")
        return self


class PriorDayLevels(BaseModel):
    """Validated prior-regular-session high/low/close. Absent entirely
    (``RegimeEngineInput.prior_day is None``) means unavailable -- never
    zero or a carried-forward guess."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    high: _FinitePositiveDecimal
    low: _FinitePositiveDecimal
    close: _FinitePositiveDecimal

    @model_validator(mode="after")
    def _check_consistency(self) -> PriorDayLevels:
        if not (self.high >= self.low and self.high >= self.close and self.low <= self.close):
            raise ValueError("prior-day high/low/close values are inconsistent")
        return self


class RegimeEngineInput(BaseModel):
    """One validated request to compute regular-session SPY features as of
    the last bar in ``bars``.

    ``bars`` are the only bars the engine will ever see for this
    classification -- there is no way to pass a "future" bar, since the
    engine always treats ``bars[-1]`` as "now." Feeding a longer ``bars``
    list (extending later into the session) and calling again is how a
    caller advances "now"; the engine itself never looks ahead.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Literal["SPY"] = SUPPORTED_SYMBOL
    session_date: date
    bars: Annotated[list[IntradayBar], Field(min_length=1, max_length=MAX_BARS_PER_INPUT)]
    prior_day: PriorDayLevels | None = None
    same_time_historical_volume_baseline: Annotated[Decimal, Field(gt=0)] | None = None
    catalyst_state: CatalystState
    breadth_state: BreadthState

    @model_validator(mode="after")
    def _check_session_date_is_a_weekday(self) -> RegimeEngineInput:
        if self.session_date.weekday() >= 5:
            raise ValueError("session_date must be a Monday-Friday calendar date")
        return self

    @model_validator(mode="after")
    def _check_bars_are_ordered_regular_session_bars(self) -> RegimeEngineInput:
        previous_timestamp = None
        for bar in self.bars:
            if previous_timestamp is not None and bar.timestamp <= previous_timestamp:
                raise ValueError(
                    "bars must be strictly ascending by timestamp with no duplicates"
                )
            previous_timestamp = bar.timestamp

            et_dt = bar.timestamp.astimezone(EASTERN)
            if et_dt.date() != self.session_date:
                raise ValueError(
                    "every bar must fall on session_date in America/New_York"
                )
            if not (REGULAR_SESSION_START <= et_dt.time() < REGULAR_SESSION_END):
                raise ValueError(
                    "every bar must fall within the 09:30-16:00 America/New_York "
                    "regular session; premarket and after-hours bars are rejected"
                )
        return self

    @model_validator(mode="after")
    def _check_bars_are_five_minute_grid_aligned(self) -> RegimeEngineInput:
        for bar in self.bars:
            et_time = bar.timestamp.astimezone(EASTERN).time()
            if (
                et_time.minute % BAR_INTERVAL_MINUTES != 0
                or et_time.second != 0
                or et_time.microsecond != 0
            ):
                raise ValueError(
                    "every bar timestamp must be aligned to the regular-session "
                    "5-minute grid (America/New_York :00/:05/.../:55, zero "
                    "seconds) -- this engine consumes only the canonical SPY "
                    "5-minute bar dataset; a bar off that grid is rejected"
                )
        return self


# --- Output contracts --------------------------------------------------------------


class RegimeFeatures(BaseModel):
    """Deterministic, pure-function output of the feature stage.

    Every numeric field that depends on an unavailable, insufficient, or
    not-yet-computable input is ``None`` -- never a fabricated zero. See
    ``spy_regime_features.py`` for the exact formula and minimum-bar
    requirement behind each field.

    ``session_bars_complete`` / ``missing_interval_count`` report whether
    every expected 5-minute grid slot from ``09:30`` through the latest bar
    was actually observed -- grid-aligned input validation alone cannot
    catch a coarser-but-still-grid-aligned gap (e.g. ``09:30``, ``09:40``,
    ``09:45``). These two fields are always computed (never ``None``); the
    classifier (``spy_regime_classifier.py``) is what refuses to classify
    an incomplete session, not this contract -- descriptive features here
    are still populated wherever the underlying data allows it.

    ``as_of_timestamp`` is **not** ``bars[-1].timestamp``. A canonical
    Alpaca 5-minute bar timestamp identifies the bar's *start*, so a bar
    stamped ``15:30`` is only complete and observable at ``15:35``.
    ``as_of_timestamp`` is that completion time (``bars[-1].timestamp +
    BAR_INTERVAL_MINUTES``), and ``time_of_day_bucket`` /
    ``minutes_remaining_in_session`` are derived from it -- see
    ``spy_regime_features.py`` ("Bar timestamps are bar-start times; 'now'
    is the bar's end").
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Literal["SPY"]
    session_date: date
    as_of_timestamp: _AwareUtcTimestamp
    bars_observed: Annotated[int, Field(ge=1, le=MAX_BARS_PER_INPUT)]
    minutes_remaining_in_session: Annotated[int, Field(ge=0, le=390)]

    session_bars_complete: bool
    missing_interval_count: Annotated[int, Field(ge=0, le=MAX_BARS_PER_INPUT)]

    session_vwap: Decimal | None
    close_to_vwap_distance_bps: Decimal | None
    realized_intraday_volatility_bps: Decimal | None
    normalized_vwap_extension: Decimal | None
    vwap_slope_bps: Decimal | None

    opening_gap_bps: Decimal | None
    opening_range_high: Decimal | None
    opening_range_low: Decimal | None
    opening_range_established: bool
    opening_range_position: OpeningRangePosition

    session_return_bps: Decimal | None
    trend_strength: Decimal | None
    relative_volume: Decimal | None

    prior_day_range_position: PriorDayRangePosition
    time_of_day_bucket: TimeOfDayBucket

    catalyst_state: CatalystState
    breadth_state: BreadthState


class RegimeClassificationResult(BaseModel):
    """Deterministic, pure-function output of the classification stage.

    ``regime`` and ``scenario_horizon`` are the *only* two values a
    downstream stage (the future Deterministic Contract Selector / Options
    Strategy Agent) may consume for regime/horizon -- see
    ``DECISION_RULES.md``. ``rationale`` is a short, fixed-vocabulary trace
    of which decision-order rule produced the result, for auditability; it
    is never free text and never reproduces a raw input value.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Literal["SPY"]
    session_date: date
    as_of_timestamp: _AwareUtcTimestamp
    regime: Regime
    scenario_horizon: ScenarioHorizon
    rationale: Annotated[list[_ShortToken], Field(min_length=1, max_length=10)]
