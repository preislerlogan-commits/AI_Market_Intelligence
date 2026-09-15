"""Safe-serialization boundary tests for
market_intelligence.evaluation.spy_vwap_reversion_serialization: path
traversal / symlink / overwrite / oversized-file / malformed-JSON /
determinism / round trip, for both the evaluation input and the evaluation
record. Mirrors test_evaluation_serialization.py.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    SessionBars,
    SpyVwapReversionEvaluationInput,
)
from market_intelligence.evaluation.spy_vwap_reversion_evaluator import evaluate_spy_vwap_reversion
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    EvaluationSerializationError,
    from_json_str,
    input_from_json_str,
    input_to_json_str,
    read_input,
    read_record,
    to_json_str,
    write_record,
)
from market_intelligence.market_features.spy_regime_contracts import IntradayBar

EASTERN = ZoneInfo("America/New_York")
DAY = date(2026, 6, 10)
GENERATED_AT = datetime(2026, 6, 20, tzinfo=UTC)


def _bar(hour, minute, price="500", volume=1000):
    p = Decimal(price)
    return IntradayBar(
        timestamp=datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=EASTERN),
        open=p, high=p, low=p, close=p, volume=volume,
    )


def _input() -> SpyVwapReversionEvaluationInput:
    session = SessionBars(session_date=DAY, bars=[_bar(9, 30), _bar(9, 35, "501")])
    return SpyVwapReversionEvaluationInput(sessions=[session])


def _record():
    return evaluate_spy_vwap_reversion(_input(), generated_at=GENERATED_AT)


# --- pure helpers: record -------------------------------------------------------------


def test_to_json_str_is_deterministic_and_round_trips():
    record = _record()
    text = to_json_str(record)
    assert text == to_json_str(record)
    assert text.endswith("\n")
    assert from_json_str(text) == record


def test_from_json_str_rejects_malformed_json_without_echoing_content():
    with pytest.raises(EvaluationSerializationError) as exc:
        from_json_str('{"schema_version": "spy-vwap-')
    assert "spy-vwap-" not in str(exc.value)


def test_from_json_str_rejects_non_object_json():
    with pytest.raises(EvaluationSerializationError):
        from_json_str("[1, 2, 3]")


def test_from_json_str_rejects_schema_invalid_without_echoing_values():
    bad = '{"symbol": "SECRET_LEAK_VALUE"}'
    with pytest.raises(EvaluationSerializationError) as exc:
        from_json_str(bad)
    assert "SECRET_LEAK_VALUE" not in str(exc.value)


# --- pure helpers: input --------------------------------------------------------------


def test_input_to_json_str_is_deterministic_and_round_trips():
    evaluation_input = _input()
    text = input_to_json_str(evaluation_input)
    assert text == input_to_json_str(evaluation_input)
    assert text.endswith("\n")
    assert input_from_json_str(text) == evaluation_input


def test_input_from_json_str_rejects_malformed_json():
    with pytest.raises(EvaluationSerializationError):
        input_from_json_str("not json")


def test_input_from_json_str_rejects_non_object_json():
    with pytest.raises(EvaluationSerializationError):
        input_from_json_str("[1, 2]")


def test_input_from_json_str_rejects_schema_invalid_without_echoing_values():
    bad = '{"symbol": "SECRET_LEAK_VALUE", "sessions": []}'
    with pytest.raises(EvaluationSerializationError) as exc:
        input_from_json_str(bad)
    assert "SECRET_LEAK_VALUE" not in str(exc.value)


# --- write boundary (record) -----------------------------------------------------------


def test_write_then_read_round_trips(tmp_path):
    target = tmp_path / "record.json"
    write_record(_record(), target)
    assert read_record(target) == _record()


def test_write_refuses_existing_target_by_default(tmp_path):
    target = tmp_path / "record.json"
    write_record(_record(), target)
    with pytest.raises(EvaluationSerializationError):
        write_record(_record(), target)
    write_record(_record(), target, overwrite=True)


def test_write_overwrite_true_replaces_existing_target(tmp_path):
    target = tmp_path / "record.json"
    target.write_text("stale contents", encoding="utf-8")
    write_record(_record(), target, overwrite=True)
    assert read_record(target) == _record()
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_write_refuses_missing_parent_directory(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        write_record(_record(), tmp_path / "nope" / "record.json")


def test_write_does_not_create_directories(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        write_record(_record(), tmp_path / "sub" / "record.json")
    assert not (tmp_path / "sub").exists()


def test_write_leaves_no_temp_file_behind(tmp_path):
    write_record(_record(), tmp_path / "record.json")
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_sanitized_write_error_never_contains_the_path(tmp_path):
    missing = tmp_path / "nope" / "record.json"
    with pytest.raises(EvaluationSerializationError) as exc:
        write_record(_record(), missing)
    assert str(missing) not in str(exc.value)
    assert "nope" not in str(exc.value)


# --- symlink refusal --------------------------------------------------------------------


def _symlink_or_skip(src, dst, target_is_directory=False):
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted in this environment")


def test_write_refuses_a_symlinked_parent_directory(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    _symlink_or_skip(real_dir, link_dir, target_is_directory=True)
    with pytest.raises(EvaluationSerializationError):
        write_record(_record(), link_dir / "record.json")


def test_read_record_refuses_a_symlinked_file(tmp_path):
    real = tmp_path / "real.json"
    write_record(_record(), real)
    link = tmp_path / "link.json"
    _symlink_or_skip(real, link)
    with pytest.raises(EvaluationSerializationError):
        read_record(link)


def test_read_input_refuses_a_symlinked_file(tmp_path):
    real = tmp_path / "real_input.json"
    real.write_text(input_to_json_str(_input()), encoding="utf-8")
    link = tmp_path / "link_input.json"
    _symlink_or_skip(real, link)
    with pytest.raises(EvaluationSerializationError):
        read_input(link)


# --- read boundary (record) --------------------------------------------------------------


def test_read_record_refuses_missing_file(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_record(tmp_path / "missing.json")


def test_read_record_refuses_a_directory(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_record(tmp_path)


def test_read_record_refuses_oversized_file(tmp_path, monkeypatch):
    import market_intelligence.evaluation.spy_vwap_reversion_serialization as ser

    monkeypatch.setattr(ser, "MAX_RECORD_BYTES", 10)
    target = tmp_path / "record.json"
    write_record(_record(), target)
    with pytest.raises(EvaluationSerializationError):
        read_record(target)


def test_read_record_rejects_malformed_json_file(tmp_path):
    target = tmp_path / "record.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvaluationSerializationError):
        read_record(target)


def test_sanitized_read_record_error_never_contains_the_path(tmp_path):
    missing = tmp_path / "deeply" / "missing.json"
    with pytest.raises(EvaluationSerializationError) as exc:
        read_record(missing)
    assert "deeply" not in str(exc.value)


# --- read boundary (input) -----------------------------------------------------------------


def test_read_input_round_trips(tmp_path):
    target = tmp_path / "input.json"
    target.write_text(input_to_json_str(_input()), encoding="utf-8")
    assert read_input(target) == _input()


def test_read_input_refuses_missing_file(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_input(tmp_path / "missing.json")


def test_read_input_refuses_a_directory(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_input(tmp_path)


def test_read_input_refuses_oversized_file(tmp_path, monkeypatch):
    import market_intelligence.evaluation.spy_vwap_reversion_serialization as ser

    monkeypatch.setattr(ser, "MAX_INPUT_BYTES", 10)
    target = tmp_path / "input.json"
    target.write_text(input_to_json_str(_input()), encoding="utf-8")
    with pytest.raises(EvaluationSerializationError):
        read_input(target)


def test_read_input_rejects_malformed_json_file(tmp_path):
    target = tmp_path / "input.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvaluationSerializationError):
        read_input(target)
