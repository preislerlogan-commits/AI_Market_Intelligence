"""Offline option-chain and Contract Selector evidence adapters: valid OPRA and
indicative chain evidence, strict quote/symbol/timestamp/reference validation,
registry-derived permissions, deterministic identity, exact selector lineage,
selector authority (no insertion, omission, promotion or rejected-symbol
exposure), every selector outcome, and isolation. Synthetic data and a
labelled synthetic test registry only."""

from __future__ import annotations

import ast
import builtins
import inspect
import json
import socket
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.contract_selection.contracts import (
    ContractSelectorResult,
    FeedProvenance,
    RejectionReason,
    SelectorStatus,
)
from market_intelligence.contract_selection.selector import select_eligible_contracts
from market_intelligence.evidence.contracts import (
    CalculationInputReference,
    DuckDbRowReference,
    EvidenceItem,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    DuckDbTable,
    EndpointClass,
    EvidenceKind,
    SubjectType,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.validation import validate_item
from market_intelligence.evidence_adapters import contract_selector as cs
from market_intelligence.evidence_adapters import options as opt
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    OPTION_QUOTE_INDICATIVE_FACT,
    OPTION_QUOTE_OPRA_FACT,
)
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon
from market_intelligence.tests import evidence_adapter_fixtures as fx

PACKAGE = Path(opt.__file__).parent
REJECTED = "SPY270112C00682000"  # wide spread in ``fx.chain_snapshots``


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _quote(snapshot=None, *, reference=None, registry=None, configuration=None):
    snapshot = snapshot or fx.option_snapshot()
    return opt.adapt_option_quote(
        snapshot,
        reference=reference or fx.chain_reference(snapshot),
        context=fx.context(configuration or opt.OPTION_CHAIN_CONFIGURATION, registry),
    )


def _chain(feed="opra", **kwargs):
    return [_quote(s) for s in fx.chain_snapshots(feed, **kwargs)]


def _run(feed="opra", *, horizon=None, allow_indicative=False, all_wide=False):
    items = _chain(feed, all_wide=all_wide)
    selector_input = fx.selector_input(
        cs.option_chain_batch(items), horizon=horizon, allow_indicative=allow_indicative
    )
    result = select_eligible_contracts(selector_input, generated_at=fx.CHAIN_AS_OF)
    return items, selector_input, result


def _adapt(result, selector_input, items, *, configuration=None, registry=None):
    identity = configuration or cs.selector_configuration_identity(result.config_snapshot)
    return cs.adapt_selector_result(
        result,
        selector_input=selector_input,
        chain_items=items,
        context=fx.context(identity, registry),
    )


def _selector_item(**kwargs):
    items, selector_input, result = _run(**kwargs)
    return items, selector_input, result, _adapt(result, selector_input, items)


def _round_trip(item: EvidenceItem, registry=None) -> None:
    assert EvidenceItem.model_validate_json(item.model_dump_json()) == item
    validate_item(item, registry or fx.make_test_registry(), payload_models=ADAPTER_PAYLOAD_MODELS)


# --- Option-chain adapter: valid evidence -------------------------------------------------------


def test_opra_quote_becomes_a_registry_governed_confirmed_fact():
    snapshot = fx.option_snapshot()
    item = _quote(snapshot)
    registry = fx.make_test_registry()
    producer = next(p for p in registry.producers if p.producer_id == opt.OPTION_CHAIN_PRODUCER)
    assert item.evidence_kind is EvidenceKind.CONFIRMED_FACT
    assert item.payload_schema_id == OPTION_QUOTE_OPRA_FACT
    assert item.provenance.producer_id == opt.OPTION_CHAIN_PRODUCER
    assert item.provenance.producer_version in producer.producer_versions
    assert item.provenance.configuration_identity == opt.OPTION_CHAIN_CONFIGURATION
    assert item.freshness_policy_id == "fp.option_chain_selector.v1"  # from the registry rule
    assert item.machine_decision_eligible is True  # registry-derived, not set here
    assert item.payload == {
        "provider": "alpaca",
        "underlying": "SPY",
        "feed": "opra",
        "contract_symbol": "SPY270112C00680000",
        "expiration_date": "2027-01-12",
        "option_type": "call",
        "strike_price": "680",
        "quote_at_utc": "2027-01-12T14:57:58.250000Z",
        "bid_price": "1",
        "bid_size": 10,
        "ask_price": "1.1",
        "ask_size": 10,
        "implied_volatility": "0.2",
        "delta": "0.4",
        "gamma": "0.05",
        "theta": "-0.1",
        "vega": "0.1",
        "rho": "0.01",
        "retrieved_at_utc": "2027-01-12T14:58:00.000000Z",
    }
    assert [(s.subject_type, s.subject_id) for s in item.subjects] == [
        (SubjectType.OPTION_CONTRACT, "option:osi:SPY270112C00680000"),
        (SubjectType.INSTRUMENT, "instrument:us_equity:SPY"),
    ]
    assert item.effective_at_utc == fx.CHAIN_RETRIEVED
    assert item.provenance.source_observed_at_utc.isoformat().startswith("2027-01-12T14:57:58")
    assert item.provenance.source_references == [fx.chain_reference(snapshot)]
    assert item.quality.uncertainty_codes == []
    assert item.availability.state is AvailabilityState.AVAILABLE
    _round_trip(item)


def test_indicative_quote_is_never_presented_as_opra():
    item = _quote(fx.option_snapshot(feed="indicative"))
    assert item.payload_schema_id == OPTION_QUOTE_INDICATIVE_FACT
    assert item.payload["feed"] == "indicative"
    assert item.quality.uncertainty_codes == ["indicative_feed"]
    # Its own registry rule, never machine-decision eligible.
    assert item.machine_decision_eligible is False
    _round_trip(item)


def test_the_schema_is_chosen_from_the_feed_alone():
    assert set(inspect.signature(opt.adapt_option_quote).parameters) == {
        "snapshot",
        "reference",
        "context",
    }
    assert dict(opt.OPTION_FEED_SCHEMAS) == {
        "opra": OPTION_QUOTE_OPRA_FACT,
        "indicative": OPTION_QUOTE_INDICATIVE_FACT,
    }
    # Each schema's model admits only its own feed.
    opra = _quote().payload
    indicative = _quote(fx.option_snapshot(feed="indicative")).payload
    with pytest.raises(ValueError):
        ADAPTER_PAYLOAD_MODELS[OPTION_QUOTE_OPRA_FACT].model_validate(indicative)
    with pytest.raises(ValueError):
        ADAPTER_PAYLOAD_MODELS[OPTION_QUOTE_INDICATIVE_FACT].model_validate(opra)


def test_registries_that_merge_or_widen_feed_permissions_are_refused():
    widened = fx.make_test_registry(indicative_eligible=True)
    assert _reason(_quote, registry=widened) == "option_feed_permissions_invalid"
    registry = fx.make_test_registry()
    merged = registry.model_copy(
        update={
            "producers": [
                p.model_copy(
                    update={
                        "emission_rules": [
                            r
                            for r in p.emission_rules
                            if r.payload_schema_id != OPTION_QUOTE_INDICATIVE_FACT
                        ]
                    }
                )
                for p in registry.producers
            ]
        }
    )
    assert _reason(_quote, registry=merged) == "option_feed_permissions_invalid"
    items, selector_input, result = _run(feed="indicative", allow_indicative=True)
    assert (
        _reason(_adapt, result, selector_input, items, registry=widened)
        == "option_feed_permissions_invalid"
    )


def test_quote_evidence_is_deterministic():
    first, second = _quote(), _quote()
    assert first.item_id == second.item_id
    assert first.model_dump_json() == second.model_dump_json()
    assert _quote(fx.option_snapshot(feed="indicative")).item_id != first.item_id


def test_registry_eligibility_is_derived_never_widened():
    registry = fx.make_test_registry(eligible=False)
    item = _quote(registry=registry)
    assert item.machine_decision_eligible is False
    _round_trip(item, registry)


def test_provider_request_reference_is_accepted_for_the_same_feed():
    reference = fx.provider_reference(
        EndpointClass.ALPACA_OPTION_CHAIN, feed="opra", at=fx.CHAIN_RETRIEVED
    )
    assert _quote(reference=reference).provenance.source_references == [reference]


# --- Option-chain adapter: missing and invalid quotes -------------------------------------------


@pytest.mark.parametrize("side", ["bid", "ask"])
def test_missing_quote_side_is_partial_never_zero(side):
    item = _quote(fx.option_snapshot(**{side: None}))
    assert item.payload[f"{side}_price"] is None
    assert item.availability.state is AvailabilityState.PARTIAL
    assert item.availability.reason_code == "missing_data"
    assert item.availability.missing_components == ["quote"]
    _round_trip(item)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"bid": "-0.01"}, "price_invalid"),
        ({"ask": "-1"}, "price_invalid"),
        ({"bid": "NaN"}, "price_invalid"),
        ({"bid_price": 1.0}, "price_invalid"),  # floats are never accepted
        ({"ask_price": "1.10"}, "price_invalid"),
        ({"bid_size": -1}, "size_invalid"),
        ({"ask_size": True}, "size_invalid"),
        ({"delta": 0.4}, "source_invalid"),
    ],
)
def test_invalid_prices_and_sizes_are_refused(overrides, reason):
    assert _reason(_quote, fx.option_snapshot(**overrides)) == reason


def test_crossed_quote_is_kept_marked_and_rejected_only_by_the_selector():
    crossed = fx.option_snapshot("SPY270112C00683000", bid="1.20", ask="1.10")
    item = _quote(crossed)
    assert item.evidence_kind is EvidenceKind.CONFIRMED_FACT
    assert (item.payload["bid_price"], item.payload["ask_price"]) == ("1.2", "1.1")
    assert item.quality.uncertainty_codes == ["crossed_market"]
    _round_trip(item)
    items = [*_chain(), item]
    selector_input = fx.selector_input(cs.option_chain_batch(items))
    result = select_eligible_contracts(selector_input, generated_at=fx.CHAIN_AS_OF)
    assert result.rejection_counts[RejectionReason.CROSSED_QUOTE] == 1
    selector_item = _adapt(result, selector_input, items)
    assert "SPY270112C00683000" not in json.dumps(selector_item.payload)
    assert selector_item.payload["rejection_counts"]["crossed_quote"] == 1


def test_locked_market_is_kept_as_reported():
    item = _quote(fx.option_snapshot(bid="1.10", ask="1.10"))
    assert item.payload["bid_price"] == item.payload["ask_price"] == "1.1"


# --- Option-chain adapter: symbols, timestamps, feeds, references ---------------------------------


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"contract_symbol": "SPY 270112C00680000"}, "contract_symbol_invalid"),
        ({"contract_symbol": "QQQ270112C00680000"}, "contract_symbol_invalid"),
        ({"contract_symbol": "SPY271312C00680000"}, "contract_symbol_invalid"),
        ({"expiration_date": "2027-01-13"}, "contract_symbol_inconsistent"),
        ({"option_type": "put"}, "contract_symbol_inconsistent"),
        ({"strike_price": Decimal("681")}, "contract_symbol_inconsistent"),
        ({"option_type": "straddle"}, "source_invalid"),
        ({"underlying": "QQQ"}, "symbol_mismatch"),
        ({"provider": "other"}, "symbol_mismatch"),
    ],
)
def test_malformed_or_inconsistent_symbols_are_refused(overrides, reason):
    snapshot = fx.option_snapshot(**overrides)
    reference = fx.chain_reference(fx.option_snapshot())
    assert _reason(_quote, snapshot, reference=reference) == reason


@pytest.mark.parametrize("feed", ["sip", "iex", "OPRA", "delayed"])
def test_unsupported_feeds_are_refused(feed):
    assert _reason(_quote, fx.option_snapshot(feed=feed)) == "unknown_feed"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"quote_timestamp": "2027-01-12T14:58:00.000001Z"}, "future_timestamp_detected"),
        ({"quote_timestamp": "2027-01-12T14:57:58"}, "timestamp_invalid"),
        ({"retrieved_at": "yesterday"}, "timestamp_invalid"),
    ],
)
def test_impossible_or_malformed_timestamps_are_refused(overrides, reason):
    snapshot = fx.option_snapshot(**overrides)
    reference = fx.chain_reference(fx.option_snapshot())
    assert _reason(_quote, snapshot, reference=reference) == reason


def test_missing_quote_timestamp_falls_back_to_retrieval():
    item = _quote(fx.option_snapshot(quote_timestamp=None))
    assert item.payload["quote_at_utc"] is None
    assert item.provenance.source_observed_at_utc == fx.CHAIN_RETRIEVED


def test_holdout_dates_are_refused():
    snapshot = fx.option_snapshot(
        "SPY261016C00680000",
        quote_timestamp="2026-10-16T14:57:00Z",
        retrieved_at="2026-10-16T14:58:00Z",
    )
    assert _reason(_quote, snapshot) == "holdout_restricted"


def _reference_with(snapshot, **changes):
    reference = fx.chain_reference(snapshot)
    key = [
        c.model_copy(update={"value": changes[c.column]}) if c.column in changes else c
        for c in reference.primary_key
    ]
    return DuckDbRowReference.model_validate(
        {**reference.model_dump(), "primary_key": [k.model_dump() for k in key]}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"contract_symbol": "SPY270112C00681000"},
        {"feed": "indicative"},
        {"retrieved_at": "2027-01-12T14:59:00Z"},
    ],
)
def test_references_that_name_another_snapshot_are_refused(changes):
    snapshot = fx.option_snapshot()
    reference = _reference_with(snapshot, **changes)
    assert _reason(_quote, snapshot, reference=reference) == "source_reference_mismatch"


def test_references_of_the_wrong_kind_or_feed_are_refused():
    wrong_table = fx.chain_reference(fx.option_snapshot()).model_copy(
        update={"table": DuckDbTable.OPTION_CHAIN_SNAPSHOT_BATCHES}
    )
    assert _reason(_quote, reference=wrong_table) == "source_reference_mismatch"
    indicative_request = fx.provider_reference(
        EndpointClass.ALPACA_OPTION_CHAIN, feed="indicative", at=fx.CHAIN_RETRIEVED
    )
    assert _reason(_quote, reference=indicative_request) == "source_reference_mismatch"
    stock_request = fx.provider_reference(
        EndpointClass.ALPACA_STOCK_SNAPSHOT, feed="iex", at=fx.CHAIN_RETRIEVED
    )
    assert _reason(_quote, reference=stock_request) == "source_reference_mismatch"
    assert _reason(_quote, reference=fx.clock_reference()) == "source_reference_type_not_allowed"


def test_incomplete_source_objects_are_refused():
    context = fx.context(opt.OPTION_CHAIN_CONFIGURATION)
    assert (
        _reason(opt.adapt_option_quote, {"provider": "alpaca"}, reference=None, context=context)
        == "source_incomplete"
    )


# --- Unknown configuration, producer and schema --------------------------------------------------


def test_unknown_configuration_producer_and_undeclared_schema_are_refused():
    assert _reason(_quote, configuration="cfg1_" + "0" * 64) == "unknown_configuration"
    registry = fx.make_test_registry()
    without_producer = registry.model_copy(
        update={
            "producers": [
                p for p in registry.producers if p.producer_id != opt.OPTION_CHAIN_PRODUCER
            ]
        }
    )
    assert _reason(_quote, registry=without_producer) == "unknown_producer"
    undeclared = fx.make_test_registry(price_schemas_declared=False)
    assert _reason(_quote, registry=undeclared) == "price_schema_not_declared"


# --- Selector adapter: outcomes, lineage and separation ------------------------------------------


def test_opra_selector_result_is_a_deterministic_calculation_with_exact_lineage():
    items, selector_input, result, item = _selector_item()
    assert result.status is SelectorStatus.ELIGIBLE and result.feed_is_live_opra
    assert item.evidence_kind is EvidenceKind.DETERMINISTIC_CALCULATION
    assert item.provenance.producer_id == cs.SELECTOR_PRODUCER
    assert item.provenance.configuration_identity in cs.KNOWN_SELECTOR_CONFIGURATIONS
    assert item.freshness_policy_id == "fp.option_chain_selector.v1"
    assert item.provenance.parent_evidence_ids == sorted(i.item_id for i in items)
    (reference,) = item.provenance.source_references
    assert isinstance(reference, CalculationInputReference)
    assert reference.input_record_count == 3
    assert ContractSelectorResult.model_validate(item.payload) == result  # exact, unchanged
    assert [c["contract_symbol"] for c in item.payload["eligible_contracts"]] == [
        "SPY270112C00680000",
        "SPY270112C00681000",
    ]
    assert item.payload["research_only_contracts"] == []
    _round_trip(item)


def test_selector_evidence_is_deterministic():
    first, second = _selector_item()[3], _selector_item()[3]
    assert first.item_id == second.item_id
    assert first.model_dump_json() == second.model_dump_json()


def test_indicative_chain_is_research_only_and_never_eligible():
    items, _, result, item = _selector_item(feed="indicative", allow_indicative=True)
    assert result.status is SelectorStatus.RESEARCH_ONLY
    assert {i.payload_schema_id for i in items} == {OPTION_QUOTE_INDICATIVE_FACT}
    assert not any(i.machine_decision_eligible for i in items)
    assert item.payload["feed"] == "indicative" and item.payload["feed_is_live_opra"] is False
    assert item.payload["eligible_contracts"] == []
    assert len(item.payload["research_only_contracts"]) == 2
    _round_trip(item)


def test_indicative_chain_without_research_permission_returns_nothing():
    _, _, result, item = _selector_item(feed="indicative")
    assert result.status is SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert item.payload["eligible_contracts"] == item.payload["research_only_contracts"] == []
    assert item.payload["rejection_counts"][RejectionReason.FEED_NOT_ALLOWED.value] == 3


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({}, SelectorStatus.ELIGIBLE),
        ({"all_wide": True}, SelectorStatus.NO_ELIGIBLE_CONTRACTS),
        ({"feed": "indicative", "allow_indicative": True}, SelectorStatus.RESEARCH_ONLY),
        ({"horizon": ScenarioHorizon.INDETERMINATE}, SelectorStatus.INDETERMINATE),
    ],
)
def test_every_selector_outcome_is_preserved_exactly(kwargs, status):
    _, _, result, item = _selector_item(**kwargs)
    assert result.status is status
    assert item.payload == result.model_dump(mode="json")
    assert set(item.payload["rejection_counts"]) == {r.value for r in RejectionReason}
    assert (
        sum(item.payload["rejection_counts"].values())
        + len(item.payload["eligible_contracts"])
        + len(item.payload["research_only_contracts"])
        == 3
    )
    _round_trip(item)


def test_rejected_contracts_appear_only_as_aggregate_counts():
    _, _, result, item = _selector_item()
    assert result.rejection_counts[RejectionReason.SPREAD_TOO_WIDE] == 1
    assert REJECTED not in json.dumps(item.payload)
    assert REJECTED not in item.model_dump_json()


# --- Selector authority: tampering is refused ----------------------------------------------------


def _tamper(result, **update):
    return result.model_copy(update=update)


def test_selector_status_and_membership_cannot_be_changed():
    items, selector_input, result = _run()
    eligible = list(result.eligible_contracts)
    cases = {
        "omitted": _tamper(result, eligible_contracts=eligible[:1]),
        "duplicated": _tamper(result, eligible_contracts=eligible + eligible[:1]),
        "demoted": _tamper(result, eligible_contracts=[], research_only_contracts=eligible),
        "combined": _tamper(
            result, eligible_contracts=eligible[:1], research_only_contracts=eligible[1:]
        ),
        "status": _tamper(result, status=SelectorStatus.RESEARCH_ONLY),
    }
    for name, tampered in cases.items():
        reason = _reason(_adapt, tampered, selector_input, items)
        assert reason in {"selector_result_invalid", "selector_sets_combined"}, (name, reason)


def test_research_only_contracts_cannot_be_promoted():
    items, selector_input, result = _run(feed="indicative", allow_indicative=True)
    promoted = _tamper(
        result,
        eligible_contracts=list(result.research_only_contracts),
        research_only_contracts=[],
        status=SelectorStatus.ELIGIBLE,
    )
    assert _reason(_adapt, promoted, selector_input, items) in {
        "selector_result_invalid",
        "selector_outcome_changed",
        "selector_result_mismatch",
    }


def test_inserted_or_rejected_contracts_cannot_be_exposed():
    items, selector_input, result = _run()
    # A run with a looser spread limit returns the contract this run rejected.
    loose = selector_input.config.model_copy(
        update={"max_abs_spread": Decimal("5"), "max_pct_spread": Decimal("5")}
    )
    looser = select_eligible_contracts(
        selector_input.model_copy(update={"config": loose}), generated_at=fx.CHAIN_AS_OF
    )
    exposed = [c for c in looser.eligible_contracts if c.contract_symbol == REJECTED]
    assert exposed
    inserted = _tamper(result, eligible_contracts=[*result.eligible_contracts, *exposed])
    assert _reason(_adapt, inserted, selector_input, items) in {
        "selector_result_invalid",
        "contract_not_returned_by_selector",
    }


def test_rejection_counts_cannot_be_changed():
    items, selector_input, result = _run()
    counts = dict(result.rejection_counts)
    counts[RejectionReason.SPREAD_TOO_WIDE] = 0
    counts[RejectionReason.DELTA_OUTSIDE_RANGE] = 1
    tampered = _tamper(result, rejection_counts=counts)
    assert _reason(_adapt, tampered, selector_input, items) == "rejection_counts_changed"


def test_a_result_for_a_different_configuration_is_refused():
    items, selector_input, result = _run()
    other = selector_input.model_copy(
        update={"config": selector_input.config.model_copy(update={"min_quote_size": 2})}
    )
    other_result = select_eligible_contracts(other, generated_at=fx.CHAIN_AS_OF)
    assert _reason(_adapt, other_result, other, items) == "unknown_configuration"
    assert (
        _reason(_adapt, result, selector_input, items, configuration="cfg1_" + "0" * 64)
        == "unknown_configuration"
    )


def test_selector_needs_a_declared_price_schema_and_registered_producer():
    items, selector_input, result = _run()
    registry = fx.make_test_registry()
    without = registry.model_copy(
        update={
            "producers": [p for p in registry.producers if p.producer_id != cs.SELECTOR_PRODUCER]
        }
    )
    assert _reason(_adapt, result, selector_input, items, registry=without) == "unknown_producer"
    undeclared = fx.make_test_registry(price_schemas_declared=False)
    assert (
        _reason(_adapt, result, selector_input, items, registry=undeclared)
        == "price_schema_not_declared"
    )


# --- Selector lineage: exact parents, no inference ---------------------------------------------


def test_cited_parents_must_be_exactly_the_evaluated_chain():
    items, selector_input, result = _run()
    unused = _quote(fx.option_snapshot("SPY270112C00690000"))
    assert _reason(_adapt, result, selector_input, [*items, unused]) == (
        "parent_not_used_by_selector"
    )
    assert _reason(_adapt, result, selector_input, items[:2]) == "lineage_incomplete"
    altered = _quote(fx.option_snapshot("SPY270112C00682000", ask="2.90"))
    assert _reason(_adapt, result, selector_input, [*items[:2], altered]) == (
        "parent_quote_mismatch"
    )


def test_inference_and_non_chain_parents_are_refused():
    items, selector_input, result = _run()
    inference = items[2].model_copy(update={"evidence_kind": EvidenceKind.CURRENT_INFERENCE})
    assert _reason(_adapt, result, selector_input, [*items[:2], inference]) == (
        "parent_kind_not_allowed"
    )
    other = items[2].model_copy(update={"payload_schema_id": "market_bar_fact.v1"})
    assert _reason(_adapt, result, selector_input, [*items[:2], other]) == (
        "parent_not_option_chain_fact"
    )
    relabelled = items[2].model_copy(update={"payload_schema_id": OPTION_QUOTE_INDICATIVE_FACT})
    assert _reason(_adapt, result, selector_input, [*items[:2], relabelled]) == (
        "parent_not_option_chain_fact"
    )


def test_chain_evidence_must_be_one_batch():
    items = _chain()
    indicative = _quote(fx.option_snapshot("SPY270112C00690000", feed="indicative"))
    assert _reason(cs.option_chain_batch, [items[0], indicative]) == "chain_evidence_not_one_batch"
    assert _reason(cs.option_chain_batch, [items[0], items[0]]) == "duplicate_chain_evidence"
    assert _reason(cs.option_chain_batch, []) == "no_chain_evidence"


def test_batch_is_rebuilt_unchanged_from_evidence():
    batch = cs.option_chain_batch(list(reversed(_chain())))
    assert batch.feed is FeedProvenance.OPRA
    assert batch.retrieved_at == fx.CHAIN_RETRIEVED
    assert [c.contract_symbol for c in batch.contracts] == sorted(
        s.contract_symbol for s in fx.chain_snapshots()
    )
    assert batch.contracts[0].ask_price == Decimal("1.10")


# --- Isolation and surfaces --------------------------------------------------------------------


def test_options_adapters_open_no_database_file_or_connection_at_runtime(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("options adapters must not open a database, file or connection")

    snapshots = fx.chain_snapshots()  # import the connector dataclass first
    monkeypatch.setattr(duckdb, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(builtins, "open", refuse)
    items = [_quote(s) for s in snapshots]
    selector_input = fx.selector_input(cs.option_chain_batch(items))
    result = select_eligible_contracts(selector_input, generated_at=fx.CHAIN_AS_OF)
    assert _adapt(result, selector_input, items).evidence_kind is (
        EvidenceKind.DETERMINISTIC_CALCULATION
    )


_FORBIDDEN_SURFACE_WORDS = (
    "order",
    "broker",
    "robinhood",
    "execut",
    "recommend",
    "rank",
    "probab",
    "sentiment",
    "direction",
    "submit",
)


@pytest.mark.parametrize("module", ["options.py", "contract_selector.py"])
def test_no_order_brokerage_execution_or_recommendation_surface(module):
    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.asname or node.name)
    for name in names:
        assert not any(word in name.lower() for word in _FORBIDDEN_SURFACE_WORDS), (module, name)
    payload_fields = set(fx.option_snapshot().__dataclass_fields__) | set(
        ContractSelectorResult.model_fields
    )
    for field in payload_fields:
        assert not any(word in field.lower() for word in _FORBIDDEN_SURFACE_WORDS), field
