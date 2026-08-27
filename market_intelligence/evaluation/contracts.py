"""Narrow, strict public contract for the offline agent-evaluation foundation.

This module defines **only** the data shapes for recording a repeatable agent
evaluation: the fixed enums, one evaluation finding, one human citation
adjudication, and one evaluation-run record. It contains no scoring, no
extraction, no repeatability logic, and no I/O -- see
``market_intelligence/evaluation/rubric.py`` (deterministic rubric-completeness
validator) and ``market_intelligence/evaluation/serialization.py`` (safe local
JSON round trip) for the rest of the foundation, and
``docs/AGENT_EVALUATION_HARNESS.md`` for the scope boundary.

Everything here is intentionally offline and pure. Nothing in this module (or
anywhere else under ``market_intelligence/evaluation/``) imports a data
connector, the OpenAI client, the DuckDB storage layer, an agent, or the
orchestration layer, and nothing makes a network request. See
``docs/AGENT_EVALUATION_HARNESS.md`` and the offline-guarantee test in
``market_intelligence/tests/test_evaluation_offline.py``.

Design rules (all enforced below):

- Pydantic v2 models, ``extra="forbid"``, every string and list bounded.
- Timestamps must be timezone-aware and are normalized to UTC.
- Run IDs are generated locally and deterministically (``build_run_id``) from
  sanitized inputs -- never a provider response ID.
- There is deliberately **no** field for a raw model response, a provider
  response ID, a credential, a URL, a database path, or an unrestricted
  metadata dictionary. Free-text fields additionally reject a few obvious
  leak shapes as defense-in-depth (``_reject_leaky_text``).
- ``citation classification`` and ``citation reason`` may only ever be one of
  the fixed enum members below.
- ``reason == CitationReason.OTHER`` requires a short bounded reviewer note;
  every other reason forbids a free-text reviewer note.
- ``CitationClassification.UNABLE_TO_DETERMINE`` is a first-class outcome. No
  validator here (or in ``rubric.py``) ever converts it into a pass or a
  failure automatically.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

SCHEMA_VERSION = "evaluation-foundation-1"

# --- Fixed enums ----------------------------------------------------------------


class AgentIdentifier(StrEnum):
    """The three bounded, single-turn agents this foundation can characterize."""

    MARKET_EVIDENCE = "market_evidence"
    NEWS_ANALYST = "news_analyst"
    MACRO_ANALYST = "macro_analyst"


class FindingSeverity(StrEnum):
    """Severity of one recorded evaluation finding.

    ``FAILURE`` records a concrete, observed defect in a characterization; it is
    **not** a statement that the agent as a whole is invalid, and completion of
    a characterization that contains failures is still a valid completed
    characterization (see ``rubric.py``).
    """

    INFO = "info"
    WARNING = "warning"
    FAILURE = "failure"


class CitationClassification(StrEnum):
    """Human adjudication of whether one cited evidence item supports one claim.

    ``UNABLE_TO_DETERMINE`` is first-class and permanent: it is never resolved
    by guessing and never auto-converted into ``SUPPORTED``/``UNSUPPORTED``.
    """

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNABLE_TO_DETERMINE = "unable_to_determine"


class CitationReason(StrEnum):
    """The single fixed reason recorded alongside a ``CitationClassification``.

    Exactly one is recorded per adjudication. ``OTHER`` -- and only ``OTHER`` --
    requires a short bounded ``reviewer_note`` (see ``CitationAdjudication``).
    """

    VALUE_MATCHES_EVIDENCE = "value_matches_evidence"
    VALUE_ABSENT_FROM_EVIDENCE = "value_absent_from_evidence"
    VALUE_CONFLICTS_WITH_EVIDENCE = "value_conflicts_with_evidence"
    EVIDENCE_IS_OFF_TOPIC = "evidence_is_off_topic"
    CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION = "claim_adds_unsupported_characterization"
    CLAIM_SCOPE_EXCEEDS_SINGLE_OBSERVATION = "claim_scope_exceeds_single_observation"
    SANITIZED_MATERIAL_INSUFFICIENT = "sanitized_material_insufficient"
    OTHER = "other"


class FindingCategory(StrEnum):
    """Fixed, bounded category for one evaluation finding.

    This mirrors the in-scope evaluation dimensions named in
    ``docs/AGENT_EVALUATION_HARNESS.md``. It is a closed set: a finding that
    does not fit one of the first categories uses ``OTHER`` and should carry a
    ``detail`` note.
    """

    FACTUAL_TRANSCRIPTION = "factual_transcription"
    CITATION_SUPPORT = "citation_support"
    ABSTENTION_BEHAVIOR = "abstention_behavior"
    CROSS_AGENT_CONSISTENCY = "cross_agent_consistency"
    REPEATABILITY = "repeatability"
    RUBRIC_COMPLETENESS = "rubric_completeness"
    SCOPE_BOUNDARY = "scope_boundary"
    OTHER = "other"


# --- Shared field helpers -----------------------------------------------------

_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"://"),  # any URL scheme
    re.compile(r"\\\\"),  # UNC path
    re.compile(r"[A-Za-z]:[\\/]"),  # Windows drive-letter path
    re.compile(r"\bresp_[A-Za-z0-9]{6,}\b"),  # OpenAI response-id shape
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9]{8,}\b"),  # API-key shape
)


def _reject_leaky_text(value: str) -> str:
    """Reject a handful of obvious credential / URL / path / response-id shapes.

    Defense-in-depth only -- the primary guarantee is that there is no field
    for any of those things. This never echoes the offending value.
    """
    for pattern in _LEAK_PATTERNS:
        if pattern.search(value):
            raise ValueError(
                "text contains a disallowed URL, path, credential, or response-id shape"
            )
    return value


def _to_utc(value: datetime) -> datetime:
    """Require a timezone-aware datetime; normalize it to UTC.

    A naive datetime is rejected outright (never assumed to be UTC).
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


SafeText = Annotated[str, AfterValidator(_reject_leaky_text)]
UtcTimestamp = Annotated[datetime, AfterValidator(_to_utc)]

_Identifier = Annotated[str, Field(min_length=1, max_length=64), AfterValidator(_reject_leaky_text)]
_ShortText = Annotated[
    str, Field(min_length=1, max_length=200), AfterValidator(_reject_leaky_text)
]
_ReviewerNote = Annotated[
    str, Field(min_length=1, max_length=280), AfterValidator(_reject_leaky_text)
]
_DetailText = Annotated[
    str, Field(min_length=1, max_length=600), AfterValidator(_reject_leaky_text)
]

_RUN_ID_RE = re.compile(r"^evalrun-[0-9a-f]{24}$")


def build_run_id(
    agent: AgentIdentifier | str,
    characterization_label: str,
    created_at: datetime,
) -> str:
    """Deterministically derive a stable local run ID from sanitized inputs.

    The ID is a function of the agent, the characterization label, and the
    (UTC-normalized) creation timestamp only -- it never incorporates a
    provider response ID, a path, or any credential. Re-deriving it from the
    same three inputs always yields the same ID.
    """
    agent_value = str(AgentIdentifier(agent))
    _reject_leaky_text(characterization_label)
    stamp = _to_utc(created_at).isoformat()
    digest = hashlib.sha256(
        f"{agent_value}\x1f{characterization_label}\x1f{stamp}".encode()
    ).hexdigest()[:24]
    return f"evalrun-{digest}"


# --- Models -----------------------------------------------------------------


class ClaimCitationPair(BaseModel):
    """One (claim, cited-evidence) pair that a characterization expects an
    adjudication for. ``claim_id``/``citation_id`` are sanitized local handles,
    never raw evidence content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: _Identifier
    citation_id: _Identifier

    def as_tuple(self) -> tuple[str, str]:
        return (self.claim_id, self.citation_id)


# --- Classification / reason compatibility matrix ----------------------------
#
# The deterministic compatibility matrix for the human citation-support rubric.
# Independent enum validation of ``classification`` and ``reason`` is not
# enough -- some combinations are semantically contradictory (e.g. a
# ``supported`` adjudication whose reason is ``value_conflicts_with_evidence``).
# This is the single, explicit, immutable source of truth for which
# ``CitationReason`` values a given ``CitationClassification`` may pair with,
# enforced by ``CitationAdjudication`` below.
#
# ``CitationReason.OTHER`` is permitted for every classification (it always
# carries a required bounded ``reviewer_note``). ``unable_to_determine``
# remains a valid first-class outcome -- it simply has its own narrow allowed
# reason set. This matrix is deliberately minimal: it only forbids
# combinations that cannot be coherent, and never widens or narrows what the
# enums themselves already permit for any other field.
CLASSIFICATION_REASON_MATRIX: Mapping[CitationClassification, frozenset[CitationReason]] = (
    MappingProxyType(
        {
            CitationClassification.SUPPORTED: frozenset(
                {
                    CitationReason.VALUE_MATCHES_EVIDENCE,
                    CitationReason.OTHER,
                }
            ),
            CitationClassification.PARTIALLY_SUPPORTED: frozenset(
                {
                    CitationReason.VALUE_ABSENT_FROM_EVIDENCE,
                    CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION,
                    CitationReason.CLAIM_SCOPE_EXCEEDS_SINGLE_OBSERVATION,
                    CitationReason.OTHER,
                }
            ),
            CitationClassification.UNSUPPORTED: frozenset(
                {
                    CitationReason.VALUE_ABSENT_FROM_EVIDENCE,
                    CitationReason.VALUE_CONFLICTS_WITH_EVIDENCE,
                    CitationReason.EVIDENCE_IS_OFF_TOPIC,
                    CitationReason.CLAIM_ADDS_UNSUPPORTED_CHARACTERIZATION,
                    CitationReason.CLAIM_SCOPE_EXCEEDS_SINGLE_OBSERVATION,
                    CitationReason.OTHER,
                }
            ),
            CitationClassification.UNABLE_TO_DETERMINE: frozenset(
                {
                    CitationReason.SANITIZED_MATERIAL_INSUFFICIENT,
                    CitationReason.OTHER,
                }
            ),
        }
    )
)

# Fixed, sanitized message for an incompatible classification/reason pairing.
# Deliberately names neither the classification nor the reason, and never
# reproduces a reviewer note, an identifier, a path, or record content.
INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE = (
    "classification and reason are not a permitted combination in the "
    "citation-support compatibility matrix"
)


class CitationAdjudication(BaseModel):
    """One human adjudication of one (claim, cited-evidence) pair.

    ``classification`` and ``reason`` may only be members of the fixed enums,
    and their pairing must be permitted by ``CLASSIFICATION_REASON_MATRIX``.
    ``reviewer_note`` is required when -- and only when -- ``reason`` is
    ``CitationReason.OTHER``.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: _Identifier
    citation_id: _Identifier
    reviewer: _Identifier
    adjudicated_at: UtcTimestamp
    classification: CitationClassification
    reason: CitationReason
    reviewer_note: _ReviewerNote | None = None

    @model_validator(mode="after")
    def _check_reviewer_note(self) -> CitationAdjudication:
        if self.reason is CitationReason.OTHER and self.reviewer_note is None:
            raise ValueError("reason 'other' requires a short reviewer_note")
        if self.reason is not CitationReason.OTHER and self.reviewer_note is not None:
            raise ValueError(
                "reviewer_note is only allowed when reason is 'other'"
            )
        return self

    @model_validator(mode="after")
    def _check_classification_reason_compatible(self) -> CitationAdjudication:
        if self.reason not in CLASSIFICATION_REASON_MATRIX[self.classification]:
            raise ValueError(INCOMPATIBLE_CLASSIFICATION_REASON_MESSAGE)
        return self

    def as_pair(self) -> tuple[str, str]:
        return (self.claim_id, self.citation_id)


class EvaluationFinding(BaseModel):
    """One recorded observation about a characterization.

    A finding is descriptive, never a verdict on the agent. ``category`` and
    ``severity`` come only from the fixed enums.
    """

    model_config = ConfigDict(extra="forbid")

    category: FindingCategory
    severity: FindingSeverity
    summary: _ShortText
    claim_id: _Identifier | None = None
    citation_id: _Identifier | None = None
    detail: _DetailText | None = None

    @model_validator(mode="after")
    def _check_other_detail(self) -> EvaluationFinding:
        if self.category is FindingCategory.OTHER and self.detail is None:
            raise ValueError("category 'other' requires a detail note")
        return self


class EvaluationRunRecord(BaseModel):
    """One recorded evaluation-run characterization of one agent.

    This is the single unit the serialization helpers read and write. It holds
    the expected (claim, citation) pairs, the human adjudications recorded so
    far, and any findings. Whether the rubric is *complete* is not stored here
    -- it is computed deterministically by
    ``market_intelligence.evaluation.rubric.check_rubric_completeness``.

    A completed characterization -- even one whose adjudications are entirely
    ``unsupported`` / ``partially_supported`` / ``unable_to_determine``, and
    even one carrying ``FAILURE`` findings -- is a valid completed
    characterization. Completion never implies the agent is validated or that
    any run universally passes.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: Annotated[str, Field(pattern=_RUN_ID_RE.pattern)]
    schema_version: Annotated[str, Field(pattern=r"^evaluation-foundation-1$")] = SCHEMA_VERSION
    agent: AgentIdentifier
    characterization_label: _ShortText
    created_at: UtcTimestamp
    evidence_fixture_name: _ShortText
    summary: _ShortText
    expected_pairs: Annotated[
        list[ClaimCitationPair], Field(min_length=1, max_length=500)
    ]
    adjudications: Annotated[
        list[CitationAdjudication], Field(default_factory=list, max_length=500)
    ]
    findings: Annotated[
        list[EvaluationFinding], Field(default_factory=list, max_length=200)
    ]

    @model_validator(mode="after")
    def _check_run_id_matches(self) -> EvaluationRunRecord:
        expected = build_run_id(self.agent, self.characterization_label, self.created_at)
        if self.run_id != expected:
            raise ValueError(
                "run_id is not the deterministic id for this agent/label/created_at"
            )
        return self
