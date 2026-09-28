"""Bundle-time freshness evaluation and clock-health gating (design §C.4, §F).
Synthetic data only."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence.contracts import EvidenceFreshness
from market_intelligence.evidence.enums import (
    ClockHealthReason,
    EvidenceKind,
    FreshnessReason,
    FreshnessState,
    OffHoursMode,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.freshness import (
    ClockAssessment,
    assess_clock,
    evaluate_freshness,
)
from market_intelligence.evidence.registry import ClockPolicy
from market_intelligence.tests import evidence_fixtures as f

REGISTRY = f.make_registry()
BARS_POLICY = REGISTRY.freshness_policy("fp.bars.v1")
CLOCK_ITEM = f.clock_item(f.T0)
HEALTHY = ClockAssessment(reason=ClockHealthReason.HEALTHY, clock_item_id=CLOCK_ITEM.item_id)
NO_CLOCK = ClockAssessment(reason=ClockHealthReason.NO_CLOCK_FACT, clock_item_id=None)


def _evaluate(item, as_of, *, policy=BARS_POLICY, clock=HEALTHY):
    return evaluate_freshness(item, policy, as_of=as_of, clock=clock)


# --- Boundaries ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("age", "state", "reason"),
    [
        (timedelta(0), FreshnessState.CURRENT, FreshnessReason.WITHIN_CURRENT_WINDOW),
        (timedelta(seconds=300), FreshnessState.CURRENT, FreshnessReason.WITHIN_CURRENT_WINDOW),
        (
            timedelta(seconds=300, microseconds=1),
            FreshnessState.AGING,
            FreshnessReason.WITHIN_AGING_WINDOW,
        ),
        (timedelta(seconds=900), FreshnessState.AGING, FreshnessReason.WITHIN_AGING_WINDOW),
        (
            timedelta(seconds=900, microseconds=1),
            FreshnessState.STALE,
            FreshnessReason.BEYOND_STALE_BOUNDARY,
        ),
    ],
)
def test_exact_boundary_instants(age, state, reason):
    result = _evaluate(f.bar_item(), f.T0 + age)
    assert (result.state, result.reason) == (state, reason)
    assert result.age_seconds == int(age.total_seconds())
    assert result.clock_health_item_id == CLOCK_ITEM.item_id
    assert result.clock_health_reason is ClockHealthReason.HEALTHY


def test_kind_is_static_while_freshness_changes_with_as_of():
    bar = f.bar_item()
    states = [_evaluate(bar, f.T0 + timedelta(seconds=s)).state for s in (60, 600, 1200)]
    assert states == [FreshnessState.CURRENT, FreshnessState.AGING, FreshnessState.STALE]
    assert bar.evidence_kind is EvidenceKind.CONFIRMED_FACT
    assert bar.item_id == f.bar_item().item_id


def test_freshness_is_monotone_non_increasing_in_as_of():
    order = {FreshnessState.CURRENT: 2, FreshnessState.AGING: 1, FreshnessState.STALE: 0}
    bar = f.bar_item()
    previous = 2
    for seconds in range(0, 2000, 7):
        rank = order[_evaluate(bar, f.T0 + timedelta(seconds=seconds)).state]
        assert rank <= previous
        previous = rank


def test_timeless_policy_does_not_evaluate_the_clock():
    result = _evaluate(
        f.research_item(),
        f.T0 + timedelta(days=3650),
        policy=REGISTRY.freshness_policy("fp.research_timeless.v1"),
    )
    assert result.state is FreshnessState.TIMELESS
    assert result.clock_health_item_id is None
    assert result.clock_health_reason is ClockHealthReason.NOT_EVALUATED


def test_unset_policy_parameters_evaluate_to_unknown_without_the_clock():
    unset = BARS_POLICY.model_copy(update={"current_until_seconds": None})
    result = _evaluate(f.bar_item(), f.T0, policy=unset)
    assert result.state is FreshnessState.UNKNOWN
    assert result.reason is FreshnessReason.POLICY_PARAMETERS_UNSET
    assert result.clock_health_item_id is None
    assert result.clock_health_reason is ClockHealthReason.NOT_EVALUATED


def test_freeze_at_session_close_fails_closed_without_a_calendar():
    freeze = BARS_POLICY.model_copy(update={"off_hours_mode": OffHoursMode.FREEZE_AT_SESSION_CLOSE})
    frozen = _evaluate(f.bar_item(), f.T0, policy=freeze)
    assert frozen.state is FreshnessState.UNKNOWN
    assert frozen.reason is FreshnessReason.OFF_HOURS_RULE_APPLIED
    assert frozen.clock_health_item_id == CLOCK_ITEM.item_id


def test_future_observed_time():
    bar = f.bar_item()
    within = _evaluate(bar, f.T0 - timedelta(seconds=5))
    assert within.state is FreshnessState.CURRENT and within.age_seconds is None
    beyond = _evaluate(bar, f.T0 - timedelta(seconds=6))
    assert beyond.state is FreshnessState.UNKNOWN
    assert beyond.reason is FreshnessReason.OBSERVED_TIMESTAMP_IN_FUTURE


def test_policy_must_match_and_apply_to_the_kind():
    with pytest.raises(EvidenceValidationError):
        _evaluate(f.bar_item(), f.T0, policy=REGISTRY.freshness_policy("fp.clock.v1"))
    inference_policy = REGISTRY.freshness_policy("fp.inference.v1")
    wrong = f.bar_item(freshness_policy_id="fp.inference.v1")
    with pytest.raises(EvidenceValidationError):
        _evaluate(wrong, f.T0, policy=inference_policy)


# --- Clock health and clock-ID semantics ------------------------------------------------


def test_missing_clock_fact_is_unknown_with_null_id_and_a_bounded_reason():
    result = _evaluate(f.bar_item(), f.T0, clock=NO_CLOCK)
    assert result.state is FreshnessState.UNKNOWN
    assert result.reason is FreshnessReason.CLOCK_HEALTH_UNKNOWN
    assert result.clock_health_item_id is None
    assert result.clock_health_reason is ClockHealthReason.NO_CLOCK_FACT


def test_unset_clock_policy_is_unknown_with_null_id():
    unset = ClockPolicy(policy_id="clock.unset.v0", clock_producer_id=f.CLOCK_PRODUCER)
    assessment = assess_clock([CLOCK_ITEM], unset, f.T0)
    assert assessment.reason is ClockHealthReason.CLOCK_POLICY_UNSET
    assert assessment.clock_item_id is None
    result = _evaluate(f.bar_item(), f.T0, clock=assessment)
    assert result.clock_health_item_id is None
    assert result.clock_health_reason is ClockHealthReason.CLOCK_POLICY_UNSET


def test_healthy_clock_names_the_evaluated_fact():
    assessment = assess_clock([CLOCK_ITEM], REGISTRY.clock_policy, f.T0 + timedelta(seconds=30))
    assert assessment.reason is ClockHealthReason.HEALTHY
    assert assessment.clock_item_id == CLOCK_ITEM.item_id


@pytest.mark.parametrize(
    ("clock", "as_of", "reason"),
    [
        (f.clock_item(f.T0, offset_ms=5000), f.T0, ClockHealthReason.OFFSET_EXCEEDED),
        (f.clock_item(f.T0, synced=timedelta(hours=2)), f.T0, ClockHealthReason.SYNC_TOO_OLD),
        (f.clock_item(f.T0), f.T0 + timedelta(minutes=11), ClockHealthReason.CLOCK_FACT_TOO_OLD),
    ],
)
def test_an_evaluated_but_failing_clock_fact_is_still_named(clock, as_of, reason):
    assessment = assess_clock([clock], REGISTRY.clock_policy, as_of)
    assert assessment.reason is reason
    assert assessment.clock_item_id == clock.item_id
    result = _evaluate(f.bar_item(as_of), as_of, clock=assessment)
    assert result.state is FreshnessState.UNKNOWN
    assert result.clock_health_item_id == clock.item_id
    assert result.clock_health_reason is reason


def test_unhealthy_clock_maps_to_clock_unhealthy_and_too_old_to_unknown():
    unhealthy = assess_clock([f.clock_item(f.T0, offset_ms=5000)], REGISTRY.clock_policy, f.T0)
    assert _evaluate(f.bar_item(), f.T0, clock=unhealthy).reason is FreshnessReason.CLOCK_UNHEALTHY
    too_old = assess_clock([CLOCK_ITEM], REGISTRY.clock_policy, f.T0 + timedelta(minutes=11))
    result = _evaluate(f.bar_item(f.T0 + timedelta(minutes=11)), f.T0 + timedelta(minutes=11),
                       clock=too_old)
    assert result.reason is FreshnessReason.CLOCK_HEALTH_UNKNOWN


def test_clock_ignores_future_readings_and_other_producers():
    future = f.clock_item(f.T0 + timedelta(minutes=1))
    assert assess_clock([future], REGISTRY.clock_policy, f.T0).reason is (
        ClockHealthReason.NO_CLOCK_FACT
    )
    assert assess_clock([f.bar_item()], REGISTRY.clock_policy, f.T0).reason is (
        ClockHealthReason.NO_CLOCK_FACT
    )


def test_clock_assessment_contract_ties_the_id_to_an_evaluated_fact():
    with pytest.raises(ValidationError):
        ClockAssessment(reason=ClockHealthReason.NO_CLOCK_FACT, clock_item_id=CLOCK_ITEM.item_id)
    with pytest.raises(ValidationError):
        ClockAssessment(reason=ClockHealthReason.HEALTHY, clock_item_id=None)
    with pytest.raises(ValidationError):
        ClockAssessment(reason=ClockHealthReason.NOT_EVALUATED, clock_item_id=None)


def _freshness(**overrides):
    fields = dict(
        policy_id="fp.bars.v1",
        evaluated_at_utc=f.T0,
        source_observed_at_utc=f.T0,
        age_seconds=0,
        state=FreshnessState.CURRENT,
        reason=FreshnessReason.WITHIN_CURRENT_WINDOW,
        clock_health_item_id=CLOCK_ITEM.item_id,
        clock_health_reason=ClockHealthReason.HEALTHY,
    )
    fields.update(overrides)
    return EvidenceFreshness(**fields)


def test_freshness_contract_enforces_clock_semantics():
    assert _freshness()
    with pytest.raises(ValidationError):  # current without a clock fact
        _freshness(clock_health_item_id=None)
    with pytest.raises(ValidationError):  # current with an unknown clock
        _freshness(clock_health_reason=ClockHealthReason.NO_CLOCK_FACT, clock_health_item_id=None)
    with pytest.raises(ValidationError):  # unknown clock but an ID for a fact never evaluated
        _freshness(
            state=FreshnessState.UNKNOWN,
            reason=FreshnessReason.CLOCK_HEALTH_UNKNOWN,
            age_seconds=None,
            clock_health_reason=ClockHealthReason.NO_CLOCK_FACT,
        )
    assert _freshness(
        state=FreshnessState.UNKNOWN,
        reason=FreshnessReason.CLOCK_HEALTH_UNKNOWN,
        age_seconds=None,
        clock_health_reason=ClockHealthReason.CLOCK_FACT_TOO_OLD,
    )


# --- Status items ------------------------------------------------------------------------


def test_status_items_are_evaluated_for_their_own_currency():
    bar = f.bar_item()
    status = f.stale_item(bar, f.T0 + timedelta(minutes=20))
    result = _evaluate(status, f.T0 + timedelta(minutes=21))
    assert result.state is FreshnessState.CURRENT
    assert status.evidence_kind is EvidenceKind.STALE_EVIDENCE


# --- Ambiguous clock facts --------------------------------------------------------------


def test_identical_clock_retries_count_as_one_fact():
    retry = f.clock_item(f.T0)
    assert retry.item_id == CLOCK_ITEM.item_id
    assessment = assess_clock([CLOCK_ITEM, retry], REGISTRY.clock_policy, f.T0)
    assert assessment.reason is ClockHealthReason.HEALTHY
    assert assessment.clock_item_id == CLOCK_ITEM.item_id
    assert assessment.competing_clock_item_ids == []


def test_one_unique_latest_clock_fact_is_evaluated_normally():
    assessment = assess_clock([CLOCK_ITEM], REGISTRY.clock_policy, f.T0)
    assert assessment.reason is ClockHealthReason.HEALTHY
    assert assessment.competing_clock_item_ids == []


def test_two_distinct_facts_at_the_latest_time_are_ambiguous():
    other = f.clock_item(f.T0, offset_ms=7)
    assessment = assess_clock([CLOCK_ITEM, other], REGISTRY.clock_policy, f.T0)
    assert assessment.reason is ClockHealthReason.AMBIGUOUS_CLOCK_FACTS
    assert assessment.clock_item_id is None
    assert assessment.competing_clock_item_ids == sorted([CLOCK_ITEM.item_id, other.item_id])


def test_conflicting_healthy_and_unhealthy_readings_are_ambiguous():
    unhealthy = f.clock_item(f.T0, offset_ms=5000)
    assessment = assess_clock([CLOCK_ITEM, unhealthy], REGISTRY.clock_policy, f.T0)
    assert assessment.reason is ClockHealthReason.AMBIGUOUS_CLOCK_FACTS
    assert assessment.clock_item_id is None


def test_ambiguous_clock_makes_freshness_unknown_without_an_accepted_fact():
    other = f.clock_item(f.T0, offset_ms=7)
    assessment = assess_clock([CLOCK_ITEM, other], REGISTRY.clock_policy, f.T0)
    result = _evaluate(f.bar_item(), f.T0, clock=assessment)
    assert result.state is FreshnessState.UNKNOWN
    assert result.reason is FreshnessReason.CLOCK_HEALTH_UNKNOWN
    assert result.clock_health_item_id is None
    assert result.clock_health_reason is ClockHealthReason.AMBIGUOUS_CLOCK_FACTS
    assert result.competing_clock_item_ids == sorted([CLOCK_ITEM.item_id, other.item_id])


def test_reversed_input_order_gives_identical_clock_results():
    other = f.clock_item(f.T0, offset_ms=7)
    forward = assess_clock([CLOCK_ITEM, other], REGISTRY.clock_policy, f.T0)
    backward = assess_clock([other, CLOCK_ITEM], REGISTRY.clock_policy, f.T0)
    assert forward == backward


def test_older_facts_do_not_cause_ambiguity_when_one_is_uniquely_newer():
    older_a = f.clock_item(f.T0 - timedelta(minutes=2), offset_ms=7)
    older_b = f.clock_item(f.T0 - timedelta(minutes=2), offset_ms=9)
    newer = f.clock_item(f.T0 - timedelta(minutes=1))
    assessment = assess_clock([older_a, older_b, newer], REGISTRY.clock_policy, f.T0)
    assert assessment.reason is ClockHealthReason.HEALTHY
    assert assessment.clock_item_id == newer.item_id


def test_clock_contracts_require_competing_ids_only_for_ambiguity():
    with pytest.raises(ValidationError):
        ClockAssessment(reason=ClockHealthReason.AMBIGUOUS_CLOCK_FACTS, clock_item_id=None)
    with pytest.raises(ValidationError):
        ClockAssessment(
            reason=ClockHealthReason.AMBIGUOUS_CLOCK_FACTS,
            clock_item_id=CLOCK_ITEM.item_id,
            competing_clock_item_ids=sorted(
                [CLOCK_ITEM.item_id, f.clock_item(f.T0, offset_ms=7).item_id]
            ),
        )
    with pytest.raises(ValidationError):
        ClockAssessment(
            reason=ClockHealthReason.HEALTHY,
            clock_item_id=CLOCK_ITEM.item_id,
            competing_clock_item_ids=[CLOCK_ITEM.item_id],
        )
