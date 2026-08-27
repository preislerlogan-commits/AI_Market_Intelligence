"""Behaviour tests for the Macro Analyst deterministic factual-transcription check.

Offline and synthetic only -- these exercise
``market_intelligence/evaluation/macro_factual_transcription.py`` against
hand-authored fixtures. Passing them says nothing about any real agent output.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from market_intelligence.evaluation.contracts import FindingCategory, FindingSeverity
from market_intelligence.evaluation.fixtures.macro_transcription import (
    MACRO_TRANSCRIPTION_FIXTURES,
    MACRO_TRANSCRIPTION_FIXTURES_BY_NAME,
)
from market_intelligence.evaluation.macro_factual_transcription import (
    MATCH_IS_NOT_VALIDATION,
    MISMATCH_CATEGORIES,
    MacroTranscriptionInput,
    TranscriptionEvidenceFact,
    collect_findings,
    evaluate_macro_transcription,
    evaluate_macro_transcription_batch,
)


def _single_input(summary: str, **fact_overrides) -> MacroTranscriptionInput:
    fact_fields = dict(
        citation_id="cite-1",
        series_id="FEDFUNDS",
        observation_date="2026-07-01",
        value="3.63",
        frequency="monthly",
        units="percent",
    )
    fact_fields.update(fact_overrides)
    return MacroTranscriptionInput(
        claim_id="claim-1",
        claim_series_id="FEDFUNDS",
        claim_summary=summary,
        cited_facts=[TranscriptionEvidenceFact(**fact_fields)],
    )


# ---------------------------------------------------------------------------
# Fixture-driven cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture", MACRO_TRANSCRIPTION_FIXTURES, ids=lambda f: f.name
)
def test_fixture_produces_its_expected_outcome(fixture):
    result = evaluate_macro_transcription(fixture.item)

    assert result.outcome == fixture.expected_outcome
    assert result.statement_form == fixture.expected_form
    assert result.mismatch_categories == fixture.expected_mismatch_categories


@pytest.mark.parametrize(
    "fixture", MACRO_TRANSCRIPTION_FIXTURES, ids=lambda f: f.name
)
def test_fixture_finding_severity_matches_outcome(fixture):
    result = evaluate_macro_transcription(fixture.item)
    expected_severity = {
        "match": FindingSeverity.INFO,
        "mismatch": FindingSeverity.FAILURE,
        "human_review": FindingSeverity.WARNING,
    }[fixture.expected_outcome]

    assert result.finding.severity == expected_severity
    assert result.finding.category == FindingCategory.FACTUAL_TRANSCRIPTION
    assert result.finding.claim_id == fixture.item.claim_id


def test_every_mismatch_category_is_a_known_slug():
    for fixture in MACRO_TRANSCRIPTION_FIXTURES:
        result = evaluate_macro_transcription(fixture.item)
        for category in result.mismatch_categories:
            assert category in MISMATCH_CATEGORIES


# ---------------------------------------------------------------------------
# Determinism and ordering
# ---------------------------------------------------------------------------


def test_evaluation_is_deterministic():
    item = MACRO_TRANSCRIPTION_FIXTURES_BY_NAME["wrong_value"].item
    assert evaluate_macro_transcription(item) == evaluate_macro_transcription(item)


def test_batch_preserves_input_order():
    items = [f.item for f in MACRO_TRANSCRIPTION_FIXTURES]
    results = evaluate_macro_transcription_batch(items)

    assert [r.claim_id for r in results] == [i.claim_id for i in items]
    assert collect_findings(results) == tuple(r.finding for r in results)


def test_mismatch_categories_are_sorted():
    result = evaluate_macro_transcription(
        MACRO_TRANSCRIPTION_FIXTURES_BY_NAME["wrong_units_frequency"].item
    )
    assert list(result.mismatch_categories) == sorted(result.mismatch_categories)
    assert result.mismatch_categories == ("frequency", "units")


def test_result_carries_no_pass_or_validated_flag():
    result = evaluate_macro_transcription(
        MACRO_TRANSCRIPTION_FIXTURES_BY_NAME["exact_match_single"].item
    )
    assert not hasattr(result, "passed")
    assert not hasattr(result, "validated")
    assert "does not mean" in MATCH_IS_NOT_VALIDATION
    assert "universal sense" in MATCH_IS_NOT_VALIDATION


# ---------------------------------------------------------------------------
# Grammar: only the two controlled forms are recognized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "summary",
    [
        "FEDFUNDS is currently 3.63 percent.",
        "The observation for 2026-07-01 shows 3.63.",
        "The stored monthly value on 2026-07-01 was 3.63 percent.",
        "The stored monthly observation dated 2026-07-01 is 3.63 percent and rising.",
        "The stored monthly observation dated 2026-07-01 is 3.63 bushels.",
        "The stored monthly observation dated 2026-07 is 3.63 percent.",
        "The stored monthly observation dated 2026-07-01 has a value of 3.63 percent.",
        "The stored monthly observation dated 2026-07-01 reads 3.63 percent.",
        "The rate accelerated between the two stored monthly observations.",
        # The "Comparing the stored observations dated X and Y, ..." phrasing is
        # not part of the Macro Analyst's canonical wording and is not recognized.
        "Comparing the stored monthly observations dated 2026-06-01 and "
        "2026-07-01, the value increased from 5.00 to 5.33.",
        "Comparing the stored monthly observations dated 2026-06-01 and "
        "2026-07-01, the value was unchanged at 5.00.",
    ],
)
def test_unrecognized_wording_is_routed_to_human_review(summary):
    result = evaluate_macro_transcription(_single_input(summary))
    assert result.outcome == "human_review"
    assert result.statement_form == "unrecognized"
    assert result.finding.severity == FindingSeverity.WARNING
    assert result.mismatch_categories == ()


@pytest.mark.parametrize(
    "summary",
    [
        "The stored monthly observation dated 2026-07-01 is 3.63 percent.",
        "The stored monthly observation dated 2026-07-01 was 3.63 percent.",
        "The stored monthly observation dated 2026-07-01 is 3.63.",
        "The stored monthly observation dated 2026-07-01 was 3.63 percent, as stored.",
        "The stored monthly observation dated 2026-07-01 is 3.63 percent, "
        "per official FRED metadata.",
    ],
)
def test_recognized_single_observation_phrasings_match(summary):
    result = evaluate_macro_transcription(_single_input(summary))
    assert result.statement_form == "single_observation"
    assert result.outcome == "match"


# ---------------------------------------------------------------------------
# Canonical frequency spelling: the agent capitalizes "Monthly" / "Quarterly"
# ---------------------------------------------------------------------------


def test_capitalized_monthly_canonical_statement_is_recognized_and_matches():
    # The Macro Analyst's accepted live output writes "The stored Monthly
    # observation ..." -- its own _validate_frequency_wording only checks a
    # .lower() copy of the text, so the capitalized word is valid agent output.
    result = evaluate_macro_transcription(
        _single_input(
            "The stored Monthly observation dated 2031-03-01 is 1.23 percent.",
            observation_date="2031-03-01",
            value="1.23",
            frequency="monthly",
        )
    )
    assert result.statement_form == "single_observation"
    assert result.outcome == "match"
    assert result.mismatch_categories == ()


def test_capitalized_quarterly_canonical_statement_is_recognized_and_matches():
    result = evaluate_macro_transcription(
        _single_input(
            "The stored Quarterly observation dated 2031-01-01 was 4.56 index "
            "points, as stored.",
            observation_date="2031-01-01",
            value="4.56",
            frequency="quarterly",
            units="index points",
        )
    )
    assert result.statement_form == "single_observation"
    assert result.outcome == "match"
    assert result.mismatch_categories == ()


def test_capitalized_frequency_in_comparison_form_is_case_normalized():
    item = _comparison_input(
        "The stored Monthly observation dated 2026-07-01 was 5.33; it increased "
        "from the stored Monthly observation dated 2026-06-01, which was 5.00.",
        prev_value="5.00",
        latest_value="5.33",
    )
    result = evaluate_macro_transcription(item)
    assert result.statement_form == "comparison"
    assert result.outcome == "match"


def test_case_normalization_does_not_hide_a_genuine_frequency_mismatch():
    # A capitalized "Quarterly" claim against a monthly cited fact is still a
    # frequency mismatch -- normalization only folds case, it does not equate
    # different frequency words.
    result = evaluate_macro_transcription(
        _single_input(
            "The stored Quarterly observation dated 2026-07-01 is 3.63 percent.",
            frequency="monthly",
        )
    )
    assert result.outcome == "mismatch"
    assert result.mismatch_categories == ("frequency",)


def test_units_are_only_checked_when_the_claim_states_them():
    # Claim omits units entirely -> units mismatch is impossible even though the
    # cited fact carries units.
    result = evaluate_macro_transcription(
        _single_input(
            "The stored monthly observation dated 2026-07-01 is 3.63.",
            units="index",
        )
    )
    assert result.outcome == "match"


def test_claim_stating_units_the_fact_lacks_is_a_units_mismatch():
    result = evaluate_macro_transcription(
        _single_input(
            "The stored monthly observation dated 2026-07-01 is 3.63 percent.",
            units=None,
        )
    )
    assert result.outcome == "mismatch"
    assert result.mismatch_categories == ("units",)


def test_decimal_equality_ignores_trailing_zero_formatting():
    result = evaluate_macro_transcription(
        _single_input(
            "The stored monthly observation dated 2026-07-01 is 3.63 percent.",
            value="3.630000",
        )
    )
    assert result.outcome == "match"


def test_extra_numbers_in_the_sentence_are_not_scraped_as_the_value():
    # The sentence carries two full dates (2026-07-01, 2026-06-01) as well as the
    # two values; a generic number scraper would pick a date component. The check
    # reads only the named latest_value / previous_value groups.
    item = MACRO_TRANSCRIPTION_FIXTURES_BY_NAME["extra_unrelated_numbers"].item
    result = evaluate_macro_transcription(item)
    assert result.outcome == "match"
    assert result.statement_form == "comparison"


# ---------------------------------------------------------------------------
# Comparison direction
# ---------------------------------------------------------------------------


def _comparison_input(summary: str, prev_value: str, latest_value: str) -> MacroTranscriptionInput:
    return MacroTranscriptionInput(
        claim_id="claim-c",
        claim_series_id="FEDFUNDS",
        claim_summary=summary,
        cited_facts=[
            TranscriptionEvidenceFact(
                citation_id="cite-prev",
                series_id="FEDFUNDS",
                observation_date="2026-06-01",
                value=prev_value,
                frequency="monthly",
            ),
            TranscriptionEvidenceFact(
                citation_id="cite-latest",
                series_id="FEDFUNDS",
                observation_date="2026-07-01",
                value=latest_value,
                frequency="monthly",
            ),
        ],
    )


def test_comparison_direction_must_agree_with_the_two_values():
    ok = _comparison_input(
        "The stored monthly observation dated 2026-07-01 was 4.50; it decreased "
        "from the stored monthly observation dated 2026-06-01, which was 5.00.",
        prev_value="5.00",
        latest_value="4.50",
    )
    assert evaluate_macro_transcription(ok).outcome == "match"

    wrong = _comparison_input(
        "The stored monthly observation dated 2026-07-01 was 4.50; it increased "
        "from the stored monthly observation dated 2026-06-01, which was 5.00.",
        prev_value="5.00",
        latest_value="4.50",
    )
    result = evaluate_macro_transcription(wrong)
    assert result.outcome == "mismatch"
    assert result.mismatch_categories == ("comparison_direction",)


def test_unchanged_comparison_form_is_recognized():
    item = _comparison_input(
        "The stored monthly observation dated 2026-07-01 was 5.00; it was "
        "unchanged from the stored monthly observation dated 2026-06-01, which "
        "was 5.00.",
        prev_value="5.00",
        latest_value="5.00",
    )
    result = evaluate_macro_transcription(item)
    assert result.statement_form == "comparison"
    assert result.outcome == "match"


def test_unchanged_wording_with_moving_values_is_a_direction_mismatch():
    item = _comparison_input(
        "The stored monthly observation dated 2026-07-01 was 5.25; it was "
        "unchanged from the stored monthly observation dated 2026-06-01, which "
        "was 5.00.",
        prev_value="5.00",
        latest_value="5.25",
    )
    result = evaluate_macro_transcription(item)
    assert result.outcome == "mismatch"
    assert result.mismatch_categories == ("comparison_direction",)


def test_comparison_previous_observation_date_and_value_are_checked():
    item = _comparison_input(
        "The stored monthly observation dated 2026-07-01 was 5.33; it increased "
        "from the stored monthly observation dated 2026-05-01, which was 4.00.",
        prev_value="5.00",
        latest_value="5.33",
    )
    result = evaluate_macro_transcription(item)
    assert result.outcome == "mismatch"
    assert set(result.mismatch_categories) == {
        "previous_observation_date",
        "previous_value",
    }


# ---------------------------------------------------------------------------
# Citation-structure guards
# ---------------------------------------------------------------------------


def test_single_form_with_two_cited_facts_is_a_structure_mismatch():
    item = MacroTranscriptionInput(
        claim_id="claim-s",
        claim_series_id="FEDFUNDS",
        claim_summary="The stored monthly observation dated 2026-07-01 is 3.63 percent.",
        cited_facts=[
            TranscriptionEvidenceFact(
                citation_id="cite-1",
                series_id="FEDFUNDS",
                observation_date="2026-07-01",
                value="3.63",
                frequency="monthly",
            ),
            TranscriptionEvidenceFact(
                citation_id="cite-2",
                series_id="FEDFUNDS",
                observation_date="2026-06-01",
                value="3.50",
                frequency="monthly",
            ),
        ],
    )
    result = evaluate_macro_transcription(item)
    assert result.outcome == "mismatch"
    assert result.mismatch_categories == ("citation_structure",)


def test_comparison_form_with_one_cited_fact_is_a_structure_mismatch():
    item = MacroTranscriptionInput(
        claim_id="claim-c1",
        claim_series_id="FEDFUNDS",
        claim_summary=(
            "The stored monthly observation dated 2026-07-01 was 5.33; it "
            "increased from the stored monthly observation dated 2026-06-01, "
            "which was 5.00."
        ),
        cited_facts=[
            TranscriptionEvidenceFact(
                citation_id="cite-1",
                series_id="FEDFUNDS",
                observation_date="2026-07-01",
                value="5.33",
                frequency="monthly",
            )
        ],
    )
    result = evaluate_macro_transcription(item)
    assert result.outcome == "mismatch"
    assert result.mismatch_categories == ("citation_structure",)


# ---------------------------------------------------------------------------
# Input bounds
# ---------------------------------------------------------------------------


def test_cited_facts_must_be_one_or_two():
    with pytest.raises(ValidationError):
        MacroTranscriptionInput(
            claim_id="c",
            claim_series_id="FEDFUNDS",
            claim_summary="x",
            cited_facts=[],
        )
    with pytest.raises(ValidationError):
        MacroTranscriptionInput(
            claim_id="c",
            claim_series_id="FEDFUNDS",
            claim_summary="x",
            cited_facts=[_single_input("x").cited_facts[0] for _ in range(3)],
        )


def test_frequency_must_be_a_recognized_word():
    with pytest.raises(ValidationError):
        TranscriptionEvidenceFact(
            citation_id="cite-1",
            series_id="FEDFUNDS",
            observation_date="2026-07-01",
            value="3.63",
            frequency="fortnightly",
        )


def test_observation_date_must_be_an_iso_calendar_date():
    with pytest.raises(ValidationError):
        TranscriptionEvidenceFact(
            citation_id="cite-1",
            series_id="FEDFUNDS",
            observation_date="2026-13-40",
            value="3.63",
            frequency="monthly",
        )


def test_claim_summary_is_length_bounded():
    with pytest.raises(ValidationError):
        MacroTranscriptionInput(
            claim_id="c",
            claim_series_id="FEDFUNDS",
            claim_summary="x" * 401,
            cited_facts=[_single_input("x").cited_facts[0]],
        )


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        MacroTranscriptionInput(
            claim_id="c",
            claim_series_id="FEDFUNDS",
            claim_summary="x",
            cited_facts=[_single_input("x").cited_facts[0]],
            note="unexpected",
        )


# ---------------------------------------------------------------------------
# No content leakage in findings
# ---------------------------------------------------------------------------


def test_mismatch_finding_never_reproduces_claim_or_evidence_text():
    item = _single_input(
        "The stored monthly observation dated 2026-01-15 is 8675.309 percent.",
        observation_date="2026-07-01",
        value="3.63",
    )
    result = evaluate_macro_transcription(item)
    assert result.outcome == "mismatch"

    serialized = result.finding.model_dump_json()
    for leak in ("8675.309", "2026-01-15", "3.63", "2026-07-01"):
        assert leak not in serialized
    assert "8675.309" not in result.finding.summary


def test_human_review_finding_never_reproduces_claim_text():
    item = _single_input("The FEDFUNDS print landed at 8675.309 percent, a fresh high.")
    result = evaluate_macro_transcription(item)
    assert result.outcome == "human_review"
    assert "8675.309" not in result.finding.model_dump_json()


def test_all_fixture_findings_are_leak_free_of_the_synthetic_values():
    for fixture in MACRO_TRANSCRIPTION_FIXTURES:
        result = evaluate_macro_transcription(fixture.item)
        serialized = result.finding.model_dump_json()
        for fact in fixture.item.cited_facts:
            assert str(fact.value) not in serialized
