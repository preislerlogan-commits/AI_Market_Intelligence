"""Strict local input contract for the offline Macro characterization workflow.

This is the single, evaluation-specific shape the offline Macro characterization
workflow (``macro_characterization_workflow.py``) and its CLI
(``scripts/characterize_macro_report.py``) accept. It is deliberately the
*minimum* needed to (a) run the existing deterministic Macro
factual-transcription check (``macro_factual_transcription.py``) for every claim
and (b) enumerate the expected claim/citation pairs a human reviewer must
adjudicate.

It carries **only**:

- a sanitized ``characterization_label`` (short, bounded, leak-screened);
- one entry per Macro claim -- the claim's local ``claim_id``, its declared
  ``claim_series_id``, the ``claim_summary`` string, and the one or two cited
  *synthetic / redacted* evidence facts the transcription evaluator needs
  (each an existing ``TranscriptionEvidenceFact``);
- the expected ``(claim_id, citation_id)`` pairs.

It has **no** field for a credential, a URL, a provider response ID, a raw
provider payload, an unrestricted metadata dictionary, a database path, or any
model reasoning -- there is simply nowhere to put those, and the free-text
fields additionally reject a few obvious leak shapes (inherited from
``TranscriptionEvidenceFact`` / ``contracts._reject_leaky_text``).

This module also defines :class:`MacroAdjudicationInput` -- the strict local
input for *completing* a characterization: only the scaffold's deterministic
``run_id`` and a bounded list of completed human
:class:`~market_intelligence.evaluation.contracts.CitationAdjudication` records,
and nothing else (same ``extra="forbid"``, no credential / URL / response ID /
path / raw evidence / model-reasoning / metadata field).

Nothing here imports a connector, the model client, the storage layer, an
agent, or the orchestration layer, and nothing makes a network request (see
``market_intelligence/tests/test_evaluation_offline.py``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.evaluation.contracts import (
    _RUN_ID_RE,
    CitationAdjudication,
    ClaimCitationPair,
    _reject_leaky_text,
)
from market_intelligence.evaluation.macro_factual_transcription import MacroTranscriptionInput
from market_intelligence.evaluation.serialization import (
    MAX_RECORD_BYTES,
    EvaluationSerializationError,
)

MAX_CLAIMS = 50
MAX_EXPECTED_PAIRS = 100
MAX_ADJUDICATIONS = 500

_Label = Annotated[
    str, Field(min_length=1, max_length=200), AfterValidator(_reject_leaky_text)
]


class MacroCharacterizationInput(BaseModel):
    """One local, offline Macro characterization input.

    ``claims`` holds one ``MacroTranscriptionInput`` per Macro claim (its
    ``claim_id`` must be unique across the list). ``expected_pairs`` lists every
    ``(claim_id, citation_id)`` a human reviewer is expected to adjudicate; each
    pair must reference a claim in ``claims`` and a ``citation_id`` that claim
    actually cites, every claim must be covered by at least one expected pair,
    and no pair may be listed twice.
    """

    model_config = ConfigDict(extra="forbid")

    characterization_label: _Label
    claims: Annotated[
        list[MacroTranscriptionInput], Field(min_length=1, max_length=MAX_CLAIMS)
    ]
    expected_pairs: Annotated[
        list[ClaimCitationPair], Field(min_length=1, max_length=MAX_EXPECTED_PAIRS)
    ]

    @model_validator(mode="after")
    def _check_consistent(self) -> MacroCharacterizationInput:
        claim_ids = [claim.claim_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claims contain a duplicate claim_id")

        citations_by_claim: dict[str, set[str]] = {
            claim.claim_id: {fact.citation_id for fact in claim.cited_facts}
            for claim in self.claims
        }

        seen: set[tuple[str, str]] = set()
        for pair in self.expected_pairs:
            key = pair.as_tuple()
            if key in seen:
                raise ValueError(
                    "expected_pairs contain a duplicate (claim_id, citation_id) pair"
                )
            seen.add(key)
            if pair.claim_id not in citations_by_claim:
                raise ValueError(
                    "an expected pair references a claim_id not present in claims"
                )
            if pair.citation_id not in citations_by_claim[pair.claim_id]:
                raise ValueError(
                    "an expected pair references a citation_id its claim does not cite"
                )

        covered = {pair.claim_id for pair in self.expected_pairs}
        if covered != set(claim_ids):
            raise ValueError("every claim must be covered by at least one expected pair")
        return self


class MacroAdjudicationInput(BaseModel):
    """Strict local input for completing a Macro characterization scaffold.

    Carries **only**:

    - ``run_id`` -- the scaffold ``EvaluationRunRecord``'s deterministic local
      id (``evalrun-<24 hex>``, a digest of agent + label + created_at; never a
      provider response id). The completion CLI refuses to proceed unless this
      matches the scaffold it was handed.
    - ``adjudications`` -- a bounded list of completed human
      :class:`~market_intelligence.evaluation.contracts.CitationAdjudication`
      records, exactly as the reviewer recorded them.

    It has **no** field for a credential, a URL, a provider response id, a raw
    provider payload, an unrestricted metadata dictionary, a database path, raw
    evidence text, or any model reasoning. Whether the adjudications actually
    cover every expected pair (with no missing, duplicate, or unexpected pair)
    is **not** checked here -- that is
    ``macro_characterization_workflow.complete_macro_characterization``'s job,
    which raises a sanitized error. This contract only bounds the shape.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: Annotated[str, Field(pattern=_RUN_ID_RE.pattern)]
    adjudications: Annotated[
        list[CitationAdjudication], Field(min_length=1, max_length=MAX_ADJUDICATIONS)
    ]


def input_to_json_str(model: MacroCharacterizationInput) -> str:
    """Serialize a ``MacroCharacterizationInput`` to deterministic, sorted JSON.

    Mirrors ``serialization.to_json_str``: sorted keys, two-space indent, a
    single trailing newline, so re-serializing an unchanged input is
    byte-stable.
    """
    payload = model.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def input_from_json_str(text: str) -> MacroCharacterizationInput:
    """Parse and validate a ``MacroCharacterizationInput`` from a JSON string."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise EvaluationSerializationError(
            "characterization input is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise EvaluationSerializationError(
            "characterization input must be a JSON object"
        )
    try:
        return MacroCharacterizationInput.model_validate(payload)
    except Exception:
        raise EvaluationSerializationError(
            "characterization input failed schema validation"
        ) from None


def read_characterization_input(
    path: str | os.PathLike[str],
) -> MacroCharacterizationInput:
    """Read and validate a ``MacroCharacterizationInput`` from a local file.

    Reuses the serialization boundary's safety rules: refuses a symlinked file
    or parent, and refuses a file larger than ``MAX_RECORD_BYTES`` before
    reading it. Every failure raises a sanitized ``EvaluationSerializationError``
    that never contains the path, the file bytes, or the input content.
    """
    source = Path(path)
    if source.parent.is_symlink():
        raise EvaluationSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise EvaluationSerializationError("target path is a symlink")
    if not source.exists():
        raise EvaluationSerializationError("characterization input file does not exist")
    if not source.is_file():
        raise EvaluationSerializationError("characterization input path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise EvaluationSerializationError("failed to stat characterization input") from None
    if size > MAX_RECORD_BYTES:
        raise EvaluationSerializationError("characterization input file is too large")
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EvaluationSerializationError("failed to read characterization input") from None
    return input_from_json_str(raw)


def adjudication_input_to_json_str(model: MacroAdjudicationInput) -> str:
    """Serialize a ``MacroAdjudicationInput`` to deterministic, sorted JSON.

    Mirrors ``input_to_json_str`` / ``serialization.to_json_str``: sorted keys,
    two-space indent, a single trailing newline.
    """
    payload = model.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def adjudication_input_from_json_str(text: str) -> MacroAdjudicationInput:
    """Parse and validate a ``MacroAdjudicationInput`` from a JSON string."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise EvaluationSerializationError(
            "adjudication input is not valid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise EvaluationSerializationError("adjudication input must be a JSON object")
    try:
        return MacroAdjudicationInput.model_validate(payload)
    except Exception:
        raise EvaluationSerializationError(
            "adjudication input failed schema validation"
        ) from None


def read_adjudication_input(
    path: str | os.PathLike[str],
) -> MacroAdjudicationInput:
    """Read and validate a ``MacroAdjudicationInput`` from a local file.

    Reuses the serialization boundary's safety rules: refuses a symlinked file
    or parent, and refuses a file larger than ``MAX_RECORD_BYTES`` before
    reading it. Every failure raises a sanitized ``EvaluationSerializationError``
    that never contains the path, the file bytes, or the input content.
    """
    source = Path(path)
    if source.parent.is_symlink():
        raise EvaluationSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise EvaluationSerializationError("target path is a symlink")
    if not source.exists():
        raise EvaluationSerializationError("adjudication input file does not exist")
    if not source.is_file():
        raise EvaluationSerializationError("adjudication input path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise EvaluationSerializationError("failed to stat adjudication input") from None
    if size > MAX_RECORD_BYTES:
        raise EvaluationSerializationError("adjudication input file is too large")
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EvaluationSerializationError("failed to read adjudication input") from None
    return adjudication_input_from_json_str(raw)
