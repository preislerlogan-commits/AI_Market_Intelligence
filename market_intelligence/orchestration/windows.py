"""Deterministic, conservative date-window computation for orchestration jobs.

Every job in one orchestration run computes its window from the same
injected UTC "as-of" instant (see
``market_intelligence/orchestration/clock.py``), so a run is fully
reproducible and every job in it agrees on "now". No window ever includes a
partial/still-in-progress current period.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def bars_window(as_of: datetime, lookback_days: int) -> tuple[str, str]:
    """A fully completed historical bars window ending at the start of ``as_of``'s UTC day.

    Mirrors ``scripts/ingest_alpaca_bars.py``'s default-window logic: using
    the start of the as-of UTC day (rather than the as-of instant itself)
    as the window's end deliberately excludes any still-in-progress
    trading session, so the window only ever covers completed history.
    """
    as_of_utc = as_of.astimezone(UTC)
    end_dt = as_of_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=lookback_days)
    return (
        start_dt.isoformat().replace("+00:00", "Z"),
        end_dt.isoformat().replace("+00:00", "Z"),
    )


def fred_observations_window(as_of: datetime, lookback_days: int) -> tuple[str, str]:
    """A fully completed historical observations window ending the day before ``as_of``.

    Mirrors ``scripts/ingest_fred_observations.py``'s default-window logic:
    ending at the day before ``as_of`` (rather than ``as_of``'s own
    calendar date) deliberately avoids requesting a still-possibly-revised
    or not-yet-published "today" observation for daily/weekly series.
    """
    as_of_utc = as_of.astimezone(UTC)
    end_date = (as_of_utc - timedelta(days=1)).date()
    start_date = end_date - timedelta(days=lookback_days)
    return start_date.isoformat(), end_date.isoformat()
