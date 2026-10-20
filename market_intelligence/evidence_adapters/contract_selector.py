"""Adapter for deterministic Contract Selector results (registry
``deterministic_contract_selector``, ``spy-contract-selector-1``).

The selector's own ``ContractSelectorResult`` becomes one
``deterministic_calculation`` (``contract_selector_result.v1``, the existing,
unchanged payload) that cites, as parents, exactly the option-chain evidence
items the selector evaluated.

**Selector authority is never shared.** Only the selector assigns a returned
contract to ``eligible`` or ``research_only``; every other contract is
rejected and appears only in the aggregate ``rejection_counts``. This adapter
adds no eligibility logic of its own. It refuses any result that:

- is not exactly what the selector returns for the cited evidence (the
  selector is re-run on the batch rebuilt from that evidence);
- adds, omits, duplicates or reclassifies a returned contract, combines the
  eligible and research-only sets, exposes a rejected contract, or changes
  a rejection count (the Evidence Envelope's permanent selector boundary);
- cites an option-chain item the selector did not use, or omits one it did;
- relies on inference, or on anything but option-chain facts under the
  schema of their own feed;
- returns any eligible contract from an indicative chain (the selector's
  feed gate never does; this is checked again here);
- uses a configuration other than the reviewed default selector
  configurations.

The payload carries no ranking, inference, probability, recommendation or
execution instruction: the selector result has no field for any of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    ContractSelectorResult,
    FeedProvenance,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    SelectorConfig,
    SelectorConfigSnapshot,
    SelectorStatus,
)
from market_intelligence.contract_selection.selector import (
    _config_snapshot,
    select_eligible_contracts,
)
from market_intelligence.evidence.canonical import canonical_json_bytes, canonical_sha256
from market_intelligence.evidence.contracts import (
    CalculationInputReference,
    EvidenceItem,
    EvidenceSubject,
    EvidenceTemporalScope,
)
from market_intelligence.evidence.enums import ConsumerId, EvidenceKind, SourceTier, SubjectType
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_SELECTOR_PAYLOAD_ID,
    CONTRACT_SELECTOR_PRODUCER_ID,
    ContractView,
    validate_contract_view,
)
from market_intelligence.evidence_adapters.common import (
    SPY_SUBJECT_ID,
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    eastern_date,
    fail,
    refuse_spy_holdout,
    require_configuration,
    require_price_schema,
)
from market_intelligence.evidence_adapters.options import (
    OPTION_CHAIN_PRODUCER,
    OPTION_FEED_SCHEMAS,
    require_option_feed_permissions,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    OptionQuoteFact,
)

SELECTOR_PRODUCER = CONTRACT_SELECTOR_PRODUCER_ID
SELECTOR_INPUT_SCHEMA = "spy-contract-selector-1-input"
_SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=SPY_SUBJECT_ID)


def selector_configuration_identity(snapshot: SelectorConfigSnapshot) -> str:
    """The ``cfg1_`` identity of the exact selector configuration a run used."""
    return adapter_configuration_identity(
        {"engine": "spy-contract-selector-1", "config": snapshot.model_dump(mode="json")}
    )


# Only the reviewed default configurations: the operational default, and the
# same with indicative research explicitly allowed. Any other is refused.
KNOWN_SELECTOR_CONFIGURATIONS = frozenset(
    selector_configuration_identity(_config_snapshot(config))
    for config in (SelectorConfig(), SelectorConfig(allow_indicative_for_research=True))
)


def _quote_fact(item: Any) -> OptionQuoteFact:
    if not isinstance(item, EvidenceItem):
        raise fail("source_invalid")
    if item.evidence_kind is EvidenceKind.CURRENT_INFERENCE:
        raise fail("parent_kind_not_allowed")  # inference never feeds the selector
    if (
        item.evidence_kind is not EvidenceKind.CONFIRMED_FACT
        or item.payload_schema_id not in OPTION_FEED_SCHEMAS.values()
        or item.provenance.producer_id != OPTION_CHAIN_PRODUCER
    ):
        raise fail("parent_not_option_chain_fact")
    try:
        # The schema's own model admits only its feed: an indicative quote
        # under the OPRA schema (or the reverse) fails here.
        model = ADAPTER_PAYLOAD_MODELS[item.payload_schema_id]
        return model.model_validate_json(canonical_json_bytes(item.payload))
    except ValidationError:
        raise fail("parent_not_option_chain_fact") from None


def option_chain_batch(chain_items: Sequence[EvidenceItem]) -> OptionChainBatch:
    """The selector's own input batch, rebuilt from option-chain evidence:
    one provider, one feed and one retrieval instant, copied unchanged."""
    if not chain_items:
        raise fail("no_chain_evidence")
    facts = [_quote_fact(item) for item in chain_items]
    first = facts[0]
    for fact in facts[1:]:
        if (fact.provider, fact.feed, fact.retrieved_at_utc) != (
            first.provider,
            first.feed,
            first.retrieved_at_utc,
        ):
            raise fail("chain_evidence_not_one_batch")
    if len({f.contract_symbol for f in facts}) != len(facts):
        raise fail("duplicate_chain_evidence")

    def number(value: str | None) -> Decimal | None:
        return None if value is None else Decimal(value)

    try:
        return OptionChainBatch(
            provider=first.provider,
            feed=FeedProvenance(first.feed),
            retrieved_at=first.retrieved_at_utc,
            contracts=[
                OptionContractQuote(
                    contract_symbol=f.contract_symbol,
                    option_type=OptionType(f.option_type),
                    expiration_date=f.expiration_date,
                    strike_price=Decimal(f.strike_price),
                    bid_price=number(f.bid_price),
                    bid_size=f.bid_size,
                    ask_price=number(f.ask_price),
                    ask_size=f.ask_size,
                    implied_volatility=number(f.implied_volatility),
                    delta=number(f.delta),
                    gamma=number(f.gamma),
                    theta=number(f.theta),
                    vega=number(f.vega),
                    rho=number(f.rho),
                )
                for f in sorted(facts, key=lambda f: f.contract_symbol)
            ],
        )
    except ValidationError:
        raise fail("chain_evidence_invalid") from None


def _check_lineage(selector_input: ContractSelectorInput, chain_items) -> list[str]:
    """The cited items must be exactly the batch the selector evaluated."""
    rebuilt = option_chain_batch(chain_items)
    used = {c.contract_symbol for c in selector_input.batch.contracts}
    cited = {c.contract_symbol for c in rebuilt.contracts}
    if cited - used:
        raise fail("parent_not_used_by_selector")
    if used - cited:
        raise fail("lineage_incomplete")
    by_symbol = {c.contract_symbol: c for c in selector_input.batch.contracts}
    if (
        rebuilt.provider != selector_input.batch.provider
        or rebuilt.feed is not selector_input.batch.feed
        or rebuilt.retrieved_at != selector_input.batch.retrieved_at
        or any(c != by_symbol[c.contract_symbol] for c in rebuilt.contracts)
    ):
        raise fail("parent_quote_mismatch")
    return sorted(item.item_id for item in chain_items)


def _check_authority(result: ContractSelectorResult, selector_input: ContractSelectorInput):
    """The selector alone decides: the result must be its exact output."""
    eligible = [c.contract_symbol for c in result.eligible_contracts]
    research = [c.contract_symbol for c in result.research_only_contracts]
    if eligible and research or set(eligible) & set(research):
        raise fail("selector_sets_combined")
    try:
        ContractSelectorResult.model_validate_json(result.model_dump_json())
    except ValidationError:
        raise fail("selector_result_invalid") from None
    recomputed = select_eligible_contracts(selector_input, generated_at=result.generated_at)
    try:
        validate_contract_view(
            recomputed,
            ContractView(
                consumer_id=ConsumerId.BUNDLE_BUILDER,
                eligible_contracts=eligible,
                research_only_contracts=research,
                rejection_counts=dict(result.rejection_counts),
            ),
        )
    except ValidationError:
        raise fail("selector_result_invalid") from None
    except EvidenceValidationError:
        raise
    if recomputed.model_dump(mode="json") != result.model_dump(mode="json"):
        raise fail("selector_result_mismatch")


def adapt_selector_result(
    result: Any,
    *,
    selector_input: Any,
    chain_items: Sequence[EvidenceItem],
    context: AdapterContext,
) -> EvidenceItem:
    """The selector's result as a ``deterministic_calculation`` citing exactly
    the option-chain evidence it evaluated."""
    if not isinstance(result, ContractSelectorResult) or not isinstance(
        selector_input, ContractSelectorInput
    ):
        raise fail("source_invalid")
    identity = selector_configuration_identity(result.config_snapshot)
    if identity not in KNOWN_SELECTOR_CONFIGURATIONS:
        raise fail("unknown_configuration")
    require_configuration(context, identity)
    require_price_schema(context.registry, CONTRACT_SELECTOR_PAYLOAD_ID)
    require_option_feed_permissions(context.registry)
    parents = _check_lineage(selector_input, chain_items)
    _check_authority(result, selector_input)
    if result.feed is not FeedProvenance.OPRA and (
        result.eligible_contracts or result.status is SelectorStatus.ELIGIBLE
    ):
        raise fail("indicative_contract_eligible")
    returned = [*result.eligible_contracts, *result.research_only_contracts]
    refuse_spy_holdout(
        eastern_date(result.as_of_timestamp),
        eastern_date(result.retrieved_at),
        *(c.expiration_date for c in returned),
        *(c.expiration_date for c in selector_input.batch.contracts),
    )
    return build_item(
        context,
        producer_id=SELECTOR_PRODUCER,
        kind=EvidenceKind.DETERMINISTIC_CALCULATION,
        payload_schema_id=CONTRACT_SELECTOR_PAYLOAD_ID,
        payload=result.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[_SPY],
        effective_at=max(result.as_of_timestamp, result.retrieved_at),
        observed_at=result.retrieved_at,
        temporal_scope=EvidenceTemporalScope(),
        references=[
            CalculationInputReference(
                reference_type="calculation_input",
                input_schema_id=SELECTOR_INPUT_SCHEMA,
                input_sha256=canonical_sha256(selector_input.model_dump(mode="json")),
                input_record_count=len(selector_input.batch.contracts),
            )
        ],
        tier=SourceTier.NOT_APPLICABLE,
        parents=parents,
    )
