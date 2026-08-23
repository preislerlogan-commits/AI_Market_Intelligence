"""Injectable UTC clock for orchestration runs.

Every job within one orchestration run must share exactly one "as-of"
instant, so the whole run's date windows are computed deterministically and
reproducibly (see ``market_intelligence/orchestration/windows.py``). Tests
always inject a fixed clock; real usage defaults to the current UTC
instant.

``resolve_as_of`` is the single place that calls an injected ``Clock`` and
validates its result: both ``build_plan`` and ``execute_run`` (see
``market_intelligence/orchestration/runner.py``) call the clock they were
given exactly once, through this function, and reuse the one resolved
instant for every job in that call -- never calling the injected clock
again afterward.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


class ClockError(RuntimeError):
    """Raised when an injected clock does not return a valid instant.

    Never includes the raw returned value -- only a fixed sanitized message.
    """


def system_clock() -> datetime:
    """The default clock: the current UTC instant."""
    return datetime.now(UTC)


def resolve_as_of(clock: Clock) -> datetime:
    """Call ``clock`` exactly once and return a validated, UTC-normalized instant.

    Raises ``ClockError`` if the clock does not return a timezone-aware
    ``datetime``.
    """
    as_of = clock()
    if not isinstance(as_of, datetime) or as_of.tzinfo is None:
        raise ClockError("Injected clock must return a timezone-aware datetime.")
    return as_of.astimezone(UTC)
