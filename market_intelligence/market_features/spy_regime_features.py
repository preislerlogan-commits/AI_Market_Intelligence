"""Deterministic, pure feature computation for the offline SPY intraday
regime engine -- step c of Phase 1 (see
``docs/OPTIONS_DECISION_WORKFLOW.md``).

``compute_features`` is a pure function: ``RegimeEngineInput -> RegimeFeatures``.
It makes no network request, opens no database connection, and imports
nothing from ``market_intelligence.data_connectors``,
``market_intelligence.storage``, ``market_intelligence.agents``,
``market_intelligence.orchestration``, or ``market_intelligence.model_clients``.
Feeding the same input always produces the same output; there is no clock
read, random source, or other hidden state. See
``market_intelligence/tests/test_spy_regime_engine_offline.py`` for the
static and fresh-interpreter proof of the import boundary.

This module computes **features only** -- it never classifies a regime or a
scenario horizon (see ``spy_regime_classifier.py``), and it never describes
VWAP distance as "overvaluation" or "undervaluation" (see
``DECISION_RULES.md``, "VWAP distance is not a valuation"): every VWAP
field here is a session-relative, mechanical execution-benchmark
statistic.

**No lookahead, by construction.** Every feature is computed only from
``RegimeEngineInput.bars``, and the engine always treats ``bars[-1]`` as
"now" -- there is no field anywhere in this module's input that could carry
a bar timestamped later than "now." A caller that wants to evaluate a later
point in the same session must call again with a longer ``bars`` list.

**Unavailable is never zero.** Every field below documents the exact
condition under which it is ``None`` (unavailable/indeterminate/insufficient
history) rather than a computed value. None of those conditions is ever
silently treated as zero.

## Formulas (all in Decimal arithmetic; see ``_BPS_QUANT``/``_RATIO_QUANT``
for the fixed output precision)

- **session_bars_complete / missing_interval_count**: every expected
  5-minute grid time from ``09:30`` through the last bar's time is compared
  against the supplied bar timestamps; ``missing_interval_count`` is how
  many of those expected slots have no matching bar, and
  ``session_bars_complete`` is ``missing_interval_count == 0``. Anchored to
  the session open, not the first supplied bar, so an input that starts
  mid-session without covering everything from ``09:30`` is never reported
  complete. Always computed (never ``None``) -- this is a descriptive
  feature; it is ``spy_regime_classifier.py`` that refuses to classify
  when it is ``False``. Grid-aligned input validation alone cannot catch
  this: ``09:30``, ``09:40``, ``09:45`` is fully grid-aligned but has a
  missing ``09:35`` interval.
- **Typical price** of bar *i*: ``(high_i + low_i + close_i) / 3``.
- **Cumulative session VWAP** as of bar *k*:
  ``sum(typical_price_i * volume_i for i in 1..k) / sum(volume_i for i in 1..k)``,
  or ``None`` if cumulative volume through bar *k* is zero.
- **close_to_vwap_distance_bps**: ``(close_n - vwap_n) / vwap_n * 10000``
  where *n* is the last bar, or ``None`` if ``vwap_n`` is ``None``.
- **realized_intraday_volatility_bps**: the sample standard deviation
  (``ddof=1``) of bar-to-bar percentage returns
  (``(close_i - close_i-1) / close_i-1 * 10000`` for ``i in 2..n``),
  requiring at least ``MIN_BARS_FOR_VOLATILITY`` bars (at least 3 returns);
  otherwise ``None``.
- **normalized_vwap_extension**: ``close_to_vwap_distance_bps /
  realized_intraday_volatility_bps`` -- both terms are already in basis
  points, so this ratio is dimensionally consistent (bps / bps, a pure
  number) and is never computed as a raw decimal distance over a bps
  volatility (which would be wrong by a factor of 10,000) -- or ``None`` if
  either input is ``None`` or the volatility is exactly zero or non-finite
  (never divide by a zero/non-finite denominator).
- **vwap_slope_bps**: ``(vwap_n - vwap_n-LOOKBACK) / vwap_n-LOOKBACK * 10000``
  where the lookback is exactly ``VWAP_SLOPE_LOOKBACK_BARS`` (6) canonical
  5-minute bars *and* exactly ``VWAP_SLOPE_LOOKBACK_BARS *
  BAR_INTERVAL_MINUTES`` (30) elapsed minutes between the two endpoint
  timestamps -- both conditions are checked explicitly, so an internal gap
  (a missing bar) that makes 6 array positions span more or less than 30
  actual minutes yields ``None`` rather than a slope computed over the
  wrong elapsed time. Also ``None`` if fewer than
  ``VWAP_SLOPE_LOOKBACK_BARS + 1`` bars are available, or if either
  endpoint VWAP is itself ``None``.
- **opening_gap_bps**: ``(session_open - prior_day.close) / prior_day.close
  * 10000``, where ``session_open`` is the *first* bar's open, only when
  that first bar's America/New_York time is exactly ``09:30`` and
  ``prior_day`` was supplied; otherwise ``None``.
- **opening_range_high/low**: the opening range is defined as exactly the
  three completed canonical 5-minute bars at America/New_York ``09:30``,
  ``09:35``, and ``09:40`` -- high is the max high and low is the min low
  of those three specific bars. ``established`` is ``True`` only once a bar
  at each of those three exact times has been observed; if any one of them
  is missing (not yet elapsed, or an internal gap), ``opening_range_high``/
  ``opening_range_low`` are ``None``, ``established`` is ``False``, and
  ``opening_range_position`` is ``INDETERMINATE`` -- this fails closed
  rather than approximating the range from whatever bars happen to be
  present in the window.
- **session_return_bps**: ``(close_n - session_open) / session_open * 10000``
  (same ``session_open`` definition as the gap), or ``None``.
- **trend_strength**: the signed Kaufman-style efficiency ratio over closes,
  ``(close_n - close_1) / sum(abs(close_i - close_i-1) for i in 2..n)``,
  bounded in ``[-1, 1]`` by construction (triangle inequality); ``0`` when
  every close is identical (a genuine, computed "no movement" value, not a
  missing-input substitution); ``None`` if fewer than 2 bars are available.
- **relative_volume**: ``sum(volume_i for i in 1..n) /
  same_time_historical_volume_baseline``, or ``None`` if no baseline was
  supplied. The baseline is an opaque upstream-supplied value this module
  cannot verify; it must be cumulative volume computed on the same
  canonical 5-minute cadence through the same elapsed-session position
  (``09:30`` through the current bar's wall-clock time) -- see
  ``spy_regime_contracts.py`` ("Unavailable is never zero").
- **prior_day_range_position**: the last close vs. ``prior_day.high``/
  ``low``, or ``UNAVAILABLE`` if ``prior_day`` is ``None``.
- **time_of_day_bucket**: a fixed partition of the regular session by the
  last bar's America/New_York time -- see ``_time_of_day_bucket``.
- **minutes_remaining_in_session**: whole minutes from the last bar's
  America/New_York time to ``16:00``, floored, always available since every
  bar is already validated to be within the regular session.
"""

from __future__ import annotations

from datetime import time as _time
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from market_intelligence.market_features.spy_regime_contracts import (
    BAR_INTERVAL_MINUTES,
    EASTERN,
    REGULAR_SESSION_END,
    REGULAR_SESSION_START,
    IntradayBar,
    OpeningRangePosition,
    PriorDayLevels,
    PriorDayRangePosition,
    RegimeEngineInput,
    RegimeFeatures,
    TimeOfDayBucket,
)

# --- Fixed, provisional, documented thresholds -----------------------------------
#
# See docs/OPTIONS_DECISION_WORKFLOW.md and PROJECT_STATE.md: these are
# provisional hypotheses, not validated values. Changing a number here never
# requires touching the classifier (spy_regime_classifier.py) -- that is the
# point of keeping feature computation separate from classification.

MIN_BARS_FOR_VOLATILITY = 4  # >= 3 bar-to-bar returns

# The VWAP-slope lookback is exactly 6 canonical 5-minute bars *and* exactly
# 30 elapsed minutes between the two endpoint bars -- both are checked (see
# compute_features) so a gap can never silently turn "6 array positions
# back" into something other than 30 real minutes.
VWAP_SLOPE_LOOKBACK_BARS = 6
VWAP_SLOPE_LOOKBACK_ELAPSED = timedelta(minutes=VWAP_SLOPE_LOOKBACK_BARS * BAR_INTERVAL_MINUTES)

# The opening range is exactly these three completed 5-minute bars -- not
# "any bar observed in a time window" -- so a missing one of the three
# fails closed instead of approximating the range from fewer bars.
_OPENING_RANGE_BAR_TIMES = (_time(9, 30), _time(9, 35), _time(9, 40))

_BPS_QUANT = Decimal("0.0001")
_RATIO_QUANT = Decimal("0.000001")
_PRICE_QUANT = Decimal("0.000001")

_TEN_THOUSAND = Decimal("10000")
_THREE = Decimal("3")


def _quant(value: Decimal, exponent: Decimal) -> Decimal:
    return value.quantize(exponent, rounding=ROUND_HALF_EVEN)


def _typical_price(bar: IntradayBar) -> Decimal:
    return (bar.high + bar.low + bar.close) / _THREE


def _cumulative_vwap_series(bars: list[IntradayBar]) -> list[Decimal | None]:
    """VWAP as of each bar, in order. ``None`` at index k if cumulative
    volume through bar k is zero (never substituted with zero)."""
    series: list[Decimal | None] = []
    cumulative_pv = Decimal(0)
    cumulative_volume = 0
    for bar in bars:
        cumulative_pv += _typical_price(bar) * bar.volume
        cumulative_volume += bar.volume
        if cumulative_volume == 0:
            series.append(None)
        else:
            series.append(cumulative_pv / Decimal(cumulative_volume))
    return series


def _bps_distance(numerator: Decimal, reference: Decimal) -> Decimal | None:
    if reference == 0:
        return None
    return _quant((numerator - reference) / reference * _TEN_THOUSAND, _BPS_QUANT)


def _realized_intraday_volatility_bps(bars: list[IntradayBar]) -> Decimal | None:
    if len(bars) < MIN_BARS_FOR_VOLATILITY:
        return None
    returns_bps: list[Decimal] = []
    for previous_bar, bar in zip(bars, bars[1:], strict=False):
        if previous_bar.close == 0:
            return None
        returns_bps.append((bar.close - previous_bar.close) / previous_bar.close * _TEN_THOUSAND)
    n = len(returns_bps)
    mean = sum(returns_bps) / Decimal(n)
    variance = sum((r - mean) ** 2 for r in returns_bps) / Decimal(n - 1)
    if variance < 0:
        return None
    return _quant(variance.sqrt(), _BPS_QUANT)


def _trend_strength(bars: list[IntradayBar]) -> Decimal | None:
    if len(bars) < 2:
        return None
    closes = [bar.close for bar in bars]
    net_move = closes[-1] - closes[0]
    path_length = sum(abs(b - a) for a, b in zip(closes, closes[1:], strict=False))
    if path_length == 0:
        return Decimal(0)
    return _quant(net_move / path_length, _RATIO_QUANT)


def _expected_grid_times(last_time: _time) -> list[_time]:
    """Every canonical 5-minute grid time from 09:30 through ``last_time``,
    inclusive. Anchored to the session open (not the first supplied bar),
    so an input that starts mid-session cannot report itself complete."""
    start_minutes = REGULAR_SESSION_START.hour * 60 + REGULAR_SESSION_START.minute
    last_minutes = last_time.hour * 60 + last_time.minute
    times = []
    for total_minutes in range(start_minutes, last_minutes + 1, BAR_INTERVAL_MINUTES):
        hour, minute = divmod(total_minutes, 60)
        times.append(_time(hour, minute))
    return times


def _session_completeness(bars: list[IntradayBar]) -> tuple[bool, int]:
    """Compare the supplied bar timestamps against every expected 5-minute
    grid slot from 09:30 through the latest bar. Grid-aligned input
    validation (spy_regime_contracts.py) only checks that each individual
    bar sits on the grid -- it cannot detect a coarser-but-still-aligned
    gap (e.g. 09:30, 09:40, 09:45, skipping 09:35). This does."""
    last_et_time = bars[-1].timestamp.astimezone(EASTERN).time()
    observed_times = {bar.timestamp.astimezone(EASTERN).time() for bar in bars}
    expected_times = _expected_grid_times(last_et_time)
    missing_interval_count = sum(1 for t in expected_times if t not in observed_times)
    return missing_interval_count == 0, missing_interval_count


def _opening_range(
    bars: list[IntradayBar],
) -> tuple[Decimal | None, Decimal | None, bool]:
    """The opening range is exactly the three completed 5-minute bars at
    09:30, 09:35, and 09:40 America/New_York -- not any bar merely observed
    inside that window. Unavailable (``None, None, False``) until a bar at
    each of those three exact times has been observed; a gap at any one of
    them fails closed instead of approximating from the others."""
    bars_by_time = {
        bar.timestamp.astimezone(EASTERN).time(): bar
        for bar in bars
        if bar.timestamp.astimezone(EASTERN).time() in _OPENING_RANGE_BAR_TIMES
    }
    if not all(t in bars_by_time for t in _OPENING_RANGE_BAR_TIMES):
        return None, None, False
    opening_bars = [bars_by_time[t] for t in _OPENING_RANGE_BAR_TIMES]
    high = max(bar.high for bar in opening_bars)
    low = min(bar.low for bar in opening_bars)
    return high, low, True


def _opening_range_position(
    close: Decimal, high: Decimal | None, low: Decimal | None, established: bool
) -> OpeningRangePosition:
    if not established or high is None or low is None:
        return OpeningRangePosition.INDETERMINATE
    if close > high:
        return OpeningRangePosition.ABOVE
    if close < low:
        return OpeningRangePosition.BELOW
    return OpeningRangePosition.INSIDE


def _session_open(bars: list[IntradayBar]) -> Decimal | None:
    first_bar = bars[0]
    if first_bar.timestamp.astimezone(EASTERN).time() != REGULAR_SESSION_START:
        return None
    return first_bar.open


def _prior_day_range_position(
    close: Decimal, prior_day: PriorDayLevels | None
) -> PriorDayRangePosition:
    if prior_day is None:
        return PriorDayRangePosition.UNAVAILABLE
    if close > prior_day.high:
        return PriorDayRangePosition.ABOVE_PRIOR_HIGH
    if close < prior_day.low:
        return PriorDayRangePosition.BELOW_PRIOR_LOW
    return PriorDayRangePosition.INSIDE_PRIOR_RANGE


def _time_of_day_bucket(current_time: _time) -> TimeOfDayBucket:
    if current_time < _time(10, 0):
        return TimeOfDayBucket.OPEN
    if current_time < _time(11, 30):
        return TimeOfDayBucket.MID_MORNING
    if current_time < _time(14, 0):
        return TimeOfDayBucket.MIDDAY
    if current_time < _time(15, 0):
        return TimeOfDayBucket.AFTERNOON
    return TimeOfDayBucket.POWER_HOUR


def _minutes_remaining_in_session(current_time: _time) -> int:
    end_minutes = REGULAR_SESSION_END.hour * 60 + REGULAR_SESSION_END.minute
    current_minutes = current_time.hour * 60 + current_time.minute + current_time.second / 60
    remaining = end_minutes - current_minutes
    return max(0, int(remaining))


def compute_features(engine_input: RegimeEngineInput) -> RegimeFeatures:
    """Compute one deterministic ``RegimeFeatures`` from a validated
    ``RegimeEngineInput``. Pure: no I/O, no clock read, no randomness."""
    bars = engine_input.bars
    last_bar = bars[-1]
    last_et = last_bar.timestamp.astimezone(EASTERN)
    current_time = last_et.time()

    session_bars_complete, missing_interval_count = _session_completeness(bars)

    vwap_series = _cumulative_vwap_series(bars)
    session_vwap = vwap_series[-1]

    close_to_vwap_distance_bps = (
        _bps_distance(last_bar.close, session_vwap) if session_vwap is not None else None
    )

    realized_intraday_volatility_bps = _realized_intraday_volatility_bps(bars)

    normalized_vwap_extension: Decimal | None = None
    if (
        close_to_vwap_distance_bps is not None
        and realized_intraday_volatility_bps is not None
        and realized_intraday_volatility_bps.is_finite()
        and realized_intraday_volatility_bps != 0
    ):
        normalized_vwap_extension = _quant(
            close_to_vwap_distance_bps / realized_intraday_volatility_bps, _RATIO_QUANT
        )

    vwap_slope_bps: Decimal | None = None
    if len(bars) > VWAP_SLOPE_LOOKBACK_BARS:
        lookback_index = -1 - VWAP_SLOPE_LOOKBACK_BARS
        earlier_bar = bars[lookback_index]
        elapsed = last_bar.timestamp - earlier_bar.timestamp
        if elapsed == VWAP_SLOPE_LOOKBACK_ELAPSED:
            earlier_vwap = vwap_series[lookback_index]
            if earlier_vwap is not None and session_vwap is not None:
                vwap_slope_bps = _bps_distance(session_vwap, earlier_vwap)

    session_open = _session_open(bars)
    opening_gap_bps: Decimal | None = None
    if session_open is not None and engine_input.prior_day is not None:
        opening_gap_bps = _bps_distance(session_open, engine_input.prior_day.close)

    opening_range_high, opening_range_low, opening_range_established = _opening_range(bars)
    opening_range_position = _opening_range_position(
        last_bar.close, opening_range_high, opening_range_low, opening_range_established
    )

    session_return_bps: Decimal | None = None
    if session_open is not None:
        session_return_bps = _bps_distance(last_bar.close, session_open)

    trend_strength = _trend_strength(bars)

    relative_volume: Decimal | None = None
    if engine_input.same_time_historical_volume_baseline is not None:
        cumulative_volume = sum(bar.volume for bar in bars)
        relative_volume = _quant(
            Decimal(cumulative_volume) / engine_input.same_time_historical_volume_baseline,
            _RATIO_QUANT,
        )

    prior_day_range_position = _prior_day_range_position(last_bar.close, engine_input.prior_day)
    time_of_day_bucket = _time_of_day_bucket(current_time)
    minutes_remaining_in_session = _minutes_remaining_in_session(current_time)

    return RegimeFeatures(
        symbol=engine_input.symbol,
        session_date=engine_input.session_date,
        as_of_timestamp=last_bar.timestamp,
        bars_observed=len(bars),
        minutes_remaining_in_session=minutes_remaining_in_session,
        session_bars_complete=session_bars_complete,
        missing_interval_count=missing_interval_count,
        session_vwap=(_quant(session_vwap, _PRICE_QUANT) if session_vwap is not None else None),
        close_to_vwap_distance_bps=close_to_vwap_distance_bps,
        realized_intraday_volatility_bps=realized_intraday_volatility_bps,
        normalized_vwap_extension=normalized_vwap_extension,
        vwap_slope_bps=vwap_slope_bps,
        opening_gap_bps=opening_gap_bps,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        opening_range_established=opening_range_established,
        opening_range_position=opening_range_position,
        session_return_bps=session_return_bps,
        trend_strength=trend_strength,
        relative_volume=relative_volume,
        prior_day_range_position=prior_day_range_position,
        time_of_day_bucket=time_of_day_bucket,
        catalyst_state=engine_input.catalyst_state,
        breadth_state=engine_input.breadth_state,
    )
