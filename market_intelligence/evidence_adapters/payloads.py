"""Payload schemas for the offline producer adapters (docs/EVIDENCE_REGISTRY.md §4).

Strict and bounded. Prices and sizes are canonical decimal strings, never
floats; timestamps are canonical UTC strings. No schema carries an
indicator, label, direction, sentiment, probability or recommendation.

Free text appears only in fields the schema marks **display-only**
(``headline``, ``summary``, ``title``). Those fields are provider content,
never a machine-decision input: a news item's claim is provider-reported and
unverified, and only its publication metadata is a confirmed fact.

The regime payloads reuse the engine's own ``spy-regime-engine-1`` output
contracts unchanged; no parallel model is defined.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator

from market_intelligence.evidence.payloads import CORE_PAYLOAD_MODELS
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    CanonicalDecimal,
    SafeText,
    UtcTimestamp,
)
from market_intelligence.market_features.spy_regime_contracts import (
    RegimeClassificationResult,
    RegimeFeatures,
)

MARKET_BAR_FACT = "market_bar_fact.v1"
MARKET_SNAPSHOT_FACT = "market_snapshot_fact.v1"
NEWS_PUBLICATION_FACT = "news_publication_fact.v1"
MACRO_OBSERVATION_FACT = "macro_observation_fact.v1"
MACRO_SERIES_METADATA_FACT = "macro_series_metadata_fact.v1"
SPY_REGIME_FEATURES = "spy_regime_features.v1"
SPY_REGIME_CLASSIFICATION = "spy_regime_classification.v1"
# One schema per feed, so registry permissions are separate structurally.
OPTION_QUOTE_OPRA_FACT = "option_quote_opra_fact.v1"
OPTION_QUOTE_INDICATIVE_FACT = "option_quote_indicative_fact.v1"
# Analysis-agent outputs: labelled, non-directional current_inference only.
MARKET_EVIDENCE_OBSERVATION = "market_evidence_observation.v1"
NEWS_EVENT_CLAIM = "news_event_claim.v1"
MACRO_CLAIM = "macro_claim.v1"

DISPLAY_TEXT_LIMIT = 500
_Symbol = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.]{0,9}$")]
_SeriesId = Annotated[str, Field(pattern=r"^[A-Z0-9_]{1,32}$")]
_ShortText = Annotated[SafeText, Field(min_length=1, max_length=128)]
_DisplayOnly = Annotated[SafeText, Field(min_length=1, max_length=DISPLAY_TEXT_LIMIT)]
_Size = Annotated[int, Field(ge=0, le=10_000_000_000)]

WINDOW_DESCRIPTION = (
    "True only when the bar lies entirely within the ordinary 09:30-16:00 "
    "America/New_York clock window on a Monday-Friday. A clock-window check only: "
    "it does not prove the exchange was open, that the date was a trading session, "
    "or that a shortened session was complete. No exchange calendar is approved, so "
    "actual session classification is unavailable."
)


class MarketBarFact(BaseModel):
    """One provider-reported bar. ``bar_start_utc`` is the provider's bar
    timestamp (the bar's start); ``bar_end_utc`` is when it was complete.

    ``within_regular_hours_window`` is a clock-window check, never
    exchange-calendar validation (see ``WINDOW_DESCRIPTION``)."""

    model_config = STRICT_FROZEN

    provider: Literal["alpaca"]
    symbol: _Symbol
    timeframe: Literal["5Min"]
    feed: Literal["iex"]
    adjustment: Literal["raw"]
    currency: Literal["USD"]
    bar_start_utc: UtcTimestamp
    bar_end_utc: UtcTimestamp
    within_regular_hours_window: Annotated[bool, Field(description=WINDOW_DESCRIPTION)]
    open: CanonicalDecimal
    high: CanonicalDecimal
    low: CanonicalDecimal
    close: CanonicalDecimal
    volume: _Size
    trade_count: _Size | None
    vwap: CanonicalDecimal | None


class MarketSnapshotFact(BaseModel):
    """The latest trade and, when reported, the latest quote, as reported.
    An absent quote is null and named as a missing component, never zero."""

    model_config = STRICT_FROZEN

    provider: Literal["alpaca"]
    symbol: _Symbol
    feed: Literal["iex", "sip"]
    trade_at_utc: UtcTimestamp
    trade_price: CanonicalDecimal
    trade_size: _Size
    quote_at_utc: UtcTimestamp | None
    bid_price: CanonicalDecimal | None
    bid_size: _Size | None
    ask_price: CanonicalDecimal | None
    ask_size: _Size | None

    @model_validator(mode="after")
    def _check_quote(self) -> MarketSnapshotFact:
        parts = (self.quote_at_utc, self.bid_price, self.bid_size, self.ask_price, self.ask_size)
        if any(p is None for p in parts) and any(p is not None for p in parts):
            raise ValueError("a quote is either complete or absent")
        return self


class NewsPublicationFact(BaseModel):
    """Publication metadata is fact. ``headline`` and ``summary`` are
    display-only provider content, explicitly unverified."""

    model_config = STRICT_FROZEN

    provider: Literal["alpaca"]
    provider_article_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9._-]{1,64}$")]
    publisher: _ShortText
    created_at_utc: UtcTimestamp
    updated_at_utc: UtcTimestamp | None
    related_symbols: Annotated[list[_Symbol], Field(max_length=32)]
    content_status: Literal["provider_reported_unverified"]
    headline: _DisplayOnly
    headline_truncated: bool
    summary: _DisplayOnly | None
    summary_truncated: bool

    @model_validator(mode="after")
    def _check(self) -> NewsPublicationFact:
        if self.related_symbols != sorted(set(self.related_symbols)):
            raise ValueError("related_symbols must be sorted and unique")
        if self.updated_at_utc is not None and self.updated_at_utc < self.created_at_utc:
            raise ValueError("an update cannot precede publication")
        if self.summary is None and self.summary_truncated:
            raise ValueError("an absent summary cannot be truncated")
        return self


class MacroObservationFact(BaseModel):
    """One FRED observation value in one vintage window, as reported. The
    frequency and units are copied from the series metadata."""

    model_config = STRICT_FROZEN

    provider: Literal["fred"]
    series_id: _SeriesId
    observation_date: date
    realtime_start: date
    realtime_end: date
    value: CanonicalDecimal
    frequency_short: _ShortText
    units_short: _ShortText

    @model_validator(mode="after")
    def _check(self) -> MacroObservationFact:
        if self.realtime_end < self.realtime_start:
            raise ValueError("a vintage window cannot end before it starts")
        return self


class MacroSeriesMetadataFact(BaseModel):
    """FRED series metadata as reported at ``retrieved_at_utc`` (the stored
    table keeps no history). ``title`` is display-only."""

    model_config = STRICT_FROZEN

    provider: Literal["fred"]
    series_id: _SeriesId
    title: _DisplayOnly
    frequency: _ShortText
    frequency_short: _ShortText
    units: _ShortText
    units_short: _ShortText
    seasonal_adjustment: _ShortText
    seasonal_adjustment_short: _ShortText
    observation_start: date
    observation_end: date
    last_updated_utc: UtcTimestamp
    retrieved_at_utc: UtcTimestamp


class OptionQuoteFact(BaseModel):
    """One provider-reported SPY option-contract observation from one chain
    snapshot. The feed is copied verbatim: ``indicative`` is never relabelled
    ``opra``. No field is derived here: no eligibility, ranking, sentiment,
    direction, return or probability. Whether a contract is eligible is the
    deterministic Contract Selector's decision alone."""

    model_config = STRICT_FROZEN

    provider: Literal["alpaca"]
    underlying: Literal["SPY"]
    feed: Literal["opra", "indicative"]
    contract_symbol: Annotated[str, Field(pattern=r"^SPY\d{6}[CP]\d{8}$")]
    expiration_date: date
    option_type: Literal["call", "put"]
    strike_price: CanonicalDecimal
    quote_at_utc: UtcTimestamp | None
    bid_price: CanonicalDecimal | None
    bid_size: _Size | None
    ask_price: CanonicalDecimal | None
    ask_size: _Size | None
    implied_volatility: CanonicalDecimal | None
    delta: CanonicalDecimal | None
    gamma: CanonicalDecimal | None
    theta: CanonicalDecimal | None
    vega: CanonicalDecimal | None
    rho: CanonicalDecimal | None
    retrieved_at_utc: UtcTimestamp


class OptionQuoteOpraFact(OptionQuoteFact):
    """``option_quote_opra_fact.v1``: the shared model, OPRA feed only."""

    feed: Literal["opra"]


class OptionQuoteIndicativeFact(OptionQuoteFact):
    """``option_quote_indicative_fact.v1``: the shared model, indicative feed
    only. Never eligible for operational machine-decision use."""

    feed: Literal["indicative"]


# --- Analysis-agent inference payloads ------------------------------------------------------

_AgentText = Annotated[SafeText, Field(min_length=1, max_length=400)]
_Limitation = Annotated[SafeText, Field(min_length=1, max_length=300)]
_CitationKey = Annotated[str, Field(min_length=1, max_length=64)]
_ModelQuality = Literal["sufficient", "limited", "insufficient"]
_Channel = Literal[
    "rates",
    "inflation",
    "growth",
    "earnings",
    "liquidity",
    "risk_appetite",
    "regulation",
    "supply_chain",
    "credit_conditions",
    "currency",
    "housing",
    "energy",
    "other",
]


class _AgentInference(BaseModel):
    """Fields every agent inference carries. The model's own
    ``evidence_quality`` is kept here as ``model_reported_evidence_quality``
    only; it never sets the item's quality profile. Direction and trade
    recommendation are fixed as not performed."""

    model_config = STRICT_FROZEN

    agent: Literal["market_evidence_agent", "news_agent", "macro_agent"]
    agent_generated_at_utc: UtcTimestamp
    model_reported_evidence_quality: _ModelQuality | None
    limitations: Annotated[list[_Limitation], Field(max_length=6)]
    cited_evidence_keys: Annotated[list[_CitationKey], Field(min_length=1, max_length=16)]
    non_directional: Literal[True]
    directional_assessment: Literal["not_performed"]
    trade_recommendation: Literal["not_performed"]


class _AgentClaim(_AgentInference):
    report_status: Literal["completed"]
    claim_index: Annotated[int, Field(ge=0, le=15)]
    claim_count: Annotated[int, Field(ge=1, le=16)]

    @model_validator(mode="after")
    def _check_index(self) -> _AgentClaim:
        if self.claim_index >= self.claim_count:
            raise ValueError("claim_index must be below claim_count")
        return self


class MarketEvidenceObservation(_AgentClaim):
    """``market_evidence_observation.v1``: one Market Evidence Agent observation."""

    agent: Literal["market_evidence_agent"]
    symbol: Literal["SPY"]
    session_date_et: date | None
    evidence_summary: Annotated[SafeText, Field(min_length=1, max_length=800)] | None
    category: Literal["data_quality", "price", "volume", "news", "macro"]
    statement: _AgentText


class NewsEventClaim(_AgentClaim):
    """``news_event_claim.v1``: one News Analyst event claim."""

    agent: Literal["news_agent"]
    symbol: Literal["SPY"]
    snapshot_created_at_utc: UtcTimestamp
    source_article_count: Annotated[int, Field(ge=0, le=10_000)]
    event_type: Literal[
        "monetary_policy",
        "economic_data",
        "corporate",
        "regulatory",
        "geopolitical",
        "market_structure",
        "other",
    ]
    claim_summary: _AgentText
    content_basis: Literal["headline_only", "headline_and_provider_summary"]
    transmission_channels: Annotated[list[_Channel], Field(max_length=4)]
    relevance: Literal["direct", "broad_market", "sector_or_industry"]
    relevance_rationale: Annotated[SafeText, Field(min_length=1, max_length=300)]
    conditional_mechanism: Annotated[SafeText, Field(min_length=1, max_length=300)] | None


class MacroClaimInference(_AgentClaim):
    """``macro_claim.v1``: one Macro Analyst claim about one series."""

    agent: Literal["macro_agent"]
    series_ids: Annotated[list[_SeriesId], Field(min_length=1, max_length=16)]
    snapshot_created_at_utc: UtcTimestamp
    source_series_count: Annotated[int, Field(ge=0, le=10_000)]
    series_id: _SeriesId
    claim_summary: _AgentText
    content_basis: Literal["stored_observation_and_official_metadata"]
    economic_category: Literal[
        "policy_rate", "inflation", "labor", "growth", "liquidity", "housing", "energy", "other"
    ]
    transmission_channels: Annotated[list[_Channel], Field(max_length=4)]
    conditional_mechanism: Annotated[SafeText, Field(min_length=1, max_length=300)] | None


ADAPTER_PAYLOAD_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        **CORE_PAYLOAD_MODELS,
        MARKET_BAR_FACT: MarketBarFact,
        MARKET_SNAPSHOT_FACT: MarketSnapshotFact,
        NEWS_PUBLICATION_FACT: NewsPublicationFact,
        MACRO_OBSERVATION_FACT: MacroObservationFact,
        MACRO_SERIES_METADATA_FACT: MacroSeriesMetadataFact,
        SPY_REGIME_FEATURES: RegimeFeatures,
        SPY_REGIME_CLASSIFICATION: RegimeClassificationResult,
        OPTION_QUOTE_OPRA_FACT: OptionQuoteOpraFact,
        OPTION_QUOTE_INDICATIVE_FACT: OptionQuoteIndicativeFact,
        MARKET_EVIDENCE_OBSERVATION: MarketEvidenceObservation,
        NEWS_EVENT_CLAIM: NewsEventClaim,
        MACRO_CLAIM: MacroClaimInference,
    }
)
