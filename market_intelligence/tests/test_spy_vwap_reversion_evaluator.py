"""Tests for market_intelligence.evaluation.spy_vwap_reversion_evaluator.

``evaluate_spy_vwap_reversion`` is a pure function; these tests never touch a
network or a database. Every scenario below uses the "frozen VWAP via
zero-volume signal bar" trick: prior bars are flat (constant OHLC) with
volume > 0, which keeps the cumulative session VWAP at an exact, known price;
the signal bar itself is given ``volume=0``, so it contributes zero to
cumulative price*volume and cumulative volume and therefore never moves the
VWAP away from that known price, while its own ``close`` can be set freely to
create an exact, known extension. Future (post-signal) bars are fully
controlled OHLC so every outcome formula can be hand-verified independently
of the evaluator's own implementation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from zoneinfo import ZoneInfo

import pytest

from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    EligibilityStatus,
    ExtensionSide,
    ForwardHorizon,
    SampleStatus,
    SessionBars,
    SpyVwapReversionEvaluationInput,
)
from market_intelligence.evaluation.spy_vwap_reversion_evaluator import (
    SampleSizeThresholds,
    evaluate_spy_vwap_reversion,
)
from market_intelligence.market_features.spy_regime_contracts import IntradayBar

EASTERN = ZoneInfo("America/New_York")
GENERATED_AT = datetime(2026, 6, 20, 12, 0, tzinfo=ZoneInfo("UTC"))
_QUANT = Decimal("0.0001")


def et(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=EASTERN)


def make_bar(day, hour, minute, *, open, high, low, close, volume) -> IntradayBar:
    return IntradayBar(
        timestamp=et(day, hour, minute),
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def flat_bar(day, hour, minute, price, volume=1000) -> IntradayBar:
    return make_bar(
        day, hour, minute, open=price, high=price, low=price, close=price, volume=volume
    )


def frozen_signal_bar(day, hour, minute, close) -> IntradayBar:
    """A bar with volume=0: it never moves the cumulative VWAP (its
    typical_price * 0 contributes nothing to cumulative pv or volume), so
    the VWAP at this bar is exactly whatever it was after the prior bar --
    while its own close is fully controlled."""
    return flat_bar(day, hour, minute, close, volume=0)


def _all_session_times():
    times = []
    hh, mm = 9, 30
    while (hh, mm) <= (15, 55):
        times.append((hh, mm))
        mm += 5
        if mm == 60:
            mm = 0
            hh += 1
    return times


def full_day_flat_then_signal(day, *, flat_price="500", signal_close="506"):
    """A full 78-bar regular session: every bar flat at ``flat_price``
    except the last, which is a frozen signal bar at ``signal_close``."""
    times = _all_session_times()
    bars = [flat_bar(day, h, m, flat_price) for (h, m) in times[:-1]]
    last_h, last_m = times[-1]
    bars.append(frozen_signal_bar(day, last_h, last_m, signal_close))
    return bars


def full_day_with_mid_session_signal(day, signal_index, *, flat_price="500", signal_close="506"):
    """A full 78-bar regular session, flat at ``flat_price`` throughout,
    except bar ``signal_index`` which is a frozen (volume=0) signal bar at
    ``signal_close`` -- the session VWAP stays exactly ``flat_price`` for
    every bar (before and after), while bar ``signal_index`` itself carries
    a real, known extension and the session still reaches its true close."""
    times = _all_session_times()
    bars = []
    for i, (h, m) in enumerate(times):
        if i == signal_index:
            bars.append(frozen_signal_bar(day, h, m, signal_close))
        else:
            bars.append(flat_bar(day, h, m, flat_price))
    return bars


def quant(value: Decimal) -> Decimal:
    return value.quantize(_QUANT, rounding=ROUND_HALF_EVEN)


DAY = date(2026, 6, 10)  # Wednesday


def _single_session_input(bars, **session_kwargs) -> SpyVwapReversionEvaluationInput:
    return SpyVwapReversionEvaluationInput(
        sessions=[SessionBars(session_date=DAY, bars=bars, **session_kwargs)]
    )


def _dp_for_bar(record, bar_index):
    return next(dp for dp in record.decision_points if dp.bar_index_in_session == bar_index)


def _dp_for_session_bar(record, session_date, bar_index):
    return next(
        dp
        for dp in record.decision_points
        if dp.session_date == session_date and dp.bar_index_in_session == bar_index
    )


def build_signal_and_30m_window(day, signal_hour, signal_minute, signal_close, window_specs):
    """A signal bar (frozen VWAP=500 via the zero-volume trick) followed by
    exactly 6 more 5-minute bars (so the intraday_30m horizon is available),
    built from ``window_specs`` (six dicts of open/high/low/close strings).
    Returns ``(bars, signal_index)``.
    """
    assert len(window_specs) == 6
    all_times = _all_session_times()
    signal_index = all_times.index((signal_hour, signal_minute))
    prior = [flat_bar(day, h, m, "500") for h, m in all_times[:signal_index]]
    signal = frozen_signal_bar(day, signal_hour, signal_minute, signal_close)
    window_times = all_times[signal_index + 1 : signal_index + 7]
    window = [
        make_bar(day, h, m, volume=1000, **spec)
        for (h, m), spec in zip(window_times, window_specs, strict=True)
    ]
    return [*prior, signal, *window], signal_index


_NEUTRAL_SPEC = {"open": "510", "high": "510", "low": "510", "close": "510"}


# --- Frozen-VWAP touch / no-touch ---------------------------------------------------


def test_frozen_vwap_is_touched_by_a_future_bar():
    touching_spec = {"open": "505", "high": "512", "low": "498", "close": "505"}
    specs = [
        _NEUTRAL_SPEC, _NEUTRAL_SPEC, touching_spec,
        _NEUTRAL_SPEC, _NEUTRAL_SPEC, _NEUTRAL_SPEC,
    ]
    bars, signal_index = build_signal_and_30m_window(DAY, 9, 40, "506", specs)

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, signal_index)
    assert dp.eligibility == EligibilityStatus.ELIGIBLE
    assert dp.signal_vwap == Decimal("500.000000")
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)
    assert outcome.available is True
    assert outcome.touched_vwap is True
    assert outcome.bars_to_touch == 3
    assert outcome.minutes_to_touch == 15


def test_frozen_vwap_is_not_touched_when_price_stays_away():
    specs = [_NEUTRAL_SPEC] * 6
    bars, signal_index = build_signal_and_30m_window(DAY, 9, 40, "506", specs)

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, signal_index)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)
    assert outcome.available is True
    assert outcome.touched_vwap is False
    assert outcome.bars_to_touch is None
    assert outcome.minutes_to_touch is None


# --- Partial / full / no retracement, and overshoot ---------------------------------


@pytest.mark.parametrize(
    "horizon_close, expected_pct",
    [
        ("500", Decimal(100)),  # full retracement: price returns exactly to VWAP
        ("503", Decimal(50)),  # partial retracement: halfway back
        ("510", Decimal(-2) / Decimal(3) * 100),  # extended further away (negative)
        ("494", Decimal(200)),  # overshoot past VWAP to the other side
    ],
)
def test_pct_extension_retraced_matches_the_independent_formula(horizon_close, expected_pct):
    horizon_spec = {
        "open": horizon_close, "high": horizon_close, "low": horizon_close, "close": horizon_close,
    }
    specs = [_NEUTRAL_SPEC] * 5 + [horizon_spec]
    bars, signal_index = build_signal_and_30m_window(DAY, 9, 40, "506", specs)

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, signal_index)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)

    signal_close = Decimal("506")
    signal_vwap = Decimal("500")
    original_extension = abs(signal_close - signal_vwap)
    toward = Decimal(1) * (signal_close - Decimal(horizon_close))
    expected = quant(toward / original_extension * Decimal(100))
    assert outcome.pct_extension_retraced == expected == quant(expected_pct)

    expected_signed_return = quant(toward / signal_close * Decimal(10000))
    assert outcome.signed_return_toward_vwap_bps == expected_signed_return


# --- Favorable / adverse excursion --------------------------------------------------


def test_max_favorable_and_adverse_excursion_are_measured_toward_and_away_from_vwap():
    bar_x = {"open": "506", "high": "508", "low": "495", "close": "500"}
    bar_y = {"open": "500", "high": "512", "low": "500", "close": "505"}
    specs = [bar_x, bar_y, _NEUTRAL_SPEC, _NEUTRAL_SPEC, _NEUTRAL_SPEC, _NEUTRAL_SPEC]
    bars, signal_index = build_signal_and_30m_window(DAY, 9, 40, "506", specs)

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, signal_index)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)

    signal_close = Decimal("506")
    # favorable (toward VWAP, i.e. downward) = signal_close - bar.low
    favorable = max(signal_close - Decimal("495"), signal_close - Decimal("500"))
    # adverse (away from VWAP, i.e. upward) = bar.high - signal_close
    adverse = max(Decimal("508") - signal_close, Decimal("512") - signal_close)
    assert outcome.max_favorable_excursion_bps == quant(favorable / signal_close * Decimal(10000))
    assert outcome.max_adverse_excursion_bps == quant(adverse / signal_close * Decimal(10000))


# --- Bullish / bearish symmetry ------------------------------------------------------


def test_below_vwap_extension_mirrors_the_above_vwap_case():
    horizon_spec = {"open": "500", "high": "500", "low": "500", "close": "500"}
    specs = [_NEUTRAL_SPEC] * 5 + [horizon_spec]
    # Below VWAP; extension -6.
    bars, signal_index = build_signal_and_30m_window(DAY, 9, 40, "494", specs)

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, signal_index)
    assert dp.eligibility == EligibilityStatus.ELIGIBLE
    assert dp.extension_side == ExtensionSide.BELOW_VWAP
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)
    # Full retracement from below, same magnitude as the above-VWAP case.
    assert outcome.pct_extension_retraced == quant(Decimal(100))


def test_extension_side_and_eligibility_for_zero_and_unavailable_vwap():
    # Zero extension: signal close exactly equals the frozen VWAP.
    prior = [flat_bar(DAY, 9, 30, "500")]
    zero_signal = frozen_signal_bar(DAY, 9, 35, "500")
    record = evaluate_spy_vwap_reversion(
        _single_session_input([*prior, zero_signal]), generated_at=GENERATED_AT
    )
    dp = _dp_for_bar(record, 1)
    assert dp.eligibility == EligibilityStatus.ZERO_EXTENSION
    assert dp.extension_side is None
    assert all(not o.available for o in dp.outcomes)

    # VWAP unavailable: every bar so far has zero volume, so cumulative
    # volume is zero and session_vwap is None.
    zero_volume_bars = [
        make_bar(DAY, 9, 30, open="500", high="500", low="500", close="500", volume=0),
        make_bar(DAY, 9, 35, open="501", high="501", low="501", close="501", volume=0),
    ]
    record2 = evaluate_spy_vwap_reversion(
        _single_session_input(zero_volume_bars), generated_at=GENERATED_AT
    )
    dp2 = _dp_for_bar(record2, 1)
    assert dp2.eligibility == EligibilityStatus.VWAP_UNAVAILABLE
    assert dp2.signal_vwap is None
    assert dp2.extension_side is None
    assert all(not o.available for o in dp2.outcomes)


# --- Exact forward-horizon boundaries -------------------------------------------------


def test_intraday_30m_available_at_the_exact_target_bar():
    # Signal at 10:00 (bar index 6); target bar_end = 10:35 -> bar timestamp 10:30 (index 12).
    all_times = _all_session_times()
    signal_index = all_times.index((10, 0))
    bars = [flat_bar(DAY, h, m, "500") for h, m in all_times[:signal_index]]
    bars.append(frozen_signal_bar(DAY, 10, 0, "506"))  # index 6
    for h, m in all_times[signal_index + 1 : signal_index + 7]:
        bars.append(flat_bar(DAY, h, m, "500"))

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, 6)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)
    assert outcome.available is True
    assert outcome.horizon_timestamp.astimezone(EASTERN) == et(DAY, 10, 35)


def test_intraday_30m_unavailable_when_the_exact_target_bar_is_missing():
    prior_times = _all_session_times()[:6]
    bars = [flat_bar(DAY, h, m, "500") for h, m in prior_times]
    bars.append(frozen_signal_bar(DAY, 10, 0, "506"))  # index 6, signal_as_of = 10:05
    # Provide bars up through 10:25 (target is 10:35), then skip straight to
    # 10:40 -- so no bar's bar_end ever equals the exact 10:35 target.
    for h, m in [(10, 5), (10, 10), (10, 15), (10, 20), (10, 25), (10, 40)]:
        bars.append(flat_bar(DAY, h, m, "500"))

    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, 6)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)
    assert outcome.available is False
    assert outcome.price_at_horizon is None


def test_intraday_2h_unavailable_when_target_exceeds_session_close_even_with_full_data():
    # The last bar of a full regular session (15:55) is 5 minutes from
    # close; a 2h horizon from there is always far beyond 16:00, so it must
    # be unavailable regardless of how much (nonexistent) future data
    # might otherwise exist.
    bars = full_day_flat_then_signal(DAY, flat_price="500", signal_close="506")
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    dp = _dp_for_bar(record, 77)
    outcome_2h = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_2H)
    assert outcome_2h.available is False


def test_to_session_close_available_only_when_the_full_session_is_present():
    bars = full_day_with_mid_session_signal(DAY, 10, flat_price="500", signal_close="506")
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    # An early-session decision point (index 10) should see an available
    # to_session_close horizon, since the full session's remaining bars are present.
    dp_early = _dp_for_bar(record, 10)
    outcome = next(o for o in dp_early.outcomes if o.horizon == ForwardHorizon.TO_SESSION_CLOSE)
    assert outcome.available is True

    # A session that stops before 15:55 never reaches close.
    short_bars = bars[:40]
    record_short = evaluate_spy_vwap_reversion(
        _single_session_input(short_bars), generated_at=GENERATED_AT
    )
    dp_short = _dp_for_bar(record_short, 10)
    outcome_short = next(
        o for o in dp_short.outcomes if o.horizon == ForwardHorizon.TO_SESSION_CLOSE
    )
    assert outcome_short.available is False


def test_next_session_is_unavailable_even_when_a_complete_next_session_follows():
    # Without a deterministic exchange calendar, the next dated SessionBars
    # entry is never treated as a verified next trading session -- see
    # PROJECT_STATE.md and SpyVwapReversionEvaluationInput's docstring.
    # Even a fully gapless, complete following session must not make
    # NEXT_SESSION available.
    day1 = date(2026, 6, 10)
    day2 = date(2026, 6, 11)
    bars1 = full_day_with_mid_session_signal(day1, 10, flat_price="500", signal_close="506")
    bars2 = [flat_bar(day2, h, m, "500") for h, m in _all_session_times()]

    complete_input = SpyVwapReversionEvaluationInput(
        sessions=[
            SessionBars(session_date=day1, bars=bars1),
            SessionBars(session_date=day2, bars=bars2),
        ]
    )
    record = evaluate_spy_vwap_reversion(complete_input, generated_at=GENERATED_AT)
    dp = _dp_for_session_bar(record, day1, 10)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.NEXT_SESSION)
    assert outcome.available is False
    assert outcome.price_at_horizon is None

    # Next session present but incomplete -> still unavailable.
    incomplete_input = SpyVwapReversionEvaluationInput(
        sessions=[
            SessionBars(session_date=day1, bars=bars1),
            SessionBars(session_date=day2, bars=bars2[:40]),
        ]
    )
    record2 = evaluate_spy_vwap_reversion(incomplete_input, generated_at=GENERATED_AT)
    dp2 = _dp_for_session_bar(record2, day1, 10)
    outcome2 = next(o for o in dp2.outcomes if o.horizon == ForwardHorizon.NEXT_SESSION)
    assert outcome2.available is False

    # No next session at all -> unavailable.
    single_input = _single_session_input(bars1)
    record3 = evaluate_spy_vwap_reversion(single_input, generated_at=GENERATED_AT)
    dp3 = _dp_for_bar(record3, 10)
    outcome3 = next(o for o in dp3.outcomes if o.horizon == ForwardHorizon.NEXT_SESSION)
    assert outcome3.available is False


def test_next_session_is_unavailable_across_a_friday_to_monday_boundary():
    # A Friday session followed by the next Monday's session looks
    # contiguous "by date alone," but this evaluator has no exchange
    # calendar and must not treat that adjacency as a verified next
    # trading session (no weekend assumption is guessed either way).
    friday = date(2026, 6, 12)
    monday = date(2026, 6, 15)
    bars_friday = full_day_with_mid_session_signal(
        friday, 10, flat_price="500", signal_close="506"
    )
    bars_monday = [flat_bar(monday, h, m, "500") for h, m in _all_session_times()]

    evaluation_input = SpyVwapReversionEvaluationInput(
        sessions=[
            SessionBars(session_date=friday, bars=bars_friday),
            SessionBars(session_date=monday, bars=bars_monday),
        ]
    )
    record = evaluate_spy_vwap_reversion(evaluation_input, generated_at=GENERATED_AT)
    dp = _dp_for_session_bar(record, friday, 10)
    outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.NEXT_SESSION)
    assert outcome.available is False


def test_missing_horizon_counts_include_next_session_for_every_eligible_point():
    bars = [flat_bar(DAY, h, m, str(500 + i)) for i, (h, m) in enumerate(_all_session_times())]
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    assert (
        record.missing_horizon_counts[ForwardHorizon.NEXT_SESSION]
        == record.eligible_observation_count
    )


def test_no_prefix_receives_context_from_a_later_session():
    # A decision point's signal-side fields (regime, horizon, VWAP,
    # extension) must be identical whether or not a chronologically later
    # session is supplied in the same evaluation input -- future sessions
    # must never leak into an earlier session's signal.
    day1 = date(2026, 6, 10)
    day2 = date(2026, 6, 11)
    bars1 = full_day_with_mid_session_signal(day1, 10, flat_price="500", signal_close="506")
    bars2 = [flat_bar(day2, h, m, "999") for h, m in _all_session_times()]

    record_alone = evaluate_spy_vwap_reversion(
        _single_session_input(bars1), generated_at=GENERATED_AT
    )
    record_with_next = evaluate_spy_vwap_reversion(
        SpyVwapReversionEvaluationInput(
            sessions=[
                SessionBars(session_date=day1, bars=bars1),
                SessionBars(session_date=day2, bars=bars2),
            ]
        ),
        generated_at=GENERATED_AT,
    )

    for k in range(len(bars1)):
        dp_alone = _dp_for_session_bar(record_alone, day1, k)
        dp_with_next = _dp_for_session_bar(record_with_next, day1, k)
        assert dp_alone.eligibility == dp_with_next.eligibility
        assert dp_alone.regime == dp_with_next.regime
        assert dp_alone.scenario_horizon == dp_with_next.scenario_horizon
        assert dp_alone.extension_side == dp_with_next.extension_side
        assert dp_alone.signal_vwap == dp_with_next.signal_vwap
        assert dp_alone.signal_close == dp_with_next.signal_close
        for horizon in ForwardHorizon:
            outcome_alone = next(o for o in dp_alone.outcomes if o.horizon == horizon)
            outcome_with_next = next(o for o in dp_with_next.outcomes if o.horizon == horizon)
            assert outcome_alone == outcome_with_next


# --- No lookahead --------------------------------------------------------------------


def test_signal_fields_are_identical_regardless_of_future_bars():
    times = _all_session_times()[:15]
    short_bars = [flat_bar(DAY, h, m, str(500 + i)) for i, (h, m) in enumerate(times)]
    long_bars = short_bars + [
        flat_bar(DAY, h, m, str(500 + 15 + i))
        for i, (h, m) in enumerate(_all_session_times()[15:30])
    ]

    record_short = evaluate_spy_vwap_reversion(
        _single_session_input(short_bars), generated_at=GENERATED_AT
    )
    record_long = evaluate_spy_vwap_reversion(
        _single_session_input(long_bars), generated_at=GENERATED_AT
    )

    for k in range(len(short_bars)):
        dp_short = _dp_for_bar(record_short, k)
        dp_long = _dp_for_bar(record_long, k)
        assert dp_short.signal_as_of == dp_long.signal_as_of
        assert dp_short.eligibility == dp_long.eligibility
        assert dp_short.session_bars_complete == dp_long.session_bars_complete
        assert dp_short.regime == dp_long.regime
        assert dp_short.scenario_horizon == dp_long.scenario_horizon
        assert dp_short.extension_side == dp_long.extension_side
        assert dp_short.close_to_vwap_distance_bps == dp_long.close_to_vwap_distance_bps
        assert dp_short.normalized_vwap_extension == dp_long.normalized_vwap_extension
        assert dp_short.signal_vwap == dp_long.signal_vwap
        assert dp_short.signal_close == dp_long.signal_close


# --- Incomplete sessions --------------------------------------------------------------


def test_incomplete_session_is_still_recorded_with_indeterminate_regime():
    # 09:30, 09:35, 09:40, then a gap (skip 09:45), then 09:50, 09:55, 10:00.
    bars = [
        flat_bar(DAY, 9, 30, "500"),
        flat_bar(DAY, 9, 35, "500"),
        flat_bar(DAY, 9, 40, "500"),
        frozen_signal_bar(DAY, 9, 50, "506"),  # index 3, after the gap
        make_bar(DAY, 9, 55, open="500", high="500", low="500", close="500", volume=1000),
    ]
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)

    dp_before_gap = _dp_for_bar(record, 2)  # 09:40, before the gap
    assert dp_before_gap.session_bars_complete is True

    dp_after_gap = _dp_for_bar(record, 3)  # 09:50, after the gap
    assert dp_after_gap.session_bars_complete is False
    from market_intelligence.market_features.spy_regime_contracts import Regime, ScenarioHorizon

    assert dp_after_gap.regime == Regime.INDETERMINATE
    assert dp_after_gap.scenario_horizon == ScenarioHorizon.INDETERMINATE
    # Still eligible and scored -- indeterminate is a real, recorded outcome.
    assert dp_after_gap.eligibility == EligibilityStatus.ELIGIBLE
    assert dp_after_gap.extension_side == ExtensionSide.ABOVE_VWAP


# --- Overlapping-observation accounting -----------------------------------------------


def test_candidate_and_eligible_counts_reflect_overlapping_five_minute_observations():
    bars = [flat_bar(DAY, h, m, str(500 + i)) for i, (h, m) in enumerate(_all_session_times())]
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)

    assert record.unique_session_count == 1
    assert record.candidate_decision_point_count == len(bars) == 78
    assert len(record.decision_points) == 78
    assert len({dp.signal_as_of for dp in record.decision_points}) == 78
    assert len({dp.session_date for dp in record.decision_points}) == 1
    assert (
        record.eligible_observation_count
        + record.no_signal_vwap_unavailable_count
        + record.no_signal_zero_extension_count
        == record.candidate_decision_point_count
    )


# --- Session-level aggregation de-duplicates overlapping observations -----------------


def test_session_level_summary_first_averages_within_a_session():
    # Session A: 15 bars -> signal indices 0..8 (9 points) have an available
    # 30m horizon (need 6 more bars). Session B: 9 bars -> signal indices
    # 0..2 (3 points) have an available 30m horizon. Unequal counts mean
    # observation-level pooling and session-level (mean-of-session-means)
    # aggregation generally diverge.
    day_a = date(2026, 6, 10)
    day_b = date(2026, 6, 11)
    times = _all_session_times()
    bars_a = [flat_bar(day_a, h, m, str(500 + (i * 3) % 7)) for i, (h, m) in enumerate(times[:15])]
    bars_b = [flat_bar(day_b, h, m, str(500 + (i * 5) % 11)) for i, (h, m) in enumerate(times[:9])]

    evaluation_input = SpyVwapReversionEvaluationInput(
        sessions=[
            SessionBars(session_date=day_a, bars=bars_a),
            SessionBars(session_date=day_b, bars=bars_b),
        ]
    )
    low_thresholds = SampleSizeThresholds(
        min_observations_for_summary=1, min_sessions_for_summary=1
    )
    record = evaluate_spy_vwap_reversion(
        evaluation_input, generated_at=GENERATED_AT, sample_thresholds=low_thresholds
    )

    for side in (ExtensionSide.ABOVE_VWAP, ExtensionSide.BELOW_VWAP):
        side_summary = next(
            hs for hs in record.horizon_summaries if hs.horizon == ForwardHorizon.INTRADAY_30M
        )
        bundle = (
            side_summary.above_vwap
            if side == ExtensionSide.ABOVE_VWAP
            else side_summary.below_vwap
        )

        eligible = [
            dp
            for dp in record.decision_points
            if dp.eligibility == EligibilityStatus.ELIGIBLE and dp.extension_side == side
        ]
        eligible_sessions = {dp.session_date for dp in eligible}
        outcomes_by_session: dict[date, list] = {}
        for dp in eligible:
            outcome = next(o for o in dp.outcomes if o.horizon == ForwardHorizon.INTRADAY_30M)
            if outcome.available:
                outcomes_by_session.setdefault(dp.session_date, []).append(
                    outcome.pct_extension_retraced
                )

        assert bundle.session_level.eligible_count == len(eligible_sessions)
        assert bundle.session_level.available_count == len(outcomes_by_session)

        if not outcomes_by_session:
            continue

        pooled = [v for values in outcomes_by_session.values() for v in values]
        expected_observation_mean = quant(sum(pooled, Decimal(0)) / Decimal(len(pooled)))

        session_means = [
            sum(values, Decimal(0)) / Decimal(len(values))
            for values in outcomes_by_session.values()
        ]
        expected_session_mean = quant(sum(session_means, Decimal(0)) / Decimal(len(session_means)))

        assert bundle.observation_level.pct_extension_retraced.mean == expected_observation_mean
        assert bundle.session_level.pct_extension_retraced.mean == expected_session_mean

    # The fixture deliberately gives the two sessions unequal eligible
    # counts for the ABOVE_VWAP side, so pooling (observation-level) and
    # averaging-per-session-first (session-level) must actually diverge --
    # proving session-level aggregation is not just pooling under another
    # name.
    above_bundle = next(
        hs for hs in record.horizon_summaries if hs.horizon == ForwardHorizon.INTRADAY_30M
    ).above_vwap
    if above_bundle.observation_level.available_count >= 2:
        assert (
            above_bundle.observation_level.pct_extension_retraced.mean
            != above_bundle.session_level.pct_extension_retraced.mean
        )


# --- Insufficient samples --------------------------------------------------------------


def test_descriptive_stats_are_insufficient_sample_below_the_default_threshold():
    bars = [flat_bar(DAY, h, m, str(500 + i)) for i, (h, m) in enumerate(_all_session_times()[:10])]
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)

    horizon_summary = next(
        hs for hs in record.horizon_summaries if hs.horizon == ForwardHorizon.INTRADAY_30M
    )
    bundle = horizon_summary.above_vwap.observation_level
    assert bundle.pct_extension_retraced.status == SampleStatus.INSUFFICIENT_SAMPLE
    assert bundle.pct_extension_retraced.mean is None
    assert bundle.pct_extension_retraced.sample_size < 50


def test_descriptive_stats_become_ok_once_the_threshold_is_lowered():
    times = _all_session_times()[:10]
    bars = [flat_bar(DAY, h, m, str(500 + i)) for i, (h, m) in enumerate(times)]
    low_thresholds = SampleSizeThresholds(
        min_observations_for_summary=1, min_sessions_for_summary=1
    )
    record = evaluate_spy_vwap_reversion(
        _single_session_input(bars), generated_at=GENERATED_AT, sample_thresholds=low_thresholds
    )
    horizon_summary = next(
        hs for hs in record.horizon_summaries if hs.horizon == ForwardHorizon.INTRADAY_30M
    )
    bundle = horizon_summary.above_vwap.observation_level
    if bundle.eligible_count > 0:
        assert bundle.pct_extension_retraced.status == SampleStatus.OK


# --- Determinism -----------------------------------------------------------------------


def test_evaluation_is_deterministic():
    bars = [flat_bar(DAY, h, m, str(500 + i)) for i, (h, m) in enumerate(_all_session_times())]
    evaluation_input = _single_session_input(bars)
    record1 = evaluate_spy_vwap_reversion(evaluation_input, generated_at=GENERATED_AT)
    record2 = evaluate_spy_vwap_reversion(evaluation_input, generated_at=GENERATED_AT)
    assert record1 == record2


# --- Frozen, unmodified thresholds are recorded, not derived --------------------------


def test_regime_thresholds_snapshot_matches_the_default_thresholds():
    from market_intelligence.market_features.spy_regime_classifier import DEFAULT_THRESHOLDS

    bars = [flat_bar(DAY, h, m, "500") for h, m in _all_session_times()[:3]]
    record = evaluate_spy_vwap_reversion(_single_session_input(bars), generated_at=GENERATED_AT)
    snapshot = record.regime_thresholds
    assert (
        snapshot.extension_threshold_for_reversion
        == DEFAULT_THRESHOLDS.extension_threshold_for_reversion
    )
    assert (
        snapshot.min_corroborators_for_reversion
        == DEFAULT_THRESHOLDS.min_corroborators_for_reversion
    )
