"""Comprehensive mocked tests for
``market_intelligence.orchestration.spy_contract_capture`` -- the
synchronized-capture coordinator for the first real Contract Selector run.

Every Alpaca client here is a hand-written fake with no network access
whatsoever -- these tests never touch ``httpx``, never read ``Settings``,
and never make a live request. Coverage: the regular-hours-after-six-bars
gate, the future-bar defensive filter, the indeterminate-horizon early exit
(no chain request), underlying-price extraction (trade preferred, quote
fallback, staleness/crossed-quote rejection, market-data time vs. request
time), the resolved-horizon happy path (moneyness-band strike window,
horizon DTE expiration window, exactly three provider calls), chain
truncation (distinct from any other chain failure), and the provenance
ordering/lag gate.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

import pytest

from market_intelligence.contract_selection.contracts import (
    OptionType,
    SelectorConfig,
)
from market_intelligence.data_connectors.alpaca_bars import (
    AlpacaBarsCredentialsMissingError,
    AlpacaBarsError,
    Bar,
)
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaCredentialsMissingError,
)
from market_intelligence.data_connectors.alpaca_options_chain import (
    AlpacaOptionsChainError,
    AlpacaOptionsChainTruncatedError,
    OptionChainSnapshot,
    OptionChainSnapshotBatch,
    OptionChainTruncationReason,
)
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon
from market_intelligence.orchestration.spy_contract_capture import (
    CaptureStatus,
    bar_to_intraday_bar,
    capture_contract_selector_input,
    compute_expiration_window,
    compute_strike_window,
    extract_underlying_price,
    snapshot_to_quote,
)

SESSION_DATE = date(2026, 9, 16)  # Wednesday


def _bar(hour: int, minute: int, *, open_=None, close=None, high=None, low=None) -> Bar:
    close = close if close is not None else Decimal("680.00")
    open_ = open_ if open_ is not None else close
    high = high if high is not None else max(open_, close) + Decimal("0.02")
    low = low if low is not None else min(open_, close) - Decimal("0.02")
    ts = datetime(2026, 9, 16, hour, minute, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    return Bar(
        provider="alpaca",
        symbol="SPY",
        timeframe="5Min",
        feed="iex",
        adjustment="raw",
        currency="USD",
        timestamp=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        trade_count=10,
        vwap=close,
        retrieved_at=ts,
    )


def _flat_bars() -> list[Bar]:
    """Six flat 5-minute bars, 09:30-09:55 ET starts (13:30-13:55 UTC), all
    completing by 14:00 UTC (10:00 ET) -- classifies to
    regime=INDETERMINATE / scenario_horizon=INDETERMINATE (empirically
    verified: no trend, no extension, no rule matches)."""
    return [_bar(13, m) for m in (30, 35, 40, 45, 50, 55)]


def _trending_bars() -> list[Bar]:
    """Eight bars, 09:30-10:05 ET starts (13:30-14:05 UTC), a clean uptrend
    completing at 14:10 UTC (10:10 ET) -- classifies to
    regime=TREND_CONTINUATION / scenario_horizon=INTRADAY_2H (empirically
    verified)."""
    closes = [
        Decimal("680.00"), Decimal("680.50"), Decimal("681.00"), Decimal("681.60"),
        Decimal("682.20"), Decimal("682.90"), Decimal("683.60"), Decimal("684.40"),
    ]
    bars = []
    prev = Decimal("680.00")
    for offset, close in zip(range(0, 40, 5), closes):
        hour = 13 + (30 + offset) // 60
        minute = (30 + offset) % 60
        bars.append(_bar(hour, minute, open_=prev, close=close))
        prev = close
    return bars


def _snapshot_payload(*, trade_price=None, trade_ts=None, bid=None, ask=None, quote_ts=None):
    payload: dict = {}
    if trade_price is not None or trade_ts is not None:
        payload["latestTrade"] = {"p": trade_price, "t": trade_ts}
    if bid is not None or ask is not None or quote_ts is not None:
        payload["latestQuote"] = {"bp": bid, "ap": ask, "t": quote_ts}
    return payload


def _snapshot(symbol="SPY260918C00680000", **overrides) -> OptionChainSnapshot:
    fields = dict(
        provider="alpaca",
        underlying="SPY",
        feed="indicative",
        contract_symbol=symbol,
        expiration_date="2026-09-18",
        option_type="call",
        strike_price=Decimal("680"),
        quote_timestamp="2026-09-16T14:11:50Z",
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal("1.10"),
        ask_size=10,
        trade_timestamp=None,
        trade_price=None,
        trade_size=None,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.4"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.1"),
        vega=Decimal("0.1"),
        rho=Decimal("0.01"),
        retrieved_at="2026-09-16T14:11:50Z",
    )
    fields.update(overrides)
    return OptionChainSnapshot(**fields)


class _SequentialClock:
    """Returns each value in order, then repeats the last one forever."""

    def __init__(self, *values: datetime):
        self._values = list(values)
        self.call_count = 0

    def __call__(self) -> datetime:
        self.call_count += 1
        if len(self._values) > 1:
            return self._values.pop(0)
        return self._values[0]


class _FakeBarsClient:
    def __init__(self, bars: list[Bar] | None = None, error: Exception | None = None):
        self._bars = bars if bars is not None else []
        self._error = error
        self.calls: list[dict] = []

    def get_bars(self, symbol, timeframe, *, start, end, limit, max_pages, client=None):
        self.calls.append(
            dict(symbol=symbol, timeframe=timeframe, start=start, end=end,
                 limit=limit, max_pages=max_pages)
        )
        if self._error:
            raise self._error
        return list(self._bars)


class _FakeMarketDataClient:
    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self._payload = payload if payload is not None else {}
        self._error = error
        self.calls: list[str] = []

    def get_snapshot(self, symbol, *, client=None):
        self.calls.append(symbol)
        if self._error:
            raise self._error
        return dict(self._payload)


class _FakeOptionsChainClient:
    def __init__(
        self,
        snapshots: tuple[OptionChainSnapshot, ...] = (),
        retrieved_at: str = "2026-09-16T14:11:50Z",
        error: Exception | None = None,
    ):
        self._snapshots = snapshots
        self._retrieved_at = retrieved_at
        self._error = error
        self.calls: list = []

    def get_chain_snapshot(self, request, *, client=None):
        self.calls.append(request)
        if self._error:
            raise self._error
        return OptionChainSnapshotBatch(
            retrieved_at=self._retrieved_at, request=request, snapshots=self._snapshots
        )


def _run(
    *,
    now: datetime,
    as_of_timestamp: datetime | None = None,
    bars_client=None,
    market_data_client=None,
    options_chain_client=None,
    selector_config: SelectorConfig = SelectorConfig(),
):
    clock = _SequentialClock(now, as_of_timestamp or now)
    return capture_contract_selector_input(
        bars_client=bars_client or _FakeBarsClient(),
        market_data_client=market_data_client or _FakeMarketDataClient(),
        options_chain_client=options_chain_client or _FakeOptionsChainClient(),
        selector_config=selector_config,
        clock=clock,
    ), clock


# ---------------------------------------------------------------------------
# Gate 1: regular-hours-after-six-bars
# ---------------------------------------------------------------------------


def test_before_ten_am_et_makes_no_provider_calls():
    bars = _FakeBarsClient()
    price = _FakeMarketDataClient()
    chain = _FakeOptionsChainClient()
    result, _ = _run(
        now=datetime(2026, 9, 16, 13, 59, tzinfo=UTC),  # 09:59 ET
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )
    assert result.status == CaptureStatus.OUTSIDE_REGULAR_HOURS
    assert bars.calls == []
    assert price.calls == []
    assert chain.calls == []


def test_at_or_after_session_close_makes_no_provider_calls():
    bars = _FakeBarsClient()
    result, _ = _run(now=datetime(2026, 9, 16, 20, 0, tzinfo=UTC), bars_client=bars)  # 16:00 ET
    assert result.status == CaptureStatus.OUTSIDE_REGULAR_HOURS
    assert bars.calls == []


def test_weekend_makes_no_provider_calls():
    bars = _FakeBarsClient()
    result, _ = _run(now=datetime(2026, 9, 19, 14, 0, tzinfo=UTC), bars_client=bars)  # Saturday
    assert result.status == CaptureStatus.OUTSIDE_REGULAR_HOURS
    assert bars.calls == []


def test_exactly_ten_am_et_with_six_completed_bars_proceeds_past_the_gate():
    bars = _FakeBarsClient(bars=_flat_bars())
    result, _ = _run(now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC), bars_client=bars)  # 10:00 ET
    assert result.status != CaptureStatus.OUTSIDE_REGULAR_HOURS
    assert len(bars.calls) == 1


# ---------------------------------------------------------------------------
# Gate 2: bar completeness (six real completed bars, defensively filtered)
# ---------------------------------------------------------------------------


def test_bars_client_error_yields_bars_unavailable_with_no_further_calls():
    bars = _FakeBarsClient(error=AlpacaBarsError("boom"))
    price = _FakeMarketDataClient()
    result, _ = _run(
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC), bars_client=bars, market_data_client=price
    )
    assert result.status == CaptureStatus.BARS_UNAVAILABLE
    assert price.calls == []


def test_bars_credentials_missing_yields_bars_unavailable():
    bars = _FakeBarsClient(error=AlpacaBarsCredentialsMissingError("no creds"))
    result, _ = _run(now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC), bars_client=bars)
    assert result.status == CaptureStatus.BARS_UNAVAILABLE


def test_fewer_than_six_completed_bars_stops_before_price_or_chain():
    bars = _FakeBarsClient(bars=_flat_bars()[:5])
    price = _FakeMarketDataClient()
    chain = _FakeOptionsChainClient()
    result, _ = _run(
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )
    assert result.status == CaptureStatus.INSUFFICIENT_COMPLETED_BARS
    assert price.calls == []
    assert chain.calls == []


def test_a_not_yet_complete_bar_is_dropped_before_counting():
    """Even if the (mocked) provider misbehaves and returns a bar that would
    not complete until after "now", the coordinator's own defensive filter
    drops it -- six flat bars plus one bar starting at 10:00 (completing at
    10:05, after "now" == 10:00 exactly) must still count as only six."""
    bars = _flat_bars() + [_bar(14, 0)]
    client = _FakeBarsClient(bars=bars)
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price="680.0", trade_ts="2026-09-16T13:59:50Z")
    )
    result, _ = _run(
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC), bars_client=client, market_data_client=price
    )
    assert result.status != CaptureStatus.INSUFFICIENT_COMPLETED_BARS
    # Past the bar-completeness gate: the price client was reached, and the
    # (still only six real, completed) bars classify the same as _flat_bars()
    # alone -- INDETERMINATE, not a different result from the stray 7th bar.
    assert price.calls == ["SPY"]
    assert result.status == CaptureStatus.INDETERMINATE


# ---------------------------------------------------------------------------
# Gate 3: indeterminate regime/horizon -- stop before the chain request
# ---------------------------------------------------------------------------


def test_indeterminate_regime_stops_before_the_option_chain_request():
    bars = _FakeBarsClient(bars=_flat_bars())
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price=680.0, trade_ts="2026-09-16T13:59:50Z")
    )
    chain = _FakeOptionsChainClient()
    result, _ = _run(
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )
    assert result.status == CaptureStatus.INDETERMINATE
    assert result.scenario_horizon == ScenarioHorizon.INDETERMINATE
    assert result.underlying_price == Decimal("680.0")
    assert result.underlying_price_timestamp == datetime(2026, 9, 16, 13, 59, 50, tzinfo=UTC)
    assert result.selector_input is None
    assert len(bars.calls) == 1
    assert len(price.calls) == 1
    assert chain.calls == []  # the third provider request is never made
    assert "stopped_before_option_chain_request" in result.notes


# ---------------------------------------------------------------------------
# Underlying-price extraction (requirement 4)
# ---------------------------------------------------------------------------


def test_price_unavailable_stops_before_the_option_chain_request():
    bars = _FakeBarsClient(bars=_flat_bars())
    price = _FakeMarketDataClient(payload={})
    chain = _FakeOptionsChainClient()
    result, _ = _run(
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )
    assert result.status == CaptureStatus.PRICE_UNAVAILABLE
    assert chain.calls == []


def test_price_credentials_missing_yields_price_unavailable():
    price = _FakeMarketDataClient(error=AlpacaCredentialsMissingError("no creds"))
    result, _ = _run(
        now=datetime(2026, 9, 16, 14, 0, tzinfo=UTC),
        bars_client=_FakeBarsClient(bars=_flat_bars()), market_data_client=price,
    )
    assert result.status == CaptureStatus.PRICE_UNAVAILABLE


def test_extract_underlying_price_uses_the_trade_market_data_timestamp_not_now():
    """Requirement 4: the returned timestamp is the provider's own market-
    data time, never the (later) request/"now" time."""
    now = datetime(2026, 9, 16, 14, 0, 30, tzinfo=UTC)
    payload = _snapshot_payload(trade_price="680.25", trade_ts="2026-09-16T13:59:00Z")
    price, timestamp = extract_underlying_price(payload, now=now, max_quote_age_seconds=300)
    assert price == Decimal("680.25")
    assert timestamp == datetime(2026, 9, 16, 13, 59, 0, tzinfo=UTC)
    assert timestamp != now


def test_extract_underlying_price_falls_back_to_quote_midpoint_when_trade_missing():
    now = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
    payload = _snapshot_payload(bid="679.90", ask="680.10", quote_ts="2026-09-16T13:59:30Z")
    price, timestamp = extract_underlying_price(payload, now=now, max_quote_age_seconds=300)
    assert price == Decimal("680.00")
    assert timestamp == datetime(2026, 9, 16, 13, 59, 30, tzinfo=UTC)


def test_extract_underlying_price_rejects_a_crossed_quote_fallback():
    now = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
    payload = _snapshot_payload(bid="680.20", ask="680.10", quote_ts="2026-09-16T13:59:30Z")
    with pytest.raises(Exception):
        extract_underlying_price(payload, now=now, max_quote_age_seconds=300)


def test_extract_underlying_price_rejects_a_stale_trade_and_stale_quote():
    now = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
    payload = _snapshot_payload(
        trade_price="680.00", trade_ts="2026-09-16T13:00:00Z",  # an hour stale
        bid="679.90", ask="680.10", quote_ts="2026-09-16T13:00:00Z",
    )
    with pytest.raises(Exception):
        extract_underlying_price(payload, now=now, max_quote_age_seconds=300)


def test_extract_underlying_price_rejects_a_future_trade_timestamp():
    now = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
    payload = _snapshot_payload(trade_price="680.00", trade_ts="2026-09-16T14:05:00Z")
    with pytest.raises(Exception):
        extract_underlying_price(payload, now=now, max_quote_age_seconds=300)


def test_extract_underlying_price_rejects_a_non_positive_trade_price():
    now = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)
    payload = _snapshot_payload(trade_price="0", trade_ts="2026-09-16T13:59:50Z")
    with pytest.raises(Exception):
        extract_underlying_price(payload, now=now, max_quote_age_seconds=300)


# ---------------------------------------------------------------------------
# Coordinator-level price-validation-time reference (PR #54 fix): the
# underlying-price observation must be validated against its own
# price_validation_time, taken right after the snapshot request returns --
# never against capture_start, which governs only the regular-hours
# preflight, the bars request's own upper bound, and completed-bar
# filtering.
# ---------------------------------------------------------------------------


def test_price_after_capture_start_but_at_or_before_price_validation_time_is_accepted():
    """A price timestamp later than capture_start -- which the old,
    single-timestamp behavior would have rejected as "from the future" --
    must be accepted once it is validated against the later
    price_validation_time instead."""
    capture_start = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    price_validation_time = datetime(2026, 9, 16, 14, 12, 40, tzinfo=UTC)
    bars = _FakeBarsClient(bars=_trending_bars())
    price = _FakeMarketDataClient(
        # After capture_start (14:12:00), but at/before price_validation_time
        # (14:12:40) -- exactly the window the fix must accept.
        payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:12:20Z")
    )
    chain = _FakeOptionsChainClient(retrieved_at="2026-09-16T14:12:45Z")

    result, clock = _run(
        now=capture_start, as_of_timestamp=price_validation_time,
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )

    assert result.status == CaptureStatus.RESOLVED
    assert result.underlying_price == Decimal("684.50")
    assert result.underlying_price_timestamp == datetime(2026, 9, 16, 14, 12, 20, tzinfo=UTC)


def test_price_after_price_validation_time_is_rejected():
    """A price timestamp after price_validation_time itself -- not merely
    after capture_start -- must still be rejected as being from the
    future, relative to the instant it was actually validated."""
    capture_start = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    price_validation_time = datetime(2026, 9, 16, 14, 12, 30, tzinfo=UTC)
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:12:35Z")
    )

    result, _ = _run(
        now=capture_start, as_of_timestamp=price_validation_time,
        bars_client=_FakeBarsClient(bars=_trending_bars()), market_data_client=price,
    )

    assert result.status == CaptureStatus.PRICE_UNAVAILABLE


def test_over_age_price_is_rejected_relative_to_price_validation_time():
    """Recency is judged against price_validation_time, not capture_start --
    an old price must be rejected even though capture_start (an unrelated,
    earlier instant) is not itself stale relative to it."""
    capture_start = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    price_validation_time = datetime(2026, 9, 16, 14, 20, 0, tzinfo=UTC)
    price = _FakeMarketDataClient(
        # 20 minutes stale relative to price_validation_time -- exceeds the
        # default 300-second max_quote_age_seconds.
        payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:00:00Z")
    )

    result, _ = _run(
        now=capture_start, as_of_timestamp=price_validation_time,
        bars_client=_FakeBarsClient(bars=_trending_bars()), market_data_client=price,
    )

    assert result.status == CaptureStatus.PRICE_UNAVAILABLE


def test_bar_completeness_gate_still_uses_capture_start_not_price_validation_time():
    """A bar that completes after capture_start but before the later
    price_validation_time must still be dropped -- the bar-completeness
    gate is always judged against capture_start, never the separate, later
    clock read taken for price validation, even when that later read is
    itself well after the bar completed."""
    capture_start = datetime(2026, 9, 16, 14, 0, 0, tzinfo=UTC)  # 10:00 ET
    # The 7th bar completes at 14:05 -- after capture_start, but before the
    # price_validation_time used below (14:06).
    bars = _flat_bars() + [_bar(14, 0)]
    bars_client = _FakeBarsClient(bars=bars)
    price_validation_time = datetime(2026, 9, 16, 14, 6, 0, tzinfo=UTC)
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price="680.0", trade_ts="2026-09-16T13:59:50Z")
    )

    result, _ = _run(
        now=capture_start, as_of_timestamp=price_validation_time,
        bars_client=bars_client, market_data_client=price,
        selector_config=SelectorConfig(max_quote_age_seconds=600),
    )

    # regime_as_of_timestamp is the completion time of the last bar the
    # regime engine actually used -- 14:00 (the 6th flat bar), never 14:05
    # (the 7th bar), proving the extra, later-completing bar was excluded
    # even though price_validation_time (14:06) is well after it completed.
    assert result.regime_as_of_timestamp == datetime(2026, 9, 16, 14, 0, tzinfo=UTC)


def test_provenance_gap_violation_between_price_and_chain_still_fails_closed():
    """The existing ordering/lag validator must still fail closed after the
    fix -- unaffected by price validation now happening against its own,
    separate, later clock read rather than capture_start."""
    capture_start = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    price_validation_time = datetime(2026, 9, 16, 14, 12, 10, tzinfo=UTC)
    bars = _FakeBarsClient(bars=_trending_bars())
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:12:05Z")
    )
    # 115 seconds after the price timestamp -- exceeds the default 60-second
    # max_price_to_chain_gap_seconds.
    chain = _FakeOptionsChainClient(retrieved_at="2026-09-16T14:14:00Z")

    result, _ = _run(
        now=capture_start, as_of_timestamp=price_validation_time,
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )

    assert result.status == CaptureStatus.PROVENANCE_INVALID
    assert result.selector_input is None


# ---------------------------------------------------------------------------
# Resolved horizon: happy path -- exactly three provider calls
# ---------------------------------------------------------------------------


def test_resolved_horizon_makes_exactly_three_calls_and_builds_a_valid_input():
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)  # 10:12 ET
    as_of = datetime(2026, 9, 16, 14, 12, 5, tzinfo=UTC)
    bars = _FakeBarsClient(bars=_trending_bars())
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
    )
    chain = _FakeOptionsChainClient(
        snapshots=(_snapshot(),), retrieved_at="2026-09-16T14:11:50Z"
    )

    result, clock = _run(
        now=now, as_of_timestamp=as_of,
        bars_client=bars, market_data_client=price, options_chain_client=chain,
    )

    assert result.status == CaptureStatus.RESOLVED
    assert len(bars.calls) == 1
    assert len(price.calls) == 1
    assert len(chain.calls) == 1
    # capture_start, price_validation_time (taken right after the snapshot
    # request returns), and the final as_of_timestamp -- three reads, never
    # one reused wall-clock instant across the whole sequence.
    assert clock.call_count == 3

    selector_input = result.selector_input
    assert selector_input is not None
    assert selector_input.scenario_horizon == ScenarioHorizon.INTRADAY_2H
    assert selector_input.regime_as_of_timestamp == datetime(2026, 9, 16, 14, 10, tzinfo=UTC)
    assert selector_input.underlying_price_timestamp == datetime(
        2026, 9, 16, 14, 11, 30, tzinfo=UTC
    )
    assert selector_input.batch.retrieved_at == datetime(2026, 9, 16, 14, 11, 50, tzinfo=UTC)
    assert selector_input.as_of_timestamp == as_of
    assert selector_input.underlying_price == Decimal("684.50")
    assert selector_input.batch.feed.value == "indicative"
    assert len(selector_input.batch.contracts) == 1
    assert result.candidate_contract_count == 1
    assert "ready_for_selector" in result.notes


def test_resolved_horizon_strike_window_uses_the_full_moneyness_band():
    config = SelectorConfig()
    gte, lte = compute_strike_window(Decimal("684.50"), config=config)
    expected_gte = (Decimal("684.50") * config.min_moneyness).to_integral_value(
        rounding=ROUND_FLOOR
    )
    expected_lte = (Decimal("684.50") * config.max_moneyness).to_integral_value(
        rounding=ROUND_CEILING
    )
    assert gte == expected_gte
    assert lte == expected_lte
    assert gte == Decimal("581")
    assert lte == Decimal("788")


def test_resolved_horizon_expiration_window_uses_the_horizon_dte_mapping():
    config = SelectorConfig()
    gte, lte = compute_expiration_window(
        ScenarioHorizon.INTRADAY_2H, session_date=SESSION_DATE, config=config
    )
    min_dte, max_dte = config.horizon_expiration_windows[ScenarioHorizon.INTRADAY_2H]
    assert gte == (SESSION_DATE + timedelta(days=min_dte)).isoformat()
    assert lte == (SESSION_DATE + timedelta(days=max_dte)).isoformat()


def test_resolved_horizon_chain_request_is_bounded_by_the_connector_ceilings():
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    bars = _FakeBarsClient(bars=_trending_bars())
    price = _FakeMarketDataClient(
        payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
    )
    chain = _FakeOptionsChainClient(retrieved_at="2026-09-16T14:11:50Z")
    _run(now=now, as_of_timestamp=now, bars_client=bars, market_data_client=price,
         options_chain_client=chain)
    (request,) = chain.calls
    assert request.max_pages == 10
    assert request.max_total_contracts == 5000
    assert request.feed == "indicative"


# ---------------------------------------------------------------------------
# Chain truncation vs. other chain failures (requirement 6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        OptionChainTruncationReason.MAX_PAGES_EXCEEDED,
        OptionChainTruncationReason.MAX_TOTAL_CONTRACTS_EXCEEDED,
    ],
)
def test_chain_pagination_or_contract_ceiling_yields_chain_truncated(reason):
    """Typed boundary: the coordinator distinguishes truncation by catching
    AlpacaOptionsChainTruncatedError, never by matching exception text."""
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    chain = _FakeOptionsChainClient(error=AlpacaOptionsChainTruncatedError(reason))
    result, _ = _run(
        now=now,
        bars_client=_FakeBarsClient(bars=_trending_bars()),
        market_data_client=_FakeMarketDataClient(
            payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
        ),
        options_chain_client=chain,
    )
    assert result.status == CaptureStatus.CHAIN_TRUNCATED
    assert result.selector_input is None


def test_max_pages_truncation_and_max_contracts_truncation_report_distinct_notes():
    """The two truncation reasons remain distinguishable in the sanitized
    result, via a fixed note -- never the underlying exception text."""
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)

    def _capture_with(reason):
        chain = _FakeOptionsChainClient(error=AlpacaOptionsChainTruncatedError(reason))
        result, _ = _run(
            now=now,
            bars_client=_FakeBarsClient(bars=_trending_bars()),
            market_data_client=_FakeMarketDataClient(
                payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
            ),
            options_chain_client=chain,
        )
        return result

    pages_result = _capture_with(OptionChainTruncationReason.MAX_PAGES_EXCEEDED)
    contracts_result = _capture_with(OptionChainTruncationReason.MAX_TOTAL_CONTRACTS_EXCEEDED)
    assert pages_result.notes != contracts_result.notes
    assert "option_chain_max_pages_exceeded" in pages_result.notes
    assert "option_chain_max_total_contracts_exceeded" in contracts_result.notes


def test_other_chain_failure_yields_chain_unavailable_not_truncated():
    """A real, unrelated AlpacaOptionsChainError (not the typed truncation
    subclass) must never be misclassified as truncation."""
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    chain = _FakeOptionsChainClient(
        error=AlpacaOptionsChainError("Alpaca option-chain request failed with status 500.")
    )
    result, _ = _run(
        now=now,
        bars_client=_FakeBarsClient(bars=_trending_bars()),
        market_data_client=_FakeMarketDataClient(
            payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
        ),
        options_chain_client=chain,
    )
    assert result.status == CaptureStatus.CHAIN_UNAVAILABLE
    assert result.status != CaptureStatus.CHAIN_TRUNCATED


def test_chain_failure_exception_text_never_appears_in_the_result():
    """Requirement: never expose exception text or raw provider content --
    a deliberately distinctive, sensitive-looking exception message must
    never leak into any sanitized result field."""
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    sentinel = "super-secret-request-id-4f9c1e sensitive-provider-payload-xyz"
    chain = _FakeOptionsChainClient(error=AlpacaOptionsChainError(sentinel))
    result, _ = _run(
        now=now,
        bars_client=_FakeBarsClient(bars=_trending_bars()),
        market_data_client=_FakeMarketDataClient(
            payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
        ),
        options_chain_client=chain,
    )
    assert result.status == CaptureStatus.CHAIN_UNAVAILABLE
    rendered = repr(result)
    assert sentinel not in rendered
    assert "super-secret" not in rendered


# ---------------------------------------------------------------------------
# Provenance ordering/lag gate (requirement 3)
# ---------------------------------------------------------------------------


def test_provenance_invalid_when_the_chain_is_retrieved_before_the_price_timestamp():
    """The mocked chain client returns a retrieved_at that precedes the
    underlying price's own market-data timestamp -- an impossible capture --
    which the (unchanged) ContractSelectorInput validator refuses, and the
    coordinator reports it rather than raising."""
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    chain = _FakeOptionsChainClient(retrieved_at="2026-09-16T14:11:00Z")  # before the price ts
    result, _ = _run(
        now=now,
        bars_client=_FakeBarsClient(bars=_trending_bars()),
        market_data_client=_FakeMarketDataClient(
            payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
        ),
        options_chain_client=chain,
    )
    assert result.status == CaptureStatus.PROVENANCE_INVALID
    assert result.selector_input is None
    assert "provenance_ordering_or_lag_violated" in result.notes


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


def test_bar_to_intraday_bar_drops_provider_provenance_and_keeps_price_volume():
    bar = _bar(13, 30, close=Decimal("680.25"))
    intraday = bar_to_intraday_bar(bar)
    assert intraday.timestamp == datetime(2026, 9, 16, 13, 30, tzinfo=UTC)
    assert intraday.close == Decimal("680.25")
    assert intraday.volume == 1000
    assert not hasattr(intraday, "provider")
    assert not hasattr(intraday, "feed")


def test_snapshot_to_quote_drops_provider_and_timestamp_provenance():
    snapshot = _snapshot()
    quote = snapshot_to_quote(snapshot)
    assert quote.contract_symbol == snapshot.contract_symbol
    assert quote.option_type == OptionType.CALL
    assert quote.strike_price == snapshot.strike_price
    assert not hasattr(quote, "provider")
    assert not hasattr(quote, "retrieved_at")
    assert not hasattr(quote, "quote_timestamp")


# ---------------------------------------------------------------------------
# market_features remains offline/pure after this coordinator's relocation
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MARKET_FEATURES_DIR = _REPO_ROOT / "market_intelligence" / "market_features"
_REGIME_ENGINE_FILES = sorted(_MARKET_FEATURES_DIR.glob("spy_regime_*.py"))

_FORBIDDEN_REGIME_ENGINE_IMPORT_PREFIXES = (
    "httpx",
    "duckdb",
    "socket",
    "requests",
    "market_intelligence.data_connectors",
    "market_intelligence.storage",
    "market_intelligence.orchestration",
)


def _imported_module_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_this_coordinator_no_longer_lives_under_market_features():
    """Confirms the relocation: the live-request coordinator must not sit
    beside the pure regime-engine kernel in market_features/ -- see the
    module docstring, "Lives in orchestration/, not market_features/"."""
    assert not (_MARKET_FEATURES_DIR / "spy_contract_capture.py").exists()
    orchestration_module = (
        _REPO_ROOT / "market_intelligence" / "orchestration" / "spy_contract_capture.py"
    )
    assert orchestration_module.exists()


def test_there_are_regime_engine_files_to_scan():
    assert _REGIME_ENGINE_FILES
    assert len(_REGIME_ENGINE_FILES) == 3  # contracts.py, features.py, classifier.py


@pytest.mark.parametrize("path", _REGIME_ENGINE_FILES, ids=lambda p: p.name)
def test_market_features_regime_engine_remains_offline_after_the_move(path):
    """The pure regime-engine kernel this coordinator calls into remains
    fully offline -- this coordinator's relocation out of market_features/
    changed nothing about that boundary. Mirrors
    test_spy_regime_engine_offline.py's own import scan."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for name in _imported_module_names(tree):
        for prefix in _FORBIDDEN_REGIME_ENGINE_IMPORT_PREFIXES:
            assert not (name == prefix or name.startswith(prefix + ".")), (
                f"{path.name} imports forbidden module {name!r}"
            )


# ---------------------------------------------------------------------------
# Price-recency threshold: bounded, on every result, and serializable
# ---------------------------------------------------------------------------


def test_price_recency_threshold_is_recorded_on_every_status_not_just_resolved():
    """Requirement: never an undocumented module constant -- always
    recoverable from the result, even for a status that never reached the
    price check (e.g. OUTSIDE_REGULAR_HOURS)."""
    config = SelectorConfig(max_quote_age_seconds=123)
    result, _ = _run(
        now=datetime(2026, 9, 16, 13, 59, tzinfo=UTC),  # before the gate opens
        selector_config=config,
    )
    assert result.status == CaptureStatus.OUTSIDE_REGULAR_HOURS
    assert result.price_recency_max_age_seconds == 123


def test_price_recency_threshold_is_serialized_and_round_trips(tmp_path):
    """The threshold this run was configured with survives a real write/read
    round trip through the persisted ContractSelectorInput -- so a captured
    manifest can later prove which bound was enforced."""
    from market_intelligence.contract_selection.serialization import read_input, write_input

    config = SelectorConfig(max_quote_age_seconds=123)
    now = datetime(2026, 9, 16, 14, 12, 0, tzinfo=UTC)
    result, _ = _run(
        now=now,
        as_of_timestamp=datetime(2026, 9, 16, 14, 12, 5, tzinfo=UTC),
        bars_client=_FakeBarsClient(bars=_trending_bars()),
        market_data_client=_FakeMarketDataClient(
            payload=_snapshot_payload(trade_price="684.50", trade_ts="2026-09-16T14:11:30Z")
        ),
        options_chain_client=_FakeOptionsChainClient(
            snapshots=(_snapshot(),), retrieved_at="2026-09-16T14:11:50Z"
        ),
        selector_config=config,
    )

    assert result.status == CaptureStatus.RESOLVED
    assert result.price_recency_max_age_seconds == 123
    assert result.selector_input.config.max_quote_age_seconds == 123

    path = tmp_path / "captured_input.json"
    write_input(result.selector_input, path)
    round_tripped = read_input(path)
    assert round_tripped.config.max_quote_age_seconds == 123
    assert round_tripped == result.selector_input
