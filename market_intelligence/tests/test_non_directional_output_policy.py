"""Tests for the shared market_intelligence.agents.non_directional_output_policy module.

Confirms the extraction from the Market Evidence Agent preserved exact
matching behavior, that ``find_prohibited_content_category`` and the
read-only ``POLICY_PATTERNS`` view are the meaningful public surface, that
``POLICY_PATTERNS`` cannot be mutated by any caller (it is a live
``types.MappingProxyType``, never the module's internal mutable dict), and
that both the Market Evidence Agent and the News Analyst reuse the very same
``find_prohibited_content_category`` function object (not two independent
copies).
"""

from __future__ import annotations

import re
from types import MappingProxyType

import pytest

from market_intelligence.agents import market_evidence_agent, news_analyst
from market_intelligence.agents.non_directional_output_policy import (
    CATEGORY_BULLISH_BEARISH_BIAS,
    CATEGORY_DIRECTIONAL_PREDICTION,
    CATEGORY_OPTIONS_DETAIL,
    CATEGORY_TRADE_RECOMMENDATION_OR_ACTION,
    POLICY_PATTERNS,
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


def test_policy_patterns_covers_the_same_categories_as_find_prohibited_content_category():
    """``POLICY_PATTERNS`` (added for MacroAnalyst's clause-local negation
    check) must cover exactly the same fixed categories, in the same fixed
    priority order, that ``find_prohibited_content_category`` itself checks
    -- never a divergent or duplicated copy."""
    assert list(POLICY_PATTERNS) == [
        CATEGORY_DIRECTIONAL_PREDICTION,
        CATEGORY_BULLISH_BEARISH_BIAS,
        CATEGORY_TRADE_RECOMMENDATION_OR_ACTION,
        CATEGORY_OPTIONS_DETAIL,
    ]
    for category, pattern in POLICY_PATTERNS.items():
        sample = {
            CATEGORY_DIRECTIONAL_PREDICTION: "This is expected to rally.",
            CATEGORY_BULLISH_BEARISH_BIAS: "Sentiment is bullish.",
            CATEGORY_TRADE_RECOMMENDATION_OR_ACTION: "We recommend buying this stock.",
            CATEGORY_OPTIONS_DETAIL: "Discuss the call option strike premium.",
        }[category]
        assert pattern.search(sample)
        assert find_prohibited_content_category(sample) == category


# ---------------------------------------------------------------------------
# POLICY_PATTERNS is a read-only view, never a mutable alias -- a caller
# (e.g. MacroAnalyst) may iterate it, but can never add, replace, or delete
# an entry through it. The module's internal, mutable _POLICY_PATTERNS dict
# is never itself exported.
# ---------------------------------------------------------------------------


def test_policy_patterns_is_a_mapping_proxy_not_the_internal_mutable_dict():
    assert isinstance(POLICY_PATTERNS, MappingProxyType)
    assert not isinstance(POLICY_PATTERNS, dict)


def test_policy_patterns_cannot_add_a_new_entry():
    with pytest.raises(TypeError):
        POLICY_PATTERNS["new_category"] = re.compile(r"placeholder")


def test_policy_patterns_cannot_replace_an_existing_entry():
    with pytest.raises(TypeError):
        POLICY_PATTERNS[CATEGORY_DIRECTIONAL_PREDICTION] = re.compile(r"placeholder")


def test_policy_patterns_cannot_delete_an_entry():
    with pytest.raises(TypeError):
        del POLICY_PATTERNS[CATEGORY_DIRECTIONAL_PREDICTION]


def test_policy_patterns_cannot_be_cleared():
    with pytest.raises(AttributeError):
        POLICY_PATTERNS.clear()


def test_attempted_mutation_never_affects_find_prohibited_content_category():
    """Even after every mutation attempt above (each of which raised and
    changed nothing), the shared matcher still returns the same categories
    for the same fixed samples -- proving POLICY_PATTERNS mutation attempts
    can never desynchronize it from find_prohibited_content_category."""
    assert (
        find_prohibited_content_category("The stock is expected to rally further.")
        == CATEGORY_DIRECTIONAL_PREDICTION
    )
    assert (
        find_prohibited_content_category("Sentiment is bearish.") == CATEGORY_BULLISH_BEARISH_BIAS
    )
    assert (
        find_prohibited_content_category("We recommend buying this stock now.")
        == CATEGORY_TRADE_RECOMMENDATION_OR_ACTION
    )
    assert (
        find_prohibited_content_category("Discuss the call option strike premium.")
        == CATEGORY_OPTIONS_DETAIL
    )
    safe_text = "The close increased from the prior stored value."
    assert find_prohibited_content_category(safe_text) is None


def test_negated_directional_disclaimer_is_still_flagged_by_the_raw_function():
    """Local reproduction of the negated-disclaimer false-positive class that
    motivated MacroAnalyst's narrow, limitations-only negated-directional-
    prediction allowance (see market_intelligence/agents/macro_analyst.py,
    docs/MACRO_ANALYST.md). ``find_prohibited_content_category`` itself
    matches known fixed phrasing only, with no negation awareness of any
    kind -- a clearly negated disclaimer such as "does not predict future
    market direction" still matches CATEGORY_DIRECTIONAL_PREDICTION here.
    This is deliberately unchanged: only MacroAnalyst's own
    limitations-only post-processing narrowly exempts this case; the shared
    function every agent (including MacroAnalyst itself, for claim_summary/
    conditional_mechanism) calls directly is not modified."""
    negated_disclaimer = "These observations do not predict future market direction."
    assert (
        find_prohibited_content_category(negated_disclaimer) == CATEGORY_DIRECTIONAL_PREDICTION
    )


def test_market_evidence_agent_uses_the_shared_function():
    assert (
        market_evidence_agent.find_prohibited_content_category is find_prohibited_content_category
    )


def test_news_analyst_uses_the_shared_function():
    assert news_analyst.find_prohibited_content_category is find_prohibited_content_category
