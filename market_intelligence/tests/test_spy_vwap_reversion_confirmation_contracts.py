"""Contract and decision-rule tests for the SPY VWAP confirmation result.

Synthetic values only. Covers the exact-rational representation, the pure
decision rules (p-value, Holm, statuses, labels, buckets, nearest rank) at
and immediately around every preregistered boundary, cell gates, the fixed
configuration/provenance, and rejection of malformed or internally
inconsistent edits to a full result. (Coherent edits are a recomputation
concern -- see the verifier tests in test_spy_vwap_reversion_confirmation.py.)
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from fractions import Fraction

import pytest
from pydantic import ValidationError

from market_intelligence.evaluation.spy_vwap_reversion_confirmation import analyze_confirmation
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    BASE_PREREGISTRATION_COMMIT_SHA,
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    CLARIFICATION_COMMIT_SHA,
    CONFIRMATION_SCHEMA_VERSION,
    FIXED_NOTES,
    INTERVAL_LOWER_INDEX,
    INTERVAL_UPPER_INDEX,
    SAMPLE_DESIGNS,
    BootstrapInference,
    ConfigurationSnapshot,
    ContrastStatus,
    ExactRational,
    ExtensionBucket,
    GatedCell,
    HorizonStatus,
    PairedCell,
    Provenance,
    SecondaryLabel,
    SpyVwapConfirmationResult,
    StudyLabel,
    StudySample,
    below_horizon_status,
    contrast_status,
    derive_secondary_label,
    derive_study_label,
    display_decimal,
    expected_configuration,
    expected_subgroup_keys,
    extension_bucket,
    holm_adjust,
    nearest_rank_index,
    primary_horizon_status,
    raw_p_value,
)
from market_intelligence.evaluation.spy_vwap_reversion_contracts import SampleStatus
from market_intelligence.tests.spy_vwap_confirmation_fixtures import (
    CONFIRMATION_START,
    analysis_kwargs,
    make_record,
    patterned_value,
    study_points,
    weekdays,
)

EPS = Fraction(1, 10**13)  # beyond the 12-decimal-place requirement
ONE = Fraction(1)
ALPHA = Fraction(1, 20)
P = HorizonStatus.POSITIVE
N = HorizonStatus.NEGATIVE
I = HorizonStatus.INCONCLUSIVE  # noqa: E741
X = HorizonStatus.INSUFFICIENT_SAMPLE


def _r(value: Fraction | int | str) -> ExactRational:
    return ExactRational.from_fraction(Fraction(value))


# --- Frozen constants ---------------------------------------------------------------


def test_frozen_preregistration_constants():
    assert BASE_PREREGISTRATION_COMMIT_SHA == "f77d30f8e6e90a6b77eeca11fd11c3da9c9540c1"
    assert CLARIFICATION_COMMIT_SHA == "1ff654de6dbbd54aeecb471010d1a612ca5abda6"
    assert CONFIRMATION_SCHEMA_VERSION == "spy-vwap-reversion-confirmation-1"
    assert BOOTSTRAP_SEED == 20260923
    assert BOOTSTRAP_REPLICATES == 10_000
    assert (INTERVAL_LOWER_INDEX, INTERVAL_UPPER_INDEX) == (249, 9749)


def test_sample_designs_match_the_preregistration_and_c1():
    confirmation = SAMPLE_DESIGNS[StudySample.CONFIRMATION]
    holdout = SAMPLE_DESIGNS[StudySample.HOLDOUT]
    assert (confirmation.window_start.isoformat(), confirmation.window_end.isoformat()) == (
        "2026-02-23",
        "2026-08-14",
    )
    assert (holdout.window_start.isoformat(), holdout.window_end.isoformat()) == (
        "2026-09-23",
        "2026-12-04",
    )
    assert (
        confirmation.min_complete_sessions,
        confirmation.min_primary_cell_sessions,
        confirmation.min_paired_sessions,
        confirmation.min_secondary_cell_sessions,
    ) == (100, 80, 80, 80)
    assert (
        holdout.min_complete_sessions,
        holdout.min_primary_cell_sessions,
        holdout.min_paired_sessions,
        holdout.min_secondary_cell_sessions,
    ) == (40, 40, 40, 40)


# --- Exact rationals -------------------------------------------------------------------


def test_exact_rational_round_trips_and_reduces():
    value = ExactRational.from_fraction(Fraction(6, 4))
    assert (value.numerator, value.denominator) == (3, 2)
    assert value.as_fraction() == Fraction(3, 2)
    assert ExactRational.model_validate(value.model_dump(mode="json")) == value


def test_display_decimal_keeps_thirty_places_and_rounds_half_even():
    assert display_decimal(Fraction(1, 3)) == Decimal("0." + "3" * 30)
    assert str(display_decimal(Fraction(2, 3))).endswith("67")
    # Exactly half a unit in the 30th place rounds to even.
    half = Fraction(1, 2 * 10**30)
    assert display_decimal(half) == Decimal(0)
    assert display_decimal(3 * half) == Decimal("2E-30")


@pytest.mark.parametrize(
    "payload",
    [
        {"numerator": 2, "denominator": 4, "decimal_30dp": "0.5"},  # not reduced
        {"numerator": 1, "denominator": 0, "decimal_30dp": "0"},  # zero denominator
        {"numerator": 1, "denominator": 3, "decimal_30dp": "0.333"},  # display mismatch
        {"numerator": 1, "denominator": 2, "decimal_30dp": "NaN"},  # non-finite
        {"numerator": True, "denominator": 1, "decimal_30dp": "1"},  # bool is not int
        {"numerator": int("9" * 401), "denominator": 1, "decimal_30dp": "0"},  # digit bound
        {"numerator": 1, "denominator": 1, "decimal_30dp": "1", "extra": 1},  # extra field
    ],
)
def test_exact_rational_rejects_invalid_payloads(payload):
    with pytest.raises(ValidationError):
        ExactRational.model_validate(payload)


# --- p-value and Holm -------------------------------------------------------------------


def test_raw_p_value_all_positive_replicates():
    assert raw_p_value(0, BOOTSTRAP_REPLICATES) == Fraction(2, 10001)


def test_raw_p_value_all_zero_replicates_is_one():
    assert raw_p_value(BOOTSTRAP_REPLICATES, BOOTSTRAP_REPLICATES) == 1


def test_raw_p_value_mixed_tails_uses_the_smaller_tail():
    assert raw_p_value(100, 9900) == Fraction(2 * 101, 10001)
    assert raw_p_value(9900, 100) == Fraction(2 * 101, 10001)


def test_raw_p_value_is_capped_at_one():
    assert raw_p_value(5000, 5000) == 1  # 2 * 5001 / 10001 > 1
    assert raw_p_value(6000, 4000) == Fraction(8002, 10001)


def test_holm_orders_by_raw_p_and_is_monotone():
    p = (Fraction(1, 100), Fraction(2, 100), Fraction(3, 100))
    assert holm_adjust(p) == (Fraction(3, 100), Fraction(4, 100), Fraction(4, 100))
    # Order in the input does not matter beyond the fixed tie order.
    assert holm_adjust((p[2], p[0], p[1])) == (Fraction(4, 100), Fraction(3, 100), Fraction(4, 100))


def test_holm_caps_at_one():
    assert holm_adjust((Fraction(1, 2), Fraction(9, 10), Fraction(1, 5))) == (
        ONE,
        ONE,
        Fraction(3, 5),
    )


@pytest.mark.parametrize("perm", list(itertools.permutations(range(3))))
def test_holm_ties_get_identical_adjusted_values_under_any_order(perm):
    base = (Fraction(2, 100), Fraction(2, 100), Fraction(1, 2))
    permuted = tuple(base[i] for i in perm)
    adjusted = holm_adjust(permuted)
    by_value = {}
    for raw, adj in zip(permuted, adjusted, strict=True):
        by_value.setdefault(raw, set()).add(adj)
    assert by_value[Fraction(2, 100)] == {Fraction(6, 100)}
    assert by_value[Fraction(1, 2)] == {Fraction(1, 2)}


def test_holm_adjusted_values_never_decrease_along_the_raw_order():
    p = (Fraction(4, 100), Fraction(1, 100), Fraction(45, 1000))
    adjusted = holm_adjust(p)
    order = sorted(range(3), key=lambda i: (p[i], i))
    assert [adjusted[i] for i in order] == sorted(adjusted[i] for i in order)


# --- Primary status boundaries --------------------------------------------------------


@pytest.mark.parametrize(
    ("estimate", "lower", "upper", "p_holm", "expected"),
    [
        (ONE, EPS, 5, ALPHA - EPS, P),  # exactly +1.0 bps
        (ONE + EPS, EPS, 5, ALPHA - EPS, P),  # just above
        (ONE - EPS, EPS, 5, ALPHA - EPS, I),  # just below +1.0
        (2, Fraction(0), 5, ALPHA - EPS, I),  # lower bound exactly 0
        (2, -EPS, 5, ALPHA - EPS, I),  # lower bound just below 0
        (2, EPS, 5, ALPHA, I),  # adjusted p exactly 0.05
        (2, EPS, 5, ALPHA + EPS, I),  # adjusted p just above
        (-ONE, -5, -EPS, ALPHA - EPS, N),  # exactly -1.0 bps
        (-ONE - EPS, -5, -EPS, ALPHA - EPS, N),
        (-ONE + EPS, -5, -EPS, ALPHA - EPS, I),  # just inside -1.0
        (-2, -5, Fraction(0), ALPHA - EPS, I),  # upper bound exactly 0
        (-2, -5, EPS, ALPHA - EPS, I),
        (-2, -5, -EPS, ALPHA, I),
        (Fraction(0), -1, 1, Fraction(1), I),
    ],
)
def test_primary_status_boundaries(estimate, lower, upper, p_holm, expected):
    result = primary_horizon_status(
        Fraction(estimate), Fraction(lower), Fraction(upper), Fraction(p_holm)
    )
    assert result == expected


# --- Study label ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overall_ok", "statuses", "expected"),
    [
        (False, (P, P, P), StudyLabel.INSUFFICIENT_SAMPLE),
        (True, (P, X, P), StudyLabel.INSUFFICIENT_SAMPLE),
        (True, (P, P, P), StudyLabel.SUPPORTED_FOR_FURTHER_SHADOW_RESEARCH),
        (True, (I, I, I), StudyLabel.NOT_SUPPORTED),
        (True, (N, N, N), StudyLabel.NOT_SUPPORTED),
        (True, (I, N, I), StudyLabel.NOT_SUPPORTED),
        (True, (P, I, I), StudyLabel.MIXED),
        (True, (P, P, I), StudyLabel.MIXED),
        (True, (P, P, N), StudyLabel.MIXED),  # a negative alongside positives is mixed
        (True, (N, P, N), StudyLabel.MIXED),
    ],
)
def test_study_label_first_match(overall_ok, statuses, expected):
    assert derive_study_label(overall_ok, statuses) == expected


# --- Secondary rules ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lower", "upper", "expected"),
    [(EPS, 1, P), (Fraction(0), 1, I), (-1, -EPS, N), (-1, Fraction(0), I), (-1, 1, I)],
)
def test_below_horizon_status_uses_the_interval_only(lower, upper, expected):
    assert below_horizon_status(Fraction(lower), Fraction(upper)) == expected


@pytest.mark.parametrize(
    ("estimate", "lower", "upper", "p_raw", "expected"),
    [
        (ONE, EPS, 3, ALPHA - EPS, ContrastStatus.MATERIALLY_DIFFERENT),
        (-ONE, -3, -EPS, ALPHA - EPS, ContrastStatus.MATERIALLY_DIFFERENT),
        (ONE - EPS, EPS, 3, ALPHA - EPS, ContrastStatus.INCONCLUSIVE),
        (-ONE + EPS, -3, -EPS, ALPHA - EPS, ContrastStatus.INCONCLUSIVE),
        (2, Fraction(0), 3, ALPHA - EPS, ContrastStatus.INCONCLUSIVE),
        (2, EPS, 3, ALPHA, ContrastStatus.INCONCLUSIVE),
    ],
)
def test_contrast_status_requires_all_three_conditions(estimate, lower, upper, p_raw, expected):
    result = contrast_status(Fraction(estimate), Fraction(lower), Fraction(upper), Fraction(p_raw))
    assert result == expected


MD = ContrastStatus.MATERIALLY_DIFFERENT
CI = ContrastStatus.INCONCLUSIVE
CX = ContrastStatus.INSUFFICIENT_SAMPLE


@pytest.mark.parametrize(
    ("overall_ok", "statuses", "contrast", "expected"),
    [
        (False, (P, P, P), CI, SecondaryLabel.INSUFFICIENT_SAMPLE),
        (True, (P, X, P), MD, SecondaryLabel.INSUFFICIENT_SAMPLE),
        (True, (P, I, P), CI, SecondaryLabel.BELOW_HORIZON_DEPENDENT),
        (True, (P, P, P), MD, SecondaryLabel.BELOW_HORIZON_DEPENDENT),
        (True, (P, P, P), CI, SecondaryLabel.BELOW_CONSISTENT_REVERSION),
        (True, (I, I, I), CI, SecondaryLabel.BELOW_NO_REVERSION),
        (True, (N, N, N), CI, SecondaryLabel.BELOW_NO_REVERSION),
        # C1.3: an insufficient contrast can never trigger horizon dependence.
        (True, (I, I, I), CX, SecondaryLabel.BELOW_NO_REVERSION),
        (True, (P, P, P), CX, SecondaryLabel.BELOW_CONSISTENT_REVERSION),
        (True, (P, N, P), CX, SecondaryLabel.BELOW_HORIZON_DEPENDENT),
    ],
)
def test_secondary_label_first_match(overall_ok, statuses, contrast, expected):
    assert derive_secondary_label(overall_ok, statuses, contrast) == expected


# --- Buckets and nearest rank ------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, ExtensionBucket.UNAVAILABLE),
        ("0", ExtensionBucket.WITHIN_RANGE_BAND),
        ("1.0", ExtensionBucket.WITHIN_RANGE_BAND),
        ("-1.0", ExtensionBucket.WITHIN_RANGE_BAND),
        ("1.000001", ExtensionBucket.INTERMEDIATE),
        ("1.499999", ExtensionBucket.INTERMEDIATE),
        ("-1.499999", ExtensionBucket.INTERMEDIATE),
        ("1.5", ExtensionBucket.AT_OR_ABOVE_REVERSION_THRESHOLD),
        ("-1.5", ExtensionBucket.AT_OR_ABOVE_REVERSION_THRESHOLD),
        ("9", ExtensionBucket.AT_OR_ABOVE_REVERSION_THRESHOLD),
        # Beyond the 28-digit Decimal context: exact comparison is required.
        ("1.00000000000000000000000000000000001", ExtensionBucket.INTERMEDIATE),
        ("-1.49999999999999999999999999999999999", ExtensionBucket.INTERMEDIATE),
    ],
)
def test_extension_bucket_boundaries(value, expected):
    assert extension_bucket(None if value is None else Decimal(value)) == expected


def test_nearest_rank_indices():
    assert [nearest_rank_index(p, 10) for p in (10, 25, 50, 75, 90)] == [0, 2, 4, 7, 8]
    assert [nearest_rank_index(p, 80) for p in (10, 25, 50, 75, 90)] == [7, 19, 39, 59, 71]
    assert nearest_rank_index(10, 1) == 0


def test_subgroup_keys_are_exactly_the_one_way_cells():
    keys = expected_subgroup_keys()
    assert len(keys) == 84 == len(set(keys))  # (5 + 5 + 4) groups x 2 sides x 3 horizons


# --- Inference and gated cells -----------------------------------------------------------


def _inference(**overrides):
    payload = dict(
        estimate=_r(2),
        interval_lower=_r(1),
        interval_upper=_r(3),
        replicates_at_or_below_zero=0,
        replicates_at_or_above_zero=BOOTSTRAP_REPLICATES,
        p_raw=_r(Fraction(2, 10001)),
    )
    payload.update(overrides)
    return BootstrapInference(**payload)


def test_bootstrap_inference_accepts_a_consistent_summary():
    assert _inference().p_raw.as_fraction() == Fraction(2, 10001)


@pytest.mark.parametrize(
    "overrides",
    [
        {"interval_lower": _r(4)},  # lower > upper
        {"replicates_at_or_above_zero": 9000},  # tails do not cover every replicate
        {"replicates_at_or_below_zero": 250, "replicates_at_or_above_zero": 9750},  # vs lower>0
        {"p_raw": _r(Fraction(3, 10001))},  # wrong p-value
        {"replicates_at_or_below_zero": 10_001},  # out of bounds
    ],
)
def test_bootstrap_inference_rejects_inconsistent_summaries(overrides):
    with pytest.raises(ValidationError):
        _inference(**overrides)


def test_gated_cell_status_must_match_its_gate():
    ok = GatedCell(
        status=SampleStatus.OK,
        required_sessions=80,
        required_observations=0,
        session_count=80,
        observation_count=80,
        inference=_inference(p_raw=None),
    )
    assert ok.status == SampleStatus.OK
    with pytest.raises(ValidationError):  # 79 < 80 cannot be ok
        GatedCell(
            status=SampleStatus.OK,
            required_sessions=80,
            required_observations=0,
            session_count=79,
            observation_count=79,
            inference=_inference(p_raw=None),
        )
    with pytest.raises(ValidationError):  # insufficient carries no statistic
        GatedCell(
            status=SampleStatus.INSUFFICIENT_SAMPLE,
            required_sessions=80,
            required_observations=0,
            session_count=79,
            observation_count=79,
            inference=_inference(p_raw=None),
        )
    with pytest.raises(ValidationError):  # observations gate (49 < 50)
        GatedCell(
            status=SampleStatus.OK,
            required_sessions=40,
            required_observations=50,
            session_count=40,
            observation_count=49,
            inference=_inference(p_raw=None),
        )


def test_paired_cell_gate():
    with pytest.raises(ValidationError):
        PairedCell(
            status=SampleStatus.OK,
            required_sessions=40,
            paired_session_count=39,
            inference=_inference(),
        )
    cell = PairedCell(
        status=SampleStatus.INSUFFICIENT_SAMPLE, required_sessions=40, paired_session_count=39
    )
    assert cell.inference is None


# --- Configuration and provenance --------------------------------------------------------


def test_configuration_must_equal_the_preregistered_values():
    config = expected_configuration(StudySample.CONFIRMATION, python_version="3.13.14")
    payload = config.model_dump(mode="json")
    assert ConfigurationSnapshot.model_validate(payload).bootstrap_seed == BOOTSTRAP_SEED
    for field, bad in (
        ("bootstrap_seed", 1),
        ("bootstrap_replicates", 9999),
        ("min_primary_cell_sessions", 40),
        ("effect_floor_bps", "0.5"),
        ("window_end", "2026-08-17"),
    ):
        tampered = dict(payload, **{field: bad})
        with pytest.raises(ValidationError):
            ConfigurationSnapshot.model_validate(tampered)


def _provenance(**overrides):
    payload = dict(
        input_sha256="a" * 64,
        evaluation_record_sha256="b" * 64,
        code_commit_sha="c" * 40,
        base_preregistration_commit_sha=BASE_PREREGISTRATION_COMMIT_SHA,
        clarification_commit_sha=CLARIFICATION_COMMIT_SHA,
        evaluation_record_schema_version="spy-vwap-reversion-evaluation-1",
    )
    payload.update(overrides)
    return Provenance(**payload)


@pytest.mark.parametrize(
    "overrides",
    [
        {"code_commit_sha": "C" * 40},
        {"code_commit_sha": "c" * 39},
        {"code_commit_sha": "c" * 41},
        {"code_commit_sha": "g" * 40},
        {"input_sha256": "a" * 63},
        {"evaluation_record_sha256": "B" * 64},
        {"base_preregistration_commit_sha": "0" * 40},
        {"clarification_commit_sha": "0" * 40},
        {"evaluation_record_schema_version": "spy-vwap-reversion-evaluation-2"},
    ],
)
def test_provenance_rejects_malformed_or_unfrozen_values(overrides):
    with pytest.raises(ValidationError):
        _provenance(**overrides)


# --- Full-result contract ---------------------------------------------------------------


@pytest.fixture(scope="module")
def supported_result() -> SpyVwapConfirmationResult:
    days = weekdays(CONFIRMATION_START, 100)
    record = make_record(
        study_points(days, above=patterned_value("5"), below=patterned_value("0.2"))
    )
    return analyze_confirmation(record, **analysis_kwargs(record))


def test_result_field_set_is_fixed_and_has_no_forbidden_fields(supported_result):
    assert set(SpyVwapConfirmationResult.model_fields) == {
        "schema_version",
        "symbol",
        "sample",
        "window_start",
        "window_end",
        "generated_at",
        "provenance",
        "configuration",
        "counts",
        "overall_gate",
        "primary",
        "secondary",
        "secondary_outcomes",
        "asymmetry",
        "subgroups",
        "notes",
    }
    # Every field name at every nesting level (the fixed notes legitimately
    # *mention* options/P&L/recommendations in order to disclaim them).
    keys: set[str] = set()

    def collect(node):
        if isinstance(node, dict):
            for key, value in node.items():
                keys.add(key.lower())
                collect(value)
        elif isinstance(node, list):
            for item in node:
                collect(item)

    collect(supported_result.model_dump(mode="json"))
    for forbidden in (
        "pnl",
        "profit",
        "option",
        "contract",
        "recommend",
        "trade",
        "agent",
        "path",
        "url",
        "metadata",
        "reason",
        "credential",
        "price",
    ):
        assert not any(forbidden in key for key in keys), forbidden
    # No raw bar or decision-point content.
    raw_data_keys = {"bars", "open", "high", "low", "close", "volume", "decision_points"}
    assert not keys & raw_data_keys
    assert supported_result.notes == FIXED_NOTES


def test_result_round_trips_through_json(supported_result):
    payload = supported_result.model_dump(mode="json")
    assert SpyVwapConfirmationResult.model_validate(payload) == supported_result


def _tamper(result, mutate):
    payload = result.model_dump(mode="json")
    mutate(payload)
    return payload


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["primary"].__setitem__("study_label", "not_supported"),
        lambda p: p["primary"]["horizons"][0].__setitem__("status", "inconclusive"),
        lambda p: p["primary"]["horizons"][1].__setitem__(
            "p_holm",
            {
                "numerator": 1,
                "denominator": 10001,
                "decimal_30dp": str(display_decimal(Fraction(1, 10001))),
            },
        ),
        lambda p: p["secondary"].__setitem__("secondary_label", "below_consistent_reversion"),
        lambda p: p.__setitem__("notes", p["notes"][:-1]),
        lambda p: p.__setitem__("window_end", "2026-09-22"),
        lambda p: p.__setitem__("sample", "holdout"),
        lambda p: p["overall_gate"].__setitem__("required_complete_sessions", 40),
        lambda p: p.__setitem__("subgroups", p["subgroups"][:-1]),
        lambda p: p.__setitem__("recommendation", "buy"),
        lambda p: p.__setitem__("schema_version", "spy-vwap-reversion-evaluation-1"),
    ],
)
def test_tampered_results_are_rejected(supported_result, mutate):
    with pytest.raises(ValidationError):
        SpyVwapConfirmationResult.model_validate(_tamper(supported_result, mutate))


# --- Inconsistent rational edits inside a full result -------------------------------------


def _estimate_path(payload):
    return payload["primary"]["horizons"][0]["cell"]["inference"]["estimate"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r.__setitem__("numerator", r["numerator"] + 1),  # numerator only
        lambda r: r.__setitem__("denominator", r["denominator"] + 1),  # denominator only
        lambda r: r.__setitem__("decimal_30dp", "123.4"),  # display only
        lambda r: r.update(  # consistent pair, but no longer the value the status used
            numerator=-5, denominator=1, decimal_30dp=str(display_decimal(Fraction(-5)))
        ),
    ],
)
def test_tampering_any_rational_field_fails_validation(supported_result, mutate):
    payload = supported_result.model_dump(mode="json")
    mutate(_estimate_path(payload))
    with pytest.raises(ValidationError):
        SpyVwapConfirmationResult.model_validate(payload)


# --- Gates cannot drift from the sample design / configuration snapshot -----------------


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["primary"]["horizons"][0]["cell"].__setitem__("required_sessions", 40),
        lambda p: p["secondary"]["below_horizons"][2]["cell"].__setitem__("required_sessions", 40),
        lambda p: p["secondary"]["close_minus_30m_contrast"]["cell"].__setitem__(
            "required_sessions", 40
        ),
        lambda p: p["secondary_outcomes"][0]["touch_rate"].__setitem__("required_sessions", 40),
        lambda p: p["secondary_outcomes"][4]["signed_return_session_quantiles"].__setitem__(
            "required_sessions", 40
        ),
        lambda p: p["asymmetry"][1]["cell"].__setitem__("required_sessions", 40),
        lambda p: p["subgroups"][0]["cell"].__setitem__("required_sessions", 20),
        lambda p: p["subgroups"][0]["cell"].__setitem__("required_observations", 10),
        lambda p: p["configuration"].__setitem__("min_subgroup_sessions", 20),
        lambda p: p["configuration"].__setitem__("min_paired_sessions", 40),
    ],
)
def test_no_gate_can_drift_from_the_sample_design(supported_result, mutate):
    # Each edit keeps the cell's own status arithmetic consistent (100 or 200
    # sessions/observations still clear a lowered requirement); only the
    # cross-check against the sample design / configuration catches it.
    with pytest.raises(ValidationError):
        SpyVwapConfirmationResult.model_validate(_tamper(supported_result, mutate))
