"""Navigation state for the offline dashboard prototype (IA §2).

An immutable value: the page plus the scenario, card and bundle the user is
looking at. The bundle travels with the user, so moving between Setup Detail
and Contract Review never swaps to a different card or bundle, and a page
never silently picks a newer one. Pure Python; no UI framework.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from market_intelligence.dashboard.views import DashboardModel


class Page(StrEnum):
    MARKET_OVERVIEW = "market_overview"
    PREMARKET_BRIEF = "premarket_brief"
    LIVE_SCANNER = "live_scanner"
    SETUP_DETAIL = "setup_detail"
    CONTRACT_REVIEW = "contract_review"
    RESEARCH_STATUS = "research_status"
    ASSISTANT = "assistant"


PAGE_TITLES: dict[Page, str] = {
    Page.MARKET_OVERVIEW: "Market Overview",
    Page.PREMARKET_BRIEF: "Pre-Market Brief",
    Page.LIVE_SCANNER: "Live Scanner",
    Page.SETUP_DETAIL: "Setup Detail",
    Page.CONTRACT_REVIEW: "Contract Review",
    Page.RESEARCH_STATUS: "Research and System Status",
    Page.ASSISTANT: "Assistant (not available)",
}
# Reached from context (one card), never browsed blindly.
CONTEXT_PAGES = frozenset({Page.SETUP_DETAIL, Page.CONTRACT_REVIEW})


class NavigationError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class NavigationState:
    scenario_id: str
    page: Page = Page.MARKET_OVERVIEW
    card_id: str | None = None
    bundle_id: str | None = None

    def __post_init__(self) -> None:
        if (self.card_id is None) != (self.bundle_id is None):
            raise NavigationError("card_and_bundle_travel_together")


def go_to(state: NavigationState, page: Page) -> NavigationState:
    """Change page, keeping the selected card and its bundle."""
    return replace(state, page=page)


def open_card(state: NavigationState, card_id: str, bundle_id: str) -> NavigationState:
    """Open one card's Setup Detail, carrying the card's own bundle."""
    return replace(state, page=Page.SETUP_DETAIL, card_id=card_id, bundle_id=bundle_id)


def open_contract_review(state: NavigationState) -> NavigationState:
    """Contract Review for the card already open: same card, same bundle."""
    if state.card_id is None:
        raise NavigationError("no_card_selected")
    return replace(state, page=Page.CONTRACT_REVIEW)


def switch_scenario(state: NavigationState, scenario_id: str) -> NavigationState:
    """A different synthetic scenario starts fresh: no card carries over."""
    if scenario_id == state.scenario_id:
        return state
    return NavigationState(scenario_id=scenario_id, page=state.page)


def selected_card(model: DashboardModel, state: NavigationState) -> str | None:
    """The card the state names, checked against its bundle. Never swapped
    for another card or a newer bundle; a mismatch is refused."""
    if state.card_id is None:
        return None
    detail = model.cards.get(state.card_id)
    if detail is None:
        raise NavigationError("card_not_in_scenario")
    if detail.summary.bundle_id != state.bundle_id:
        raise NavigationError("card_bundle_mismatch")
    return state.card_id
