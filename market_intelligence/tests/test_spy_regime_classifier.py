"""Tests for market_intelligence.market_features.spy_regime_classifier.

These construct ``RegimeFeatures`` directly (bypassing bar-level feature
math, which is covered by test_spy_regime_features.py) so each regime and
horizon rule can be exercised in isolation, plus end-to-end tests through
compute_features + classify for realistic session shapes. Nothing here
touches a network or a database.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from market_intelligence.market_features.spy_regime_classifier import (
    DEFAULT_THRESHOLDS,
    RegimeThresholds,
    classify,
    classify_batch,
    classify_horizon,
    classify_regime,
)
from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    OpeningRangePosition,
    PriorDayRangePosition,
    Regime,
    RegimeEngineInput,
    RegimeFeatures,
    ScenarioHorizon,
    TimeOfDayBucket,
)
from market_intelligence.market_features.spy_regime_features import compute_features

EASTERN = ZoneInfo("America/New_York")
SESSION_DATE = date(2026, 6, 10)


def et(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN)


def base_features(**overrides) -> RegimeFeatures:
    values = {
        "symbol": "SPY",
        "session_date": SESSION_DATE,
        "as_of_timestamp": et(SESSION_DATE, 11, 0),
        "bars_observed": 20,
        "minutes_remaining_in_session": 300,
        "session_bars_complete": True,
        "missing_interval_count": 0,
        "session_vwap": Decimal("500.000000"),
        "close_to_vwap_distance_bps": Decimal("0.0000"),
        "realized_intraday_volatility_bps": Decimal("5.0000"),
        "normalized_vwap_extension": Decimal("0.000000"),
        "vwap_slope_bps": Decimal("0.0000"),
        "opening_gap_bps": None,
        "opening_range_high": Decimal("501"),
        "opening_range_low": Decimal("499"),
        "opening_range_established": True,
        "opening_range_position": OpeningRangePosition.INSIDE,
        "session_return_bps": Decimal("0.0000"),
        "trend_strength": Decimal("0.000000"),
        "relative_volume": Decimal("1.000000"),
        "prior_day_range_position": PriorDayRangePosition.INSIDE_PRIOR_RANGE,
        "time_of_day_bucket": TimeOfDayBucket.MID_MORNING,
        "catalyst_state": CatalystState.NONE,
        "breadth_state": BreadthState.MIXED,
    }
    values.update(overrides)
    return RegimeFeatures(**values)


# --- Session completeness gate (checked before every other rule) ------------------------


def test_incomplete_session_is_indeterminate_regardless_of_otherwise_qualifying_signals():
    """A session with a missing 5-minute interval must classify as
    indeterminate even when every other trend-continuation condition is
    satisfied -- the completeness gate runs before any other rule."""
    features = base_features(
        session_bars_complete=False,
        missing_interval_count=1,
        trend_strength=Decimal("0.9"),
        vwap_slope_bps=Decimal("10"),
        opening_range_established=True,
        opening_range_position=OpeningRangePosition.ABOVE,
        close_to_vwap_distance_bps=Decimal("50"),
    )
    regime, rationale = classify_regime(features)
    assert regime == Regime.INDETERMINATE
    assert rationale == ["session_incomplete"]


def test_incomplete_session_overrides_active_catalyst_event_driven():
    """The completeness gate is checked before event-driven -- an active
    catalyst can never produce event_driven over an incomplete session."""
    features = base_features(
        session_bars_complete=False,
        missing_interval_count=2,
        catalyst_state=CatalystState.ACTIVE,
    )
    regime, rationale = classify_regime(features)
    assert regime == Regime.INDETERMINATE
    assert rationale == ["session_incomplete"]
    assert regime != Regime.EVENT_DRIVEN


def test_incomplete_session_horizon_is_indeterminate_even_with_a_non_indeterminate_regime():
    """classify_horizon checks session_bars_complete directly against the
    features, independent of whatever regime value it is called with."""
    features = base_features(session_bars_complete=False, missing_interval_count=3)
    horizon, rationale = classify_horizon(Regime.EVENT_DRIVEN, features)
    assert horizon == ScenarioHorizon.INDETERMINATE
    assert rationale == ["horizon_session_incomplete"]


def test_incomplete_session_end_to_end_via_classify():
    features = base_features(
        session_bars_complete=False,
        missing_interval_count=1,
        catalyst_state=CatalystState.ACTIVE,
        minutes_remaining_in_session=200,
    )
    result = classify(features)
    assert result.regime == Regime.INDETERMINATE
    assert result.scenario_horizon == ScenarioHorizon.INDETERMINATE
    assert result.rationale == ["session_incomplete", "horizon_session_incomplete"]


def test_complete_session_is_not_gated():
    features = base_features(session_bars_complete=True, missing_interval_count=0)
    regime, rationale = classify_regime(features)
    assert rationale != ["session_incomplete"]


# --- Event-driven -----------------------------------------------------------------------


def test_event_driven_from_catalyst_alone_regardless_of_price_features():
    features = base_features(catalyst_state=CatalystState.ACTIVE, trend_strength=None)
    regime, rationale = classify_regime(features)
    assert regime == Regime.EVENT_DRIVEN
    assert rationale == ["catalyst_active"]


def test_event_driven_takes_precedence_over_trend_continuation_conditions():
    features = base_features(
        catalyst_state=CatalystState.ACTIVE,
        trend_strength=Decimal("0.9"),
        vwap_slope_bps=Decimal("10"),
        close_to_vwap_distance_bps=Decimal("50"),
        opening_range_position=OpeningRangePosition.ABOVE,
    )
    regime, _ = classify_regime(features)
    assert regime == Regime.EVENT_DRIVEN


def test_catalyst_scheduled_or_unknown_does_not_trigger_event_driven():
    for state in (CatalystState.NONE, CatalystState.SCHEDULED, CatalystState.UNKNOWN):
        features = base_features(catalyst_state=state)
        regime, _ = classify_regime(features)
        assert regime != Regime.EVENT_DRIVEN


# --- Insufficient evidence ---------------------------------------------------------------


def test_insufficient_history_is_indeterminate():
    features = base_features(trend_strength=None)
    regime, rationale = classify_regime(features)
    assert regime == Regime.INDETERMINATE
    assert rationale == ["insufficient_trend_data"]


# --- Trend continuation -------------------------------------------------------------------


def _trend_up_features(**overrides) -> RegimeFeatures:
    values = {
        "trend_strength": Decimal("0.8"),
        "vwap_slope_bps": Decimal("10"),
        "opening_range_established": True,
        "opening_range_position": OpeningRangePosition.ABOVE,
        "close_to_vwap_distance_bps": Decimal("50"),
    }
    values.update(overrides)
    return base_features(**values)


def test_trend_continuation_when_all_signals_aligned_upward():
    features = _trend_up_features()
    regime, rationale = classify_regime(features)
    assert regime == Regime.TREND_CONTINUATION
    assert rationale == ["trend_structure_vwap_aligned"]


def test_trend_continuation_when_all_signals_aligned_downward():
    features = _trend_up_features(
        trend_strength=Decimal("-0.8"),
        vwap_slope_bps=Decimal("-10"),
        opening_range_position=OpeningRangePosition.BELOW,
        close_to_vwap_distance_bps=Decimal("-50"),
    )
    regime, _ = classify_regime(features)
    assert regime == Regime.TREND_CONTINUATION


def test_trend_continuation_fails_when_opening_range_not_established():
    features = _trend_up_features(opening_range_established=False)
    regime, _ = classify_regime(features)
    assert regime != Regime.TREND_CONTINUATION


def test_trend_continuation_fails_when_vwap_slope_opposes_trend():
    features = _trend_up_features(vwap_slope_bps=Decimal("-10"))
    regime, _ = classify_regime(features)
    assert regime != Regime.TREND_CONTINUATION


def test_trend_continuation_fails_when_extension_opposes_trend_direction():
    features = _trend_up_features(close_to_vwap_distance_bps=Decimal("-50"))
    regime, _ = classify_regime(features)
    assert regime != Regime.TREND_CONTINUATION


def test_trend_continuation_fails_when_trend_strength_too_weak():
    features = _trend_up_features(trend_strength=Decimal("0.1"))
    regime, _ = classify_regime(features)
    assert regime != Regime.TREND_CONTINUATION


# --- VWAP mean reversion ------------------------------------------------------------------


def _reversion_features(**overrides) -> RegimeFeatures:
    values = {
        "normalized_vwap_extension": Decimal("2.0"),
        "close_to_vwap_distance_bps": Decimal("40"),
        "trend_strength": Decimal("0.1"),
        "vwap_slope_bps": Decimal("1"),
        "relative_volume": Decimal("1.0"),
        "opening_range_established": True,
        "opening_range_position": OpeningRangePosition.INSIDE,
        "time_of_day_bucket": TimeOfDayBucket.MIDDAY,
    }
    values.update(overrides)
    return base_features(**values)


def test_mean_reversion_with_extension_plus_two_corroborators():
    features = _reversion_features()
    regime, rationale = classify_regime(features)
    assert regime == Regime.VWAP_MEAN_REVERSION
    assert rationale == ["extension_with_corroborators"]


def test_large_extension_alone_is_indeterminate_not_reversion():
    """A large VWAP extension with no corroborators and an otherwise
    unremarkable session must never, by itself, produce a reversion call
    (see DECISION_RULES.md, 'A large VWAP extension alone is not a trade
    signal')."""
    features = base_features(
        normalized_vwap_extension=Decimal("5.0"),
        close_to_vwap_distance_bps=Decimal("80"),
        trend_strength=Decimal("0.1"),
        vwap_slope_bps=Decimal("10"),  # fails the "VWAP roughly flat" corroborator
        relative_volume=Decimal("2.0"),  # fails the "volume not elevated" corroborator
        opening_range_established=True,
        opening_range_position=OpeningRangePosition.ABOVE,  # fails "no breakout" corroborator
        time_of_day_bucket=TimeOfDayBucket.OPEN,  # fails "not the open" corroborator
    )
    regime, rationale = classify_regime(features)
    assert regime == Regime.INDETERMINATE
    assert rationale == ["no_rule_matched"]


def test_mean_reversion_fails_with_only_one_corroborator():
    features = _reversion_features(
        relative_volume=Decimal("2.0"),  # breaks corroborator 2
        opening_range_position=OpeningRangePosition.ABOVE,  # breaks corroborator 3
        time_of_day_bucket=TimeOfDayBucket.OPEN,  # breaks corroborator 4
        # vwap_slope_bps stays flat: corroborator 1 (the only one) still holds.
    )
    regime, _ = classify_regime(features)
    assert regime != Regime.VWAP_MEAN_REVERSION


def test_mean_reversion_fails_on_a_trend_day():
    """The engine must distinguish trend days from reversion conditions --
    a strong trend blocks reversion even with a large extension and
    corroborators satisfied."""
    features = _reversion_features(trend_strength=Decimal("0.9"))
    regime, _ = classify_regime(features)
    assert regime != Regime.VWAP_MEAN_REVERSION


def test_mean_reversion_fails_below_extension_threshold():
    features = _reversion_features(normalized_vwap_extension=Decimal("0.5"))
    regime, _ = classify_regime(features)
    assert regime != Regime.VWAP_MEAN_REVERSION


# --- Range -----------------------------------------------------------------------------


def test_range_when_low_trend_and_contained_extension():
    features = base_features(
        trend_strength=Decimal("0.05"),
        normalized_vwap_extension=Decimal("0.3"),
    )
    regime, rationale = classify_regime(features)
    assert regime == Regime.RANGE
    assert rationale == ["low_trend_contained_extension"]


def test_range_requires_extension_available():
    features = base_features(
        trend_strength=Decimal("0.05"),
        normalized_vwap_extension=None,
    )
    regime, _ = classify_regime(features)
    assert regime == Regime.INDETERMINATE


# --- Conflicting / insufficient evidence -> indeterminate ------------------------------


def test_conflicting_evidence_is_indeterminate():
    # Moderate trend strength: too weak for continuation, too strong for
    # range or reversion, and no catalyst -- nothing fires.
    features = base_features(
        trend_strength=Decimal("0.45"),
        normalized_vwap_extension=Decimal("0.6"),
        vwap_slope_bps=Decimal("1"),
    )
    regime, rationale = classify_regime(features)
    assert regime == Regime.INDETERMINATE
    assert rationale == ["no_rule_matched"]


# --- Scenario horizon ---------------------------------------------------------------------


def test_horizon_indeterminate_when_regime_indeterminate():
    features = base_features(trend_strength=None)
    horizon, rationale = classify_horizon(Regime.INDETERMINATE, features)
    assert horizon == ScenarioHorizon.INDETERMINATE
    assert rationale == ["horizon_indeterminate_regime"]


def test_horizon_indeterminate_when_no_time_remaining():
    features = base_features(minutes_remaining_in_session=0)
    horizon, rationale = classify_horizon(Regime.TREND_CONTINUATION, features)
    assert horizon == ScenarioHorizon.INDETERMINATE
    assert rationale == ["horizon_no_time_remaining"]


def test_horizon_event_driven_30m_with_enough_time():
    features = base_features(minutes_remaining_in_session=60)
    horizon, _ = classify_horizon(Regime.EVENT_DRIVEN, features)
    assert horizon == ScenarioHorizon.INTRADAY_30M


def test_horizon_event_driven_falls_back_to_close_near_end_of_session():
    features = base_features(minutes_remaining_in_session=3)
    horizon, _ = classify_horizon(Regime.EVENT_DRIVEN, features)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE


def test_horizon_trend_continuation_2h_with_enough_time():
    features = base_features(minutes_remaining_in_session=120)
    horizon, _ = classify_horizon(Regime.TREND_CONTINUATION, features)
    assert horizon == ScenarioHorizon.INTRADAY_2H


def test_horizon_trend_continuation_falls_back_to_close_late_in_session():
    features = base_features(minutes_remaining_in_session=20)
    horizon, _ = classify_horizon(Regime.TREND_CONTINUATION, features)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE


def test_horizon_mean_reversion_30m_with_enough_time():
    features = base_features(minutes_remaining_in_session=60)
    horizon, _ = classify_horizon(Regime.VWAP_MEAN_REVERSION, features)
    assert horizon == ScenarioHorizon.INTRADAY_30M


def test_horizon_mean_reversion_falls_back_to_close_near_end_of_session():
    features = base_features(minutes_remaining_in_session=3)
    horizon, _ = classify_horizon(Regime.VWAP_MEAN_REVERSION, features)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE


def test_horizon_range_is_always_next_session():
    features = base_features(minutes_remaining_in_session=45)
    horizon, rationale = classify_horizon(Regime.RANGE, features)
    assert horizon == ScenarioHorizon.NEXT_SESSION
    assert rationale == ["range_next_session"]


# --- classify() end-to-end ------------------------------------------------------------------


def test_classify_combines_regime_and_horizon_with_full_rationale():
    features = _trend_up_features(minutes_remaining_in_session=200)
    result = classify(features)
    assert result.regime == Regime.TREND_CONTINUATION
    assert result.scenario_horizon == ScenarioHorizon.INTRADAY_2H
    assert result.rationale == ["trend_structure_vwap_aligned", "trend_continuation_2h"]
    assert result.symbol == "SPY"
    assert result.session_date == SESSION_DATE


# --- Determinism / batch order preservation -------------------------------------------------


def _flat_bar(hour: int, minute: int, price: str, volume: int = 1000) -> IntradayBar:
    return IntradayBar(
        timestamp=et(SESSION_DATE, hour, minute),
        open=Decimal(price),
        high=Decimal(price),
        low=Decimal(price),
        close=Decimal(price),
        volume=volume,
    )


def _synthetic_input(session_date: date, seed_price: int) -> RegimeEngineInput:
    base = datetime(session_date.year, session_date.month, session_date.day, 9, 30, tzinfo=EASTERN)
    bars = []
    price = seed_price
    for i in range(10):
        ts = base + timedelta(minutes=5 * i)
        bars.append(
            IntradayBar(
                timestamp=ts,
                open=Decimal(price),
                high=Decimal(price + 1),
                low=Decimal(price - 1),
                close=Decimal(price),
                volume=1000 + i,
            )
        )
        price += 1
    return RegimeEngineInput(
        session_date=session_date,
        bars=bars,
        prior_day=None,
        same_time_historical_volume_baseline=None,
        catalyst_state=CatalystState.UNKNOWN,
        breadth_state=BreadthState.UNAVAILABLE,
    )


def test_classify_is_deterministic_for_repeated_calls():
    features = _trend_up_features()
    first = classify(features)
    second = classify(features)
    assert first == second


def test_classify_batch_preserves_input_order_and_is_deterministic():
    inputs = [
        _synthetic_input(SESSION_DATE, 500),
        _synthetic_input(date(2026, 6, 11), 300),
        _synthetic_input(date(2026, 6, 12), 700),
    ]
    results_a = classify_batch(inputs)
    results_b = classify_batch(inputs)

    assert [r.session_date for r in results_a] == [
        SESSION_DATE,
        date(2026, 6, 11),
        date(2026, 6, 12),
    ]
    assert results_a == results_b


def test_classify_batch_empty_list_returns_empty_list():
    assert classify_batch([]) == []


# --- Session completeness gate, end-to-end via compute_features -------------------------


def test_end_to_end_active_catalyst_with_a_gap_stays_indeterminate():
    """A real, grid-aligned but 10-minute-spaced bar series with an active
    catalyst must classify as indeterminate, not event_driven, once run
    through the full compute_features -> classify pipeline."""
    bars = [_flat_bar(9, 30, "500"), _flat_bar(9, 40, "501"), _flat_bar(9, 50, "502")]
    engine_input = RegimeEngineInput(
        session_date=SESSION_DATE,
        bars=bars,
        prior_day=None,
        same_time_historical_volume_baseline=None,
        catalyst_state=CatalystState.ACTIVE,
        breadth_state=BreadthState.UNAVAILABLE,
    )
    features = compute_features(engine_input)
    assert features.session_bars_complete is False
    result = classify(features)
    assert result.regime == Regime.INDETERMINATE
    assert result.scenario_horizon == ScenarioHorizon.INDETERMINATE
    assert result.rationale == ["session_incomplete", "horizon_session_incomplete"]


def test_end_to_end_otherwise_qualifying_trend_with_a_gap_stays_indeterminate():
    """A clean, strongly monotonic uptrend -- exactly the shape that would
    otherwise drive trend_continuation once an opening range and VWAP
    slope are established -- must still classify as indeterminate when an
    interior 5-minute interval (09:45) is missing."""
    step_minutes = [0, 5, 10, 15, 25, 30, 35, 40]  # 20 (09:50) is missing
    bars = [
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30) + timedelta(minutes=m),
            open=Decimal(str(500 + i)),
            high=Decimal(str(500 + i)),
            low=Decimal(str(500 + i)),
            close=Decimal(str(500 + i)),
            volume=1000,
        )
        for i, m in enumerate(step_minutes)
    ]
    engine_input = RegimeEngineInput(
        session_date=SESSION_DATE,
        bars=bars,
        prior_day=None,
        same_time_historical_volume_baseline=None,
        catalyst_state=CatalystState.NONE,
        breadth_state=BreadthState.UNAVAILABLE,
    )
    features = compute_features(engine_input)
    assert features.session_bars_complete is False
    assert features.trend_strength == Decimal("1.000000")  # a pure monotonic move
    result = classify(features)
    assert result.regime == Regime.INDETERMINATE
    assert result.rationale == ["session_incomplete", "horizon_session_incomplete"]


def test_default_thresholds_are_the_documented_values():
    assert DEFAULT_THRESHOLDS.extension_threshold_for_reversion == Decimal("1.5")
    assert DEFAULT_THRESHOLDS.min_corroborators_for_reversion == 2
    assert DEFAULT_THRESHOLDS.intraday_30m_min_minutes_remaining == 30
    assert DEFAULT_THRESHOLDS.intraday_2h_min_minutes_remaining == 120


# --- Horizon boundary: a horizon may never describe more time than remains ----------------


def test_horizon_event_driven_30m_boundary_29_vs_30_minutes():
    just_short = base_features(minutes_remaining_in_session=29)
    horizon, _ = classify_horizon(Regime.EVENT_DRIVEN, just_short)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE

    exactly_enough = base_features(minutes_remaining_in_session=30)
    horizon, _ = classify_horizon(Regime.EVENT_DRIVEN, exactly_enough)
    assert horizon == ScenarioHorizon.INTRADAY_30M


def test_horizon_mean_reversion_30m_boundary_29_vs_30_minutes():
    just_short = base_features(minutes_remaining_in_session=29)
    horizon, _ = classify_horizon(Regime.VWAP_MEAN_REVERSION, just_short)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE

    exactly_enough = base_features(minutes_remaining_in_session=30)
    horizon, _ = classify_horizon(Regime.VWAP_MEAN_REVERSION, exactly_enough)
    assert horizon == ScenarioHorizon.INTRADAY_30M


def test_horizon_trend_continuation_2h_boundary_119_vs_120_minutes():
    just_short = base_features(minutes_remaining_in_session=119)
    horizon, _ = classify_horizon(Regime.TREND_CONTINUATION, just_short)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE

    exactly_enough = base_features(minutes_remaining_in_session=120)
    horizon, _ = classify_horizon(Regime.TREND_CONTINUATION, exactly_enough)
    assert horizon == ScenarioHorizon.INTRADAY_2H


@pytest.mark.parametrize(
    "regime,minutes_remaining",
    [
        (Regime.EVENT_DRIVEN, 29),
        (Regime.VWAP_MEAN_REVERSION, 29),
        (Regime.TREND_CONTINUATION, 119),
    ],
)
def test_no_horizon_exceeds_remaining_session_time(regime, minutes_remaining):
    """No horizon bucket may claim more time than actually remains in the
    regular session -- a 30-minute or 2-hour horizon must fall back to
    to_session_close whenever the remaining time is even one minute short
    of the named duration."""
    features = base_features(minutes_remaining_in_session=minutes_remaining)
    horizon, _ = classify_horizon(regime, features)
    assert horizon == ScenarioHorizon.TO_SESSION_CLOSE


# --- RegimeThresholds validation: fail closed on construction -----------------------------


def test_default_thresholds_construct_without_error():
    RegimeThresholds()


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("extension_threshold_for_reversion", Decimal("-0.1")),
        ("vwap_slope_min_bps_for_continuation", Decimal("-1")),
        ("relative_volume_elevated_min", Decimal("-1")),
        ("range_extension_max", Decimal("-1")),
    ],
)
def test_negative_numeric_thresholds_are_rejected(field_name, bad_value):
    with pytest.raises(ValueError):
        RegimeThresholds(**{field_name: bad_value})


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("extension_threshold_for_reversion", Decimal("Infinity")),
        ("vwap_slope_min_bps_for_continuation", Decimal("NaN")),
    ],
)
def test_non_finite_numeric_thresholds_are_rejected(field_name, bad_value):
    with pytest.raises(ValueError):
        RegimeThresholds(**{field_name: bad_value})


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("trend_strength_min_for_continuation", Decimal("0")),
        ("trend_strength_min_for_continuation", Decimal("1.1")),
        ("trend_strength_max_for_reversion", Decimal("-0.1")),
        ("trend_strength_max_for_reversion", Decimal("1")),
        ("range_trend_strength_max", Decimal("-0.1")),
        ("range_trend_strength_max", Decimal("1")),
    ],
)
def test_trend_strength_bounds_outside_valid_range_are_rejected(field_name, bad_value):
    with pytest.raises(ValueError):
        RegimeThresholds(**{field_name: bad_value})


@pytest.mark.parametrize("bad_value", [0, 5, -1])
def test_min_corroborators_outside_one_to_four_is_rejected(bad_value):
    with pytest.raises(ValueError):
        RegimeThresholds(min_corroborators_for_reversion=bad_value)


@pytest.mark.parametrize("value", [1, 2, 3, 4])
def test_min_corroborators_within_one_to_four_is_accepted(value):
    RegimeThresholds(min_corroborators_for_reversion=value)


@pytest.mark.parametrize("bad_value", [5, 29, 31, 0, -5])
def test_intraday_30m_minimum_cannot_be_weakened_or_changed(bad_value):
    with pytest.raises(ValueError):
        RegimeThresholds(intraday_30m_min_minutes_remaining=bad_value)


@pytest.mark.parametrize("bad_value", [45, 119, 121, 0, -5])
def test_intraday_2h_minimum_cannot_be_weakened_or_changed(bad_value):
    with pytest.raises(ValueError):
        RegimeThresholds(intraday_2h_min_minutes_remaining=bad_value)
