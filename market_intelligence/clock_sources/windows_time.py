"""Windows Time service (``w32tm``) clock-health source.

One fixed, read-only query, ``w32tm /query /status /verbose``, run through an
injected ``ProcessRunner``. The default runner (``SubprocessRunner``) only
ever executes that exact argument vector, with ``shell=False``, a strict
timeout, no standard input and a bounded output size.

The status report is parsed strictly: English labels only and ``.``-decimal
numbers only. Localized, missing or malformed fields fail closed
(``malformed_output``). The last synchronization time is derived from
"Time since Last Good Sync Time" (seconds), so no localized date is ever
parsed. The configured source name is reduced to a bounded ``SourceKind``
and never returned. Wall-clock and monotonic timestamps bracket the
command; an inconsistency between them fails closed.

Thresholds come from the registry's ``ClockPolicy`` (see ``base.evaluate``).
Running this source is a live measurement of the Windows Time service's own
state; it never asks a time server for a fresh sample. It is never run by
tests, and using it in a briefing requires the briefing's separate live
authorization.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from market_intelligence.clock_sources.base import (
    ClockSourceResult,
    ClockSourceStatus,
    ProcessResult,
    ProcessRunner,
    SourceKind,
    evaluate,
)
from market_intelligence.evidence.registry import ClockPolicy
from market_intelligence.evidence_adapters.clock import ClockHealthReading

QUERY_ARGS = ("/query", "/status", "/verbose")
DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_OUTPUT_BYTES = 16_384
_SAFE_CHARS = re.compile(r"^[\x20-\x7e\r\n\t]*$")
_LINE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ]{0,60}):\s*(?P<value>.*)$")
_SECONDS = re.compile(r"^(?P<n>-?\d+(?:\.\d+)?)s$")
_LEADING_INT = re.compile(r"^(?P<n>\d+)\b")
_REQUIRED = (
    "Leap Indicator",
    "Stratum",
    "Source",
    "Phase Offset",
    "Last Sync Error",
    "Time since Last Good Sync Time",
)


def w32tm_path(system_root: str | None = None) -> Path:
    """The system's own ``w32tm.exe``, by absolute path (no PATH lookup)."""
    return Path(system_root or r"C:\Windows") / "System32" / "w32tm.exe"


class SubprocessRunner:
    """Runs only the allowlisted ``w32tm`` query. No shell, no input, a
    strict timeout, ASCII output bounded to ``MAX_OUTPUT_BYTES``."""

    def __init__(self, executable: Path) -> None:
        self._allowed = (str(executable), *QUERY_ARGS)

    def run(self, argv: Sequence[str], *, timeout_seconds: float) -> ProcessResult:
        if tuple(argv) != self._allowed:
            raise ValueError("only the allowlisted w32tm query may run")
        if not 0 < timeout_seconds <= 60:
            raise ValueError("timeout must be bounded")
        try:
            completed = subprocess.run(  # noqa: S603 - fixed argv, never a shell
                list(self._allowed),
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProcessResult(returncode=None, stdout="", timed_out=True)
        except OSError:
            return ProcessResult(returncode=None, stdout="")
        raw = completed.stdout[: MAX_OUTPUT_BYTES + 1]
        if len(raw) > MAX_OUTPUT_BYTES:
            return ProcessResult(returncode=completed.returncode, stdout="")  # parsed as malformed
        return ProcessResult(
            returncode=completed.returncode, stdout=raw.decode("ascii", errors="replace")
        )


def _fields(text: str) -> dict[str, str] | None:
    if not _SAFE_CHARS.match(text):
        return None  # non-ASCII (localized) or control characters
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LINE.match(line.strip())
        if match is None:
            continue
        label = match["label"].strip()
        if label in fields:
            return None  # a repeated field is ambiguous
        fields[label] = match["value"].strip()
    if not all(label in fields for label in _REQUIRED):
        return None
    return fields


def _seconds(value: str) -> float | None:
    match = _SECONDS.match(value)
    return float(match["n"]) if match else None


def _leading_int(value: str) -> int | None:
    match = _LEADING_INT.match(value)
    return int(match["n"]) if match else None


def _source_kind(value: str) -> SourceKind:
    lowered = value.lower()
    if lowered.startswith("local cmos clock"):
        return SourceKind.LOCAL_CMOS_CLOCK
    if lowered.startswith("free-running system clock"):
        return SourceKind.FREE_RUNNING_CLOCK
    if lowered.startswith("vm ic time synchronization provider"):
        return SourceKind.VIRTUAL_MACHINE_HOST
    if value and " " not in value:  # a host name, optionally ",0x<flags>"
        return SourceKind.NETWORK_TIME_SERVER
    return SourceKind.UNKNOWN


class WindowsTimeSource:
    """Measures the Windows Time service's synchronization state once."""

    def __init__(
        self,
        *,
        runner: ProcessRunner,
        policy: ClockPolicy,
        executable: Path,
        platform: str,
        wall_clock: Callable[[], datetime],
        monotonic_ns: Callable[[], int],
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._runner = runner
        self._policy = policy
        self._argv = (str(executable), *QUERY_ARGS)
        self._platform = platform
        self._wall_clock = wall_clock
        self._monotonic_ns = monotonic_ns
        self._timeout = timeout_seconds

    def measure(self) -> ClockSourceResult:
        if self._platform != "win32":
            return ClockSourceResult(status=ClockSourceStatus.UNSUPPORTED_PLATFORM)
        wall_start = self._wall_clock()
        mono_start = self._monotonic_ns()
        outcome = self._runner.run(self._argv, timeout_seconds=self._timeout)
        mono_end = self._monotonic_ns()
        wall_end = self._wall_clock()
        timing = dict(
            wall_started_at_utc=wall_start,
            wall_finished_at_utc=wall_end,
            monotonic_elapsed_ns=mono_end - mono_start,
        )
        if outcome.timed_out:
            return ClockSourceResult(status=ClockSourceStatus.TIMEOUT, **timing)
        if outcome.returncode != 0:
            return ClockSourceResult(status=ClockSourceStatus.COMMAND_FAILED, **timing)
        if not self._clocks_consistent(wall_start, wall_end, mono_start, mono_end):
            return ClockSourceResult(status=ClockSourceStatus.INCONSISTENT_CLOCKS, **timing)
        fields = _fields(outcome.stdout)
        if fields is None:
            return ClockSourceResult(status=ClockSourceStatus.MALFORMED_OUTPUT, **timing)
        offset_s = _seconds(fields["Phase Offset"])
        since_sync_s = _seconds(fields["Time since Last Good Sync Time"])
        stratum = _leading_int(fields["Stratum"])
        leap = _leading_int(fields["Leap Indicator"])
        sync_error = _leading_int(fields["Last Sync Error"])
        if None in (offset_s, since_sync_s, stratum, leap, sync_error) or since_sync_s < 0:
            return ClockSourceResult(status=ClockSourceStatus.MALFORMED_OUTPUT, **timing)
        kind = _source_kind(fields["Source"])
        measured_at = wall_end
        offset_ms = round(offset_s * 1000)
        last_sync = measured_at - timedelta(seconds=since_sync_s)
        synchronized = (
            leap != 3
            and sync_error == 0
            and kind in (SourceKind.NETWORK_TIME_SERVER, SourceKind.VIRTUAL_MACHINE_HOST)
        )
        observed = dict(
            source_kind=kind,
            synchronized=synchronized,
            stratum=stratum,
            offset_ms=offset_ms,
            last_sync_at_utc=last_sync,
            measured_at_utc=measured_at,
            **timing,
        )
        if not synchronized:
            return ClockSourceResult(status=ClockSourceStatus.NOT_SYNCHRONIZED, **observed)
        status = evaluate(
            self._policy, offset_ms=offset_ms, last_sync_at=last_sync, measured_at=measured_at
        )
        if status is not ClockSourceStatus.HEALTHY:
            return ClockSourceResult(status=status, **observed)
        try:
            reading = ClockHealthReading(
                measured_at_utc=measured_at, offset_ms=offset_ms, last_sync_at_utc=last_sync
            )
        except ValueError:
            return ClockSourceResult(status=ClockSourceStatus.MALFORMED_OUTPUT, **timing)
        return ClockSourceResult(status=status, reading=reading, **observed)

    def _clocks_consistent(
        self, wall_start: datetime, wall_end: datetime, mono_start: int, mono_end: int
    ) -> bool:
        """Both clocks moved forward, the measurement stayed within the
        command timeout, and wall time agreed with monotonic time to within
        that same bound. No health threshold is involved."""
        if wall_start.utcoffset() is None or wall_end.utcoffset() is None:
            return False
        if wall_end < wall_start or mono_end < mono_start:
            return False
        bound = timedelta(seconds=self._timeout)
        wall_elapsed = wall_end - wall_start
        mono_elapsed = timedelta(microseconds=(mono_end - mono_start) / 1000)
        return (
            wall_elapsed <= bound
            and mono_elapsed <= bound
            and abs(wall_elapsed - mono_elapsed) <= bound
        )


def system_windows_time_source(policy: ClockPolicy) -> WindowsTimeSource:
    """The real source, wired to the operating system. Building it makes no
    request; ``measure()`` runs the local query once."""
    import os
    import sys

    executable = w32tm_path(os.environ.get("SystemRoot"))
    return WindowsTimeSource(
        runner=SubprocessRunner(executable),
        policy=policy,
        executable=executable,
        platform=sys.platform,
        wall_clock=lambda: datetime.now(UTC),
        monotonic_ns=time.monotonic_ns,
    )
