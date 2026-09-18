"""Tests for market_intelligence/contract_selection/contracts.py.

Covers strict validation (extra="forbid"), bounded collections, timezone-
aware timestamps, finite numerics, missing-is-never-zero, the SPY-only
constraint, and the fixed-enum-completeness / count-consistency validators
on SelectorConfig and ContractSelectorResult.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    ContractSelectorResult,
    FeedProvenance,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    RejectionReason,
    SelectorConfig,
    SelectorConfigSnapshot,
    SelectorStatus,
)
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon

AS_OF = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)
RETRIEVED_AT = datetime(2026, 9, 16, 14, 58, tzinfo=UTC)
EXPIRY = date(2026, 9, 17)


def _quote(**overrides) -> OptionContractQuote:
    fields = dict(
        contract_symbol="SPY260917C00680000",
        option_type=OptionType.CALL,
        expiration_date=EXPIRY,
        strike_price=Decimal("680"),
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal("1.10"),
        ask_size=10,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.4"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.1"),
        vega=Decimal("0.1"),
        rho=Decimal("0.01"),
    )
    fields.update(overrides)
    return OptionContractQuote(**fields)


def _batch(**overrides) -> OptionChainBatch:
    fields = dict(
        provider="alpaca",
        feed=FeedProvenance.OPRA,
        retrieved_at=RETRIEVED_AT,
        contracts=[_quote()],
    )
    fields.update(overrides)
    return OptionChainBatch(**fields)


def _selector_input(**overrides) -> ContractSelectorInput:
    batch = overrides.pop("batch", None) or _batch()
    fields = dict(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        regime_as_of_timestamp=batch.retrieved_at,
        underlying_price_timestamp=batch.retrieved_at,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        batch=batch,
    )
    fields.update(overrides)
    return ContractSelectorInput(**fields)


# ---------------------------------------------------------------------------
# OptionContractQuote
# ---------------------------------------------------------------------------


def test_quote_accepts_a_fully_populated_contract():
    quote = _quote()
    assert quote.delta == Decimal("0.4")


def test_quote_allows_every_optional_field_to_be_missing():
    quote = _quote(
        bid_price=None, bid_size=None, ask_price=None, ask_size=None,
        implied_volatility=None, delta=None, gamma=None, theta=None, vega=None, rho=None,
    )
    assert quote.delta is None
    assert quote.implied_volatility is None


def test_quote_has_no_open_interest_field():
    assert not hasattr(OptionContractQuote(**_quote().model_dump()), "open_interest")


def test_quote_rejects_extra_fields():
    with pytest.raises(ValidationError):
        OptionContractQuote(**_quote().model_dump(), open_interest=100)


def test_quote_rejects_non_positive_strike():
    with pytest.raises(ValidationError):
        _quote(strike_price=Decimal("0"))


def test_quote_rejects_negative_bid_price():
    with pytest.raises(ValidationError):
        _quote(bid_price=Decimal("-1"))


def test_quote_rejects_negative_bid_size():
    with pytest.raises(ValidationError):
        _quote(bid_size=-1)


def test_quote_rejects_nonfinite_greek():
    with pytest.raises(ValidationError):
        _quote(delta=Decimal("Infinity"))


def test_quote_is_frozen():
    quote = _quote()
    with pytest.raises(ValidationError):
        quote.delta = Decimal("0.5")


# ---------------------------------------------------------------------------
# OptionChainBatch
# ---------------------------------------------------------------------------


def test_batch_requires_utc_aware_retrieved_at():
    with pytest.raises(ValidationError):
        OptionChainBatch(
            provider="alpaca",
            feed=FeedProvenance.OPRA,
            retrieved_at=datetime(2026, 9, 16, 14, 58),  # naive
            contracts=[_quote()],
        )


def test_batch_rejects_duplicate_contract_symbols():
    with pytest.raises(ValidationError):
        _batch(contracts=[_quote(), _quote()])


def test_batch_allows_an_empty_contract_list():
    batch = _batch(contracts=[])
    assert batch.contracts == []


def test_batch_rejects_non_spy_underlying():
    with pytest.raises(ValidationError):
        OptionChainBatch(
            provider="alpaca",
            underlying="QQQ",
            feed=FeedProvenance.OPRA,
            retrieved_at=RETRIEVED_AT,
            contracts=[],
        )


def test_batch_rejects_extra_fields():
    with pytest.raises(ValidationError):
        OptionChainBatch(**_batch().model_dump(), extra_field=1)


# ---------------------------------------------------------------------------
# SelectorConfig
# ---------------------------------------------------------------------------


def test_default_selector_config_is_valid():
    config = SelectorConfig()
    assert set(config.horizon_expiration_windows) == {
        ScenarioHorizon.INTRADAY_30M,
        ScenarioHorizon.INTRADAY_2H,
        ScenarioHorizon.TO_SESSION_CLOSE,
        ScenarioHorizon.NEXT_SESSION,
    }


def test_selector_config_rejects_a_missing_horizon_window():
    windows = dict(SelectorConfig().horizon_expiration_windows)
    del windows[ScenarioHorizon.NEXT_SESSION]
    with pytest.raises(ValidationError):
        SelectorConfig(horizon_expiration_windows=windows)


def test_selector_config_rejects_an_indeterminate_horizon_window():
    windows = dict(SelectorConfig().horizon_expiration_windows)
    windows[ScenarioHorizon.INDETERMINATE] = (0, 1)
    with pytest.raises(ValidationError):
        SelectorConfig(horizon_expiration_windows=windows)


def test_selector_config_rejects_reversed_dte_window():
    windows = dict(SelectorConfig().horizon_expiration_windows)
    windows[ScenarioHorizon.INTRADAY_30M] = (5, 1)
    with pytest.raises(ValidationError):
        SelectorConfig(horizon_expiration_windows=windows)


def test_selector_config_rejects_dte_window_beyond_60_days():
    windows = dict(SelectorConfig().horizon_expiration_windows)
    windows[ScenarioHorizon.NEXT_SESSION] = (1, 61)
    with pytest.raises(ValidationError):
        SelectorConfig(horizon_expiration_windows=windows)


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_moneyness", Decimal("0")),
        ("min_moneyness", Decimal("1.5")),
        ("max_moneyness", Decimal("0.5")),
        ("min_abs_delta", Decimal("-0.1")),
        ("min_abs_delta", Decimal("1.5")),
        ("max_abs_delta", Decimal("-0.1")),
        ("max_abs_spread", Decimal("0")),
        ("max_abs_spread", Decimal("-1")),
        ("max_pct_spread", Decimal("0")),
        ("max_pct_spread", Decimal("1.5")),
    ],
)
def test_selector_config_rejects_out_of_bound_thresholds(field, value):
    with pytest.raises(ValidationError):
        SelectorConfig(**{field: value})


def test_selector_config_rejects_min_delta_above_max_delta():
    with pytest.raises(ValidationError):
        SelectorConfig(min_abs_delta=Decimal("0.7"), max_abs_delta=Decimal("0.5"))


def test_selector_config_allow_indicative_for_research_defaults_to_false():
    config = SelectorConfig()
    assert config.allow_indicative_for_research is False


def test_selector_config_allow_indicative_for_research_can_be_set_true():
    config = SelectorConfig(allow_indicative_for_research=True)
    assert config.allow_indicative_for_research is True


def test_selector_config_rejects_extra_fields():
    with pytest.raises(ValidationError):
        SelectorConfig(unexpected_field=True)


def test_selector_config_is_frozen():
    config = SelectorConfig()
    with pytest.raises(ValidationError):
        config.min_moneyness = Decimal("0.5")


# ---------------------------------------------------------------------------
# ContractSelectorInput
# ---------------------------------------------------------------------------


def test_selector_input_accepts_defaults():
    selector_input = _selector_input()
    assert selector_input.symbol == "SPY"
    assert selector_input.requested_option_type is None
    assert isinstance(selector_input.config, SelectorConfig)


def test_selector_input_requires_utc_aware_as_of_timestamp():
    with pytest.raises(ValidationError):
        _selector_input(as_of_timestamp=datetime(2026, 9, 16, 15, 0))  # naive


def test_selector_input_rejects_non_positive_underlying_price():
    with pytest.raises(ValidationError):
        _selector_input(underlying_price=Decimal("0"))


def test_selector_input_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ContractSelectorInput(**_selector_input().model_dump(), extra_field=1)


def test_selector_input_requires_utc_aware_regime_as_of_timestamp():
    with pytest.raises(ValidationError):
        _selector_input(regime_as_of_timestamp=datetime(2026, 9, 16, 14, 58))  # naive


def test_selector_input_requires_utc_aware_underlying_price_timestamp():
    with pytest.raises(ValidationError):
        _selector_input(underlying_price_timestamp=datetime(2026, 9, 16, 14, 58))  # naive


# ---------------------------------------------------------------------------
# Capture provenance chain: regime_as_of_timestamp <= underlying_price_timestamp
# <= batch.retrieved_at (the final leg, batch.retrieved_at <= as_of_timestamp,
# remains selector.py's own SNAPSHOT_STALE/SNAPSHOT_FROM_FUTURE gate -- see
# test_contract_selector.py).
# ---------------------------------------------------------------------------


def test_provenance_chain_accepts_timestamps_at_the_exact_same_instant():
    """Zero gap everywhere is not a violation of either ordering or lag."""
    selector_input = _selector_input(
        regime_as_of_timestamp=RETRIEVED_AT,
        underlying_price_timestamp=RETRIEVED_AT,
        batch=_batch(retrieved_at=RETRIEVED_AT),
    )
    assert selector_input.regime_as_of_timestamp == RETRIEVED_AT
    assert selector_input.underlying_price_timestamp == RETRIEVED_AT


def test_provenance_chain_rejects_price_timestamp_before_regime_as_of():
    with pytest.raises(ValidationError):
        _selector_input(
            regime_as_of_timestamp=RETRIEVED_AT,
            underlying_price_timestamp=RETRIEVED_AT - timedelta(seconds=1),
        )


def test_provenance_chain_rejects_retrieved_at_before_price_timestamp():
    with pytest.raises(ValidationError):
        _selector_input(
            regime_as_of_timestamp=RETRIEVED_AT - timedelta(seconds=10),
            underlying_price_timestamp=RETRIEVED_AT,
            batch=_batch(retrieved_at=RETRIEVED_AT - timedelta(seconds=1)),
        )


def test_provenance_chain_accepts_gap_exactly_at_the_regime_to_price_bound():
    config = SelectorConfig()
    regime_ts = RETRIEVED_AT - timedelta(seconds=config.max_regime_to_price_gap_seconds)
    selector_input = _selector_input(
        regime_as_of_timestamp=regime_ts,
        underlying_price_timestamp=RETRIEVED_AT,
        batch=_batch(retrieved_at=RETRIEVED_AT),
        config=config,
    )
    assert selector_input.regime_as_of_timestamp == regime_ts


def test_provenance_chain_rejects_gap_one_second_past_the_regime_to_price_bound():
    config = SelectorConfig()
    regime_ts = RETRIEVED_AT - timedelta(seconds=config.max_regime_to_price_gap_seconds + 1)
    with pytest.raises(ValidationError):
        _selector_input(
            regime_as_of_timestamp=regime_ts,
            underlying_price_timestamp=RETRIEVED_AT,
            batch=_batch(retrieved_at=RETRIEVED_AT),
            config=config,
        )


def test_provenance_chain_accepts_gap_exactly_at_the_price_to_chain_bound():
    config = SelectorConfig()
    price_ts = RETRIEVED_AT - timedelta(seconds=config.max_price_to_chain_gap_seconds)
    selector_input = _selector_input(
        regime_as_of_timestamp=price_ts,
        underlying_price_timestamp=price_ts,
        batch=_batch(retrieved_at=RETRIEVED_AT),
        config=config,
    )
    assert selector_input.underlying_price_timestamp == price_ts


def test_provenance_chain_rejects_gap_one_second_past_the_price_to_chain_bound():
    config = SelectorConfig()
    price_ts = RETRIEVED_AT - timedelta(seconds=config.max_price_to_chain_gap_seconds + 1)
    with pytest.raises(ValidationError):
        _selector_input(
            regime_as_of_timestamp=price_ts,
            underlying_price_timestamp=price_ts,
            batch=_batch(retrieved_at=RETRIEVED_AT),
            config=config,
        )


def test_provenance_chain_ignores_the_final_leg_left_to_selector_py():
    """batch.retrieved_at vs. as_of_timestamp is deliberately NOT enforced
    here -- a batch retrieved long after as_of_timestamp (or long before it)
    must still construct successfully; selector.py's own freshness gate is
    what reports it."""
    selector_input = _selector_input(
        regime_as_of_timestamp=RETRIEVED_AT,
        underlying_price_timestamp=RETRIEVED_AT,
        batch=_batch(retrieved_at=RETRIEVED_AT),
        as_of_timestamp=RETRIEVED_AT + timedelta(days=1),
    )
    assert selector_input.as_of_timestamp == RETRIEVED_AT + timedelta(days=1)


# ---------------------------------------------------------------------------
# SelectorConfig: new provenance-lag thresholds
# ---------------------------------------------------------------------------


def test_selector_config_provenance_lag_defaults():
    config = SelectorConfig()
    assert config.max_regime_to_price_gap_seconds == 300
    assert config.max_price_to_chain_gap_seconds == 60
    assert config.max_quote_age_seconds == 300


@pytest.mark.parametrize(
    "field",
    ["max_regime_to_price_gap_seconds", "max_price_to_chain_gap_seconds", "max_quote_age_seconds"],
)
@pytest.mark.parametrize("value", [0, -1])
def test_selector_config_rejects_non_positive_lag_thresholds(field, value):
    with pytest.raises(ValidationError):
        SelectorConfig(**{field: value})


def test_selector_input_has_no_news_model_credential_db_or_brokerage_field():
    forbidden_substrings = (
        "news", "article", "credential", "api_key", "secret", "token",
        "db_path", "database", "order", "position", "account", "model_output",
    )
    field_names = set(ContractSelectorInput.model_fields)
    for name in field_names:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower()


# ---------------------------------------------------------------------------
# ContractSelectorResult
# ---------------------------------------------------------------------------


def _config_snapshot(**overrides) -> SelectorConfigSnapshot:
    config = SelectorConfig()
    fields = dict(
        horizon_expiration_windows=dict(config.horizon_expiration_windows),
        min_moneyness=config.min_moneyness,
        max_moneyness=config.max_moneyness,
        min_abs_delta=config.min_abs_delta,
        max_abs_delta=config.max_abs_delta,
        max_abs_spread=config.max_abs_spread,
        max_pct_spread=config.max_pct_spread,
        min_quote_size=config.min_quote_size,
        max_snapshot_age_seconds=config.max_snapshot_age_seconds,
        max_regime_to_price_gap_seconds=config.max_regime_to_price_gap_seconds,
        max_price_to_chain_gap_seconds=config.max_price_to_chain_gap_seconds,
        max_quote_age_seconds=config.max_quote_age_seconds,
        allow_indicative_for_research=config.allow_indicative_for_research,
    )
    fields.update(overrides)
    return SelectorConfigSnapshot(**fields)


def _base_rejection_counts() -> dict[RejectionReason, int]:
    return {reason: 0 for reason in RejectionReason}


def _result(**overrides) -> ContractSelectorResult:
    fields = dict(
        symbol="SPY",
        generated_at=AS_OF,
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        requested_option_type=None,
        regime_as_of_timestamp=RETRIEVED_AT,
        underlying_price_timestamp=RETRIEVED_AT,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        feed=FeedProvenance.OPRA,
        feed_is_live_opra=True,
        retrieved_at=RETRIEVED_AT,
        config_snapshot=_config_snapshot(),
        status=SelectorStatus.ELIGIBLE,
        candidate_contract_count=1,
        eligible_contract_count=1,
        research_only_contract_count=0,
        rejection_counts=_base_rejection_counts(),
        eligible_contracts=[_quote()],
        research_only_contracts=[],
        notes=["no_open_interest_available"],
    )
    fields.update(overrides)
    return ContractSelectorResult(**fields)


def _research_only_result(**overrides) -> ContractSelectorResult:
    """A minimal, internally-consistent research_only baseline for tests
    that need to start from a valid research result and mutate one field."""
    fields = dict(
        feed=FeedProvenance.INDICATIVE,
        feed_is_live_opra=False,
        config_snapshot=_config_snapshot(allow_indicative_for_research=True),
        status=SelectorStatus.RESEARCH_ONLY,
        eligible_contract_count=0,
        eligible_contracts=[],
        research_only_contract_count=1,
        research_only_contracts=[_quote()],
    )
    fields.update(overrides)
    return _result(**fields)


def test_result_accepts_a_consistent_eligible_result():
    result = _result()
    assert result.status == SelectorStatus.ELIGIBLE
    assert result.schema_version == "spy-contract-selector-1"


def test_result_retains_the_capture_provenance_timestamps():
    result = _result(
        regime_as_of_timestamp=RETRIEVED_AT - timedelta(minutes=1),
        underlying_price_timestamp=RETRIEVED_AT,
    )
    assert result.regime_as_of_timestamp == RETRIEVED_AT - timedelta(minutes=1)
    assert result.underlying_price_timestamp == RETRIEVED_AT


def test_result_has_no_recommendation_ranking_score_or_trade_action_field():
    forbidden_substrings = ("recommend", "rank", "score", "trade_action", "prediction")
    for name in ContractSelectorResult.model_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in name.lower()


def test_result_rejects_eligible_status_with_zero_eligible_contracts():
    with pytest.raises(ValidationError):
        _result(
            status=SelectorStatus.ELIGIBLE,
            eligible_contract_count=0,
            eligible_contracts=[],
            candidate_contract_count=0,
        )


def test_result_rejects_no_eligible_contracts_status_with_nonzero_count():
    with pytest.raises(ValidationError):
        _result(status=SelectorStatus.NO_ELIGIBLE_CONTRACTS)


def test_result_rejects_indeterminate_status_with_nonzero_eligible_count():
    with pytest.raises(ValidationError):
        _result(status=SelectorStatus.INDETERMINATE)


def test_result_rejects_incomplete_rejection_counts():
    counts = _base_rejection_counts()
    del counts[RejectionReason.SNAPSHOT_STALE]
    with pytest.raises(ValidationError):
        _result(rejection_counts=counts)


def test_result_rejects_counts_not_summing_to_candidate_count():
    counts = _base_rejection_counts()
    counts[RejectionReason.SPREAD_TOO_WIDE] = 5
    with pytest.raises(ValidationError):
        _result(rejection_counts=counts, candidate_contract_count=1)


def test_result_rejects_indeterminate_status_without_full_horizon_indeterminate_count():
    counts = _base_rejection_counts()
    with pytest.raises(ValidationError):
        _result(
            status=SelectorStatus.INDETERMINATE,
            eligible_contract_count=0,
            eligible_contracts=[],
            candidate_contract_count=3,
            rejection_counts=counts,
        )


def test_result_rejects_nonzero_horizon_indeterminate_when_status_not_indeterminate():
    counts = _base_rejection_counts()
    counts[RejectionReason.HORIZON_INDETERMINATE] = 1
    with pytest.raises(ValidationError):
        _result(rejection_counts=counts, candidate_contract_count=2)


def test_result_rejects_mismatched_feed_is_live_opra():
    with pytest.raises(ValidationError):
        _result(feed=FeedProvenance.INDICATIVE, feed_is_live_opra=True)


# ---------------------------------------------------------------------------
# Feed-safety boundary: structural enforcement on ContractSelectorResult
# ---------------------------------------------------------------------------


def test_result_accepts_a_consistent_research_only_result():
    result = _research_only_result()
    assert result.status == SelectorStatus.RESEARCH_ONLY
    assert result.eligible_contracts == []
    assert result.eligible_contract_count == 0
    assert result.research_only_contract_count == 1


def test_result_rejects_eligible_status_with_indicative_feed():
    """Requirement 2/3: an indicative-feed batch can never satisfy the
    ELIGIBLE status, enforced at the schema level even for a hand-built
    result, independent of selector.py's own logic."""
    with pytest.raises(ValidationError):
        _result(
            feed=FeedProvenance.INDICATIVE,
            feed_is_live_opra=False,
            status=SelectorStatus.ELIGIBLE,
        )


def test_result_rejects_research_only_status_with_opra_feed():
    with pytest.raises(ValidationError):
        _research_only_result(feed=FeedProvenance.OPRA, feed_is_live_opra=True)


def test_result_rejects_nonzero_eligible_contracts_under_research_only_status():
    """A RESEARCH_ONLY result can never populate eligible_contracts -- that
    field is the one a future strategy agent would read as the operational
    eligible set."""
    with pytest.raises(ValidationError):
        _research_only_result(
            eligible_contract_count=1,
            eligible_contracts=[_quote()],
            candidate_contract_count=2,
        )


def test_result_rejects_nonzero_research_only_contracts_under_eligible_status():
    with pytest.raises(ValidationError):
        _result(
            research_only_contract_count=1,
            research_only_contracts=[_quote()],
            candidate_contract_count=2,
        )


def test_result_rejects_research_only_contract_count_mismatch():
    with pytest.raises(ValidationError):
        _research_only_result(research_only_contract_count=2)


def test_result_rejects_research_only_counts_not_summing_to_candidate_count():
    counts = _base_rejection_counts()
    counts[RejectionReason.SPREAD_TOO_WIDE] = 5
    with pytest.raises(ValidationError):
        _research_only_result(rejection_counts=counts, candidate_contract_count=1)


def test_a_research_only_result_cannot_be_used_as_an_eligible_set():
    """Simulates a careless future consumer that reads eligible_contracts
    without checking status first -- a research_only result must never
    hand it anything."""
    result = _research_only_result()
    assert result.status != SelectorStatus.ELIGIBLE
    assert result.eligible_contracts == []
    assert len(result.eligible_contracts) == 0


def test_result_rejects_extra_fields():
    with pytest.raises(ValidationError):
        ContractSelectorResult(**_result().model_dump(mode="json"), extra_field=1)


def test_result_rejects_empty_notes():
    with pytest.raises(ValidationError):
        _result(notes=[])
