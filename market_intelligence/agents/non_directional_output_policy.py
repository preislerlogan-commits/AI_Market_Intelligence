"""Shared, deterministic, fail-closed non-directional-output content policy.

This module holds exactly the fixed regex/pattern matching originally built
for the Market Evidence Agent's post-response content policy check (see
``market_intelligence/agents/market_evidence_agent.py``), extracted so any
current or future agent that must never predict market direction, state
bullish/bearish bias, recommend a trade, or discuss options can apply the
same fixed denylist without duplicating it.

It exposes one function, ``find_prohibited_content_category``, plus one
public constant, ``POLICY_PATTERNS`` -- a **read-only** ``Mapping`` view
(``types.MappingProxyType``) over the exact fixed category-to-pattern
mapping ``find_prohibited_content_category`` itself iterates over, never a
duplicate copy and never a mutable alias a caller could add to, replace an
entry in, or delete from. It does not define its own exception type, does
not know about any specific agent's report/analysis schema, and does not
log or return the text it matched against -- each caller is responsible for
raising its own sanitized, agent-specific error (e.g.
``MarketEvidencePolicyError``, ``NewsAnalystPolicyError``) using the
returned category string.

``POLICY_PATTERNS`` exists so a caller that needs per-match detail this
module's own single-category-per-call ``find_prohibited_content_category``
deliberately does not expose (e.g. a match's exact span, to check for an
adjacent clause-local negation cue) can still reuse the identical, unmodified
patterns rather than hand-duplicating them -- read-only, so that reuse can
never mutate the shared policy out from under every other caller. As of this
addition, the only such caller is ``MacroAnalyst``'s narrow,
limitations-only negated-directional-disclaimer allowance (see
``market_intelligence/agents/macro_analyst.py``);
``find_prohibited_content_category`` itself, and every other caller's use of
it, is completely unchanged by this addition.

**This is a conservative, bounded filter and defense-in-depth on top of each
agent's own developer instructions -- it matches known fixed phrasing, not
general semantic meaning, so it is not proof that every possible
directional, bias, trade-recommendation, or options-related statement is
caught.** See each agent's own policy-check docstring/docs for the scope and
limits of how it is applied.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

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

# Public, READ-ONLY view over _POLICY_PATTERNS above (same fixed category
# order) -- a live MappingProxyType, never a plain dict, so no caller can
# add, replace, or delete an entry through this name (attempting to does
# raise TypeError; see test_non_directional_output_policy.py). Exposed so a
# caller needing per-match detail (e.g. MacroAnalyst's clause-local negation
# check) can iterate the identical, unmodified patterns without duplicating
# them, while the module's own internal _POLICY_PATTERNS dict -- the only
# mutable reference to this mapping -- is never itself exported.
# find_prohibited_content_category below is completely unaffected by this
# view existing, and still iterates the internal _POLICY_PATTERNS directly.
POLICY_PATTERNS: Mapping[str, re.Pattern[str]] = MappingProxyType(_POLICY_PATTERNS)


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
