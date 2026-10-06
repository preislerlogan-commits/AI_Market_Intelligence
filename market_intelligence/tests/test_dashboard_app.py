"""Headless rendering of the Streamlit prototype with ``AppTest``: every page
and scenario, the drill path, fail-closed rendering and the absence of any
execution control. Synthetic data only; no server, database or network."""

from __future__ import annotations

import dataclasses
import re
import socket
import traceback
from pathlib import Path

import duckdb
import pytest

# Streamlit is declared in the `dev` extra. These render tests are part of the
# verified dashboard milestone, so a missing Streamlit fails here instead of
# silently skipping them.
import streamlit as st
from streamlit.testing.v1 import AppTest

from market_intelligence.dashboard import fixtures
from market_intelligence.dashboard import labels as L
from market_intelligence.dashboard.fixtures import (
    HOLDOUT_T0,
    SCENARIO_ORDER,
    build_scenarios,
)
from market_intelligence.dashboard.navigation import Page

APP = Path(fixtures.__file__).parent / "app.py"
RESTRICTED_BAR = fixtures.bar_item(HOLDOUT_T0, close="611.11")
REJECTED_CONTRACT = "SPY270112C00682000"  # rejected in every synthetic selector run
ALLOWED_BUTTON = re.compile(
    r"^(Open setup detail|Open contract review|Back to setup detail"
    r"|Open the newer revision|Open revision \d+)$"
)


@pytest.fixture(autouse=True)
def fresh_cache():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def _app(scenario: str = SCENARIO_ORDER[0]) -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=120)
    at.run()
    if scenario != SCENARIO_ORDER[0]:
        at.sidebar.selectbox[0].set_value(scenario).run()
    return at


def _go(at: AppTest, page: Page) -> AppTest:
    return at.sidebar.radio[0].set_value(page.value).run()


def _click(at: AppTest, label: str) -> AppTest:
    matches = [b for b in at.button if b.label == label]
    assert matches, (label, [b.label for b in at.button])
    return matches[0].click().run()


def _text(at: AppTest) -> str:
    parts: list[str] = []
    for group in (at.title, at.markdown, at.caption, at.info, at.warning, at.error):
        parts += [str(e.value) for e in group]
    parts += [e.label for e in at.button]
    parts += [df.value.to_string() for df in at.dataframe]
    return "\n".join(parts)


def _assert_safe(at: AppTest) -> None:
    assert not at.exception
    text = _text(at)
    assert L.forbidden_terms_in(text) == [], L.forbidden_terms_in(text)
    assert RESTRICTED_BAR.item_id not in text and "611.11" not in text
    assert REJECTED_CONTRACT not in text  # rejected contracts are counts only
    assert L.SYNTHETIC_BANNER in text and L.SCOPE_STATEMENT in text
    for button in at.button:
        assert ALLOWED_BUTTON.match(button.label), button.label
    for widget in (
        "number_input",
        "slider",
        "date_input",
        "time_input",
        "file_uploader",
        "checkbox",
        "toggle",
        "multiselect",
        "text_area",
        "chat_input",
    ):
        assert not getattr(at, widget, []), widget
    assert [t.label for t in at.text_input] in ([], ["Question for the assistant (disabled)"])
    assert all(t.disabled for t in at.text_input)


# --- Every page, every scenario -----------------------------------------------------------------


@pytest.mark.parametrize("scenario", SCENARIO_ORDER)
def test_every_page_renders_safely(scenario):
    at = _app(scenario)
    for page in Page:
        _go(at, page)
        _assert_safe(at)
        assert not at.error, [e.value for e in at.error]
        if page in (Page.MARKET_OVERVIEW, Page.PREMARKET_BRIEF, Page.LIVE_SCANNER):
            text = _text(at)
            assert "As of" in text and "UTC" in text and "Evidence readiness" in text


def test_scanner_lanes_are_separate_and_honest():
    at = _go(_app(), Page.LIVE_SCANNER)
    assert [t.label for t in at.tabs] == ["VWAP reversion", "Trend continuation"]
    infos = [i.value for i in at.info]
    assert any(v.startswith("Live qualification not authorized") for v in infos)
    assert any(v.startswith(L.TREND_LANE_TEXT) for v in infos)
    assert not [b for b in at.button if b.label == "Open setup detail"]


def test_no_qualified_setup_renders_as_a_neutral_result():
    at = _go(_app("no_qualified_setup"), Page.LIVE_SCANNER)
    text = _text(at)
    assert "No qualified setup in this lane (a valid result, not an error)" in text
    assert not at.error


# --- The drill path keeps one card and one bundle -----------------------------------------------


def test_drill_path_preserves_card_and_bundle_identity():
    scenario = build_scenarios()["ready_for_manual_review"]
    card = scenario.vwap.history[0].card
    at = _go(_app("ready_for_manual_review"), Page.LIVE_SCANNER)
    _click(at, "Open setup detail")
    assert at.sidebar.radio[0].value == Page.SETUP_DETAIL.value
    assert card.decision_context_bundle_id in _text(at)
    assert L.short_id(card.card_id) in _text(at)
    _click(at, "Open contract review")
    assert at.sidebar.radio[0].value == Page.CONTRACT_REVIEW.value
    text = _text(at)
    assert card.decision_context_bundle_id in text and L.short_id(card.card_id) in text
    assert "Eligible contracts (as returned by the selector)" in text
    assert "Research only, not eligible" in text
    eligible = [df.value for df in at.dataframe if "Eligible contract" in df.value.columns]
    assert list(eligible[0]["Eligible contract"]) == card.contracts.eligible_contracts
    _click(at, "Back to setup detail")
    assert card.decision_context_bundle_id in _text(at)
    _go(at, Page.MARKET_OVERVIEW)
    _go(at, Page.SETUP_DETAIL)
    assert card.decision_context_bundle_id in _text(at)  # the card travels with the user


def test_superseded_card_offers_the_newer_revision():
    at = _go(_app("outdated_and_superseded"), Page.LIVE_SCANNER)
    _click(at, "Open setup detail")
    assert "Outdated view" in _text(at)
    _click(at, "Open revision 1")
    text = _text(at)
    assert "Newer evidence available" in text and "Superseded" in text
    _click(at, "Open the newer revision")
    assert "Newer evidence available" not in _text(at)


@pytest.mark.parametrize(
    ("scenario", "expected", "absent"),
    [
        ("selector_unavailable", "Contract selector unavailable", "no eligible contracts"),
        ("no_eligible_contracts", "The selector returned no eligible contracts", None),
        ("research_only_contracts", "INDICATIVE", None),
    ],
)
def test_contract_review_states(scenario, expected, absent):
    at = _go(_app(scenario), Page.LIVE_SCANNER)
    _click(at, "Open setup detail")
    _click(at, "Open contract review")
    text = _text(at)
    assert expected in text
    if absent:
        assert absent not in text.lower()
    _assert_safe(at)


def test_context_pages_without_a_card_explain_how_to_open_one():
    at = _go(_app(), Page.SETUP_DETAIL)
    assert "Open a card from the Live Scanner" in _text(at)
    _go(at, Page.CONTRACT_REVIEW)
    assert "Open a card first" in _text(at)


# --- Assistant, fail-closed rendering and isolation -------------------------------------------


def test_assistant_is_a_disabled_placeholder():
    at = _go(_app(), Page.ASSISTANT)
    assert at.text_input[0].disabled
    assert any("registry grant" in i.value for i in at.info)
    assert not [b for b in at.button]


def test_invalid_bundle_renders_only_the_reason_token(monkeypatch):
    real = build_scenarios()
    first = real[SCENARIO_ORDER[0]]
    forged = first.market.bundle.model_copy(update={"unresolved_material_conflict_count": 9})
    tampered = {
        **real,
        first.scenario_id: dataclasses.replace(
            first, market=dataclasses.replace(first.market, bundle=forged)
        ),
    }
    monkeypatch.setattr(fixtures, "build_scenarios", lambda: tampered)
    at = AppTest.from_file(str(APP), default_timeout=120).run()
    assert [e.value for e in at.error] == [
        "Validation failed. Nothing is shown. Reason: Bundle invalid (bundle_invalid)"
    ]
    assert not at.dataframe and "As of" not in _text(at)


def test_rendering_opens_no_database_or_connection(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the prototype must not open a database or a connection")

    real_connect = socket.socket.connect

    def loopback_self_pipe_only(sock, address):
        # On Windows the test harness's asyncio event loop builds its internal
        # self-pipe with a loopback socket pair. That, and nothing else, may
        # connect; any connection the dashboard made would be refused here.
        stack = [frame.name for frame in traceback.extract_stack()]
        if "_fallback_socketpair" in stack and address[0] in ("127.0.0.1", "::1"):
            return real_connect(sock, address)
        refuse()

    monkeypatch.setattr(duckdb, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", loopback_self_pipe_only)
    monkeypatch.setattr(socket, "create_connection", refuse)
    at = _app("ready_for_manual_review")
    for page in Page:
        _go(at, page)
        assert not at.exception
