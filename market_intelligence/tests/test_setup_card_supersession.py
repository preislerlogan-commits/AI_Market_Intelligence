"""Supersession, immutable history and render-time display status
(docs/SETUP_CARD_CONTRACT.md §5.3, §5.4, §6). Synthetic data only."""

from __future__ import annotations

from datetime import timedelta

import pytest

from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.setup_cards.builder import build_setup_card
from market_intelligence.setup_cards.contracts import SetupCardContent, seal_card
from market_intelligence.setup_cards.enums import (
    CardKind,
    CardRevisionReason,
    DisplayStatus,
    Lane,
    NoSetupReason,
    SetupLifecycle,
    SetupQualification,
)
from market_intelligence.setup_cards.supersession import (
    display_status,
    lifecycle_transition_allowed,
    validate_card_history,
    validate_supersession,
)
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import setup_card_fixtures as s

L = SetupLifecycle
Q = SetupQualification


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _card_at(minutes, lifecycle, qualification, *, prior=None, reason=None, expires_at=None):
    at = f.T0 + timedelta(minutes=minutes)
    bar = f.bar_item(at)
    evaluation = s.evaluation_item(
        lifecycle=lifecycle,
        qualification=qualification,
        at=at,
        parents=[bar],
        expires_at=expires_at,
    )
    ctx = s.card_context(
        bar,
        f.research_item(),
        f.clock_item(at),
        evaluation,
        as_of=at + timedelta(minutes=1),
        recorded_at=at,
    )
    card = build_setup_card(
        ctx,
        card_kind=CardKind.SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=s.PROVENANCE.model_copy(
            update={"generated_at_utc": at + timedelta(minutes=1, seconds=5)}
        ),
        setup_subject_id=s.SETUP_SUBJECT,
        supporting_ids=[bar.item_id],
        prior_card=prior,
        revision_reason=reason,
    )
    return card, ctx


def _reseal(card, **changes):
    fields = {n: getattr(card, n) for n in SetupCardContent.model_fields}
    fields.update(changes)
    return seal_card(SetupCardContent(**fields))


# --- Lifecycle transition table --------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "allowed"),
    [
        (L.OBSERVED, L.DEVELOPING, True),
        (L.OBSERVED, L.EVALUATION_COMPLETE, True),
        (L.DEVELOPING, L.EVALUATION_COMPLETE, True),
        (L.DEVELOPING, L.DEVELOPING, True),
        (L.OBSERVED, L.INVALIDATED, True),
        (L.EVALUATION_COMPLETE, L.EXPIRED, True),
        (L.DEVELOPING, L.OBSERVED, False),
        (L.EVALUATION_COMPLETE, L.DEVELOPING, False),
        (L.EVALUATION_COMPLETE, L.OBSERVED, False),
        (L.INVALIDATED, L.DEVELOPING, False),
        (L.EXPIRED, L.EVALUATION_COMPLETE, False),
        (L.INVALIDATED, L.EXPIRED, False),
        (L.INVALIDATED, L.INVALIDATED, True),
    ],
)
def test_lifecycle_transition_table(old, new, allowed):
    assert lifecycle_transition_allowed(old, new) is allowed


# --- Supersession ----------------------------------------------------------------------------


def test_forward_supersession_through_a_synthetic_setup_lifecycle():
    observed, _ = _card_at(0, L.OBSERVED, Q.NOT_EVALUATED)
    developing, _ = _card_at(
        5, L.DEVELOPING, Q.NOT_EVALUATED, prior=observed, reason=CardRevisionReason.LIFECYCLE_CHANGE
    )
    complete, _ = _card_at(
        10,
        L.EVALUATION_COMPLETE,
        Q.QUALIFIED,
        prior=developing,
        reason=CardRevisionReason.LIFECYCLE_CHANGE,
    )
    expired, _ = _card_at(
        15, L.EXPIRED, Q.QUALIFIED, prior=complete, reason=CardRevisionReason.EXPIRATION
    )
    history = [observed, developing, complete, expired]
    validate_card_history(history)
    assert [c.card_revision.revision_number for c in history] == [1, 2, 3, 4]
    assert expired.card_revision.supersedes_card_id == complete.card_id


def test_backward_lifecycle_is_refused():
    complete, _ = _card_at(0, L.EVALUATION_COMPLETE, Q.QUALIFIED)
    assert (
        _reason(
            _card_at,
            5,
            L.DEVELOPING,
            Q.NOT_EVALUATED,
            prior=complete,
            reason=CardRevisionReason.LIFECYCLE_CHANGE,
        )
        == "lifecycle_transition_forbidden"
    )


def test_terminal_setup_never_returns_to_active():
    complete, _ = _card_at(0, L.EVALUATION_COMPLETE, Q.QUALIFIED)
    invalidated, _ = _card_at(
        5, L.INVALIDATED, Q.QUALIFIED, prior=complete, reason=CardRevisionReason.LIFECYCLE_CHANGE
    )
    assert (
        _reason(
            _card_at,
            10,
            L.EVALUATION_COMPLETE,
            Q.QUALIFIED,
            prior=invalidated,
            reason=CardRevisionReason.LIFECYCLE_CHANGE,
        )
        == "lifecycle_transition_forbidden"
    )


def test_terminal_state_keeps_its_qualification():
    complete, _ = _card_at(0, L.EVALUATION_COMPLETE, Q.QUALIFIED)
    assert (
        _reason(
            _card_at,
            5,
            L.INVALIDATED,
            Q.NOT_QUALIFIED,
            prior=complete,
            reason=CardRevisionReason.LIFECYCLE_CHANGE,
        )
        == "terminal_state_changed_qualification"
    )


def test_lifecycle_change_needs_the_matching_reason():
    observed, _ = _card_at(0, L.OBSERVED, Q.NOT_EVALUATED)
    assert (
        _reason(
            _card_at,
            5,
            L.DEVELOPING,
            Q.NOT_EVALUATED,
            prior=observed,
            reason=CardRevisionReason.NEWER_BUNDLE,
        )
        == "lifecycle_change_reason_mismatch"
    )
    complete, _ = _card_at(0, L.EVALUATION_COMPLETE, Q.QUALIFIED)
    assert (
        _reason(
            _card_at,
            5,
            L.EXPIRED,
            Q.QUALIFIED,
            prior=complete,
            reason=CardRevisionReason.LIFECYCLE_CHANGE,
        )
        == "lifecycle_change_reason_mismatch"
    )


def test_newer_bundle_without_lifecycle_change():
    first, _ = _card_at(0, L.DEVELOPING, Q.NOT_EVALUATED)
    second, _ = _card_at(
        5, L.DEVELOPING, Q.NOT_EVALUATED, prior=first, reason=CardRevisionReason.NEWER_BUNDLE
    )
    validate_card_history([first, second])


def test_superseding_needs_a_reason():
    first, _ = _card_at(0, L.DEVELOPING, Q.NOT_EVALUATED)
    assert _reason(_card_at, 5, L.DEVELOPING, Q.NOT_EVALUATED, prior=first) == (
        "superseding_card_needs_a_reason"
    )


def test_superseding_card_must_share_identity_and_not_be_older():
    first, _ = _card_at(10, L.DEVELOPING, Q.NOT_EVALUATED)
    older, _ = _card_at(0, L.DEVELOPING, Q.NOT_EVALUATED)
    relinked = _reseal(
        older,
        card_revision=older.card_revision.model_copy(
            update={
                "revision_number": 2,
                "supersedes_card_id": first.card_id,
                "revision_reason": CardRevisionReason.NEWER_BUNDLE,
            }
        ),
    )
    assert _reason(validate_supersession, relinked, first) == "superseding_card_is_older"


def test_correction_creates_a_new_card_and_leaves_history_unchanged():
    first, _ = _card_at(0, L.EVALUATION_COMPLETE, Q.QUALIFIED)
    snapshot = first.model_dump_json()
    corrected, _ = _card_at(
        5,
        L.EVALUATION_COMPLETE,
        Q.QUALIFIED,
        prior=first,
        reason=CardRevisionReason.EVIDENCE_CORRECTION,
    )
    assert corrected.card_id != first.card_id
    assert first.model_dump_json() == snapshot
    validate_card_history([first, corrected])


# --- History --------------------------------------------------------------------------------


def test_history_refuses_branches_duplicates_and_missing_predecessors():
    first, _ = _card_at(0, L.DEVELOPING, Q.NOT_EVALUATED)
    a, _ = _card_at(
        5, L.DEVELOPING, Q.NOT_EVALUATED, prior=first, reason=CardRevisionReason.NEWER_BUNDLE
    )
    b, _ = _card_at(
        6, L.DEVELOPING, Q.NOT_EVALUATED, prior=first, reason=CardRevisionReason.NEWER_BUNDLE
    )
    assert _reason(validate_card_history, [first, a, b]) == "card_history_branch"
    assert _reason(validate_card_history, [first, first]) == "card_recorded_twice"
    assert _reason(validate_card_history, [a]) == "predecessor_card_missing"


# --- Display status (render-time) ------------------------------------------------------------


def test_display_status_rules():
    first, _ = _card_at(0, L.DEVELOPING, Q.NOT_EVALUATED)
    second, _ = _card_at(
        5, L.DEVELOPING, Q.NOT_EVALUATED, prior=first, reason=CardRevisionReason.NEWER_BUNDLE
    )
    history = [first, second]
    now = second.effective_at_utc + timedelta(seconds=30)
    assert display_status(first, history=history, now=now, view_max_age_seconds=300) is (
        DisplayStatus.SUPERSEDED
    )
    # A superseded card never becomes current again, even if opened.
    assert (
        display_status(
            first, history=history, now=now, view_max_age_seconds=10_000, opened_as_historical=True
        )
        is DisplayStatus.SUPERSEDED
    )
    assert display_status(second, history=history, now=now, view_max_age_seconds=300) is (
        DisplayStatus.CURRENT_VIEW
    )
    assert display_status(second, history=history, now=now, view_max_age_seconds=10) is (
        DisplayStatus.OUTDATED_VIEW
    )
    # The view policy is unset today: fail closed.
    assert display_status(second, history=history, now=now, view_max_age_seconds=None) is (
        DisplayStatus.OUTDATED_VIEW
    )
    assert (
        display_status(
            second, history=history, now=now, view_max_age_seconds=300, opened_as_historical=True
        )
        is DisplayStatus.HISTORICAL
    )


def test_display_status_is_not_stored_in_the_card():
    card, _ = _card_at(0, L.DEVELOPING, Q.NOT_EVALUATED)
    assert "display_status" not in type(card).model_fields


# --- no_qualified_setup history ------------------------------------------------------------


def test_no_qualified_setup_cards_supersede_by_lane_and_session():
    items = s.core_items()
    ctx = s.card_context(items["bar"], items["research"], items["clock"], s.lane_conclusion())
    first = build_setup_card(
        ctx,
        card_kind=CardKind.NO_QUALIFIED_SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=s.PROVENANCE,
    )
    later = f.T0 + timedelta(minutes=2)
    later_ctx = s.card_context(
        f.bar_item(later),
        items["research"],
        f.clock_item(later),
        s.lane_conclusion(at=later),
        as_of=later + timedelta(minutes=1),
        recorded_at=later,
    )
    second = build_setup_card(
        later_ctx,
        card_kind=CardKind.NO_QUALIFIED_SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=s.PROVENANCE,
        prior_card=first,
        revision_reason=CardRevisionReason.NEWER_BUNDLE,
    )
    validate_card_history([first, second])
    assert second.no_setup_reason is NoSetupReason.CRITERIA_NOT_MET
    setup_card, _ = _card_at(3, L.DEVELOPING, Q.NOT_EVALUATED)
    relinked = _reseal(
        setup_card,
        card_revision=second.card_revision.model_copy(update={"supersedes_card_id": first.card_id}),
    )
    assert _reason(validate_supersession, relinked, first) == "superseding_card_identity_mismatch"
