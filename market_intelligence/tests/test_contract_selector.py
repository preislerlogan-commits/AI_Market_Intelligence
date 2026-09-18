"""Tests for market_intelligence/contract_selection/selector.py.

Covers every filter in the published order, boundary values, deterministic
ordering, empty/indeterminate outcomes, missing Greeks, stale/mixed
snapshots, the feed-safety boundary (OPRA-only operational eligibility,
indicative research mode, batch-level gate ordering), and the
horizon-to-expiration-window mapping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    FeedProvenance,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    RejectionReason,
    SelectorConfig,
    SelectorStatus,
)
from market_intelligence.contract_selection.selector import select_eligible_contracts
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon

AS_OF = datetime(2026, 9, 16, 15, 0, tzinfo=UTC)  # 11:00 America/New_York
RETRIEVED_AT = datetime(2026, 9, 16, 14, 58, tzinfo=UTC)
GENERATED_AT = datetime(2026, 9, 16, 15, 1, tzinfo=UTC)
NEAR_EXPIRY = date(2026, 9, 16)  # 0 DTE relative to AS_OF's America/New_York date


def _quote(**overrides) -> OptionContractQuote:
    fields = dict(
        contract_symbol="SPY260916C00680000",
        option_type=OptionType.CALL,
        expiration_date=NEAR_EXPIRY,
        strike_price=Decimal("680"),
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal("1.10"),
        ask_size=10,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.10"),
        rho=Decimal("0.01"),
    )
    fields.update(overrides)
    return OptionContractQuote(**fields)


def _batch(contracts, **overrides) -> OptionChainBatch:
    fields = dict(
        provider="alpaca",
        feed="opra",
        retrieved_at=RETRIEVED_AT,
        contracts=contracts,
    )
    fields.update(overrides)
    return OptionChainBatch(**fields)


def _selector_input(contracts, **overrides) -> ContractSelectorInput:
    batch = overrides.pop("batch", None) or _batch(contracts)
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


def _run(contracts, **overrides):
    return select_eligible_contracts(
        _selector_input(contracts, **overrides), generated_at=GENERATED_AT
    )


def _only_reason(result) -> RejectionReason:
    """Assert exactly one non-zero rejection reason and return it."""
    nonzero = {reason: count for reason, count in result.rejection_counts.items() if count}
    assert len(nonzero) == 1, nonzero
    reason, count = next(iter(nonzero.items()))
    assert count == 1
    return reason


# ---------------------------------------------------------------------------
# A fully eligible baseline contract passes every filter
# ---------------------------------------------------------------------------


def test_a_well_formed_contract_is_eligible():
    result = _run([_quote()])
    assert result.status == SelectorStatus.ELIGIBLE
    assert result.eligible_contract_count == 1
    assert result.candidate_contract_count == 1
    assert result.eligible_contracts[0].contract_symbol == "SPY260916C00680000"
    assert sum(result.rejection_counts.values()) == 0


def test_empty_batch_yields_no_eligible_contracts_with_zero_candidates():
    result = _run([])
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert result.candidate_contract_count == 0
    assert result.eligible_contract_count == 0
    assert sum(result.rejection_counts.values()) == 0
    assert "empty_batch" in result.notes


# ---------------------------------------------------------------------------
# Indeterminate horizon short-circuits everything
# ---------------------------------------------------------------------------


def test_indeterminate_horizon_yields_empty_eligible_set_and_indeterminate_status():
    second = _quote(contract_symbol="SPY260916C00690000", strike_price=Decimal("690"))
    result = _run([_quote(), second], scenario_horizon=ScenarioHorizon.INDETERMINATE)
    assert result.status == SelectorStatus.INDETERMINATE
    assert result.eligible_contract_count == 0
    assert result.eligible_contracts == []
    assert result.rejection_counts[RejectionReason.HORIZON_INDETERMINATE] == 2
    assert sum(result.rejection_counts.values()) == 2


def test_indeterminate_horizon_with_empty_batch_is_still_indeterminate():
    result = _run([], scenario_horizon=ScenarioHorizon.INDETERMINATE)
    assert result.status == SelectorStatus.INDETERMINATE
    assert result.candidate_contract_count == 0
    assert result.rejection_counts[RejectionReason.HORIZON_INDETERMINATE] == 0


# ---------------------------------------------------------------------------
# 1. Expiration / DTE
# ---------------------------------------------------------------------------


def test_expiration_within_the_horizon_window_is_eligible():
    # intraday_30m default window is (0, 2) DTE; NEAR_EXPIRY is 0 DTE.
    result = _run([_quote(expiration_date=NEAR_EXPIRY)])
    assert result.status == SelectorStatus.ELIGIBLE


def test_expiration_at_the_upper_dte_boundary_is_eligible():
    boundary = date(2026, 9, 18)  # 2 DTE from 2026-09-16
    result = _run([_quote(expiration_date=boundary)])
    assert result.status == SelectorStatus.ELIGIBLE


def test_expiration_one_day_past_the_upper_dte_boundary_is_rejected():
    beyond = date(2026, 9, 19)  # 3 DTE, outside (0, 2)
    result = _run([_quote(expiration_date=beyond)])
    assert _only_reason(result) == RejectionReason.EXPIRATION_OUTSIDE_WINDOW


def test_already_expired_contract_is_rejected():
    expired = date(2026, 9, 15)  # -1 DTE
    result = _run([_quote(expiration_date=expired)])
    assert _only_reason(result) == RejectionReason.EXPIRATION_OUTSIDE_WINDOW


def test_next_session_horizon_uses_its_own_window():
    # next_session default window is (1, 5) DTE; 0 DTE is outside it.
    result = _run(
        [_quote(expiration_date=NEAR_EXPIRY)], scenario_horizon=ScenarioHorizon.NEXT_SESSION
    )
    assert _only_reason(result) == RejectionReason.EXPIRATION_OUTSIDE_WINDOW

    within = _run(
        [_quote(expiration_date=date(2026, 9, 17))],  # 1 DTE
        scenario_horizon=ScenarioHorizon.NEXT_SESSION,
    )
    assert within.status == SelectorStatus.ELIGIBLE


# ---------------------------------------------------------------------------
# 2. Option type (only when a directional side is explicitly supplied)
# ---------------------------------------------------------------------------


def test_no_requested_option_type_admits_both_calls_and_puts():
    call = _quote(contract_symbol="SPY260916C00680000", option_type=OptionType.CALL)
    put = _quote(
        contract_symbol="SPY260916P00680000", option_type=OptionType.PUT, delta=Decimal("-0.40")
    )
    result = _run([call, put])
    assert result.eligible_contract_count == 2


def test_requested_call_rejects_put():
    put = _quote(
        contract_symbol="SPY260916P00680000", option_type=OptionType.PUT, delta=Decimal("-0.40")
    )
    result = _run([put], requested_option_type=OptionType.CALL)
    assert _only_reason(result) == RejectionReason.OPTION_TYPE_MISMATCH


def test_requested_put_admits_put_and_rejects_call():
    call = _quote(contract_symbol="SPY260916C00680000", option_type=OptionType.CALL)
    put = _quote(
        contract_symbol="SPY260916P00680000", option_type=OptionType.PUT, delta=Decimal("-0.40")
    )
    result = _run([call, put], requested_option_type=OptionType.PUT)
    assert result.eligible_contract_count == 1
    assert result.eligible_contracts[0].option_type == OptionType.PUT
    assert result.rejection_counts[RejectionReason.OPTION_TYPE_MISMATCH] == 1


# ---------------------------------------------------------------------------
# 3. Strike / moneyness
# ---------------------------------------------------------------------------


def test_moneyness_at_lower_boundary_is_eligible():
    config = SelectorConfig()
    strike = (Decimal("680") * config.min_moneyness).quantize(Decimal("0.01"))
    result = _run([_quote(strike_price=strike, delta=Decimal("0.20"))])
    assert result.status == SelectorStatus.ELIGIBLE


def test_moneyness_below_lower_boundary_is_rejected():
    result = _run([_quote(strike_price=Decimal("100"), delta=Decimal("0.20"))])
    assert _only_reason(result) == RejectionReason.STRIKE_OUTSIDE_MONEYNESS_BAND


def test_moneyness_above_upper_boundary_is_rejected():
    result = _run([_quote(strike_price=Decimal("2000"), delta=Decimal("0.20"))])
    assert _only_reason(result) == RejectionReason.STRIKE_OUTSIDE_MONEYNESS_BAND


# ---------------------------------------------------------------------------
# 4. Delta range
# ---------------------------------------------------------------------------


def test_missing_delta_is_rejected_as_delta_outside_range():
    result = _run([_quote(delta=None)])
    assert _only_reason(result) == RejectionReason.DELTA_OUTSIDE_RANGE


def test_delta_below_minimum_is_rejected():
    result = _run([_quote(delta=Decimal("0.05"))])
    assert _only_reason(result) == RejectionReason.DELTA_OUTSIDE_RANGE


def test_delta_above_maximum_is_rejected():
    result = _run([_quote(delta=Decimal("0.90"))])
    assert _only_reason(result) == RejectionReason.DELTA_OUTSIDE_RANGE


def test_negative_delta_uses_absolute_value_for_puts():
    put = _quote(
        contract_symbol="SPY260916P00680000", option_type=OptionType.PUT, delta=Decimal("-0.40")
    )
    result = _run([put])
    assert result.status == SelectorStatus.ELIGIBLE


def test_delta_at_exact_boundaries_is_eligible():
    config = SelectorConfig()
    low = _quote(contract_symbol="SPY260916C00680001", delta=config.min_abs_delta)
    high = _quote(contract_symbol="SPY260916C00680002", delta=config.max_abs_delta)
    result = _run([low, high])
    assert result.eligible_contract_count == 2


# ---------------------------------------------------------------------------
# 5. Required IV and remaining Greeks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["implied_volatility", "gamma", "theta", "vega", "rho"])
def test_missing_each_required_greek_or_iv_is_rejected(field):
    result = _run([_quote(**{field: None})])
    assert _only_reason(result) == RejectionReason.MISSING_IV_OR_GREEKS


def test_missing_iv_and_greeks_is_never_treated_as_zero():
    """A contract missing every Greek must be rejected, not silently scored
    as if delta/gamma/etc. were zero."""
    result = _run([_quote(implied_volatility=None, gamma=None, theta=None, vega=None, rho=None)])
    assert result.eligible_contract_count == 0
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS


# ---------------------------------------------------------------------------
# 6. Positive bid/ask
# ---------------------------------------------------------------------------


def test_missing_bid_is_rejected():
    result = _run([_quote(bid_price=None)])
    assert _only_reason(result) == RejectionReason.NON_POSITIVE_BID_ASK


def test_missing_ask_is_rejected():
    result = _run([_quote(ask_price=None)])
    assert _only_reason(result) == RejectionReason.NON_POSITIVE_BID_ASK


def test_zero_bid_is_rejected():
    result = _run([_quote(bid_price=Decimal("0"))])
    assert _only_reason(result) == RejectionReason.NON_POSITIVE_BID_ASK


def test_zero_ask_is_rejected():
    result = _run([_quote(ask_price=Decimal("0"))])
    assert _only_reason(result) == RejectionReason.NON_POSITIVE_BID_ASK


# ---------------------------------------------------------------------------
# 7. Non-crossed quote
# ---------------------------------------------------------------------------


def test_crossed_quote_is_rejected():
    result = _run([_quote(bid_price=Decimal("1.20"), ask_price=Decimal("1.10"))])
    assert _only_reason(result) == RejectionReason.CROSSED_QUOTE


def test_equal_bid_and_ask_is_not_crossed():
    result = _run([_quote(bid_price=Decimal("1.10"), ask_price=Decimal("1.10"))])
    assert result.status == SelectorStatus.ELIGIBLE


# ---------------------------------------------------------------------------
# 8. Maximum absolute and percentage spread
# ---------------------------------------------------------------------------


def test_spread_at_the_absolute_boundary_is_eligible():
    # A high enough base price that the equivalent percentage spread stays
    # well inside max_pct_spread, isolating the absolute-spread boundary.
    config = SelectorConfig()
    result = _run(
        [_quote(bid_price=Decimal("10.00"), ask_price=Decimal("10.00") + config.max_abs_spread)]
    )
    assert result.status == SelectorStatus.ELIGIBLE


def test_spread_one_cent_beyond_the_absolute_boundary_is_rejected():
    config = SelectorConfig()
    over = Decimal("10.00") + config.max_abs_spread + Decimal("0.01")
    result = _run([_quote(bid_price=Decimal("10.00"), ask_price=over)])
    assert _only_reason(result) == RejectionReason.SPREAD_TOO_WIDE


def test_spread_beyond_the_percentage_boundary_is_rejected():
    # A tiny absolute spread that nonetheless exceeds max_pct_spread of mid.
    result = _run([_quote(bid_price=Decimal("0.02"), ask_price=Decimal("0.04"))])
    assert _only_reason(result) == RejectionReason.SPREAD_TOO_WIDE


# ---------------------------------------------------------------------------
# 9. Minimum quote size, where available
# ---------------------------------------------------------------------------


def test_bid_size_below_minimum_is_rejected():
    result = _run([_quote(bid_size=0)])
    assert _only_reason(result) == RejectionReason.QUOTE_SIZE_TOO_SMALL


def test_ask_size_below_minimum_is_rejected():
    result = _run([_quote(ask_size=0)])
    assert _only_reason(result) == RejectionReason.QUOTE_SIZE_TOO_SMALL


def test_missing_quote_sizes_do_not_trigger_the_size_filter():
    result = _run([_quote(bid_size=None, ask_size=None)])
    assert result.status == SelectorStatus.ELIGIBLE


# ---------------------------------------------------------------------------
# Batch-level gate 1: feed governance (the feed-safety boundary)
# ---------------------------------------------------------------------------


def test_default_opra_batch_can_become_eligible():
    """Requirement: operational eligibility must default to OPRA only, and
    a default (research not requested) OPRA batch must still be able to
    reach ELIGIBLE."""
    result = _run([_quote()])  # _batch()'s default feed is "opra"
    assert result.status == SelectorStatus.ELIGIBLE
    assert result.feed == FeedProvenance.OPRA
    assert result.feed_is_live_opra is True
    assert result.eligible_contract_count == 1
    assert result.research_only_contract_count == 0


def test_default_indicative_batch_cannot_become_eligible():
    """Requirement: indicative-feed contracts must never receive
    SelectorStatus.ELIGIBLE. Without explicit research permission (the
    SelectorConfig default), the whole batch is rejected outright."""
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=RETRIEVED_AT,
            underlying_price_timestamp=RETRIEVED_AT,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], feed="indicative"),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status != SelectorStatus.ELIGIBLE
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert result.eligible_contract_count == 0
    assert result.eligible_contracts == []
    assert result.research_only_contract_count == 0
    assert _only_reason(result) == RejectionReason.FEED_NOT_ALLOWED
    assert result.feed_is_live_opra is False


def test_explicit_indicative_research_mode_returns_research_only_never_eligible():
    config = SelectorConfig(allow_indicative_for_research=True)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=RETRIEVED_AT,
            underlying_price_timestamp=RETRIEVED_AT,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], feed="indicative"),
            config=config,
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.RESEARCH_ONLY
    assert result.status != SelectorStatus.ELIGIBLE
    assert result.eligible_contract_count == 0
    assert result.eligible_contracts == []
    assert result.research_only_contract_count == 1
    assert result.research_only_contracts[0].contract_symbol == "SPY260916C00680000"
    assert result.feed_is_live_opra is False
    assert "indicative_feed_non_live_non_opra" in result.notes
    assert "research_only_not_operationally_eligible" in result.notes


def test_research_mode_still_applies_every_per_contract_filter():
    """Research mode is not a bypass of quality filters -- a contract that
    fails a per-contract filter is still rejected under its own reason,
    not silently promoted to research_only_contracts."""
    config = SelectorConfig(allow_indicative_for_research=True)
    good = _quote()
    bad_delta = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681"), delta=None
    )
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=RETRIEVED_AT,
            underlying_price_timestamp=RETRIEVED_AT,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([good, bad_delta], feed="indicative"),
            config=config,
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.RESEARCH_ONLY
    assert result.research_only_contract_count == 1
    assert result.rejection_counts[RejectionReason.DELTA_OUTSIDE_RANGE] == 1


def test_a_research_only_result_cannot_be_passed_as_an_operational_eligible_set():
    """A hypothetical future strategy agent that reads
    ContractSelectorResult.eligible_contracts under status == ELIGIBLE can
    never receive indicative-sourced contracts: research_only results
    always carry an empty eligible_contracts list."""
    config = SelectorConfig(allow_indicative_for_research=True)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=RETRIEVED_AT,
            underlying_price_timestamp=RETRIEVED_AT,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], feed="indicative"),
            config=config,
        ),
        generated_at=GENERATED_AT,
    )

    def operational_eligible_set(selector_result):
        """Mimics how a future strategy agent would source its eligible
        set -- only ever from ELIGIBLE results."""
        if selector_result.status != SelectorStatus.ELIGIBLE:
            return []
        return selector_result.eligible_contracts

    assert operational_eligible_set(result) == []
    assert result.eligible_contracts == []


def test_feed_gate_precedes_per_contract_filtering():
    """A disallowed-feed batch must be rejected entirely under
    FEED_NOT_ALLOWED, never diluted by unrelated per-contract rejection
    reasons a contract would otherwise have failed on first."""
    good = _quote()
    bad_delta = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681"), delta=None
    )
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=RETRIEVED_AT,
            underlying_price_timestamp=RETRIEVED_AT,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([good, bad_delta], feed="indicative"),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.rejection_counts[RejectionReason.FEED_NOT_ALLOWED] == 2
    assert result.rejection_counts[RejectionReason.DELTA_OUTSIDE_RANGE] == 0
    assert sum(result.rejection_counts.values()) == 2


# ---------------------------------------------------------------------------
# Batch-level gate 2: snapshot freshness
# ---------------------------------------------------------------------------


def test_snapshot_retrieved_at_exactly_as_of_time_is_accepted():
    """age_seconds == 0 is not "in the future" (age_seconds < 0) and not
    stale -- the boundary sits exactly on the accepted side of both gates."""
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=AS_OF,
            underlying_price_timestamp=AS_OF,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], retrieved_at=AS_OF),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.ELIGIBLE


def test_snapshot_within_freshness_bound_is_eligible():
    config = SelectorConfig()
    boundary_retrieved_at = AS_OF - timedelta(seconds=config.max_snapshot_age_seconds)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=boundary_retrieved_at,
            underlying_price_timestamp=boundary_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], retrieved_at=boundary_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.ELIGIBLE


def test_snapshot_older_than_freshness_bound_is_rejected():
    config = SelectorConfig()
    stale_retrieved_at = AS_OF - timedelta(seconds=config.max_snapshot_age_seconds + 1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=stale_retrieved_at,
            underlying_price_timestamp=stale_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], retrieved_at=stale_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert _only_reason(result) == RejectionReason.SNAPSHOT_STALE


def test_snapshot_retrieved_one_second_in_the_future_is_rejected_from_future():
    """age_seconds < 0 (retrieved_at after as_of_timestamp) is rejected under
    the distinct SNAPSHOT_FROM_FUTURE reason -- never SNAPSHOT_STALE, and
    regardless of how small the future offset is relative to
    max_snapshot_age_seconds."""
    future_retrieved_at = AS_OF + timedelta(seconds=1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=future_retrieved_at,
            underlying_price_timestamp=future_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], retrieved_at=future_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert _only_reason(result) == RejectionReason.SNAPSHOT_FROM_FUTURE
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS


def test_snapshot_retrieved_one_microsecond_in_the_future_is_rejected_from_future():
    """Even a single microsecond of future skew -- far below
    max_snapshot_age_seconds -- trips the future gate; this is not a
    magnitude check, it is a sign check on age_seconds."""
    future_retrieved_at = AS_OF + timedelta(microseconds=1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=future_retrieved_at,
            underlying_price_timestamp=future_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], retrieved_at=future_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert _only_reason(result) == RejectionReason.SNAPSHOT_FROM_FUTURE


def test_snapshot_retrieved_far_in_the_future_is_still_from_future_not_stale():
    """A large future offset (well beyond max_snapshot_age_seconds) must
    still be reported as SNAPSHOT_FROM_FUTURE, not SNAPSHOT_STALE -- the
    future gate is checked first and unconditionally, not only near the
    boundary."""
    config = SelectorConfig()
    future_retrieved_at = AS_OF + timedelta(seconds=config.max_snapshot_age_seconds + 1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=future_retrieved_at,
            underlying_price_timestamp=future_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], retrieved_at=future_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert _only_reason(result) == RejectionReason.SNAPSHOT_FROM_FUTURE


def test_stale_batch_gating_precedes_per_contract_rejection_reasons():
    """Requirement 5/7: a stale batch must be rejected wholesale under
    SNAPSHOT_STALE -- never disguised as ordinary per-contract quality
    failures for the contracts that would also have failed other filters
    first."""
    config = SelectorConfig()
    stale_retrieved_at = AS_OF - timedelta(seconds=config.max_snapshot_age_seconds + 1)
    good = _quote()
    bad_delta = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681"), delta=None
    )
    crossed = _quote(
        contract_symbol="SPY260916C00682000", strike_price=Decimal("682"),
        bid_price=Decimal("2.00"), ask_price=Decimal("1.00"),
    )
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=stale_retrieved_at,
            underlying_price_timestamp=stale_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([good, bad_delta, crossed], retrieved_at=stale_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert result.rejection_counts[RejectionReason.SNAPSHOT_STALE] == 3
    assert result.rejection_counts[RejectionReason.DELTA_OUTSIDE_RANGE] == 0
    assert result.rejection_counts[RejectionReason.CROSSED_QUOTE] == 0
    assert sum(result.rejection_counts.values()) == 3


def test_stale_indicative_research_batch_yields_research_only_with_zero_candidates():
    config = SelectorConfig(allow_indicative_for_research=True)
    stale_retrieved_at = AS_OF - timedelta(seconds=config.max_snapshot_age_seconds + 1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=stale_retrieved_at,
            underlying_price_timestamp=stale_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], feed="indicative", retrieved_at=stale_retrieved_at),
            config=config,
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.RESEARCH_ONLY
    assert result.status != SelectorStatus.ELIGIBLE
    assert result.research_only_contract_count == 0
    assert result.rejection_counts[RejectionReason.SNAPSHOT_STALE] == 1


def test_future_batch_gating_precedes_per_contract_rejection_reasons():
    """A future-dated batch must be rejected wholesale under
    SNAPSHOT_FROM_FUTURE -- never disguised as ordinary per-contract quality
    failures for contracts that would also have failed other filters
    first, and never as SNAPSHOT_STALE."""
    future_retrieved_at = AS_OF + timedelta(seconds=1)
    good = _quote()
    bad_delta = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681"), delta=None
    )
    crossed = _quote(
        contract_symbol="SPY260916C00682000", strike_price=Decimal("682"),
        bid_price=Decimal("2.00"), ask_price=Decimal("1.00"),
    )
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=future_retrieved_at,
            underlying_price_timestamp=future_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([good, bad_delta, crossed], retrieved_at=future_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert result.rejection_counts[RejectionReason.SNAPSHOT_FROM_FUTURE] == 3
    assert result.rejection_counts[RejectionReason.SNAPSHOT_STALE] == 0
    assert result.rejection_counts[RejectionReason.DELTA_OUTSIDE_RANGE] == 0
    assert result.rejection_counts[RejectionReason.CROSSED_QUOTE] == 0
    assert sum(result.rejection_counts.values()) == 3


def test_opra_future_batch_is_never_eligible():
    future_retrieved_at = AS_OF + timedelta(seconds=1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=future_retrieved_at,
            underlying_price_timestamp=future_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], feed="opra", retrieved_at=future_retrieved_at),
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status != SelectorStatus.ELIGIBLE
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert result.eligible_contract_count == 0
    assert result.eligible_contracts == []


def test_indicative_future_batch_never_exposes_research_contracts():
    config = SelectorConfig(allow_indicative_for_research=True)
    future_retrieved_at = AS_OF + timedelta(seconds=1)
    result = select_eligible_contracts(
        ContractSelectorInput(
            scenario_horizon=ScenarioHorizon.INTRADAY_30M,
            regime_as_of_timestamp=future_retrieved_at,
            underlying_price_timestamp=future_retrieved_at,
            as_of_timestamp=AS_OF,
            underlying_price=Decimal("680"),
            batch=_batch([_quote()], feed="indicative", retrieved_at=future_retrieved_at),
            config=config,
        ),
        generated_at=GENERATED_AT,
    )
    assert result.status == SelectorStatus.RESEARCH_ONLY
    assert result.research_only_contract_count == 0
    assert result.research_only_contracts == []
    assert result.rejection_counts[RejectionReason.SNAPSHOT_FROM_FUTURE] == 1


def test_future_and_stale_batch_rejection_counts_remain_complete_and_deterministic():
    """Both time-gate rejections still satisfy the whole-batch accounting
    invariant (rejection_counts + eligible + research_only ==
    candidate_contract_count) and are reproducible across repeated runs on
    the same input."""
    future_retrieved_at = AS_OF + timedelta(seconds=1)
    contracts = [
        _quote(),
        _quote(contract_symbol="SPY260916C00681000", strike_price=Decimal("681")),
    ]
    selector_input = ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        regime_as_of_timestamp=future_retrieved_at,
        underlying_price_timestamp=future_retrieved_at,
        as_of_timestamp=AS_OF,
        underlying_price=Decimal("680"),
        batch=_batch(contracts, retrieved_at=future_retrieved_at),
    )
    first = select_eligible_contracts(selector_input, generated_at=GENERATED_AT)
    second = select_eligible_contracts(selector_input, generated_at=GENERATED_AT)
    assert first == second
    total_accounted = (
        sum(first.rejection_counts.values())
        + first.eligible_contract_count
        + first.research_only_contract_count
    )
    assert total_accounted == first.candidate_contract_count == 2
    assert first.rejection_counts[RejectionReason.SNAPSHOT_FROM_FUTURE] == 2


# ---------------------------------------------------------------------------
# Mixed batches, ordering, and rejection-count bookkeeping
# ---------------------------------------------------------------------------


def test_mixed_batch_partitions_correctly_between_eligible_and_rejected():
    eligible_one = _quote(contract_symbol="SPY260916C00680000", strike_price=Decimal("680"))
    eligible_two = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681"), delta=Decimal("0.35")
    )
    rejected_missing_greek = _quote(
        contract_symbol="SPY260916C00682000", strike_price=Decimal("682"), vega=None
    )
    rejected_crossed = _quote(
        contract_symbol="SPY260916C00683000", strike_price=Decimal("683"),
        bid_price=Decimal("2.00"), ask_price=Decimal("1.00"),
    )
    result = _run([eligible_one, eligible_two, rejected_missing_greek, rejected_crossed])
    assert result.status == SelectorStatus.ELIGIBLE
    assert result.candidate_contract_count == 4
    assert result.eligible_contract_count == 2
    assert result.rejection_counts[RejectionReason.MISSING_IV_OR_GREEKS] == 1
    assert result.rejection_counts[RejectionReason.CROSSED_QUOTE] == 1
    assert sum(result.rejection_counts.values()) == 2


def test_eligible_contracts_are_sorted_deterministically():
    # Deliberately supplied out of order: higher strike, later expiration first.
    later = _quote(
        contract_symbol="SPY260918C00680000",
        expiration_date=date(2026, 9, 18),
        strike_price=Decimal("680"),
    )
    higher_strike = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681")
    )
    put = _quote(
        contract_symbol="SPY260916P00680000", option_type=OptionType.PUT,
        strike_price=Decimal("680"), delta=Decimal("-0.40"),
    )
    call = _quote(contract_symbol="SPY260916C00680000", strike_price=Decimal("680"))

    result = _run([later, higher_strike, put, call])
    symbols = [c.contract_symbol for c in result.eligible_contracts]
    assert symbols == [
        "SPY260916C00680000",  # 2026-09-16, call, 680
        "SPY260916C00681000",  # 2026-09-16, call, 681
        "SPY260916P00680000",  # 2026-09-16, put, 680 (call < put alphabetically)
        "SPY260918C00680000",  # 2026-09-18, call, 680
    ]


def test_repeated_calls_with_the_same_input_are_identical():
    second_contract = _quote(contract_symbol="SPY260916C00681000", strike_price=Decimal("681"))
    contracts = [_quote(), second_contract]
    selector_input = _selector_input(contracts)
    first = select_eligible_contracts(selector_input, generated_at=GENERATED_AT)
    second = select_eligible_contracts(selector_input, generated_at=GENERATED_AT)
    assert first == second


def test_no_eligible_contracts_status_when_every_candidate_is_rejected():
    second_contract = _quote(
        contract_symbol="SPY260916C00681000", strike_price=Decimal("681"), delta=None
    )
    result = _run([_quote(delta=None), second_contract])
    assert result.status == SelectorStatus.NO_ELIGIBLE_CONTRACTS
    assert result.eligible_contract_count == 0
    assert result.rejection_counts[RejectionReason.DELTA_OUTSIDE_RANGE] == 2


# ---------------------------------------------------------------------------
# No recommendation, ranking, score, or trade action in the result
# ---------------------------------------------------------------------------


def test_result_never_produces_a_directional_or_ranking_signal():
    result = _run([_quote()])
    assert not hasattr(result, "recommendation")
    assert not hasattr(result, "rank")
    assert not hasattr(result, "score")
    assert "no_recommendation_ranking_or_score" in result.notes
    assert "no_open_interest_available" in result.notes
