"""Safe-serialization boundary tests: path traversal / symlink / overwrite /
oversized-file / malformed-JSON / leak-sanitization / determinism / round trip."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from market_intelligence.evaluation.contracts import (
    AgentIdentifier,
    CitationAdjudication,
    CitationClassification,
    CitationReason,
    ClaimCitationPair,
    EvaluationRunRecord,
    build_run_id,
)
from market_intelligence.evaluation.serialization import (
    EvaluationSerializationError,
    from_json_str,
    read_record,
    to_json_str,
    write_record,
)

TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _record():
    label = "synthetic-serialization"
    agent = AgentIdentifier.NEWS_ANALYST
    return EvaluationRunRecord(
        run_id=build_run_id(agent, label, TS),
        agent=agent,
        characterization_label=label,
        created_at=TS,
        evidence_fixture_name="synthetic/shape",
        summary="synthetic summary",
        expected_pairs=[ClaimCitationPair(claim_id="claim-a", citation_id="cite-1")],
        adjudications=[
            CitationAdjudication(
                claim_id="claim-a",
                citation_id="cite-1",
                reviewer="reviewer-1",
                adjudicated_at=TS,
                classification=CitationClassification.SUPPORTED,
                reason=CitationReason.VALUE_MATCHES_EVIDENCE,
            )
        ],
        findings=[],
    )


# --- pure helpers ---------------------------------------------------------


def test_to_json_str_is_deterministic_and_round_trips():
    record = _record()
    text = to_json_str(record)
    assert text == to_json_str(record)
    assert text.endswith("\n")
    assert from_json_str(text) == record


def test_from_json_str_rejects_malformed_json_without_echoing_content():
    with pytest.raises(EvaluationSerializationError) as exc:
        from_json_str('{"run_id": "evalrun-')
    assert "evalrun-" not in str(exc.value)


def test_from_json_str_rejects_non_object_json():
    with pytest.raises(EvaluationSerializationError):
        from_json_str("[1, 2, 3]")


def test_from_json_str_rejects_schema_invalid_without_echoing_values():
    bad = '{"run_id": "evalrun-000000000000000000000000", "agent": "SECRET_AGENT_VALUE"}'
    with pytest.raises(EvaluationSerializationError) as exc:
        from_json_str(bad)
    assert "SECRET_AGENT_VALUE" not in str(exc.value)


# --- write boundary -----------------------------------------------------


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


def test_write_refuses_missing_parent_directory(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        write_record(_record(), tmp_path / "nope" / "record.json")


def test_write_refuses_when_parent_is_a_file(tmp_path):
    parent = tmp_path / "afile"
    parent.write_text("x")
    with pytest.raises(EvaluationSerializationError):
        write_record(_record(), parent / "record.json")


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


# --- symlink refusal --------------------------------------------------


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


def test_read_refuses_a_symlinked_file(tmp_path):
    real = tmp_path / "real.json"
    write_record(_record(), real)
    link = tmp_path / "link.json"
    _symlink_or_skip(real, link)
    with pytest.raises(EvaluationSerializationError):
        read_record(link)


# --- read boundary ----------------------------------------------------


def test_read_refuses_missing_file(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_record(tmp_path / "missing.json")


def test_read_refuses_a_directory(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_record(tmp_path)


def test_read_refuses_oversized_file(tmp_path, monkeypatch):
    import market_intelligence.evaluation.serialization as ser

    monkeypatch.setattr(ser, "MAX_RECORD_BYTES", 10)
    target = tmp_path / "record.json"
    write_record(_record(), target)
    with pytest.raises(EvaluationSerializationError):
        read_record(target)


def test_read_rejects_malformed_json_file(tmp_path):
    target = tmp_path / "record.json"
    target.write_text("{not json", encoding="utf-8")
    with pytest.raises(EvaluationSerializationError):
        read_record(target)


def test_read_rejects_a_path_traversal_style_name_that_does_not_exist(tmp_path):
    with pytest.raises(EvaluationSerializationError):
        read_record(tmp_path / ".." / "somewhere" / "record.json")


def test_sanitized_read_error_never_contains_the_path(tmp_path):
    missing = tmp_path / "deeply" / "missing.json"
    with pytest.raises(EvaluationSerializationError) as exc:
        read_record(missing)
    assert "deeply" not in str(exc.value)
