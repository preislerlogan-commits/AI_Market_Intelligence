"""Offline tests for the Macro characterization workflow.

Synthetic only -- these exercise
``market_intelligence/evaluation/macro_characterization_input.py`` and
``market_intelligence/evaluation/macro_characterization_workflow.py`` against
hand-authored fixtures. Passing them says nothing about any real agent output.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from market_intelligence.evaluation.contracts import (
    AgentIdentifier,
    CitationAdjudication,
    CitationClassification,
    CitationReason,
    ClaimCitationPair,
    FindingCategory,
    FindingSeverity,
    build_run_id,
)
from market_intelligence.evaluation.fixtures.macro_characterization import (
    COMPLETE_MULTI_CLAIM,
)
from market_intelligence.evaluation.macro_characterization_input import (
    MacroCharacterizationInput,
    input_from_json_str,
    input_to_json_str,
)
from market_intelligence.evaluation.macro_characterization_workflow import (
    COMPLETION_IS_NOT_VALIDATION,
    EVIDENCE_FIXTURE_NAME,
    TRANSCRIPTION_IS_NOT_CITATION_SUPPORT,
    MacroCharacterizationError,
    PendingCitationAdjudication,
    build_macro_characterization,
    complete_macro_characterization,
)
from market_intelligence.evaluation.macro_factual_transcription import (
    MacroTranscriptionInput,
    TranscriptionEvidenceFact,
)
from market_intelligence.evaluation.rubric import check_rubric_completeness

_CREATED_AT = datetime(2031, 5, 1, tzinfo=UTC)
_ADJUDICATED_AT = datetime(2031, 5, 2, tzinfo=UTC)


def _fact(citation_id: str = "cite-a", series_id: str = "SYNTHRATE") -> TranscriptionEvidenceFact:
    return TranscriptionEvidenceFact(
        citation_id=citation_id,
        series_id=series_id,
        observation_date="2031-04-01",
        value="2.50",
        frequency="monthly",
        units="percent",
    )


def _claim(
    claim_id: str = "claim-1", series_id: str = "SYNTHRATE", **facts
) -> MacroTranscriptionInput:
    return MacroTranscriptionInput(
        claim_id=claim_id,
        claim_series_id=series_id,
        claim_summary="The stored monthly observation dated 2031-04-01 is 2.50 percent.",
        cited_facts=facts.get("cited_facts", [_fact(series_id=series_id)]),
    )


def _adj(
    claim_id: str,
    citation_id: str,
    classification: CitationClassification,
    reason: CitationReason,
    note: str | None = None,
) -> CitationAdjudication:
    return CitationAdjudication(
        claim_id=claim_id,
        citation_id=citation_id,
        reviewer="reviewer-1",
        adjudicated_at=_ADJUDICATED_AT,
        classification=classification,
        reason=reason,
        reviewer_note=note,
    )


def _full_adjudications() -> list[CitationAdjudication]:
    return [
        _adj("claim-single-match", "cite-a", CitationClassification.SUPPORTED,
             CitationReason.VALUE_MATCHES_EVIDENCE),
        _adj("claim-single-mismatch", "cite-b", CitationClassification.UNSUPPORTED,
             CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE),
        _adj("claim-unrecognized", "cite-c", CitationClassification.UNABLE_TO_DETERMINE,
             CitationReason.SANITIZED_MATERIAL_INSUFFICIENT),
        _adj("claim-comparison-match", "cite-d-prev", CitationClassification.PARTIALLY_SUPPORTED,
             CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION),
        _adj("claim-comparison-match", "cite-d-latest", CitationClassification.SUPPORTED,
             CitationReason.VALUE_MATCHES_EVIDENCE),
    ]


# ---------------------------------------------------------------------------
# Input contract
# ---------------------------------------------------------------------------


def test_complete_fixture_validates_and_round_trips():
    text = input_to_json_str(COMPLETE_MULTI_CLAIM)
    assert input_from_json_str(text) == COMPLETE_MULTI_CLAIM


def test_committed_json_fixture_is_byte_stable():
    from market_intelligence.evaluation.fixtures import FIXTURE_DIR

    path = FIXTURE_DIR / "macro_characterization_input_complete.json"
    assert input_to_json_str(COMPLETE_MULTI_CLAIM) == path.read_text(encoding="utf-8")


def test_input_rejects_extra_fields():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="x",
            claims=[_claim()],
            expected_pairs=[ClaimCitationPair(claim_id="claim-1", citation_id="cite-a")],
            note="unexpected",
        )


def test_input_rejects_leaky_label():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="see https://evil.example/leak",
            claims=[_claim()],
            expected_pairs=[ClaimCitationPair(claim_id="claim-1", citation_id="cite-a")],
        )


def test_input_rejects_duplicate_claim_id():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="dup",
            claims=[_claim("claim-1"), _claim("claim-1")],
            expected_pairs=[ClaimCitationPair(claim_id="claim-1", citation_id="cite-a")],
        )


def test_input_rejects_expected_pair_for_unknown_claim():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="unknown",
            claims=[_claim("claim-1")],
            expected_pairs=[ClaimCitationPair(claim_id="claim-2", citation_id="cite-a")],
        )


def test_input_rejects_expected_pair_with_uncited_citation():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="uncited",
            claims=[_claim("claim-1")],
            expected_pairs=[ClaimCitationPair(claim_id="claim-1", citation_id="cite-z")],
        )


def test_input_rejects_duplicate_expected_pair():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="dup-pair",
            claims=[_claim("claim-1")],
            expected_pairs=[
                ClaimCitationPair(claim_id="claim-1", citation_id="cite-a"),
                ClaimCitationPair(claim_id="claim-1", citation_id="cite-a"),
            ],
        )


def test_input_requires_every_claim_covered_by_an_expected_pair():
    with pytest.raises(ValidationError):
        MacroCharacterizationInput(
            characterization_label="uncovered",
            claims=[_claim("claim-1"), _claim("claim-2", series_id="SYNTHLABOR")],
            expected_pairs=[ClaimCitationPair(claim_id="claim-1", citation_id="cite-a")],
        )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def test_builder_produces_macro_analyst_record_with_no_adjudications():
    result = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    record = result.run_record

    assert record.agent is AgentIdentifier.MACRO_ANALYST
    assert record.adjudications == []
    assert record.evidence_fixture_name == EVIDENCE_FIXTURE_NAME
    assert record.run_id == build_run_id(
        AgentIdentifier.MACRO_ANALYST,
        COMPLETE_MULTI_CLAIM.characterization_label,
        _CREATED_AT,
    )
    assert list(record.expected_pairs) == list(COMPLETE_MULTI_CLAIM.expected_pairs)


def test_builder_carries_one_transcription_finding_per_claim_plus_a_scope_finding():
    result = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    findings = result.run_record.findings

    scope = [f for f in findings if f.category is FindingCategory.SCOPE_BOUNDARY]
    transcription = [f for f in findings if f.category is FindingCategory.FACTUAL_TRANSCRIPTION]
    assert len(scope) == 1
    assert scope[0].severity is FindingSeverity.INFO
    assert len(transcription) == len(COMPLETE_MULTI_CLAIM.claims)

    severities = sorted(f.severity for f in transcription)
    assert severities == sorted(
        [FindingSeverity.INFO, FindingSeverity.FAILURE, FindingSeverity.WARNING,
         FindingSeverity.INFO]
    )


def test_builder_emits_one_pending_template_per_expected_pair_and_never_pre_classifies():
    result = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)

    assert len(result.pending_adjudications) == len(COMPLETE_MULTI_CLAIM.expected_pairs)
    assert {p.as_pair() for p in result.pending_adjudications} == {
        pair.as_tuple() for pair in COMPLETE_MULTI_CLAIM.expected_pairs
    }
    for pending in result.pending_adjudications:
        assert isinstance(pending, PendingCitationAdjudication)
        assert pending.classification is None
        assert pending.reason is None


def test_builder_never_records_a_transcription_match_as_citation_support():
    result = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)

    # No adjudication of any kind is invented, even though two claims transcribe
    # their cited fact exactly.
    assert result.run_record.adjudications == []
    assert check_rubric_completeness(result.run_record).complete is False
    assert "never" in TRANSCRIPTION_IS_NOT_CITATION_SUPPORT


def test_builder_is_deterministic():
    a = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    b = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    assert a.run_record.model_dump() == b.run_record.model_dump()
    assert a.transcription_results == b.transcription_results


# ---------------------------------------------------------------------------
# Completion
# ---------------------------------------------------------------------------


def test_completion_attaches_every_adjudication_and_passes_the_rubric():
    built = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    completed = complete_macro_characterization(built.run_record, _full_adjudications())

    rubric = check_rubric_completeness(completed)
    assert rubric.complete is True
    assert len(completed.adjudications) == 5


def test_completion_preserves_all_four_classifications():
    built = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    completed = complete_macro_characterization(built.run_record, _full_adjudications())

    tally = check_rubric_completeness(completed).classification_tally
    assert tally[CitationClassification.SUPPORTED] == 2
    assert tally[CitationClassification.PARTIALLY_SUPPORTED] == 1
    assert tally[CitationClassification.UNSUPPORTED] == 1
    assert tally[CitationClassification.UNABLE_TO_DETERMINE] == 1


def test_completion_refuses_missing_pair():
    built = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    with pytest.raises(MacroCharacterizationError):
        complete_macro_characterization(built.run_record, _full_adjudications()[:-1])


def test_completion_refuses_duplicate_pair():
    built = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    adjudications = _full_adjudications()
    with pytest.raises(MacroCharacterizationError):
        complete_macro_characterization(
            built.run_record, [*adjudications, adjudications[0]]
        )


def test_completion_refuses_unexpected_pair():
    built = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    adjudications = _full_adjudications()[:-1]
    adjudications.append(
        _adj("claim-single-match", "cite-b", CitationClassification.SUPPORTED,
             CitationReason.VALUE_MATCHES_EVIDENCE)
    )
    with pytest.raises(MacroCharacterizationError):
        complete_macro_characterization(built.run_record, adjudications)


def test_completion_error_is_sanitized():
    built = build_macro_characterization(COMPLETE_MULTI_CLAIM, created_at=_CREATED_AT)
    try:
        complete_macro_characterization(built.run_record, _full_adjudications()[:-1])
    except MacroCharacterizationError as exc:
        message = str(exc)
        assert "cite-" not in message
        assert "claim-" not in message
        assert "SYNTH" not in message
    else:  # pragma: no cover - defensive
        pytest.fail("expected MacroCharacterizationError")


def test_completion_is_not_validation_constant_is_explicit():
    assert "does not mean" in COMPLETION_IS_NOT_VALIDATION
    assert "universal sense" in COMPLETION_IS_NOT_VALIDATION
