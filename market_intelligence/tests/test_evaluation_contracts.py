"""Strict-schema, enum, bounds, timezone, and leak-guard tests for the
evaluation contracts."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from market_intelligence.evaluation.contracts import (
    CLASSIFICATION_REASON_MATRIX,
    INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE,
    SCHEMA_VERSION,
    AgentIdentifier,
    CitationAdjudication,
    CitationClassification,
    CitationReason,
    ClaimCitationPair,
    EvaluationFinding,
    EvaluationRunRecord,
    FindingCategory,
    FindingSeverity,
    build_run_id,
)

# Independent (test-authored, not imported from the module under test) copy of
# the compatibility matrix from the task specification -- so the parametrized
# test below proves the *exact* intended matrix, not merely self-consistency.
_EXPECTED_ALLOWED: dict[CitationClassification, set[CitationReason]] = {
    CitationClassification.SUPPORTED: {
        CitationReason.VALUE_MATCHES_EVIDENCE,
        CitationReason.OTHER,
    },
    CitationClassification.PARTIALLY_SUPPORTED: {
        CitationReason.VALUE_ABSENT_FROM_EVIDENCE,
        CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION,
        CitationReason.CLAIM_SCOPE_EXCEEDS_SINGLE_OBSERVATION,
        CitationReason.OTHER,
    },
    CitationClassification.UNSUPPORTED: {
        CitationReason.VALUE_ABSENT_FROM_EVIDENCE,
        CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE,
        CitationReason.EVIDENCE_IS_OFF_TOPIC,
        CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION,
        CitationReason.CLAIM_SCOPE_EXCEEDS_SINGLE_OBSERVATION,
        CitationReason.OTHER,
    },
    CitationClassification.UNABLE_TO_DETERMINE: {
        CitationReason.SANITIZED_MATERIAL_INSUFFICIENT,
        CitationReason.OTHER,
    },
}

# A classification each reason is permitted to pair with, for tests that vary
# only the reason.
_COMPATIBLE_CLASSIFICATION: dict[CitationReason, CitationClassification] = {
    CitationReason.VALUE_MATCHES_EVIDENCE: CitationClassification.SUPPORTED,
    CitationReason.VALUE_ABSENT_FROM_EVIDENCE: CitationClassification.UNSUPPORTED,
    CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE: CitationClassification.UNSUPPORTED,
    CitationReason.EVIDENCE_IS_OFF_TOPIC: CitationClassification.UNSUPPORTED,
    CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION: CitationClassification.UNSUPPORTED,
    CitationReason.CLAIM_SCOPE_EXCEEDS_SINGLE_OBSERVATION: CitationClassification.UNSUPPORTED,
    CitationReason.SANITIZED_MATERIAL_INSUFFICIENT: CitationClassification.UNABLE_TO_DETERMINE,
    CitationReason.OTHER: CitationClassification.SUPPORTED,
}

TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _adj(**overrides):
    base = dict(
        claim_id="claim-a",
        citation_id="cite-1",
        reviewer="reviewer-1",
        adjudicated_at=TS,
        classification=CitationClassification.SUPPORTED,
        reason=CitationReason.VALUE_MATCHES_EVIDENCE,
    )
    base.update(overrides)
    return CitationAdjudication(**base)


def _record(**overrides):
    label = overrides.pop("characterization_label", "synthetic-label")
    agent = overrides.pop("agent", AgentIdentifier.MACRO_ANALYST)
    created_at = overrides.pop("created_at", TS)
    run_id = overrides.pop("run_id", None) or build_run_id(agent, label, TS)
    base = dict(
        run_id=run_id,
        agent=agent,
        characterization_label=label,
        created_at=created_at,
        evidence_fixture_name="synthetic/shape",
        summary="synthetic summary",
        expected_pairs=[ClaimCitationPair(claim_id="claim-a", citation_id="cite-1")],
        adjudications=[_adj()],
        findings=[],
    )
    base.update(overrides)
    return EvaluationRunRecord(**base)


# --- enums ------------------------------------------------------------------


def test_enum_member_values_are_the_fixed_strings():
    assert [a.value for a in AgentIdentifier] == [
        "market_evidence",
        "news_analyst",
        "macro_analyst",
    ]
    assert [s.value for s in FindingSeverity] == ["info", "warning", "failure"]
    assert [c.value for c in CitationClassification] == [
        "supported",
        "partially_supported",
        "unsupported",
        "unable_to_determine",
    ]
    assert [r.value for r in CitationReason] == [
        "value_matches_evidence",
        "value_absent_from_evidence",
        "value_conflicts_with_evidence",
        "evidence_is_off_topic",
        "claim_adds_unsupported_characterization",
        "claim_scope_exceeds_single_observation",
        "sanitized_material_insufficient",
        "other",
    ]
    assert set(FindingCategory) == {
        FindingCategory.FACTUAL_TRANSCRIPTION,
        FindingCategory.CITATION_SUPPORT,
        FindingCategory.ABSTENTION_BEHAVIOR,
        FindingCategory.CROSS_AGENT_CONSISTENCY,
        FindingCategory.REPEATABILITY,
        FindingCategory.RUBRIC_COMPLETENESS,
        FindingCategory.SCOPE_BOUNDARY,
        FindingCategory.OTHER,
    }


@pytest.mark.parametrize("bad", ["Supported", "maybe", "", "unknown"])
def test_classification_and_reason_reject_values_outside_the_enum(bad):
    with pytest.raises(ValidationError):
        _adj(classification=bad)
    with pytest.raises(ValidationError):
        _adj(reason=bad)


# --- strictness / bounds --------------------------------------------------


def test_models_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        _adj(extra_field="x")
    with pytest.raises(ValidationError):
        _record(surprise=1)
    with pytest.raises(ValidationError):
        ClaimCitationPair(claim_id="c", citation_id="d", note="x")


def test_identifier_and_text_length_bounds():
    with pytest.raises(ValidationError):
        _adj(claim_id="x" * 65)
    with pytest.raises(ValidationError):
        _adj(claim_id="")
    with pytest.raises(ValidationError):
        _record(summary="s" * 201)
    with pytest.raises(ValidationError):
        _record(expected_pairs=[])


def test_expected_and_adjudication_list_upper_bounds():
    many = [ClaimCitationPair(claim_id=f"c{i}", citation_id=f"e{i}") for i in range(501)]
    with pytest.raises(ValidationError):
        _record(expected_pairs=many)


# --- timezone enforcement -----------------------------------------------


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValidationError):
        _adj(adjudicated_at=datetime(2026, 1, 2, 3, 4, 5))
    with pytest.raises(ValidationError):
        _record(
            run_id=build_run_id(AgentIdentifier.MACRO_ANALYST, "synthetic-label", TS),
            created_at=datetime(2026, 1, 2, 3, 4, 5),
        )


def test_non_utc_aware_timestamp_is_normalized_to_utc():
    eastern = timezone(timedelta(hours=-5))
    adj = _adj(adjudicated_at=datetime(2026, 1, 2, 0, 4, 5, tzinfo=eastern))
    assert adj.adjudicated_at.tzinfo is UTC
    assert adj.adjudicated_at == datetime(2026, 1, 2, 5, 4, 5, tzinfo=UTC)


# --- reviewer-note rule ------------------------------------------------


def test_reason_other_requires_a_reviewer_note():
    with pytest.raises(ValidationError):
        _adj(reason=CitationReason.OTHER)
    ok = _adj(reason=CitationReason.OTHER, reviewer_note="short synthetic note")
    assert ok.reviewer_note == "short synthetic note"


@pytest.mark.parametrize(
    "reason",
    [r for r in CitationReason if r is not CitationReason.OTHER],
)
def test_non_other_reasons_forbid_a_reviewer_note(reason):
    classification = _COMPATIBLE_CLASSIFICATION[reason]
    with pytest.raises(ValidationError):
        _adj(classification=classification, reason=reason, reviewer_note="disallowed note")
    assert _adj(classification=classification, reason=reason).reviewer_note is None


def test_reviewer_note_length_is_bounded():
    with pytest.raises(ValidationError):
        _adj(reason=CitationReason.OTHER, reviewer_note="x" * 281)


# --- unable_to_determine stays first-class ---------------------------


def test_unable_to_determine_is_a_valid_recordable_outcome():
    adj = _adj(
        classification=CitationClassification.UNABLE_TO_DETERMINE,
        reason=CitationReason.SANITIZED_MATERIAL_INSUFFICIENT,
    )
    record = _record(adjudications=[adj])
    # No field, property, or method converts it into a pass/fail.
    assert not hasattr(record, "passed")
    assert record.adjudications[0].classification is CitationClassification.UNABLE_TO_DETERMINE


def test_unable_to_determine_also_accepts_other_with_a_note():
    adj = _adj(
        classification=CitationClassification.UNABLE_TO_DETERMINE,
        reason=CitationReason.OTHER,
        reviewer_note="synthetic: the redaction removed the cited value",
    )
    assert adj.classification is CitationClassification.UNABLE_TO_DETERMINE


# --- classification / reason compatibility matrix ------------------------


def test_matrix_is_immutable_and_matches_the_specification():
    assert {k: set(v) for k, v in CLASSIFICATION_REASON_MATRIX.items()} == _EXPECTED_ALLOWED
    with pytest.raises(TypeError):
        CLASSIFICATION_REASON_MATRIX[CitationClassification.SUPPORTED] = frozenset()


@pytest.mark.parametrize(
    ("classification", "reason"),
    list(itertools.product(CitationClassification, CitationReason)),
)
def test_every_classification_reason_combination_follows_the_exact_matrix(classification, reason):
    allowed = reason in _EXPECTED_ALLOWED[classification]
    kwargs = {"classification": classification, "reason": reason}
    if reason is CitationReason.OTHER:
        kwargs["reviewer_note"] = "synthetic bounded note"
    if allowed:
        adj = _adj(**kwargs)
        assert adj.classification is classification
        assert adj.reason is reason
    else:
        with pytest.raises(ValidationError):
            _adj(**kwargs)


@pytest.mark.parametrize(
    ("classification", "reason"),
    [
        (CitationClassification.SUPPORTED, CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE),
        (CitationClassification.SUPPORTED, CitationReason.EVIDENCE_IS_OFF_TOPIC),
        (CitationClassification.SUPPORTED, CitationReason.VALUE_ABSENT_FROM_EVIDENCE),
        (CitationClassification.PARTIALLY_SUPPORTED, CitationReason.VALUE_MATCHES_EVIDENCE),
        (CitationClassification.PARTIALLY_SUPPORTED, CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE),
        (CitationClassification.PARTIALLY_SUPPORTED, CitationReason.EVIDENCE_IS_OFF_TOPIC),
        (CitationClassification.UNSUPPORTED, CitationReason.VALUE_MATCHES_EVIDENCE),
        (CitationClassification.UNABLE_TO_DETERMINE, CitationReason.VALUE_MATCHES_EVIDENCE),
        (CitationClassification.UNABLE_TO_DETERMINE, CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE),
        (CitationClassification.UNABLE_TO_DETERMINE, CitationReason.EVIDENCE_IS_OFF_TOPIC),
        (CitationClassification.SUPPORTED, CitationReason.SANITIZED_MATERIAL_INSUFFICIENT),
    ],
)
def test_contradictory_classification_reason_pairs_are_rejected(classification, reason):
    with pytest.raises(ValidationError) as exc:
        _adj(classification=classification, reason=reason)
    assert INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE in str(exc.value)


def test_incompatible_pair_message_is_sanitized():
    with pytest.raises(ValidationError) as exc:
        _adj(
            claim_id="claim-secret-id",
            citation_id="cite-secret-id",
            reviewer="reviewer-secret",
            classification=CitationClassification.UNABLE_TO_DETERMINE,
            reason=CitationReason.VALUE_MATCHES_EVIDENCE,
        )
    message = str(exc.value)
    assert INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE in message
    assert "claim-secret-id" not in message
    assert "cite-secret-id" not in message
    assert "reviewer-secret" not in message


# --- run id --------------------------------------------------------


def test_build_run_id_is_deterministic_and_shaped():
    a = build_run_id(AgentIdentifier.NEWS_ANALYST, "label-x", TS)
    b = build_run_id("news_analyst", "label-x", TS)
    assert a == b
    assert a.startswith("evalrun-")
    assert len(a) == len("evalrun-") + 24
    assert build_run_id(AgentIdentifier.MACRO_ANALYST, "label-x", TS) != a


def test_record_rejects_a_run_id_that_is_not_the_deterministic_one():
    with pytest.raises(ValidationError):
        _record(run_id="evalrun-" + "0" * 24)


def test_schema_version_is_pinned():
    assert _record().schema_version == SCHEMA_VERSION
    with pytest.raises(ValidationError):
        _record(schema_version="evaluation-foundation-2")


# --- leak guard ---------------------------------------------------


@pytest.mark.parametrize(
    "leaky",
    [
        "see https://example.com/article",
        r"C:\Users\x\data\market_intelligence.duckdb",
        "response resp_abc123def456 was used",
        "key sk-ABCDEFGH12345678",
        r"\\server\share\file",
    ],
)
def test_free_text_rejects_obvious_url_path_and_credential_shapes(leaky):
    with pytest.raises(ValidationError):
        _record(summary=leaky)
    with pytest.raises(ValidationError):
        _adj(reason=CitationReason.OTHER, reviewer_note=leaky)


def test_finding_category_other_requires_detail():
    with pytest.raises(ValidationError):
        EvaluationFinding(
            category=FindingCategory.OTHER,
            severity=FindingSeverity.INFO,
            summary="s",
        )
    ok = EvaluationFinding(
        category=FindingCategory.OTHER,
        severity=FindingSeverity.INFO,
        summary="s",
        detail="a synthetic detail note",
    )
    assert ok.detail == "a synthetic detail note"
