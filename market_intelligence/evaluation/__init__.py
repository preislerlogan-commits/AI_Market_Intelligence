"""Offline foundation for the repeatable agent-evaluation methodology.

This package is the *safe, offline* part of Phase 0 criterion P0-7 (see
``docs/PHASE_0_EXIT.md`` and ``docs/AGENT_EVALUATION_HARNESS.md``). It provides:

- ``contracts`` -- strict Pydantic v2 shapes: the fixed enums, one evaluation
  finding, one human citation adjudication, and one evaluation-run record;
- ``rubric`` -- a deterministic rubric-completeness validator;
- ``serialization`` -- pure JSON helpers plus a narrow, symlink-refusing,
  no-overwrite, atomic, bounded local file round trip;
- ``macro_factual_transcription`` -- the *first, Macro-Analyst-only* deterministic
  factual-transcription check over synthetic claim/evidence inputs
  (``evaluate_macro_transcription``): it recognizes only the two exact
  controlled Macro Analyst statement forms, verifies series ID / observation
  date / ``Decimal`` value / frequency wording / units-when-stated / previous
  observation / comparison direction, and emits one ``info`` / ``failure`` /
  ``warning`` finding. A match is **not** a validation
  (``MATCH_IS_NOT_VALIDATION``);
- ``macro_characterization_input`` / ``macro_characterization_workflow`` -- the
  *first, offline* workflow to characterize one Macro Analyst report: a strict
  local input contract, a pure builder that runs the transcription check for
  every claim and emits one pending human-adjudication template per expected
  claim/citation pair (never pre-classifying citation support), and a pure
  completion step that attaches completed human adjudications only when every
  expected pair has exactly one. Completion is not validation
  (``CHARACTERIZATION_COMPLETION_IS_NOT_VALIDATION``);
- ``fixtures`` -- small hand-authored synthetic/redacted records and
  synthetic Macro-transcription / Macro-characterization inputs for tests.

**No real characterization has been performed.** The workflow is offline only;
it makes no live request and no database access. P0-7 and Phase 0 remain open.

It deliberately does **not** implement lexical-overlap scoring, the citation-
support rubric adjudication, the abstention matrix, cross-agent consistency,
repeatability requests, live-output recording, an LLM judge, or any
factual-transcription check for the Market Evidence Agent or News Analyst, and
does not import a data connector, the model client, the storage layer, an agent,
or the orchestration layer, and makes no network request.

**No real agent output has been evaluated by this package.** No
factual-transcription result of any live agent run and no citation-support
adjudication of any real agent output exists, and no first characterization has
been recorded. Phase 0 remains open.
"""

from __future__ import annotations

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
from market_intelligence.evaluation.macro_characterization_input import (
    MacroCharacterizationInput,
    input_from_json_str,
    input_to_json_str,
    read_characterization_input,
)
from market_intelligence.evaluation.macro_characterization_workflow import (
    COMPLETION_IS_NOT_VALIDATION as CHARACTERIZATION_COMPLETION_IS_NOT_VALIDATION,
)
from market_intelligence.evaluation.macro_characterization_workflow import (
    EVIDENCE_FIXTURE_NAME,
    TRANSCRIPTION_IS_NOT_CITATION_SUPPORT,
    CharacterizationBuildResult,
    MacroCharacterizationError,
    PendingCitationAdjudication,
    build_macro_characterization,
    complete_macro_characterization,
)
from market_intelligence.evaluation.macro_factual_transcription import (
    MATCH_IS_NOT_VALIDATION,
    MISMATCH_CATEGORIES,
    MacroTranscriptionInput,
    MacroTranscriptionResult,
    TranscriptionEvidenceFact,
    collect_findings,
    evaluate_macro_transcription,
    evaluate_macro_transcription_batch,
)
from market_intelligence.evaluation.rubric import (
    COMPLETION_IS_NOT_VALIDATION,
    RubricCompletenessResult,
    check_rubric_completeness,
)
from market_intelligence.evaluation.serialization import (
    MAX_RECORD_BYTES,
    EvaluationSerializationError,
    from_json_str,
    read_record,
    to_json_str,
    write_record,
)

__all__ = [
    "CLASSIFICATION_REASON_MATRIX",
    "INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE",
    "SCHEMA_VERSION",
    "AgentIdentifier",
    "CitationAdjudication",
    "CitationClassification",
    "CitationReason",
    "ClaimCitationPair",
    "EvaluationFinding",
    "EvaluationRunRecord",
    "FindingCategory",
    "FindingSeverity",
    "build_run_id",
    "COMPLETION_IS_NOT_VALIDATION",
    "RubricCompletenessResult",
    "check_rubric_completeness",
    "MATCH_IS_NOT_VALIDATION",
    "MISMATCH_CATEGORIES",
    "MacroTranscriptionInput",
    "MacroTranscriptionResult",
    "TranscriptionEvidenceFact",
    "collect_findings",
    "evaluate_macro_transcription",
    "evaluate_macro_transcription_batch",
    "MacroCharacterizationInput",
    "input_from_json_str",
    "input_to_json_str",
    "read_characterization_input",
    "CHARACTERIZATION_COMPLETION_IS_NOT_VALIDATION",
    "TRANSCRIPTION_IS_NOT_CITATION_SUPPORT",
    "EVIDENCE_FIXTURE_NAME",
    "CharacterizationBuildResult",
    "MacroCharacterizationError",
    "PendingCitationAdjudication",
    "build_macro_characterization",
    "complete_macro_characterization",
    "MAX_RECORD_BYTES",
    "EvaluationSerializationError",
    "from_json_str",
    "read_record",
    "to_json_str",
    "write_record",
]
