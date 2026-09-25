"""Statistical-engine tests for the SPY VWAP confirmation analysis.

Synthetic records only (see ``spy_vwap_confirmation_fixtures``): no real
bars, no real evaluation record, no database, no network. Several scenarios
use synthetic sessions dated inside the holdout window purely because its
40-session gate keeps the bootstrap cheap; no real holdout data exists or is
touched.
"""

from __future__ import annotations

import hashlib
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from market_intelligence.evaluation.spy_vwap_reversion_confirmation import (
    AnalysisRefusal,
    ConfirmationAnalysisError,
    ConfirmationVerificationError,
    analyze_confirmation,
    bootstrap_cells,
    canonical_record_sha256,
    is_complete_session,
    verify_confirmation_result,
)
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    PRIMARY_HORIZONS,
    ContrastStatus,
    ExactRational,
    ExtensionBucket,
    HorizonStatus,
    SecondaryLabel,
    SpyVwapConfirmationResult,
    StudyLabel,
    StudySample,
    SubgroupDimension,
    holm_adjust,
    raw_p_value,
)
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    ExtensionSide,
    ForwardHorizon,
    HorizonOutcome,
    SampleStatus,
    SampleThresholdsSnapshot,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import to_json_str
from market_intelligence.market_features.spy_regime_contracts import Regime, TimeOfDayBucket
from market_intelligence.tests.spy_vwap_confirmation_fixtures import (
    CODE_COMMIT,
    CONFIRMATION_START,
    GENERATED_AT,
    HOLDOUT_START,
    INPUT_SHA,
    analysis_kwargs,
    default_regime_thresholds,
    make_record,
    patterned_value,
    point,
    reference_result_sha256,
    session,
    study_points,
    weekdays,
)

H30, H2, HC = PRIMARY_HORIZONS
ABOVE = ExtensionSide.ABOVE_VWAP
BELOW = ExtensionSide.BELOW_VWAP
START = {StudySample.CONFIRMATION: CONFIRMATION_START, StudySample.HOLDOUT: HOLDOUT_START}


def _analyze(points, sample=StudySample.HOLDOUT, **record_kwargs):
    record = make_record(points, **record_kwargs)
    return analyze_confirmation(record, **analysis_kwargs(record, sample))


def _study(n, sample=StudySample.HOLDOUT, *, above, below, **kwargs):
    return _analyze(
        study_points(weekdays(START[sample], n), above=above, below=below, **kwargs), sample
    )


def _constant(value):
    return lambda i, side, h: value


def _by_horizon(values: dict):
    return lambda i, side, h: values[h]


def _primary(result, horizon):
    return next(item for item in result.primary.horizons if item.horizon == horizon)


def _below(result, horizon):
    return next(item for item in result.secondary.below_horizons if item.horizon == horizon)


def _subgroup(result, dimension, group, side, horizon):
    return next(
        item
        for item in result.subgroups
        if (item.dimension, item.group, item.extension_side, item.horizon)
        == (dimension, group, side, horizon)
    )


# --- Bootstrap engine vs. a straightforward reference -------------------------------


def reference_bootstrap(values: Sequence[Fraction]):
    """Deliberately naive: fresh generator, per-draw Fraction sums, sort."""
    n = len(values)
    rng = random.Random(BOOTSTRAP_SEED)
    replicates = []
    for _ in range(BOOTSTRAP_REPLICATES):
        total = Fraction(0)
        for _ in range(n):
            total += values[rng.randrange(n)]
        replicates.append(total / n)
    ordered = sorted(replicates)
    return {
        "estimate": sum(values, Fraction(0)) / n,
        "lower": ordered[249],
        "upper": ordered[9749],
        "at_or_below_zero": sum(1 for r in replicates if r <= 0),
        "at_or_above_zero": sum(1 for r in replicates if r >= 0),
    }, ordered


VALUE_SETS = {
    "single": [Fraction(7, 3)],
    "three_mixed_sign": [Fraction(-5, 2), Fraction(1, 3), Fraction(4)],
    "seven_awkward_denominators": [
        Fraction(1, 3),
        Fraction(-2, 7),
        Fraction(Decimal("1.2345")),
        Fraction(11, 13),
        Fraction(-1, 78),
        Fraction(Decimal("0.0001")),
        Fraction(5, 77),
    ],
    "twenty_five_mixed": [Fraction(i * 7 % 11 - 5, (i % 4) + 1) for i in range(25)],
}


@pytest.mark.parametrize("name", list(VALUE_SETS))
def test_bootstrap_matches_the_straightforward_reference(name):
    values = VALUE_SETS[name]
    expected, _ = reference_bootstrap(values)
    outcome = bootstrap_cells({"cell": values})["cell"]
    assert outcome.estimate == expected["estimate"]
    assert outcome.lower == expected["lower"]
    assert outcome.upper == expected["upper"]
    assert outcome.at_or_below_zero == expected["at_or_below_zero"]
    assert outcome.at_or_above_zero == expected["at_or_above_zero"]
    assert outcome.p_raw == raw_p_value(expected["at_or_below_zero"], expected["at_or_above_zero"])


def test_interval_is_exactly_order_statistics_249_and_9749():
    # Widely spread values make replicate means (almost) all distinct, so an
    # off-by-one index would select a different value.
    generator = random.Random(3)
    values = [Fraction(generator.randint(-(10**6), 10**6), 10**4) for _ in range(25)]
    _, ordered = reference_bootstrap(values)
    outcome = bootstrap_cells({"cell": values})["cell"]
    assert outcome.lower == ordered[249]
    assert outcome.upper == ordered[9749]
    # The neighbours differ, so an off-by-one index would be detected.
    assert ordered[248] != ordered[249] or ordered[249] != ordered[250]
    assert ordered[9748] != ordered[9749] or ordered[9749] != ordered[9750]


def test_bootstrap_is_reproducible_and_independent_of_cell_order_and_grouping():
    cells = {
        "a": VALUE_SETS["twenty_five_mixed"],
        "b": [v * 3 - 1 for v in VALUE_SETS["twenty_five_mixed"]],  # same N as "a"
        "c": VALUE_SETS["seven_awkward_denominators"],
    }
    together = bootstrap_cells(cells)
    reversed_order = bootstrap_cells(dict(reversed(list(cells.items()))))
    singles = {key: bootstrap_cells({key: values})[key] for key, values in cells.items()}
    assert together == reversed_order == singles
    assert bootstrap_cells(cells) == together


def test_bootstrap_tail_counts_for_all_positive_and_all_zero_cells():
    positive = bootstrap_cells({"p": [Fraction(1)] * 5})["p"]
    assert (positive.at_or_below_zero, positive.at_or_above_zero) == (0, BOOTSTRAP_REPLICATES)
    assert positive.p_raw == Fraction(2, 10001)
    zero = bootstrap_cells({"z": [Fraction(0)] * 5})["z"]
    assert (zero.at_or_below_zero, zero.at_or_above_zero) == (BOOTSTRAP_REPLICATES,) * 2
    assert zero.p_raw == 1


def test_bootstrap_never_rounds_tiny_values():
    tiny = Fraction(1, 10**40)  # zero at any fixed 12- or 30-place scale
    outcome = bootstrap_cells({"t": [tiny, 2 * tiny, 3 * tiny]})["t"]
    assert outcome.lower > 0
    assert outcome.at_or_below_zero == 0


def test_bootstrap_refuses_an_empty_cell():
    with pytest.raises(ValueError):
        bootstrap_cells({"empty": []})


# --- Primary analysis ---------------------------------------------------------------------


@pytest.fixture(scope="module")
def supported():
    points = study_points(
        weekdays(CONFIRMATION_START, 100),
        above=patterned_value("5"),
        below=patterned_value("0.2"),
        points_per_side=2,
    )
    record = make_record(points)
    return record, analyze_confirmation(record, **analysis_kwargs(record))


def test_supported_scenario(supported):
    _, result = supported
    assert result.overall_gate.status == SampleStatus.OK
    assert [item.status for item in result.primary.horizons] == [HorizonStatus.POSITIVE] * 3
    assert result.primary.study_label == StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH
    assert result.counts.complete_session_count == 100
    raw = [item.cell.inference.p_raw.as_fraction() for item in result.primary.horizons]
    assert tuple(item.p_holm.as_fraction() for item in result.primary.horizons) == holm_adjust(raw)


def test_primary_estimate_is_the_unweighted_mean_of_session_means(supported):
    record, result = supported
    for horizon in PRIMARY_HORIZONS:
        per_session: dict[date, list[Fraction]] = {}
        for dp in record.decision_points:
            if dp.extension_side != ABOVE:
                continue
            outcome = next(o for o in dp.outcomes if o.horizon == horizon)
            if outcome.available:
                value = Fraction(outcome.signed_return_toward_vwap_bps)
                per_session.setdefault(dp.session_date, []).append(value)
        means = [sum(v, Fraction(0)) / len(v) for v in per_session.values()]
        expected = sum(means, Fraction(0)) / len(means)
        cell = _primary(result, horizon).cell
        assert cell.inference.estimate.as_fraction() == expected
        assert cell.session_count == 100
        assert cell.observation_count == 200  # two above-VWAP points per session


def test_result_provenance_and_configuration(supported):
    record, result = supported
    assert result.provenance.evaluation_record_sha256 == canonical_record_sha256(record)
    assert result.provenance.input_sha256 == INPUT_SHA
    assert result.provenance.code_commit_sha == CODE_COMMIT
    assert result.generated_at == GENERATED_AT
    assert result.configuration.sample == StudySample.CONFIRMATION
    assert result.configuration.min_primary_cell_sessions == 80


def test_canonical_record_hash_is_the_sha256_of_the_canonical_bytes(supported):
    record, _ = supported
    expected = hashlib.sha256(to_json_str(record).encode("utf-8")).hexdigest()
    assert canonical_record_sha256(record) == expected


def test_shuffled_decision_points_give_identical_results(supported):
    record, result = supported
    shuffled = list(record.decision_points)
    random.Random(7).shuffle(shuffled)
    other = make_record(shuffled)
    other_result = analyze_confirmation(other, **analysis_kwargs(other))
    first = result.model_dump(mode="json")
    second = other_result.model_dump(mode="json")
    first["provenance"].pop("evaluation_record_sha256")
    second["provenance"].pop("evaluation_record_sha256")
    assert first == second


def test_analysis_is_deterministic(supported):
    record, result = supported
    assert analyze_confirmation(record, **analysis_kwargs(record)) == result


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", HorizonStatus.POSITIVE),  # exactly +1.0 bps
        ("1.0000000000001", HorizonStatus.POSITIVE),
        ("0.9999999999999", HorizonStatus.INCONCLUSIVE),  # 1e-13 below the floor
        ("-1", HorizonStatus.NEGATIVE),
        ("-0.9999999999999", HorizonStatus.INCONCLUSIVE),
    ],
)
def test_effect_floor_boundaries_end_to_end(value, expected):
    result = _study(40, above=_constant(value), below=_constant("0"))
    for item in result.primary.horizons:
        assert item.status == expected
        assert item.cell.inference.estimate.as_fraction() == Fraction(Decimal(value))


def test_mixed_and_not_supported_labels_end_to_end():
    mixed = _study(40, above=_by_horizon({H30: "3", H2: "-3", HC: "0"}), below=_constant("0"))
    assert [item.status for item in mixed.primary.horizons] == [
        HorizonStatus.POSITIVE,
        HorizonStatus.NEGATIVE,
        HorizonStatus.INCONCLUSIVE,
    ]
    assert mixed.primary.study_label == StudyLabel.MIXED

    not_supported = _study(40, above=_constant("-3"), below=_constant("0"))
    assert not_supported.primary.study_label == StudyLabel.NOT_SUPPORTED


# --- Sample gates ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sessions", "expected"), [(99, SampleStatus.INSUFFICIENT_SAMPLE), (100, SampleStatus.OK)]
)
def test_confirmation_overall_gate_99_vs_100(sessions, expected):
    result = _study(sessions, StudySample.CONFIRMATION, above=_constant("3"), below=_constant("0"))
    assert result.overall_gate.status == expected
    if expected == SampleStatus.OK:
        assert result.primary.study_label == StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH
    else:
        # Cell statistics are still reported; the study label is insufficient.
        assert all(item.cell.status == SampleStatus.OK for item in result.primary.horizons)
        assert result.primary.study_label == StudyLabel.INSUFFICIENT_SAMPLE
        assert result.secondary.secondary_label == SecondaryLabel.INSUFFICIENT_SAMPLE


@pytest.mark.parametrize(
    ("contributing", "expected"), [(79, SampleStatus.INSUFFICIENT_SAMPLE), (80, SampleStatus.OK)]
)
def test_confirmation_primary_cell_gate_79_vs_80(contributing, expected):
    def above(i, side, h):
        return None if (h == H2 and i >= contributing) else "3"

    result = _study(100, StudySample.CONFIRMATION, above=above, below=_constant("0"))
    cell = _primary(result, H2).cell
    assert (cell.session_count, cell.status) == (contributing, expected)
    if expected == SampleStatus.OK:
        assert result.primary.study_label == StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH
    else:
        # The Holm family is incomplete: no adjusted p-value, no status.
        assert all(item.p_holm is None for item in result.primary.horizons)
        assert all(
            item.status == HorizonStatus.INSUFFICIENT_SAMPLE for item in result.primary.horizons
        )
        assert _primary(result, H30).cell.inference is not None  # still reported
        assert result.primary.study_label == StudyLabel.INSUFFICIENT_SAMPLE


@pytest.mark.parametrize(
    ("sessions", "expected"), [(39, SampleStatus.INSUFFICIENT_SAMPLE), (40, SampleStatus.OK)]
)
def test_holdout_overall_and_cell_gates_39_vs_40(sessions, expected):
    result = _study(sessions, above=_constant("3"), below=_constant("0"))
    assert result.overall_gate.status == expected
    assert result.overall_gate.required_complete_sessions == 40
    for item in result.primary.horizons:
        assert (item.cell.session_count, item.cell.status) == (sessions, expected)
        assert item.cell.required_sessions == 40
    expected_label = (
        StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH
        if expected == SampleStatus.OK
        else StudyLabel.INSUFFICIENT_SAMPLE
    )
    assert result.primary.study_label == expected_label


def test_secondary_cells_use_the_sample_primary_gate(supported):
    _, confirmation = supported
    holdout = _study(40, above=_constant("3"), below=_constant("0"))
    for result, gate in ((confirmation, 80), (holdout, 40)):
        for item in result.secondary_outcomes:
            assert item.touch_rate.required_sessions == gate
            assert item.signed_return_session_quantiles.required_sessions == gate
        for item in result.secondary.below_horizons:
            assert item.cell.required_sessions == gate
        assert result.secondary.close_minus_30m_contrast.cell.required_sessions == gate


# --- Paired contrast ------------------------------------------------------------------------


def _paired_below(total: int, paired: int, *, close="-4", thirty="4"):
    """Below-VWAP 30m available in sessions [0, paired] ... arranged so exactly
    ``paired`` sessions have both horizons and every below cell still has
    ``total - (total - paired) // 2`` or more sessions."""
    missing = total - paired
    no_close = set(range(missing // 2))
    no_thirty = set(range(missing // 2, missing))

    def below(i, side, h):
        if h == HC:
            return None if i in no_close else close
        if h == H30:
            return None if i in no_thirty else thirty
        return "0"

    return below


@pytest.mark.parametrize(
    ("sample", "total", "paired", "expected"),
    [
        (StudySample.CONFIRMATION, 100, 79, ContrastStatus.INSUFFICIENT_SAMPLE),
        (StudySample.CONFIRMATION, 100, 80, ContrastStatus.MATERIALLY_DIFFERENT),
        (StudySample.HOLDOUT, 48, 39, ContrastStatus.INSUFFICIENT_SAMPLE),
        (StudySample.HOLDOUT, 48, 40, ContrastStatus.MATERIALLY_DIFFERENT),
    ],
)
def test_paired_contrast_gate_and_intersection(sample, total, paired, expected):
    result = _study(total, sample, above=_constant("3"), below=_paired_below(total, paired))
    contrast = result.secondary.close_minus_30m_contrast
    assert contrast.cell.paired_session_count == paired
    assert contrast.status == expected
    if expected != ContrastStatus.INSUFFICIENT_SAMPLE:
        # Only sessions with both horizons contribute: every difference is -8.
        assert contrast.cell.inference.estimate.as_fraction() == -8
        assert contrast.cell.inference.p_raw is not None


def test_paired_contrast_uses_session_means_differences():
    def below(i, side, h):
        return {H30: str(i % 3), H2: "0", HC: str(i % 3 + 5)}[h]

    result = _study(40, above=_constant("3"), below=below)
    contrast = result.secondary.close_minus_30m_contrast
    assert contrast.cell.inference.estimate.as_fraction() == 5
    assert contrast.status == ContrastStatus.MATERIALLY_DIFFERENT


def test_insufficient_paired_contrast_cannot_trigger_horizon_dependence():
    # All three below-VWAP horizons are positive and each below cell passes,
    # but only 39 sessions have both 30m and close: the contrast is
    # insufficient and must not make the label horizon-dependent.
    total, paired = 48, 39
    base = _paired_below(total, paired, close="3", thirty="9")

    def below(i, side, h):
        value = base(i, side, h)
        return "3" if (h == H2) else value

    result = _study(total, above=_constant("3"), below=below)
    assert [item.status for item in result.secondary.below_horizons] == [HorizonStatus.POSITIVE] * 3
    assert result.secondary.close_minus_30m_contrast.status == ContrastStatus.INSUFFICIENT_SAMPLE
    assert result.secondary.secondary_label == SecondaryLabel.BELOW_CONSISTENT_REVERSION


def test_secondary_labels_end_to_end():
    dependent = _study(40, above=_constant("3"), below=_by_horizon({H30: "3", H2: "0", HC: "-3"}))
    assert [item.status for item in dependent.secondary.below_horizons] == [
        HorizonStatus.POSITIVE,
        HorizonStatus.INCONCLUSIVE,
        HorizonStatus.NEGATIVE,
    ]
    assert dependent.secondary.secondary_label == SecondaryLabel.BELOW_HORIZON_DEPENDENT

    via_contrast = _study(40, above=_constant("3"), below=_by_horizon({H30: "2", H2: "5", HC: "9"}))
    assert [item.status for item in via_contrast.secondary.below_horizons] == [
        HorizonStatus.POSITIVE
    ] * 3
    assert (
        via_contrast.secondary.close_minus_30m_contrast.status
        == ContrastStatus.MATERIALLY_DIFFERENT
    )
    assert via_contrast.secondary.secondary_label == SecondaryLabel.BELOW_HORIZON_DEPENDENT

    consistent = _study(40, above=_constant("3"), below=_constant("4"))
    assert consistent.secondary.secondary_label == SecondaryLabel.BELOW_CONSISTENT_REVERSION

    none = _study(40, above=_constant("3"), below=_constant("0"))
    assert none.secondary.secondary_label == SecondaryLabel.BELOW_NO_REVERSION


def test_asymmetry_is_a_descriptive_paired_interval_without_p_value():
    result = _study(40, above=_constant("5"), below=_constant("2"))
    for item in result.asymmetry:
        assert item.cell.inference.estimate.as_fraction() == 3
        assert item.cell.inference.p_raw is None


# --- Secondary outcomes ------------------------------------------------------------------


def test_nearest_rank_session_quantiles():
    result = _study(40, above=lambda i, s, h: str(i), below=_constant("0"))
    quantiles = next(
        item.signed_return_session_quantiles
        for item in result.secondary_outcomes
        if (item.extension_side, item.horizon) == (ABOVE, H30)
    )
    # Session means are 0..39; nearest-rank index ceil(p/100 * 40) - 1.
    assert [
        q.as_fraction()
        for q in (quantiles.p10, quantiles.p25, quantiles.p50, quantiles.p75, quantiles.p90)
    ] == [3, 9, 19, 29, 35]


def test_touch_rate_mfe_and_mae_are_session_level_means():
    result = _study(40, above=_constant("3"), below=_constant("0"))
    item = next(
        o for o in result.secondary_outcomes if (o.extension_side, o.horizon) == (ABOVE, HC)
    )
    # Sessions alternate touched / not touched, two points per session.
    assert item.touch_rate.inference.estimate.as_fraction() == Fraction(1, 2)
    assert item.max_favorable_excursion_bps.inference.estimate.as_fraction() == 5
    assert item.max_adverse_excursion_bps.inference.estimate.as_fraction() == 3


@pytest.mark.parametrize(
    ("close", "vwap", "side", "included"),
    [
        ("500.10", "500.00", ABOVE, True),  # exactly $0.10
        ("500.099999", "500.00", ABOVE, False),  # just below
        ("499.90", "500.00", BELOW, True),  # absolute value
        ("499.900001", "500.00", BELOW, False),
        # 34 significant digits: Decimal context subtraction would round this
        # up to exactly 0.10; the exact comparison keeps it below the floor.
        ("500.0999999999999999999999999999999", "500", ABOVE, False),
    ],
)
def test_pct_retraced_floor(close, vwap, side, included):
    days = weekdays(HOLDOUT_START, 40)
    points = []
    for day in days:
        points.extend(
            session(
                day,
                {
                    5: {
                        "side": side,
                        "signed": "1",
                        "signal_close": close,
                        "signal_vwap": vwap,
                        "pct": "25",
                    },
                    6: {
                        "side": side,
                        "signed": "1",
                        "signal_close": "501",
                        "signal_vwap": "500",
                        "pct": "75",
                    },
                },
            )
        )
    result = _analyze(points)
    item = next(
        o for o in result.secondary_outcomes if (o.extension_side, o.horizon) == (side, H30)
    )
    counts = item.pct_floor_counts
    assert counts.available_observations == 80
    assert counts.below_floor_observations == (0 if included else 40)
    assert counts.at_or_above_floor_observations == (80 if included else 40)
    assert (
        item.pct_extension_retraced_floored.observation_count
        == counts.at_or_above_floor_observations
    )
    expected_mean = Fraction(50) if included else Fraction(75)
    assert item.pct_extension_retraced_floored.inference.estimate.as_fraction() == expected_mean


# --- Subgroups ------------------------------------------------------------------------------


def test_subgroup_session_and_observation_gates_and_fixed_buckets():
    days = weekdays(HOLDOUT_START, 45)
    points = []
    for i, day in enumerate(days):
        spec = {
            # Regime session gate: trend in 39 sessions, reversion in 40, two
            # points per session each (78 / 80 observations, both >= 50).
            3: {
                "side": ABOVE,
                "signed": "2",
                "regime": Regime.TREND_CONTINUATION if i < 39 else Regime.RANGE,
            },
            13: {
                "side": ABOVE,
                "signed": "2",
                "regime": Regime.TREND_CONTINUATION if i < 39 else Regime.RANGE,
            },
            4: {
                "side": ABOVE,
                "signed": "2",
                "regime": Regime.VWAP_MEAN_REVERSION if i < 40 else Regime.RANGE,
            },
            14: {
                "side": ABOVE,
                "signed": "2",
                "regime": Regime.VWAP_MEAN_REVERSION if i < 40 else Regime.RANGE,
            },
            # Observation gate: 40 sessions each; open has 49 points, afternoon 50.
            7: {
                "side": ABOVE,
                "signed": "2",
                "time_of_day": TimeOfDayBucket.OPEN if i < 40 else TimeOfDayBucket.MIDDAY,
            },
            8: {
                "side": ABOVE,
                "signed": "2",
                "time_of_day": TimeOfDayBucket.AFTERNOON if i < 40 else TimeOfDayBucket.MIDDAY,
            },
            # Buckets exactly at 1.0 and 1.5.
            20: {"side": ABOVE, "signed": "2", "normalized_extension": "1.0"},
            21: {"side": ABOVE, "signed": "2", "normalized_extension": "-1.5"},
        }
        if i < 9:
            spec[9] = {"side": ABOVE, "signed": "2", "time_of_day": TimeOfDayBucket.OPEN}
        if i < 10:
            spec[10] = {"side": ABOVE, "signed": "2", "time_of_day": TimeOfDayBucket.AFTERNOON}
        points.extend(session(day, spec))
    result = _analyze(points)

    def cell(dimension, group):
        return _subgroup(result, dimension, group, ABOVE, H30).cell

    trend = cell(SubgroupDimension.REGIME, Regime.TREND_CONTINUATION.value)
    reversion = cell(SubgroupDimension.REGIME, Regime.VWAP_MEAN_REVERSION.value)
    assert (trend.session_count, trend.status) == (39, SampleStatus.INSUFFICIENT_SAMPLE)
    assert trend.inference is None
    assert (reversion.session_count, reversion.status) == (40, SampleStatus.OK)

    open_cell = cell(SubgroupDimension.TIME_OF_DAY, TimeOfDayBucket.OPEN.value)
    afternoon = cell(SubgroupDimension.TIME_OF_DAY, TimeOfDayBucket.AFTERNOON.value)
    assert (open_cell.session_count, open_cell.observation_count, open_cell.status) == (
        40,
        49,
        SampleStatus.INSUFFICIENT_SAMPLE,
    )
    assert (afternoon.session_count, afternoon.observation_count, afternoon.status) == (
        40,
        50,
        SampleStatus.OK,
    )

    within = cell(SubgroupDimension.EXTENSION_BUCKET, ExtensionBucket.WITHIN_RANGE_BAND.value)
    at_or_above = cell(
        SubgroupDimension.EXTENSION_BUCKET, ExtensionBucket.AT_OR_ABOVE_REVERSION_THRESHOLD.value
    )
    intermediate = cell(SubgroupDimension.EXTENSION_BUCKET, ExtensionBucket.INTERMEDIATE.value)
    assert within.observation_count == 45  # exactly 1.0 is inside the range band
    assert intermediate.observation_count == 0
    # |-1.5| is at the reversion threshold; the fixture's other points use 2.
    assert (
        at_or_above.observation_count == sum(1 for dp in points if dp.extension_side == ABOVE) - 45
    )
    for item in result.subgroups:
        assert item.cell.inference is None or item.cell.inference.p_raw is None


def test_unfiltered_primary_result_is_independent_of_subgroups():
    base = study_points(weekdays(HOLDOUT_START, 40), above=_constant("3"), below=_constant("0"))
    relabeled = study_points(
        weekdays(HOLDOUT_START, 40),
        above=_constant("3"),
        below=_constant("0"),
        regime_cycle=(Regime.TREND_CONTINUATION, Regime.INDETERMINATE),
        extension_cycle=(None, "0.3"),
    )
    first, second = _analyze(base), _analyze(relabeled)
    assert first.primary.model_dump() == second.primary.model_dump()


# --- Complete-session derivation ----------------------------------------------------------


def test_complete_session_rule():
    day = HOLDOUT_START
    assert is_complete_session(session(day, {}))
    assert not is_complete_session(session(day, {}, n_points=77))
    assert not is_complete_session(session(day, {}, complete=False))
    shifted = session(day, {}, n_points=79)[1:]  # 78 points, indices 1..78
    assert not is_complete_session(shifted)


def test_incomplete_sessions_are_counted_and_excluded():
    days = weekdays(HOLDOUT_START, 42)
    points = study_points(days[:40], above=_constant("3"), below=_constant("0"))
    points += session(days[40], {5: {"side": ABOVE, "signed": "-50"}}, complete=False)
    points += session(days[41], {5: {"side": ABOVE, "signed": "-50"}}, n_points=77)
    result = _analyze(points)
    assert (result.counts.unique_session_count, result.counts.complete_session_count) == (42, 40)
    assert result.counts.incomplete_session_count == 2
    assert _primary(result, H30).cell.inference.estimate.as_fraction() == 3


# --- Refusals ----------------------------------------------------------------------------


def _refusal(points, sample=StudySample.HOLDOUT, **overrides):
    record = make_record(points)
    kwargs = analysis_kwargs(record, sample)
    kwargs.update(overrides)
    with pytest.raises(ConfirmationAnalysisError) as info:
        analyze_confirmation(record, **kwargs)
    return info.value


def _one_session(day):
    return session(day, {5: {"side": ABOVE, "signed": "1"}})


@pytest.mark.parametrize(
    ("days", "sample"),
    [
        ([date(2026, 3, 2)], StudySample.HOLDOUT),  # confirmation date, holdout sample
        ([date(2026, 9, 23)], StudySample.CONFIRMATION),  # holdout date, confirmation sample
        ([date(2026, 3, 2), date(2026, 9, 23)], StudySample.CONFIRMATION),  # mixed
        ([date(2026, 8, 17)], StudySample.CONFIRMATION),  # discovery date
        ([date(2026, 9, 22)], StudySample.HOLDOUT),  # discovery date
        ([date(2026, 2, 20)], StudySample.CONFIRMATION),  # prior-context day, not in the window
        ([date(2026, 12, 7)], StudySample.HOLDOUT),  # after the holdout window
    ],
)
def test_out_of_window_dates_are_refused(days, sample):
    points = [dp for day in days for dp in _one_session(day)]
    error = _refusal(points, sample)
    assert error.reason == AnalysisRefusal.WINDOW_VIOLATION
    assert str(error) == "window_violation"


def test_nondefault_thresholds_are_refused():
    points = _one_session(HOLDOUT_START)
    tuned = default_regime_thresholds().model_copy(
        update={"extension_threshold_for_reversion": Decimal("1.4")}
    )
    record = make_record(points, regime_thresholds=tuned)
    with pytest.raises(ConfirmationAnalysisError) as info:
        analyze_confirmation(record, **analysis_kwargs(record, StudySample.HOLDOUT))
    assert info.value.reason == AnalysisRefusal.NONDEFAULT_REGIME_THRESHOLDS

    lowered = SampleThresholdsSnapshot(min_observations_for_summary=10, min_sessions_for_summary=5)
    record = make_record(points, sample_thresholds=lowered)
    with pytest.raises(ConfirmationAnalysisError) as info:
        analyze_confirmation(record, **analysis_kwargs(record, StudySample.HOLDOUT))
    assert info.value.reason == AnalysisRefusal.NONDEFAULT_SAMPLE_THRESHOLDS


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_commit_sha": CODE_COMMIT.upper()},
        {"code_commit_sha": CODE_COMMIT[:-1]},
        {"code_commit_sha": CODE_COMMIT + "0"},
        {"code_commit_sha": "z" * 40},
        {"input_sha256": "A" * 64},
        {"input_sha256": "a" * 63},
        {"evaluation_record_sha256": "not-a-hash"},
    ],
)
def test_malformed_provenance_is_refused(overrides):
    error = _refusal(_one_session(HOLDOUT_START), **overrides)
    assert error.reason == AnalysisRefusal.PROVENANCE_INVALID


def test_record_hash_mismatch_is_refused():
    error = _refusal(_one_session(HOLDOUT_START), evaluation_record_sha256="0" * 64)
    assert error.reason == AnalysisRefusal.RECORD_HASH_MISMATCH


def _unavailable_signed_outcome(horizon):
    return HorizonOutcome(
        horizon=horizon,
        available=True,
        horizon_timestamp=GENERATED_AT,
        price_at_horizon=Decimal("500"),
        touched_vwap=False,
        signed_return_toward_vwap_bps=None,
        pct_extension_retraced=Decimal("1"),
        max_favorable_excursion_bps=Decimal("1"),
        max_adverse_excursion_bps=Decimal("1"),
    )


def test_inconsistent_records_are_refused():
    day = HOLDOUT_START
    base = _one_session(day)

    duplicate = base + [base[0]]
    assert _refusal(duplicate).reason == AnalysisRefusal.INCONSISTENT_RECORD

    record = make_record(base, unique_session_count=2)
    with pytest.raises(ConfirmationAnalysisError) as info:
        analyze_confirmation(record, **analysis_kwargs(record, StudySample.HOLDOUT))
    assert info.value.reason == AnalysisRefusal.INCONSISTENT_RECORD

    no_vwap = list(base)
    no_vwap[5] = no_vwap[5].model_copy(update={"signal_vwap": None})
    assert _refusal(no_vwap).reason == AnalysisRefusal.INCONSISTENT_RECORD

    missing_metric = list(base)
    outcomes = [
        _unavailable_signed_outcome(o.horizon) if o.horizon == H30 else o
        for o in missing_metric[5].outcomes
    ]
    missing_metric[5] = missing_metric[5].model_copy(update={"outcomes": outcomes})
    assert _refusal(missing_metric).reason == AnalysisRefusal.INCONSISTENT_RECORD


def test_point_builder_sanity():
    # Guards the fixture itself: a filler point is ineligible with no outcome.
    filler = point(HOLDOUT_START, 0)
    assert filler.extension_side is None
    assert not any(o.available for o in filler.outcomes)
    eligible = point(HOLDOUT_START, 1, side=ABOVE, signed="1")
    assert (
        next(o for o in eligible.outcomes if o.horizon == ForwardHorizon.NEXT_SESSION).available
        is False
    )
    assert HOLDOUT_START + timedelta(days=0) == date(2026, 9, 23)


# --- Secondary label under C1.3 (end to end) ---------------------------------------------


def test_insufficient_paired_contrast_with_differing_statuses_is_horizon_dependent():
    # 39 of 48 sessions carry both below-VWAP 30m and close: the contrast is
    # insufficient, but the per-horizon statuses differ (30m positive, 2h
    # inconclusive, close negative), which alone triggers rule 2.
    total, paired = 48, 39
    base = _paired_below(total, paired, close="-3", thirty="3")

    def below(i, side, h):
        return "0" if h == H2 else base(i, side, h)

    result = _study(total, above=_constant("3"), below=below)
    contrast = result.secondary.close_minus_30m_contrast
    assert contrast.status == ContrastStatus.INSUFFICIENT_SAMPLE
    assert contrast.cell.inference is None
    assert all(item.cell.status == SampleStatus.OK for item in result.secondary.below_horizons)
    assert [item.status for item in result.secondary.below_horizons] == [
        HorizonStatus.POSITIVE,
        HorizonStatus.INCONCLUSIVE,
        HorizonStatus.NEGATIVE,
    ]
    assert result.secondary.secondary_label == SecondaryLabel.BELOW_HORIZON_DEPENDENT


def test_below_horizon_cell_under_its_gate_makes_the_secondary_label_insufficient():
    # Holdout gate is 40: below-VWAP 2h is available in only 39 of 40 sessions.
    def below(i, side, h):
        return None if (h == H2 and i >= 39) else "3"

    result = _study(40, above=_constant("3"), below=below)
    assert result.overall_gate.status == SampleStatus.OK
    cell = _below(result, H2).cell
    assert (cell.session_count, cell.status) == (39, SampleStatus.INSUFFICIENT_SAMPLE)
    assert _below(result, H2).status == HorizonStatus.INSUFFICIENT_SAMPLE
    assert result.secondary.secondary_label == SecondaryLabel.INSUFFICIENT_SAMPLE
    # The primary study is unaffected by a secondary cell.
    assert result.primary.study_label == StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH


def test_overall_gate_failure_makes_the_secondary_label_insufficient():
    result = _study(39, above=_constant("3"), below=_constant("3"))
    assert result.overall_gate.status == SampleStatus.INSUFFICIENT_SAMPLE
    assert result.secondary.secondary_label == SecondaryLabel.INSUFFICIENT_SAMPLE


# --- Display rounding can never change a decision ---------------------------------------


def test_display_rounding_cannot_change_a_status():
    # 1 - 1e-40 bps displays as 1.000... at 30 places, but the exact value is
    # below the +1.0 floor, so every primary horizon stays inconclusive.
    value = "0." + "9" * 40
    result = _study(40, above=_constant(value), below=_constant("0"))
    for item in result.primary.horizons:
        estimate = item.cell.inference.estimate
        assert estimate.decimal_30dp == Decimal(1)
        assert estimate.as_fraction() < 1
        assert item.status == HorizonStatus.INCONCLUSIVE
    assert result.primary.study_label == StudyLabel.NOT_SUPPORTED


# --- Recomputation verifier: the honest integrity boundary ------------------------------


def _verify(record, result, **overrides):
    kwargs = {
        "expected_input_sha256": INPUT_SHA,
        "expected_evaluation_record_sha256": canonical_record_sha256(record),
    }
    kwargs.update(overrides)
    verify_confirmation_result(record, result, **kwargs)


def _coherent_edit(result) -> dict:
    """Change a primary estimate to a different value that keeps every
    status and label unchanged: internally consistent, so schema-valid."""
    payload = result.model_dump(mode="json")
    payload["primary"]["horizons"][0]["cell"]["inference"]["estimate"] = (
        ExactRational.from_fraction(Fraction(42)).model_dump(mode="json")
    )
    return payload


def test_verifier_accepts_an_untouched_deterministic_result(supported):
    record, result = supported
    _verify(record, result)
    # Plain JSON payloads are accepted too (revalidated through the contracts).
    verify_confirmation_result(
        record.model_dump(mode="json"),
        result.model_dump(mode="json"),
        expected_input_sha256=INPUT_SHA,
        expected_evaluation_record_sha256=canonical_record_sha256(record),
    )


def test_a_coherent_edit_stays_schema_valid_but_fails_verification(supported):
    record, result = supported
    edited = _coherent_edit(result)
    # The honest boundary: the contract cannot detect a coherent edit ...
    reparsed = SpyVwapConfirmationResult.model_validate(edited)
    assert reparsed.primary.horizons[0].cell.inference.estimate.as_fraction() == 42
    assert reparsed.primary.study_label == result.primary.study_label
    # ... but deterministic recomputation from the referenced record does.
    with pytest.raises(ConfirmationVerificationError):
        _verify(record, edited)


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_input_sha256": "cd" * 32},
        {"expected_evaluation_record_sha256": "ef" * 32},
        {"expected_input_sha256": "not-a-hash"},
        {"expected_evaluation_record_sha256": None},
    ],
)
def test_wrong_expected_hashes_fail_with_one_fixed_sanitized_error(supported, overrides):
    record, result = supported
    with pytest.raises(ConfirmationVerificationError) as info:
        _verify(record, result, **overrides)
    error = info.value
    assert str(error) == ConfirmationVerificationError.MESSAGE
    assert error.args == (ConfirmationVerificationError.MESSAGE,)
    assert error.__cause__ is None and error.__context__ is None
    leaked = (
        INPUT_SHA,
        canonical_record_sha256(record),
        result.provenance.code_commit_sha,
        "cd" * 32,
        "ef" * 32,
        "not-a-hash",
        "2026",
    )
    for value in leaked:
        assert value not in str(error) and value not in repr(error)


def test_invalid_inputs_fail_with_the_same_fixed_error(supported):
    record, result = supported
    broken = result.model_dump(mode="json")
    broken["primary"]["study_label"] = "not_a_label"
    for bad_record, bad_result in ((record, broken), ({"symbol": "SPY"}, result)):
        with pytest.raises(ConfirmationVerificationError) as info:
            verify_confirmation_result(
                bad_record,
                bad_result,
                expected_input_sha256=INPUT_SHA,
                expected_evaluation_record_sha256=canonical_record_sha256(record),
            )
        assert str(info.value) == ConfirmationVerificationError.MESSAGE


def test_verification_is_order_independent_but_bound_to_the_exact_record_bytes(supported):
    record, result = supported
    shuffled_points = list(record.decision_points)
    random.Random(11).shuffle(shuffled_points)
    shuffled = make_record(shuffled_points)
    shuffled_result = analyze_confirmation(shuffled, **analysis_kwargs(shuffled))
    _verify(shuffled, shuffled_result)
    # Same statistics, different canonical bytes: the original result claims
    # the original record's hash, so it does not verify against the shuffle.
    with pytest.raises(ConfirmationVerificationError):
        _verify(shuffled, result, expected_evaluation_record_sha256=canonical_record_sha256(record))


def test_result_json_is_byte_identical_across_processes():
    script = (
        "from market_intelligence.tests.spy_vwap_confirmation_fixtures import "
        "reference_result_sha256\n"
        "print(reference_result_sha256())\n"
    )
    local_hash = reference_result_sha256()
    for seed in ("0", "12345"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        completed = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, timeout=300, env=env
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout.strip() == local_hash
