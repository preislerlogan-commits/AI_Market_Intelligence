"""Tests for market_intelligence.evaluation.spy_vwap_reversion_contracts.

Pure Pydantic v2 contract tests -- no network, no database, no evaluator
logic (see test_spy_vwap_reversion_evaluator.py for that).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    MAX_BARS_PER_SESSION,
    DecisionPointRecord,
    DescriptiveStats,
    EligibilityStatus,
    ExtensionSide,
    ForwardHorizon,
    HorizonDescriptiveBundle,
    HorizonOutcome,
    HorizonSideSummary,
    HorizonSummary,
    RegimeThresholdsSnapshot,
    SampleStatus,
    SampleThresholdsSnapshot,
    SessionBars,
    SpyVwapReversionEvaluationInput,
    SpyVwapReversionEvaluationRecord,
)
from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    Regime,
    ScenarioHorizon,
    TimeOfDayBucket,
)

EASTERN = ZoneInfo("America/New_York")
DAY = date(2026, 6, 10)  # Wednesday


def et(hour, minute, day=DAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN)


def flat_bar(hour, minute, price="500", volume=1000, day=DAY):
    p = Decimal(price)
    return IntradayBar(
        timestamp=et(hour, minute, day), open=p, high=p, low=p, close=p, volume=volume
    )


# --- SessionBars: reuses RegimeEngineInput validation --------------------------------


def test_session_bars_accepts_a_valid_ascending_grid_aligned_sequence():
    bars = [flat_bar(9, 30), flat_bar(9, 35)]
    session = SessionBars(session_date=DAY, bars=bars)
    assert session.session_date == DAY


def test_session_bars_rejects_out_of_order_bars():
    bars = [flat_bar(9, 35), flat_bar(9, 30)]
    with pytest.raises(ValidationError):
        SessionBars(session_date=DAY, bars=bars)


def test_session_bars_rejects_a_non_grid_aligned_bar():
    off_grid = IntradayBar(
        timestamp=datetime(2026, 6, 10, 9, 31, tzinfo=EASTERN),
        open=Decimal("500"), high=Decimal("500"), low=Decimal("500"), close=Decimal("500"),
        volume=1000,
    )
    with pytest.raises(ValidationError):
        SessionBars(session_date=DAY, bars=[off_grid])


def test_session_bars_rejects_a_weekend_session_date():
    saturday = date(2026, 6, 13)
    bar = IntradayBar(
        timestamp=datetime(2026, 6, 13, 9, 30, tzinfo=EASTERN),
        open=Decimal("500"), high=Decimal("500"), low=Decimal("500"), close=Decimal("500"),
        volume=1000,
    )
    with pytest.raises(ValidationError):
        SessionBars(session_date=saturday, bars=[bar])


def test_session_bars_too_many_bars_is_rejected():
    bars = [flat_bar(9, 30)] * (MAX_BARS_PER_SESSION + 1)
    with pytest.raises(ValidationError):
        SessionBars(session_date=DAY, bars=bars)


def test_session_bars_defaults_catalyst_and_breadth_to_unknown_unavailable():
    session = SessionBars(session_date=DAY, bars=[flat_bar(9, 30)])
    assert session.catalyst_state == CatalystState.UNKNOWN
    assert session.breadth_state == BreadthState.UNAVAILABLE


# --- SpyVwapReversionEvaluationInput: strictly ascending sessions --------------------


def test_evaluation_input_accepts_strictly_ascending_sessions():
    day2 = date(2026, 6, 11)
    s1 = SessionBars(session_date=DAY, bars=[flat_bar(9, 30, day=DAY)])
    s2 = SessionBars(session_date=day2, bars=[flat_bar(9, 30, day=day2)])
    evaluation_input = SpyVwapReversionEvaluationInput(sessions=[s1, s2])
    assert len(evaluation_input.sessions) == 2


def test_evaluation_input_rejects_out_of_order_sessions():
    day2 = date(2026, 6, 11)
    s1 = SessionBars(session_date=day2, bars=[flat_bar(9, 30, day=day2)])
    s2 = SessionBars(session_date=DAY, bars=[flat_bar(9, 30, day=DAY)])
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationInput(sessions=[s1, s2])


def test_evaluation_input_rejects_duplicate_session_dates():
    s1 = SessionBars(session_date=DAY, bars=[flat_bar(9, 30)])
    s2 = SessionBars(session_date=DAY, bars=[flat_bar(9, 35)])
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationInput(sessions=[s1, s2])


def test_evaluation_input_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationInput.model_validate(
            {"sessions": [], "unexpected": "value"}
        )


# --- HorizonOutcome consistency -------------------------------------------------------


def test_horizon_outcome_unavailable_forbids_any_derived_field():
    with pytest.raises(ValidationError):
        HorizonOutcome(
            horizon=ForwardHorizon.INTRADAY_30M,
            available=False,
            touched_vwap=False,
        )


def test_horizon_outcome_available_requires_core_fields():
    with pytest.raises(ValidationError):
        HorizonOutcome(horizon=ForwardHorizon.INTRADAY_30M, available=True)


def test_horizon_outcome_touched_requires_touch_counts():
    with pytest.raises(ValidationError):
        HorizonOutcome(
            horizon=ForwardHorizon.INTRADAY_30M,
            available=True,
            horizon_timestamp=datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
            price_at_horizon=Decimal("500"),
            touched_vwap=True,
        )


def test_horizon_outcome_not_touched_forbids_touch_counts():
    with pytest.raises(ValidationError):
        HorizonOutcome(
            horizon=ForwardHorizon.INTRADAY_30M,
            available=True,
            horizon_timestamp=datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
            price_at_horizon=Decimal("500"),
            touched_vwap=False,
            bars_to_touch=1,
            minutes_to_touch=5,
        )


def _available_outcome(horizon=ForwardHorizon.INTRADAY_30M) -> HorizonOutcome:
    return HorizonOutcome(
        horizon=horizon,
        available=True,
        horizon_timestamp=datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
        price_at_horizon=Decimal("500"),
        touched_vwap=True,
        bars_to_touch=1,
        minutes_to_touch=5,
        pct_extension_retraced=Decimal("100"),
        signed_return_toward_vwap_bps=Decimal("10"),
        max_favorable_excursion_bps=Decimal("10"),
        max_adverse_excursion_bps=Decimal("2"),
    )


def _unavailable_outcome(horizon: ForwardHorizon) -> HorizonOutcome:
    return HorizonOutcome(horizon=horizon, available=False)


def _all_four_outcomes(*, available_horizon=None) -> list[HorizonOutcome]:
    outcomes = []
    for horizon in ForwardHorizon:
        if horizon == available_horizon:
            outcomes.append(_available_outcome(horizon))
        else:
            outcomes.append(_unavailable_outcome(horizon))
    return outcomes


# --- DecisionPointRecord consistency ---------------------------------------------------


def _base_decision_point_kwargs():
    return dict(
        session_date=DAY,
        signal_as_of=datetime(2026, 6, 10, 14, 0, tzinfo=UTC),
        bar_index_in_session=0,
        session_bars_complete=True,
        regime=Regime.RANGE,
        scenario_horizon=ScenarioHorizon.NEXT_SESSION,
        time_of_day_bucket=TimeOfDayBucket.MIDDAY,
    )


def test_decision_point_requires_all_four_horizons_in_outcomes():
    with pytest.raises(ValidationError):
        DecisionPointRecord(
            **_base_decision_point_kwargs(),
            eligibility=EligibilityStatus.ELIGIBLE,
            extension_side=ExtensionSide.ABOVE_VWAP,
            close_to_vwap_distance_bps=Decimal("10"),
            normalized_vwap_extension=Decimal("2"),
            signal_vwap=Decimal("500"),
            signal_close=Decimal("505"),
            outcomes=_all_four_outcomes()[:3],
        )


def test_decision_point_non_eligible_forbids_extension_side():
    with pytest.raises(ValidationError):
        DecisionPointRecord(
            **_base_decision_point_kwargs(),
            eligibility=EligibilityStatus.ZERO_EXTENSION,
            extension_side=ExtensionSide.ABOVE_VWAP,
            outcomes=_all_four_outcomes(),
        )


def test_decision_point_non_eligible_forbids_available_outcomes():
    with pytest.raises(ValidationError):
        DecisionPointRecord(
            **_base_decision_point_kwargs(),
            eligibility=EligibilityStatus.VWAP_UNAVAILABLE,
            outcomes=_all_four_outcomes(available_horizon=ForwardHorizon.INTRADAY_30M),
        )


def test_decision_point_eligible_with_no_available_outcomes_is_valid():
    dp = DecisionPointRecord(
        **_base_decision_point_kwargs(),
        eligibility=EligibilityStatus.ELIGIBLE,
        extension_side=ExtensionSide.ABOVE_VWAP,
        close_to_vwap_distance_bps=Decimal("10"),
        normalized_vwap_extension=Decimal("2"),
        signal_vwap=Decimal("500"),
        signal_close=Decimal("505"),
        outcomes=_all_four_outcomes(),
    )
    assert dp.eligibility == EligibilityStatus.ELIGIBLE


# --- DescriptiveStats consistency -------------------------------------------------------


def test_descriptive_stats_insufficient_sample_forbids_values():
    with pytest.raises(ValidationError):
        DescriptiveStats(status=SampleStatus.INSUFFICIENT_SAMPLE, sample_size=3, mean=Decimal("1"))


def test_descriptive_stats_ok_requires_every_value():
    with pytest.raises(ValidationError):
        DescriptiveStats(status=SampleStatus.OK, sample_size=100, mean=Decimal("1"))


def test_descriptive_stats_ok_with_all_values_is_valid():
    stats = DescriptiveStats(
        status=SampleStatus.OK,
        sample_size=100,
        mean=Decimal("1"),
        median=Decimal("1"),
        minimum=Decimal("0"),
        maximum=Decimal("2"),
    )
    assert stats.sample_size == 100


# --- HorizonSummary side-label consistency ----------------------------------------------


def _bundle(sample_size=0, status=SampleStatus.INSUFFICIENT_SAMPLE) -> HorizonDescriptiveBundle:
    stats = DescriptiveStats(status=status, sample_size=sample_size)
    return HorizonDescriptiveBundle(
        eligible_count=sample_size,
        available_count=sample_size,
        missing_count=0,
        touch_rate=stats,
        pct_extension_retraced=stats,
        signed_return_toward_vwap_bps=stats,
        max_favorable_excursion_bps=stats,
        max_adverse_excursion_bps=stats,
    )


def test_horizon_summary_rejects_mismatched_side_labels():
    with pytest.raises(ValidationError):
        HorizonSummary(
            horizon=ForwardHorizon.INTRADAY_30M,
            above_vwap=HorizonSideSummary(
                extension_side=ExtensionSide.BELOW_VWAP,  # wrong label
                observation_level=_bundle(),
                session_level=_bundle(),
            ),
            below_vwap=HorizonSideSummary(
                extension_side=ExtensionSide.BELOW_VWAP,
                observation_level=_bundle(),
                session_level=_bundle(),
            ),
        )


def test_horizon_descriptive_bundle_rejects_inconsistent_counts():
    stats = DescriptiveStats(status=SampleStatus.INSUFFICIENT_SAMPLE, sample_size=0)
    with pytest.raises(ValidationError):
        HorizonDescriptiveBundle(
            eligible_count=5,
            available_count=2,
            missing_count=2,  # should be 3
            touch_rate=stats,
            pct_extension_retraced=stats,
            signed_return_toward_vwap_bps=stats,
            max_favorable_excursion_bps=stats,
            max_adverse_excursion_bps=stats,
        )


# --- Top-level record enum-completeness / count consistency ----------------------------


def _default_thresholds_snapshot() -> RegimeThresholdsSnapshot:
    return RegimeThresholdsSnapshot(
        extension_threshold_for_reversion=Decimal("1.5"),
        trend_strength_min_for_continuation=Decimal("0.6"),
        trend_strength_max_for_reversion=Decimal("0.35"),
        vwap_slope_min_bps_for_continuation=Decimal("3"),
        vwap_slope_max_bps_for_reversion=Decimal("4"),
        relative_volume_elevated_min=Decimal("1.3"),
        range_trend_strength_max=Decimal("0.25"),
        range_extension_max=Decimal("1.0"),
        min_corroborators_for_reversion=2,
        intraday_30m_min_minutes_remaining=30,
        intraday_2h_min_minutes_remaining=120,
    )


def _minimal_record_kwargs(**overrides):
    dp = DecisionPointRecord(
        **_base_decision_point_kwargs(),
        eligibility=EligibilityStatus.ELIGIBLE,
        extension_side=ExtensionSide.ABOVE_VWAP,
        close_to_vwap_distance_bps=Decimal("10"),
        normalized_vwap_extension=Decimal("2"),
        signal_vwap=Decimal("500"),
        signal_close=Decimal("505"),
        outcomes=_all_four_outcomes(),
    )
    regime_counts = {r: 0 for r in Regime}
    regime_counts[Regime.RANGE] = 1
    extension_counts = {s: 0 for s in ExtensionSide}
    extension_counts[ExtensionSide.ABOVE_VWAP] = 1
    missing_horizon_counts = {h: 0 for h in ForwardHorizon}

    bundle = _bundle()
    horizon_summaries = [
        HorizonSummary(
            horizon=h,
            above_vwap=HorizonSideSummary(
                extension_side=ExtensionSide.ABOVE_VWAP,
                observation_level=bundle,
                session_level=bundle,
            ),
            below_vwap=HorizonSideSummary(
                extension_side=ExtensionSide.BELOW_VWAP,
                observation_level=bundle,
                session_level=bundle,
            ),
        )
        for h in ForwardHorizon
    ]

    kwargs = dict(
        symbol="SPY",
        generated_at=datetime(2026, 6, 20, tzinfo=UTC),
        regime_thresholds=_default_thresholds_snapshot(),
        sample_thresholds=SampleThresholdsSnapshot(
            min_observations_for_summary=50, min_sessions_for_summary=20
        ),
        unique_session_count=1,
        candidate_decision_point_count=1,
        eligible_observation_count=1,
        no_signal_vwap_unavailable_count=0,
        no_signal_zero_extension_count=0,
        regime_counts_all_candidates=regime_counts,
        regime_counts_eligible_only=regime_counts,
        extension_side_counts=extension_counts,
        missing_horizon_counts=missing_horizon_counts,
        horizon_summaries=horizon_summaries,
        decision_points=[dp],
        notes=["no_predictive_edge_established"],
    )
    kwargs.update(overrides)
    return kwargs


def test_minimal_valid_record_round_trips():
    record = SpyVwapReversionEvaluationRecord(**_minimal_record_kwargs())
    assert record.eligible_observation_count == 1


def test_record_rejects_incomplete_regime_counts_dict():
    kwargs = _minimal_record_kwargs()
    incomplete = dict(kwargs["regime_counts_all_candidates"])
    del incomplete[Regime.EVENT_DRIVEN]
    kwargs["regime_counts_all_candidates"] = incomplete
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationRecord(**kwargs)


def test_record_rejects_decision_point_count_mismatch():
    kwargs = _minimal_record_kwargs(candidate_decision_point_count=2)
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationRecord(**kwargs)


def test_record_rejects_eligible_count_inconsistent_with_no_signal_counts():
    kwargs = _minimal_record_kwargs(no_signal_vwap_unavailable_count=1)
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationRecord(**kwargs)


def test_record_rejects_extra_fields():
    kwargs = _minimal_record_kwargs()
    kwargs["unexpected_field"] = "nope"
    with pytest.raises(ValidationError):
        SpyVwapReversionEvaluationRecord(**kwargs)
