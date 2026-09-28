"""Bundle-time freshness evaluation (design §F.4) and clock-health gating.

Freshness is never stored in an item. Only the bundle builder calls these
functions. Order of evaluation, for every kind:

1. timeless policy -> ``timeless``;
2. unset policy parameters -> ``unknown``;
3. missing or unhealthy clock -> ``unknown``;
4. missing or future observed time -> ``unknown``;
5. age against the policy boundaries -> ``current`` / ``aging`` / ``stale``.

Clock semantics: ``clock_health_item_id`` names a clock fact only when a
particular fact was evaluated (healthy, unhealthy, too old, or unreadable).
Otherwise it is null, and the bounded ``clock_health_reason`` records why
(no clock fact, clock policy unset, ambiguous clock facts, or clock not
evaluated because the freshness policy is timeless or unset).

Clock facts are chosen by substantive effective time only. Identical retries
share one content-addressed ID and count once. If several distinct facts
share the latest effective time, no reading is accepted: the result is
``ambiguous_clock_facts`` with the competing IDs sorted for audit (the ID
never selects a reading), and freshness becomes ``unknown``.

``freeze_at_session_close`` needs an exchange calendar, which does not exist
(design §Q.4); such policies fail closed to ``unknown``
(``off_hours_rule_applied``). ``unknown`` never counts as ready.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Annotated

from pydantic import BaseModel, Field, ValidationError, model_validator

from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.contracts import EvidenceFreshness, EvidenceItem
from market_intelligence.evidence.enums import (
    CLOCK_FACT_EVALUATED,
    CLOCK_UNHEALTHY_REASONS,
    ClockHealthReason,
    EvidenceKind,
    FreshnessReason,
    FreshnessState,
    OffHoursMode,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.payloads import CLOCK_HEALTH_PAYLOAD, ClockHealthFactPayload
from market_intelligence.evidence.primitives import STRICT_FROZEN, ItemId
from market_intelligence.evidence.registry import ClockPolicy, FreshnessPolicy


class ClockAssessment(BaseModel):
    """The clock-health outcome at one bundle as-of."""

    model_config = STRICT_FROZEN

    reason: ClockHealthReason
    clock_item_id: ItemId | None
    competing_clock_item_ids: Annotated[list[ItemId], Field(max_length=32)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _check_assessment(self) -> ClockAssessment:
        if self.reason is ClockHealthReason.NOT_EVALUATED:
            raise ValueError("a clock assessment always evaluates the clock policy")
        if (self.reason in CLOCK_FACT_EVALUATED) != (self.clock_item_id is not None):
            raise ValueError("clock_item_id is present iff a clock fact was evaluated")
        ambiguous = self.reason is ClockHealthReason.AMBIGUOUS_CLOCK_FACTS
        ids = self.competing_clock_item_ids
        if ambiguous != (len(ids) >= 2) or (not ambiguous and ids):
            raise ValueError("competing clock IDs are listed iff clock facts are ambiguous")
        if ids != sorted(set(ids)):
            raise ValueError("competing clock IDs must be sorted and unique")
        return self

    @property
    def healthy(self) -> bool:
        return self.reason is ClockHealthReason.HEALTHY

    @property
    def unhealthy(self) -> bool:
        return self.reason in CLOCK_UNHEALTHY_REASONS


def assess_clock(
    clock_items: Iterable[EvidenceItem], policy: ClockPolicy, as_of: datetime
) -> ClockAssessment:
    """Evaluate the latest clock-health fact measured at or before ``as_of``."""
    if not policy.parameters_set:
        return ClockAssessment(reason=ClockHealthReason.CLOCK_POLICY_UNSET, clock_item_id=None)
    candidates = [
        item
        for item in clock_items
        if item.provenance.producer_id == policy.clock_producer_id
        and item.evidence_kind is EvidenceKind.CONFIRMED_FACT
        and item.payload_schema_id == CLOCK_HEALTH_PAYLOAD
        and item.effective_at_utc <= as_of
    ]
    if not candidates:
        return ClockAssessment(reason=ClockHealthReason.NO_CLOCK_FACT, clock_item_id=None)
    latest_at = max(item.effective_at_utc for item in candidates)
    # Identical retries share one content-addressed ID, so they count once.
    tied = {item.item_id: item for item in candidates if item.effective_at_utc == latest_at}
    if len(tied) > 1:
        return ClockAssessment(
            reason=ClockHealthReason.AMBIGUOUS_CLOCK_FACTS,
            clock_item_id=None,
            competing_clock_item_ids=sorted(tied),
        )
    (latest,) = tied.values()
    max_age = policy.max_measurement_age_seconds
    max_offset = policy.max_abs_offset_ms
    max_sync = policy.max_sync_age_seconds
    if max_age is None or max_offset is None or max_sync is None:  # pragma: no cover
        return ClockAssessment(reason=ClockHealthReason.CLOCK_POLICY_UNSET, clock_item_id=None)

    def outcome(reason: ClockHealthReason) -> ClockAssessment:
        return ClockAssessment(reason=reason, clock_item_id=latest.item_id)

    if as_of - latest.effective_at_utc > timedelta(seconds=max_age):
        return outcome(ClockHealthReason.CLOCK_FACT_TOO_OLD)
    try:
        reading = ClockHealthFactPayload.model_validate_json(canonical_json_bytes(latest.payload))
    except ValidationError:
        return outcome(ClockHealthReason.CLOCK_FACT_UNREADABLE)
    if abs(reading.offset_ms) > max_offset:
        return outcome(ClockHealthReason.OFFSET_EXCEEDED)
    sync_age = latest.effective_at_utc - reading.last_sync_at_utc
    if reading.last_sync_at_utc > latest.effective_at_utc or sync_age > timedelta(
        seconds=max_sync
    ):
        return outcome(ClockHealthReason.SYNC_TOO_OLD)
    return outcome(ClockHealthReason.HEALTHY)


def evaluate_freshness(
    item: EvidenceItem,
    policy: FreshnessPolicy,
    *,
    as_of: datetime,
    clock: ClockAssessment,
) -> EvidenceFreshness:
    if policy.policy_id != item.freshness_policy_id:
        raise EvidenceValidationError("freshness_policy_mismatch")
    if item.evidence_kind not in policy.applicable_kinds:
        raise EvidenceValidationError("freshness_policy_not_applicable")
    observed = item.provenance.source_observed_at_utc

    def result(
        state: FreshnessState,
        reason: FreshnessReason,
        *,
        evaluated_clock: bool = True,
        age: int | None = None,
    ) -> EvidenceFreshness:
        return EvidenceFreshness(
            policy_id=policy.policy_id,
            evaluated_at_utc=as_of,
            source_observed_at_utc=observed,
            age_seconds=age,
            state=state,
            reason=reason,
            clock_health_item_id=clock.clock_item_id if evaluated_clock else None,
            clock_health_reason=(
                clock.reason if evaluated_clock else ClockHealthReason.NOT_EVALUATED
            ),
            competing_clock_item_ids=(
                list(clock.competing_clock_item_ids) if evaluated_clock else []
            ),
        )

    if policy.timeless:
        return result(
            FreshnessState.TIMELESS, FreshnessReason.TIMELESS_BY_POLICY, evaluated_clock=False
        )
    if not policy.parameters_set:
        return result(
            FreshnessState.UNKNOWN, FreshnessReason.POLICY_PARAMETERS_UNSET, evaluated_clock=False
        )
    if clock.unhealthy:
        return result(FreshnessState.UNKNOWN, FreshnessReason.CLOCK_UNHEALTHY)
    if not clock.healthy:
        return result(FreshnessState.UNKNOWN, FreshnessReason.CLOCK_HEALTH_UNKNOWN)
    if observed is None:
        return result(FreshnessState.UNKNOWN, FreshnessReason.OBSERVED_TIMESTAMP_MISSING)
    tolerance = policy.future_tolerance_seconds
    current_until = policy.current_until_seconds
    stale_after = policy.stale_after_seconds
    if tolerance is None or current_until is None or stale_after is None:  # pragma: no cover
        return result(
            FreshnessState.UNKNOWN, FreshnessReason.POLICY_PARAMETERS_UNSET, evaluated_clock=False
        )
    if observed > as_of + timedelta(seconds=tolerance):
        return result(FreshnessState.UNKNOWN, FreshnessReason.OBSERVED_TIMESTAMP_IN_FUTURE)
    if policy.off_hours_mode is not OffHoursMode.ELAPSED_WALL_CLOCK:
        return result(FreshnessState.UNKNOWN, FreshnessReason.OFF_HOURS_RULE_APPLIED)
    if observed > as_of:
        # Within the future tolerance: folded into the current window, with
        # no age (design §B.7).
        return result(FreshnessState.CURRENT, FreshnessReason.WITHIN_CURRENT_WINDOW)
    age = as_of - observed
    age_seconds = int(age.total_seconds())
    if age <= timedelta(seconds=current_until):
        return result(
            FreshnessState.CURRENT, FreshnessReason.WITHIN_CURRENT_WINDOW, age=age_seconds
        )
    if age <= timedelta(seconds=stale_after):
        return result(FreshnessState.AGING, FreshnessReason.WITHIN_AGING_WINDOW, age=age_seconds)
    return result(FreshnessState.STALE, FreshnessReason.BEYOND_STALE_BOUNDARY, age=age_seconds)
