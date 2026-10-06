"""Trust-label formatting for the offline dashboard prototype.

Display text for the reviewed trust vocabulary
(docs/DASHBOARD_INFORMATION_ARCHITECTURE.md §8): every label is text first,
never color alone, and no wording frames a recommendation. Pure functions
only; nothing here renders, reads data or imports a UI framework.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from market_intelligence.contract_selection.contracts import SelectorStatus
from market_intelligence.evidence.enums import (
    ClockHealthReason,
    EvidenceKind,
    FreshnessState,
)
from market_intelligence.setup_cards.contracts import MANUAL_DECISION_STATEMENT
from market_intelligence.setup_cards.enums import (
    DisplayStatus,
    EvidenceReadiness,
    LaneQualificationAvailability,
    SelectorAvailability,
    SetupLifecycle,
    SetupQualification,
)

_EASTERN = ZoneInfo("America/New_York")

SCOPE_STATEMENT = "Decision support only. Manual execution. No orders are placed."
MANUAL_DECISION = MANUAL_DECISION_STATEMENT
SYNTHETIC_BANNER = (
    "SYNTHETIC PROTOTYPE. Every value on these pages is synthetic, not market data. "
    "No live provider, database or model is connected."
)
SYNTHETIC_DEFINITION_NOTE = (
    "Built from an in-memory synthetic-only setup definition to show future contract "
    "behavior. No live setup definition exists and live qualification is not authorized."
)
HOLDOUT_NOTICE = (
    "SPY price evidence dated 2026-09-23 through 2026-12-04 is restricted until the "
    "holdout is recorded. Restricted until the holdout is recorded."
)

KIND_LABELS: dict[EvidenceKind, str] = {
    EvidenceKind.CONFIRMED_FACT: "FACT",
    EvidenceKind.DETERMINISTIC_CALCULATION: "CALC",
    EvidenceKind.HISTORICAL_RESEARCH_RESULT: "RESEARCH",
    EvidenceKind.CURRENT_INFERENCE: "INFERENCE",
    EvidenceKind.MISSING_EVIDENCE: "MISSING",
    EvidenceKind.STALE_EVIDENCE: "STALE",
}

FRESHNESS_LABELS: dict[FreshnessState, str] = {
    FreshnessState.CURRENT: "CURRENT",
    FreshnessState.AGING: "AGING",
    FreshnessState.STALE: "STALE",
    FreshnessState.TIMELESS: "TIMELESS",
    FreshnessState.UNKNOWN: "FRESHNESS UNKNOWN",
}

READINESS_LABELS: dict[EvidenceReadiness, str] = {
    EvidenceReadiness.READY: "READY FOR MANUAL REVIEW",
    EvidenceReadiness.BLOCKED: "NOT READY FOR MANUAL REVIEW",
}

QUALIFICATION_LABELS: dict[SetupQualification, str] = {
    SetupQualification.QUALIFIED: "Qualification: qualified",
    SetupQualification.NOT_QUALIFIED: "Qualification: not qualified",
    SetupQualification.INDETERMINATE: "Qualification: indeterminate",
    SetupQualification.NOT_EVALUATED: "Qualification: not evaluated",
}

LIFECYCLE_LABELS: dict[SetupLifecycle, str] = {
    SetupLifecycle.OBSERVED: "Lifecycle: observed",
    SetupLifecycle.DEVELOPING: "Lifecycle: developing",
    SetupLifecycle.EVALUATION_COMPLETE: "Lifecycle: evaluation complete",
    SetupLifecycle.INVALIDATED: "Lifecycle: invalidated",
    SetupLifecycle.EXPIRED: "Lifecycle: expired",
}

DISPLAY_STATUS_LABELS: dict[DisplayStatus, str] = {
    DisplayStatus.CURRENT_VIEW: "Current view",
    DisplayStatus.OUTDATED_VIEW: "Outdated view (view-age policy unset)",
    DisplayStatus.SUPERSEDED: "Superseded",
    DisplayStatus.HISTORICAL: "Historical",
}

SELECTOR_AVAILABILITY_LABELS: dict[SelectorAvailability, str] = {
    SelectorAvailability.AVAILABLE: "Available",
    SelectorAvailability.NOT_REQUESTED: "Not requested",
    SelectorAvailability.UNAVAILABLE: "Contract selector unavailable",
}

SELECTOR_OUTCOME_LABELS: dict[SelectorStatus, str] = {
    SelectorStatus.ELIGIBLE: "Selector outcome: eligible",
    SelectorStatus.NO_ELIGIBLE_CONTRACTS: "The selector returned no eligible contracts",
    SelectorStatus.RESEARCH_ONLY: "Selector outcome: research only (not eligible)",
    SelectorStatus.INDETERMINATE: "Selector outcome: indeterminate",
}

VWAP_LANE_TEXT: dict[LaneQualificationAvailability, str] = {
    LaneQualificationAvailability.NOT_AUTHORIZED: (
        "Live qualification not authorized. Research context only."
    ),
    LaneQualificationAvailability.UNAVAILABLE: (
        "Lane evaluation unavailable: no usable lane conclusion exists."
    ),
    LaneQualificationAvailability.EVIDENCE_BLOCKED: (
        "Evidence blocked: the lane conclusion cannot be presented."
    ),
    LaneQualificationAvailability.AVAILABLE: "Lane conclusion available.",
}
TREND_LANE_TEXT = "Not researched. No trend signal exists."
NO_QUALIFIED_SETUP_TEXT = "No qualified setup in this lane (a valid result, not an error)."

ASSISTANT_PLACEHOLDER = (
    "The assistant is not available. Assistant access needs a separate registry grant "
    "(read-only access to the card's evidence bundle) and its own implementation "
    "authorization. Neither exists, so nothing here can answer questions."
)

# Reviewed forbidden wording (IA §8, plus the prototype authorization). Matched
# as whole words, case-insensitively.
FORBIDDEN_TERMS = (
    "buy",
    "sell",
    "trade",
    "trades",
    "enter",
    "exit",
    "signal",
    "will rise",
    "will fall",
    "guaranteed",
    "guarantee",
    "high probability",
    "high-probability",
    "strong buy",
    "recommended contract",
    "recommendation",
    "ready to trade",
)
# Fixed reviewed phrases that negate a forbidden word; allowed verbatim only.
ALLOWED_PHRASES = (TREND_LANE_TEXT,)
_FORBIDDEN = re.compile(
    r"(?<![a-z0-9_])(" + "|".join(re.escape(t) for t in FORBIDDEN_TERMS) + r")(?![a-z0-9_])",
    re.IGNORECASE,
)


def forbidden_terms_in(text: str) -> list[str]:
    """Every forbidden term in ``text`` outside the allowed fixed phrases."""
    for phrase in ALLOWED_PHRASES:
        text = text.replace(phrase, " ")
    return sorted({m.group(1).lower() for m in _FORBIDDEN.finditer(text)})


def humanize(token: str) -> str:
    """A bounded reason token as readable text, with the token kept."""
    return f"{token.replace('_', ' ').capitalize()} ({token})"


def kind_label(kind: EvidenceKind) -> str:
    return KIND_LABELS[kind]


def freshness_label(state: FreshnessState, reason: str, age_seconds: int | None) -> str:
    label = FRESHNESS_LABELS[state]
    if state is FreshnessState.TIMELESS:
        return label
    age = "age unknown" if age_seconds is None else f"age {format_age(age_seconds)}"
    return f"{label} ({age}; {reason})"


def clock_label(reason: ClockHealthReason) -> str:
    if reason is ClockHealthReason.HEALTHY:
        return "Clock healthy"
    if reason is ClockHealthReason.NOT_EVALUATED:
        return "Clock not evaluated"
    return f"Clock: {humanize(reason.value)}"


def format_age(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def format_time(moment: datetime | None) -> str:
    """America/New_York and UTC, both with explicit zone labels."""
    if moment is None:
        return "not recorded"
    eastern = moment.astimezone(_EASTERN)
    zone = eastern.tzname() or "ET"
    return f"{eastern:%Y-%m-%d %H:%M:%S} {zone} ({moment:%Y-%m-%d %H:%M:%S} UTC)"


def short_id(identifier: str) -> str:
    """A readable short form; the full ID is always shown beside it."""
    prefix, _, digest = identifier.partition("_")
    return f"{prefix}_...{digest[-8:]}" if digest else identifier
