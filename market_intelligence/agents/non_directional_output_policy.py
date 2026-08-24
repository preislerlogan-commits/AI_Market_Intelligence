"""Shared, deterministic, fail-closed non-directional-output content policy.

This module holds exactly the fixed regex/pattern matching originally built
for the Market Evidence Agent's post-response content policy check (see
``market_intelligence/agents/market_evidence_agent.py``), extracted so any
current or future agent that must never predict market direction, state
bullish/bearish bias, recommend a trade, or discuss options can apply the
same fixed denylist without duplicating it.

It exposes exactly one function, ``find_prohibited_content_category``. It
does not define its own exception type, does not know about any specific
agent's report/analysis schema, and does not log or return the text it
matched against -- each caller is responsible for raising its own
sanitized, agent-specific error (e.g. ``MarketEvidencePolicyError``,
``NewsAnalystPolicyError``) using the returned category string.

**This is a conservative, bounded filter and defense-in-depth on top of each
agent's own developer instructions -- it matches known fixed phrasing, not
general semantic meaning, so it is not proof that every possible
directional, bias, trade-recommendation, or options-related statement is
caught.** See each agent's own policy-check docstring/docs for the scope and
limits of how it is applied.
"""

from __future__ import annotations

import re

_DIRECTIONAL_PREDICTION_RE = re.compile(
    r"\b(?:will|going\s+to)\s+(?:rise|rally|surge|climb|jump|soar|fall|drop|"
    r"plunge|decline|slide|tumble|break\s*out|reverse|correct)\b"
    r"|\b(?:expected|likely|poised|set|primed)\s+to\s+(?:rise|rally|surge|"
    r"climb|fall|drop|plunge|decline|slide|tumble)\b"
    r"|\bprice\s+target\b"
    r"|\b(?:forecast|predict)(?:s|ed|ing|ion)?\b"
    r"|\boutlook\s+is\b",
    re.IGNORECASE,
)

_BIAS_RE = re.compile(r"\b(?:bullish|bearish)\b", re.IGNORECASE)

_TRADE_ACTION_RE = re.compile(
    r"\b(?:buy|sell|short|long)\s+(?:this|the)\s+(?:stock|symbol|shares?|"
    r"position|security)\b"
    r"|\brecommend(?:s|ed|ing)?\s+(?:a\s+)?(?:buy|sell|buying|selling|shorting)\b"
    r"|\b(?:should|consider)\s+(?:buy|sell|buying|selling|shorting)\b"
    r"|\bgo(?:ing)?\s+(?:long|short)\b"
    r"|\benter(?:ing)?\s+a\s+(?:trade|position)\b"
    r"|\bexit(?:ing)?\s+(?:the|a)\s+(?:trade|position)\b"
    r"|\bstop[\s-]?loss\b"
    r"|\btake[\s-]?profit\b"
    r"|\b(?:buy|sell)\s+signal\b"
    r"|\bhold\s+(?:this|the)\s+(?:stock|position|shares?)\b"
    r"|\btrade\s+recommendation\b",
    re.IGNORECASE,
)

_OPTIONS_RE = re.compile(
    r"\bstrikes?\b"
    r"|\boptions?\b"
    r"|\b(?:call|put)\s+options?\b"
    r"|\bpremiums?\b",
    re.IGNORECASE,
)

# Fixed, sanitized violation-category strings, returned by
# find_prohibited_content_category and reused verbatim by every caller.
CATEGORY_DIRECTIONAL_PREDICTION = "directional_prediction"
CATEGORY_BULLISH_BEARISH_BIAS = "bullish_bearish_bias"
CATEGORY_TRADE_RECOMMENDATION_OR_ACTION = "trade_recommendation_or_action"
CATEGORY_OPTIONS_DETAIL = "options_detail"

_POLICY_PATTERNS: dict[str, re.Pattern[str]] = {
    CATEGORY_DIRECTIONAL_PREDICTION: _DIRECTIONAL_PREDICTION_RE,
    CATEGORY_BULLISH_BEARISH_BIAS: _BIAS_RE,
    CATEGORY_TRADE_RECOMMENDATION_OR_ACTION: _TRADE_ACTION_RE,
    CATEGORY_OPTIONS_DETAIL: _OPTIONS_RE,
}


def find_prohibited_content_category(text: str) -> str | None:
    """Return the first matching prohibited-content category for ``text``, or ``None``.

    Checks, in a fixed order, for directional-prediction, bullish/bearish-bias,
    trade-recommendation/action, and options-related language. Never returns,
    logs, or otherwise retains the matched text itself -- only one of the
    fixed ``CATEGORY_*`` strings above, or ``None`` if nothing matched.
    """
    for category, pattern in _POLICY_PATTERNS.items():
        if pattern.search(text):
            return category
    return None
