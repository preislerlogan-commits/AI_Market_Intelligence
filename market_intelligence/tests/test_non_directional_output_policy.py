"""Tests for the shared market_intelligence.agents.non_directional_output_policy module.

Confirms the extraction from the Market Evidence Agent preserved exact
matching behavior, that only the one function is a meaningful public
surface, and that both the Market Evidence Agent and the News Analyst reuse
the very same function object (not two independent copies).
"""

from __future__ import annotations

from market_intelligence.agents import market_evidence_agent, news_analyst
from market_intelligence.agents.non_directional_output_policy import (
    CATEGORY_BULLISH_BEARISH_BIAS,
    CATEGORY_DIRECTIONAL_PREDICTION,
    CATEGORY_OPTIONS_DETAIL,
    CATEGORY_TRADE_RECOMMENDATION_OR_ACTION,
    find_prohibited_content_category,
)


def test_returns_none_for_safe_factual_text():
    safe_text = "The close increased from the prior stored value."
    assert find_prohibited_content_category(safe_text) is None


def test_detects_directional_prediction():
    assert (
        find_prohibited_content_category("The stock is expected to rally further.")
        == CATEGORY_DIRECTIONAL_PREDICTION
    )


def test_detects_bullish_bearish_bias():
    category = find_prohibited_content_category("Sentiment is bearish.")
    assert category == CATEGORY_BULLISH_BEARISH_BIAS


def test_detects_trade_recommendation_or_action():
    assert (
        find_prohibited_content_category("We recommend buying this stock now.")
        == CATEGORY_TRADE_RECOMMENDATION_OR_ACTION
    )


def test_detects_options_detail():
    assert (
        find_prohibited_content_category("Discuss the call option strike premium.")
        == CATEGORY_OPTIONS_DETAIL
    )


def test_market_evidence_agent_uses_the_shared_function():
    assert (
        market_evidence_agent.find_prohibited_content_category is find_prohibited_content_category
    )


def test_news_analyst_uses_the_shared_function():
    assert news_analyst.find_prohibited_content_category is find_prohibited_content_category
