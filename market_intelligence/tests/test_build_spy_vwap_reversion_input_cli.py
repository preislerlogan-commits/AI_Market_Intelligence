"""Tests for scripts/build_spy_vwap_reversion_input.py.

Every test runs against an isolated, synthetic temporary DuckDB database
(injected via ``main(settings=...)``) and redirects the allowed output
directory to a temporary ``data/evaluations/local`` -- nothing here touches
the real repository database or makes a network request.
"""

from __future__ import annotations

import importlib.util
import json
import os
from decimal import Decimal
from pathlib import Path
from types import ModuleType

import pytest

from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    input_to_json_str,
    read_input,
)
from market_intelligence.orchestration.spy_vwap_reversion_input_builder import (
    build_spy_vwap_reversion_input,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.tests.test_spy_vwap_reversion_input_builder import (
    FRI,
    MON,
    file_digest,
    initialized_settings,
    insert_rows,
    isolated_settings,
    seeded_week_settings,
    session_rows,
)

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"


def load_script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def local_dir(tmp_path) -> Path:
    directory = tmp_path / "repo" / "data" / "evaluations" / "local"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def cli(local_dir, monkeypatch) -> ModuleType:
    module = load_script("build_spy_vwap_reversion_input")
    monkeypatch.setattr(module, "_ALLOWED_OUTPUT_DIR", local_dir)
    return module


def _args(output: Path, *extra: str, start: str = "2026-08-17", end: str = "2026-08-21"):
    return ["--start-date", start, "--end-date", end, "--output", str(output), *extra]


def test_dry_run_builds_validates_and_writes_nothing(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    output = local_dir / "input.json"

    exit_code = cli.main(_args(output), settings=settings)

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mode"] == "dry_run"
    assert payload["output_written"] is False
    assert payload["included_session_count"] == 5
    assert payload["prior_day_counts"] == {
        "available": 4,
        "previous_weekday_not_stored": 1,
        "previous_weekday_session_excluded": 0,
    }
    assert payload["point_in_time_context"]["catalyst_state"] == "unknown"
    assert "input_building_only_not_an_evaluation_result" in payload["notes"]
    assert not output.exists()
    assert list(local_dir.iterdir()) == []


def test_write_produces_byte_stable_input_accepted_by_the_evaluator(
    cli, local_dir, tmp_path, capsys
):
    settings = seeded_week_settings(tmp_path)
    output = local_dir / "input.json"

    assert cli.main(_args(output, "--write"), settings=settings) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["output_written"] is True

    expected = input_to_json_str(
        build_spy_vwap_reversion_input(start_date=MON, end_date=FRI, settings=settings)
        .evaluation_input
    )
    assert output.read_text(encoding="utf-8") == expected
    assert read_input(output).sessions[0].session_date == MON

    second = local_dir / "input-again.json"
    assert cli.main(_args(second, "--write"), settings=settings) == 0
    capsys.readouterr()
    assert second.read_bytes() == output.read_bytes()

    evaluator = load_script("evaluate_spy_vwap_reversion")
    assert evaluator.main(["--input", str(output), "--output", str(local_dir / "rec.json")]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["unique_session_count"] == 5
    assert summary["candidate_decision_point_count"] == 390


def test_database_is_not_modified_by_a_write_run(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    database = DuckDBManager(settings=settings).database_path
    before = file_digest(database)

    assert cli.main(_args(local_dir / "input.json", "--write"), settings=settings) == 0
    assert file_digest(database) == before


def test_stdout_never_reproduces_prices_or_timestamps(cli, local_dir, tmp_path, capsys):
    settings = initialized_settings(tmp_path)
    insert_rows(settings, session_rows(MON, base=Decimal("432.10")))
    insert_rows(settings, session_rows(FRI, base=Decimal("432.10"))[:50])

    assert cli.main(_args(local_dir / "input.json"), settings=settings) == 0
    out = capsys.readouterr().out
    assert "432.1" not in out
    assert "T13:30" not in out
    payload = json.loads(out)
    assert payload["excluded_session_counts"]["incomplete_session"] == 1
    assert payload["excluded_session_counts"]["no_regular_session_bars"] == 3


def test_no_complete_session_is_reported_and_nothing_is_written(cli, local_dir, tmp_path, capsys):
    settings = initialized_settings(tmp_path)
    insert_rows(settings, session_rows(MON)[:77])
    output = local_dir / "input.json"

    assert cli.main(_args(output, "--write"), settings=settings) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "no_complete_sessions"
    assert payload["output_written"] is False
    assert not output.exists()


@pytest.mark.parametrize(
    ("start", "end"),
    [("2026-8-17", "2026-08-21"), ("2026-08-21", "2026-08-17"), ("2026-02-30", "2026-03-02")],
)
def test_invalid_dates_are_rejected(cli, local_dir, tmp_path, capsys, start, end):
    settings = seeded_week_settings(tmp_path)
    exit_code = cli.main(_args(local_dir / "x.json", start=start, end=end), settings=settings)
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {"error": "invalid_input"}


def test_missing_database_is_a_sanitized_storage_error(cli, local_dir, tmp_path, capsys):
    settings = isolated_settings(tmp_path)
    (tmp_path / "data").mkdir()
    assert cli.main(_args(local_dir / "x.json"), settings=settings) == 1
    out = capsys.readouterr().out
    assert json.loads(out) == {"error": "storage_error"}
    assert not DuckDBManager(settings=settings).database_path.exists()


# --- Output-path refusals --------------------------------------------------------------


def _assert_refused(cli, settings, output, capsys):
    assert cli.main(_args(output, "--write"), settings=settings) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "output_path_refused"}


def test_write_outside_the_local_dir_is_refused(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    _assert_refused(cli, settings, tmp_path / "input.json", capsys)
    _assert_refused(cli, settings, local_dir.parent / "input.json", capsys)
    _assert_refused(cli, settings, local_dir, capsys)
    assert not (tmp_path / "input.json").exists()


def test_path_traversal_is_refused_even_if_it_lands_inside(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    (local_dir / "sub").mkdir()
    _assert_refused(cli, settings, local_dir / "sub" / ".." / "input.json", capsys)
    _assert_refused(cli, settings, local_dir / ".." / "escape.json", capsys)
    assert not (local_dir / "input.json").exists()


def test_existing_output_is_never_overwritten(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    output = local_dir / "input.json"
    output.write_text("keep", encoding="utf-8")
    _assert_refused(cli, settings, output, capsys)
    assert output.read_text(encoding="utf-8") == "keep"


def test_missing_parent_directory_is_never_created(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    _assert_refused(cli, settings, local_dir / "new" / "input.json", capsys)
    assert not (local_dir / "new").exists()


def _symlink_or_skip(src, dst, target_is_directory=False):
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")


def test_symlinked_directory_is_refused_even_if_it_stays_inside(
    cli, local_dir, tmp_path, capsys
):
    settings = seeded_week_settings(tmp_path)
    (local_dir / "real").mkdir()
    _symlink_or_skip(local_dir / "real", local_dir / "link", target_is_directory=True)
    _assert_refused(cli, settings, local_dir / "link" / "input.json", capsys)
    assert list((local_dir / "real").iterdir()) == []


def test_symlinked_target_file_is_refused(cli, local_dir, tmp_path, capsys):
    settings = seeded_week_settings(tmp_path)
    _symlink_or_skip(tmp_path / "elsewhere.json", local_dir / "input.json")
    _assert_refused(cli, settings, local_dir / "input.json", capsys)
    assert not (tmp_path / "elsewhere.json").exists()


def test_symlinked_component_is_refused_without_os_symlink_support(
    cli, local_dir, tmp_path, capsys, monkeypatch
):
    """Platform-independent check of the same rule: simulate a symlinked
    directory inside the allowed directory (resolve() would stay inside)."""
    settings = seeded_week_settings(tmp_path)
    (local_dir / "sub").mkdir()
    flagged = local_dir / "sub"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == flagged or original(self))
    _assert_refused(cli, settings, flagged / "input.json", capsys)
    assert list(flagged.iterdir()) == []


def test_unknown_argument_is_rejected(cli):
    with pytest.raises(SystemExit):
        cli._parse_args(
            ["--start-date", "2026-08-17", "--end-date", "2026-08-21", "--output", "x", "--execute"]
        )
