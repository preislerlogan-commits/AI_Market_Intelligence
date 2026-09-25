"""Serialization tests for the SPY VWAP confirmation result: deterministic
byte-stable JSON, exact-rational preservation, and the shared local-file
guarantees (no overwrite, no directory creation, symlink refusal, bounded
read, sanitized errors). Synthetic results only."""

from __future__ import annotations

import json
import os

import pytest

from market_intelligence.evaluation import spy_vwap_reversion_confirmation_serialization as ser
from market_intelligence.evaluation.serialization import EvaluationSerializationError
from market_intelligence.evaluation.spy_vwap_reversion_confirmation import analyze_confirmation
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import StudySample
from market_intelligence.tests.spy_vwap_confirmation_fixtures import (
    HOLDOUT_START,
    analysis_kwargs,
    make_record,
    patterned_value,
    study_points,
    weekdays,
)


@pytest.fixture(scope="module")
def result():
    record = make_record(
        study_points(
            weekdays(HOLDOUT_START, 40), above=patterned_value("3"), below=patterned_value("1")
        )
    )
    return analyze_confirmation(record, **analysis_kwargs(record, StudySample.HOLDOUT))


def test_json_is_deterministic_byte_stable_and_round_trips(result):
    text = ser.result_to_json_str(result)
    assert text == ser.result_to_json_str(result)
    assert text.endswith("\n") and not text.endswith("\n\n")
    parsed = ser.result_from_json_str(text)
    assert parsed == result
    assert ser.result_to_json_str(parsed) == text
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_exact_rationals_survive_json_exactly(result):
    parsed = ser.result_from_json_str(ser.result_to_json_str(result))
    for original, again in zip(result.primary.horizons, parsed.primary.horizons, strict=True):
        assert (
            original.cell.inference.estimate.as_fraction()
            == again.cell.inference.estimate.as_fraction()
        )
        assert original.p_holm.as_fraction() == again.p_holm.as_fraction()


def test_write_then_read_round_trips(tmp_path, result):
    target = tmp_path / "confirmation.json"
    ser.write_result(result, target)
    assert target.read_text(encoding="utf-8") == ser.result_to_json_str(result)
    assert ser.read_result(target) == result
    assert [p.name for p in tmp_path.iterdir()] == ["confirmation.json"]  # no temp file left


def test_write_never_overwrites(tmp_path, result):
    target = tmp_path / "confirmation.json"
    target.write_text("existing", encoding="utf-8")
    with pytest.raises(EvaluationSerializationError):
        ser.write_result(result, target)
    assert target.read_text(encoding="utf-8") == "existing"


def test_write_does_not_create_directories(tmp_path, result):
    target = tmp_path / "missing" / "confirmation.json"
    with pytest.raises(EvaluationSerializationError):
        ser.write_result(result, target)
    assert not (tmp_path / "missing").exists()


def _symlink_or_skip(src, dst, target_is_directory=False):
    try:
        os.symlink(src, dst, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available in this environment")


def test_write_refuses_a_symlinked_parent(tmp_path, result):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(real, link, target_is_directory=True)
    with pytest.raises(EvaluationSerializationError):
        ser.write_result(result, link / "confirmation.json")
    assert list(real.iterdir()) == []


def test_read_refuses_a_symlinked_file(tmp_path, result):
    target = tmp_path / "confirmation.json"
    ser.write_result(result, target)
    link = tmp_path / "link.json"
    _symlink_or_skip(target, link)
    with pytest.raises(EvaluationSerializationError):
        ser.read_result(link)


def test_read_refuses_an_oversized_file_before_reading(tmp_path, monkeypatch, result):
    target = tmp_path / "confirmation.json"
    ser.write_result(result, target)
    monkeypatch.setattr(ser, "MAX_RESULT_BYTES", 10)

    def fail(*args, **kwargs):
        raise AssertionError("the file must not be parsed")

    monkeypatch.setattr(ser, "result_from_json_str", fail)
    with pytest.raises(EvaluationSerializationError, match="too large"):
        ser.read_result(target)


@pytest.mark.parametrize("text", ["not json", "[1, 2]", "{}"])
def test_malformed_payloads_yield_sanitized_errors(tmp_path, text):
    target = tmp_path / "secret-name.json"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(EvaluationSerializationError) as info:
        ser.read_result(target)
    assert "secret-name" not in str(info.value)
    assert str(tmp_path) not in str(info.value)


def test_tampered_label_is_rejected_on_read(tmp_path, result):
    payload = json.loads(ser.result_to_json_str(result))
    genuine = payload["primary"]["study_label"]
    payload["primary"]["study_label"] = "mixed" if genuine != "mixed" else "not_supported"
    target = tmp_path / "tampered.json"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(EvaluationSerializationError, match="schema validation"):
        ser.read_result(target)
