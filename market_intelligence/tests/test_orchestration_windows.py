"""Tests for market_intelligence.orchestration.windows.

Pure date-arithmetic tests -- no network, database, or Settings involved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from market_intelligence.orchestration.windows import bars_window, fred_observations_window


def test_bars_window_ends_at_start_of_as_of_utc_day():
    as_of = datetime(2026, 8, 23, 14, 30, 0, tzinfo=UTC)
    start, end = bars_window(as_of, lookback_days=5)

    assert end == "2026-08-23T00:00:00Z"
    assert start == "2026-08-18T00:00:00Z"


def test_bars_window_never_includes_partial_current_day():
    as_of = datetime(2026, 8, 23, 23, 59, 59, tzinfo=UTC)
    _, end = bars_window(as_of, lookback_days=1)
    assert end == "2026-08-23T00:00:00Z"
    assert end <= as_of.isoformat().replace("+00:00", "Z")


def test_bars_window_start_strictly_before_end():
    as_of = datetime(2026, 8, 23, 0, 0, 0, tzinfo=UTC)
    start, end = bars_window(as_of, lookback_days=5)
    assert start < end


def test_bars_window_is_deterministic_for_same_as_of():
    as_of = datetime(2026, 8, 23, 9, 0, 0, tzinfo=UTC)
    assert bars_window(as_of, 5) == bars_window(as_of, 5)


def test_bars_window_converts_non_utc_as_of_to_utc():
    from datetime import timedelta, timezone

    as_of_est = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
    start, end = bars_window(as_of_est, lookback_days=1)
    # 10:00 EST == 14:00 UTC -> still same UTC calendar day
    assert end == "2026-08-23T00:00:00Z"
    assert start == "2026-08-22T00:00:00Z"


def test_fred_window_ends_day_before_as_of():
    as_of = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    start, end = fred_observations_window(as_of, lookback_days=90)

    assert end == "2026-08-22"
    assert start == "2026-05-24"


def test_fred_window_never_requests_as_of_calendar_date():
    as_of = datetime(2026, 8, 23, 0, 0, 1, tzinfo=UTC)
    _, end = fred_observations_window(as_of, lookback_days=1)
    assert end != as_of.date().isoformat()


def test_fred_window_start_strictly_before_end():
    as_of = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    start, end = fred_observations_window(as_of, lookback_days=90)
    assert start < end


def test_fred_window_is_deterministic_for_same_as_of():
    as_of = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
    assert fred_observations_window(as_of, 90) == fred_observations_window(as_of, 90)
