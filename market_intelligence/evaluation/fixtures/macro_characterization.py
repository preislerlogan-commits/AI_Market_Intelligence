"""Synthetic fixture for the offline Macro characterization workflow.

Everything here is **synthetic and hand-authored**. It contains no real article
text, no real URLs, no evidence IDs from any live run, no observation values
from any live run, no credentials, no provider response IDs, and no copied model
output. Identifiers (``claim-single-match``, ``cite-a``, ...) and series IDs are
placeholders. It exists only to exercise
``market_intelligence/evaluation/macro_characterization_workflow.py`` and
``scripts/characterize_macro_report.py``, and proves no agent quality of any
kind. See ``PROVENANCE.md`` in this directory.
"""

from __future__ import annotations

from datetime import UTC, datetime

from market_intelligence.evaluation.contracts import (
    AgentIdentifier,
    CitationAdjudication,
    CitationClassification,
    CitationReason,
    ClaimCitationPair,
    build_run_id,
)
from market_intelligence.evaluation.macro_characterization_input import (
    MacroAdjudicationInput,
    MacroCharacterizationInput,
)
from market_intelligence.evaluation.macro_factual_transcription import (
    MacroTranscriptionInput,
    TranscriptionEvidenceFact,
)

# A complete, multi-claim synthetic Macro characterization covering the three
# transcription outcomes (match / mismatch / human-review) plus a two-cited-fact
# comparison claim whose two citations both need adjudication.
COMPLETE_MULTI_CLAIM = MacroCharacterizationInput(
    characterization_label="synthetic-macro-characterization-complete",
    claims=[
        MacroTranscriptionInput(
            claim_id="claim-single-match",
            claim_series_id="SYNTHRATE",
            claim_summary=(
                "The stored monthly observation dated 2031-04-01 is 2.50 percent, "
                "per official FRED metadata."
            ),
            cited_facts=[
                TranscriptionEvidenceFact(
                    citation_id="cite-a",
                    series_id="SYNTHRATE",
                    observation_date="2031-04-01",
                    value="2.50",
                    frequency="monthly",
                    units="percent",
                )
            ],
        ),
        MacroTranscriptionInput(
            claim_id="claim-single-mismatch",
            claim_series_id="SYNTHPRICE",
            claim_summary=(
                "The stored monthly observation dated 2031-04-01 is 318.00 index points."
            ),
            cited_facts=[
                TranscriptionEvidenceFact(
                    citation_id="cite-b",
                    series_id="SYNTHPRICE",
                    observation_date="2031-04-01",
                    value="315.00",
                    frequency="monthly",
                    units="index points",
                )
            ],
        ),
        MacroTranscriptionInput(
            claim_id="claim-unrecognized",
            claim_series_id="SYNTHRATE",
            claim_summary=(
                "The latest SYNTHRATE reading reflects the prevailing policy stance "
                "as of spring 2031."
            ),
            cited_facts=[
                TranscriptionEvidenceFact(
                    citation_id="cite-c",
                    series_id="SYNTHRATE",
                    observation_date="2031-04-01",
                    value="2.50",
                    frequency="monthly",
                    units="percent",
                )
            ],
        ),
        MacroTranscriptionInput(
            claim_id="claim-comparison-match",
            claim_series_id="SYNTHLABOR",
            claim_summary=(
                "The stored monthly observation dated 2031-04-01 was 4.10; it "
                "decreased from the stored monthly observation dated 2031-03-01, "
                "which was 4.30."
            ),
            cited_facts=[
                TranscriptionEvidenceFact(
                    citation_id="cite-d-prev",
                    series_id="SYNTHLABOR",
                    observation_date="2031-03-01",
                    value="4.30",
                    frequency="monthly",
                ),
                TranscriptionEvidenceFact(
                    citation_id="cite-d-latest",
                    series_id="SYNTHLABOR",
                    observation_date="2031-04-01",
                    value="4.10",
                    frequency="monthly",
                ),
            ],
        ),
    ],
    expected_pairs=[
        ClaimCitationPair(claim_id="claim-single-match", citation_id="cite-a"),
        ClaimCitationPair(claim_id="claim-single-mismatch", citation_id="cite-b"),
        ClaimCitationPair(claim_id="claim-unrecognized", citation_id="cite-c"),
        ClaimCitationPair(claim_id="claim-comparison-match", citation_id="cite-d-prev"),
        ClaimCitationPair(claim_id="claim-comparison-match", citation_id="cite-d-latest"),
    ],
)


def load_complete_multi_claim() -> MacroCharacterizationInput:
    return COMPLETE_MULTI_CLAIM


# The fixed created_at used to derive the scaffold run_id for the completion
# fixture below. Synthetic; it corresponds to no real characterization.
CHARACTERIZATION_CREATED_AT = datetime(2031, 5, 1, tzinfo=UTC)
_ADJUDICATED_AT = datetime(2031, 5, 2, tzinfo=UTC)

# The completed human citation adjudications for ``COMPLETE_MULTI_CLAIM`` -- one
# per expected pair, exercising all four classifications. Every value is
# synthetic and hand-authored: reviewer id, timestamps, classifications, and
# reasons are placeholders and are not a judgement of any real agent output.
COMPLETE_ADJUDICATION_INPUT = MacroAdjudicationInput(
    run_id=build_run_id(
        AgentIdentifier.MACRO_ANALYST,
        COMPLETE_MULTI_CLAIM.characterization_label,
        CHARACTERIZATION_CREATED_AT,
    ),
    adjudications=[
        CitationAdjudication(
            claim_id="claim-single-match",
            citation_id="cite-a",
            reviewer="synthetic-reviewer",
            adjudicated_at=_ADJUDICATED_AT,
            classification=CitationClassification.SUPPORTED,
            reason=CitationReason.VALUE_MATCHES_EVIDENCE,
        ),
        CitationAdjudication(
            claim_id="claim-single-mismatch",
            citation_id="cite-b",
            reviewer="synthetic-reviewer",
            adjudicated_at=_ADJUDICATED_AT,
            classification=CitationClassification.UNSUPPORTED,
            reason=CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE,
        ),
        CitationAdjudication(
            claim_id="claim-unrecognized",
            citation_id="cite-c",
            reviewer="synthetic-reviewer",
            adjudicated_at=_ADJUDICATED_AT,
            classification=CitationClassification.UNABLE_TO_DETERMINE,
            reason=CitationReason.SANITIZED_MATERIAL_INSUFFICIENT,
        ),
        CitationAdjudication(
            claim_id="claim-comparison-match",
            citation_id="cite-d-prev",
            reviewer="synthetic-reviewer",
            adjudicated_at=_ADJUDICATED_AT,
            classification=CitationClassification.PARTIALLY_SUPPORTED,
            reason=CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION,
        ),
        CitationAdjudication(
            claim_id="claim-comparison-match",
            citation_id="cite-d-latest",
            reviewer="synthetic-reviewer",
            adjudicated_at=_ADJUDICATED_AT,
            classification=CitationClassification.SUPPORTED,
            reason=CitationReason.VALUE_MATCHES_EVIDENCE,
        ),
    ],
)


def load_complete_adjudication_input() -> MacroAdjudicationInput:
    return COMPLETE_ADJUDICATION_INPUT
