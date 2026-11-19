"""Streamlit rendering for the live macro-and-news briefing handoff.

The Streamlit process owns the run and receives the briefing result
directly; only the materialized result lives in ``st.session_state``. The
existing Market Overview and Pre-Market Brief views render it. The run
control is a button, disabled while no authorization is recorded or the
preflight is not ready, and it can run at most once per session; a refresh
never runs anything. Live assistant access is not available.

**This implements the display handoff only. It does not authorize or
execute a live run.**
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import streamlit as st

from market_intelligence.briefing.session import (
    REFUSAL_KEY,
    BriefingSessionController,
    SessionRefusal,
    default_controller,
)
from market_intelligence.dashboard import app as dashboard_app
from market_intelligence.dashboard import labels as L

LIVE_ASSISTANT_UNAVAILABLE = (
    "Live assistant access remains unavailable. The assistant reads synthetic registries "
    "only; a live briefing is reviewed through the dashboard views below."
)
NOT_AUTHORIZED_NOTICE = (
    "No live briefing is authorized. The run control stays disabled until a recorded "
    "authorization exists and the preflight is ready. This page does not authorize or "
    "execute a live run."
)
PAGES = {"premarket_brief": "Pre-Market Brief", "market_overview": "Market Overview"}

# Replaced only by tests, to inject a controller built from fakes.
controller_factory: Callable[[], BriefingSessionController] = default_controller


def _status(controller: BriefingSessionController) -> bool:
    status = controller.status(st.session_state)
    report = status.preflight
    st.markdown("**Preflight (no network request; configuration presence only)**")
    st.dataframe(
        pd.DataFrame(
            [
                ("FRED API key", report.fred_configured),
                ("Alpaca API key and secret", report.alpaca_configured),
                ("OpenAI API key", report.openai_configured),
                ("OpenAI model setting", report.openai_model_configured),
            ],
            columns=["Setting", "Present"],
        ),
        hide_index=True,
    )
    st.markdown(f"**Authorization:** {'recorded' if status.authorized else 'not authorized'}")
    if not status.authorized:
        st.warning(NOT_AUTHORIZED_NOTICE)
    for blocker in status.blockers:
        st.caption(f"Run blocked: {L.humanize(blocker)}")
    return status.run_enabled


def _run(controller: BriefingSessionController) -> None:
    try:
        controller.run_once(st.session_state)
    except SessionRefusal as refusal:
        st.session_state[REFUSAL_KEY] = refusal.reason


def render(controller: BriefingSessionController) -> None:
    st.set_page_config(page_title="Macro-and-news briefing (live handoff)", layout="wide")
    st.title("Macro-and-news briefing")
    st.info(LIVE_ASSISTANT_UNAVAILABLE)
    enabled = _status(controller)
    st.button(
        "Run the authorized briefing once",
        disabled=not enabled,
        on_click=_run,
        args=(controller,),
    )
    refusal = st.session_state.get(REFUSAL_KEY)
    if refusal:
        st.error(f"The briefing refused to run or finish. Reason: {L.humanize(refusal)}")
    record = controller.result(st.session_state)
    if record is None:
        st.caption("No briefing result is held in this session.")
        return
    st.button("Clear the briefing from memory", on_click=controller.clear, args=(st.session_state,))
    st.caption(
        f"Result held in memory only (registry `{record.registry_label}`, bundle "
        f"`{record.bundle_id}`). Its temporary store has already been deleted."
    )
    page = st.radio("View", list(PAGES), format_func=PAGES.get, key="briefing_page")
    if page == "market_overview":
        dashboard_app._market_overview(record.model)
    else:
        dashboard_app._premarket(record.model)
    st.caption(L.SCOPE_STATEMENT)


def main() -> None:
    render(controller_factory())
