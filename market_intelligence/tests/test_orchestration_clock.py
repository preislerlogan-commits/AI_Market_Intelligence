"""Tests for market_intelligence.orchestration.clock.

Pure clock-resolution tests -- no network, database, or Settings involved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from market_intelligence.orchestration.clock import ClockError, resolve_as_of


def test_resolve_as_of_calls_clock_exactly_once():
    calls = []

    def counting_clock():
        calls.append(1)
        return datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)

    resolve_as_of(counting_clock)

    assert len(calls) == 1


def test_resolve_as_of_normalizes_non_utc_timezone_to_utc():
    as_of_est = datetime(2026, 8, 23, 10, 0, 0, tzinfo=timezone(timedelta(hours=-4)))

    resolved = resolve_as_of(lambda: as_of_est)

    assert resolved.tzinfo == UTC
    assert resolved == datetime(2026, 8, 23, 14, 0, 0, tzinfo=UTC)


def test_resolve_as_of_rejects_naive_datetime():
    with pytest.raises(ClockError):
        resolve_as_of(lambda: datetime(2026, 8, 23, 12, 0, 0))


def test_resolve_as_of_rejects_non_datetime_return_value():
    with pytest.raises(ClockError):
        resolve_as_of(lambda: "2026-08-23T12:00:00Z")
