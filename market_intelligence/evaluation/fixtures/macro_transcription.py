"""Synthetic fixtures for the Macro Analyst deterministic factual-transcription check.

Every fixture here is **synthetic and hand-authored**. None contains real
article text, real URLs, real evidence IDs from any live run, real observation
values from any live run, credentials, provider response IDs, or copied model
output. Identifiers (``claim-1``, ``cite-1``, ...) and series IDs are
placeholders. They exist only to exercise
``market_intelligence/evaluation/macro_factual_transcription.py`` and prove no
agent quality of any kind. See ``PROVENANCE.md`` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass

from market_intelligence.evaluation.macro_factual_transcription import (
    MacroTranscriptionInput,
    TranscriptionEvidenceFact,
)


@dataclass(frozen=True)
class MacroTranscriptionFixture:
    """One synthetic claim + its cited synthetic evidence, plus the expected result."""

    name: str
    description: str
    item: MacroTranscriptionInput
    expected_outcome: str
    expected_form: str
    expected_mismatch_categories: tuple[str, ...]


def _single_fact(**overrides) -> TranscriptionEvidenceFact:
    fields = dict(
        citation_id="cite-1",
        series_id="FEDFUNDS",
        observation_date="2026-07-01",
        value="3.63",
        frequency="monthly",
        units="percent",
    )
    fields.update(overrides)
    return TranscriptionEvidenceFact(**fields)


_EXACT_MATCH_SINGLE = MacroTranscriptionFixture(
    name="exact_match_single",
    description="Single-observation claim whose every token matches the cited fact.",
    item=MacroTranscriptionInput(
        claim_id="claim-1",
        claim_series_id="FEDFUNDS",
        claim_summary=(
            "The stored monthly observation dated 2026-07-01 is 3.63 percent, "
            "per official FRED metadata."
        ),
        cited_facts=[_single_fact()],
    ),
    expected_outcome="match",
    expected_form="single_observation",
    expected_mismatch_categories=(),
)

_WRONG_VALUE = MacroTranscriptionFixture(
    name="wrong_value",
    description="Single-observation claim states a value the cited fact does not carry.",
    item=MacroTranscriptionInput(
        claim_id="claim-2",
        claim_series_id="FEDFUNDS",
        claim_summary="The stored monthly observation dated 2026-07-01 is 9.99 percent.",
        cited_facts=[_single_fact(value="3.63")],
    ),
    expected_outcome="mismatch",
    expected_form="single_observation",
    expected_mismatch_categories=("value",),
)

_WRONG_DATE = MacroTranscriptionFixture(
    name="wrong_date",
    description="Single-observation claim states a date the cited fact does not carry.",
    item=MacroTranscriptionInput(
        claim_id="claim-3",
        claim_series_id="FEDFUNDS",
        claim_summary="The stored monthly observation dated 2026-06-01 is 3.63 percent.",
        cited_facts=[_single_fact(observation_date="2026-07-01")],
    ),
    expected_outcome="mismatch",
    expected_form="single_observation",
    expected_mismatch_categories=("observation_date",),
)

_WRONG_SERIES = MacroTranscriptionFixture(
    name="wrong_series",
    description="Claim's declared series does not match the cited fact's series.",
    item=MacroTranscriptionInput(
        claim_id="claim-4",
        claim_series_id="GS10",
        claim_summary="The stored monthly observation dated 2026-07-01 is 3.63 percent.",
        cited_facts=[_single_fact(series_id="FEDFUNDS")],
    ),
    expected_outcome="mismatch",
    expected_form="single_observation",
    expected_mismatch_categories=("series_id",),
)

_WRONG_UNITS_FREQUENCY = MacroTranscriptionFixture(
    name="wrong_units_frequency",
    description="Claim states the wrong reporting frequency and the wrong units.",
    item=MacroTranscriptionInput(
        claim_id="claim-5",
        claim_series_id="CPIAUCSL",
        claim_summary="The stored quarterly observation dated 2026-07-01 is 315.0 index points.",
        cited_facts=[
            _single_fact(
                series_id="CPIAUCSL",
                observation_date="2026-07-01",
                value="315.0",
                frequency="monthly",
                units="index",
            )
        ],
    ),
    expected_outcome="mismatch",
    expected_form="single_observation",
    expected_mismatch_categories=("frequency", "units"),
)

_INCORRECT_DIRECTION = MacroTranscriptionFixture(
    name="incorrect_direction",
    description=(
        "Comparison claim states 'increased' while the two cited values fell."
    ),
    item=MacroTranscriptionInput(
        claim_id="claim-6",
        claim_series_id="UNRATE",
        claim_summary=(
            "The stored monthly observation dated 2026-07-01 was 4.10; it "
            "increased from the stored monthly observation dated 2026-06-01, "
            "which was 4.30."
        ),
        cited_facts=[
            TranscriptionEvidenceFact(
                citation_id="cite-prev",
                series_id="UNRATE",
                observation_date="2026-06-01",
                value="4.30",
                frequency="monthly",
            ),
            TranscriptionEvidenceFact(
                citation_id="cite-latest",
                series_id="UNRATE",
                observation_date="2026-07-01",
                value="4.10",
                frequency="monthly",
            ),
        ],
    ),
    expected_outcome="mismatch",
    expected_form="comparison",
    expected_mismatch_categories=("comparison_direction",),
)

_UNSUPPORTED_WORDING = MacroTranscriptionFixture(
    name="unsupported_wording",
    description=(
        "Claim wording is not one of the two recognized controlled statement "
        "forms; deterministic check cannot apply, so it is routed to human review."
    ),
    item=MacroTranscriptionInput(
        claim_id="claim-7",
        claim_series_id="FEDFUNDS",
        claim_summary=(
            "The latest FEDFUNDS reading of 3.63 percent reflects the current "
            "policy stance as of mid-2026."
        ),
        cited_facts=[_single_fact()],
    ),
    expected_outcome="human_review",
    expected_form="unrecognized",
    expected_mismatch_categories=(),
)

_EXTRA_UNRELATED_NUMBERS = MacroTranscriptionFixture(
    name="extra_unrelated_numbers",
    description=(
        "Comparison claim whose sentence carries many digit runs (two full "
        "dates); the check reads only the named value groups and still matches, "
        "proving it does not generically scrape numbers."
    ),
    item=MacroTranscriptionInput(
        claim_id="claim-8",
        claim_series_id="GS10",
        claim_summary=(
            "The stored monthly observation dated 2026-07-01 was 4.23; it "
            "increased from the stored monthly observation dated 2026-06-01, "
            "which was 4.09."
        ),
        cited_facts=[
            TranscriptionEvidenceFact(
                citation_id="cite-prev",
                series_id="GS10",
                observation_date="2026-06-01",
                value="4.090000",
                frequency="monthly",
            ),
            TranscriptionEvidenceFact(
                citation_id="cite-latest",
                series_id="GS10",
                observation_date="2026-07-01",
                value="4.230000",
                frequency="monthly",
            ),
        ],
    ),
    expected_outcome="match",
    expected_form="comparison",
    expected_mismatch_categories=(),
)

_EXACT_MATCH_COMPARISON = MacroTranscriptionFixture(
    name="exact_match_comparison",
    description="Comparison claim whose dates, values, and direction all match.",
    item=MacroTranscriptionInput(
        claim_id="claim-9",
        claim_series_id="FEDFUNDS",
        claim_summary=(
            "The stored monthly observation dated 2026-07-01 was 5.330000; it "
            "increased from the stored monthly observation dated 2026-06-01, "
            "which was 5.000000."
        ),
        cited_facts=[
            TranscriptionEvidenceFact(
                citation_id="cite-prev",
                series_id="FEDFUNDS",
                observation_date="2026-06-01",
                value="5.00",
                frequency="monthly",
            ),
            TranscriptionEvidenceFact(
                citation_id="cite-latest",
                series_id="FEDFUNDS",
                observation_date="2026-07-01",
                value="5.33",
                frequency="monthly",
            ),
        ],
    ),
    expected_outcome="match",
    expected_form="comparison",
    expected_mismatch_categories=(),
)


MACRO_TRANSCRIPTION_FIXTURES: tuple[MacroTranscriptionFixture, ...] = (
    _EXACT_MATCH_SINGLE,
    _WRONG_VALUE,
    _WRONG_DATE,
    _WRONG_SERIES,
    _WRONG_UNITS_FREQUENCY,
    _INCORRECT_DIRECTION,
    _UNSUPPORTED_WORDING,
    _EXTRA_UNRELATED_NUMBERS,
    _EXACT_MATCH_COMPARISON,
)

MACRO_TRANSCRIPTION_FIXTURES_BY_NAME: dict[str, MacroTranscriptionFixture] = {
    fixture.name: fixture for fixture in MACRO_TRANSCRIPTION_FIXTURES
}


def load_macro_transcription_fixtures() -> tuple[MacroTranscriptionFixture, ...]:
    return MACRO_TRANSCRIPTION_FIXTURES
