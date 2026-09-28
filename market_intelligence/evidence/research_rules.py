"""Research-reference rules (design §B.10, §C.1).

Only one recorded study schema is registered: the SPY VWAP-reversion
confirmation result (``spy-vwap-reversion-confirmation-1``). Its label enums
and fixed notes are imported from the existing, unchanged result contract.
Nothing here reads a result file or any holdout data.

Fail-closed rules:

- an unregistered result schema is refused;
- ``holdout_result`` is refused: no holdout result is recorded;
- the SPY VWAP study's ``holdout_state`` must be ``sealed``;
- the research status must follow a status rule explicitly defined by the
  reviewed design and registry. Only the recorded confirmation mapping is
  defined (``confirmation_result`` + ``supported_for_further_shadow_research``
  -> ``shadow_pending``); every other label is refused rather than guessed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    CONFIRMATION_SCHEMA_VERSION,
    FIXED_NOTES,
    SecondaryLabel,
    StudyLabel,
)
from market_intelligence.evidence.contracts import EvidenceResearchReference
from market_intelligence.evidence.enums import HoldoutState, ResearchStatus, ResultKind
from market_intelligence.evidence.errors import EvidenceValidationError


@dataclass(frozen=True)
class ResearchSchemaRule:
    study_id: str
    primary_labels: frozenset[str]
    secondary_labels: frozenset[str]
    fixed_caveats: frozenset[str]
    status_by_kind_and_label: Mapping[tuple[ResultKind, str], ResearchStatus]


RESEARCH_SCHEMAS: Mapping[str, ResearchSchemaRule] = MappingProxyType(
    {
        CONFIRMATION_SCHEMA_VERSION: ResearchSchemaRule(
            study_id="spy_vwap_reversion",
            primary_labels=frozenset(label.value for label in StudyLabel),
            secondary_labels=frozenset(label.value for label in SecondaryLabel),
            fixed_caveats=frozenset(FIXED_NOTES),
            status_by_kind_and_label=MappingProxyType(
                {
                    (
                        ResultKind.CONFIRMATION_RESULT,
                        StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH.value,
                    ): ResearchStatus.SHADOW_PENDING,
                }
            ),
        )
    }
)


def validate_research_reference(reference: EvidenceResearchReference) -> None:
    rule = RESEARCH_SCHEMAS.get(reference.result_schema_version)
    if rule is None:
        raise EvidenceValidationError("unknown_research_schema")
    if reference.study_id != rule.study_id:
        raise EvidenceValidationError("research_study_mismatch")
    if reference.result_kind is ResultKind.HOLDOUT_RESULT:
        raise EvidenceValidationError("holdout_result_not_recorded")
    if reference.holdout_state is not HoldoutState.SEALED:
        raise EvidenceValidationError("holdout_state_must_be_sealed")
    if reference.primary_label not in rule.primary_labels:
        raise EvidenceValidationError("research_label_unknown")
    if not set(reference.secondary_labels) <= rule.secondary_labels:
        raise EvidenceValidationError("research_label_unknown")
    if not rule.fixed_caveats <= set(reference.fixed_caveats):
        raise EvidenceValidationError("research_fixed_caveat_missing")
    expected = rule.status_by_kind_and_label.get(
        (reference.result_kind, reference.primary_label)
    )
    if expected is None:
        raise EvidenceValidationError("research_status_rule_missing")
    if reference.research_status is not expected:
        raise EvidenceValidationError("research_status_mismatch")
