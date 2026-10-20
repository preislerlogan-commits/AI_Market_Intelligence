"""Adapter for SPY option-chain snapshots (registry ``alpaca_option_chain_snapshot``).

One validated chain snapshot (``alpaca_options_chain.OptionChainSnapshot`` or
an object with the same fields) becomes one ``confirmed_fact``. Only
provider-reported fields are copied.

- **One schema per feed, chosen from the feed itself.** ``opra`` quotes become
  ``option_quote_opra_fact.v1`` and ``indicative`` quotes
  ``option_quote_indicative_fact.v1``; no caller can choose otherwise.
  Indicative quotes also carry the ``indicative_feed`` uncertainty code and
  are never presented as ``opra``.
- **Registry permissions are checked, never widened.** Both schemas must be
  declared SPY price content and emitted by this producer as confirmed facts,
  and no rule may make indicative quotes machine-decision eligible; any other
  registry is refused. Producer version, freshness policy and eligibility
  then come from the registry. Whether a contract is operationally eligible
  is decided only by the deterministic Contract Selector, whose feed gate
  keeps indicative data out (``feed_not_allowed``) or research-only.
- **A crossed quote (bid above ask) is a provider observation.** It is kept
  as reported and marked ``crossed_market``; the selector, not this adapter,
  rejects it (``crossed_quote``).
- **Refused:** malformed or inconsistent OCC symbols, impossible timestamps,
  negative or non-finite prices, unknown feeds, producers, schemas and
  configurations, and source references that do not name this snapshot.
- Nothing is calculated: no sentiment, direction, return, probability,
  eligibility or recommendation.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from types import MappingProxyType
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
    SPY_SUBJECT_ID,
    SPY_SYMBOL,
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    canonical_decimal,
    eastern_date,
    fail,
    parse_model,
    parse_utc,
    refuse_spy_holdout,
    require_configuration,
    require_price_schema,
    source_fields,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    OPTION_QUOTE_INDICATIVE_FACT,
    OPTION_QUOTE_OPRA_FACT,
)

OPTION_CHAIN_PRODUCER = "alpaca_option_chain_snapshot"
OPTION_FEED_SCHEMAS = MappingProxyType(
    {"opra": OPTION_QUOTE_OPRA_FACT, "indicative": OPTION_QUOTE_INDICATIVE_FACT}
)
OPTION_FEEDS = frozenset(OPTION_FEED_SCHEMAS)
OPTION_CHAIN_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": OPTION_CHAIN_PRODUCER,
        "payload_schemas": dict(OPTION_FEED_SCHEMAS),
        "underlying": SPY_SYMBOL,
        "crossed_market": "preserved_and_marked",
    }
)
_OCC = re.compile(r"^(?P<root>SPY)(?P<yymmdd>\d{6})(?P<cp>[CP])(?P<strike>\d{8})$")
_SNAPSHOT_FIELDS = (
    "provider",
    "underlying",
    "feed",
    "contract_symbol",
    "expiration_date",
    "option_type",
    "strike_price",
    "quote_timestamp",
    "bid_price",
    "bid_size",
    "ask_price",
    "ask_size",
    "implied_volatility",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "retrieved_at",
)
_GREEKS = ("implied_volatility", "delta", "gamma", "theta", "vega", "rho")
_SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=SPY_SUBJECT_ID)


def _check_symbol(values: dict[str, Any]) -> date:
    """The OCC symbol must be well formed and agree with every parsed field."""
    symbol = values["contract_symbol"]
    match = _OCC.fullmatch(symbol) if isinstance(symbol, str) else None
    if match is None:
        raise fail("contract_symbol_invalid")
    try:
        expiration = date.fromisoformat(values["expiration_date"])
        encoded = date(
            2000 + int(match["yymmdd"][:2]), int(match["yymmdd"][2:4]), int(match["yymmdd"][4:])
        )
    except (TypeError, ValueError):
        raise fail("contract_symbol_invalid") from None
    strike = values["strike_price"]
    if (
        encoded != expiration
        or {"C": "call", "P": "put"}[match["cp"]] != values["option_type"]
        or not isinstance(strike, Decimal)
        or not strike.is_finite()
        or strike <= 0
        or Decimal(int(match["strike"])) / 1000 != strike
    ):
        raise fail("contract_symbol_inconsistent")
    return expiration


def _price(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise fail("price_invalid")
    return canonical_decimal(value, reason="price_invalid")


def _greek(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite():
        raise fail("source_invalid")
    return canonical_decimal(value)


def _size(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise fail("size_invalid")
    return value


def _check_reference(reference: Any, values: dict[str, Any], retrieved) -> None:
    """The reference must name this exact snapshot (stored row) or the
    option-chain request it came from, on the same feed."""
    if isinstance(reference, DuckDbRowReference):
        if reference.table not in (
            DuckDbTable.OPTION_CHAIN_SNAPSHOTS,
            DuckDbTable.OPTION_CHAIN_SNAPSHOT_BATCH_ITEMS,
        ):
            raise fail("source_reference_mismatch")
        key = {c.column: c.value for c in reference.primary_key}
        for column in ("provider", "underlying", "feed", "contract_symbol"):
            if key[column] != values[column]:
                raise fail("source_reference_mismatch")
        if parse_utc(key["retrieved_at"], reason="source_reference_mismatch") != retrieved:
            raise fail("source_reference_mismatch")
        return
    if isinstance(reference, ProviderRequestReference):
        if (
            reference.endpoint_class is not EndpointClass.ALPACA_OPTION_CHAIN
            or reference.provider != values["provider"]
            or reference.feed is None
            or reference.feed.value != values["feed"]
            or reference.response_class is not ResponseClass.OK
            or reference.requested_at_utc > retrieved
        ):
            raise fail("source_reference_mismatch")
        return
    raise fail("source_reference_type_not_allowed")


def require_option_feed_permissions(registry: Any) -> None:
    """Refuse a registry whose option-quote permissions are not separated by
    feed: both schemas declared as SPY price content, each emitted by the
    chain producer as a confirmed fact, and no rule anywhere making an
    indicative quote machine-decision eligible."""
    for schema in OPTION_FEED_SCHEMAS.values():
        require_price_schema(registry, schema)
    producer = next((p for p in registry.producers if p.producer_id == OPTION_CHAIN_PRODUCER), None)
    if producer is None:
        raise fail("unknown_producer")
    kinds = {r.payload_schema_id: r.evidence_kind for r in producer.emission_rules}
    if any(kinds.get(s) is not EvidenceKind.CONFIRMED_FACT for s in OPTION_FEED_SCHEMAS.values()):
        raise fail("option_feed_permissions_invalid")
    for entry in registry.producers:
        for rule in entry.emission_rules:
            if (
                rule.payload_schema_id == OPTION_QUOTE_INDICATIVE_FACT
                and rule.machine_decision_eligible
            ):
                raise fail("option_feed_permissions_invalid")


def adapt_option_quote(snapshot: Any, *, reference: Any, context: AdapterContext) -> EvidenceItem:
    """One option-chain snapshot as a ``confirmed_fact`` under its feed's
    schema, effective at its batch retrieval instant and observed at its
    provider quote time."""
    require_configuration(context, OPTION_CHAIN_CONFIGURATION)
    require_option_feed_permissions(context.registry)
    values = source_fields(snapshot, _SNAPSHOT_FIELDS)
    if values["provider"] != "alpaca" or values["underlying"] != SPY_SYMBOL:
        raise fail("symbol_mismatch")
    if values["feed"] not in OPTION_FEEDS:
        raise fail("unknown_feed")
    if values["option_type"] not in ("call", "put"):
        raise fail("source_invalid")
    expiration = _check_symbol(values)
    retrieved = parse_utc(values["retrieved_at"])
    quoted = None if values["quote_timestamp"] is None else parse_utc(values["quote_timestamp"])
    if quoted is not None and quoted > retrieved:
        raise fail("future_timestamp_detected")  # quoted after it was retrieved
    refuse_spy_holdout(
        expiration, eastern_date(retrieved), *([eastern_date(quoted)] if quoted else [])
    )
    bid, ask = _price(values["bid_price"]), _price(values["ask_price"])
    # Kept as reported; the selector rejects it as ``crossed_quote``.
    crossed = bid is not None and ask is not None and Decimal(bid) > Decimal(ask)
    _check_reference(reference, values, retrieved)
    schema = OPTION_FEED_SCHEMAS[values["feed"]]
    payload = parse_model(
        ADAPTER_PAYLOAD_MODELS[schema],
        {
            "provider": values["provider"],
            "underlying": values["underlying"],
            "feed": values["feed"],
            "contract_symbol": values["contract_symbol"],
            "expiration_date": expiration,
            "option_type": values["option_type"],
            "strike_price": canonical_decimal(values["strike_price"]),
            "quote_at_utc": quoted,
            "bid_price": bid,
            "bid_size": _size(values["bid_size"]),
            "ask_price": ask,
            "ask_size": _size(values["ask_size"]),
            **{name: _greek(values[name]) for name in _GREEKS},
            "retrieved_at_utc": retrieved,
        },
        "source_invalid",
    )
    availability = None
    if bid is None or ask is None:
        # A missing side is reported as missing, never as zero.
        availability = EvidenceAvailability(
            state=AvailabilityState.PARTIAL,
            reason_code="missing_data",
            missing_components=["quote"],
        )
    return build_item(
        context,
        producer_id=OPTION_CHAIN_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=schema,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[
            EvidenceSubject(
                subject_type=SubjectType.OPTION_CONTRACT,
                subject_id=f"option:osi:{values['contract_symbol']}",
            ),
            _SPY,
        ],
        effective_at=retrieved,
        observed_at=quoted or retrieved,
        temporal_scope=EvidenceTemporalScope(),
        references=[reference],
        tier=SourceTier.LICENSED_MARKET_DATA,
        uncertainty=sorted(
            [
                *(["crossed_market"] if crossed else []),
                *(["indicative_feed"] if values["feed"] == "indicative" else []),
            ]
        ),
        availability=availability,
    )
