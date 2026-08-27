"""Deterministic rubric-completeness behaviour tests."""

from __future__ import annotations

from datetime import UTC, datetime

from market_intelligence.evaluation.contracts import (
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
from market_intelligence.evaluation.rubric import (
    COMPLETION_IS_NOT_VALIDATION,
    check_rubric_completeness,
)

TS = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


def _adj(claim, cite, classification=CitationClassification.SUPPORTED,
         reason=CitationReason.VALUE_MATCHES_EVIDENCE, note=None):
    return CitationAdjudication(
        claim_id=claim,
        citation_id=cite,
        reviewer="reviewer-1",
        adjudicated_at=TS,
        classification=classification,
        reason=reason,
        reviewer_note=note,
    )


def _record(expected, adjudications, findings=None):
    label = "synthetic"
    agent = AgentIdentifier.MACRO_ANALYST
    return EvaluationRunRecord(
        run_id=build_run_id(agent, label, TS),
        agent=agent,
        characterization_label=label,
        created_at=TS,
        evidence_fixture_name="synthetic/shape",
        summary="synthetic",
        expected_pairs=[ClaimCitationPair(claim_id=c, citation_id=e) for c, e in expected],
        adjudications=adjudications,
        findings=findings or [],
    )


def test_every_pair_adjudicated_exactly_once_is_complete():
    record = _record(
        [("claim-a", "cite-1"), ("claim-b", "cite-2")],
        [_adj("claim-a", "cite-1"), _adj("claim-b", "cite-2")],
    )
    result = check_rubric_completeness(record)
    assert result.complete is True
    assert result.missing_pairs == ()
    assert result.unexpected_pairs == ()
    assert result.duplicate_adjudication_pairs == ()


def test_completion_does_not_depend_on_classifications_or_findings():
    record = _record(
        [("claim-a", "cite-1"), ("claim-b", "cite-2")],
        [
            _adj("claim-a", "cite-1", CitationClassification.UNSUPPORTED,
                 CitationReason.EVIDENCE_IS_OFF_TOPIC),
            _adj("claim-b", "cite-2", CitationClassification.UNABLE_TO_DETERMINE,
                 CitationReason.SANITIZED_MATERIAL_INSUFFICIENT),
        ],
        findings=[
            EvaluationFinding(
                category=FindingCategory.CITATION_SUPPORT,
                severity=FindingSeverity.FAILURE,
                summary="a synthetic failure finding",
            )
        ],
    )
    result = check_rubric_completeness(record)
    assert result.complete is True
    assert result.classification_tally == {
        CitationClassification.UNSUPPORTED: 1,
        CitationClassification.UNABLE_TO_DETERMINE: 1,
    }


def test_missing_adjudication_makes_the_rubric_incomplete():
    record = _record(
        [("claim-a", "cite-1"), ("claim-b", "cite-2")],
        [_adj("claim-a", "cite-1")],
    )
    result = check_rubric_completeness(record)
    assert result.complete is False
    assert result.missing_pairs == (("claim-b", "cite-2"),)


def test_unexpected_and_duplicate_and_missing_are_all_reported():
    record = _record(
        [("claim-a", "cite-1"), ("claim-b", "cite-2")],
        [
            _adj("claim-a", "cite-1"),
            _adj("claim-a", "cite-1", CitationClassification.PARTIALLY_SUPPORTED,
                 CitationReason.VALUE_ABSENT_FROM_EVIDENCE),
            _adj("claim-c", "cite-9", CitationClassification.UNSUPPORTED,
                 CitationReason.OTHER, note="synthetic unexpected"),
        ],
    )
    result = check_rubric_completeness(record)
    assert result.complete is False
    assert result.duplicate_adjudication_pairs == (("claim-a", "cite-1"),)
    assert result.unexpected_pairs == (("claim-c", "cite-9"),)
    assert result.missing_pairs == (("claim-b", "cite-2"),)


def test_duplicate_expected_pair_is_detected():
    record = _record(
        [("claim-a", "cite-1"), ("claim-a", "cite-1")],
        [_adj("claim-a", "cite-1")],
    )
    result = check_rubric_completeness(record)
    assert result.complete is False
    assert result.duplicate_expected_pairs == (("claim-a", "cite-1"),)


def test_ordering_is_deterministic_and_sorted():
    record = _record(
        [("claim-z", "cite-2"), ("claim-a", "cite-9"), ("claim-a", "cite-1")],
        [
            _adj("claim-a", "cite-9"),
            _adj("claim-z", "cite-2"),
            _adj("claim-a", "cite-1"),
        ],
    )
    first = check_rubric_completeness(record)
    second = check_rubric_completeness(record)
    assert first == second
    assert first.ordered_expected_pairs == (
        ("claim-a", "cite-1"),
        ("claim-a", "cite-9"),
        ("claim-z", "cite-2"),
    )
    assert list(first.ordered_adjudicated_pairs) == sorted(first.ordered_adjudicated_pairs)


def test_result_names_completion_not_a_pass_and_carries_the_disclaimer():
    result = check_rubric_completeness(
        _record([("claim-a", "cite-1")], [_adj("claim-a", "cite-1")])
    )
    assert not hasattr(result, "passed")
    assert not hasattr(result, "validated")
    assert "not mean the agent is validated" in COMPLETION_IS_NOT_VALIDATION
