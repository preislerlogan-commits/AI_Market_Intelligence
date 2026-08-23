"""Injectable UTC clock for orchestration runs.

Every job within one orchestration run must share exactly one "as-of"
instant, so the whole run's date windows are computed deterministically and
reproducibly (see ``market_intelligence/orchestration/windows.py``). Tests
always inject a fixed clock; real usage defaults to the current UTC
instant.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    """The default clock: the current UTC instant."""
    return datetime.now(UTC)
