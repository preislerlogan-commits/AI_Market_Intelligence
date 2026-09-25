"""Pure, offline confirmation analysis for the preregistered SPY
VWAP-reversion study (``docs/SPY_VWAP_REVERSION_PREREGISTRATION.md``,
including clarification C1).

``analyze_confirmation`` maps one validated ``SpyVwapReversionEvaluationRecord``
to one ``SpyVwapConfirmationResult``. It reuses the record's decision points
exactly as the evaluator froze them -- it never recomputes features,
regimes, VWAP, horizons, or decision points -- and it makes no network
request, opens no database, reads no file, and reads no clock
(``generated_at`` is caller-supplied). It imports only the standard library,
the existing evaluation contracts, and the existing step-c regime-engine
contracts/defaults.

## Refusals (fixed reasons, never echoing record content)

The analysis refuses, with ``ConfirmationAnalysisError``:

- malformed provenance (hashes, code commit);
- a record whose canonical SHA-256 does not equal the supplied one;
- non-default regime thresholds or evaluator sample thresholds;
- any session date outside the selected sample's fixed window (so no
  discovery-window date can enter either result);
- an internally inconsistent record (duplicate decision points, a session
  count that does not match its decision points, an eligible point without
  a side/VWAP/close, or an available outcome without its metric).

## Sample

Only **complete** sessions are analyzed: exactly 78 decision points, unique
indices 0-77, and a final point with ``session_bars_complete=True`` (C1.6).
Incomplete sessions are counted, never repaired, and contribute nothing.

## Cells

A cell is one (side, horizon[, subgroup]) combination. A session contributes
to a cell when it has at least one eligible point on that side (and in that
subgroup) whose horizon outcome is available; its value is the unweighted
mean of those points' metric. The primary population is every eligible
above-VWAP point -- no regime, time, bucket, or extension-size filter.

## Deterministic bootstrap (§5, C1.1, C1.2)

Per cell: values ordered by ascending session date; a fresh
``random.Random(20260923)``; exactly 10,000 replicates, each drawing ``N``
sessions with replacement via ``rng.randrange(N)`` in replicate-major order;
replicate estimate = mean of the drawn values; interval =
``[sorted[249], sorted[9749]]`` with no interpolation; tail counts
``#(replicate <= 0)`` and ``#(replicate >= 0)``.

**Exact arithmetic (C1.2).** Every stored input value (signed return,
MFE/MAE, percentage retraced, prices) originates in the evaluation record as
a finite ``Decimal``. Each is converted losslessly to its exact rational
value (``Fraction(Decimal)`` is exact), and all later arithmetic --
session means, bootstrap replicates, interval bounds, p-values, Holm
adjustments, paired contrasts, and every comparison against +/-1.0 bps, 0,
and 0.05 -- is exact rational arithmetic. No quantization and no floating
point occurs before any decision. In the result, each value's
``numerator``/``denominator`` fields are authoritative; the 30-decimal
``decimal_30dp`` value is display-only and cannot affect a status or label.
This implements C1.2's no-rounding intent and changes no threshold,
population, gate, or decision rule.

**Secondary label (C1.3).** An ``insufficient_sample`` paired contrast does
not by itself make the secondary label ``insufficient_sample``; it only
cannot trigger ``below_horizon_dependent``. Differing below-VWAP horizon
statuses can still trigger it. The label is ``insufficient_sample`` only
when the overall sample gate or a required below-VWAP horizon-cell gate
fails.

## What validation, hashes, and verification do and do not establish

- **Schema validation** (``SpyVwapConfirmationResult``) rejects malformed and
  internally inconsistent results: a status or label that does not follow
  from the serialized values, a display value that does not match its exact
  rational, a gate that drifts from the sample design, and so on. It
  **cannot** detect a coherent hand edit -- one that changes an estimate,
  its interval, p-values, statuses, and label consistently. The result is
  not tamper-proof.
- **Source hashes** identify the exact canonical evaluator-input bytes and
  evaluation-record bytes that the result *claims* to have analyzed. They
  do not prove who produced the result or that it is authentic.
- **The code commit** is operator-supplied provenance, validated for shape
  (40 lowercase hex characters) but not independently attested; it does not
  prove which code ran.
- **Authenticity** would require trusted custody of the files or an external
  signature, which is out of scope.
- **Statistical correctness** of a result is checked by
  ``verify_confirmation_result``: it deterministically recomputes the
  analysis from the referenced, validated evaluation record and requires
  the complete recomputed result to equal the supplied one. That detects
  coherent edits to any statistic, status, or label, but only relative to
  the evaluation record supplied -- whose own integrity is in turn only as
  good as its custody and the evaluator that produced it.

**Why the bootstrap is fast.** Session values are exact
``Fraction``s. Within a cell they are put over one common denominator ``D``
as integers ``A_i``, so each replicate total is an integer sum and each
replicate estimate is exactly ``total / (N * D)``. Sorting, tail counting,
and every comparison happen on those exact integers -- nothing is rounded.
Because the draw sequence depends only on the seed and ``N``, cells with the
same ``N`` share one generated draw stream; this is identical, draw for
draw, to resetting the generator for every cell (proven against a
straightforward per-cell reference implementation in the tests).
"""

from __future__ import annotations

import hashlib
import random
import re
import sys
from collections import defaultdict
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from math import lcm

from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    BASE_PREREGISTRATION_COMMIT_SHA,
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    CLARIFICATION_COMMIT_SHA,
    DEFAULT_EVALUATION_SAMPLE_THRESHOLDS,
    FIXED_NOTES,
    INTERVAL_LOWER_INDEX,
    INTERVAL_UPPER_INDEX,
    PCT_RETRACED_FLOOR_DOLLARS,
    PRIMARY_HORIZONS,
    QUANTILE_PERCENTS,
    SAMPLE_DESIGNS,
    SESSION_BAR_COUNT,
    SUBGROUP_MIN_OBSERVATIONS,
    SUBGROUP_MIN_SESSIONS,
    AsymmetryResult,
    BelowHorizonResult,
    BootstrapInference,
    ContrastStatus,
    ExactRational,
    GatedCell,
    HorizonStatus,
    OverallGate,
    PairedCell,
    PairedContrastResult,
    PctFloorCounts,
    PrimaryAnalysis,
    PrimaryHorizonResult,
    Provenance,
    SampleCounts,
    SecondaryAnalysis,
    SessionQuantiles,
    SideHorizonSecondary,
    SpyVwapConfirmationResult,
    StudySample,
    SubgroupDimension,
    SubgroupResult,
    _default_regime_thresholds_snapshot,
    below_horizon_status,
    contrast_status,
    derive_secondary_label,
    derive_study_label,
    expected_configuration,
    expected_subgroup_keys,
    extension_bucket,
    holm_adjust,
    nearest_rank_index,
    primary_horizon_status,
    raw_p_value,
)
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    DecisionPointRecord,
    EligibilityStatus,
    ExtensionSide,
    ForwardHorizon,
    HorizonOutcome,
    SampleStatus,
    SpyVwapReversionEvaluationRecord,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import to_json_str

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_PCT_FLOOR = Fraction(PCT_RETRACED_FLOOR_DOLLARS)


class AnalysisRefusal(StrEnum):
    """Fixed, sanitized reasons the analysis refuses a record."""

    PROVENANCE_INVALID = "provenance_invalid"
    RECORD_HASH_MISMATCH = "record_hash_mismatch"
    NONDEFAULT_REGIME_THRESHOLDS = "nondefault_regime_thresholds"
    NONDEFAULT_SAMPLE_THRESHOLDS = "nondefault_sample_thresholds"
    WINDOW_VIOLATION = "window_violation"
    INCONSISTENT_RECORD = "inconsistent_record"


class ConfirmationAnalysisError(Exception):
    """Raised with one fixed ``AnalysisRefusal``; the message never contains
    record content, dates, values, or hashes."""

    def __init__(self, reason: AnalysisRefusal) -> None:
        super().__init__(reason.value)
        self.reason = reason


def is_valid_commit_sha(value: object) -> bool:
    """Exactly 40 lowercase hexadecimal characters."""
    return isinstance(value, str) and _COMMIT_RE.fullmatch(value) is not None


def canonical_record_sha256(record: SpyVwapReversionEvaluationRecord) -> str:
    """SHA-256 of the record's canonical serialization (the exact bytes the
    CLI writes via ``spy_vwap_reversion_serialization.write_record``)."""
    return hashlib.sha256(to_json_str(record).encode("utf-8")).hexdigest()


# --- Deterministic bootstrap -----------------------------------------------------------


@dataclass(frozen=True)
class BootstrapOutcome:
    """Exact bootstrap summary for one cell."""

    estimate: Fraction
    lower: Fraction
    upper: Fraction
    at_or_below_zero: int
    at_or_above_zero: int

    @property
    def p_raw(self) -> Fraction:
        return raw_p_value(self.at_or_below_zero, self.at_or_above_zero)


def _scaled_integers(values: Sequence[Fraction]) -> tuple[list[int], int]:
    denominator = lcm(*(value.denominator for value in values))
    return [value.numerator * (denominator // value.denominator) for value in values], denominator


def bootstrap_cells(
    cells: Mapping[Hashable, Sequence[Fraction]],
) -> dict[Hashable, BootstrapOutcome]:
    """Session-blocked bootstrap of every cell, each exactly as if the
    generator were freshly reset to ``BOOTSTRAP_SEED`` for that cell alone.
    ``cells`` values must already be ordered by ascending session date."""
    keys_by_size: dict[int, list[Hashable]] = defaultdict(list)
    for key, values in cells.items():
        if not values:
            raise ValueError("a bootstrapped cell must contain at least one session")
        keys_by_size[len(values)].append(key)

    results: dict[Hashable, BootstrapOutcome] = {}
    for size in sorted(keys_by_size):
        keys = keys_by_size[size]
        scaled = [_scaled_integers(cells[key]) for key in keys]
        getters = [integers.__getitem__ for integers, _ in scaled]
        totals: list[list[int]] = [[] for _ in keys]
        appenders = [column.append for column in totals]
        draw = random.Random(BOOTSTRAP_SEED).randrange
        draws_per_replicate = range(size)
        for _ in range(BOOTSTRAP_REPLICATES):
            indices = [draw(size) for _ in draws_per_replicate]
            for getter, append in zip(getters, appenders, strict=True):
                append(sum(map(getter, indices)))
        for key, (integers, denominator), column in zip(keys, scaled, totals, strict=True):
            scale = size * denominator
            ordered = sorted(column)
            results[key] = BootstrapOutcome(
                estimate=Fraction(sum(integers), scale),
                lower=Fraction(ordered[INTERVAL_LOWER_INDEX], scale),
                upper=Fraction(ordered[INTERVAL_UPPER_INDEX], scale),
                at_or_below_zero=sum(1 for total in column if total <= 0),
                at_or_above_zero=sum(1 for total in column if total >= 0),
            )
    return results


# --- Record checks ----------------------------------------------------------------------


def _check_record(
    record: SpyVwapReversionEvaluationRecord, sample: StudySample
) -> dict[date, list[DecisionPointRecord]]:
    if record.regime_thresholds != _default_regime_thresholds_snapshot():
        raise ConfirmationAnalysisError(AnalysisRefusal.NONDEFAULT_REGIME_THRESHOLDS)
    if record.sample_thresholds != DEFAULT_EVALUATION_SAMPLE_THRESHOLDS:
        raise ConfirmationAnalysisError(AnalysisRefusal.NONDEFAULT_SAMPLE_THRESHOLDS)

    design = SAMPLE_DESIGNS[sample]
    by_session: dict[date, list[DecisionPointRecord]] = defaultdict(list)
    seen: set[tuple[date, int]] = set()
    for dp in record.decision_points:
        if not design.window_start <= dp.session_date <= design.window_end:
            raise ConfirmationAnalysisError(AnalysisRefusal.WINDOW_VIOLATION)
        identity = (dp.session_date, dp.bar_index_in_session)
        if identity in seen:
            raise ConfirmationAnalysisError(AnalysisRefusal.INCONSISTENT_RECORD)
        seen.add(identity)
        if dp.eligibility == EligibilityStatus.ELIGIBLE and (
            dp.extension_side is None or dp.signal_vwap is None or dp.signal_close is None
        ):
            raise ConfirmationAnalysisError(AnalysisRefusal.INCONSISTENT_RECORD)
        for outcome in dp.outcomes:
            if outcome.available and None in (
                outcome.signed_return_toward_vwap_bps,
                outcome.pct_extension_retraced,
                outcome.max_favorable_excursion_bps,
                outcome.max_adverse_excursion_bps,
            ):
                raise ConfirmationAnalysisError(AnalysisRefusal.INCONSISTENT_RECORD)
        by_session[dp.session_date].append(dp)
    if len(by_session) != record.unique_session_count:
        raise ConfirmationAnalysisError(AnalysisRefusal.INCONSISTENT_RECORD)
    return by_session


def is_complete_session(points: Sequence[DecisionPointRecord]) -> bool:
    """C1.6: exactly 78 decision points, unique indices 0-77, and the final
    point carries ``session_bars_complete=True``."""
    if len(points) != SESSION_BAR_COUNT:
        return False
    by_index = {dp.bar_index_in_session: dp for dp in points}
    if set(by_index) != set(range(SESSION_BAR_COUNT)):
        return False
    return by_index[SESSION_BAR_COUNT - 1].session_bars_complete


# --- Cell construction ------------------------------------------------------------------


def _outcome(dp: DecisionPointRecord, horizon: ForwardHorizon) -> HorizonOutcome:
    return next(outcome for outcome in dp.outcomes if outcome.horizon == horizon)


def _original_extension(dp: DecisionPointRecord) -> Fraction:
    """The evaluator's percentage-retraced denominator,
    ``|signal_close - signal_vwap|``, computed exactly (Decimal subtraction
    and ``abs`` would round to the context precision)."""
    return abs(Fraction(dp.signal_close) - Fraction(dp.signal_vwap))  # type: ignore[arg-type]


_Metric = Callable[[HorizonOutcome], Decimal | int]


def _signed_return(outcome: HorizonOutcome) -> Decimal:
    return outcome.signed_return_toward_vwap_bps  # type: ignore[return-value]


def _touch(outcome: HorizonOutcome) -> int:
    return 1 if outcome.touched_vwap else 0


def _mfe(outcome: HorizonOutcome) -> Decimal:
    return outcome.max_favorable_excursion_bps  # type: ignore[return-value]


def _mae(outcome: HorizonOutcome) -> Decimal:
    return outcome.max_adverse_excursion_bps  # type: ignore[return-value]


def _pct(outcome: HorizonOutcome) -> Decimal:
    return outcome.pct_extension_retraced  # type: ignore[return-value]


@dataclass(frozen=True)
class _CellData:
    """Per-session means (ascending by date) and the observation count."""

    session_means: dict[date, Fraction]
    observation_count: int

    @property
    def ordered_values(self) -> tuple[Fraction, ...]:
        return tuple(self.session_means[day] for day in sorted(self.session_means))


def _cell(
    points: Sequence[DecisionPointRecord],
    horizon: ForwardHorizon,
    metric: _Metric,
    include: Callable[[DecisionPointRecord], bool] | None = None,
) -> _CellData:
    sums: dict[date, Fraction] = defaultdict(Fraction)
    counts: dict[date, int] = defaultdict(int)
    for dp in points:
        if include is not None and not include(dp):
            continue
        outcome = _outcome(dp, horizon)
        if not outcome.available:
            continue
        sums[dp.session_date] += Fraction(metric(outcome))
        counts[dp.session_date] += 1
    means = {day: sums[day] / counts[day] for day in sums}
    return _CellData(session_means=means, observation_count=sum(counts.values()))


def _paired(first: _CellData, second: _CellData) -> tuple[Fraction, ...]:
    """``first - second`` for sessions present in both, ascending by date."""
    shared = sorted(set(first.session_means) & set(second.session_means))
    return tuple(first.session_means[day] - second.session_means[day] for day in shared)


def _rational(value: Fraction) -> ExactRational:
    return ExactRational.from_fraction(value)


def _inference(outcome: BootstrapOutcome, *, with_p: bool) -> BootstrapInference:
    return BootstrapInference(
        estimate=_rational(outcome.estimate),
        interval_lower=_rational(outcome.lower),
        interval_upper=_rational(outcome.upper),
        replicates_at_or_below_zero=outcome.at_or_below_zero,
        replicates_at_or_above_zero=outcome.at_or_above_zero,
        p_raw=_rational(outcome.p_raw) if with_p else None,
    )


# --- Analysis -----------------------------------------------------------------------------


def analyze_confirmation(
    record: SpyVwapReversionEvaluationRecord,
    *,
    sample: StudySample,
    input_sha256: str,
    evaluation_record_sha256: str,
    code_commit_sha: str,
    generated_at: datetime,
) -> SpyVwapConfirmationResult:
    """Run the preregistered confirmation (or holdout) analysis over one
    validated evaluation record. Pure: no I/O, no clock read; the only
    randomness is the fixed-seed bootstrap."""
    if not (
        isinstance(input_sha256, str)
        and _SHA256_RE.fullmatch(input_sha256)
        and isinstance(evaluation_record_sha256, str)
        and _SHA256_RE.fullmatch(evaluation_record_sha256)
        and is_valid_commit_sha(code_commit_sha)
    ):
        raise ConfirmationAnalysisError(AnalysisRefusal.PROVENANCE_INVALID)
    if canonical_record_sha256(record) != evaluation_record_sha256:
        raise ConfirmationAnalysisError(AnalysisRefusal.RECORD_HASH_MISMATCH)

    by_session = _check_record(record, sample)
    design = SAMPLE_DESIGNS[sample]

    complete_days = sorted(day for day, points in by_session.items() if is_complete_session(points))
    eligible = [
        dp
        for day in complete_days
        for dp in sorted(by_session[day], key=lambda point: point.bar_index_in_session)
        if dp.eligibility == EligibilityStatus.ELIGIBLE
    ]
    side_points = {
        side: [dp for dp in eligible if dp.extension_side == side] for side in ExtensionSide
    }

    overall_gate = OverallGate(
        required_complete_sessions=design.min_complete_sessions,
        complete_session_count=len(complete_days),
        status=(
            SampleStatus.OK
            if len(complete_days) >= design.min_complete_sessions
            else SampleStatus.INSUFFICIENT_SAMPLE
        ),
    )
    counts = SampleCounts(
        unique_session_count=len(by_session),
        complete_session_count=len(complete_days),
        incomplete_session_count=len(by_session) - len(complete_days),
        eligible_above_vwap_count=len(side_points[ExtensionSide.ABOVE_VWAP]),
        eligible_below_vwap_count=len(side_points[ExtensionSide.BELOW_VWAP]),
    )

    # -- Collect every cell's data, then bootstrap only the cells that pass.
    signed: dict[tuple[ExtensionSide, ForwardHorizon], _CellData] = {}
    secondary_cells: dict[tuple[ExtensionSide, ForwardHorizon, str], _CellData] = {}
    floor_counts: dict[tuple[ExtensionSide, ForwardHorizon], PctFloorCounts] = {}
    for side in ExtensionSide:
        points = side_points[side]
        for horizon in PRIMARY_HORIZONS:
            signed[(side, horizon)] = _cell(points, horizon, _signed_return)
            secondary_cells[(side, horizon, "touch")] = _cell(points, horizon, _touch)
            secondary_cells[(side, horizon, "mfe")] = _cell(points, horizon, _mfe)
            secondary_cells[(side, horizon, "mae")] = _cell(points, horizon, _mae)
            at_or_above_floor = [dp for dp in points if _original_extension(dp) >= _PCT_FLOOR]
            secondary_cells[(side, horizon, "pct")] = _cell(at_or_above_floor, horizon, _pct)
            available = sum(1 for dp in points if _outcome(dp, horizon).available)
            floored = secondary_cells[(side, horizon, "pct")].observation_count
            floor_counts[(side, horizon)] = PctFloorCounts(
                available_observations=available,
                below_floor_observations=available - floored,
                at_or_above_floor_observations=floored,
            )

    below = ExtensionSide.BELOW_VWAP
    above = ExtensionSide.ABOVE_VWAP
    contrast_values = _paired(
        signed[(below, ForwardHorizon.TO_SESSION_CLOSE)],
        signed[(below, ForwardHorizon.INTRADAY_30M)],
    )
    asymmetry_values = {
        horizon: _paired(signed[(above, horizon)], signed[(below, horizon)])
        for horizon in PRIMARY_HORIZONS
    }

    def group_of(dimension: SubgroupDimension, dp: DecisionPointRecord) -> str:
        if dimension == SubgroupDimension.REGIME:
            return dp.regime.value
        if dimension == SubgroupDimension.TIME_OF_DAY:
            return dp.time_of_day_bucket.value
        return extension_bucket(dp.normalized_vwap_extension).value

    subgroup_cells: dict[tuple, _CellData] = {}
    for dimension, group, side, horizon in expected_subgroup_keys():
        subgroup_cells[(dimension, group, side, horizon)] = _cell(
            side_points[side],
            horizon,
            _signed_return,
            include=lambda dp, d=dimension, g=group: group_of(d, dp) == g,
        )

    jobs: dict[Hashable, Sequence[Fraction]] = {}
    for key, data in signed.items():
        # Above-VWAP signed return is the primary cell; below-VWAP signed
        # return is a secondary cell (C1.6). Both gates are reported as used.
        gate = (
            design.min_primary_cell_sessions
            if key[0] == ExtensionSide.ABOVE_VWAP
            else design.min_secondary_cell_sessions
        )
        if len(data.session_means) >= gate:
            jobs[("signed",) + key] = data.ordered_values
    for key, data in secondary_cells.items():
        if len(data.session_means) >= design.min_secondary_cell_sessions:
            jobs[("secondary",) + key] = data.ordered_values
    if len(contrast_values) >= design.min_paired_sessions:
        jobs[("contrast",)] = contrast_values
    for horizon, values in asymmetry_values.items():
        if len(values) >= design.min_secondary_cell_sessions:
            jobs[("asymmetry", horizon)] = values
    for key, data in subgroup_cells.items():
        if (
            len(data.session_means) >= SUBGROUP_MIN_SESSIONS
            and data.observation_count >= SUBGROUP_MIN_OBSERVATIONS
        ):
            jobs[("subgroup",) + key] = data.ordered_values
    boot = bootstrap_cells(jobs)

    def gated(
        data: _CellData,
        job_key: Hashable,
        *,
        required_sessions: int,
        required_observations: int = 0,
        with_p: bool = False,
    ) -> GatedCell:
        outcome = boot.get(job_key)
        return GatedCell(
            status=SampleStatus.OK if outcome is not None else SampleStatus.INSUFFICIENT_SAMPLE,
            required_sessions=required_sessions,
            required_observations=required_observations,
            session_count=len(data.session_means),
            observation_count=data.observation_count,
            inference=_inference(outcome, with_p=with_p) if outcome is not None else None,
        )

    def paired_cell(count: int, job_key: Hashable, *, required: int, with_p: bool) -> PairedCell:
        outcome = boot.get(job_key)
        return PairedCell(
            status=SampleStatus.OK if outcome is not None else SampleStatus.INSUFFICIENT_SAMPLE,
            required_sessions=required,
            paired_session_count=count,
            inference=_inference(outcome, with_p=with_p) if outcome is not None else None,
        )

    # -- Primary (above-VWAP) horizons, Holm family, study label.
    primary_cells = [
        gated(
            signed[(above, horizon)],
            ("signed", above, horizon),
            required_sessions=design.min_primary_cell_sessions,
            with_p=True,
        )
        for horizon in PRIMARY_HORIZONS
    ]
    family_complete = all(cell.status == SampleStatus.OK for cell in primary_cells)
    if family_complete:
        adjusted = holm_adjust([boot[("signed", above, h)].p_raw for h in PRIMARY_HORIZONS])
    primary_results = []
    for index, (horizon, cell) in enumerate(zip(PRIMARY_HORIZONS, primary_cells, strict=True)):
        if family_complete:
            outcome = boot[("signed", above, horizon)]
            p_holm = adjusted[index]
            status = primary_horizon_status(outcome.estimate, outcome.lower, outcome.upper, p_holm)
            primary_results.append(
                PrimaryHorizonResult(
                    horizon=horizon, cell=cell, p_holm=_rational(p_holm), status=status
                )
            )
        else:
            primary_results.append(
                PrimaryHorizonResult(
                    horizon=horizon,
                    cell=cell,
                    p_holm=None,
                    status=HorizonStatus.INSUFFICIENT_SAMPLE,
                )
            )
    overall_ok = overall_gate.status == SampleStatus.OK
    primary = PrimaryAnalysis(
        horizons=primary_results,
        study_label=derive_study_label(overall_ok, [result.status for result in primary_results]),
    )

    # -- Secondary: below-VWAP horizons, paired contrast, label.
    below_results = []
    for horizon in PRIMARY_HORIZONS:
        cell = gated(
            signed[(below, horizon)],
            ("signed", below, horizon),
            required_sessions=design.min_secondary_cell_sessions,
        )
        outcome = boot.get(("signed", below, horizon))
        status = (
            below_horizon_status(outcome.lower, outcome.upper)
            if outcome is not None
            else HorizonStatus.INSUFFICIENT_SAMPLE
        )
        below_results.append(BelowHorizonResult(horizon=horizon, cell=cell, status=status))
    contrast_outcome = boot.get(("contrast",))
    contrast = PairedContrastResult(
        cell=paired_cell(
            len(contrast_values), ("contrast",), required=design.min_paired_sessions, with_p=True
        ),
        status=(
            contrast_status(
                contrast_outcome.estimate,
                contrast_outcome.lower,
                contrast_outcome.upper,
                contrast_outcome.p_raw,
            )
            if contrast_outcome is not None
            else ContrastStatus.INSUFFICIENT_SAMPLE
        ),
    )
    secondary = SecondaryAnalysis(
        below_horizons=below_results,
        close_minus_30m_contrast=contrast,
        secondary_label=derive_secondary_label(
            overall_ok, [result.status for result in below_results], contrast.status
        ),
    )

    # -- Secondary outcomes per side and horizon.
    secondary_outcomes = []
    for side in ExtensionSide:
        for horizon in PRIMARY_HORIZONS:
            required = design.min_secondary_cell_sessions

            def secondary_cell(
                name: str, side=side, horizon=horizon, required=required
            ) -> GatedCell:
                return gated(
                    secondary_cells[(side, horizon, name)],
                    ("secondary", side, horizon, name),
                    required_sessions=required,
                )

            secondary_outcomes.append(
                SideHorizonSecondary(
                    extension_side=side,
                    horizon=horizon,
                    touch_rate=secondary_cell("touch"),
                    max_favorable_excursion_bps=secondary_cell("mfe"),
                    max_adverse_excursion_bps=secondary_cell("mae"),
                    pct_extension_retraced_floored=secondary_cell("pct"),
                    pct_floor_counts=floor_counts[(side, horizon)],
                    signed_return_session_quantiles=_quantiles(
                        signed[(side, horizon)].ordered_values, required
                    ),
                )
            )

    asymmetry = [
        AsymmetryResult(
            horizon=horizon,
            cell=paired_cell(
                len(asymmetry_values[horizon]),
                ("asymmetry", horizon),
                required=design.min_secondary_cell_sessions,
                with_p=False,
            ),
        )
        for horizon in PRIMARY_HORIZONS
    ]

    subgroups = [
        SubgroupResult(
            dimension=dimension,
            group=group,
            extension_side=side,
            horizon=horizon,
            cell=gated(
                subgroup_cells[(dimension, group, side, horizon)],
                ("subgroup", dimension, group, side, horizon),
                required_sessions=SUBGROUP_MIN_SESSIONS,
                required_observations=SUBGROUP_MIN_OBSERVATIONS,
            ),
        )
        for dimension, group, side, horizon in expected_subgroup_keys()
    ]

    return SpyVwapConfirmationResult(
        sample=sample,
        window_start=design.window_start,
        window_end=design.window_end,
        generated_at=generated_at,
        provenance=Provenance(
            input_sha256=input_sha256,
            evaluation_record_sha256=evaluation_record_sha256,
            code_commit_sha=code_commit_sha,
            base_preregistration_commit_sha=BASE_PREREGISTRATION_COMMIT_SHA,
            clarification_commit_sha=CLARIFICATION_COMMIT_SHA,
            evaluation_record_schema_version=record.schema_version,
        ),
        configuration=expected_configuration(sample, python_version=_python_version()),
        counts=counts,
        overall_gate=overall_gate,
        primary=primary,
        secondary=secondary,
        secondary_outcomes=secondary_outcomes,
        asymmetry=asymmetry,
        subgroups=subgroups,
        notes=FIXED_NOTES,
    )


class ConfirmationVerificationError(Exception):
    """The single, fixed, sanitized verification failure. Its message never
    contains a value, hash, date, path, or record content, and it never
    chains the underlying exception."""

    MESSAGE = "confirmation_result_verification_failed"

    def __init__(self) -> None:
        super().__init__(self.MESSAGE)


def _revalidated(model_type, value):
    """Re-run full contract validation. ``model_validate`` returns an
    existing instance unchanged (Pydantic does not revalidate instances by
    default), so an instance is dumped (exact ``Decimal``s preserved) and
    validated again."""
    payload = value.model_dump() if isinstance(value, model_type) else value
    return model_type.model_validate(payload)


def verify_confirmation_result(
    evaluation_record: SpyVwapReversionEvaluationRecord | Mapping[str, object],
    confirmation_result: SpyVwapConfirmationResult | Mapping[str, object],
    *,
    expected_input_sha256: str,
    expected_evaluation_record_sha256: str,
) -> None:
    """Verify a confirmation result by deterministic recomputation.

    Validates both inputs through their contracts, requires both expected
    hashes to equal the result's provenance (and the record hash to equal
    the record's canonical serialization), recomputes the analysis from the
    record with the result's own sample, ``generated_at``, and code commit,
    and requires the complete recomputed result to equal the supplied one --
    including the fixed provenance constants and configuration snapshot
    (so verification must run on the Python version the result records).

    Pure: no I/O, no clock read. Returns ``None`` on success; any mismatch or
    invalid input raises ``ConfirmationVerificationError`` with one fixed
    message. See the module docstring for what this does and does not
    establish.
    """
    try:
        record = _revalidated(SpyVwapReversionEvaluationRecord, evaluation_record)
        result = _revalidated(SpyVwapConfirmationResult, confirmation_result)
        if not (
            isinstance(expected_input_sha256, str)
            and _SHA256_RE.fullmatch(expected_input_sha256)
            and isinstance(expected_evaluation_record_sha256, str)
            and _SHA256_RE.fullmatch(expected_evaluation_record_sha256)
        ):
            raise ValueError
        if (
            result.provenance.input_sha256 != expected_input_sha256
            or result.provenance.evaluation_record_sha256 != expected_evaluation_record_sha256
        ):
            raise ValueError
        recomputed = analyze_confirmation(
            record,
            sample=result.sample,
            input_sha256=expected_input_sha256,
            evaluation_record_sha256=expected_evaluation_record_sha256,
            code_commit_sha=result.provenance.code_commit_sha,
            generated_at=result.generated_at,
        )
        matches = recomputed.model_dump(mode="json") == result.model_dump(mode="json")
    except Exception:
        matches = False
    if not matches:
        raise ConfirmationVerificationError() from None


def _python_version() -> str:
    """The running interpreter's ``major.minor.micro`` (recorded, per §9)."""
    return "{}.{}.{}".format(*sys.version_info[:3])


def _quantiles(values: Sequence[Fraction], required: int) -> SessionQuantiles:
    """Nearest-rank quantiles (§3) of the session means, gated like the
    sample's secondary cells."""
    if len(values) < required:
        return SessionQuantiles(
            status=SampleStatus.INSUFFICIENT_SAMPLE,
            required_sessions=required,
            session_count=len(values),
        )
    ordered = sorted(values)
    picked = {
        percent: _rational(ordered[nearest_rank_index(percent, len(ordered))])
        for percent in QUANTILE_PERCENTS
    }
    return SessionQuantiles(
        status=SampleStatus.OK,
        required_sessions=required,
        session_count=len(values),
        p10=picked[10],
        p25=picked[25],
        p50=picked[50],
        p75=picked[75],
        p90=picked[90],
    )
