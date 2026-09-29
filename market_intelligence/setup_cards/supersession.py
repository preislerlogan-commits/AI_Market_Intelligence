"""Card supersession, immutable history and render-time display status
(docs/SETUP_CARD_CONTRACT.md §5.3, §5.4, §6).

Cards never change. A later bundle, correction, lifecycle change or
expiration produces a new card that names the card it supersedes. History is
append-only: branches are refused, and a terminal setup never returns to an
active state.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.setup_cards.contracts import SetupCard
from market_intelligence.setup_cards.enums import (
    LIFECYCLE_ORDER,
    TERMINAL_LIFECYCLES,
    CardKind,
    CardRevisionReason,
    DisplayStatus,
    SetupLifecycle,
)


def _fail(reason: str) -> EvidenceValidationError:
    return EvidenceValidationError(reason)


def card_identity_key(card: SetupCard) -> tuple[str, ...]:
    """What a superseding card must share with its predecessor."""
    if card.card_kind is CardKind.SETUP:
        return ("setup", card.lane.value, card.setup_subject_id or "")
    return ("no_qualified_setup", card.lane.value, card.session_date.isoformat())


def lifecycle_transition_allowed(old: SetupLifecycle, new: SetupLifecycle) -> bool:
    if old in TERMINAL_LIFECYCLES:
        # Terminal: never back to an active state (a corrected terminal card
        # may restate the same terminal state).
        return new is old
    if new in TERMINAL_LIFECYCLES:
        return True
    # Active states only move forward; a completed evaluation never reopens.
    return LIFECYCLE_ORDER[new] >= LIFECYCLE_ORDER[old]


def validate_supersession(new: SetupCard, prior: SetupCard) -> None:
    revision = new.card_revision
    if revision.supersedes_card_id != prior.card_id:
        raise _fail("supersedes_wrong_card")
    if revision.revision_number != prior.card_revision.revision_number + 1:
        raise _fail("card_revision_number_mismatch")
    if card_identity_key(new) != card_identity_key(prior):
        raise _fail("superseding_card_identity_mismatch")
    if new.effective_at_utc < prior.effective_at_utc:
        raise _fail("superseding_card_is_older")
    if new.card_kind is CardKind.SETUP:
        old_state, new_state = prior.lifecycle_state, new.lifecycle_state
        if old_state is None or new_state is None:  # pragma: no cover - contract guarantees
            raise _fail("setup_lifecycle_missing")
        if not lifecycle_transition_allowed(old_state, new_state):
            raise _fail("lifecycle_transition_forbidden")
        if new_state in TERMINAL_LIFECYCLES and new.qualification is not prior.qualification:
            raise _fail("terminal_state_changed_qualification")
        if new_state is not old_state:
            expected = (
                CardRevisionReason.EXPIRATION
                if new_state is SetupLifecycle.EXPIRED
                else CardRevisionReason.LIFECYCLE_CHANGE
            )
            if revision.revision_reason is not expected:
                raise _fail("lifecycle_change_reason_mismatch")


def validate_card_history(cards: Sequence[SetupCard]) -> None:
    """Append-only history: unique cards, every predecessor present, no
    branches, and every link a valid supersession."""
    by_id: dict[str, SetupCard] = {}
    for card in cards:
        if card.card_id in by_id:
            raise _fail("card_recorded_twice")
        by_id[card.card_id] = card
    successors: dict[str, str] = {}
    for card in cards:
        prior_id = card.card_revision.supersedes_card_id
        if prior_id is None:
            continue
        if prior_id not in by_id:
            raise _fail("predecessor_card_missing")
        if prior_id in successors:
            raise _fail("card_history_branch")
        successors[prior_id] = card.card_id
        validate_supersession(card, by_id[prior_id])


def display_status(
    card: SetupCard,
    *,
    history: Sequence[SetupCard],
    now: datetime,
    view_max_age_seconds: int | None,
    opened_as_historical: bool = False,
) -> DisplayStatus:
    """Render-time only; never stored in the card.

    A superseded card never becomes current again. The view policy is an open
    decision; while it is unset (``None``) no card can be confirmed current,
    so the result fails closed to ``outdated_view``.
    """
    if any(c.card_revision.supersedes_card_id == card.card_id for c in history):
        return DisplayStatus.SUPERSEDED
    if opened_as_historical:
        return DisplayStatus.HISTORICAL
    if view_max_age_seconds is None:
        return DisplayStatus.OUTDATED_VIEW
    if now - card.effective_at_utc > timedelta(seconds=view_max_age_seconds):
        return DisplayStatus.OUTDATED_VIEW
    return DisplayStatus.CURRENT_VIEW
