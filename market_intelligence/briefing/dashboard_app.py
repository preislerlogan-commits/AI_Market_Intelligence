"""Streamlit entry for the offline macro-and-news briefing.

Run with ``streamlit run market_intelligence/briefing/dashboard_app.py``. It
runs the briefing once with the deterministic fakes (no provider, model or
network; the temporary workspace is removed before rendering) and hands the
read-back, validated result to the existing dashboard unchanged. It never
runs a live briefing.
"""

from __future__ import annotations

import streamlit as st

from market_intelligence.briefing import fakes
from market_intelligence.briefing.workflow import (
    BriefingDependencies,
    BriefingRefusal,
    BriefingRequest,
    run_briefing,
)
from market_intelligence.dashboard.app import main


@st.cache_resource
def _source():
    deps = BriefingDependencies(
        sources=fakes.FakeSources(),
        model_client=fakes.FakeModelClient(),
        clock=fakes.fake_clock,
        clock_health=fakes.fake_clock_health,
    )
    try:
        model = run_briefing(BriefingRequest(), deps).model
    except BriefingRefusal as refusal:
        model = refusal.reason
    return (
        ("macro_news_briefing",),
        {"macro_news_briefing": "Macro-and-news briefing"},
        {"macro_news_briefing": model},
    )


if __name__ == "__main__":  # ``streamlit run`` and AppTest execute this as __main__
    main(source=_source)
