"""Strict Pydantic v2 contracts for the offline SPY VWAP-extension /
reversion evaluation -- step d of Phase 1 (see
``docs/OPTIONS_DECISION_WORKFLOW.md``).

This evaluates the **underlying SPY setup only**: whether price extended
away from its signal-time session VWAP tends to move back toward (or away
from) that frozen reference over fixed forward horizons. It is not an
options, contract, recommendation, alert, agent, or execution evaluation --
there is no field anywhere in this module for an option contract, a
strategy, a P&L figure, a win rate, or a trade recommendation. See
``DECISION_RULES.md`` ("A large VWAP extension alone is not a trade
signal") and ``docs/OPTIONS_DECISION_WORKFLOW.md`` ("Evaluation stages").

**Offline core.** This module defines data shapes only -- no feature math,
no outcome scoring (see ``spy_vwap_reversion_evaluator.py``), and no I/O
(see ``spy_vwap_reversion_serialization.py``). It imports the existing,
already-offline SPY intraday regime engine contracts
(``market_intelligence.market_features.spy_regime_contracts``) to reuse
their bar shape, upstream-validated enums, and regime/horizon output enums
-- consuming "outputs from the existing regime engine" is required by
design, not a boundary violation. It imports nothing from
``market_intelligence.data_connectors``, ``market_intelligence.storage``,
``market_intelligence.model_clients``, ``market_intelligence.agents``,
``market_intelligence.orchestration``, or ``market_intelligence.config``,
and makes no network or database call. See
``market_intelligence/tests/test_spy_vwap_reversion_offline.py`` for the
static and fresh-interpreter proof.

**Frozen hypothesis.** Every threshold this evaluation exercises is the
*existing, unmodified* ``RegimeThresholds`` from
``spy_regime_classifier.py`` -- this evaluation does not tune, optimize, or
grid-search anything; see ``RegimeThresholdsSnapshot`` below, which records
(never derives) the exact threshold values a given evaluation run used, for
audit purposes only.

**No lookahead by construction, at the signal.** A decision point's regime,
horizon, VWAP, and extension are always computed from a bar prefix ending at
the signal bar -- exactly the same no-lookahead guarantee already proven for
``spy_regime_features.compute_features`` (see
``spy_regime_features.py``, "No lookahead"). Future bars are consumed **only**
by the outcome-scoring stage (``spy_vwap_reversion_evaluator.py``), which is
the entire point of an *evaluation* -- see that module for the exact forward
window and formulas.

**The frozen VWAP reference.** ``DecisionPointRecord.signal_vwap`` is the
signal-time session VWAP (``RegimeFeatures.session_vwap``) at the moment the
decision point was recorded. Every outcome for that decision point (in every
``HorizonOutcome``) is scored against this **same, frozen** value -- it is
never recomputed at a later bar, so the target cannot move after the signal.

**Unavailable is never zero, never shortened, never guessed.** A
``HorizonOutcome`` with ``available=False`` has every other field ``None`` --
enforced by validator, not just convention. A non-eligible
``DecisionPointRecord`` (VWAP unavailable, or the extension is exactly zero)
has no ``extension_side`` and no available horizon outcome -- also enforced.
Indeterminate is a first-class, recorded outcome, never silently dropped: an
ineligible or indeterminate-regime decision point is still one row in
``SpyVwapReversionEvaluationRecord.decision_points``, counted in the fixed
enum-count dictionaries below.

**No misleading statistics.** ``DescriptiveStats`` refuses to report a
mean/median/min/max below its sample-size threshold -- it reports
``insufficient_sample`` instead (see ``SampleThresholdsSnapshot`` and
``spy_vwap_reversion_evaluator.py``). Every descriptive summary is reported
twice: once pooling overlapping five-minute observations naively
(``observation_level``) and once first aggregating to one number per session
(``session_level``) -- see ``HorizonDescriptiveBundle``. One stored
three-day dataset cannot establish an edge; this module only makes the
insufficiency machine-checkable, it does not lower the bar to make a small
run look conclusive.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator

from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    PriorDayLevels,
    Regime,
    RegimeEngineInput,
    ScenarioHorizon,
    TimeOfDayBucket,
)

SCHEMA_VERSION = "spy-vwap-reversion-evaluation-1"

# The engine this evaluation exercises supports exactly one underlying.
SUPPORTED_SYMBOL = "SPY"

# Generous but bounded caps. A single regular session is at most 90 bars
# (see spy_regime_contracts.MAX_BARS_PER_INPUT); MAX_SESSIONS is roughly six
# months of trading days -- deliberately far beyond the ~3 trading days
# actually stored today (see DATA_CATALOG.md), so this is a structural
# ceiling, not a claim that a large sample exists.
MAX_BARS_PER_SESSION = 90
MAX_SESSIONS = 130
MAX_DECISION_POINTS = MAX_SESSIONS * MAX_BARS_PER_SESSION


# --- Small shared field validators -----------------------------------------------


def _require_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


_AwareUtcTimestamp = Annotated[datetime, AfterValidator(_require_utc_aware)]
_ShortToken = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]

# Fields on HorizonOutcome that must be None whenever available=False. A
# module-level constant (not a class attribute) so it is a plain tuple, not
# subject to Pydantic's private-attribute handling for underscore-prefixed
# class attributes.
_HORIZON_OUTCOME_DERIVED_FIELDS = (
    "horizon_timestamp",
    "price_at_horizon",
    "touched_vwap",
    "bars_to_touch",
    "minutes_to_touch",
    "pct_extension_retraced",
    "signed_return_toward_vwap_bps",
    "max_favorable_excursion_bps",
    "max_adverse_excursion_bps",
)


# --- Fixed evaluation enums --------------------------------------------------------


class ForwardHorizon(StrEnum):
    """The four fixed forward horizons this evaluation scores. Deliberately
    the same four bounded buckets the regime engine itself may report as a
    ``ScenarioHorizon`` (minus ``indeterminate``, which is not a horizon to
    score against -- see ``EligibilityStatus`` / ``HorizonOutcome`` for how
    "no signal" and "not yet available" are represented instead)."""

    INTRADAY_30M = "intraday_30m"
    INTRADAY_2H = "intraday_2h"
    TO_SESSION_CLOSE = "to_session_close"
    NEXT_SESSION = "next_session"


class ExtensionSide(StrEnum):
    """Which side of the frozen signal-time VWAP the signal-time close sat
    on. Named by price's position relative to VWAP, not by any trade
    direction or recommendation -- this evaluation makes none. Every
    descriptive summary is reported separately per side (see
    ``HorizonSideSummary``)."""

    ABOVE_VWAP = "above_vwap"
    BELOW_VWAP = "below_vwap"


class EligibilityStatus(StrEnum):
    """Why a candidate decision point either was or was not scored.

    ``ELIGIBLE`` is the only status with a defined ``extension_side`` and any
    ``available`` horizon outcome. The other two are real, recorded
    exclusions -- never silently dropped -- counted in
    ``SpyVwapReversionEvaluationRecord.no_signal_vwap_unavailable_count`` /
    ``no_signal_zero_extension_count``.
    """

    ELIGIBLE = "eligible"
    VWAP_UNAVAILABLE = "vwap_unavailable"
    ZERO_EXTENSION = "zero_extension"


class SampleStatus(StrEnum):
    """Whether a ``DescriptiveStats`` cleared its minimum sample-size
    threshold. ``INSUFFICIENT_SAMPLE`` never carries a computed value --
    see ``DescriptiveStats``."""

    OK = "ok"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


# --- Input contracts ----------------------------------------------------------------


class SessionBars(BaseModel):
    """One regular session's chronological SPY 5-minute bars, plus the same
    upstream-validated context the regime engine itself consumes.

    Reuses ``IntradayBar`` / ``PriorDayLevels`` / ``CatalystState`` /
    ``BreadthState`` from the regime engine's own contracts -- this module
    defines no parallel bar shape. Construction re-validates ``bars`` by
    building a ``RegimeEngineInput`` from the *full* session (ordering,
    5-minute grid alignment, single ``session_date``, regular-session
    window, OHLC consistency) -- every one of those properties is
    prefix-closed, so this one check also guarantees every shorter prefix
    ``bars[: k + 1]`` the evaluator builds is independently valid, without
    duplicating the regime engine's own validators here.
    """

    model_config = ConfigDict(extra="forbid")

    session_date: date
    bars: Annotated[list[IntradayBar], Field(min_length=1, max_length=MAX_BARS_PER_SESSION)]
    prior_day: PriorDayLevels | None = None
    same_time_historical_volume_baseline: Annotated[Decimal, Field(gt=0)] | None = None
    catalyst_state: CatalystState = CatalystState.UNKNOWN
    breadth_state: BreadthState = BreadthState.UNAVAILABLE

    @model_validator(mode="after")
    def _check_bars_form_a_valid_regime_engine_input(self) -> SessionBars:
        try:
            RegimeEngineInput(
                session_date=self.session_date,
                bars=self.bars,
                prior_day=self.prior_day,
                same_time_historical_volume_baseline=self.same_time_historical_volume_baseline,
                catalyst_state=self.catalyst_state,
                breadth_state=self.breadth_state,
            )
        except ValidationError:
            raise ValueError(
                "session bars are not a valid regular-session, 5-minute-grid-"
                "aligned, strictly ascending sequence for session_date"
            ) from None
        return self


class SpyVwapReversionEvaluationInput(BaseModel):
    """One validated request to evaluate the VWAP-extension/reversion
    hypothesis over one or more chronologically ordered SPY sessions.

    ``sessions`` must be strictly ascending by ``session_date`` with no
    duplicates. There is no trading-holiday calendar here (matching
    ``spy_regime_contracts.py`` / ``session_quality.py``) -- "the next
    session" in the evaluator means the next entry in this list, which is
    only the literal next trading session if the caller supplied
    contiguous sessions; a caller-introduced gap is not detected.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Literal["SPY"] = SUPPORTED_SYMBOL
    sessions: Annotated[list[SessionBars], Field(min_length=1, max_length=MAX_SESSIONS)]

    @model_validator(mode="after")
    def _check_sessions_are_strictly_ascending(self) -> SpyVwapReversionEvaluationInput:
        previous_date = None
        for session in self.sessions:
            if previous_date is not None and session.session_date <= previous_date:
                raise ValueError(
                    "sessions must be strictly ascending by session_date with no duplicates"
                )
            previous_date = session.session_date
        return self


# --- Output contracts: per-decision-point ---------------------------------------


class HorizonOutcome(BaseModel):
    """One forward-outcome measurement at one fixed horizon, scored against
    the frozen signal-time VWAP. See ``spy_vwap_reversion_evaluator.py`` for
    the exact window and formulas.

    ``available=False`` means the full horizon did not exist in the supplied
    data (not enough future bars, a gap, or the next session missing/
    incomplete) -- every other field is then ``None``, enforced below. This
    horizon is never shortened to fit the available data and never guessed.
    """

    model_config = ConfigDict(extra="forbid")

    horizon: ForwardHorizon
    available: bool
    horizon_timestamp: _AwareUtcTimestamp | None = None
    price_at_horizon: Decimal | None = None
    touched_vwap: bool | None = None
    bars_to_touch: Annotated[int, Field(ge=1)] | None = None
    minutes_to_touch: Annotated[int, Field(ge=0)] | None = None
    pct_extension_retraced: Decimal | None = None
    signed_return_toward_vwap_bps: Decimal | None = None
    max_favorable_excursion_bps: Decimal | None = None
    max_adverse_excursion_bps: Decimal | None = None

    @model_validator(mode="after")
    def _check_unavailable_is_fully_none(self) -> HorizonOutcome:
        if not self.available:
            if any(
                getattr(self, name) is not None
                for name in _HORIZON_OUTCOME_DERIVED_FIELDS
            ):
                raise ValueError(
                    "an unavailable horizon outcome must have every derived "
                    "field set to None"
                )
        else:
            if self.horizon_timestamp is None or self.price_at_horizon is None:
                raise ValueError(
                    "an available horizon outcome must carry horizon_timestamp "
                    "and price_at_horizon"
                )
            if self.touched_vwap is None:
                raise ValueError("an available horizon outcome must record touched_vwap")
        return self

    @model_validator(mode="after")
    def _check_touch_fields_consistent(self) -> HorizonOutcome:
        if self.touched_vwap:
            if self.bars_to_touch is None or self.minutes_to_touch is None:
                raise ValueError(
                    "touched_vwap=True requires bars_to_touch and minutes_to_touch"
                )
        else:
            if self.bars_to_touch is not None or self.minutes_to_touch is not None:
                raise ValueError(
                    "bars_to_touch/minutes_to_touch require touched_vwap=True"
                )
        return self


class DecisionPointRecord(BaseModel):
    """One candidate completed bar, with the regime engine's own outputs and
    (when eligible) every forward outcome. Recorded for **every** candidate
    bar -- including a non-eligible one and an indeterminate-regime one --
    never silently discarded.
    """

    model_config = ConfigDict(extra="forbid")

    session_date: date
    signal_as_of: _AwareUtcTimestamp
    bar_index_in_session: Annotated[int, Field(ge=0)]
    eligibility: EligibilityStatus
    session_bars_complete: bool
    regime: Regime
    scenario_horizon: ScenarioHorizon
    time_of_day_bucket: TimeOfDayBucket
    extension_side: ExtensionSide | None = None
    close_to_vwap_distance_bps: Decimal | None = None
    normalized_vwap_extension: Decimal | None = None
    signal_vwap: Decimal | None = None
    signal_close: Decimal | None = None
    outcomes: Annotated[list[HorizonOutcome], Field(min_length=4, max_length=4)]

    @model_validator(mode="after")
    def _check_outcomes_cover_every_horizon_exactly_once(self) -> DecisionPointRecord:
        horizons = [outcome.horizon for outcome in self.outcomes]
        if sorted(horizons, key=lambda h: h.value) != sorted(
            ForwardHorizon, key=lambda h: h.value
        ):
            raise ValueError("outcomes must include exactly one entry per ForwardHorizon")
        return self

    @model_validator(mode="after")
    def _check_eligibility_consistency(self) -> DecisionPointRecord:
        if self.eligibility != EligibilityStatus.ELIGIBLE:
            if self.extension_side is not None:
                raise ValueError("a non-eligible decision point must have no extension_side")
            if any(outcome.available for outcome in self.outcomes):
                raise ValueError(
                    "a non-eligible decision point must have no available horizon outcome"
                )
        return self


# --- Output contracts: aggregate summaries ---------------------------------------


class DescriptiveStats(BaseModel):
    """A sample-size-gated descriptive summary of one numeric series.

    ``status`` is ``insufficient_sample`` whenever ``sample_size`` is below
    the threshold the evaluation run used (see
    ``SampleThresholdsSnapshot``) -- in that case every computed value is
    ``None``, never a value computed from too few points and never a
    fabricated placeholder. When ``status`` is ``ok``, every computed value
    is present.
    """

    model_config = ConfigDict(extra="forbid")

    status: SampleStatus
    sample_size: Annotated[int, Field(ge=0)]
    mean: Decimal | None = None
    median: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    @model_validator(mode="after")
    def _check_value_presence_matches_status(self) -> DescriptiveStats:
        values = (self.mean, self.median, self.minimum, self.maximum)
        if self.status == SampleStatus.INSUFFICIENT_SAMPLE:
            if any(v is not None for v in values):
                raise ValueError("insufficient_sample must not carry any computed value")
        else:
            if any(v is None for v in values):
                raise ValueError("ok status requires every computed value")
        return self


class HorizonDescriptiveBundle(BaseModel):
    """Descriptive stats for one (horizon, extension side, aggregation
    level) combination.

    ``eligible_count`` / ``available_count`` / ``missing_count`` are counted
    in the unit of this bundle's own level: decision-point observations for
    an ``observation_level`` bundle, unique sessions for a ``session_level``
    bundle (a session counts as "available" there once it contributes at
    least one available observation). See
    ``spy_vwap_reversion_evaluator.py`` for exactly how each statistic is
    built at each level -- ``observation_level`` pools every eligible
    decision point's outcome directly (explicitly *not* independent
    observations -- they overlap every five minutes); ``session_level``
    first reduces each session to one number before summarizing across
    sessions, so a long session can never dominate the cross-session
    statistic.
    """

    model_config = ConfigDict(extra="forbid")

    eligible_count: Annotated[int, Field(ge=0)]
    available_count: Annotated[int, Field(ge=0)]
    missing_count: Annotated[int, Field(ge=0)]
    touch_rate: DescriptiveStats
    pct_extension_retraced: DescriptiveStats
    signed_return_toward_vwap_bps: DescriptiveStats
    max_favorable_excursion_bps: DescriptiveStats
    max_adverse_excursion_bps: DescriptiveStats

    @model_validator(mode="after")
    def _check_counts_are_consistent(self) -> HorizonDescriptiveBundle:
        if self.available_count + self.missing_count != self.eligible_count:
            raise ValueError("available_count + missing_count must equal eligible_count")
        return self


class HorizonSideSummary(BaseModel):
    """Both aggregation levels for one extension side at one horizon."""

    model_config = ConfigDict(extra="forbid")

    extension_side: ExtensionSide
    observation_level: HorizonDescriptiveBundle
    session_level: HorizonDescriptiveBundle


class HorizonSummary(BaseModel):
    """Both extension sides for one fixed forward horizon."""

    model_config = ConfigDict(extra="forbid")

    horizon: ForwardHorizon
    above_vwap: HorizonSideSummary
    below_vwap: HorizonSideSummary

    @model_validator(mode="after")
    def _check_side_labels_match(self) -> HorizonSummary:
        if self.above_vwap.extension_side != ExtensionSide.ABOVE_VWAP:
            raise ValueError("above_vwap bundle must carry extension_side ABOVE_VWAP")
        if self.below_vwap.extension_side != ExtensionSide.BELOW_VWAP:
            raise ValueError("below_vwap bundle must carry extension_side BELOW_VWAP")
        return self


class RegimeThresholdsSnapshot(BaseModel):
    """A record (never a re-derivation) of the exact, unmodified regime
    classifier thresholds a given evaluation run used -- see
    ``spy_regime_classifier.RegimeThresholds``. This evaluation freezes the
    hypothesis: it never tunes, optimizes, or grid-searches these values,
    and this snapshot exists purely for audit-trail purposes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    extension_threshold_for_reversion: Decimal
    trend_strength_min_for_continuation: Decimal
    trend_strength_max_for_reversion: Decimal
    vwap_slope_min_bps_for_continuation: Decimal
    vwap_slope_max_bps_for_reversion: Decimal
    relative_volume_elevated_min: Decimal
    range_trend_strength_max: Decimal
    range_extension_max: Decimal
    min_corroborators_for_reversion: int
    intraday_30m_min_minutes_remaining: int
    intraday_2h_min_minutes_remaining: int


class SampleThresholdsSnapshot(BaseModel):
    """The minimum sample sizes a given evaluation run used to gate
    ``DescriptiveStats`` (see ``SampleStatus.INSUFFICIENT_SAMPLE``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_observations_for_summary: Annotated[int, Field(gt=0)]
    min_sessions_for_summary: Annotated[int, Field(gt=0)]


class SpyVwapReversionEvaluationRecord(BaseModel):
    """The complete, deterministic, sanitized output of one offline
    VWAP-extension/reversion evaluation run.

    Contains **no** raw provider payload, credential, URL, response ID,
    free metadata, option/contract data, or database path -- there is
    simply no field for any of those. It contains no P&L, options return,
    win rate, profitability figure, or trade recommendation of any kind --
    see the module docstring and ``DECISION_RULES.md``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    symbol: Literal["SPY"]
    generated_at: _AwareUtcTimestamp

    regime_thresholds: RegimeThresholdsSnapshot
    sample_thresholds: SampleThresholdsSnapshot

    unique_session_count: Annotated[int, Field(ge=0)]
    candidate_decision_point_count: Annotated[int, Field(ge=0)]
    eligible_observation_count: Annotated[int, Field(ge=0)]
    no_signal_vwap_unavailable_count: Annotated[int, Field(ge=0)]
    no_signal_zero_extension_count: Annotated[int, Field(ge=0)]

    regime_counts_all_candidates: dict[Regime, Annotated[int, Field(ge=0)]]
    regime_counts_eligible_only: dict[Regime, Annotated[int, Field(ge=0)]]
    extension_side_counts: dict[ExtensionSide, Annotated[int, Field(ge=0)]]
    missing_horizon_counts: dict[ForwardHorizon, Annotated[int, Field(ge=0)]]

    horizon_summaries: Annotated[list[HorizonSummary], Field(min_length=4, max_length=4)]

    decision_points: Annotated[
        list[DecisionPointRecord], Field(max_length=MAX_DECISION_POINTS)
    ]

    notes: Annotated[list[_ShortToken], Field(min_length=1, max_length=20)]

    @model_validator(mode="after")
    def _check_enum_count_dicts_are_complete(self) -> SpyVwapReversionEvaluationRecord:
        if set(self.regime_counts_all_candidates) != set(Regime):
            raise ValueError("regime_counts_all_candidates must have exactly one entry per Regime")
        if set(self.regime_counts_eligible_only) != set(Regime):
            raise ValueError("regime_counts_eligible_only must have exactly one entry per Regime")
        if set(self.extension_side_counts) != set(ExtensionSide):
            raise ValueError("extension_side_counts must have exactly one entry per ExtensionSide")
        if set(self.missing_horizon_counts) != set(ForwardHorizon):
            raise ValueError(
                "missing_horizon_counts must have exactly one entry per ForwardHorizon"
            )
        return self

    @model_validator(mode="after")
    def _check_decision_point_count_matches(self) -> SpyVwapReversionEvaluationRecord:
        if len(self.decision_points) != self.candidate_decision_point_count:
            raise ValueError(
                "decision_points must contain exactly candidate_decision_point_count entries"
            )
        return self

    @model_validator(mode="after")
    def _check_top_level_counts_are_consistent(self) -> SpyVwapReversionEvaluationRecord:
        expected_eligible = (
            self.candidate_decision_point_count
            - self.no_signal_vwap_unavailable_count
            - self.no_signal_zero_extension_count
        )
        if expected_eligible != self.eligible_observation_count:
            raise ValueError(
                "eligible_observation_count must equal candidate_decision_point_count "
                "minus both no-signal counts"
            )
        if sum(self.regime_counts_all_candidates.values()) != self.candidate_decision_point_count:
            raise ValueError(
                "regime_counts_all_candidates must sum to candidate_decision_point_count"
            )
        if sum(self.regime_counts_eligible_only.values()) != self.eligible_observation_count:
            raise ValueError(
                "regime_counts_eligible_only must sum to eligible_observation_count"
            )
        if sum(self.extension_side_counts.values()) != self.eligible_observation_count:
            raise ValueError("extension_side_counts must sum to eligible_observation_count")
        return self
