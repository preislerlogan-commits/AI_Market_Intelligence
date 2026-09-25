"""Tests for scripts/run_spy_vwap_confirmation.py.

Offline only: the CLI consumes a local synthetic
``SpyVwapReversionEvaluationInput`` JSON file; it never opens a database,
makes a network request, or reads a real evaluation artifact. Covers
dry-run, write-mode round trip and provenance hashes, two-path preflight,
no overwrite, no directory creation, the local-only allowlist, traversal and
symlink escapes, partial-write failure behavior, and sanitized output.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from zoneinfo import ZoneInfo

import pytest

from market_intelligence.evaluation.serialization import EvaluationSerializationError
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    BASE_PREREGISTRATION_COMMIT_SHA,
    CLARIFICATION_COMMIT_SHA,
)
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_serialization import (
    read_result,
)
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    SessionBars,
    SpyVwapReversionEvaluationInput,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    input_to_json_str,
    read_record,
    to_json_str,
)
from market_intelligence.market_features.spy_regime_contracts import IntradayBar

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_spy_vwap_confirmation.py"
LOCAL_DIR = REPO_ROOT / "data" / "evaluations" / "local"
EASTERN = ZoneInfo("America/New_York")
NOW = datetime(2031, 5, 1, tzinfo=UTC)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
CONFIRMATION_DAYS = (date(2026, 3, 2), date(2026, 3, 3))


def _session(day: date, seed: int) -> SessionBars:
    bars = []
    price = Decimal("500")
    for i in range(78):
        timestamp = datetime(day.year, day.month, day.day, 9, 30, tzinfo=EASTERN) + timedelta(
            minutes=5 * i
        )
        step = Decimal(((i * 7 + seed) % 11) - 5) / Decimal(10)
        close = price + step
        bars.append(
            IntradayBar(
                timestamp=timestamp,
                open=price,
                high=max(price, close) + Decimal("0.05"),
                low=min(price, close) - Decimal("0.05"),
                close=close,
                volume=1000 + i,
            )
        )
        price = close
    return SessionBars(session_date=day, bars=bars)


def _input(days=CONFIRMATION_DAYS) -> SpyVwapReversionEvaluationInput:
    return SpyVwapReversionEvaluationInput(
        sessions=[_session(day, seed) for seed, day in enumerate(days)]
    )


@pytest.fixture
def input_file(tmp_path) -> Path:
    path = tmp_path / "input.json"
    path.write_bytes(input_to_json_str(_input()).encode("utf-8"))
    return path


@pytest.fixture
def local_output():
    """Factory for uniquely named paths under data/evaluations/local/,
    cleaned up afterwards (the test, not the CLI, creates the directory)."""
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    def _make(name: str) -> Path:
        path = LOCAL_DIR / f"pytest-{uuid.uuid4().hex}-{name}"
        created.append(path)
        return path

    yield _make
    for path in created:
        path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_spy_vwap_confirmation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(cli, capsys, argv) -> tuple[int, dict, str]:
    code = cli.main(argv, now=NOW)
    out = capsys.readouterr().out
    return code, json.loads(out), out


def _argv(input_path, record, confirmation, *, sample="confirmation", commit=COMMIT, write=False):
    argv = [
        "--sample",
        sample,
        "--input",
        str(input_path),
        "--record-output",
        str(record),
        "--confirmation-output",
        str(confirmation),
        "--code-commit",
        commit,
    ]
    return argv + (["--write"] if write else [])


def _assert_sanitized(out: str, *paths: Path) -> None:
    for path in paths:
        assert str(path) not in out
        assert path.name not in out
    assert "2026-03" not in out  # no decision-point dates
    for token in out.replace('"', " ").replace(",", " ").split():
        assert len(token) < 40 or not all(c in "0123456789abcdef" for c in token)  # no hashes


# --- Dry run ------------------------------------------------------------------------------


def test_dry_run_evaluates_offline_and_writes_nothing(cli, capsys, input_file, local_output):
    record, confirmation = local_output("record.json"), local_output("confirmation.json")
    before = sorted(p.name for p in LOCAL_DIR.iterdir())
    code, payload, out = _run(cli, capsys, _argv(input_file, record, confirmation))
    assert code == 0
    assert payload["mode"] == "dry_run"
    assert (payload["record_written"], payload["confirmation_written"]) == (False, False)
    assert payload["unique_session_count"] == 2
    assert payload["study_label"] == "insufficient_sample"
    assert set(payload["primary"]) == {"intraday_30m", "intraday_2h", "to_session_close"}
    assert not record.exists() and not confirmation.exists()
    assert sorted(p.name for p in LOCAL_DIR.iterdir()) == before
    _assert_sanitized(out, input_file, record, confirmation)
    # No statistic, hash, or price ever reaches stdout (the fixed notes only
    # disclaim options/P&L; they carry no value).
    for forbidden in ("estimate", "interval", "p_raw", "p_holm", "sha", "price", "numerator"):
        assert forbidden not in out


def test_dry_run_output_is_deterministic(cli, capsys, input_file, tmp_path):
    argv = _argv(input_file, tmp_path / "r.json", tmp_path / "c.json")
    first = _run(cli, capsys, argv)[2]
    assert _run(cli, capsys, argv)[2] == first


# --- Write mode ----------------------------------------------------------------------------


def test_write_round_trip_with_exact_provenance_hashes(cli, capsys, input_file, local_output):
    record_path, confirmation_path = local_output("record.json"), local_output("confirmation.json")
    code, payload, out = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert code == 0
    assert (payload["record_written"], payload["confirmation_written"]) == (True, True)
    _assert_sanitized(out, input_file, record_path, confirmation_path)

    record = read_record(record_path)
    result = read_result(confirmation_path)
    record_bytes = record_path.read_bytes()
    assert record_bytes == to_json_str(record).encode("utf-8")
    assert result.provenance.evaluation_record_sha256 == hashlib.sha256(record_bytes).hexdigest()
    assert result.provenance.input_sha256 == hashlib.sha256(input_file.read_bytes()).hexdigest()
    assert result.provenance.code_commit_sha == COMMIT
    assert result.provenance.base_preregistration_commit_sha == BASE_PREREGISTRATION_COMMIT_SHA
    assert result.provenance.clarification_commit_sha == CLARIFICATION_COMMIT_SHA
    assert result.generated_at == NOW == record.generated_at


def test_both_paths_are_preflighted_before_anything_is_written(
    cli, capsys, input_file, local_output
):
    record_path, confirmation_path = local_output("record.json"), local_output("confirmation.json")
    confirmation_path.write_text("existing", encoding="utf-8")
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert (code, payload) == (2, {"error": "output_path_refused", "reason": "exists"})
    assert not record_path.exists()
    assert confirmation_path.read_text(encoding="utf-8") == "existing"


def test_existing_record_output_is_never_overwritten(cli, capsys, input_file, local_output):
    record_path, confirmation_path = local_output("record.json"), local_output("confirmation.json")
    record_path.write_text("keep", encoding="utf-8")
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert code == 2 and payload["reason"] == "exists"
    assert record_path.read_text(encoding="utf-8") == "keep"
    assert not confirmation_path.exists()


def test_identical_output_paths_are_refused(cli, capsys, input_file, local_output):
    path = local_output("same.json")
    code, payload, _ = _run(cli, capsys, _argv(input_file, path, path, write=True))
    assert (code, payload["reason"]) == (2, "same_path")
    assert not path.exists()


def test_missing_parent_is_refused_and_never_created(cli, capsys, input_file, local_output):
    missing_dir = LOCAL_DIR / f"pytest-missing-{uuid.uuid4().hex}"
    code, payload, _ = _run(
        cli,
        capsys,
        _argv(input_file, missing_dir / "r.json", local_output("c.json"), write=True),
    )
    assert (code, payload["reason"]) == (2, "missing_parent")
    assert not missing_dir.exists()


@pytest.mark.parametrize(
    "relative",
    ["record.json", "docs/record.json", "data/evaluations/record.json", "data/evaluations/local"],
)
def test_outputs_outside_the_local_directory_are_refused(
    cli, capsys, input_file, local_output, relative
):
    target = REPO_ROOT / relative
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, target, local_output("c.json"), write=True)
    )
    assert (code, payload["error"]) == (2, "output_path_refused")
    assert payload["reason"] in {"outside_allowed_directory"}


def test_output_outside_the_repository_is_refused(cli, capsys, input_file, tmp_path, local_output):
    code, payload, _ = _run(
        cli,
        capsys,
        _argv(input_file, local_output("r.json"), tmp_path / "c.json", write=True),
    )
    assert (code, payload["reason"]) == (2, "outside_allowed_directory")
    assert not (tmp_path / "c.json").exists()


def test_dotdot_traversal_is_refused_even_when_it_resolves_inside(
    cli, capsys, input_file, local_output
):
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    sneaky = LOCAL_DIR / ".." / "local" / f"pytest-{uuid.uuid4().hex}.json"
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, sneaky, local_output("c.json"), write=True)
    )
    assert (code, payload["reason"]) == (2, "traversal")
    assert not Path(os.path.abspath(sneaky)).exists()


def test_symlink_escape_is_refused(cli, capsys, input_file, tmp_path, local_output):
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    link = LOCAL_DIR / f"pytest-link-{uuid.uuid4().hex}"
    try:
        os.symlink(tmp_path, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available in this environment")
    try:
        code, payload, _ = _run(
            cli, capsys, _argv(input_file, link / "r.json", local_output("c.json"), write=True)
        )
        assert (code, payload["reason"]) == (2, "symlink_component")
        assert list(tmp_path.iterdir()) == [input_file]
    finally:
        link.unlink()


def test_record_write_failure_writes_nothing(cli, capsys, input_file, local_output, monkeypatch):
    record_path, confirmation_path = local_output("record.json"), local_output("confirmation.json")

    def fail(*args, **kwargs):
        raise EvaluationSerializationError("failed to write evaluation record")

    monkeypatch.setattr(cli, "write_record", fail)
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert code == 1
    assert payload == {
        "error": "record_write_failed",
        "record_written": False,
        "confirmation_written": False,
    }
    assert not record_path.exists() and not confirmation_path.exists()


def test_confirmation_write_failure_keeps_the_record_and_reports_it(
    cli, capsys, input_file, local_output, monkeypatch
):
    record_path, confirmation_path = local_output("record.json"), local_output("confirmation.json")

    def fail(*args, **kwargs):
        raise EvaluationSerializationError("failed to write confirmation result")

    monkeypatch.setattr(cli, "write_result", fail)
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert code == 1
    assert payload == {
        "error": "confirmation_write_failed",
        "record_written": True,
        "confirmation_written": False,
    }
    assert read_record(record_path).symbol == "SPY"  # complete, valid, never deleted
    assert not confirmation_path.exists()
    record_bytes = record_path.read_bytes()
    assert sorted(p.name for p in LOCAL_DIR.iterdir() if p.name.endswith(".tmp")) == []

    # Re-running (writer restored) never overwrites the surviving record:
    # the two-path preflight refuses before anything is evaluated or written.
    monkeypatch.undo()
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert (code, payload) == (2, {"error": "output_path_refused", "reason": "exists"})
    assert record_path.read_bytes() == record_bytes
    assert not confirmation_path.exists()


def test_the_confirmation_target_is_never_overwritten_even_at_publish_time(
    cli, capsys, input_file, local_output, monkeypatch
):
    """If the result target appears after preflight (a race), the atomic
    no-overwrite publish refuses it: the pre-existing file is untouched and
    the record that was already written is kept."""
    record_path, confirmation_path = local_output("record.json"), local_output("confirmation.json")
    real_write_record = cli.write_record

    def write_record_then_race(record, path):
        real_write_record(record, path)
        confirmation_path.write_text("appeared-after-preflight", encoding="utf-8")

    monkeypatch.setattr(cli, "write_record", write_record_then_race)
    code, payload, _ = _run(
        cli, capsys, _argv(input_file, record_path, confirmation_path, write=True)
    )
    assert code == 1
    assert payload == {
        "error": "confirmation_write_failed",
        "record_written": True,
        "confirmation_written": False,
    }
    assert confirmation_path.read_text(encoding="utf-8") == "appeared-after-preflight"
    assert read_record(record_path).symbol == "SPY"


# --- Argument, input, and analysis refusals -------------------------------------------------


@pytest.mark.parametrize(
    "commit",
    [COMMIT.upper(), COMMIT[:-1], COMMIT + "0", "g" * 40, "", "main", COMMIT[:7]],
)
def test_invalid_code_commit_is_refused_before_anything_else(cli, capsys, tmp_path, commit):
    missing_input = tmp_path / "does-not-exist.json"
    code, payload, _ = _run(
        cli, capsys, _argv(missing_input, tmp_path / "r", tmp_path / "c", commit=commit)
    )
    assert (code, payload) == (2, {"error": "invalid_code_commit"})


def test_missing_or_malformed_input_is_refused(cli, capsys, tmp_path):
    code, payload, _ = _run(
        cli, capsys, _argv(tmp_path / "missing.json", tmp_path / "r", tmp_path / "c")
    )
    assert (code, payload) == (2, {"error": "invalid_input"})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, payload, _ = _run(cli, capsys, _argv(bad, tmp_path / "r", tmp_path / "c"))
    assert (code, payload) == (2, {"error": "invalid_input"})


@pytest.mark.parametrize(
    "mangle",
    [
        lambda text: text.replace("\n", "\r\n"),
        lambda text: text.rstrip("\n"),
        lambda text: json.dumps(json.loads(text)),  # valid, but not canonical
    ],
)
def test_non_canonical_input_is_refused(cli, capsys, tmp_path, mangle):
    path = tmp_path / "input.json"
    path.write_bytes(mangle(input_to_json_str(_input())).encode("utf-8"))
    code, payload, _ = _run(cli, capsys, _argv(path, tmp_path / "r", tmp_path / "c"))
    assert (code, payload) == (2, {"error": "input_not_canonical"})


@pytest.mark.parametrize(
    ("days", "sample"),
    [
        (CONFIRMATION_DAYS, "holdout"),
        ((date(2026, 8, 17),), "confirmation"),  # discovery sample
        ((date(2026, 9, 23),), "confirmation"),
    ],
)
def test_out_of_window_input_is_refused_and_nothing_is_written(
    cli, capsys, tmp_path, local_output, days, sample
):
    path = tmp_path / "input.json"
    path.write_bytes(input_to_json_str(_input(days)).encode("utf-8"))
    record_path, confirmation_path = local_output("r.json"), local_output("c.json")
    code, payload, _ = _run(
        cli, capsys, _argv(path, record_path, confirmation_path, sample=sample, write=True)
    )
    assert (code, payload) == (2, {"error": "analysis_refused", "reason": "window_violation"})
    assert not record_path.exists() and not confirmation_path.exists()


def test_required_arguments_are_enforced(cli):
    with pytest.raises(SystemExit) as info:
        cli.main(["--sample", "confirmation"])
    assert info.value.code == 2
    with pytest.raises(SystemExit):
        cli.main(_argv("i", "r", "c", sample="discovery"))


def test_junction_escape_is_refused(cli, capsys, input_file, tmp_path, local_output):
    """Windows directory junctions need no symlink privilege, so this
    exercises the link refusal where symlinks cannot be created."""
    try:
        import _winapi
    except ImportError:
        pytest.skip("directory junctions are Windows-only")
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = LOCAL_DIR / f"pytest-junction-{uuid.uuid4().hex}"
    try:
        _winapi.CreateJunction(str(outside), str(junction))
    except OSError:
        pytest.skip("directory junctions are not available in this environment")
    try:
        code, payload, _ = _run(
            cli, capsys, _argv(input_file, junction / "r.json", local_output("c.json"), write=True)
        )
        assert (code, payload["reason"]) == (2, "symlink_component")
        assert list(outside.iterdir()) == []
    finally:
        os.rmdir(junction)  # removes the junction only, never its target
