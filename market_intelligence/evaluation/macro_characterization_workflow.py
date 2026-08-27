"""Pure, offline builder + completion step for one Macro characterization.

This is the minimum offline workflow needed to *characterize* one Macro Analyst
report -- **not** to run one. It makes no live request and no database access;
it only consumes a local ``MacroCharacterizationInput`` (already sanitized) and
produces an ``EvaluationRunRecord`` scaffold plus one pending human-adjudication
template per expected claim/citation pair.

Two pure steps:

``build_macro_characterization``
    Runs the existing deterministic Macro factual-transcription check
    (``macro_factual_transcription.evaluate_macro_transcription``) for **every**
    claim, creates an ``EvaluationRunRecord`` carrying those transcription
    findings, and emits one :class:`PendingCitationAdjudication` for every
    expected pair. It **never** pre-classifies citation support -- the record's
    ``adjudications`` list is always empty -- and it **never** converts a
    transcription match into citation support (see
    :data:`TRANSCRIPTION_IS_NOT_CITATION_SUPPORT`).

``complete_macro_characterization``
    Accepts the run record plus the completed human ``CitationAdjudication``
    list, validates that every expected pair has **exactly one** adjudication
    (refusing missing, duplicate, or unexpected pairs), preserves every
    ``supported`` / ``partially_supported`` / ``unsupported`` /
    ``unable_to_determine`` classification exactly as recorded, and returns the
    completed ``EvaluationRunRecord``. Completion does **not** imply validation
    (see :data:`COMPLETION_IS_NOT_VALIDATION`).

Nothing here imports a connector, the model client, the storage layer, an
agent, or the orchestration layer, and nothing makes a network request.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from market_intelligence.evaluation.contracts import (
    AgentIdentifier,
    CitationAdjudication,
    EvaluationFinding,
    EvaluationRunRecord,
    FindingCategory,
    FindingSeverity,
    build_run_id,
)
from market_intelligence.evaluation.macro_characterization_input import (
    MacroCharacterizationInput,
)
from market_intelligence.evaluation.macro_factual_transcription import (
    MacroTranscriptionResult,
    evaluate_macro_transcription,
)
from market_intelligence.evaluation.rubric import check_rubric_completeness

# A fixed, non-leaking evidence-fixture handle recorded on every scaffold. It is
# deliberately generic: a characterization input may be a committed synthetic
# fixture or a local, gitignored real input, and either way this workflow only
# ever sees the sanitized ``MacroCharacterizationInput`` shape, never a raw
# evidence package, a database path, or a provider payload.
EVIDENCE_FIXTURE_NAME = "local/macro-characterization-input"

COMPLETION_IS_NOT_VALIDATION = (
    "Completing this workflow means every expected claim/citation pair has "
    "exactly one recorded human adjudication and the deterministic "
    "transcription check has run for every claim. It does not mean the Macro "
    "Analyst is validated, that any claim is factually accurate, that any cited "
    "evidence supports its claim, or that the run passes in any universal sense."
)

TRANSCRIPTION_IS_NOT_CITATION_SUPPORT = (
    "A deterministic transcription match confirms only that a claim's recognized "
    "value/date/unit/frequency/direction tokens transcribe the cited evidence "
    "fact. It is never recorded as, or converted into, citation support -- that "
    "is a separate human adjudication this builder always leaves pending."
)

_SCOPE_FINDING_SUMMARY = (
    "Citation support is not adjudicated by this builder; every expected pair "
    "has a pending human adjudication."
)


@dataclass(frozen=True)
class PendingCitationAdjudication:
    """One not-yet-adjudicated expected ``(claim_id, citation_id)`` pair.

    Carries **no** classification and **no** reason: the builder never
    pre-classifies citation support. A human reviewer records exactly one
    :class:`~market_intelligence.evaluation.contracts.CitationAdjudication` for
    this pair against the rubric in ``docs/AGENT_EVALUATION_HARNESS.md``.
    """

    claim_id: str
    citation_id: str
    classification: None = None
    reason: None = None

    def as_pair(self) -> tuple[str, str]:
        return (self.claim_id, self.citation_id)


@dataclass(frozen=True)
class CharacterizationBuildResult:
    """Output of :func:`build_macro_characterization`.

    ``run_record`` has an empty ``adjudications`` list (citation support is
    never pre-classified). ``transcription_results`` preserves input claim
    order. ``pending_adjudications`` has exactly one entry per expected pair.
    """

    run_record: EvaluationRunRecord
    transcription_results: tuple[MacroTranscriptionResult, ...]
    pending_adjudications: tuple[PendingCitationAdjudication, ...]


class MacroCharacterizationError(RuntimeError):
    """Sanitized workflow error.

    Never includes an identifier, a reviewer note, a filesystem path, a
    classification, or any record content -- only a fixed, non-input-derived
    description.
    """


def _outcome_counts(results: Sequence[MacroTranscriptionResult]) -> tuple[int, int, int]:
    match = sum(1 for r in results if r.outcome == "match")
    mismatch = sum(1 for r in results if r.outcome == "mismatch")
    human_review = sum(1 for r in results if r.outcome == "human_review")
    return (match, mismatch, human_review)


def build_macro_characterization(
    characterization: MacroCharacterizationInput,
    *,
    created_at: datetime,
) -> CharacterizationBuildResult:
    """Build the offline characterization scaffold for one Macro report.

    Pure and deterministic given ``characterization`` and ``created_at``:

    - runs :func:`evaluate_macro_transcription` for every claim, in order;
    - creates an ``EvaluationRunRecord`` (agent ``macro_analyst``, locally
      derived ``run_id``) carrying every transcription finding plus one
      ``scope_boundary`` ``info`` finding recording that citation support is not
      adjudicated here;
    - emits one :class:`PendingCitationAdjudication` per expected pair.

    The run record's ``adjudications`` list is always empty. A transcription
    match is never recorded as citation support.
    """
    results = tuple(
        evaluate_macro_transcription(claim) for claim in characterization.claims
    )

    scope_finding = EvaluationFinding(
        category=FindingCategory.SCOPE_BOUNDARY,
        severity=FindingSeverity.INFO,
        summary=_SCOPE_FINDING_SUMMARY,
    )
    findings = [scope_finding, *(result.finding for result in results)]

    match, mismatch, human_review = _outcome_counts(results)
    summary = (
        f"Offline Macro characterization scaffold: {len(characterization.claims)} "
        f"claims, {len(characterization.expected_pairs)} expected pairs; "
        f"transcription {match}/{mismatch}/{human_review} "
        f"match/mismatch/review; citation support pending."
    )

    run_id = build_run_id(
        AgentIdentifier.MACRO_ANALYST,
        characterization.characterization_label,
        created_at,
    )

    run_record = EvaluationRunRecord(
        run_id=run_id,
        agent=AgentIdentifier.MACRO_ANALYST,
        characterization_label=characterization.characterization_label,
        created_at=created_at,
        evidence_fixture_name=EVIDENCE_FIXTURE_NAME,
        summary=summary,
        expected_pairs=list(characterization.expected_pairs),
        adjudications=[],
        findings=findings,
    )

    pending = tuple(
        PendingCitationAdjudication(claim_id=pair.claim_id, citation_id=pair.citation_id)
        for pair in characterization.expected_pairs
    )

    return CharacterizationBuildResult(
        run_record=run_record,
        transcription_results=results,
        pending_adjudications=pending,
    )


def _completed_summary(
    run_record: EvaluationRunRecord, adjudications: Sequence[CitationAdjudication]
) -> str:
    return (
        f"Offline Macro characterization complete: "
        f"{len(run_record.expected_pairs)} expected pairs, "
        f"{len(adjudications)} human adjudications recorded; "
        f"completion is not validation."
    )


def complete_macro_characterization(
    run_record: EvaluationRunRecord,
    adjudications: Sequence[CitationAdjudication],
) -> EvaluationRunRecord:
    """Attach completed human adjudications to a characterization scaffold.

    The ``run_record`` must be an unadjudicated Macro scaffold:

    - ``run_record.agent`` must be ``AgentIdentifier.MACRO_ANALYST`` -- this
      workflow only completes Macro characterizations;
    - ``run_record.adjudications`` must be empty -- completion attaches the human
      adjudications exactly once; it never appends to or re-completes a record
      that already carries any.

    A record that fails either check raises a fixed, sanitized
    ``MacroCharacterizationError`` that reproduces no record content, identifier,
    classification, reviewer note, or path.

    It then validates that ``adjudications`` contains **exactly one** entry for
    **every** expected ``(claim_id, citation_id)`` pair in
    ``run_record.expected_pairs``:

    - a pair adjudicated more than once -> ``MacroCharacterizationError``;
    - an adjudication for a pair that was not expected -> ``MacroCharacterizationError``;
    - an expected pair with no adjudication -> ``MacroCharacterizationError``.

    Every ``supported`` / ``partially_supported`` / ``unsupported`` /
    ``unable_to_determine`` classification is preserved exactly as recorded --
    nothing here reclassifies, drops, or resolves an adjudication. The returned
    ``EvaluationRunRecord`` is fully re-validated. Completion does **not** imply
    the agent is validated (see :data:`COMPLETION_IS_NOT_VALIDATION`).
    """
    if run_record.agent is not AgentIdentifier.MACRO_ANALYST:
        raise MacroCharacterizationError(
            "run record is not a Macro Analyst characterization scaffold"
        )
    if run_record.adjudications:
        raise MacroCharacterizationError(
            "run record already carries adjudications; it is not an unadjudicated scaffold"
        )

    expected = [pair.as_tuple() for pair in run_record.expected_pairs]
    expected_set = set(expected)
    provided_counts = Counter(adj.as_pair() for adj in adjudications)

    if any(count > 1 for count in provided_counts.values()):
        raise MacroCharacterizationError(
            "completed adjudications contain more than one entry for a pair"
        )
    if any(pair not in expected_set for pair in provided_counts):
        raise MacroCharacterizationError(
            "completed adjudications include a pair that was not expected"
        )
    if any(provided_counts[pair] == 0 for pair in expected_set):
        raise MacroCharacterizationError(
            "completed adjudications are missing one or more expected pairs"
        )

    completed = EvaluationRunRecord(
        run_id=run_record.run_id,
        schema_version=run_record.schema_version,
        agent=run_record.agent,
        characterization_label=run_record.characterization_label,
        created_at=run_record.created_at,
        evidence_fixture_name=run_record.evidence_fixture_name,
        summary=_completed_summary(run_record, adjudications),
        expected_pairs=list(run_record.expected_pairs),
        adjudications=list(adjudications),
        findings=list(run_record.findings),
    )

    rubric = check_rubric_completeness(completed)
    if not rubric.complete:
        raise MacroCharacterizationError(
            "completed characterization did not pass the rubric-completeness check"
        )
    return completed
