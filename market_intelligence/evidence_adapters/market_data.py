"""Adapters for Alpaca SPY market bars and market snapshots (registry
``alpaca_market_bars`` and ``alpaca_market_snapshot``).

Confirmed facts only, copied from validated source fields: no indicator,
setup, direction or recommendation is calculated. SPY price evidence dated
inside the sealed holdout window is refused before any item is built.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, time, timedelta
from typing import Any

from market_intelligence.evidence.contracts import (
    DuckDbRowReference,
    EvidenceAvailability,
    EvidenceItem,
    EvidenceSubject,
    EvidenceTemporalScope,
    ProviderRequestReference,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    DuckDbTable,
    EndpointClass,
    EvidenceKind,
    ResponseClass,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence_adapters.common import (
    EASTERN,
    SPY_SUBJECT_ID,
    SPY_SYMBOL,
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    canonical_decimal,
    decimal_from_json_number,
    eastern_date,
    fail,
    missing_evidence,
    parse_model,
    parse_utc,
    refuse_spy_holdout,
    require_configuration,
    require_price_schema,
    source_fields,
    window_subject,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    MARKET_BAR_FACT,
    MARKET_SNAPSHOT_FACT,
    MarketBarFact,
    MarketSnapshotFact,
)

BARS_PRODUCER = "alpaca_market_bars"
SNAPSHOT_PRODUCER = "alpaca_market_snapshot"
BAR_TIMEFRAMES = {"5Min": timedelta(minutes=5)}
BAR_FEEDS = frozenset({"iex"})
SNAPSHOT_FEEDS = frozenset({"iex", "sip"})
REGULAR_OPEN, REGULAR_CLOSE = time(9, 30), time(16, 0)

BARS_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": BARS_PRODUCER,
        "adjustment": "raw",
        "currency": "USD",
        "feeds": sorted(BAR_FEEDS),
        "payload_schema": MARKET_BAR_FACT,
        "symbols": [SPY_SYMBOL],
        "timeframes": sorted(BAR_TIMEFRAMES),
    }
)
SNAPSHOT_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": SNAPSHOT_PRODUCER,
        "feeds": sorted(SNAPSHOT_FEEDS),
        "payload_schema": MARKET_SNAPSHOT_FACT,
        "symbols": [SPY_SYMBOL],
    }
)

_BAR_FIELDS = (
    "provider",
    "symbol",
    "timeframe",
    "feed",
    "adjustment",
    "currency",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "vwap",
    "retrieved_at",
)
_SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=SPY_SUBJECT_ID)
_UNCERTAINTY = {"iex": ["iex_partial_volume"], "sip": []}


def _within_regular_hours_window(start: datetime, end: datetime) -> bool:
    """Monday-Friday, 09:30-16:00 America/New_York clock time only. This does
    not prove the exchange was open, that the date was a trading session, or
    that a shortened session was complete: no exchange calendar is approved."""
    s, e = start.astimezone(EASTERN), end.astimezone(EASTERN)
    return (
        s.date() == e.date()
        and s.weekday() < 5
        and REGULAR_OPEN <= s.time()
        and e.time() <= REGULAR_CLOSE
    )


def _check_bar_reference(reference: Any, values: Mapping[str, Any], start: datetime) -> None:
    if isinstance(reference, DuckDbRowReference):
        if reference.table is not DuckDbTable.MARKET_BARS:
            raise fail("source_reference_mismatch")
        key = {c.column: c.value for c in reference.primary_key}
        for column in ("provider", "symbol", "timeframe", "feed", "adjustment", "currency"):
            if key[column] != values[column]:
                raise fail("source_reference_mismatch")
        if parse_utc(key["bar_timestamp"], reason="source_reference_mismatch") != start:
            raise fail("source_reference_mismatch")
        return
    if isinstance(reference, ProviderRequestReference):
        if (
            reference.endpoint_class is not EndpointClass.ALPACA_STOCK_BARS
            or reference.provider != values["provider"]
            or reference.feed is None
            or reference.feed.value != values["feed"]
            or reference.response_class is not ResponseClass.OK
        ):
            raise fail("source_reference_mismatch")
        return
    raise fail("source_reference_type_not_allowed")


def adapt_market_bar(bar: Any, *, reference: Any, context: AdapterContext) -> EvidenceItem:
    """One validated Alpaca bar (``alpaca_bars.Bar`` or an object with the
    same fields) as a ``confirmed_fact``. ``reference`` is the stored row or
    the provider request it came from."""
    require_configuration(context, BARS_CONFIGURATION)
    require_price_schema(context.registry, MARKET_BAR_FACT)
    values = source_fields(bar, _BAR_FIELDS)
    if values["provider"] != "alpaca" or values["symbol"] != SPY_SYMBOL:
        raise fail("symbol_mismatch")
    if values["timeframe"] not in BAR_TIMEFRAMES:
        raise fail("unsupported_timeframe")
    if values["feed"] not in BAR_FEEDS:
        raise fail("unknown_feed")
    if values["adjustment"] != "raw" or values["currency"] != "USD":
        raise fail("unsupported_bar_variant")
    start = parse_utc(values["timestamp"])
    end = start + BAR_TIMEFRAMES[values["timeframe"]]
    retrieved = parse_utc(values["retrieved_at"])
    refuse_spy_holdout(eastern_date(start), eastern_date(end), eastern_date(retrieved))
    if retrieved < end:
        raise fail("bar_not_complete")  # retrieved before the bar closed
    _check_bar_reference(reference, values, start)
    in_window = _within_regular_hours_window(start, end)
    payload = parse_model(
        MarketBarFact,
        {
            "provider": values["provider"],
            "symbol": values["symbol"],
            "timeframe": values["timeframe"],
            "feed": values["feed"],
            "adjustment": values["adjustment"],
            "currency": values["currency"],
            "bar_start_utc": start,
            "bar_end_utc": end,
            "within_regular_hours_window": in_window,
            "open": canonical_decimal(values["open"]),
            "high": canonical_decimal(values["high"]),
            "low": canonical_decimal(values["low"]),
            "close": canonical_decimal(values["close"]),
            "volume": values["volume"],
            "trade_count": values["trade_count"],
            "vwap": None if values["vwap"] is None else canonical_decimal(values["vwap"]),
        },
        "source_invalid",
    )
    o, h, lo, c = (values[k] for k in ("open", "high", "low", "close"))
    if not (h >= lo and h >= o and h >= c and lo <= o and lo <= c and lo > 0):
        raise fail("bar_ohlc_inconsistent")
    session = eastern_date(start)
    # The window subject keys the New York date for bundles and lineage; like
    # the window flag, it is not exchange-calendar validation.
    subjects = [_SPY, window_subject(session)] if in_window else [_SPY]
    return build_item(
        context,
        producer_id=BARS_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=MARKET_BAR_FACT,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=subjects,
        effective_at=end,
        observed_at=end,
        temporal_scope=EvidenceTemporalScope(session_date=session if in_window else None),
        references=[reference],
        tier=SourceTier.LICENSED_MARKET_DATA,
        uncertainty=_UNCERTAINTY[values["feed"]],
    )


# --- Snapshot ---------------------------------------------------------------------------------


def _object(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise fail("source_invalid")
    return value


def _size(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise fail("source_invalid")
    return value


def adapt_market_snapshot(
    snapshot: Any,
    *,
    symbol: str,
    feed: str,
    reference: Any,
    context: AdapterContext,
) -> EvidenceItem:
    """One Alpaca snapshot response (``get_snapshot``'s JSON object) as a
    ``confirmed_fact``. Only the latest trade and latest quote are read; the
    minute and daily bars are not used. An absent quote is reported as a
    partial fact naming the missing component."""
    require_configuration(context, SNAPSHOT_CONFIGURATION)
    require_price_schema(context.registry, MARKET_SNAPSHOT_FACT)
    if symbol != SPY_SYMBOL:
        raise fail("symbol_mismatch")
    if feed not in SNAPSHOT_FEEDS:
        raise fail("unknown_feed")
    if (
        not isinstance(reference, ProviderRequestReference)
        or reference.endpoint_class is not EndpointClass.ALPACA_STOCK_SNAPSHOT
        or reference.provider != "alpaca"
        or reference.feed is None
        or reference.feed.value != feed
        or reference.response_class is not ResponseClass.OK
    ):
        raise fail("source_reference_mismatch")
    body = _object(snapshot)
    trade = _object(body.get("latestTrade"))
    trade_at = parse_utc(trade.get("t"))
    trade_price = decimal_from_json_number(trade.get("p"))
    trade_size = _size(trade.get("s"))
    quote_raw = body.get("latestQuote")
    quote_at = bid = bid_size = ask = ask_size = None
    if quote_raw is not None:
        quote = _object(quote_raw)
        quote_at = parse_utc(quote.get("t"))
        bid, ask = (
            decimal_from_json_number(quote.get("bp")),
            decimal_from_json_number(quote.get("ap")),
        )
        bid_size, ask_size = _size(quote.get("bs")), _size(quote.get("as"))
    times = [t for t in (trade_at, quote_at) if t is not None]
    refuse_spy_holdout(*(t.date() for t in times), *(eastern_date(t) for t in times))
    if max(times) > reference.requested_at_utc + timedelta(minutes=5):
        raise fail("future_timestamp_detected")
    payload = parse_model(
        MarketSnapshotFact,
        {
            "provider": "alpaca",
            "symbol": symbol,
            "feed": feed,
            "trade_at_utc": trade_at,
            "trade_price": canonical_decimal(trade_price),
            "trade_size": trade_size,
            "quote_at_utc": quote_at,
            "bid_price": None if bid is None else canonical_decimal(bid),
            "bid_size": bid_size,
            "ask_price": None if ask is None else canonical_decimal(ask),
            "ask_size": ask_size,
        },
        "source_invalid",
    )
    availability = None
    if quote_raw is None:
        availability = EvidenceAvailability(
            state=AvailabilityState.PARTIAL,
            reason_code="missing_data",
            missing_components=["latest_quote"],
        )
    return build_item(
        context,
        producer_id=SNAPSHOT_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=MARKET_SNAPSHOT_FACT,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[_SPY],
        effective_at=max(times),
        observed_at=min(times),
        temporal_scope=EvidenceTemporalScope(),
        references=[reference],
        tier=SourceTier.LICENSED_MARKET_DATA,
        uncertainty=_UNCERTAINTY[feed],
        availability=availability,
    )


def missing_market_bars(
    *, checked_at: datetime, query_sha256: str, context: AdapterContext
) -> EvidenceItem:
    """No bar was returned or stored for a requested SPY window: an explicit
    ``missing_evidence`` item with the existing ``bars_missing`` reason."""
    require_configuration(context, BARS_CONFIGURATION)
    refuse_spy_holdout(eastern_date(checked_at))
    return missing_evidence(
        context,
        producer_id=BARS_PRODUCER,
        expected_subject_id=SPY_SUBJECT_ID,
        checked_at=checked_at,
        query_sha256=query_sha256,
        reason_code="bars_missing",
        payload_models=ADAPTER_PAYLOAD_MODELS,
        tier=SourceTier.LICENSED_MARKET_DATA,
    )
