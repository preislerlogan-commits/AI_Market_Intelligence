"""Safe-serialization boundary tests for
market_intelligence.evaluation.spy_vwap_reversion_serialization: path
traversal / symlink / overwrite / oversized-file / malformed-JSON /
determinism / round trip, for both the evaluation input and the evaluation
record. Mirrors test_evaluation_serialization.py.
"""

from __future__ import annotations

import json
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
    write_input,
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


def test_input_from_json_str_rejects_non_default_point_in_time_context_without_echoing_values():
    payload = json.loads(input_to_json_str(_input()))
    payload["sessions"][0]["same_time_historical_volume_baseline"] = "87654321.5"
    payload["sessions"][0]["catalyst_state"] = "active"
    payload["sessions"][0]["breadth_state"] = "risk_on_broad"
    bad = json.dumps(payload)
    with pytest.raises(EvaluationSerializationError) as exc:
        input_from_json_str(bad)
    assert "87654321.5" not in str(exc.value)
    assert "risk_on_broad" not in str(exc.value)
    assert str(exc.value) == "evaluation input failed schema validation"


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


# --- write_input (never overwrites) ----------------------------------------------------


def test_write_input_writes_byte_stable_json_that_reads_back(tmp_path):
    target = tmp_path / "input.json"
    write_input(_input(), target)
    assert target.read_text(encoding="utf-8") == input_to_json_str(_input())
    assert read_input(target) == _input()
    assert [p.name for p in tmp_path.iterdir()] == ["input.json"]


def test_write_input_never_overwrites(tmp_path):
    target = tmp_path / "input.json"
    target.write_text("keep", encoding="utf-8")
    with pytest.raises(EvaluationSerializationError):
        write_input(_input(), target)
    assert target.read_text(encoding="utf-8") == "keep"


def test_write_input_does_not_create_directories(tmp_path):
    with pytest.raises(EvaluationSerializationError) as excinfo:
        write_input(_input(), tmp_path / "missing" / "input.json")
    assert not (tmp_path / "missing").exists()
    assert str(tmp_path) not in str(excinfo.value)


def test_write_input_refuses_a_symlinked_parent_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _symlink_or_skip(real, link, target_is_directory=True)
    with pytest.raises(EvaluationSerializationError):
        write_input(_input(), link / "input.json")
    assert list(real.iterdir()) == []


# --- Record read ceiling (C1.4) and prior-record byte stability -------------------------
#
# The only bound changed for the confirmation analysis: the evaluation-record
# read ceiling, from 20,000,000 bytes to exactly 64 MiB. Everything else
# (input ceiling, record shape, schema version, write guarantees) is pinned.

_RECORD_SHA256 = "ac4db7155ee0cf6b81da6a5d3b428daa9e9f173f13642706e7479d1e7f935fe2"
_INPUT_SHA256 = "dce8e0a25796444a3bfe74b0c7819dba23b38efd109c90064aa75a155f83b5a0"


def test_record_read_ceiling_is_exactly_64_mib_and_input_ceiling_is_unchanged():
    import market_intelligence.evaluation.spy_vwap_reversion_serialization as ser

    assert ser.MAX_RECORD_BYTES == 67_108_864 == 64 * 1024 * 1024
    assert ser.MAX_INPUT_BYTES == 20_000_000


def test_canonical_record_and_input_bytes_are_unchanged():
    # Hashes pinned from the unmodified serializer before this change.
    import hashlib

    assert hashlib.sha256(to_json_str(_record()).encode("utf-8")).hexdigest() == _RECORD_SHA256
    assert hashlib.sha256(input_to_json_str(_input()).encode("utf-8")).hexdigest() == _INPUT_SHA256


def test_evaluation_record_schema_and_field_set_are_unchanged():
    from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
        SCHEMA_VERSION,
        SpyVwapReversionEvaluationRecord,
    )

    assert SCHEMA_VERSION == "spy-vwap-reversion-evaluation-1"
    assert set(SpyVwapReversionEvaluationRecord.model_fields) == {
        "schema_version",
        "symbol",
        "generated_at",
        "regime_thresholds",
        "sample_thresholds",
        "unique_session_count",
        "candidate_decision_point_count",
        "eligible_observation_count",
        "no_signal_vwap_unavailable_count",
        "no_signal_zero_extension_count",
        "regime_counts_all_candidates",
        "regime_counts_eligible_only",
        "extension_side_counts",
        "missing_horizon_counts",
        "horizon_summaries",
        "decision_points",
        "notes",
    }


def test_a_record_file_above_the_former_ceiling_but_below_64_mib_is_readable(tmp_path):
    # Trailing JSON whitespace pads a valid record past 20,000,000 bytes.
    target = tmp_path / "record.json"
    text = to_json_str(_record())
    padding = 20_000_001 - len(text.encode("utf-8"))
    target.write_text(text + " " * padding, encoding="utf-8")
    assert 20_000_000 < target.stat().st_size < 67_108_864
    assert read_record(target) == _record()


def test_a_record_file_above_64_mib_is_refused_before_parsing(tmp_path, monkeypatch):
    import market_intelligence.evaluation.spy_vwap_reversion_serialization as ser

    target = tmp_path / "record.json"
    with open(target, "wb") as handle:
        handle.truncate(67_108_864 + 1)

    def fail(*args, **kwargs):
        raise AssertionError("an oversized record must not be read or parsed")

    monkeypatch.setattr(ser, "from_json_str", fail)
    monkeypatch.setattr(type(target), "read_text", fail)
    with pytest.raises(EvaluationSerializationError, match="too large"):
        read_record(target)
