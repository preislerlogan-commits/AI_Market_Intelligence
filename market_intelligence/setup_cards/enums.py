"""Bounded vocabularies for ``setup-card-1`` (docs/SETUP_CARD_CONTRACT.md §3, §5).

Seven separate state vocabularies, never collapsed into one "status":

1. evidence readiness (``EvidenceReadiness`` + ``ReadinessBlocker``);
2. setup qualification (``SetupQualification``);
3. setup lifecycle (``SetupLifecycle``);
4. display status (``DisplayStatus``, render-time only);
5. contract-selector outcome (the selector's own four ``SelectorStatus``
   values, reused unchanged from ``contract_selection``);
6. selector availability (``SelectorAvailability``);
7. research status (the Evidence Envelope's ``ResearchStatus`` and
   ``HoldoutState``, reused unchanged).
"""

from __future__ import annotations

from enum import StrEnum

from market_intelligence.contract_selection.contracts import SelectorStatus
from market_intelligence.evidence.enums import HoldoutState, ResearchStatus

# Vocabulary 5: exactly the selector's four outcomes, never extended here.
SelectorOutcome = SelectorStatus
# Vocabulary 7: the envelope's research vocabulary, reused unchanged.
CardResearchStatus = ResearchStatus
CardHoldoutState = HoldoutState


class CardKind(StrEnum):
    SETUP = "setup"
    NO_QUALIFIED_SETUP = "no_qualified_setup"


class Lane(StrEnum):
    VWAP_REVERSION = "vwap_reversion"
    TREND_CONTINUATION = "trend_continuation"


class EvidenceReadiness(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


class ReadinessBlocker(StrEnum):
    MISSING_REQUIRED_EVIDENCE = "missing_required_evidence"
    STALE_REQUIRED_EVIDENCE = "stale_required_evidence"
    UNKNOWN_FRESHNESS = "unknown_freshness"
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    AMBIGUOUS_CLOCK_FACTS = "ambiguous_clock_facts"
    UNRESOLVED_CRITICAL_CONFLICT = "unresolved_critical_conflict"
    HOLDOUT_RESTRICTED = "holdout_restricted"
    INELIGIBLE_REQUIRED_INPUT = "ineligible_required_input"


class SetupQualification(StrEnum):
    """What the authorized deterministic setup rules concluded."""

    QUALIFIED = "qualified"
    NOT_QUALIFIED = "not_qualified"
    INDETERMINATE = "indeterminate"
    NOT_EVALUATED = "not_evaluated"


CONCLUDED_QUALIFICATIONS = frozenset(
    {
        SetupQualification.QUALIFIED,
        SetupQualification.NOT_QUALIFIED,
        SetupQualification.INDETERMINATE,
    }
)
QUALIFICATION_ITEM_REQUIRED = frozenset(
    {SetupQualification.QUALIFIED, SetupQualification.NOT_QUALIFIED}
)


class SetupLifecycle(StrEnum):
    """Where an observation is in time and processing. Never qualification."""

    OBSERVED = "observed"
    DEVELOPING = "developing"
    EVALUATION_COMPLETE = "evaluation_complete"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


ACTIVE_LIFECYCLES = frozenset(
    {SetupLifecycle.OBSERVED, SetupLifecycle.DEVELOPING, SetupLifecycle.EVALUATION_COMPLETE}
)
TERMINAL_LIFECYCLES = frozenset({SetupLifecycle.INVALIDATED, SetupLifecycle.EXPIRED})
PRE_EVALUATION_LIFECYCLES = frozenset({SetupLifecycle.OBSERVED, SetupLifecycle.DEVELOPING})
# A lane-level "nothing qualified" conclusion exists only once evaluation is
# complete, or in a compatible later terminal state.
LANE_CONCLUSION_LIFECYCLES = frozenset(
    {SetupLifecycle.EVALUATION_COMPLETE, SetupLifecycle.INVALIDATED, SetupLifecycle.EXPIRED}
)
# Forward order of the active states; lifecycle never moves backward.
LIFECYCLE_ORDER = {
    SetupLifecycle.OBSERVED: 0,
    SetupLifecycle.DEVELOPING: 1,
    SetupLifecycle.EVALUATION_COMPLETE: 2,
}


class NoSetupReason(StrEnum):
    """Why a cited, authorized deterministic lane evaluation concluded
    ``not_qualified``. Missing authority or unready evidence is never a
    reason: it is a ``LaneQualificationAvailability`` state with no card."""

    CRITERIA_NOT_MET = "criteria_not_met"
    NO_CANDIDATE_EVALUATED = "no_candidate_evaluated"


class SelectorAvailability(StrEnum):
    """Infrastructure availability, separate from the selector's outcome."""

    NOT_REQUESTED = "not_requested"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class LaneQualificationAvailability(StrEnum):
    """A dashboard availability state, **outside the setup-card contract**.
    Only ``available`` can lead to a ``no_qualified_setup`` card; every other
    state produces no card, never a false ``no_qualified_setup`` conclusion.

    - ``not_authorized``: no registered setup definition for the lane;
    - ``unavailable``: a definition exists but its lane evaluation is
      missing, invalid, ambiguous or from the wrong producer;
    - ``evidence_blocked``: a valid ``not_qualified`` evaluation exists but
      the card's readiness derivation has blockers;
    - ``available``: a valid conclusion with ready evidence.
    """

    AVAILABLE = "available"
    NOT_AUTHORIZED = "not_authorized"
    UNAVAILABLE = "unavailable"
    EVIDENCE_BLOCKED = "evidence_blocked"


class DisplayStatus(StrEnum):
    """Render-time only; never stored in a card."""

    CURRENT_VIEW = "current_view"
    OUTDATED_VIEW = "outdated_view"
    SUPERSEDED = "superseded"
    HISTORICAL = "historical"


class CardRevisionReason(StrEnum):
    ORIGINAL = "original"
    NEWER_BUNDLE = "newer_bundle"
    EVIDENCE_CORRECTION = "evidence_correction"
    LIFECYCLE_CHANGE = "lifecycle_change"
    EXPIRATION = "expiration"
