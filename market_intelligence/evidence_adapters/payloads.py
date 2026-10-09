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
    }
)
