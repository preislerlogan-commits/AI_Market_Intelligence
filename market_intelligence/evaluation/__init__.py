"""Offline foundation for the repeatable agent-evaluation methodology.

This package is the *safe, offline* part of Phase 0 criterion P0-7 (see
``docs/PHASE_0_EXIT.md`` and ``docs/AGENT_EVALUATION_HARNESS.md``). It provides:

- ``contracts`` -- strict Pydantic v2 shapes: the fixed enums, one evaluation
  finding, one human citation adjudication, and one evaluation-run record;
- ``rubric`` -- a deterministic rubric-completeness validator;
- ``serialization`` -- pure JSON helpers plus a narrow, symlink-refusing,
  no-overwrite, atomic, bounded local file round trip;
- ``fixtures`` -- small hand-authored synthetic/redacted records for tests.

It deliberately does **not** implement factual-transcription extraction,
lexical-overlap scoring, repeatability requests, live-output recording, an LLM
judge, or any connector / OpenAI / database / agent call. Nothing here imports
a data connector, the model client, the storage layer, an agent, or the
orchestration layer, and nothing makes a network request.

**No agent has been evaluated by this package.** No factual-transcription
result and no citation-support adjudication of any real agent output exists.
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
    "MAX_RECORD_BYTES",
    "EvaluationSerializationError",
    "from_json_str",
    "read_record",
    "to_json_str",
    "write_record",
]
