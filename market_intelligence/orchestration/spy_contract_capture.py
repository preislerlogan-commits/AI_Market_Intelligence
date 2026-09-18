"""Live, read-only, point-in-time-safe capture of one
``ContractSelectorInput`` -- the smallest coordinator needed for the first
real Contract Selector (step e) run.

**Lives in ``orchestration/``, not ``market_features/``.** This module
sequences three live, read-only Alpaca requests across independent
boundaries and applies bounded, provisional gating around them -- it is a
coordinator, exactly the kind of thing ``orchestration/`` already exists for
(see ``orchestration/adapters.py`` / ``runner.py``), not a pure evidence
builder. ``market_intelligence/market_features/spy_regime_contracts.py`` /
``spy_regime_features.py`` / ``spy_regime_classifier.py`` (the regime engine
this coordinator calls into) remain in ``market_features/`` and remain fully
offline/pure -- see ``market_intelligence/tests
/test_spy_regime_engine_offline.py``, which this module never touches or
weakens. Keeping this coordinator out of ``market_features/`` entirely
removes any risk of that package's offline boundary being blurred by a
future refactor.

This module makes exactly the minimum sequence of live, read-only Alpaca
requests the design requires (see ``docs/OPTIONS_DECISION_WORKFLOW.md`` and
the recorded synchronized-capture design), never more:

1. **Bars** (``AlpacaBarsClient.get_bars``) -- SPY 5-minute bars from the
   session open through "now" (``capture_start``, the one instant resolved
   at the very start of the sequence), never a bar completing after it.
2. **Underlying price** (``AlpacaMarketDataClient.get_snapshot``) -- one
   read-only snapshot; the *market-data* timestamp on the trade/quote is
   used as its provenance time, never the request/wall-clock time. Its own
   recency is judged against ``price_validation_time`` -- a second, later
   instant resolved immediately *after* this request returns -- never
   against ``capture_start``: no single wall-clock timestamp governs this
   entire multi-request sequence.
3. **Option chain** (``AlpacaOptionsChainClient.get_chain_snapshot``) -- made
   **only** when the regime and scenario horizon both resolved (never for an
   indeterminate result) -- one bounded, indicative-feed retrieval sized
   from the selector configuration's own moneyness band and the resolved
   horizon's own expiration window.

All three clients are dependency-injected: this module never constructs its
own ``Settings`` or client, and every test exercising it uses fakes/mocks --
**no live request is ever made by importing or testing this module.**

## Gating (in order)

1. **Regular-hours-after-six-bars gate.** The earliest capture time is
   ``10:00`` America/New_York -- the instant the sixth 09:30-grid 5-minute
   bar (09:55-10:00) actually completes -- through ``16:00`` (session close),
   Monday-Friday. Outside that window, **no client is called at all**.
2. **Bar-completeness gate.** Any bar the bars client returns that would not
   yet be complete as of ``capture_start`` (``bar.timestamp + 5min >
   capture_start``) is dropped before it ever reaches the regime engine --
   defensive, independent of trusting the provider's own ``end`` boundary
   semantics, and never relaxed by the later ``price_validation_time``. If
   fewer than
   ``MIN_COMPLETED_BARS`` (6) genuinely-completed bars remain, capture stops
   -- **no price or chain request is made.**
3. **Indeterminate-horizon gate.** The underlying price *is* still fetched
   (so a sanitized preflight result always carries it -- see
   ``CaptureStatus.INDETERMINATE``), but if the regime engine's ``regime``
   or ``scenario_horizon`` is ``INDETERMINATE``, capture stops **before**
   the option-chain request -- the third provider request is never made.
4. **Chain-truncation gate.** ``AlpacaOptionsChainClient`` already fails
   closed (raises, returns no partial result) if the bounded retrieval would
   need more pages or contracts than its own already-reviewed ceilings
   (``MAX_PAGES`` / ``MAX_TOTAL_CONTRACTS``) allow -- this module always
   requests with exactly those ceilings (never a smaller, arbitrary one, and
   never larger). It catches the connector's own public, typed
   ``AlpacaOptionsChainTruncatedError`` (never exception text) and reports
   ``CaptureStatus.CHAIN_TRUNCATED``, distinct from every other chain
   failure (``CaptureStatus.CHAIN_UNAVAILABLE``, from the base
   ``AlpacaOptionsChainError``), so a silently partial eligible set can
   never reach the selector and an unrelated chain failure can never be
   misreported as truncation.
5. **Provenance gate.** The resulting ``ContractSelectorInput`` is
   constructed last; if its own schema-level ordering/lag validator (see
   ``contract_selection.contracts``, "Capture provenance chain") rejects it,
   capture reports ``CaptureStatus.PROVENANCE_INVALID`` rather than
   propagating the exception.

## What this module does not do

No model, agent, recommendation, ranking, order, or brokerage action of any
kind. It never calls ``select_eligible_contracts`` itself -- it only ever
*produces* a ``ContractSelectorInput`` for a separate, later, explicit
invocation of ``scripts/select_spy_option_contracts.py`` (which is what
enforces the ``--allow-indicative-research`` trust boundary). It never
writes to any database and never persists anything by itself (see
``scripts/capture_spy_contract_selector_input.py`` for the dry-run-first
CLI that optionally does, via ``contract_selection.serialization
.write_input``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import ValidationError

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    SelectorConfig,
)
from market_intelligence.data_connectors.alpaca_bars import (
    AlpacaBarsClient,
    AlpacaBarsCredentialsMissingError,
    AlpacaBarsError,
    Bar,
)
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaCredentialsMissingError,
    AlpacaMarketDataClient,
    AlpacaMarketDataError,
)
from market_intelligence.data_connectors.alpaca_options_chain import (
    DEFAULT_LIMIT as CHAIN_DEFAULT_LIMIT,
)
from market_intelligence.data_connectors.alpaca_options_chain import (
    MAX_PAGES as CHAIN_MAX_PAGES,
)
from market_intelligence.data_connectors.alpaca_options_chain import (
    MAX_TOTAL_CONTRACTS as CHAIN_MAX_TOTAL_CONTRACTS,
)
from market_intelligence.data_connectors.alpaca_options_chain import (
    AlpacaOptionsChainClient,
    AlpacaOptionsChainError,
    AlpacaOptionsChainInvalidInputError,
    AlpacaOptionsChainTruncatedError,
    AlpacaOptionsCredentialsMissingError,
    OptionChainSnapshot,
    OptionChainTruncationReason,
    normalize_option_chain_request,
)
from market_intelligence.market_features.spy_regime_classifier import classify
from market_intelligence.market_features.spy_regime_contracts import (
    BAR_INTERVAL_MINUTES,
    EASTERN,
    REGULAR_SESSION_END,
    REGULAR_SESSION_START,
    BreadthState,
    CatalystState,
    IntradayBar,
    Regime,
    RegimeEngineInput,
    ScenarioHorizon,
)
from market_intelligence.market_features.spy_regime_features import compute_features
from market_intelligence.orchestration.clock import Clock, resolve_as_of, system_clock

# Earliest wall-clock time (America/New_York) a capture may run: the instant
# the sixth 09:30-grid 5-minute bar (09:55-10:00) actually completes.
EARLIEST_CAPTURE_TIME = time(10, 0)
MIN_COMPLETED_BARS = 6

# This module always requests the connector's own already-reviewed
# pagination/contract ceilings -- never a smaller, arbitrary one (so a
# genuine truncation report means the requested moneyness/expiration window
# actually needed more than the connector could ever supply in one bounded
# retrieval) and never a larger one.
CHAIN_MAX_PAGES_REQUESTED = CHAIN_MAX_PAGES
CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED = CHAIN_MAX_TOTAL_CONTRACTS

class CaptureStatus(StrEnum):
    """The coordinator's one summary status per capture attempt."""

    RESOLVED = "resolved"
    INDETERMINATE = "indeterminate"
    OUTSIDE_REGULAR_HOURS = "outside_regular_hours"
    INSUFFICIENT_COMPLETED_BARS = "insufficient_completed_bars"
    BARS_UNAVAILABLE = "bars_unavailable"
    PRICE_UNAVAILABLE = "price_unavailable"
    CHAIN_UNAVAILABLE = "chain_unavailable"
    CHAIN_TRUNCATED = "chain_truncated"
    PROVENANCE_INVALID = "provenance_invalid"


class PriceUnavailableError(RuntimeError):
    """Sanitized: never includes the raw snapshot payload."""


@dataclass(frozen=True)
class CaptureResult:
    """Sanitized outcome of one capture attempt.

    Never carries a raw provider payload, a credential, or a URL. Only
    ``status == CaptureStatus.RESOLVED`` carries a non-``None``
    ``selector_input`` -- every other status leaves it ``None``.

    ``price_recency_max_age_seconds`` is always populated -- the exact
    ``SelectorConfig.max_quote_age_seconds`` this run was configured with,
    echoed onto every status (not just ``RESOLVED``, where it is also
    durably recoverable from ``selector_input.config``) -- so a captured
    result can later prove which price-recency threshold governed (or would
    have governed) its own underlying-price check, even for a run that never
    reached that check (e.g. ``BARS_UNAVAILABLE``). Never an undocumented
    module constant: it is always read from the caller-supplied,
    schema-validated ``SelectorConfig``.
    """

    status: CaptureStatus
    price_recency_max_age_seconds: int
    regime_as_of_timestamp: datetime | None = None
    scenario_horizon: ScenarioHorizon | None = None
    underlying_price: Decimal | None = None
    underlying_price_timestamp: datetime | None = None
    option_chain_retrieved_at: datetime | None = None
    option_chain_max_pages: int | None = None
    option_chain_max_total_contracts: int | None = None
    candidate_contract_count: int | None = None
    selector_input: ContractSelectorInput | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


# --- Small pure helpers ------------------------------------------------------------


def _parse_provider_timestamp(value: str) -> datetime:
    """Parse a provider-normalized RFC3339 ("Z"-suffixed) timestamp string."""
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _format_rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _bar_end(bar: Bar) -> datetime:
    return _parse_provider_timestamp(bar.timestamp) + timedelta(minutes=BAR_INTERVAL_MINUTES)


def select_completed_bars(bars: list[Bar], *, now: datetime) -> list[Bar]:
    """Drop any bar that would not yet be complete as of ``now``.

    Defensive and independent of trusting the provider's own ``end``
    request-boundary semantics -- see the module docstring, gate 2.
    """
    return [bar for bar in bars if _bar_end(bar) <= now]


def bar_to_intraday_bar(bar: Bar) -> IntradayBar:
    """Adapt a connector ``Bar`` to the regime engine's own ``IntradayBar``
    shape -- drops provider/feed/adjustment/currency/retrieval provenance,
    which the regime engine deliberately never consumes."""
    return IntradayBar(
        timestamp=_parse_provider_timestamp(bar.timestamp),
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
    )


def snapshot_to_quote(snapshot: OptionChainSnapshot) -> OptionContractQuote:
    """Adapt a connector ``OptionChainSnapshot`` to the selector's own
    ``OptionContractQuote`` shape -- drops provider/underlying/feed/
    retrieval provenance and the raw quote/trade timestamps, which the
    selector deliberately never consumes (feed/retrieval provenance are
    batch-level facts recorded once on ``OptionChainBatch`` instead)."""
    return OptionContractQuote(
        contract_symbol=snapshot.contract_symbol,
        option_type=OptionType(snapshot.option_type),
        expiration_date=date.fromisoformat(snapshot.expiration_date),
        strike_price=snapshot.strike_price,
        bid_price=snapshot.bid_price,
        bid_size=snapshot.bid_size,
        ask_price=snapshot.ask_price,
        ask_size=snapshot.ask_size,
        implied_volatility=snapshot.implied_volatility,
        delta=snapshot.delta,
        gamma=snapshot.gamma,
        theta=snapshot.theta,
        vega=snapshot.vega,
        rho=snapshot.rho,
    )


def _safe_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _safe_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _parse_provider_timestamp(value.strip())
    except ValueError:
        return None


def _is_recent(timestamp: datetime, *, now: datetime, max_age_seconds: int) -> bool:
    """True only if ``timestamp`` is neither in the future relative to
    ``now`` nor older than ``max_age_seconds``."""
    age_seconds = (now - timestamp).total_seconds()
    return 0 <= age_seconds <= max_age_seconds


def extract_underlying_price(
    payload: dict[str, Any], *, now: datetime, max_quote_age_seconds: int
) -> tuple[Decimal, datetime]:
    """Extract a validated, recent underlying price and its own
    *market-data* timestamp from a raw ``get_snapshot`` payload.

    Requirement: "treat the provider price timestamp as market-data time,
    not request time" -- the returned timestamp is always the provider's own
    ``latestTrade.t`` / ``latestQuote.t`` field, never ``now`` or any other
    locally-generated value.

    Prefers the last trade: requires a finite, strictly positive price and a
    recent, parseable trade timestamp. Falls back to the bid/ask midpoint
    **only** when the trade is unusable and the quote's bid and ask are both
    present, strictly positive, non-crossed (``bid <= ask``), and the
    quote's own timestamp is recent. Raises ``PriceUnavailableError``
    (sanitized -- never echoes the payload) if neither is usable.
    """
    trade = payload.get("latestTrade")
    if isinstance(trade, dict):
        price = _safe_decimal(trade.get("p"))
        timestamp = _safe_timestamp(trade.get("t"))
        if (
            price is not None
            and price > 0
            and timestamp is not None
            and _is_recent(timestamp, now=now, max_age_seconds=max_quote_age_seconds)
        ):
            return price, timestamp

    quote = payload.get("latestQuote")
    if isinstance(quote, dict):
        bid = _safe_decimal(quote.get("bp"))
        ask = _safe_decimal(quote.get("ap"))
        timestamp = _safe_timestamp(quote.get("t"))
        if (
            bid is not None
            and ask is not None
            and bid > 0
            and ask > 0
            and bid <= ask
            and timestamp is not None
            and _is_recent(timestamp, now=now, max_age_seconds=max_quote_age_seconds)
        ):
            return (bid + ask) / 2, timestamp

    raise PriceUnavailableError("No usable, recent trade or quote price was available.")


def compute_strike_window(
    underlying_price: Decimal, *, config: SelectorConfig
) -> tuple[Decimal, Decimal]:
    """Requirement 5: the full selector-configured moneyness band, not an
    arbitrary fixed-dollar window -- ``floor(price * min_moneyness)``
    through ``ceil(price * max_moneyness)``, each rounded to a whole
    dollar (the option-chain connector's own strike granularity)."""
    strike_gte = (underlying_price * config.min_moneyness).to_integral_value(
        rounding=ROUND_FLOOR
    )
    strike_lte = (underlying_price * config.max_moneyness).to_integral_value(
        rounding=ROUND_CEILING
    )
    return strike_gte, strike_lte


def compute_expiration_window(
    scenario_horizon: ScenarioHorizon, *, session_date: date, config: SelectorConfig
) -> tuple[str, str]:
    """Requirement 7: the scenario horizon's own existing
    ``SelectorConfig.horizon_expiration_windows`` DTE window -- never a
    fallback window, and never called for an indeterminate horizon (the
    mapping deliberately has no entry for it)."""
    min_dte, max_dte = config.horizon_expiration_windows[scenario_horizon]
    expiration_gte = (session_date + timedelta(days=min_dte)).isoformat()
    expiration_lte = (session_date + timedelta(days=max_dte)).isoformat()
    return expiration_gte, expiration_lte


# --- Coordinator --------------------------------------------------------------------


def capture_contract_selector_input(
    *,
    bars_client: AlpacaBarsClient,
    market_data_client: AlpacaMarketDataClient,
    options_chain_client: AlpacaOptionsChainClient,
    selector_config: SelectorConfig = SelectorConfig(),
    requested_option_type: OptionType | None = None,
    clock: Clock = system_clock,
) -> CaptureResult:
    """Run the full synchronized capture sequence once. See the module
    docstring for the exact gate order.

    Every client is dependency-injected -- this function makes exactly the
    live calls its injected clients make, and no others: a test that injects
    fakes/mocks never touches the network.
    """
    # ``capture_start`` is the one wall-clock reference for everything that
    # must be judged relative to when this capture *began* -- the
    # regular-hours preflight, the bars request's own "through now" upper
    # bound, and completed-bar filtering. It is deliberately never reused to
    # validate the underlying-price observation: that happens against its
    # own later ``price_validation_time``, captured immediately after the
    # price snapshot request returns (see below) -- a live price request can
    # take long enough that judging its recency against the *start* of the
    # whole sequence would be wrong in both directions (too lenient if the
    # price is stale by the time it arrives, too strict if the request
    # itself took a while and a fresh price is penalized for it).
    capture_start = resolve_as_of(clock)
    capture_start_et = capture_start.astimezone(EASTERN)

    if capture_start_et.weekday() >= 5 or not (
        EARLIEST_CAPTURE_TIME <= capture_start_et.time() < REGULAR_SESSION_END
    ):
        return CaptureResult(
            status=CaptureStatus.OUTSIDE_REGULAR_HOURS,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            notes=("outside_regular_trading_hours_after_six_completed_bars",),
        )

    session_date = capture_start_et.date()
    session_open_utc = datetime.combine(
        session_date, REGULAR_SESSION_START, tzinfo=EASTERN
    ).astimezone(UTC)

    try:
        raw_bars = bars_client.get_bars(
            "SPY",
            "5Min",
            start=_format_rfc3339(session_open_utc),
            end=_format_rfc3339(capture_start),
            limit=1000,
            max_pages=1,
        )
    except (AlpacaBarsError, AlpacaBarsCredentialsMissingError):
        return CaptureResult(
            status=CaptureStatus.BARS_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            notes=("bars_request_failed",),
        )

    completed_bars = select_completed_bars(raw_bars, now=capture_start)
    if len(completed_bars) < MIN_COMPLETED_BARS:
        return CaptureResult(
            status=CaptureStatus.INSUFFICIENT_COMPLETED_BARS,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            notes=("fewer_than_six_completed_five_minute_bars",),
        )

    intraday_bars = [bar_to_intraday_bar(bar) for bar in completed_bars]
    try:
        engine_input = RegimeEngineInput(
            session_date=session_date,
            bars=intraday_bars,
            prior_day=None,
            same_time_historical_volume_baseline=None,
            catalyst_state=CatalystState.UNKNOWN,
            breadth_state=BreadthState.UNAVAILABLE,
        )
    except ValidationError:
        return CaptureResult(
            status=CaptureStatus.BARS_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            notes=("regime_engine_input_invalid",),
        )

    features = compute_features(engine_input)
    classification = classify(features)
    regime_as_of = classification.as_of_timestamp

    try:
        snapshot_payload = market_data_client.get_snapshot("SPY")
    except (AlpacaMarketDataError, AlpacaCredentialsMissingError):
        return CaptureResult(
            status=CaptureStatus.PRICE_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            notes=("underlying_price_unavailable",),
        )

    # Captured *after* the snapshot request returns, never reused from
    # ``capture_start`` -- the price's own recency must be judged against
    # when it was actually validated, not when this whole capture began (see
    # ``capture_start``'s comment above).
    price_validation_time = resolve_as_of(clock)

    try:
        price, price_timestamp = extract_underlying_price(
            snapshot_payload,
            now=price_validation_time,
            max_quote_age_seconds=selector_config.max_quote_age_seconds,
        )
    except PriceUnavailableError:
        return CaptureResult(
            status=CaptureStatus.PRICE_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            notes=("underlying_price_unavailable",),
        )

    if (
        classification.regime == Regime.INDETERMINATE
        or classification.scenario_horizon == ScenarioHorizon.INDETERMINATE
    ):
        return CaptureResult(
            status=CaptureStatus.INDETERMINATE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            underlying_price=price,
            underlying_price_timestamp=price_timestamp,
            notes=(
                "regime_or_horizon_indeterminate",
                "stopped_before_option_chain_request",
            ),
        )

    strike_gte, strike_lte = compute_strike_window(price, config=selector_config)
    expiration_gte, expiration_lte = compute_expiration_window(
        classification.scenario_horizon, session_date=session_date, config=selector_config
    )

    try:
        chain_request = normalize_option_chain_request(
            underlying="SPY",
            feed="indicative",
            expiration_date_gte=expiration_gte,
            expiration_date_lte=expiration_lte,
            strike_price_gte=strike_gte,
            strike_price_lte=strike_lte,
            option_type=requested_option_type.value if requested_option_type else None,
            limit=CHAIN_DEFAULT_LIMIT,
            max_pages=CHAIN_MAX_PAGES_REQUESTED,
            max_total_contracts=CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED,
        )
    except AlpacaOptionsChainInvalidInputError:
        return CaptureResult(
            status=CaptureStatus.CHAIN_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            underlying_price=price,
            underlying_price_timestamp=price_timestamp,
            notes=("option_chain_request_invalid",),
        )

    try:
        batch = options_chain_client.get_chain_snapshot(chain_request)
    except AlpacaOptionsCredentialsMissingError:
        return CaptureResult(
            status=CaptureStatus.CHAIN_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            underlying_price=price,
            underlying_price_timestamp=price_timestamp,
            notes=("option_chain_not_configured",),
        )
    except AlpacaOptionsChainTruncatedError as exc:
        # Typed boundary (never exception-text matching) -- see
        # AlpacaOptionsChainTruncatedError / OptionChainTruncationReason.
        # Must be caught before the broader AlpacaOptionsChainError below,
        # since this is a subclass of it.
        note = (
            "option_chain_max_pages_exceeded"
            if exc.reason == OptionChainTruncationReason.MAX_PAGES_EXCEEDED
            else "option_chain_max_total_contracts_exceeded"
        )
        return CaptureResult(
            status=CaptureStatus.CHAIN_TRUNCATED,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            underlying_price=price,
            underlying_price_timestamp=price_timestamp,
            option_chain_max_pages=CHAIN_MAX_PAGES_REQUESTED,
            option_chain_max_total_contracts=CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED,
            notes=(note,),
        )
    except AlpacaOptionsChainError:
        # Every other sanitized connector failure -- never the exception's
        # own text, which is not part of this module's output.
        return CaptureResult(
            status=CaptureStatus.CHAIN_UNAVAILABLE,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            underlying_price=price,
            underlying_price_timestamp=price_timestamp,
            notes=("option_chain_request_failed",),
        )

    as_of_timestamp = resolve_as_of(clock)
    retrieved_at = _parse_provider_timestamp(batch.retrieved_at)
    contracts = [snapshot_to_quote(snapshot) for snapshot in batch.snapshots]

    try:
        selector_input = ContractSelectorInput(
            scenario_horizon=classification.scenario_horizon,
            regime_as_of_timestamp=regime_as_of,
            underlying_price_timestamp=price_timestamp,
            as_of_timestamp=as_of_timestamp,
            underlying_price=price,
            requested_option_type=requested_option_type,
            batch=OptionChainBatch(
                provider="alpaca",
                feed="indicative",
                retrieved_at=retrieved_at,
                contracts=contracts,
            ),
            config=selector_config,
        )
    except ValidationError:
        return CaptureResult(
            status=CaptureStatus.PROVENANCE_INVALID,
            price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
            regime_as_of_timestamp=regime_as_of,
            scenario_horizon=classification.scenario_horizon,
            underlying_price=price,
            underlying_price_timestamp=price_timestamp,
            option_chain_retrieved_at=retrieved_at,
            option_chain_max_pages=CHAIN_MAX_PAGES_REQUESTED,
            option_chain_max_total_contracts=CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED,
            candidate_contract_count=len(contracts),
            notes=("provenance_ordering_or_lag_violated",),
        )

    return CaptureResult(
        status=CaptureStatus.RESOLVED,
        price_recency_max_age_seconds=selector_config.max_quote_age_seconds,
        regime_as_of_timestamp=regime_as_of,
        scenario_horizon=classification.scenario_horizon,
        underlying_price=price,
        underlying_price_timestamp=price_timestamp,
        option_chain_retrieved_at=retrieved_at,
        option_chain_max_pages=CHAIN_MAX_PAGES_REQUESTED,
        option_chain_max_total_contracts=CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED,
        candidate_contract_count=len(contracts),
        selector_input=selector_input,
        notes=("ready_for_selector",),
    )
