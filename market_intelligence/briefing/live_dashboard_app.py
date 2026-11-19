"""Streamlit entry for the live macro-and-news briefing handoff.

Run with ``streamlit run market_intelligence/briefing/live_dashboard_app.py``.
While no live run is authorized (``AUTHORIZED_LIVE_RECORDS`` is empty) the run
control stays disabled; this page does not authorize or execute a live run.
"""

from __future__ import annotations

from market_intelligence.briefing import live_app

if __name__ == "__main__":  # ``streamlit run`` and AppTest execute this as __main__
    live_app.main()
