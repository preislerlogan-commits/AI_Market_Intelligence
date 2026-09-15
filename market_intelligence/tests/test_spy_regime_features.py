"""Tests for market_intelligence.market_features.spy_regime_features.

``compute_features`` is a pure function; these tests never touch a network
or a database. All multi-bar fixtures use the canonical 5-minute grid
(09:30, 09:35, 09:40, ...) -- this engine accepts no other bar cadence
(see spy_regime_contracts.py and test_spy_regime_contracts.py for the
cadence-rejection tests themselves).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    OpeningRangePosition,
    PriorDayLevels,
    PriorDayRangePosition,
    RegimeEngineInput,
    TimeOfDayBucket,
)
from market_intelligence.market_features.spy_regime_features import compute_features

EASTERN = ZoneInfo("America/New_York")
SESSION_DATE = date(2026, 6, 10)  # Wednesday, EDT

_BPS_QUANT = Decimal("0.0001")
_RATIO_QUANT = Decimal("0.000001")


def et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=EASTERN)


def make_bar(
    *,
    timestamp: datetime,
    open: str,
    high: str,
    low: str,
    close: str,
    volume: int,
) -> IntradayBar:
    return IntradayBar(
        timestamp=timestamp,
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def flat_bar(day: date, hour: int, minute: int, price: str, volume: int = 1000) -> IntradayBar:
    return make_bar(
        timestamp=et(day, hour, minute),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
    )


def grid_time(day: date, step_index: int) -> datetime:
    """The America/New_York timestamp of the ``step_index``-th canonical
    5-minute bar of the regular session (0 == the 09:30 open bar)."""
    total_minutes = 30 + step_index * 5
    hour = 9 + total_minutes // 60
    minute = total_minutes % 60
    return et(day, hour, minute)


def flat_bar_n(day: date, step_index: int, price: str, volume: int = 1000) -> IntradayBar:
    """A flat OHLC bar at the ``step_index``-th canonical 5-minute mark."""
    return make_bar(
        timestamp=grid_time(day, step_index),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=volume,
    )


def make_input(
    *,
    bars: list[IntradayBar],
    session_date: date = SESSION_DATE,
    prior_day: PriorDayLevels | None = None,
    same_time_historical_volume_baseline: Decimal | None = None,
    catalyst_state: CatalystState = CatalystState.UNKNOWN,
    breadth_state: BreadthState = BreadthState.UNAVAILABLE,
) -> RegimeEngineInput:
    return RegimeEngineInput(
        session_date=session_date,
        bars=bars,
        prior_day=prior_day,
        same_time_historical_volume_baseline=same_time_historical_volume_baseline,
        catalyst_state=catalyst_state,
        breadth_state=breadth_state,
    )


# --- Cumulative VWAP -----------------------------------------------------------------


def test_session_vwap_single_bar_is_typical_price():
    bar = make_bar(
        timestamp=et(SESSION_DATE, 9, 30),
        open="500",
        high="501",
        low="499",
        close="500",
        volume=1000,
    )
    features = compute_features(make_input(bars=[bar]))
    # typical price = (501 + 499 + 500) / 3 = 500
    assert features.session_vwap == Decimal("500.000000")


def test_session_vwap_is_volume_weighted_across_bars():
    bars = [
        make_bar(
            timestamp=et(SESSION_DATE, 9, 30),
            open="500",
            high="500",
            low="500",
            close="500",
            volume=100,
        ),
        make_bar(
            timestamp=et(SESSION_DATE, 9, 35),
            open="510",
            high="510",
            low="510",
            close="510",
            volume=300,
        ),
    ]
    features = compute_features(make_input(bars=bars))
    # typical prices are 500 and 510; (500*100 + 510*300) / 400 = 507.5
    assert features.session_vwap == Decimal("507.500000")


def test_session_vwap_none_when_cumulative_volume_is_zero():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500", volume=0)]
    features = compute_features(make_input(bars=bars))
    assert features.session_vwap is None
    assert features.close_to_vwap_distance_bps is None


def test_session_vwap_becomes_available_once_volume_appears():
    bars = [
        flat_bar(SESSION_DATE, 9, 30, "500", volume=0),
        flat_bar(SESSION_DATE, 9, 35, "500", volume=100),
    ]
    features = compute_features(make_input(bars=bars))
    assert features.session_vwap == Decimal("500.000000")


# --- close_to_vwap_distance_bps -------------------------------------------------------


def test_close_to_vwap_distance_bps_computed():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500", volume=100)]
    features = compute_features(make_input(bars=bars))
    assert features.close_to_vwap_distance_bps == Decimal("0.0000")

    bars = [
        make_bar(
            timestamp=et(SESSION_DATE, 9, 30),
            open="500",
            high="505",
            low="500",
            close="505",
            volume=100,
        )
    ]
    features = compute_features(make_input(bars=bars))
    # typical price = (505+500+505)/3 = 503.333333, vwap == that (one bar).
    # distance = (505 - 503.333333) / 503.333333 * 10000
    assert features.close_to_vwap_distance_bps > Decimal("0")


# --- realized_intraday_volatility_bps / normalized_vwap_extension ---------------------


def test_volatility_none_below_minimum_bars():
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(3)]
    features = compute_features(make_input(bars=bars))
    assert features.realized_intraday_volatility_bps is None
    assert features.normalized_vwap_extension is None


def test_volatility_computed_with_enough_bars():
    prices = ["500", "501", "499", "502"]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))
    assert features.realized_intraday_volatility_bps is not None
    assert features.realized_intraday_volatility_bps > Decimal("0")


def test_volatility_zero_for_flat_prices_and_extension_is_none():
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(5)]
    features = compute_features(make_input(bars=bars))
    assert features.realized_intraday_volatility_bps == Decimal("0.0000")
    # A zero denominator must never be divided into -- extension stays None.
    assert features.normalized_vwap_extension is None


def test_normalized_extension_is_distance_over_volatility():
    prices = ["500", "501", "499", "502", "510"]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))
    assert features.normalized_vwap_extension is not None
    expected = features.close_to_vwap_distance_bps / features.realized_intraday_volatility_bps
    assert abs(features.normalized_vwap_extension - expected) < Decimal("0.001")


def test_compute_features_never_divides_by_a_nonfinite_volatility(monkeypatch):
    """normalized_vwap_extension must never divide by a non-finite
    denominator -- guarded explicitly in compute_features even though the
    public validation path (finite, bounded bar prices) cannot itself
    produce one. As a second, independent backstop, RegimeFeatures itself
    (spy_regime_contracts.py) refuses a non-finite value for any of its
    Decimal fields, so a non-finite volatility can never silently reach a
    caller either way -- it fails loudly instead."""
    import market_intelligence.market_features.spy_regime_features as module

    monkeypatch.setattr(module, "_realized_intraday_volatility_bps", lambda bars: Decimal("NaN"))
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(5)]
    with pytest.raises(ValidationError, match="realized_intraday_volatility_bps"):
        compute_features(make_input(bars=bars))


def _reference_close_to_vwap_distance_bps(bars: list[IntradayBar]) -> Decimal:
    """Independently reimplements the documented VWAP-distance formula
    (not calling any production helper) so a unit mistake in production
    would show up as a mismatch rather than being tautologically hidden."""
    cumulative_pv = Decimal(0)
    cumulative_volume = 0
    for bar in bars:
        typical_price = (bar.high + bar.low + bar.close) / Decimal(3)
        cumulative_pv += typical_price * bar.volume
        cumulative_volume += bar.volume
    vwap = cumulative_pv / Decimal(cumulative_volume)
    distance = (bars[-1].close - vwap) / vwap * Decimal(10000)
    return distance.quantize(_BPS_QUANT, rounding=ROUND_HALF_EVEN)


def _reference_realized_volatility_bps(bars: list[IntradayBar]) -> Decimal:
    """Independently reimplements the documented realized-volatility
    formula (sample stdev, ddof=1, of bar-to-bar bps returns)."""
    returns_bps = [
        (bar.close - previous.close) / previous.close * Decimal(10000)
        for previous, bar in zip(bars, bars[1:], strict=False)
    ]
    n = len(returns_bps)
    mean = sum(returns_bps) / Decimal(n)
    variance = sum((r - mean) ** 2 for r in returns_bps) / Decimal(n - 1)
    return variance.sqrt().quantize(_BPS_QUANT, rounding=ROUND_HALF_EVEN)


def test_normalized_vwap_extension_is_bps_over_bps_not_decimal_over_bps():
    """normalized_vwap_extension must be
    close_to_vwap_distance_bps / realized_intraday_volatility_bps -- both
    terms already in basis points. Reconstructing both terms independently
    of production and comparing the ratio catches a dimensionally
    inconsistent formula (e.g. a raw decimal-fraction distance divided by
    a bps volatility), which would be wrong by exactly a factor of 10,000."""
    prices = ["500", "506", "497", "512", "505", "509"]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))

    expected_distance = _reference_close_to_vwap_distance_bps(bars)
    expected_volatility = _reference_realized_volatility_bps(bars)
    expected_extension = (expected_distance / expected_volatility).quantize(
        _RATIO_QUANT, rounding=ROUND_HALF_EVEN
    )

    assert features.close_to_vwap_distance_bps == expected_distance
    assert features.realized_intraday_volatility_bps == expected_volatility
    assert features.normalized_vwap_extension == expected_extension

    # A 10,000x scaling mistake (distance left as a raw decimal fraction,
    # e.g. 0.0148 instead of 147.8 bps, divided by a bps volatility) would
    # land 10,000x away from the correct ratio -- assert the two are not
    # anywhere close to that far apart, so such a regression fails loudly.
    scaling_mistake_value = expected_extension / Decimal(10000)
    assert abs(features.normalized_vwap_extension - scaling_mistake_value) > Decimal("0.01")


# --- vwap_slope_bps --------------------------------------------------------------------


def test_vwap_slope_none_with_insufficient_lookback():
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(6)]
    features = compute_features(make_input(bars=bars))
    assert features.vwap_slope_bps is None


def test_vwap_slope_computed_once_lookback_satisfied():
    prices = [str(500 + i) for i in range(8)]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))
    # bars[-7] and bars[-1] are exactly 6 * 5 = 30 minutes apart here.
    assert features.vwap_slope_bps is not None
    assert features.vwap_slope_bps > Decimal("0")


def test_vwap_slope_none_when_lookback_window_has_an_internal_gap():
    """The lookback must be exactly 30 elapsed minutes, not merely 6 array
    positions back -- a missing bar inside the window (here, the bar at
    step index 3) must fail closed to None rather than silently computing
    a slope over more than 30 actual minutes."""
    prices = [str(500 + i) for i in range(8)]
    step_indices = [0, 1, 2, 4, 5, 6, 7]  # step index 3 (09:45) is missing
    bars = [
        flat_bar_n(SESSION_DATE, step, price)
        for step, price in zip(step_indices, prices[: len(step_indices)], strict=True)
    ]
    assert len(bars) == 7  # > VWAP_SLOPE_LOOKBACK_BARS (6), so the count check alone would pass
    features = compute_features(make_input(bars=bars))
    assert features.vwap_slope_bps is None


# --- Opening gap ------------------------------------------------------------------------


def test_opening_gap_requires_prior_day_and_0930_open_bar():
    bars = [flat_bar(SESSION_DATE, 9, 30, "505")]
    prior_day = PriorDayLevels(high=Decimal("501"), low=Decimal("498"), close=Decimal("500"))
    features = compute_features(make_input(bars=bars, prior_day=prior_day))
    assert features.opening_gap_bps == Decimal("100.0000")  # (505-500)/500 * 10000


def test_opening_gap_none_without_prior_day():
    bars = [flat_bar(SESSION_DATE, 9, 30, "505")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_gap_bps is None


def test_opening_gap_none_when_first_bar_is_not_the_0930_open():
    bars = [flat_bar(SESSION_DATE, 9, 35, "505")]
    prior_day = PriorDayLevels(high=Decimal("501"), low=Decimal("498"), close=Decimal("500"))
    features = compute_features(make_input(bars=bars, prior_day=prior_day))
    assert features.opening_gap_bps is None


# --- Opening range: exactly the 09:30/09:35/09:40 bars ---------------------------------


def _opening_range_bars() -> list[IntradayBar]:
    """The three completed 5-minute bars the opening range is defined
    over: max high == 502, min low == 499 across them."""
    return [
        make_bar(
            timestamp=et(SESSION_DATE, 9, 30), open="500", high="501", low="499", close="500",
            volume=100,
        ),
        make_bar(
            timestamp=et(SESSION_DATE, 9, 35), open="500", high="502", low="499", close="501",
            volume=100,
        ),
        make_bar(
            timestamp=et(SESSION_DATE, 9, 40), open="501", high="502", low="499", close="501",
            volume=100,
        ),
    ]


def test_opening_range_not_established_when_0935_bar_missing():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500"), flat_bar(SESSION_DATE, 9, 40, "502")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_established is False
    assert features.opening_range_high is None
    assert features.opening_range_low is None
    assert features.opening_range_position == OpeningRangePosition.INDETERMINATE


def test_opening_range_not_established_with_only_two_of_three_bars():
    # 09:30 and 09:40 present, 09:35 missing (an internal gap) -- must
    # fail closed rather than compute a range from the two bars present.
    bars = [
        flat_bar(SESSION_DATE, 9, 30, "500"),
        flat_bar(SESSION_DATE, 9, 40, "502"),
        flat_bar(SESSION_DATE, 9, 45, "505"),
    ]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_established is False
    assert features.opening_range_position == OpeningRangePosition.INDETERMINATE


def test_opening_range_indeterminate_when_no_opening_bars_observed():
    bars = [flat_bar(SESSION_DATE, 9, 50, "505")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_high is None
    assert features.opening_range_low is None
    assert features.opening_range_established is False
    assert features.opening_range_position == OpeningRangePosition.INDETERMINATE


def test_opening_range_established_once_all_three_bars_present():
    bars = [*_opening_range_bars(), flat_bar(SESSION_DATE, 9, 45, "501")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_established is True
    assert features.opening_range_high == Decimal("502")
    assert features.opening_range_low == Decimal("499")


def test_opening_range_position_above():
    bars = [*_opening_range_bars(), flat_bar(SESSION_DATE, 9, 45, "510")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_position == OpeningRangePosition.ABOVE


def test_opening_range_position_inside():
    bars = [*_opening_range_bars(), flat_bar(SESSION_DATE, 9, 45, "500")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_position == OpeningRangePosition.INSIDE


def test_opening_range_position_below():
    bars = [*_opening_range_bars(), flat_bar(SESSION_DATE, 9, 45, "490")]
    features = compute_features(make_input(bars=bars))
    assert features.opening_range_position == OpeningRangePosition.BELOW


# --- session_bars_complete / missing_interval_count -------------------------------------


def test_session_complete_for_continuous_data():
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(6)]  # 09:30..09:55, no gaps
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is True
    assert features.missing_interval_count == 0


def test_session_incomplete_for_ten_minute_spacing():
    """09:30, 09:40, 09:50, 10:00 is fully grid-aligned (every timestamp is
    a multiple of 5 minutes) but is 10-minute-spaced data, not continuous
    5-minute data -- grid alignment alone cannot catch this."""
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in (0, 2, 4, 6)]
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is False
    assert features.missing_interval_count == 3  # 09:35, 09:45, 09:55 missing


def test_session_incomplete_for_early_gap():
    # 09:30 present, 09:35 missing, 09:40 onward present.
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in (0, 2, 3, 4, 5)]
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is False
    assert features.missing_interval_count == 1


def test_session_incomplete_for_middle_gap():
    # 09:45 (step 3) missing from an otherwise-continuous run.
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in (0, 1, 2, 4, 5, 6)]
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is False
    assert features.missing_interval_count == 1


def test_session_incomplete_for_gap_immediately_before_the_latest_bar():
    # Step 4 (09:50) missing; the latest supplied bar is step 5 (09:55) --
    # the gap sits immediately before "now".
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in (0, 1, 2, 3, 5)]
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is False
    assert features.missing_interval_count == 1


def test_session_complete_for_continuous_early_session_partial_data():
    # Only the first three bars of the session exist yet (in progress) --
    # no interior gap, so this must still be reported complete.
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(3)]  # 09:30, 09:35, 09:40
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is True
    assert features.missing_interval_count == 0


def test_descriptive_features_still_computed_despite_an_incomplete_session():
    """An incomplete session is still valid input -- only the classifier
    (spy_regime_classifier.py) refuses to classify it. Feature computation
    itself keeps populating whatever descriptive features the available
    bars support."""
    bars = [flat_bar_n(SESSION_DATE, i, str(500 + i)) for i in (0, 2, 4, 6)]  # 10-min spacing
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is False
    assert features.session_vwap is not None
    assert features.close_to_vwap_distance_bps is not None
    assert features.trend_strength is not None


def test_session_incomplete_when_input_starts_mid_session():
    # Completeness is anchored to 09:30 (session open), not the first
    # supplied bar -- starting at 09:50 without the preceding bars is
    # incomplete even though the supplied bars are themselves contiguous.
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in (4, 5, 6)]  # 09:50, 09:55, 10:00
    features = compute_features(make_input(bars=bars))
    assert features.session_bars_complete is False
    assert features.missing_interval_count == 4  # 09:30, 09:35, 09:40, 09:45


# --- session_return_bps ---------------------------------------------------------------


def test_session_return_bps_computed_from_session_open():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500"), flat_bar(SESSION_DATE, 9, 35, "505")]
    features = compute_features(make_input(bars=bars))
    assert features.session_return_bps == Decimal("100.0000")


def test_session_return_bps_none_without_session_open_bar():
    bars = [flat_bar(SESSION_DATE, 9, 35, "505")]
    features = compute_features(make_input(bars=bars))
    assert features.session_return_bps is None


# --- trend_strength ---------------------------------------------------------------------


def test_trend_strength_none_with_single_bar():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500")]
    features = compute_features(make_input(bars=bars))
    assert features.trend_strength is None


def test_trend_strength_is_one_for_pure_monotonic_move():
    prices = ["500", "501", "502", "503"]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))
    assert features.trend_strength == Decimal("1.000000")


def test_trend_strength_is_negative_one_for_pure_monotonic_decline():
    prices = ["503", "502", "501", "500"]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))
    assert features.trend_strength == Decimal("-1.000000")


def test_trend_strength_zero_for_flat_closes():
    bars = [flat_bar_n(SESSION_DATE, i, "500") for i in range(4)]
    features = compute_features(make_input(bars=bars))
    assert features.trend_strength == Decimal("0")


def test_trend_strength_low_for_choppy_round_trip():
    prices = ["500", "510", "490", "500"]
    bars = [flat_bar_n(SESSION_DATE, i, price) for i, price in enumerate(prices)]
    features = compute_features(make_input(bars=bars))
    # net move is zero (500 -> 500) despite large path length.
    assert features.trend_strength == Decimal("0.000000")


# --- relative_volume ---------------------------------------------------------------------


def test_relative_volume_none_without_baseline():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500", volume=1000)]
    features = compute_features(make_input(bars=bars))
    assert features.relative_volume is None


def test_relative_volume_computed_with_baseline():
    bars = [
        flat_bar(SESSION_DATE, 9, 30, "500", volume=1000),
        flat_bar(SESSION_DATE, 9, 35, "500", volume=1000),
    ]
    features = compute_features(
        make_input(bars=bars, same_time_historical_volume_baseline=Decimal("1000"))
    )
    assert features.relative_volume == Decimal("2.000000")


# --- prior_day_range_position ------------------------------------------------------------


def test_prior_day_range_position_unavailable_without_prior_day():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500")]
    features = compute_features(make_input(bars=bars))
    assert features.prior_day_range_position == PriorDayRangePosition.UNAVAILABLE


def test_prior_day_range_position_above_prior_high():
    bars = [flat_bar(SESSION_DATE, 9, 30, "600")]
    prior_day = PriorDayLevels(high=Decimal("501"), low=Decimal("498"), close=Decimal("500"))
    features = compute_features(make_input(bars=bars, prior_day=prior_day))
    assert features.prior_day_range_position == PriorDayRangePosition.ABOVE_PRIOR_HIGH


def test_prior_day_range_position_below_prior_low():
    bars = [flat_bar(SESSION_DATE, 9, 30, "400")]
    prior_day = PriorDayLevels(high=Decimal("501"), low=Decimal("498"), close=Decimal("500"))
    features = compute_features(make_input(bars=bars, prior_day=prior_day))
    assert features.prior_day_range_position == PriorDayRangePosition.BELOW_PRIOR_LOW


def test_prior_day_range_position_inside_prior_range():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500")]
    prior_day = PriorDayLevels(high=Decimal("501"), low=Decimal("498"), close=Decimal("500"))
    features = compute_features(make_input(bars=bars, prior_day=prior_day))
    assert features.prior_day_range_position == PriorDayRangePosition.INSIDE_PRIOR_RANGE


# --- time_of_day_bucket / minutes_remaining_in_session ------------------------------------


def test_time_of_day_bucket_boundaries():
    # Every boundary below (10:00, 11:30, 14:00, 15:00) is itself on the
    # 5-minute grid, so the full set of bucket edges remains testable
    # through grid-aligned bars.
    cases = [
        ((9, 30), TimeOfDayBucket.OPEN),
        ((9, 55), TimeOfDayBucket.OPEN),
        ((10, 0), TimeOfDayBucket.MID_MORNING),
        ((11, 25), TimeOfDayBucket.MID_MORNING),
        ((11, 30), TimeOfDayBucket.MIDDAY),
        ((13, 55), TimeOfDayBucket.MIDDAY),
        ((14, 0), TimeOfDayBucket.AFTERNOON),
        ((14, 55), TimeOfDayBucket.AFTERNOON),
        ((15, 0), TimeOfDayBucket.POWER_HOUR),
        ((15, 55), TimeOfDayBucket.POWER_HOUR),
    ]
    for (hour, minute), expected_bucket in cases:
        bars = [flat_bar(SESSION_DATE, 9, 30, "500")]
        if (hour, minute) != (9, 30):
            bars.append(flat_bar(SESSION_DATE, hour, minute, "500"))
        features = compute_features(make_input(bars=bars))
        assert features.time_of_day_bucket == expected_bucket, (hour, minute)


def test_minutes_remaining_in_session_computed():
    bars = [flat_bar(SESSION_DATE, 15, 55, "500")]
    features = compute_features(make_input(bars=bars))
    assert features.minutes_remaining_in_session == 5


def test_minutes_remaining_in_session_at_open():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500")]
    features = compute_features(make_input(bars=bars))
    assert features.minutes_remaining_in_session == 390


# --- catalyst_state / breadth_state passthrough --------------------------------------------


def test_catalyst_and_breadth_state_passthrough():
    bars = [flat_bar(SESSION_DATE, 9, 30, "500")]
    features = compute_features(
        make_input(
            bars=bars,
            catalyst_state=CatalystState.ACTIVE,
            breadth_state=BreadthState.RISK_ON_BROAD,
        )
    )
    assert features.catalyst_state == CatalystState.ACTIVE
    assert features.breadth_state == BreadthState.RISK_ON_BROAD


# --- Determinism / no lookahead ---------------------------------------------------------


def test_compute_features_is_deterministic():
    bars = [flat_bar_n(SESSION_DATE, i, str(500 + i)) for i in range(10)]
    engine_input = make_input(bars=bars)
    first = compute_features(engine_input)
    second = compute_features(engine_input)
    assert first == second


def test_compute_features_never_reflects_bars_beyond_the_supplied_prefix():
    """Prove there is no lookahead: features computed from a prefix of bars
    must be identical regardless of what a *different* longer series would
    have looked like beyond that prefix -- because compute_features only
    ever receives the prefix itself."""
    prefix = [flat_bar_n(SESSION_DATE, i, str(500 + i)) for i in range(10)]

    continuation_a = prefix + [flat_bar_n(SESSION_DATE, 10, "1000")]
    continuation_b = prefix + [flat_bar_n(SESSION_DATE, 10, "1")]

    features_prefix_only = compute_features(make_input(bars=prefix))
    features_a_truncated_to_prefix = compute_features(make_input(bars=continuation_a[:10]))
    features_b_truncated_to_prefix = compute_features(make_input(bars=continuation_b[:10]))

    assert features_prefix_only == features_a_truncated_to_prefix == features_b_truncated_to_prefix
