"""Strict Pydantic v2 contracts for the preregistered SPY VWAP-reversion
confirmation analysis (``docs/SPY_VWAP_REVERSION_PREREGISTRATION.md``,
including dated clarification C1).

This result is a **separate** record that sits on top of an existing,
validated ``SpyVwapReversionEvaluationRecord``. It never modifies that record,
its schema version, the regime thresholds, or the evaluator's sample gate --
see ``spy_vwap_reversion_confirmation.py`` for the pure analysis that builds
it.

**Underlying setup only.** Nothing here is an option contract, an options
return, a P&L figure, a recommendation, or a trading action, and there is no
field for any of those, for a Strategy Agent, for model reasoning, for raw
bars, for paths/URLs/credentials, or for free-form metadata. No label defined
here means validated, profitable, accurate, or ready to trade.

**Exact arithmetic.** Every inferential quantity (estimates, interval bounds,
p-values, Holm-adjusted p-values, paired contrasts) is computed and compared
as an exact rational number -- nothing is rounded before a decision (C1.2).
Each is serialized as an ``ExactRational``: the reduced numerator/denominator,
which reproduces every decision exactly, plus a 30-decimal-place display
value that the contract checks against the fraction. Display values are
never the inferential value.

**Self-checking.** The decision rules are small pure functions defined in
this module (``raw_p_value``, ``holm_adjust``, ``primary_horizon_status``,
``derive_study_label``, ``below_horizon_status``, ``contrast_status``,
``derive_secondary_label``, ``extension_bucket``). The analysis uses them to
decide, and the model validators below use the same functions to re-derive
every status and label from the serialized values, so a malformed or
internally inconsistent result cannot be constructed or deserialized.

**What validation does not establish.** A coherent hand edit -- one that
changes an estimate, its interval, p-values, statuses, and label
consistently -- can still pass this contract; the result is not
tamper-proof. The provenance hashes identify the canonical input and
evaluation-record bytes the result claims; the code commit is
operator-supplied and not independently attested. Statistical correctness
is checked by recomputation (``spy_vwap_reversion_confirmation.
verify_confirmation_result``); authenticity would require trusted custody or
an external signature, which is out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from math import gcd
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    ExtensionSide,
    ForwardHorizon,
    RegimeThresholdsSnapshot,
    SampleStatus,
    SampleThresholdsSnapshot,
)
from market_intelligence.market_features.spy_regime_classifier import DEFAULT_THRESHOLDS
from market_intelligence.market_features.spy_regime_contracts import Regime, TimeOfDayBucket

CONFIRMATION_SCHEMA_VERSION = "spy-vwap-reversion-confirmation-1"

# --- Frozen provenance constants (C1.5) -------------------------------------------
#
# Neither value is ever supplied at run time, inferred, or invented.
BASE_PREREGISTRATION_COMMIT_SHA = "f77d30f8e6e90a6b77eeca11fd11c3da9c9540c1"
CLARIFICATION_COMMIT_SHA = "1ff654de6dbbd54aeecb471010d1a612ca5abda6"

# --- Frozen statistical constants (preregistration §5 and C1.2) -------------------

BOOTSTRAP_SEED = 20260923
BOOTSTRAP_REPLICATES = 10_000
INTERVAL_LOWER_INDEX = 249  # zero-based; the 250th order statistic
INTERVAL_UPPER_INDEX = 9749  # zero-based; the 9,750th order statistic
FAMILY_WISE_ALPHA = Fraction(1, 20)
SECONDARY_ALPHA = Fraction(1, 20)
EFFECT_FLOOR_BPS = Fraction(1)
PCT_RETRACED_FLOOR_DOLLARS = Decimal("0.10")
EXTENSION_RANGE_BAND_MAX = Decimal("1.0")
EXTENSION_REVERSION_THRESHOLD = Decimal("1.5")
SESSION_BAR_COUNT = 78
QUANTILE_PERCENTS = (10, 25, 50, 75, 90)
SUBGROUP_MIN_SESSIONS = 40
SUBGROUP_MIN_OBSERVATIONS = 50
PRIMARY_HORIZONS = (
    ForwardHorizon.INTRADAY_30M,
    ForwardHorizon.INTRADAY_2H,
    ForwardHorizon.TO_SESSION_CLOSE,
)
DISPLAY_DECIMAL_PLACES = 30

# Structural bound on the digits of a serialized numerator/denominator. The
# largest legitimate denominator is on the order of N * lcm(1..78) * 10^4
# (about 45 digits); this ceiling is deliberately generous but finite.
_MAX_INTEGER_DIGITS = 400

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_COMMIT_SHA_PATTERN = r"^[0-9a-f]{40}$"


# --- Fixed enums ------------------------------------------------------------------


class StudySample(StrEnum):
    """Which preregistered sample a result describes."""

    CONFIRMATION = "confirmation"
    HOLDOUT = "holdout"


class HorizonStatus(StrEnum):
    """Per-horizon status (§8). ``INSUFFICIENT_SAMPLE`` is used when the
    horizon's cell gate fails or, for primary horizons, when the Holm family
    of three is incomplete."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


class StudyLabel(StrEnum):
    """Primary study label (§8). None of these means validated, profitable,
    accurate, or ready to trade."""

    INSUFFICIENT_SAMPLE = "insufficient_sample"
    SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH = "supported_for_further_shadow_research"
    NOT_SUPPORTED = "not_supported"
    MIXED = "mixed"


class ContrastStatus(StrEnum):
    """Status of the below-VWAP close-minus-30m paired contrast (§8, C1.3)."""

    MATERIALLY_DIFFERENT = "materially_different"
    INCONCLUSIVE = "inconclusive"
    INSUFFICIENT_SAMPLE = "insufficient_sample"


class SecondaryLabel(StrEnum):
    """Secondary (below-VWAP) label (§8, C1.3)."""

    INSUFFICIENT_SAMPLE = "insufficient_sample"
    BELOW_HORIZON_DEPENDENT = "below_horizon_dependent"
    BELOW_CONSISTENT_REVERSION = "below_consistent_reversion"
    BELOW_NO_REVERSION = "below_no_reversion"


class ExtensionBucket(StrEnum):
    """Fixed normalized-extension buckets (§6), bounded by the frozen
    classifier constants ``range_extension_max`` (1.0) and
    ``extension_threshold_for_reversion`` (1.5)."""

    UNAVAILABLE = "unavailable"
    WITHIN_RANGE_BAND = "within_range_band"
    INTERMEDIATE = "intermediate"
    AT_OR_ABOVE_REVERSION_THRESHOLD = "at_or_above_reversion_threshold"


class SubgroupDimension(StrEnum):
    """The only three one-way subgroup dimensions (§7). No cross-tabulation."""

    REGIME = "regime"
    TIME_OF_DAY = "time_of_day"
    EXTENSION_BUCKET = "extension_bucket"


SUBGROUP_GROUPS: MappingProxyType[SubgroupDimension, tuple[str, ...]] = MappingProxyType(
    {
        SubgroupDimension.REGIME: tuple(r.value for r in Regime),
        SubgroupDimension.TIME_OF_DAY: tuple(t.value for t in TimeOfDayBucket),
        SubgroupDimension.EXTENSION_BUCKET: tuple(b.value for b in ExtensionBucket),
    }
)


# --- Fixed sample designs (§4, C1.1) ------------------------------------------------


@dataclass(frozen=True)
class SampleDesign:
    """One sample's fixed window and gates. Only the gates differ between
    the confirmation and holdout samples (C1.1)."""

    window_start: date
    window_end: date
    min_complete_sessions: int
    min_primary_cell_sessions: int
    min_paired_sessions: int
    min_secondary_cell_sessions: int


SAMPLE_DESIGNS: MappingProxyType[StudySample, SampleDesign] = MappingProxyType(
    {
        StudySample.CONFIRMATION: SampleDesign(
            window_start=date(2026, 2, 23),
            window_end=date(2026, 8, 14),
            min_complete_sessions=100,
            min_primary_cell_sessions=80,
            min_paired_sessions=80,
            min_secondary_cell_sessions=80,
        ),
        StudySample.HOLDOUT: SampleDesign(
            window_start=date(2026, 9, 23),
            window_end=date(2026, 12, 4),
            min_complete_sessions=40,
            min_primary_cell_sessions=40,
            min_paired_sessions=40,
            min_secondary_cell_sessions=40,
        ),
    }
)

FIXED_NOTES = (
    "underlying_setup_only_no_options_or_pnl",
    "research_result_not_validation",
    "not_a_recommendation_or_trading_action",
    "labels_never_mean_validated_profitable_accurate_or_tradeable",
    "observation_level_points_overlap_no_inference",
    "thresholds_frozen_unmodified",
    "next_session_unavailable_no_exchange_calendar",
    "options_strategy_agent_not_authorized",
)


# --- Exact rationals ------------------------------------------------------------------


def display_decimal(value: Fraction) -> Decimal:
    """``value`` rounded half-even to exactly ``DISPLAY_DECIMAL_PLACES``
    decimal places, using integer arithmetic only (no Decimal context
    rounding). Display only -- never used for a decision."""
    scale = 10**DISPLAY_DECIMAL_PLACES
    quotient, remainder = divmod(value.numerator * scale, value.denominator)
    twice = 2 * remainder
    if twice > value.denominator or (twice == value.denominator and quotient % 2 == 1):
        quotient += 1
    return Decimal(f"{quotient}E-{DISPLAY_DECIMAL_PLACES}")


_StrictInt = Annotated[int, Field(strict=True)]


class ExactRational(BaseModel):
    """An exact rational number: reduced ``numerator``/``denominator`` (the
    value every decision uses) plus a 30-decimal-place display value checked
    against it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    numerator: _StrictInt
    denominator: Annotated[int, Field(strict=True, gt=0)]
    decimal_30dp: Decimal

    @model_validator(mode="after")
    def _check_exact_and_consistent(self) -> ExactRational:
        if (
            len(str(abs(self.numerator))) > _MAX_INTEGER_DIGITS
            or len(str(self.denominator)) > _MAX_INTEGER_DIGITS
        ):
            raise ValueError("exact rational exceeds the digit bound")
        if gcd(self.numerator, self.denominator) != 1:
            raise ValueError("exact rational must be in lowest terms")
        if not self.decimal_30dp.is_finite():
            raise ValueError("display decimal must be finite")
        if self.decimal_30dp != display_decimal(self.as_fraction()):
            raise ValueError("display decimal does not match the exact value")
        return self

    @classmethod
    def from_fraction(cls, value: Fraction) -> ExactRational:
        return cls(
            numerator=value.numerator,
            denominator=value.denominator,
            decimal_30dp=display_decimal(value),
        )

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)


# --- Pure decision rules (shared by the analysis and the validators) -------------


def raw_p_value(
    at_or_below_zero: int,
    at_or_above_zero: int,
    replicates: int = BOOTSTRAP_REPLICATES,
) -> Fraction:
    """§5 finite-sample, two-sided, sign-based bootstrap p-value, exactly."""
    return min(
        Fraction(1),
        2
        * min(
            Fraction(at_or_below_zero + 1, replicates + 1),
            Fraction(at_or_above_zero + 1, replicates + 1),
        ),
    )


def holm_adjust(p_values: tuple[Fraction, ...] | list[Fraction]) -> tuple[Fraction, ...]:
    """Holm step-down adjustment, exact. ``p_values`` is given in the fixed
    horizon order; ties sort stably by that order. Adjusted values are made
    monotone by a running maximum and capped at 1. Tied raw p-values receive
    identical adjusted values under either tie order."""
    m = len(p_values)
    order = sorted(range(m), key=lambda i: (p_values[i], i))
    adjusted: list[Fraction] = [Fraction(0)] * m
    running = Fraction(0)
    for rank, index in enumerate(order):
        running = max(running, min(Fraction(1), (m - rank) * p_values[index]))
        adjusted[index] = running
    return tuple(adjusted)


def primary_horizon_status(
    estimate: Fraction, lower: Fraction, upper: Fraction, p_holm: Fraction
) -> HorizonStatus:
    """§8 primary status: effect size, interval, and Holm-adjusted p must
    all pass. Exact comparisons only."""
    if estimate >= EFFECT_FLOOR_BPS and lower > 0 and p_holm < FAMILY_WISE_ALPHA:
        return HorizonStatus.POSITIVE
    if estimate <= -EFFECT_FLOOR_BPS and upper < 0 and p_holm < FAMILY_WISE_ALPHA:
        return HorizonStatus.NEGATIVE
    return HorizonStatus.INCONCLUSIVE


def derive_study_label(
    overall_gate_passed: bool, statuses: tuple[HorizonStatus, ...] | list[HorizonStatus]
) -> StudyLabel:
    """§8 first-match primary study label."""
    if not overall_gate_passed or HorizonStatus.INSUFFICIENT_SAMPLE in statuses:
        return StudyLabel.INSUFFICIENT_SAMPLE
    if all(status == HorizonStatus.POSITIVE for status in statuses):
        return StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH
    if not any(status == HorizonStatus.POSITIVE for status in statuses):
        return StudyLabel.NOT_SUPPORTED
    return StudyLabel.MIXED


def below_horizon_status(lower: Fraction, upper: Fraction) -> HorizonStatus:
    """§8 below-VWAP per-horizon status: unadjusted 95% interval only."""
    if lower > 0:
        return HorizonStatus.POSITIVE
    if upper < 0:
        return HorizonStatus.NEGATIVE
    return HorizonStatus.INCONCLUSIVE


def contrast_status(
    estimate: Fraction, lower: Fraction, upper: Fraction, p_raw: Fraction
) -> ContrastStatus:
    """§8 / C1.3 paired contrast: all three conditions must hold."""
    excludes_zero = lower > 0 or upper < 0
    if abs(estimate) >= EFFECT_FLOOR_BPS and excludes_zero and p_raw < SECONDARY_ALPHA:
        return ContrastStatus.MATERIALLY_DIFFERENT
    return ContrastStatus.INCONCLUSIVE


def derive_secondary_label(
    overall_gate_passed: bool,
    below_statuses: tuple[HorizonStatus, ...] | list[HorizonStatus],
    paired_contrast: ContrastStatus,
) -> SecondaryLabel:
    """§8 first-match secondary label, as clarified by C1.3: an
    ``insufficient_sample`` paired contrast can never trigger
    ``below_horizon_dependent``; rule 2 then depends only on whether the
    three below-VWAP statuses differ."""
    if not overall_gate_passed or HorizonStatus.INSUFFICIENT_SAMPLE in below_statuses:
        return SecondaryLabel.INSUFFICIENT_SAMPLE
    if len(set(below_statuses)) > 1 or paired_contrast == ContrastStatus.MATERIALLY_DIFFERENT:
        return SecondaryLabel.BELOW_HORIZON_DEPENDENT
    if all(status == HorizonStatus.POSITIVE for status in below_statuses):
        return SecondaryLabel.BELOW_CONSISTENT_REVERSION
    return SecondaryLabel.BELOW_NO_REVERSION


def extension_bucket(normalized_vwap_extension: Decimal | None) -> ExtensionBucket:
    """§6 fixed bucket for one decision point, on the absolute value."""
    if normalized_vwap_extension is None:
        return ExtensionBucket.UNAVAILABLE
    # Exact: Decimal abs()/arithmetic would round to the context precision.
    magnitude = abs(Fraction(normalized_vwap_extension))
    if magnitude <= Fraction(EXTENSION_RANGE_BAND_MAX):
        return ExtensionBucket.WITHIN_RANGE_BAND
    if magnitude < Fraction(EXTENSION_REVERSION_THRESHOLD):
        return ExtensionBucket.INTERMEDIATE
    return ExtensionBucket.AT_OR_ABOVE_REVERSION_THRESHOLD


def nearest_rank_index(percent: int, count: int) -> int:
    """Zero-based nearest-rank index: ``ceil(percent / 100 * count) - 1``."""
    return (percent * count + 99) // 100 - 1


# --- Inference building blocks ---------------------------------------------------------


def _require_utc_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


_Count = Annotated[int, Field(strict=True, ge=0)]
_ReplicateCount = Annotated[int, Field(strict=True, ge=0, le=BOOTSTRAP_REPLICATES)]


class BootstrapInference(BaseModel):
    """One cell's session-blocked bootstrap summary (§5). ``p_raw`` is
    present only where the preregistration defines a p-value (primary
    horizons and the paired contrast); container models enforce which."""

    model_config = ConfigDict(extra="forbid")

    estimate: ExactRational
    interval_lower: ExactRational
    interval_upper: ExactRational
    replicates_at_or_below_zero: _ReplicateCount
    replicates_at_or_above_zero: _ReplicateCount
    p_raw: ExactRational | None = None

    @model_validator(mode="after")
    def _check_consistency(self) -> BootstrapInference:
        lower = self.interval_lower.as_fraction()
        upper = self.interval_upper.as_fraction()
        below = self.replicates_at_or_below_zero
        above = self.replicates_at_or_above_zero
        if lower > upper:
            raise ValueError("interval lower bound exceeds upper bound")
        # Every replicate is <= 0 or >= 0 (both when exactly zero).
        if below + above < BOOTSTRAP_REPLICATES:
            raise ValueError("tail counts do not cover every replicate")
        # sorted[249] > 0 means at most 249 replicates are <= 0, and so on.
        if lower > 0 and below > INTERVAL_LOWER_INDEX:
            raise ValueError("tail counts are inconsistent with the interval")
        if upper < 0 and above > BOOTSTRAP_REPLICATES - 1 - INTERVAL_UPPER_INDEX:
            raise ValueError("tail counts are inconsistent with the interval")
        if self.p_raw is not None and self.p_raw.as_fraction() != raw_p_value(below, above):
            raise ValueError("p_raw does not match the preregistered formula")
        return self


class GatedCell(BaseModel):
    """A sample-size-gated, session-level cell. ``insufficient_sample``
    carries counts and no statistic; ``ok`` always carries one."""

    model_config = ConfigDict(extra="forbid")

    status: SampleStatus
    required_sessions: Annotated[int, Field(strict=True, gt=0)]
    required_observations: _Count
    session_count: _Count
    observation_count: _Count
    inference: BootstrapInference | None = None

    @model_validator(mode="after")
    def _check_gate(self) -> GatedCell:
        if self.observation_count < self.session_count:
            raise ValueError("each contributing session has at least one observation")
        passes = (
            self.session_count >= self.required_sessions
            and self.observation_count >= self.required_observations
        )
        expected = SampleStatus.OK if passes else SampleStatus.INSUFFICIENT_SAMPLE
        if self.status != expected:
            raise ValueError("cell status does not match its gate")
        if (self.status == SampleStatus.OK) != (self.inference is not None):
            raise ValueError("ok cells carry inference; insufficient cells carry none")
        return self


class PairedCell(BaseModel):
    """A paired, within-session contrast cell gated on paired sessions."""

    model_config = ConfigDict(extra="forbid")

    status: SampleStatus
    required_sessions: Annotated[int, Field(strict=True, gt=0)]
    paired_session_count: _Count
    inference: BootstrapInference | None = None

    @model_validator(mode="after")
    def _check_gate(self) -> PairedCell:
        passes = self.paired_session_count >= self.required_sessions
        expected = SampleStatus.OK if passes else SampleStatus.INSUFFICIENT_SAMPLE
        if self.status != expected:
            raise ValueError("paired cell status does not match its gate")
        if (self.status == SampleStatus.OK) != (self.inference is not None):
            raise ValueError("ok cells carry inference; insufficient cells carry none")
        return self


def _require_p_raw(inference: BootstrapInference | None, required: bool) -> None:
    if inference is None:
        return
    if required and inference.p_raw is None:
        raise ValueError("this cell requires a preregistered p-value")
    if not required and inference.p_raw is not None:
        raise ValueError("this cell has no preregistered p-value")


# --- Primary analysis -----------------------------------------------------------------


class PrimaryHorizonResult(BaseModel):
    """One primary above-VWAP horizon (§2, §8)."""

    model_config = ConfigDict(extra="forbid")

    horizon: ForwardHorizon
    cell: GatedCell
    p_holm: ExactRational | None = None
    status: HorizonStatus

    @model_validator(mode="after")
    def _check_status(self) -> PrimaryHorizonResult:
        if self.horizon not in PRIMARY_HORIZONS:
            raise ValueError("not a primary horizon")
        _require_p_raw(self.cell.inference, required=True)
        if self.p_holm is None:
            if self.status != HorizonStatus.INSUFFICIENT_SAMPLE:
                raise ValueError("a status requires a Holm-adjusted p-value")
            return self
        inference = self.cell.inference
        if inference is None:
            raise ValueError("a Holm-adjusted p-value requires cell inference")
        expected = primary_horizon_status(
            inference.estimate.as_fraction(),
            inference.interval_lower.as_fraction(),
            inference.interval_upper.as_fraction(),
            self.p_holm.as_fraction(),
        )
        if self.status != expected:
            raise ValueError("primary status does not match the decision rule")
        return self


class OverallGate(BaseModel):
    """The sample's complete-session gate (§4, C1.1)."""

    model_config = ConfigDict(extra="forbid")

    required_complete_sessions: Annotated[int, Field(strict=True, gt=0)]
    complete_session_count: _Count
    status: SampleStatus

    @model_validator(mode="after")
    def _check_gate(self) -> OverallGate:
        passes = self.complete_session_count >= self.required_complete_sessions
        expected = SampleStatus.OK if passes else SampleStatus.INSUFFICIENT_SAMPLE
        if self.status != expected:
            raise ValueError("overall gate status does not match its threshold")
        return self


class PrimaryAnalysis(BaseModel):
    """The three primary horizons, Holm family, and study label."""

    model_config = ConfigDict(extra="forbid")

    horizons: Annotated[list[PrimaryHorizonResult], Field(min_length=3, max_length=3)]
    study_label: StudyLabel

    @model_validator(mode="after")
    def _check_family(self) -> PrimaryAnalysis:
        if tuple(result.horizon for result in self.horizons) != PRIMARY_HORIZONS:
            raise ValueError("primary horizons must be in the fixed order")
        family_complete = all(result.cell.status == SampleStatus.OK for result in self.horizons)
        if family_complete:
            raw = [result.cell.inference.p_raw.as_fraction() for result in self.horizons]  # type: ignore[union-attr]
            expected = holm_adjust(raw)
            for result, adjusted in zip(self.horizons, expected, strict=True):
                if result.p_holm is None or result.p_holm.as_fraction() != adjusted:
                    raise ValueError("Holm-adjusted p-values do not match the family")
        elif any(result.p_holm is not None for result in self.horizons):
            raise ValueError("Holm adjustment requires all three primary cells")
        return self


# --- Secondary analysis ---------------------------------------------------------------


class BelowHorizonResult(BaseModel):
    """One below-VWAP horizon's signed return (§8, unadjusted interval)."""

    model_config = ConfigDict(extra="forbid")

    horizon: ForwardHorizon
    cell: GatedCell
    status: HorizonStatus

    @model_validator(mode="after")
    def _check_status(self) -> BelowHorizonResult:
        if self.horizon not in PRIMARY_HORIZONS:
            raise ValueError("not an evaluated horizon")
        _require_p_raw(self.cell.inference, required=False)
        inference = self.cell.inference
        if inference is None:
            expected = HorizonStatus.INSUFFICIENT_SAMPLE
        else:
            expected = below_horizon_status(
                inference.interval_lower.as_fraction(), inference.interval_upper.as_fraction()
            )
        if self.status != expected:
            raise ValueError("below-VWAP status does not match the decision rule")
        return self


class PairedContrastResult(BaseModel):
    """Below-VWAP ``to_session_close`` minus ``intraday_30m`` (§5, C1.3)."""

    model_config = ConfigDict(extra="forbid")

    cell: PairedCell
    status: ContrastStatus

    @model_validator(mode="after")
    def _check_status(self) -> PairedContrastResult:
        _require_p_raw(self.cell.inference, required=True)
        inference = self.cell.inference
        if inference is None:
            expected = ContrastStatus.INSUFFICIENT_SAMPLE
        else:
            expected = contrast_status(
                inference.estimate.as_fraction(),
                inference.interval_lower.as_fraction(),
                inference.interval_upper.as_fraction(),
                inference.p_raw.as_fraction(),  # type: ignore[union-attr]
            )
        if self.status != expected:
            raise ValueError("contrast status does not match the decision rule")
        return self


class SecondaryAnalysis(BaseModel):
    """Below-VWAP horizons, the paired contrast, and the secondary label."""

    model_config = ConfigDict(extra="forbid")

    below_horizons: Annotated[list[BelowHorizonResult], Field(min_length=3, max_length=3)]
    close_minus_30m_contrast: PairedContrastResult
    secondary_label: SecondaryLabel

    @model_validator(mode="after")
    def _check_order(self) -> SecondaryAnalysis:
        if tuple(result.horizon for result in self.below_horizons) != PRIMARY_HORIZONS:
            raise ValueError("below-VWAP horizons must be in the fixed order")
        return self


class AsymmetryResult(BaseModel):
    """Descriptive above-minus-below paired session contrast (§8). Interval
    only: no p-value and no label."""

    model_config = ConfigDict(extra="forbid")

    horizon: ForwardHorizon
    cell: PairedCell

    @model_validator(mode="after")
    def _check(self) -> AsymmetryResult:
        if self.horizon not in PRIMARY_HORIZONS:
            raise ValueError("not an evaluated horizon")
        _require_p_raw(self.cell.inference, required=False)
        return self


class PctFloorCounts(BaseModel):
    """Observations excluded from percentage retraced by the $0.10 floor."""

    model_config = ConfigDict(extra="forbid")

    available_observations: _Count
    below_floor_observations: _Count
    at_or_above_floor_observations: _Count

    @model_validator(mode="after")
    def _check_sum(self) -> PctFloorCounts:
        if (
            self.below_floor_observations + self.at_or_above_floor_observations
            != self.available_observations
        ):
            raise ValueError("floor counts must sum to available observations")
        return self


class SessionQuantiles(BaseModel):
    """Nearest-rank session-level quantiles of the signed-return session
    means (§3). Descriptive only."""

    model_config = ConfigDict(extra="forbid")

    status: SampleStatus
    required_sessions: Annotated[int, Field(strict=True, gt=0)]
    session_count: _Count
    p10: ExactRational | None = None
    p25: ExactRational | None = None
    p50: ExactRational | None = None
    p75: ExactRational | None = None
    p90: ExactRational | None = None

    @model_validator(mode="after")
    def _check(self) -> SessionQuantiles:
        passes = self.session_count >= self.required_sessions
        expected = SampleStatus.OK if passes else SampleStatus.INSUFFICIENT_SAMPLE
        if self.status != expected:
            raise ValueError("quantile status does not match its gate")
        values = (self.p10, self.p25, self.p50, self.p75, self.p90)
        if self.status == SampleStatus.INSUFFICIENT_SAMPLE:
            if any(v is not None for v in values):
                raise ValueError("insufficient_sample carries no quantile")
            return self
        if any(v is None for v in values):
            raise ValueError("ok requires every quantile")
        fractions = [v.as_fraction() for v in values]  # type: ignore[union-attr]
        if fractions != sorted(fractions):
            raise ValueError("quantiles must be non-decreasing")
        return self


class SideHorizonSecondary(BaseModel):
    """Secondary outcomes for one extension side at one horizon (§3)."""

    model_config = ConfigDict(extra="forbid")

    extension_side: ExtensionSide
    horizon: ForwardHorizon
    touch_rate: GatedCell
    max_favorable_excursion_bps: GatedCell
    max_adverse_excursion_bps: GatedCell
    pct_extension_retraced_floored: GatedCell
    pct_floor_counts: PctFloorCounts
    signed_return_session_quantiles: SessionQuantiles

    @model_validator(mode="after")
    def _check(self) -> SideHorizonSecondary:
        if self.horizon not in PRIMARY_HORIZONS:
            raise ValueError("not an evaluated horizon")
        for cell in (
            self.touch_rate,
            self.max_favorable_excursion_bps,
            self.max_adverse_excursion_bps,
            self.pct_extension_retraced_floored,
        ):
            _require_p_raw(cell.inference, required=False)
        if (
            self.pct_extension_retraced_floored.observation_count
            != self.pct_floor_counts.at_or_above_floor_observations
        ):
            raise ValueError("floored cell must use exactly the at-or-above-floor points")
        return self


class SubgroupResult(BaseModel):
    """One exploratory one-way subgroup cell: signed return toward VWAP
    only, unadjusted interval, gated at 40 sessions and 50 observations."""

    model_config = ConfigDict(extra="forbid")

    dimension: SubgroupDimension
    group: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")]
    extension_side: ExtensionSide
    horizon: ForwardHorizon
    cell: GatedCell

    @model_validator(mode="after")
    def _check(self) -> SubgroupResult:
        if self.group not in SUBGROUP_GROUPS[self.dimension]:
            raise ValueError("group is not a member of its dimension")
        if self.horizon not in PRIMARY_HORIZONS:
            raise ValueError("not an evaluated horizon")
        if (
            self.cell.required_sessions != SUBGROUP_MIN_SESSIONS
            or self.cell.required_observations != SUBGROUP_MIN_OBSERVATIONS
        ):
            raise ValueError("subgroup cells use the fixed 40-session / 50-observation gate")
        _require_p_raw(self.cell.inference, required=False)
        return self


def expected_subgroup_keys() -> tuple[
    tuple[SubgroupDimension, str, ExtensionSide, ForwardHorizon], ...
]:
    """The fixed, complete, ordered set of one-way subgroup cells."""
    return tuple(
        (dimension, group, side, horizon)
        for dimension in SubgroupDimension
        for group in SUBGROUP_GROUPS[dimension]
        for side in ExtensionSide
        for horizon in PRIMARY_HORIZONS
    )


def expected_side_horizon_keys() -> tuple[tuple[ExtensionSide, ForwardHorizon], ...]:
    return tuple((side, horizon) for side in ExtensionSide for horizon in PRIMARY_HORIZONS)


# --- Configuration and provenance ------------------------------------------------------


def _default_regime_thresholds_snapshot() -> RegimeThresholdsSnapshot:
    return RegimeThresholdsSnapshot(
        **{
            name: getattr(DEFAULT_THRESHOLDS, name)
            for name in RegimeThresholdsSnapshot.model_fields
        }
    )


DEFAULT_EVALUATION_SAMPLE_THRESHOLDS = SampleThresholdsSnapshot(
    min_observations_for_summary=50, min_sessions_for_summary=20
)


class ConfigurationSnapshot(BaseModel):
    """Every value the preregistration and C1 fix for a run (§9, C1.5).
    Validated to equal ``expected_configuration`` for its sample."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sample: StudySample
    window_start: date
    window_end: date
    min_complete_sessions: int
    min_primary_cell_sessions: int
    min_paired_sessions: int
    min_secondary_cell_sessions: int
    min_subgroup_sessions: int
    min_subgroup_observations: int
    bootstrap_seed: int
    bootstrap_replicates: int
    interval_lower_index: int
    interval_upper_index: int
    family_wise_alpha: Decimal
    secondary_alpha: Decimal
    effect_floor_bps: Decimal
    pct_retraced_floor_dollars: Decimal
    extension_range_band_max: Decimal
    extension_reversion_threshold: Decimal
    session_bar_count: int
    quantile_percents: tuple[int, ...]
    primary_horizons: tuple[ForwardHorizon, ...]
    holm_tie_order: tuple[ForwardHorizon, ...]
    regime_thresholds: RegimeThresholdsSnapshot
    evaluation_sample_thresholds: SampleThresholdsSnapshot
    python_version: Annotated[
        str, Field(min_length=1, max_length=32, pattern=r"^\d+\.\d+\.\d+[a-z0-9]*$")
    ]

    @model_validator(mode="after")
    def _check_matches_preregistration(self) -> ConfigurationSnapshot:
        expected = expected_configuration(self.sample, python_version=self.python_version)
        if self.model_dump() != expected.model_dump():
            raise ValueError("configuration does not match the preregistered values")
        return self


def expected_configuration(sample: StudySample, *, python_version: str) -> ConfigurationSnapshot:
    design = SAMPLE_DESIGNS[sample]
    return ConfigurationSnapshot.model_construct(
        sample=sample,
        window_start=design.window_start,
        window_end=design.window_end,
        min_complete_sessions=design.min_complete_sessions,
        min_primary_cell_sessions=design.min_primary_cell_sessions,
        min_paired_sessions=design.min_paired_sessions,
        min_secondary_cell_sessions=design.min_secondary_cell_sessions,
        min_subgroup_sessions=SUBGROUP_MIN_SESSIONS,
        min_subgroup_observations=SUBGROUP_MIN_OBSERVATIONS,
        bootstrap_seed=BOOTSTRAP_SEED,
        bootstrap_replicates=BOOTSTRAP_REPLICATES,
        interval_lower_index=INTERVAL_LOWER_INDEX,
        interval_upper_index=INTERVAL_UPPER_INDEX,
        family_wise_alpha=Decimal("0.05"),
        secondary_alpha=Decimal("0.05"),
        effect_floor_bps=Decimal("1.0"),
        pct_retraced_floor_dollars=PCT_RETRACED_FLOOR_DOLLARS,
        extension_range_band_max=EXTENSION_RANGE_BAND_MAX,
        extension_reversion_threshold=EXTENSION_REVERSION_THRESHOLD,
        session_bar_count=SESSION_BAR_COUNT,
        quantile_percents=QUANTILE_PERCENTS,
        primary_horizons=PRIMARY_HORIZONS,
        holm_tie_order=PRIMARY_HORIZONS,
        regime_thresholds=_default_regime_thresholds_snapshot(),
        evaluation_sample_thresholds=DEFAULT_EVALUATION_SAMPLE_THRESHOLDS,
        python_version=python_version,
    )


class Provenance(BaseModel):
    """Claimed data linkage and frozen preregistration identity (C1.5).

    ``input_sha256`` / ``evaluation_record_sha256`` identify the exact
    canonical input and evaluation-record bytes this result claims to have
    analyzed; they do not by themselves prove authenticity.
    ``code_commit_sha`` is operator-supplied provenance, validated for shape
    only and not independently attested. The two preregistration SHAs are
    frozen constants. Ingestion-run IDs and builder reports are deliberately
    not here -- they are recorded in PROJECT_STATE.md / DATA_CATALOG.md."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    evaluation_record_sha256: Annotated[str, Field(pattern=_SHA256_PATTERN)]
    code_commit_sha: Annotated[str, Field(pattern=_COMMIT_SHA_PATTERN)]
    base_preregistration_commit_sha: Literal["f77d30f8e6e90a6b77eeca11fd11c3da9c9540c1"]
    clarification_commit_sha: Literal["1ff654de6dbbd54aeecb471010d1a612ca5abda6"]
    evaluation_record_schema_version: Literal["spy-vwap-reversion-evaluation-1"]


class SampleCounts(BaseModel):
    """Session and decision-point counts for the analyzed record."""

    model_config = ConfigDict(extra="forbid")

    unique_session_count: _Count
    complete_session_count: _Count
    incomplete_session_count: _Count
    eligible_above_vwap_count: _Count
    eligible_below_vwap_count: _Count

    @model_validator(mode="after")
    def _check(self) -> SampleCounts:
        if self.complete_session_count + self.incomplete_session_count != self.unique_session_count:
            raise ValueError("complete + incomplete sessions must equal unique sessions")
        return self


# --- Top-level result -----------------------------------------------------------------


class SpyVwapConfirmationResult(BaseModel):
    """The complete, deterministic result of one preregistered confirmation
    or holdout analysis. Underlying-only research: not validation, not
    options P&L, not a recommendation, not a trading action."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["spy-vwap-reversion-confirmation-1"] = CONFIRMATION_SCHEMA_VERSION
    symbol: Literal["SPY"] = "SPY"
    sample: StudySample
    window_start: date
    window_end: date
    generated_at: Annotated[datetime, AfterValidator(_require_utc_aware)]
    provenance: Provenance
    configuration: ConfigurationSnapshot
    counts: SampleCounts
    overall_gate: OverallGate
    primary: PrimaryAnalysis
    secondary: SecondaryAnalysis
    secondary_outcomes: Annotated[list[SideHorizonSecondary], Field(min_length=6, max_length=6)]
    asymmetry: Annotated[list[AsymmetryResult], Field(min_length=3, max_length=3)]
    subgroups: Annotated[list[SubgroupResult], Field(min_length=84, max_length=84)]
    notes: tuple[str, ...]

    @model_validator(mode="after")
    def _check_consistency(self) -> SpyVwapConfirmationResult:
        design = SAMPLE_DESIGNS[self.sample]
        if (self.window_start, self.window_end) != (design.window_start, design.window_end):
            raise ValueError("window does not match the preregistered sample")
        if self.configuration.sample != self.sample:
            raise ValueError("configuration sample does not match")
        if self.notes != FIXED_NOTES:
            raise ValueError("notes must be the fixed preregistered notes")
        if self.overall_gate.required_complete_sessions != design.min_complete_sessions:
            raise ValueError("overall gate does not match the sample design")
        if self.overall_gate.complete_session_count != self.counts.complete_session_count:
            raise ValueError("overall gate count does not match the sample counts")

        for result in self.primary.horizons:
            if (
                result.cell.required_sessions != design.min_primary_cell_sessions
                or result.cell.required_observations != 0
            ):
                raise ValueError("primary cell gate does not match the sample design")
        overall_ok = self.overall_gate.status == SampleStatus.OK
        expected_label = derive_study_label(
            overall_ok, [result.status for result in self.primary.horizons]
        )
        if self.primary.study_label != expected_label:
            raise ValueError("study label does not match the decision rule")

        for result in self.secondary.below_horizons:
            if result.cell.required_sessions != design.min_secondary_cell_sessions:
                raise ValueError("below-VWAP cell gate does not match the sample design")
        contrast = self.secondary.close_minus_30m_contrast
        if contrast.cell.required_sessions != design.min_paired_sessions:
            raise ValueError("paired-contrast gate does not match the sample design")
        expected_secondary = derive_secondary_label(
            overall_ok,
            [result.status for result in self.secondary.below_horizons],
            contrast.status,
        )
        if self.secondary.secondary_label != expected_secondary:
            raise ValueError("secondary label does not match the decision rule")

        keys = tuple((item.extension_side, item.horizon) for item in self.secondary_outcomes)
        if keys != expected_side_horizon_keys():
            raise ValueError("secondary outcomes must cover every side and horizon in order")
        for item in self.secondary_outcomes:
            for cell in (
                item.touch_rate,
                item.max_favorable_excursion_bps,
                item.max_adverse_excursion_bps,
                item.pct_extension_retraced_floored,
            ):
                if cell.required_sessions != design.min_secondary_cell_sessions:
                    raise ValueError("secondary cell gate does not match the sample design")
            if (
                item.signed_return_session_quantiles.required_sessions
                != design.min_secondary_cell_sessions
            ):
                raise ValueError("quantile gate does not match the sample design")

        if tuple(item.horizon for item in self.asymmetry) != PRIMARY_HORIZONS:
            raise ValueError("asymmetry must cover every horizon in order")
        for item in self.asymmetry:
            if item.cell.required_sessions != design.min_secondary_cell_sessions:
                raise ValueError("asymmetry gate does not match the sample design")

        subgroup_keys = tuple(
            (item.dimension, item.group, item.extension_side, item.horizon)
            for item in self.subgroups
        )
        if subgroup_keys != expected_subgroup_keys():
            raise ValueError("subgroups must cover every one-way cell exactly once, in order")
        return self
