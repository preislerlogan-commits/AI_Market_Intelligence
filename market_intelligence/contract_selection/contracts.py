"""Strict Pydantic v2 contracts for the deterministic SPY options-contract
eligibility selector -- Phase 1 step e (see
``docs/OPTIONS_DECISION_WORKFLOW.md``, "Deterministic Contract Selector").

This module defines **only data shapes** -- the validated input the selector
consumes and the validated output it produces. It contains no filtering
logic (see ``selector.py``) and no I/O (see ``serialization.py``).

**Fully offline, independent of the option-chain connector/storage.** This
module deliberately defines its own per-contract shape
(``OptionContractQuote``) rather than importing
``data_connectors.alpaca_options_chain.OptionChainSnapshot`` or anything
from ``market_intelligence.storage`` -- exactly the same pattern
``spy_regime_contracts.py`` uses for its own ``IntradayBar`` (see that
module's docstring): those modules import ``httpx`` and/or ``duckdb``, and
this selector must never import a data connector, a model client, storage,
an agent, orchestration, or any networked/database dependency. It **does**
import ``ScenarioHorizon`` from
``market_intelligence.market_features.spy_regime_contracts`` -- reusing the
one upstream-validated horizon enum is required by design (the selector
must consume the *same* horizon value the regime engine produces, never a
redefinition of it), and that module is itself fully offline. See
``market_intelligence/tests/test_contract_selector_offline.py`` for the
static and fresh-interpreter proof of the offline boundary.

**No field anywhere in this module carries news text, model output, a
credential, a database path, or brokerage data.** ``ContractSelectorInput``
carries exactly: SPY; one validated ``ScenarioHorizon``; one bounded
``OptionChainBatch`` with one retrieval instant; the underlying price and
as-of time; an optional explicit directional side; and bounded
``SelectorConfig``.

**Open interest is unavailable from the current option-chain endpoint.**
``OptionContractQuote`` has deliberately **no open-interest field** -- it is
never invented, inferred, or replaced with volume anywhere in this package
(see ``DATA_CATALOG.md``, "SPY option-chain snapshots (indicative)").

**Missing is never zero.** Every quote/trade/Greek field an upstream
snapshot may legitimately omit is optional and left ``None`` when absent --
a missing implied volatility, Greek, bid, or ask is never treated as zero
anywhere in this package (see ``selector.py``, "required IV and Greeks").

## Feed-safety boundary (operational vs. research)

**Operational eligibility (``SelectorStatus.ELIGIBLE``) defaults to OPRA
only, and an indicative-feed batch can never produce it, structurally.**
``SelectorConfig.allow_indicative_for_research`` defaults to ``False``. A
batch's ``feed`` (``OptionChainBatch.feed``) is a single value shared by
every contract in it (this project's option-chain connector retrieves one
feed per request), so feed governance is a **batch-level** decision, not a
per-contract one -- see ``selector.py``. When ``feed`` is ``indicative``:

- if ``allow_indicative_for_research`` is ``False`` (the default), the
  entire batch is rejected before any per-contract filter runs (see
  ``RejectionReason.FEED_NOT_ALLOWED``), and the result status is
  ``NO_ELIGIBLE_CONTRACTS`` -- never ``ELIGIBLE``;
- if ``allow_indicative_for_research`` is explicitly ``True``, the batch may
  be processed, but the result status is always ``SelectorStatus
  .RESEARCH_ONLY`` -- never ``ELIGIBLE`` -- and any contracts that pass
  every per-contract filter are placed in
  ``ContractSelectorResult.research_only_contracts``, a field **structurally
  separate from** ``eligible_contracts``. ``eligible_contracts`` is empty
  whenever ``status != ELIGIBLE``, enforced by validator below, and
  ``status == ELIGIBLE`` is itself only satisfiable when ``feed == opra``
  (also enforced by validator). A future strategy agent that consumes only
  ``ContractSelectorResult.eligible_contracts`` under ``status == ELIGIBLE``
  therefore cannot receive indicative-sourced contracts no matter how
  ``SelectorConfig`` is set -- the separation is a schema-level guarantee,
  not just selector-logic discipline.

``ContractSelectorResult.feed_is_live_opra`` always reports the truth (a
validator enforces it equals ``feed == FeedProvenance.OPRA``), and
``research_only`` results always carry a fixed
``research_only_not_operationally_eligible`` note in addition to the
existing ``indicative_feed_non_live_non_opra`` note.

**Every threshold in ``SelectorConfig`` is a provisional hypothesis, not a
validated value** -- mirrors ``spy_regime_classifier.RegimeThresholds``: no
threshold here has been evaluated against real SPY option-chain history, and
none may be tuned against the step-d VWAP-reversion evaluation or the single
stored option batch (see ``docs/OPTIONS_DECISION_WORKFLOW.md``).

## Capture provenance chain (regime -> price -> chain -> selector)

``ContractSelectorInput`` retains two additional provenance timestamps
beyond ``as_of_timestamp``: ``regime_as_of_timestamp`` (the upstream regime
engine's own ``RegimeClassificationResult.as_of_timestamp`` -- the completion
time of the last bar it used) and ``underlying_price_timestamp`` (the
market-data timestamp of the underlying price observation, never a request/
retrieval timestamp -- see ``orchestration.spy_contract_capture`` for how a
live capture populates both). Together with ``batch.retrieved_at`` and
``as_of_timestamp`` themselves, these four timestamps describe one full,
sequential, point-in-time-safe capture:

    regime_as_of_timestamp <= underlying_price_timestamp
        <= batch.retrieved_at <= as_of_timestamp

**Only the first two legs of this chain are hardened here, at construction
time** (see ``_check_provenance_ordering_and_lag`` below): a
``ContractSelectorInput`` cannot be constructed at all if
``underlying_price_timestamp`` precedes ``regime_as_of_timestamp``, if
``batch.retrieved_at`` precedes ``underlying_price_timestamp``, or if either
gap exceeds its own configured maximum (``SelectorConfig
.max_regime_to_price_gap_seconds`` / ``.max_price_to_chain_gap_seconds``).
**The final leg -- ``batch.retrieved_at`` vs. ``as_of_timestamp`` --
deliberately remains the pre-existing, unchanged ``selector.py`` freshness
gate** (``RejectionReason.SNAPSHOT_STALE`` / ``SNAPSHOT_FROM_FUTURE``,
governed by ``SelectorConfig.max_snapshot_age_seconds``): that gate already
treats a stale or future-dated batch as a reportable, structurally distinct
*business outcome* of a selector run, not a caller/programming error, and a
substantial existing test suite (``test_contract_selector.py``) depends on
being able to construct exactly those stale/future-dated inputs and observe
the selector report them. Hardening that specific leg into a construction-
time error here would make those two already-tested, intentional outcomes
unreachable, so it is left exactly as it was.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon

SCHEMA_VERSION = "spy-contract-selector-1"

# This selector has exactly one supported underlying, enforced in the type
# system (not just a runtime check) everywhere a symbol field appears.
SUPPORTED_SYMBOL = "SPY"

# A full bounded SPY chain slice under the existing connector's own ceiling
# (data_connectors.alpaca_options_chain.MAX_TOTAL_CONTRACTS); this module
# never imports that connector, so the ceiling is duplicated here as a
# structural bound, not a claim of connector parity.
MAX_CONTRACTS_PER_BATCH = 5_000
MAX_CONTRACT_SYMBOL_LENGTH = 30

_MAX_PRICE = Decimal("1000000")
_MAX_GREEK = Decimal("1000")


# --- Small shared field validators -----------------------------------------------


def _require_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _require_finite(value: Decimal) -> None:
    if not value.is_finite():
        raise ValueError("value must be a finite number")


def _require_finite_positive(value: Decimal) -> Decimal:
    _require_finite(value)
    if value <= 0:
        raise ValueError("value must be greater than zero")
    if value > _MAX_PRICE:
        raise ValueError("value exceeds the maximum supported magnitude")
    return value


def _validate_optional_nonneg_price(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    _require_finite(value)
    if value < 0:
        raise ValueError("value must not be negative")
    if value > _MAX_PRICE:
        raise ValueError("value exceeds the maximum supported magnitude")
    return value


def _validate_optional_greek(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    _require_finite(value)
    if abs(value) > _MAX_GREEK:
        raise ValueError("value exceeds the maximum supported magnitude")
    return value


_AwareUtcTimestamp = Annotated[datetime, AfterValidator(_require_utc_aware)]
_FinitePositivePrice = Annotated[Decimal, AfterValidator(_require_finite_positive)]
_OptionalNonNegPrice = Annotated[Decimal | None, AfterValidator(_validate_optional_nonneg_price)]
_OptionalGreek = Annotated[Decimal | None, AfterValidator(_validate_optional_greek)]
_ShortToken = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]
_ContractSymbol = Annotated[
    str, Field(min_length=1, max_length=MAX_CONTRACT_SYMBOL_LENGTH, pattern=r"^[A-Za-z0-9]+$")
]


# --- Fixed enums -------------------------------------------------------------------


class OptionType(StrEnum):
    """A contract's own type, as already parsed and validated upstream."""

    CALL = "call"
    PUT = "put"


class FeedProvenance(StrEnum):
    """Explicit feed provenance for the whole batch. Mirrors
    ``data_connectors.alpaca_options_chain.ALLOWED_FEEDS`` without importing
    that module -- this package is offline-only (see the module docstring).
    ``INDICATIVE`` data may be delayed or modified by the provider and must
    never be described as live OPRA data -- see
    ``ContractSelectorResult.feed_is_live_opra`` and the module docstring's
    "Feed-safety boundary" section."""

    OPRA = "opra"
    INDICATIVE = "indicative"


class SelectorStatus(StrEnum):
    """The selector's one summary status per run.

    ``INDETERMINATE`` is produced whenever ``ContractSelectorInput
    .scenario_horizon`` is ``ScenarioHorizon.INDETERMINATE`` -- in that case
    no per-contract filter is evaluated and both ``eligible_contracts`` and
    ``research_only_contracts`` are always empty (see ``selector.py``).

    ``ELIGIBLE`` means at least one contract passed every operational
    filter on an **OPRA-feed** batch -- see the module docstring's
    "Feed-safety boundary": an indicative-feed batch can never reach this
    status, enforced both in ``selector.py`` and by a validator on
    ``ContractSelectorResult`` below.

    ``NO_ELIGIBLE_CONTRACTS`` means an OPRA-feed batch was fully evaluated
    but no contract passed every filter, **or** an indicative-feed batch was
    rejected outright because indicative research was not explicitly
    allowed (``SelectorConfig.allow_indicative_for_research=False``, the
    default).

    ``RESEARCH_ONLY`` means the batch's feed was ``indicative`` and
    indicative research was explicitly allowed
    (``allow_indicative_for_research=True``); any contracts that pass every
    per-contract filter are placed in ``research_only_contracts``, never
    ``eligible_contracts`` -- this status can never be mistaken for, or
    silently treated as, an operational eligible set."""

    ELIGIBLE = "eligible"
    NO_ELIGIBLE_CONTRACTS = "no_eligible_contracts"
    RESEARCH_ONLY = "research_only"
    INDETERMINATE = "indeterminate"


class RejectionReason(StrEnum):
    """Fixed, bounded rejection-reason enum.

    ``HORIZON_INDETERMINATE``, ``FEED_NOT_ALLOWED``, ``SNAPSHOT_FROM_FUTURE``,
    and ``SNAPSHOT_STALE`` are **batch-level** gates, each evaluated exactly
    once per run, in that order, before any per-contract filter runs -- when
    one of them fires, every candidate contract in the batch is counted
    under that single reason and no per-contract filter is evaluated for any
    of them (see ``selector.py``). This ordering exists specifically so a
    stale, future-dated, or feed-disallowed batch is never disguised as a
    pile of individual contract-quality failures.

    ``SNAPSHOT_FROM_FUTURE`` and ``SNAPSHOT_STALE`` are two distinct
    freshness gates, not one: ``age_seconds = (as_of_timestamp -
    retrieved_at).total_seconds()`` is computed **without** ``abs()``, so a
    batch retrieved after ``as_of_timestamp`` (``age_seconds < 0`` -- clock
    skew or a caller error) is always rejected as ``SNAPSHOT_FROM_FUTURE``,
    distinct from an old batch (``age_seconds > max_snapshot_age_seconds``)
    rejected as ``SNAPSHOT_STALE``. The two can never both fire for the same
    batch.

    Every other reason is evaluated per contract, in the fixed order
    ``selector.py`` publishes; the first filter a contract fails is the one
    and only reason recorded for it -- a contract is never counted under
    more than one reason.
    """

    HORIZON_INDETERMINATE = "horizon_indeterminate"
    FEED_NOT_ALLOWED = "feed_not_allowed"
    SNAPSHOT_FROM_FUTURE = "snapshot_from_future"
    SNAPSHOT_STALE = "snapshot_stale"
    EXPIRATION_OUTSIDE_WINDOW = "expiration_outside_window"
    OPTION_TYPE_MISMATCH = "option_type_mismatch"
    STRIKE_OUTSIDE_MONEYNESS_BAND = "strike_outside_moneyness_band"
    DELTA_OUTSIDE_RANGE = "delta_outside_range"
    MISSING_IV_OR_GREEKS = "missing_iv_or_greeks"
    NON_POSITIVE_BID_ASK = "non_positive_bid_ask"
    CROSSED_QUOTE = "crossed_quote"
    SPREAD_TOO_WIDE = "spread_too_wide"
    QUOTE_SIZE_TOO_SMALL = "quote_size_too_small"


# --- Input contracts: per-contract / batch ------------------------------------------


class OptionContractQuote(BaseModel):
    """One normalized SPY option-chain contract observation, already
    validated and parsed by an upstream connector/storage stage.

    Deliberately narrower than, and structurally independent of,
    ``data_connectors.alpaca_options_chain.OptionChainSnapshot`` -- see the
    module docstring. There is deliberately **no open-interest field**: the
    option-chain snapshot endpoint this project uses does not supply open
    interest, and this selector never invents, infers, or substitutes a
    value for it. Every quote/Greek field the upstream snapshot may
    legitimately omit is optional and left ``None`` -- a missing value is
    never treated as zero anywhere in this package.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_symbol: _ContractSymbol
    option_type: OptionType
    expiration_date: date
    strike_price: _FinitePositivePrice

    bid_price: _OptionalNonNegPrice = None
    bid_size: Annotated[int, Field(ge=0)] | None = None
    ask_price: _OptionalNonNegPrice = None
    ask_size: Annotated[int, Field(ge=0)] | None = None

    implied_volatility: _OptionalNonNegPrice = None
    delta: _OptionalGreek = None
    gamma: _OptionalGreek = None
    theta: _OptionalGreek = None
    vega: _OptionalGreek = None
    rho: _OptionalGreek = None


class OptionChainBatch(BaseModel):
    """One bounded, already-retrieved SPY option-chain snapshot batch --
    exactly the shape the selector consumes: **one** underlying, **one**
    feed, and **one** retrieval instant shared by every contract in
    ``contracts`` (mirrors
    ``data_connectors.alpaca_options_chain.OptionChainSnapshotBatch``,
    without importing it -- see the module docstring). ``feed`` being a
    single batch-wide value is exactly why feed governance is a
    batch-level gate in ``selector.py``, not a per-contract filter."""

    model_config = ConfigDict(extra="forbid")

    provider: _ShortToken
    underlying: Literal["SPY"] = SUPPORTED_SYMBOL
    feed: FeedProvenance
    retrieved_at: _AwareUtcTimestamp
    contracts: Annotated[list[OptionContractQuote], Field(max_length=MAX_CONTRACTS_PER_BATCH)]

    @model_validator(mode="after")
    def _check_no_duplicate_contract_symbols(self) -> OptionChainBatch:
        symbols = [contract.contract_symbol for contract in self.contracts]
        if len(symbols) != len(set(symbols)):
            raise ValueError("contracts must not contain a duplicate contract_symbol")
        return self


# --- Selector configuration ---------------------------------------------------------


# Maps each *non-indeterminate* ScenarioHorizon to an inclusive
# (min_dte, max_dte) calendar-day expiration window -- see selector.py for
# exactly how DTE is computed. Provisional hypotheses, not validated values;
# never tuned against any evaluation result (see the module docstring).
DEFAULT_HORIZON_EXPIRATION_WINDOWS: dict[ScenarioHorizon, tuple[int, int]] = {
    ScenarioHorizon.INTRADAY_30M: (0, 2),
    ScenarioHorizon.INTRADAY_2H: (0, 3),
    ScenarioHorizon.TO_SESSION_CLOSE: (0, 1),
    ScenarioHorizon.NEXT_SESSION: (1, 5),
}

_CONFIGURABLE_HORIZONS = frozenset(DEFAULT_HORIZON_EXPIRATION_WINDOWS)

# The option-chain connector itself never retrieves an expiration more than
# this many calendar days out (data_connectors.alpaca_options_chain
# .MAX_EXPIRATION_RANGE_DAYS) -- duplicated here as a structural ceiling so
# a caller-configured window can never exceed what the connector could ever
# have supplied, without this module importing that connector.
_MAX_SUPPORTED_DTE = 60


class SelectorConfig(BaseModel):
    """Every selector filter threshold, centralized and explicit -- mirrors
    ``spy_regime_classifier.RegimeThresholds``.

    All defaults are provisional hypotheses (see the module docstring); none
    has been evaluated against real SPY option-chain history by this
    project, and none may be tuned against the step-d VWAP-reversion
    evaluation or the single stored option batch.

    ``horizon_expiration_windows`` maps each non-``indeterminate``
    ``ScenarioHorizon`` to an inclusive ``(min_dte, max_dte)`` window.
    ``indeterminate`` is deliberately absent from this mapping and can never
    be added to it -- an indeterminate scenario horizon always short-circuits
    to an empty eligible set before any window lookup (see ``selector.py``).

    ``allow_indicative_for_research`` defaults to ``False`` -- operational
    eligibility (``SelectorStatus.ELIGIBLE``) is OPRA-only by default. See
    the module docstring's "Feed-safety boundary" section. A caller building
    this field directly (bypassing a CLI) is still bound by this default;
    the ``select_spy_option_contracts.py`` CLI additionally requires its own
    explicit ``--allow-indicative-research`` flag and always overrides
    whatever this field says with the flag's value (see that script and
    ``docs/OPTIONS_DECISION_WORKFLOW.md``), so indicative processing can
    never happen from a JSON input file alone when driven through the CLI.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_expiration_windows: dict[
        ScenarioHorizon, tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]]
    ] = Field(default_factory=lambda: dict(DEFAULT_HORIZON_EXPIRATION_WINDOWS))

    min_moneyness: Decimal = Decimal("0.85")
    max_moneyness: Decimal = Decimal("1.15")

    min_abs_delta: Decimal = Decimal("0.15")
    max_abs_delta: Decimal = Decimal("0.65")

    max_abs_spread: Decimal = Decimal("0.50")
    max_pct_spread: Decimal = Decimal("0.15")

    min_quote_size: Annotated[int, Field(ge=0)] = 1

    max_snapshot_age_seconds: Annotated[int, Field(gt=0)] = 300

    # Bounded lag rules for the first two legs of the capture provenance
    # chain -- see the module docstring, "Capture provenance chain".
    # Provisional, like every other threshold here: never tuned against any
    # evaluation result.
    max_regime_to_price_gap_seconds: Annotated[int, Field(gt=0)] = 300
    max_price_to_chain_gap_seconds: Annotated[int, Field(gt=0)] = 60

    # How old a live underlying-price observation (trade or quote) may be,
    # relative to the coordinator's own price_validation_time (captured
    # immediately after the price snapshot request returns, not the
    # capture's initial start time), before a live capture must treat it as
    # unavailable rather than stale -- used only by
    # ``orchestration.spy_contract_capture``, not by this package's own
    # selector logic, but centralized here with every other threshold.
    max_quote_age_seconds: Annotated[int, Field(gt=0)] = 300

    allow_indicative_for_research: bool = False

    @model_validator(mode="after")
    def _check_horizon_windows_cover_every_configurable_horizon(self) -> SelectorConfig:
        if set(self.horizon_expiration_windows) != _CONFIGURABLE_HORIZONS:
            raise ValueError(
                "horizon_expiration_windows must have exactly one entry per "
                "non-indeterminate ScenarioHorizon, and no others"
            )
        for horizon, (min_dte, max_dte) in self.horizon_expiration_windows.items():
            if min_dte > max_dte:
                raise ValueError(f"{horizon}: min_dte must not exceed max_dte")
            if max_dte > _MAX_SUPPORTED_DTE:
                raise ValueError(
                    f"{horizon}: max_dte must not exceed {_MAX_SUPPORTED_DTE} -- the "
                    "option-chain connector itself never retrieves an expiration "
                    "further out than that"
                )
        return self

    @model_validator(mode="after")
    def _check_moneyness_band(self) -> SelectorConfig:
        if not (Decimal(0) < self.min_moneyness <= Decimal(1) <= self.max_moneyness):
            raise ValueError("min_moneyness must be in (0, 1] and max_moneyness must be >= 1")
        return self

    @model_validator(mode="after")
    def _check_delta_band(self) -> SelectorConfig:
        if not (Decimal(0) <= self.min_abs_delta <= self.max_abs_delta <= Decimal(1)):
            raise ValueError(
                "min_abs_delta and max_abs_delta must satisfy "
                "0 <= min_abs_delta <= max_abs_delta <= 1"
            )
        return self

    @model_validator(mode="after")
    def _check_spread_bounds(self) -> SelectorConfig:
        if self.max_abs_spread <= 0:
            raise ValueError("max_abs_spread must be greater than zero")
        if not (Decimal(0) < self.max_pct_spread <= Decimal(1)):
            raise ValueError("max_pct_spread must be in (0, 1]")
        return self


# --- Input contract ------------------------------------------------------------------


class ContractSelectorInput(BaseModel):
    """One validated request to select eligible SPY option contracts.

    Carries exactly the fields the design requires (see
    ``docs/OPTIONS_DECISION_WORKFLOW.md``, "Deterministic Contract
    Selector"): SPY only; one already-validated scenario-horizon bucket from
    the upstream regime engine; one option-chain batch with one retrieval
    instant; the underlying price and as-of time; and bounded selector
    configuration. There is no field anywhere in this model for news text,
    model output, a credential, a database path, or brokerage data.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Literal["SPY"] = SUPPORTED_SYMBOL
    scenario_horizon: ScenarioHorizon
    regime_as_of_timestamp: _AwareUtcTimestamp
    underlying_price_timestamp: _AwareUtcTimestamp
    as_of_timestamp: _AwareUtcTimestamp
    underlying_price: _FinitePositivePrice
    requested_option_type: OptionType | None = None
    batch: OptionChainBatch
    config: SelectorConfig = Field(default_factory=SelectorConfig)

    @model_validator(mode="after")
    def _check_batch_underlying_matches_symbol(self) -> ContractSelectorInput:
        if self.batch.underlying != self.symbol:
            raise ValueError("batch.underlying must match symbol")
        return self

    @model_validator(mode="after")
    def _check_provenance_ordering_and_lag(self) -> ContractSelectorInput:
        """Enforce the first two legs of the capture provenance chain -- see
        the module docstring, "Capture provenance chain", for exactly which
        leg is (and is not) hardened here and why."""
        regime_ts = self.regime_as_of_timestamp
        price_ts = self.underlying_price_timestamp
        retrieved_at = self.batch.retrieved_at
        if not (regime_ts <= price_ts <= retrieved_at):
            raise ValueError(
                "provenance timestamps must satisfy regime_as_of_timestamp <= "
                "underlying_price_timestamp <= batch.retrieved_at"
            )
        if (price_ts - regime_ts).total_seconds() > self.config.max_regime_to_price_gap_seconds:
            raise ValueError(
                "underlying_price_timestamp is too far after regime_as_of_timestamp "
                "(exceeds config.max_regime_to_price_gap_seconds)"
            )
        if (retrieved_at - price_ts).total_seconds() > self.config.max_price_to_chain_gap_seconds:
            raise ValueError(
                "batch.retrieved_at is too far after underlying_price_timestamp "
                "(exceeds config.max_price_to_chain_gap_seconds)"
            )
        return self


# --- Output contracts -----------------------------------------------------------------


class SelectorConfigSnapshot(BaseModel):
    """A record (never a re-derivation) of the exact selector configuration
    a given run used -- mirrors
    ``spy_vwap_reversion_contracts.RegimeThresholdsSnapshot``. This module
    never tunes, optimizes, or grid-searches any of these values; this
    snapshot exists purely for audit-trail purposes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    horizon_expiration_windows: dict[ScenarioHorizon, tuple[int, int]]
    min_moneyness: Decimal
    max_moneyness: Decimal
    min_abs_delta: Decimal
    max_abs_delta: Decimal
    max_abs_spread: Decimal
    max_pct_spread: Decimal
    min_quote_size: int
    max_snapshot_age_seconds: int
    max_regime_to_price_gap_seconds: int
    max_price_to_chain_gap_seconds: int
    max_quote_age_seconds: int
    allow_indicative_for_research: bool


class ContractSelectorResult(BaseModel):
    """The complete, deterministic, sanitized output of one contract-
    selection run.

    Contains **no recommendation, ranking, score, prediction, or trade
    action** -- there is simply no field for any of them (see
    ``DECISION_RULES.md``, "AI does not choose an unrestricted options
    contract"). Contains no P&L, credential, database path, raw provider
    payload, or brokerage field of any kind.

    ``eligible_contracts`` (the operational eligible set a future strategy
    agent would consume) and ``research_only_contracts`` (indicative-feed
    candidates under explicit research mode) are structurally exclusive --
    validators below enforce that ``eligible_contracts`` is non-empty only
    when ``status == ELIGIBLE`` (which itself requires ``feed == opra``),
    and that ``research_only_contracts`` is non-empty only when
    ``status == RESEARCH_ONLY`` (which itself requires
    ``feed == indicative``). See the module docstring's "Feed-safety
    boundary" section.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    symbol: Literal["SPY"]
    generated_at: _AwareUtcTimestamp

    scenario_horizon: ScenarioHorizon
    requested_option_type: OptionType | None
    regime_as_of_timestamp: _AwareUtcTimestamp
    underlying_price_timestamp: _AwareUtcTimestamp
    as_of_timestamp: _AwareUtcTimestamp
    underlying_price: Decimal

    feed: FeedProvenance
    feed_is_live_opra: bool
    retrieved_at: _AwareUtcTimestamp

    config_snapshot: SelectorConfigSnapshot

    status: SelectorStatus
    candidate_contract_count: Annotated[int, Field(ge=0)]
    eligible_contract_count: Annotated[int, Field(ge=0)]
    research_only_contract_count: Annotated[int, Field(ge=0)]
    rejection_counts: dict[RejectionReason, Annotated[int, Field(ge=0)]]
    eligible_contracts: Annotated[
        list[OptionContractQuote], Field(max_length=MAX_CONTRACTS_PER_BATCH)
    ]
    research_only_contracts: Annotated[
        list[OptionContractQuote], Field(max_length=MAX_CONTRACTS_PER_BATCH)
    ]

    notes: Annotated[list[_ShortToken], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def _check_feed_is_live_opra_is_truthful(self) -> ContractSelectorResult:
        expected = self.feed == FeedProvenance.OPRA
        if self.feed_is_live_opra != expected:
            raise ValueError("feed_is_live_opra must equal (feed == FeedProvenance.OPRA)")
        return self

    @model_validator(mode="after")
    def _check_eligible_status_requires_opra_feed(self) -> ContractSelectorResult:
        """Structural enforcement of the feed-safety boundary: no
        combination of selector inputs can ever produce an ``ELIGIBLE``
        result whose feed is not ``opra`` -- this holds even if the
        selector logic itself had a bug, because construction of the
        result model refuses it outright."""
        if self.status == SelectorStatus.ELIGIBLE and self.feed != FeedProvenance.OPRA:
            raise ValueError("status=eligible requires feed=opra")
        return self

    @model_validator(mode="after")
    def _check_research_only_status_requires_indicative_feed(self) -> ContractSelectorResult:
        if self.status == SelectorStatus.RESEARCH_ONLY and self.feed != FeedProvenance.INDICATIVE:
            raise ValueError("status=research_only requires feed=indicative")
        return self

    @model_validator(mode="after")
    def _check_eligible_contracts_match_status(self) -> ContractSelectorResult:
        if self.eligible_contract_count != len(self.eligible_contracts):
            raise ValueError("eligible_contract_count must equal len(eligible_contracts)")
        if self.status == SelectorStatus.ELIGIBLE:
            if self.eligible_contract_count == 0:
                raise ValueError("an eligible result must have at least one eligible contract")
        elif self.eligible_contract_count != 0:
            raise ValueError("eligible_contracts must be empty unless status is eligible")
        return self

    @model_validator(mode="after")
    def _check_research_only_contracts_match_status(self) -> ContractSelectorResult:
        if self.research_only_contract_count != len(self.research_only_contracts):
            raise ValueError(
                "research_only_contract_count must equal len(research_only_contracts)"
            )
        if self.status != SelectorStatus.RESEARCH_ONLY and self.research_only_contract_count != 0:
            raise ValueError("research_only_contracts must be empty unless status is research_only")
        return self

    @model_validator(mode="after")
    def _check_rejection_counts_are_complete_and_consistent(self) -> ContractSelectorResult:
        if set(self.rejection_counts) != set(RejectionReason):
            raise ValueError("rejection_counts must have exactly one entry per RejectionReason")
        total_rejected = sum(self.rejection_counts.values())
        accounted_for = (
            total_rejected + self.eligible_contract_count + self.research_only_contract_count
        )
        if accounted_for != self.candidate_contract_count:
            raise ValueError(
                "rejection_counts plus eligible_contract_count plus "
                "research_only_contract_count must equal candidate_contract_count"
            )
        horizon_indeterminate_count = self.rejection_counts[RejectionReason.HORIZON_INDETERMINATE]
        if self.status == SelectorStatus.INDETERMINATE:
            if horizon_indeterminate_count != self.candidate_contract_count:
                raise ValueError(
                    "an indeterminate result must count every candidate under "
                    "horizon_indeterminate"
                )
        elif horizon_indeterminate_count != 0:
            raise ValueError("horizon_indeterminate must be zero unless status is indeterminate")
        return self
