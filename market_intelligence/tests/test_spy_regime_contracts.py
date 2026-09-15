"""Tests for market_intelligence.market_features.spy_regime_contracts.

These tests never touch a network or a database -- the contracts module
under test has no I/O of any kind. Fixtures are built directly in Decimal/
datetime, never through a connector.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    PriorDayLevels,
    RegimeEngineInput,
)

EASTERN = ZoneInfo("America/New_York")

# A Wednesday, EDT (America/New_York is UTC-4 in June).
SESSION_DATE = date(2026, 6, 10)


def et(day: date, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=EASTERN)


def make_bar(
    *,
    timestamp: datetime,
    open: str = "500.00",
    high: str = "500.50",
    low: str = "499.50",
    close: str = "500.10",
    volume: int = 10_000,
) -> IntradayBar:
    return IntradayBar(
        timestamp=timestamp,
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def default_input(**overrides) -> dict:
    values = {
        "session_date": SESSION_DATE,
        "bars": [make_bar(timestamp=et(SESSION_DATE, 9, 30))],
        "prior_day": None,
        "same_time_historical_volume_baseline": None,
        "catalyst_state": CatalystState.UNKNOWN,
        "breadth_state": BreadthState.UNAVAILABLE,
    }
    values.update(overrides)
    return values


# --- IntradayBar -------------------------------------------------------------------


def test_valid_bar_accepted():
    bar = make_bar(timestamp=et(SESSION_DATE, 9, 30))
    assert bar.close == Decimal("500.10")


def test_bar_rejects_naive_timestamp():
    with pytest.raises(ValidationError):
        IntradayBar(
            timestamp=datetime(2026, 6, 10, 9, 30),
            open=Decimal("500"),
            high=Decimal("501"),
            low=Decimal("499"),
            close=Decimal("500"),
            volume=100,
        )


@pytest.mark.parametrize(
    "field", ["open", "high", "low", "close"],
)
def test_bar_rejects_nonfinite_price(field):
    kwargs = {
        "timestamp": et(SESSION_DATE, 9, 30),
        "open": Decimal("500"),
        "high": Decimal("501"),
        "low": Decimal("499"),
        "close": Decimal("500"),
        "volume": 100,
    }
    kwargs[field] = Decimal("NaN")
    with pytest.raises(ValidationError):
        IntradayBar(**kwargs)


def test_bar_rejects_infinite_price():
    with pytest.raises(ValidationError):
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30),
            open=Decimal("Infinity"),
            high=Decimal("501"),
            low=Decimal("499"),
            close=Decimal("500"),
            volume=100,
        )


def test_bar_rejects_zero_or_negative_price():
    with pytest.raises(ValidationError):
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30),
            open=Decimal("0"),
            high=Decimal("501"),
            low=Decimal("499"),
            close=Decimal("500"),
            volume=100,
        )


def test_bar_accepts_zero_volume():
    bar = make_bar(timestamp=et(SESSION_DATE, 9, 30), volume=0)
    assert bar.volume == 0


def test_bar_rejects_negative_volume():
    with pytest.raises(ValidationError):
        make_bar(timestamp=et(SESSION_DATE, 9, 30), volume=-1)


def test_bar_rejects_inconsistent_candle_high_below_close():
    with pytest.raises(ValidationError) as exc:
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30),
            open=Decimal("500"),
            high=Decimal("500.05"),
            low=Decimal("499.90"),
            close=Decimal("777.777777"),
            volume=100,
        )
    assert "777.777777" not in str(exc.value)


def test_bar_rejects_inconsistent_candle_low_above_open():
    with pytest.raises(ValidationError):
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30),
            open=Decimal("500"),
            high=Decimal("501"),
            low=Decimal("500.50"),
            close=Decimal("500.20"),
            volume=100,
        )


def test_bar_rejects_extra_field():
    with pytest.raises(ValidationError):
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30),
            open=Decimal("500"),
            high=Decimal("501"),
            low=Decimal("499"),
            close=Decimal("500"),
            volume=100,
            vwap=Decimal("500"),
        )


def test_bar_is_frozen():
    bar = make_bar(timestamp=et(SESSION_DATE, 9, 30))
    with pytest.raises(ValidationError):
        bar.close = Decimal("999")


# --- PriorDayLevels -----------------------------------------------------------------


def test_prior_day_levels_valid():
    levels = PriorDayLevels(high=Decimal("501"), low=Decimal("498"), close=Decimal("500"))
    assert levels.close == Decimal("500")


def test_prior_day_levels_rejects_inconsistent_values():
    with pytest.raises(ValidationError):
        PriorDayLevels(high=Decimal("495"), low=Decimal("498"), close=Decimal("500"))


def test_prior_day_levels_rejects_nonfinite():
    with pytest.raises(ValidationError):
        PriorDayLevels(high=Decimal("NaN"), low=Decimal("498"), close=Decimal("500"))


# --- RegimeEngineInput: symbol / required fields -------------------------------------


def test_input_rejects_non_spy_symbol():
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(symbol="QQQ"))


def test_input_defaults_symbol_to_spy():
    engine_input = RegimeEngineInput(**default_input())
    assert engine_input.symbol == "SPY"


def test_input_requires_catalyst_state():
    values = default_input()
    del values["catalyst_state"]
    with pytest.raises(ValidationError):
        RegimeEngineInput(**values)


def test_input_requires_breadth_state():
    values = default_input()
    del values["breadth_state"]
    with pytest.raises(ValidationError):
        RegimeEngineInput(**values)


def test_input_rejects_extra_field():
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(unexpected_field=1))


def test_input_rejects_empty_bars():
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(bars=[]))


def test_input_rejects_too_many_bars():
    # 401 distinct 1-second-spaced bars, all still inside the regular session window.
    base = et(SESSION_DATE, 9, 30)
    bars = [make_bar(timestamp=base + timedelta(seconds=i)) for i in range(401)]
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(bars=bars))


# --- RegimeEngineInput: session_date weekday -----------------------------------------


def test_input_rejects_weekend_session_date():
    weekend_date = date(2026, 6, 13)  # Saturday
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(
                session_date=weekend_date,
                bars=[make_bar(timestamp=et(weekend_date, 9, 30))],
            )
        )


# --- RegimeEngineInput: bar ordering / duplicates ------------------------------------


def test_input_rejects_unordered_bars():
    bars = [
        make_bar(timestamp=et(SESSION_DATE, 9, 35)),
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
    ]
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(bars=bars))


def test_input_rejects_duplicate_bar_timestamps():
    bars = [
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
    ]
    with pytest.raises(ValidationError) as exc:
        RegimeEngineInput(**default_input(bars=bars))
    assert "09:30" not in str(exc.value)


def test_input_accepts_strictly_ascending_bars():
    bars = [
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
        make_bar(timestamp=et(SESSION_DATE, 9, 35)),
        make_bar(timestamp=et(SESSION_DATE, 9, 40)),
    ]
    engine_input = RegimeEngineInput(**default_input(bars=bars))
    assert len(engine_input.bars) == 3


# --- RegimeEngineInput: 5-minute grid alignment / cadence -----------------------------


def test_input_accepts_bars_on_the_five_minute_grid():
    bars = [
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
        make_bar(timestamp=et(SESSION_DATE, 9, 35)),
        make_bar(timestamp=et(SESSION_DATE, 15, 55)),
    ]
    engine_input = RegimeEngineInput(**default_input(bars=bars))
    assert len(engine_input.bars) == 3


def test_input_rejects_bar_off_the_five_minute_grid():
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 9, 31))]))


def test_input_rejects_bar_with_nonzero_seconds():
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 9, 30, second=15))])
        )


def test_input_rejects_an_off_grid_bar_mixed_among_aligned_bars():
    # A stray 1-minute bar mixed among otherwise-5-minute-aligned bars must
    # be rejected because its own offset is not a multiple of 5 minutes --
    # not because this validator detects "mixed cadence" in general. A
    # coarser-but-still-grid-aligned gap (e.g. 10-minute spacing) is *not*
    # caught here; see test_spy_regime_features.py
    # (test_session_incomplete_for_ten_minute_spacing) for where it is.
    bars = [
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
        make_bar(timestamp=et(SESSION_DATE, 9, 31)),
        make_bar(timestamp=et(SESSION_DATE, 9, 35)),
    ]
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(bars=bars))


def test_input_accepts_bars_with_an_internal_gap():
    # A missing 09:40 bar (09:35 -> 09:45) is not a malformed input at this
    # layer -- both bars are individually grid-aligned and strictly
    # ascending. The gap is handled by feature computation (see
    # test_spy_regime_features.py), not rejected here.
    bars = [
        make_bar(timestamp=et(SESSION_DATE, 9, 30)),
        make_bar(timestamp=et(SESSION_DATE, 9, 35)),
        make_bar(timestamp=et(SESSION_DATE, 9, 45)),
    ]
    engine_input = RegimeEngineInput(**default_input(bars=bars))
    assert len(engine_input.bars) == 3


def test_input_rejects_grid_misaligned_bar_around_spring_forward_transition():
    # Grid alignment is checked on the America/New_York wall-clock time, so
    # it gives the same answer regardless of the underlying UTC offset --
    # 09:31 ET is off-grid whether it is EST or EDT.
    day = date(2026, 3, 9)  # Monday after the U.S. spring-forward date.
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(session_date=day, bars=[make_bar(timestamp=et(day, 9, 31))])
        )


def test_input_accepts_grid_aligned_bar_around_fall_back_transition():
    day = date(2026, 11, 2)  # Monday after the U.S. fall-back date.
    engine_input = RegimeEngineInput(
        **default_input(session_date=day, bars=[make_bar(timestamp=et(day, 9, 35))])
    )
    assert len(engine_input.bars) == 1


# --- RegimeEngineInput: regular-session window ---------------------------------------


def test_input_rejects_premarket_bar():
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 9, 0))])
        )


def test_input_rejects_after_hours_bar():
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 16, 5))])
        )


def test_input_rejects_bar_exactly_at_session_close():
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 16, 0))])
        )


def test_input_accepts_bar_exactly_at_session_open():
    engine_input = RegimeEngineInput(
        **default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 9, 30))])
    )
    assert len(engine_input.bars) == 1


def test_input_accepts_bar_just_before_session_close():
    # 15:55 is the last grid-aligned 5-minute bar of the regular session
    # (it covers 15:55-16:00); 16:00 itself is exclusive (session close).
    engine_input = RegimeEngineInput(
        **default_input(
            bars=[
                make_bar(timestamp=et(SESSION_DATE, 9, 30)),
                make_bar(timestamp=et(SESSION_DATE, 15, 55)),
            ]
        )
    )
    assert len(engine_input.bars) == 2


def test_input_rejects_bar_on_a_different_calendar_date():
    other_day = date(2026, 6, 11)
    with pytest.raises(ValidationError):
        RegimeEngineInput(
            **default_input(bars=[make_bar(timestamp=et(other_day, 9, 30))])
        )


# --- DST boundaries -------------------------------------------------------------------


def test_input_accepts_bar_around_spring_forward_transition():
    # 2026-03-08 is the U.S. spring-forward date; 2026-03-09 (Monday) is EDT (UTC-4),
    # so 09:30 ET is 13:30 UTC. The bar's timestamp is normalized to UTC by
    # validation, so the UTC hour -- not utcoffset() -- is what proves the
    # DST-aware conversion happened.
    day = date(2026, 3, 9)
    bar = make_bar(timestamp=et(day, 9, 30))
    assert bar.timestamp.hour == 13
    engine_input = RegimeEngineInput(**default_input(session_date=day, bars=[bar]))
    assert engine_input.bars[0].timestamp.astimezone(EASTERN).time().hour == 9


def test_input_accepts_bar_around_fall_back_transition():
    # 2026-11-01 is the U.S. fall-back date; 2026-11-02 (Monday) is EST (UTC-5),
    # so 09:30 ET is 14:30 UTC.
    day = date(2026, 11, 2)
    bar = make_bar(timestamp=et(day, 9, 30))
    assert bar.timestamp.hour == 14
    engine_input = RegimeEngineInput(**default_input(session_date=day, bars=[bar]))
    assert engine_input.bars[0].timestamp.astimezone(EASTERN).time().hour == 9


def test_utc_timestamp_correctly_converts_across_dst_for_same_wallclock_time():
    # The same UTC hour-of-day maps to a different ET wall-clock time on either
    # side of the DST boundary -- proving conversion is real, not a fixed offset.
    summer_day = date(2026, 6, 10)
    winter_day = date(2026, 1, 12)
    summer_bar = make_bar(timestamp=et(summer_day, 9, 30))
    winter_bar = make_bar(timestamp=et(winter_day, 9, 30))
    assert summer_bar.timestamp.hour == 13  # 09:30 EDT == 13:30 UTC
    assert winter_bar.timestamp.hour == 14  # 09:30 EST == 14:30 UTC


# --- same_time_historical_volume_baseline --------------------------------------------


def test_input_rejects_zero_volume_baseline():
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(same_time_historical_volume_baseline=Decimal("0")))


def test_input_rejects_negative_volume_baseline():
    with pytest.raises(ValidationError):
        RegimeEngineInput(**default_input(same_time_historical_volume_baseline=Decimal("-1")))


def test_input_accepts_missing_volume_baseline_as_none():
    engine_input = RegimeEngineInput(**default_input(same_time_historical_volume_baseline=None))
    assert engine_input.same_time_historical_volume_baseline is None


# --- Sanitized error messages ---------------------------------------------------------


def test_candle_inconsistency_error_does_not_echo_price_values():
    secret_close = "313131.130000"
    with pytest.raises(ValidationError) as exc:
        IntradayBar(
            timestamp=et(SESSION_DATE, 9, 30),
            open=Decimal("500"),
            high=Decimal("500.05"),
            low=Decimal("499.90"),
            close=Decimal(secret_close),
            volume=100,
        )
    assert secret_close not in str(exc.value)


def test_session_window_error_does_not_echo_timestamp():
    with pytest.raises(ValidationError) as exc:
        RegimeEngineInput(
            **default_input(bars=[make_bar(timestamp=et(SESSION_DATE, 7, 17, 43))])
        )
    message = str(exc.value)
    assert "7:17" not in message
    assert "07:17" not in message
