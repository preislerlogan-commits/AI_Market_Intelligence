"""The Windows Time clock-health source, offline only: captured synthetic
``w32tm`` results through an injected runner (healthy, stale, excessive
offset, malformed, localized, unsynchronized, timeout, failure, unsupported
platform, inconsistent clocks, unset policy), the subprocess runner's
boundaries, and the briefing integration. No command or time server is run."""

from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import pytest

from market_intelligence.briefing import fakes as F
from market_intelligence.briefing import registry as BR
from market_intelligence.briefing import workflow as W
from market_intelligence.clock_sources import windows_time as WT
from market_intelligence.clock_sources.base import (
    ClockSourceStatus,
    ProcessResult,
    SourceKind,
)
from market_intelligence.evidence.registry import ClockPolicy

POLICY = BR.make_briefing_registry().clock_policy  # 1000 ms, 3600 s, 600 s
EXE = WT.w32tm_path(r"C:\Windows")
START = F.FAKE_NOW - timedelta(seconds=30)

HEALTHY = """Leap Indicator: 0(no warning)
Stratum: 4 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0312500s
Root Dispersion: 7.7766591s
ReferenceId: 0x14650010 (source IP:  192.0.2.10)
Last Successful Sync Time: 10/29/2026 9:00:00 AM
Source: time.example.test,0x9
Poll Interval: 10 (1024s)

Phase Offset: 0.0001234s
ClockRate: 0.0156250s
State Machine: 2 (Sync)
Time Source Flags: 0 (None)
Server Role: 0 (None)
Last Sync Error: 0 (The command completed successfully.)
Time since Last Good Sync Time: 285.0145838s
"""


def _variant(**replace: str) -> str:
    text = HEALTHY
    for old, new in replace.items():
        text = text.replace(old.replace("_", " "), new)
    return text


@dataclass
class FakeRunner:
    result: ProcessResult
    calls: list[tuple[tuple[str, ...], float]] = field(default_factory=list)

    def run(self, argv, *, timeout_seconds):
        self.calls.append((tuple(argv), timeout_seconds))
        return self.result


def _source(result: ProcessResult, *, platform="win32", policy=POLICY, wall=None, mono=None):
    walls = iter(wall or [START, START + timedelta(milliseconds=40)])
    monos = iter(mono or [1_000_000_000, 1_040_000_000])
    runner = FakeRunner(result)
    source = WT.WindowsTimeSource(
        runner=runner,
        policy=policy,
        executable=EXE,
        platform=platform,
        wall_clock=lambda: next(walls),
        monotonic_ns=lambda: next(monos),
    )
    return source, runner


def _measure(stdout=HEALTHY, returncode=0, **kwargs):
    source, runner = _source(ProcessResult(returncode=returncode, stdout=stdout), **kwargs)
    return source.measure(), runner


# --- Captured synthetic results ------------------------------------------------------------------


def test_a_healthy_result_becomes_the_existing_clock_health_reading():
    result, runner = _measure()
    assert runner.calls == [((str(EXE), "/query", "/status", "/verbose"), 10.0)]
    assert result.status is ClockSourceStatus.HEALTHY
    assert result.source_kind is SourceKind.NETWORK_TIME_SERVER and result.synchronized
    assert (result.stratum, result.offset_ms) == (4, 0)
    measured = START + timedelta(milliseconds=40)
    assert result.measured_at_utc == measured == result.wall_finished_at_utc
    assert result.wall_started_at_utc == START and result.monotonic_elapsed_ns == 40_000_000
    reading = result.reading
    assert reading.measured_at_utc == measured and reading.offset_ms == 0
    assert reading.last_sync_at_utc == measured - timedelta(seconds=285.0145838)
    # Bounded fields only: no raw output, server or machine name, address or path.
    text = repr(result)
    for leak in ("time.example.test", "192.0.2.10", "Last Successful", "w32tm", "System32"):
        assert leak not in text


@pytest.mark.parametrize(
    ("stdout", "status"),
    [
        (_variant(**{"285.0145838s": "7200.0000000s"}), ClockSourceStatus.SYNC_TOO_OLD),
        (_variant(**{"0.0001234s": "2.5000000s"}), ClockSourceStatus.OFFSET_EXCEEDED),
        (_variant(**{"0.0001234s": "-1.5000000s"}), ClockSourceStatus.OFFSET_EXCEEDED),
        (
            _variant(**{"time.example.test,0x9": "Local CMOS Clock"}),
            ClockSourceStatus.NOT_SYNCHRONIZED,
        ),
        (
            _variant(**{"time.example.test,0x9": "Free-running System Clock"}),
            ClockSourceStatus.NOT_SYNCHRONIZED,
        ),
        (_variant(**{"0(no_warning)": "3(not synchronized)"}), ClockSourceStatus.NOT_SYNCHRONIZED),
        (
            _variant(**{"Last Sync Error: 0": "Last Sync Error: 1"}),
            ClockSourceStatus.NOT_SYNCHRONIZED,
        ),
    ],
)
def test_stale_offset_or_unsynchronized_results_fail_closed(stdout, status):
    result, _ = _measure(stdout)
    assert result.status is status and result.reading is None


@pytest.mark.parametrize(
    "stdout",
    [
        _variant(**{"Phase Offset: 0.0001234s\n": ""}),  # a required field is missing
        _variant(**{"0.0001234s": "0,0001234s"}),  # localized decimal separator
        _variant(**{"Time since Last Good Sync Time": "Zeit seit der letzten Synchronisierung"}),
        _variant(**{"Stratum: 4": "Stratum: vier"}),
        HEALTHY.replace("Stratum", "Stratum\u00e9"),  # non-ASCII (localized) text
        HEALTHY + "Stratum: 5 (duplicate)\n",  # an ambiguous repeated field
        "",
    ],
)
def test_malformed_or_localized_output_fails_closed(stdout):
    result, _ = _measure(stdout)
    assert result.status is ClockSourceStatus.MALFORMED_OUTPUT and result.reading is None


def test_timeout_failure_and_unsupported_platform_fail_closed():
    no_exit_code, _ = _measure(stdout="", returncode=None)  # the process never started
    assert no_exit_code.status is ClockSourceStatus.COMMAND_FAILED
    source, _ = _source(ProcessResult(returncode=None, stdout="", timed_out=True))
    assert source.measure().status is ClockSourceStatus.TIMEOUT
    failed, _ = _measure(returncode=1)
    assert failed.status is ClockSourceStatus.COMMAND_FAILED
    unsupported, runner = _measure(platform="linux")
    assert unsupported.status is ClockSourceStatus.UNSUPPORTED_PLATFORM
    assert runner.calls == []  # nothing runs on an unsupported system


@pytest.mark.parametrize(
    ("wall", "mono"),
    [
        ([START, START - timedelta(seconds=1)], None),  # wall clock moved backwards
        (None, [2_000_000_000, 1_000_000_000]),  # monotonic clock moved backwards
        ([START, START + timedelta(seconds=40)], None),  # wall jumped past the timeout
        ([START, START.replace(tzinfo=None)], None),  # a naive timestamp
    ],
)
def test_inconsistent_clocks_fail_closed(wall, mono):
    result, _ = _measure(wall=wall, mono=mono)
    assert result.status is ClockSourceStatus.INCONSISTENT_CLOCKS and result.reading is None


def test_an_unset_policy_threshold_reports_unknown_instead_of_choosing_one():
    unset = ClockPolicy(policy_id="clock.unset.v1", clock_producer_id="system_clock_health")
    result, _ = _measure(policy=unset)
    assert result.status is ClockSourceStatus.UNKNOWN_POLICY_UNSET and result.reading is None


# --- The subprocess runner -----------------------------------------------------------------------


def test_the_runner_executes_only_the_allowlisted_query_without_a_shell(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, HEALTHY.encode("ascii"), b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    runner = WT.SubprocessRunner(EXE)
    result = runner.run((str(EXE), *WT.QUERY_ARGS), timeout_seconds=5)
    assert result.returncode == 0 and "Stratum" in result.stdout
    assert captured["argv"] == [str(EXE), "/query", "/status", "/verbose"]
    assert captured["shell"] is False and captured["timeout"] == 5
    assert captured["stdin"] is subprocess.DEVNULL and captured["check"] is False
    for argv in (
        ("cmd", "/c", "w32tm /query /status & calc"),
        (str(EXE), "/query", "/status", "/verbose", "& whoami"),
        ("w32tm", *WT.QUERY_ARGS),  # PATH lookup is not allowed
        (str(EXE), "/resync"),
    ):
        with pytest.raises(ValueError):
            runner.run(argv, timeout_seconds=5)
    for timeout in (0, -1, 61):
        with pytest.raises(ValueError):
            runner.run((str(EXE), *WT.QUERY_ARGS), timeout_seconds=timeout)


@pytest.mark.parametrize(
    ("effect", "expected"),
    [
        (subprocess.TimeoutExpired(cmd="w32tm", timeout=5), ProcessResult(None, "", True)),
        (OSError("not found"), ProcessResult(None, "")),
    ],
)
def test_runner_timeouts_and_launch_failures_are_bounded(monkeypatch, effect, expected):
    def fake_run(argv, **kwargs):
        raise effect

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = WT.SubprocessRunner(EXE).run((str(EXE), *WT.QUERY_ARGS), timeout_seconds=5)
    assert result == expected


@pytest.mark.parametrize("stdout", [b"x" * (WT.MAX_OUTPUT_BYTES + 10), "Stratum: \u00fc".encode()])
def test_oversized_or_non_ascii_output_is_malformed(monkeypatch, stdout):
    monkeypatch.setattr(
        subprocess, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, stdout, b"")
    )
    raw = WT.SubprocessRunner(EXE).run((str(EXE), *WT.QUERY_ARGS), timeout_seconds=5)
    source, _ = _source(raw)
    assert source.measure().status is ClockSourceStatus.MALFORMED_OUTPUT


def test_no_shell_or_unbounded_process_api_is_used():
    package = Path(WT.__file__).parent
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                assert isinstance(node.value, ast.Constant) and node.value.value is False
            if isinstance(node, ast.Attribute):
                assert node.attr not in {"system", "popen", "Popen", "check_output", "call"}
    calls = [
        n
        for n in ast.walk(ast.parse(Path(WT.__file__).read_text(encoding="utf-8")))
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "run"
    ]
    (run,) = [c for c in calls if getattr(c.func.value, "id", None) == "subprocess"]
    assert {k.arg for k in run.keywords} >= {"shell", "timeout", "stdin", "check"}


# --- Briefing integration ------------------------------------------------------------------------


def test_a_healthy_measurement_drives_an_offline_briefing(tmp_path):
    wall = [F.FAKE_NOW - timedelta(seconds=30), F.FAKE_NOW - timedelta(seconds=29)]
    source, _ = _source(ProcessResult(0, HEALTHY), wall=wall)
    deps = W.BriefingDependencies(
        sources=F.FakeSources(),
        model_client=F.FakeModelClient(),
        clock=F.fake_clock,
        clock_health=lambda: source.measure().reading,
    )
    result = W.run_briefing(W.BriefingRequest(), deps, workspace_parent=tmp_path)
    assert result.model.briefing.bundle.strip.clock == "Clock healthy"


def test_windows_clock_health_refuses_any_unhealthy_result(monkeypatch):
    stale, _ = _source(ProcessResult(0, _variant(**{"285.0145838s": "7200.0000000s"})))
    monkeypatch.setattr(W, "system_windows_time_source", lambda policy: stale)
    with pytest.raises(W.BriefingRefusal, match="clock_sync_too_old"):
        W.windows_clock_health(POLICY)()


def _blank_settings():
    from market_intelligence.config.settings import Settings

    blank = ("alpaca_api_key", "alpaca_api_secret", "fred_api_key", "openai_api_key")
    return Settings(_env_file=None, **{name: None for name in blank})


def test_the_live_builder_still_refuses_before_building_the_source(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the clock source must not be built before authorization")

    monkeypatch.setattr(W, "system_windows_time_source", refuse)
    with pytest.raises(W.BriefingRefusal, match="live_run_not_authorized"):
        W.live_dependencies(
            _blank_settings(), W.LiveAuthorization("not-recorded"), clock=F.fake_clock
        )
    assert W.AUTHORIZED_LIVE_RECORDS == frozenset()
