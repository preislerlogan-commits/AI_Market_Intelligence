"""The shared clock-source contract and the policy evaluation every source
uses. Thresholds come from the registry's ``ClockPolicy`` only; an unset
threshold yields ``unknown``, never a chosen default."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from market_intelligence.evidence.registry import ClockPolicy
from market_intelligence.evidence_adapters.clock import ClockHealthReading


class ClockSourceStatus(StrEnum):
    HEALTHY = "healthy"
    UNKNOWN_POLICY_UNSET = "unknown_policy_unset"
    OFFSET_EXCEEDED = "offset_exceeded"
    SYNC_TOO_OLD = "sync_too_old"
    NOT_SYNCHRONIZED = "not_synchronized"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    TIMEOUT = "timeout"
    COMMAND_FAILED = "command_failed"
    MALFORMED_OUTPUT = "malformed_output"
    INCONSISTENT_CLOCKS = "inconsistent_clocks"


class SourceKind(StrEnum):
    """What the clock is synchronized to, never a machine or server name."""

    NETWORK_TIME_SERVER = "network_time_server"
    VIRTUAL_MACHINE_HOST = "virtual_machine_host"
    LOCAL_CMOS_CLOCK = "local_cmos_clock"
    FREE_RUNNING_CLOCK = "free_running_clock"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessResult:
    """A bounded process outcome. ``stdout`` is held only for parsing and is
    never placed in a ``ClockSourceResult``."""

    returncode: int | None
    stdout: str
    timed_out: bool = False


class ProcessRunner(Protocol):
    """Runs one argument vector without a shell, within ``timeout_seconds``."""

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProcessResult: ...


@dataclass(frozen=True)
class ClockSourceResult:
    """Bounded status fields only: no raw output, machine name, path or
    credential. ``reading`` is set only when ``status`` is ``healthy``."""

    status: ClockSourceStatus
    source_kind: SourceKind | None = None
    synchronized: bool | None = None
    stratum: int | None = None
    offset_ms: int | None = None
    last_sync_at_utc: datetime | None = None
    measured_at_utc: datetime | None = None
    wall_started_at_utc: datetime | None = None
    wall_finished_at_utc: datetime | None = None
    monotonic_elapsed_ns: int | None = None
    reading: ClockHealthReading | None = None

    def __post_init__(self) -> None:
        if (self.status is ClockSourceStatus.HEALTHY) != (self.reading is not None):
            raise ValueError("a reading is carried only by a healthy result")


def evaluate(
    policy: ClockPolicy, *, offset_ms: int, last_sync_at: datetime, measured_at: datetime
) -> ClockSourceStatus:
    """The registry policy's verdict on one measurement, in the freshness
    layer's order. Unset thresholds give ``unknown``; nothing is assumed."""
    if policy.max_abs_offset_ms is None or policy.max_sync_age_seconds is None:
        return ClockSourceStatus.UNKNOWN_POLICY_UNSET
    if abs(offset_ms) > policy.max_abs_offset_ms:
        return ClockSourceStatus.OFFSET_EXCEEDED
    if last_sync_at > measured_at or measured_at - last_sync_at > timedelta(
        seconds=policy.max_sync_age_seconds
    ):
        return ClockSourceStatus.SYNC_TOO_OLD
    return ClockSourceStatus.HEALTHY
