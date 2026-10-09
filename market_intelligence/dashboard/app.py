"""Streamlit rendering for the offline, synthetic dashboard prototype.

Run locally with ``streamlit run market_intelligence/dashboard/app.py``.

This module only lays out precomputed views. Fixtures, validation, view
derivation, trust labels and navigation live in their own framework-free
modules. Every page renders from committed synthetic scenarios: no database,
provider, model or network is touched, nothing is persisted, and there is no
order, quantity, sizing, brokerage or execution control anywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import pandas as pd
import streamlit as st

from market_intelligence.dashboard import labels as L
from market_intelligence.dashboard.adapter import present
from market_intelligence.dashboard.fixtures import SCENARIO_ORDER, build_scenarios
from market_intelligence.dashboard.navigation import (
    PAGE_TITLES,
    NavigationError,
    NavigationState,
    Page,
    go_to,
    open_card,
    open_contract_review,
    selected_card,
    switch_scenario,
)
from market_intelligence.dashboard.validation import DashboardInputError
from market_intelligence.dashboard.views import (
    BundleView,
    ConflictView,
    DashboardModel,
    EvidenceRow,
    LaneView,
    MissingView,
    StatusStrip,
)
from market_intelligence.evidence.enums import EvidenceKind
from market_intelligence.setup_cards.enums import SelectorAvailability

PAGES = [p.value for p in Page]


@st.cache_resource
def _models() -> dict[str, DashboardModel | str]:
    """Each scenario's validated model, or its sanitized failure token."""
    models: dict[str, DashboardModel | str] = {}
    for scenario_id, scenario in build_scenarios().items():
        try:
            models[scenario_id] = present(scenario)
        except DashboardInputError as error:
            models[scenario_id] = error.reason
    return models


ScenarioSource = Callable[
    [], tuple[Sequence[str], Mapping[str, str], Mapping[str, DashboardModel | str]]
]


def _prototype_source() -> tuple[Sequence[str], Mapping[str, str], Mapping[str, Any]]:
    """The prototype's own committed synthetic scenarios (the default)."""
    scenarios = build_scenarios()
    return SCENARIO_ORDER, {sid: s.title for sid, s in scenarios.items()}, _models()


# --- Shared pieces ------------------------------------------------------------------------------


def _rows_table(rows: Sequence[EvidenceRow]) -> pd.DataFrame:
    """Kind and freshness columns come before any value (IA §5.1)."""
    return pd.DataFrame(
        [
            {
                "Kind": r.kind_label,
                "Freshness": r.freshness,
                "Required": "required" if r.required else "",
                "Producer": r.producer_id,
                "Summary": r.summary,
                "Effective": r.effective_at,
                "Conflict": "CONFLICT" if r.conflict_ids else "",
                "Item": r.item_id,
            }
            for r in rows
        ],
        columns=[
            "Kind",
            "Freshness",
            "Required",
            "Producer",
            "Summary",
            "Effective",
            "Conflict",
            "Item",
        ],
    )


def _evidence(title: str, rows: Sequence[EvidenceRow], empty: str) -> None:
    st.markdown(f"**{title}**")
    if rows:
        st.dataframe(_rows_table(rows), hide_index=True)
    else:
        st.caption(empty)


def _strip(strip: StatusStrip) -> None:
    with st.container(border=True):
        st.markdown(f"**Status** for bundle `{strip.bundle_short_id}` ({strip.purpose})")
        cols = st.columns(4)
        cols[0].markdown(f"**As of**  \n{strip.as_of}")
        cols[1].markdown(f"**Clock**  \n{strip.clock}")
        cols[2].markdown(
            f"**Evidence readiness**  \n`{strip.readiness_label}`  \n"
            f"{len(strip.blockers)} blocking reason(s)"
        )
        cols[3].markdown(
            f"**Missing / not current**  \n{strip.missing_count} missing, "
            f"{strip.non_current_count} not current  \n"
            f"**Conflicts**  \n{strip.material_conflicts} material, "
            f"{strip.critical_conflicts} critical"
        )
        st.caption(
            f"Registry `{strip.registry_short_id}`. Full bundle ID: `{strip.bundle_id}`. "
            f"{strip.scope}"
        )
        if strip.holdout_notice:
            st.warning(f"HOLDOUT GUARD: {strip.holdout_notice}")


def _blocking(lines: Sequence[str]) -> None:
    if lines:
        st.warning("**Blocking states**\n\n" + "\n".join(f"- {line}" for line in lines))


def _missing(missing: Sequence[MissingView]) -> None:
    st.markdown("**What is missing**")
    if not missing:
        st.caption("No required evidence is missing.")
    for m in missing:
        restricted = " Restricted until the holdout is recorded." if m.holdout_restricted else ""
        st.markdown(
            f"- `MISSING` requirement `{m.requirement_id}` ({m.producer_id}): "
            f"{m.reason}.{restricted}"
        )


def _conflicts(conflicts: Sequence[ConflictView]) -> None:
    st.markdown("**Unresolved conflicts (both sides shown, no winner)**")
    if not conflicts:
        st.caption("No conflict touches this evidence.")
    for c in conflicts:
        chip = "CRITICAL CONFLICT" if c.severity == "critical" else "CONFLICT"
        st.markdown(f"`{chip}` {c.conflict_type}, severity {c.severity}, status {c.status}")
        st.dataframe(_rows_table(c.sides), hide_index=True)


def _research_notes(notes) -> None:
    with st.container(border=True):
        st.markdown("`RESEARCH` **Historical research (not live state)**")
        if not notes:
            st.caption("No research result is in this bundle.")
        for n in notes:
            st.markdown(
                f"Study `{n.study_id}` ({n.result_kind}): label `{n.primary_label}`; "
                f"research status `{n.research_status}`; holdout `{n.holdout_state}`."
            )
            st.caption("Fixed caveats: " + ", ".join(n.caveats))


def _manual_decision(text: str) -> None:
    st.info(f"MANUAL DECISION REQUIRED. {text}")


# --- Pages ------------------------------------------------------------------------------------


def _market_overview(model: DashboardModel) -> None:
    view: BundleView = model.market
    _strip(view.strip)
    _blocking(view.blocking)
    _evidence(
        "Current SPY state: facts and calculations",
        view.rows_of(EvidenceKind.CONFIRMED_FACT, EvidenceKind.DETERMINISTIC_CALCULATION),
        "No fact or calculation is in this bundle.",
    )
    st.caption(
        "No regime calculation is in this synthetic bundle. A regime label is a calculation, "
        "never a qualified setup or a directional call."
    )
    _evidence(
        "INFERENCE: context (labelled, non-directional; never supporting evidence)",
        view.rows_of(EvidenceKind.CURRENT_INFERENCE),
        "No inference is in this bundle.",
    )
    _conflicts(view.conflicts)
    _missing(view.missing)
    for a in view.ambiguous:
        st.markdown(
            f"- `AMBIGUOUS` requirement `{a.requirement_id}`: {a.reason}. No winner is chosen; "
            f"competing items: {', '.join(a.competing_item_ids)}"
        )
    _evidence("What is not current", view.non_current, "Every entry is current or timeless.")


def _premarket(model: DashboardModel) -> None:
    view = model.premarket
    _strip(view.strip)
    _blocking(view.blocking)
    _evidence(
        "Prior session: facts and calculations",
        view.rows_of(EvidenceKind.CONFIRMED_FACT, EvidenceKind.DETERMINISTIC_CALCULATION),
        "No prior-session evidence is in this bundle.",
    )
    st.markdown("**Overnight and scheduled context**")
    st.caption("No approved catalyst source.")
    _evidence(
        "INFERENCE: overnight context (labelled, non-directional)",
        view.rows_of(EvidenceKind.CURRENT_INFERENCE),
        "No inference is in this bundle.",
    )
    st.markdown("**Conditional scenarios**")
    st.caption("Scenario analysis not authorized.")
    _missing(view.missing)
    research = model.system.research if model.system else ()
    _research_notes(research)


def _lane(lane: LaneView) -> None:
    st.markdown(f"**{lane.title}**")
    if lane.availability is not None:
        state = f" (`{lane.availability.value}`)"
    else:
        state = ""
    st.info(f"{lane.text}{state}")
    for blocker in lane.blockers:
        st.markdown(f"- {blocker}")
    for card in lane.cards:
        with st.container(border=True):
            st.markdown(f"`SYNTHETIC` card `{card.short_id}` ({card.card_kind.value})")
            if card.no_setup_text:
                st.markdown(card.no_setup_text)
            if card.lifecycle_label:
                st.markdown(card.lifecycle_label)
            st.markdown(card.qualification_label)
            st.markdown(f"Evidence readiness: `{card.readiness_label}`")
            st.markdown(f"Display: {card.display_status_label}; as of {card.as_of}")
            st.button(
                "Open setup detail",
                key=f"open-{card.card_id}",
                on_click=_open_card,
                args=(card.card_id, card.bundle_id),
            )


def _scanner(model: DashboardModel) -> None:
    _strip(model.market.strip)
    st.caption("Lanes are separate and never substitute for each other. Nothing is ranked.")
    vwap, trend = st.tabs(["VWAP reversion", "Trend continuation"])
    with vwap:
        _lane(model.vwap_lane)
    with trend:
        _lane(model.trend_lane)


def _setup_detail(model: DashboardModel, nav: NavigationState) -> None:
    card_id = selected_card(model, nav)
    if card_id is None:
        st.info("Setup Detail opens for one card. Open a card from the Live Scanner.")
        return
    detail = model.cards[card_id]
    s = detail.summary
    _strip(detail.strip)
    st.markdown(f"### `SYNTHETIC` card `{s.short_id}`")
    st.caption(L.SYNTHETIC_DEFINITION_NOTE)
    cols = st.columns(3)
    cols[0].markdown(f"Lane: `{s.lane.value}`  \nKind: `{s.card_kind.value}`")
    cols[1].markdown(
        f"{s.lifecycle_label or 'Lifecycle: none (lane-level)'}  \n{s.qualification_label}"
    )
    cols[2].markdown(
        f"Evidence readiness: `{s.readiness_label}`  \nDisplay: {s.display_status_label}"
    )
    if s.no_setup_text:
        st.markdown(s.no_setup_text)
    if detail.newer_card_id:
        st.warning("Newer evidence available: this card is superseded by a later revision.")
        newer = model.cards[detail.newer_card_id]
        st.button(
            "Open the newer revision",
            on_click=_open_card,
            args=(newer.summary.card_id, newer.summary.bundle_id),
        )
    _manual_decision(detail.manual_decision)
    _blocking(detail.blocking)
    _evidence("Supporting evidence", detail.supporting, "No supporting evidence is cited.")
    _evidence("Contradicting evidence", detail.contradicting, "No contradicting evidence is cited.")
    _evidence("Context", detail.context, "No other context is cited.")
    _evidence(
        "INFERENCE: labelled context only (never supporting or contradicting evidence)",
        detail.inference_context,
        "No inference is cited.",
    )
    _missing(detail.missing)
    st.markdown("**Stale, aging or unknown-freshness entries**")
    if not detail.non_current:
        st.caption("Every cited and required entry is current or timeless.")
    for n in detail.non_current:
        need = "required" if n.required else "cited"
        st.markdown(f"- `{n.state}` {need} item `{n.item_id}`: {n.reason}")
    _research_notes(detail.research)
    _conflicts(detail.conflicts)
    with st.expander("Timing and provenance"):
        st.dataframe(pd.DataFrame(detail.provenance, columns=["Field", "Value"]), hide_index=True)
    st.markdown("**Card history (append-only)**")
    for h in detail.history:
        st.markdown(f"- Revision {h.revision_number}: `{h.short_id}`, {h.display_status_label}")
        if h.card_id != s.card_id:
            st.button(
                f"Open revision {h.revision_number}",
                key=f"rev-{h.card_id}",
                on_click=_open_card,
                args=(h.card_id, h.bundle_id),
            )
    if detail.selector_availability is SelectorAvailability.NOT_REQUESTED:
        st.caption("The contract selector was not requested for this card.")
    else:
        st.button("Open contract review", on_click=_open_contract_review)


def _contract_review(model: DashboardModel, nav: NavigationState) -> None:
    card_id = selected_card(model, nav)
    if card_id is None:
        st.info("Contract Review opens for one card's selector result. Open a card first.")
        return
    review = model.contract_reviews[card_id]
    _strip(model.cards[card_id].strip)
    st.markdown(f"Selector result for card `{L.short_id(review.card_id)}`")
    st.markdown(f"**Selector availability:** {review.availability_label}")
    _manual_decision(review.manual_decision)
    if review.availability is not SelectorAvailability.AVAILABLE:
        if review.availability is SelectorAvailability.UNAVAILABLE:
            st.warning("Contract selector unavailable. No valid selector result exists.")
        else:
            st.caption("The contract selector was not requested for this card.")
        st.button("Back to setup detail", on_click=_go, args=(Page.SETUP_DETAIL,))
        return
    st.markdown(
        f"{review.outcome_label}. Feed `{review.feed}`. Selector as of {review.selector_as_of}; "
        f"freshness {review.selector_freshness}."
    )
    st.markdown("**Eligible contracts (as returned by the selector)**")
    if review.eligible:
        st.dataframe(pd.DataFrame({"Eligible contract": review.eligible}), hide_index=True)
    else:
        st.caption("None returned in the eligible set.")
    with st.container(border=True):
        st.markdown("**Research only, not eligible**")
        if review.indicative_warning:
            st.markdown("`INDICATIVE` feed: research only, never operationally eligible.")
        if review.research_only:
            st.dataframe(
                pd.DataFrame({"Research-only contract": review.research_only}), hide_index=True
            )
        else:
            st.caption("None returned in the research-only set.")
    st.markdown("**Rejection summary (counts only)**")
    nonzero = [(reason, count) for reason, count in review.rejection_counts if count]
    if nonzero:
        st.dataframe(pd.DataFrame(nonzero, columns=["Reason", "Count"]), hide_index=True)
    else:
        st.caption("No contract was rejected.")
    st.caption("Reasons with zero rejections are omitted. Rejected contracts are never listed.")
    st.button("Back to setup detail", on_click=_go, args=(Page.SETUP_DETAIL,))


def _research_status(model: DashboardModel) -> None:
    system = model.system
    if system is None:
        return
    _research_notes(system.research)
    st.dataframe(
        pd.DataFrame(system.research_stages, columns=["Research item", "Status"]), hide_index=True
    )
    st.markdown("**System health (synthetic)**")
    st.markdown(f"Registry `{system.registry_label}`: `{system.registry_version_id}`")
    st.markdown(f"Clock: {system.clock}")
    st.markdown(f"Holdout guard: {system.holdout_guard}")
    st.dataframe(
        pd.DataFrame(system.producers, columns=["Producer", "Type", "Implementation"]),
        hide_index=True,
    )
    st.dataframe(
        pd.DataFrame(system.freshness_policies, columns=["Freshness policy", "Window"]),
        hide_index=True,
    )
    st.markdown("**Authorization status**")
    st.dataframe(
        pd.DataFrame(system.authorization, columns=["Component", "Status"]), hide_index=True
    )


def _assistant() -> None:
    st.info(L.ASSISTANT_PLACEHOLDER)
    st.text_input("Question for the assistant (disabled)", disabled=True, key="assistant_off")


# --- Navigation callbacks and the app ----------------------------------------------------------


def _nav() -> NavigationState:
    return st.session_state["nav"]


def _set_nav(nav: NavigationState) -> None:
    st.session_state["nav"] = nav
    st.session_state["page"] = nav.page.value


def _open_card(card_id: str, bundle_id: str) -> None:
    _set_nav(open_card(_nav(), card_id, bundle_id))


def _open_contract_review() -> None:
    _set_nav(open_contract_review(_nav()))


def _go(page: Page) -> None:
    _set_nav(go_to(_nav(), page))


def main(source: ScenarioSource | None = None) -> None:
    """Render the dashboard. ``source`` supplies already-validated scenario
    models (for example the offline vertical slice's read-back records);
    without it, the prototype's own synthetic scenarios are shown."""
    st.set_page_config(page_title="SPY Market Intelligence (synthetic prototype)", layout="wide")
    order, titles, models = (source or _prototype_source)()
    st.session_state.setdefault("nav", NavigationState(scenario_id=order[0]))
    st.session_state.setdefault("scenario", order[0])
    st.session_state.setdefault("page", Page.MARKET_OVERVIEW.value)

    st.sidebar.title("SPY Market Intelligence")
    st.sidebar.caption(L.SYNTHETIC_BANNER)
    st.sidebar.selectbox(
        "Synthetic scenario",
        list(order),
        format_func=lambda sid: titles[sid],
        key="scenario",
    )
    st.sidebar.radio("Page", PAGES, format_func=lambda p: PAGE_TITLES[Page(p)], key="page")
    nav = _nav()
    nav = switch_scenario(nav, st.session_state["scenario"])
    nav = go_to(nav, Page(st.session_state["page"]))
    st.session_state["nav"] = nav

    st.title(PAGE_TITLES[nav.page])
    st.info(L.SYNTHETIC_BANNER)
    model = models[nav.scenario_id]
    if isinstance(model, str):
        st.error(f"Validation failed. Nothing is shown. Reason: {L.humanize(model)}")
        return
    st.caption(f"Scenario: {model.title}. {model.summary}")
    try:
        if nav.page is Page.MARKET_OVERVIEW:
            _market_overview(model)
        elif nav.page is Page.PREMARKET_BRIEF:
            _premarket(model)
        elif nav.page is Page.LIVE_SCANNER:
            _scanner(model)
        elif nav.page is Page.SETUP_DETAIL:
            _setup_detail(model, nav)
        elif nav.page is Page.CONTRACT_REVIEW:
            _contract_review(model, nav)
        elif nav.page is Page.RESEARCH_STATUS:
            _research_status(model)
        else:
            _assistant()
    except NavigationError as error:
        st.error(f"Navigation refused. Reason: {L.humanize(error.reason)}")
    st.caption(L.SCOPE_STATEMENT)


if __name__ == "__main__":  # ``streamlit run`` and AppTest execute this as __main__
    main()
