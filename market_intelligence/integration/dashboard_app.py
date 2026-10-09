"""Streamlit entry for the offline vertical slice.

Run with ``python -m market_intelligence.integration.demo --dashboard``. It
builds the slice once (temporary stores, removed before rendering) and hands
the read-back, validated scenarios to the existing dashboard unchanged.
"""

from __future__ import annotations

import streamlit as st

from market_intelligence.dashboard.app import main
from market_intelligence.integration.demo import build_slice


@st.cache_resource
def _source():
    result = build_slice()
    return result.order, result.titles, result.models


if __name__ == "__main__":  # ``streamlit run`` and AppTest execute this as __main__
    main(source=_source)
