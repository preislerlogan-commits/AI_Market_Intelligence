"""Deterministic, conservative SPY options-contract eligibility selector --
Phase 1 step e (see ``docs/OPTIONS_DECISION_WORKFLOW.md``, "Deterministic
Contract Selector").

This module contains **no data-shape definitions** (see ``contracts.py``)
and **no I/O** (see ``serialization.py``): it consumes one already-validated
``ContractSelectorInput`` and applies a fixed, published, centralized-
threshold filter order. Nothing here makes a network request, opens a
database connection, or imports anything from
``market_intelligence.data_connectors``, ``market_intelligence.storage``,
``market_intelligence.agents``, ``market_intelligence.orchestration``, or
``market_intelligence.model_clients``. It runs without any AI model, and
before any future strategy agent -- there is no code path in this module
that calls a model, ranks a contract, or emits a recommendation.

## Decision order: batch-level gates, then per-contract filters

Four things can end this run before any per-contract filter is evaluated,
in exactly this order. When one of them fires, **every** candidate contract
in the batch is counted under that one reason and no per-contract filter
runs for any of them -- this is deliberate: a stale, future-dated, or
feed-disallowed batch must never look like a pile of individual
contract-quality failures (first-per-contract-failure counts would
otherwise disguise an unusable *batch* as ordinary per-contract rejection
noise).

0. **Indeterminate horizon.** ``ContractSelectorInput.scenario_horizon ==
   ScenarioHorizon.INDETERMINATE`` -> ``SelectorStatus.INDETERMINATE``,
   every candidate counted under ``RejectionReason.HORIZON_INDETERMINATE``.
   Mirrors ``spy_regime_classifier.py``'s "incomplete session forces
   indeterminate before every other rule".
1. **Feed-governance gate (operational vs. research; the feed-safety
   boundary).** ``OptionChainBatch.feed`` is a single value shared by the
   whole batch (this project's connector retrieves one feed per request).
   - ``feed == opra``: always operationally eligible-capable; proceed to
     the freshness gate.
   - ``feed == indicative`` and ``SelectorConfig
     .allow_indicative_for_research`` is ``False`` (the default): the
     **entire batch** is rejected -- every candidate counted under
     ``RejectionReason.FEED_NOT_ALLOWED`` -- and the result status is
     ``SelectorStatus.NO_ELIGIBLE_CONTRACTS``. **Operational eligibility
     defaults to OPRA only; an indicative batch can never reach
     ``ELIGIBLE`` this way or any other.**
   - ``feed == indicative`` and ``allow_indicative_for_research`` is
     explicitly ``True``: proceed to the freshness gate in **research
     mode** -- the eventual result status will be ``RESEARCH_ONLY``
     (never ``ELIGIBLE``), and any contracts that pass every per-contract
     filter go to ``research_only_contracts``, never ``eligible_contracts``
     (see ``contracts.py``, "Feed-safety boundary", for the schema-level
     enforcement of this separation).
2. **Freshness gates (two, in order).** ``age_seconds = (as_of_timestamp -
   batch.retrieved_at).total_seconds()`` -- computed **without** ``abs()``,
   so a batch retrieved after ``as_of_timestamp`` and an old batch are
   distinguished, not folded together:
   - If ``age_seconds < 0`` (the retrieval instant is after
     ``as_of_timestamp`` -- clock skew or a caller error), the **entire
     batch** is rejected -- every candidate counted under
     ``RejectionReason.SNAPSHOT_FROM_FUTURE`` -- with status
     ``NO_ELIGIBLE_CONTRACTS`` (operational path) or ``RESEARCH_ONLY`` with
     zero research contracts (research path). This gate runs first, so an
     OPRA batch can never reach ``ELIGIBLE`` and an indicative research
     batch can never expose a research contract when it is dated in the
     future relative to ``as_of_timestamp``.
   - Otherwise, if ``age_seconds > max_snapshot_age_seconds``, the **entire
     batch** is rejected -- every candidate counted under
     ``RejectionReason.SNAPSHOT_STALE`` -- with the same two possible
     statuses as above.
   Every contract in one batch shares the same ``retrieved_at``, so both
   checks are inherently batch-wide facts, not per-contract ones;
   evaluating them once here, before per-contract filtering, is what keeps
   a stale or future-dated batch's rejection reason uniform instead of
   getting mixed in with unrelated per-contract reasons a contract might
   otherwise have failed on first.

## Published per-contract filter order

Otherwise (horizon resolved, feed governed, batch fresh), each candidate
contract is evaluated through the following filters, in exactly this order;
the first filter a contract fails is the **one and only** ``RejectionReason``
recorded for it (a contract is never counted under more than one reason). A
contract that passes every filter is placed in ``eligible_contracts``
(operational path) or ``research_only_contracts`` (research path) --
**never both, and never the wrong one for the batch's feed.**

1. **Expiration / DTE.** ``dte = (contract.expiration_date -
   as_of_date).days``, where ``as_of_date`` is ``as_of_timestamp`` converted
   to its America/New_York calendar date (the US options market's own
   calendar). Rejected (``EXPIRATION_OUTSIDE_WINDOW``) unless ``dte`` falls
   within the inclusive ``(min_dte, max_dte)`` window
   ``SelectorConfig.horizon_expiration_windows[scenario_horizon]`` fixes for
   the supplied horizon (an already-expired contract, ``dte < 0``, is always
   outside every configured window since every window's ``min_dte >= 0``).
2. **Option type** (only when a directional side is explicitly supplied).
   If ``ContractSelectorInput.requested_option_type`` is not ``None``,
   rejected (``OPTION_TYPE_MISMATCH``) unless the contract's own
   ``option_type`` matches it. When no directional side is supplied, every
   option type passes this filter.
3. **Strike / moneyness.** ``moneyness = strike_price / underlying_price``.
   Rejected (``STRIKE_OUTSIDE_MONEYNESS_BAND``) unless ``moneyness`` falls
   within the inclusive ``[min_moneyness, max_moneyness]`` band.
4. **Delta range.** Rejected (``DELTA_OUTSIDE_RANGE``) if ``delta`` is
   ``None`` (a missing delta can never be "in range") or if
   ``abs(delta)`` falls outside the inclusive ``[min_abs_delta,
   max_abs_delta]`` band.
5. **Required IV and remaining Greeks.** ``delta`` was already confirmed
   present by filter 4; this filter additionally requires
   ``implied_volatility``, ``gamma``, ``theta``, ``vega``, and ``rho`` to
   all be present. Rejected (``MISSING_IV_OR_GREEKS``) if any of them is
   ``None``. A missing value is never filled with zero anywhere in this
   module.
6. **Positive bid/ask.** Rejected (``NON_POSITIVE_BID_ASK``) unless both
   ``bid_price`` and ``ask_price`` are present and strictly greater than
   zero.
7. **Non-crossed quote.** Rejected (``CROSSED_QUOTE``) if ``bid_price >
   ask_price``.
8. **Maximum absolute and percentage spread.** ``spread = ask_price -
   bid_price``; ``mid = (bid_price + ask_price) / 2``. Rejected
   (``SPREAD_TOO_WIDE``) if ``spread > max_abs_spread`` or (when ``mid >
   0``) ``spread / mid > max_pct_spread``.
9. **Minimum quote size, where available.** Rejected
   (``QUOTE_SIZE_TOO_SMALL``) if ``bid_size`` is present and below
   ``min_quote_size``, or if ``ask_size`` is present and below
   ``min_quote_size``. A missing size is not itself a rejection -- the
   check simply does not apply to a size the provider did not report (bid/
   ask *price* is still required to be present by filter 6, independent of
   size availability).

## Ordering of the eligible / research-only sets

Both sets are returned sorted by ``(expiration_date, option_type,
strike_price, contract_symbol)`` -- the same deterministic ordering
``data_connectors.alpaca_options_chain.AlpacaOptionsChainClient`` already
uses for its own snapshots, so the two orderings agree without this module
importing that connector. This is a stable, reproducible ordering only --
it is **not** a ranking, score, or recommendation of any kind.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    ContractSelectorResult,
    FeedProvenance,
    OptionContractQuote,
    OptionType,
    RejectionReason,
    ScenarioHorizon,
    SelectorConfig,
    SelectorConfigSnapshot,
    SelectorStatus,
)

# Matches spy_regime_contracts.EASTERN -- the US options market's own
# calendar. Defined locally (not imported) to keep this module's only
# cross-package dependency limited to the enum values it must reuse.
EASTERN = ZoneInfo("America/New_York")


def _config_snapshot(config: SelectorConfig) -> SelectorConfigSnapshot:
    return SelectorConfigSnapshot(
        horizon_expiration_windows=dict(config.horizon_expiration_windows),
        min_moneyness=config.min_moneyness,
        max_moneyness=config.max_moneyness,
        min_abs_delta=config.min_abs_delta,
        max_abs_delta=config.max_abs_delta,
        max_abs_spread=config.max_abs_spread,
        max_pct_spread=config.max_pct_spread,
        min_quote_size=config.min_quote_size,
        max_snapshot_age_seconds=config.max_snapshot_age_seconds,
        allow_indicative_for_research=config.allow_indicative_for_research,
    )


def _sort_key(contract: OptionContractQuote) -> tuple[object, ...]:
    return (
        contract.expiration_date,
        contract.option_type.value,
        contract.strike_price,
        contract.contract_symbol,
    )


def _build_notes(
    *, feed: FeedProvenance, candidate_count: int, status: SelectorStatus
) -> list[str]:
    """Fixed-vocabulary notes -- never free text, never a raw input value."""
    notes = ["no_open_interest_available", "no_recommendation_ranking_or_score"]
    if feed == FeedProvenance.INDICATIVE:
        notes.append("indicative_feed_non_live_non_opra")
    if status == SelectorStatus.RESEARCH_ONLY:
        notes.append("research_only_not_operationally_eligible")
    if candidate_count == 0:
        notes.append("empty_batch")
    return notes


def _evaluate_contract(
    contract: OptionContractQuote,
    *,
    scenario_horizon: ScenarioHorizon,
    as_of_date,
    underlying_price: Decimal,
    requested_option_type: OptionType | None,
    config: SelectorConfig,
) -> RejectionReason | None:
    """Apply the published per-contract filter order (see the module
    docstring) to one contract. The batch-level feed and freshness gates
    have already been cleared by the time this runs -- see
    ``select_eligible_contracts``. Returns the first failing
    ``RejectionReason``, or ``None`` if the contract passes every filter."""

    # 1. Expiration / DTE.
    min_dte, max_dte = config.horizon_expiration_windows[scenario_horizon]
    dte = (contract.expiration_date - as_of_date).days
    if dte < min_dte or dte > max_dte:
        return RejectionReason.EXPIRATION_OUTSIDE_WINDOW

    # 2. Option type (only when a directional side is explicitly supplied).
    if requested_option_type is not None and contract.option_type != requested_option_type:
        return RejectionReason.OPTION_TYPE_MISMATCH

    # 3. Strike / moneyness.
    moneyness = contract.strike_price / underlying_price
    if moneyness < config.min_moneyness or moneyness > config.max_moneyness:
        return RejectionReason.STRIKE_OUTSIDE_MONEYNESS_BAND

    # 4. Delta range.
    if contract.delta is None:
        return RejectionReason.DELTA_OUTSIDE_RANGE
    abs_delta = abs(contract.delta)
    if abs_delta < config.min_abs_delta or abs_delta > config.max_abs_delta:
        return RejectionReason.DELTA_OUTSIDE_RANGE

    # 5. Required IV and remaining Greeks (delta already confirmed present).
    if (
        contract.implied_volatility is None
        or contract.gamma is None
        or contract.theta is None
        or contract.vega is None
        or contract.rho is None
    ):
        return RejectionReason.MISSING_IV_OR_GREEKS

    # 6. Positive bid/ask.
    if contract.bid_price is None or contract.bid_price <= 0:
        return RejectionReason.NON_POSITIVE_BID_ASK
    if contract.ask_price is None or contract.ask_price <= 0:
        return RejectionReason.NON_POSITIVE_BID_ASK

    # 7. Non-crossed quote.
    if contract.bid_price > contract.ask_price:
        return RejectionReason.CROSSED_QUOTE

    # 8. Maximum absolute and percentage spread.
    spread = contract.ask_price - contract.bid_price
    if spread > config.max_abs_spread:
        return RejectionReason.SPREAD_TOO_WIDE
    mid = (contract.bid_price + contract.ask_price) / 2
    if mid > 0 and (spread / mid) > config.max_pct_spread:
        return RejectionReason.SPREAD_TOO_WIDE

    # 9. Minimum quote size, where available.
    if contract.bid_size is not None and contract.bid_size < config.min_quote_size:
        return RejectionReason.QUOTE_SIZE_TOO_SMALL
    if contract.ask_size is not None and contract.ask_size < config.min_quote_size:
        return RejectionReason.QUOTE_SIZE_TOO_SMALL

    return None


def _batch_gated_result(
    selector_input: ContractSelectorInput,
    *,
    generated_at: datetime,
    status: SelectorStatus,
    gate_reason: RejectionReason,
) -> ContractSelectorResult:
    """Build the result for a run that ends at a batch-level gate (an
    indeterminate horizon, a disallowed feed, or a stale batch) -- every
    candidate contract is counted under exactly one ``gate_reason`` and no
    per-contract filter runs. Both contract lists are always empty."""
    batch = selector_input.batch
    candidate_count = len(batch.contracts)
    rejection_counts = {reason: 0 for reason in RejectionReason}
    rejection_counts[gate_reason] = candidate_count
    return ContractSelectorResult(
        symbol=selector_input.symbol,
        generated_at=generated_at,
        scenario_horizon=selector_input.scenario_horizon,
        requested_option_type=selector_input.requested_option_type,
        as_of_timestamp=selector_input.as_of_timestamp,
        underlying_price=selector_input.underlying_price,
        feed=batch.feed,
        feed_is_live_opra=(batch.feed == FeedProvenance.OPRA),
        retrieved_at=batch.retrieved_at,
        config_snapshot=_config_snapshot(selector_input.config),
        status=status,
        candidate_contract_count=candidate_count,
        eligible_contract_count=0,
        research_only_contract_count=0,
        rejection_counts=rejection_counts,
        eligible_contracts=[],
        research_only_contracts=[],
        notes=_build_notes(feed=batch.feed, candidate_count=candidate_count, status=status),
    )


def select_eligible_contracts(
    selector_input: ContractSelectorInput, *, generated_at: datetime
) -> ContractSelectorResult:
    """Deterministically filter one bounded SPY option-chain batch to its
    eligible (OPRA) or research-only (indicative, explicitly opted in)
    subset. Pure function: no network, database, or model access, and no
    randomness -- the same input always produces the same output.

    ``generated_at`` is the caller-supplied moment this run is recorded as
    having happened (mirrors ``evaluate_spy_vwap_reversion``'s
    ``generated_at`` parameter) -- it is never derived from ``datetime.now``
    inside this pure function.
    """
    batch = selector_input.batch
    config = selector_input.config

    # 0. Indeterminate horizon short-circuits everything.
    if selector_input.scenario_horizon == ScenarioHorizon.INDETERMINATE:
        return _batch_gated_result(
            selector_input,
            generated_at=generated_at,
            status=SelectorStatus.INDETERMINATE,
            gate_reason=RejectionReason.HORIZON_INDETERMINATE,
        )

    research_mode = (
        batch.feed == FeedProvenance.INDICATIVE and config.allow_indicative_for_research
    )

    # 1. Feed-governance gate: operational eligibility defaults to OPRA
    # only. An indicative batch without explicit research permission is
    # rejected outright -- it can never reach eligible OR research_only.
    if batch.feed == FeedProvenance.INDICATIVE and not config.allow_indicative_for_research:
        return _batch_gated_result(
            selector_input,
            generated_at=generated_at,
            status=SelectorStatus.NO_ELIGIBLE_CONTRACTS,
            gate_reason=RejectionReason.FEED_NOT_ALLOWED,
        )

    # 2. Freshness gates -- evaluated once each for the whole batch, since
    # every contract shares the same retrieved_at. No abs(): a batch dated
    # after as_of_timestamp (age_seconds < 0) is a distinct failure mode
    # from an old batch, and this gate must run before the staleness gate
    # so a future-dated batch is never counted as merely stale.
    age_seconds = (selector_input.as_of_timestamp - batch.retrieved_at).total_seconds()
    batch_time_gate_status = SelectorStatus.RESEARCH_ONLY if research_mode else (
        SelectorStatus.NO_ELIGIBLE_CONTRACTS
    )
    if age_seconds < 0:
        return _batch_gated_result(
            selector_input,
            generated_at=generated_at,
            status=batch_time_gate_status,
            gate_reason=RejectionReason.SNAPSHOT_FROM_FUTURE,
        )
    if age_seconds > config.max_snapshot_age_seconds:
        return _batch_gated_result(
            selector_input,
            generated_at=generated_at,
            status=batch_time_gate_status,
            gate_reason=RejectionReason.SNAPSHOT_STALE,
        )

    # 3. Per-contract filtering.
    as_of_date = selector_input.as_of_timestamp.astimezone(EASTERN).date()
    candidate_count = len(batch.contracts)
    rejection_counts: dict[RejectionReason, int] = {reason: 0 for reason in RejectionReason}
    passing: list[OptionContractQuote] = []
    for contract in batch.contracts:
        reason = _evaluate_contract(
            contract,
            scenario_horizon=selector_input.scenario_horizon,
            as_of_date=as_of_date,
            underlying_price=selector_input.underlying_price,
            requested_option_type=selector_input.requested_option_type,
            config=config,
        )
        if reason is None:
            passing.append(contract)
        else:
            rejection_counts[reason] += 1

    passing_sorted = sorted(passing, key=_sort_key)

    if research_mode:
        status = SelectorStatus.RESEARCH_ONLY
        eligible_contracts: list[OptionContractQuote] = []
        research_only_contracts = passing_sorted
    else:
        status = SelectorStatus.ELIGIBLE if passing_sorted else SelectorStatus.NO_ELIGIBLE_CONTRACTS
        eligible_contracts = passing_sorted
        research_only_contracts = []

    return ContractSelectorResult(
        symbol=selector_input.symbol,
        generated_at=generated_at,
        scenario_horizon=selector_input.scenario_horizon,
        requested_option_type=selector_input.requested_option_type,
        as_of_timestamp=selector_input.as_of_timestamp,
        underlying_price=selector_input.underlying_price,
        feed=batch.feed,
        feed_is_live_opra=(batch.feed == FeedProvenance.OPRA),
        retrieved_at=batch.retrieved_at,
        config_snapshot=_config_snapshot(config),
        status=status,
        candidate_contract_count=candidate_count,
        eligible_contract_count=len(eligible_contracts),
        research_only_contract_count=len(research_only_contracts),
        rejection_counts=rejection_counts,
        eligible_contracts=eligible_contracts,
        research_only_contracts=research_only_contracts,
        notes=_build_notes(feed=batch.feed, candidate_count=candidate_count, status=status),
    )
