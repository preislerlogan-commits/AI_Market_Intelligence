"""Synthetic fixture builders for the SPY VWAP confirmation-analysis tests.

Everything here is hand-constructed: no real bars, no real evaluation
record, no database, no network. Records are built as fully validated
``SpyVwapReversionEvaluationRecord`` instances directly (not via the
evaluator) so each test controls the exact decision-point values it needs.
Imported by the confirmation test modules; not itself a test module.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from market_intelligence.evaluation.spy_vwap_reversion_confirmation import (
    canonical_record_sha256,
)
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    PRIMARY_HORIZONS,
    StudySample,
    _default_regime_thresholds_snapshot,
)
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    DecisionPointRecord,
    DescriptiveStats,
    EligibilityStatus,
    ExtensionSide,
    ForwardHorizon,
    HorizonDescriptiveBundle,
    HorizonOutcome,
    HorizonSideSummary,
    HorizonSummary,
    RegimeThresholdsSnapshot,
    SampleStatus,
    SampleThresholdsSnapshot,
    SpyVwapReversionEvaluationRecord,
)
from market_intelligence.market_features.spy_regime_contracts import (
    Regime,
    ScenarioHorizon,
    TimeOfDayBucket,
)

EASTERN = ZoneInfo("America/New_York")
GENERATED_AT = datetime(2031, 1, 2, 12, 0, tzinfo=UTC)
CODE_COMMIT = "0123456789abcdef0123456789abcdef01234567"
INPUT_SHA = "ab" * 32
CONFIRMATION_START = date(2026, 2, 23)
HOLDOUT_START = date(2026, 9, 23)

_H30, _H2, _HC = PRIMARY_HORIZONS
_ALL = (_H30, _H2, _HC)

Value = str | int | Decimal
PerHorizon = Value | Mapping[ForwardHorizon, Value] | None


def weekdays(start: date, count: int) -> list[date]:
    """``count`` consecutive weekdays from ``start`` (no holiday calendar --
    synthetic sessions only need distinct, ascending, in-window dates)."""
    days: list[date] = []
    day = start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _per_horizon(value: PerHorizon, horizon: ForwardHorizon) -> Value | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value.get(horizon)
    return value


def outcome(
    horizon: ForwardHorizon,
    signed: Value | None,
    *,
    touched: bool = False,
    mfe: Value = "5",
    mae: Value = "3",
    pct: Value = "40",
) -> HorizonOutcome:
    if signed is None or horizon == ForwardHorizon.NEXT_SESSION:
        return HorizonOutcome(horizon=horizon, available=False)
    return HorizonOutcome(
        horizon=horizon,
        available=True,
        horizon_timestamp=GENERATED_AT,
        price_at_horizon=Decimal("500"),
        touched_vwap=touched,
        bars_to_touch=1 if touched else None,
        minutes_to_touch=5 if touched else None,
        pct_extension_retraced=Decimal(pct),
        signed_return_toward_vwap_bps=Decimal(signed),
        max_favorable_excursion_bps=Decimal(mfe),
        max_adverse_excursion_bps=Decimal(mae),
    )


def point(
    day: date,
    index: int,
    *,
    side: ExtensionSide | None = None,
    signed: PerHorizon = None,
    touched: bool | Mapping[ForwardHorizon, bool] = False,
    mfe: PerHorizon = "5",
    mae: PerHorizon = "3",
    pct: PerHorizon = "40",
    regime: Regime = Regime.RANGE,
    time_of_day: TimeOfDayBucket = TimeOfDayBucket.MIDDAY,
    normalized_extension: Value | None = "2",
    signal_close: Value = "500.50",
    signal_vwap: Value = "500.00",
    session_bars_complete: bool = True,
) -> DecisionPointRecord:
    """One decision point. ``side=None`` builds an ineligible
    (``zero_extension``) filler point with no available outcome."""
    signal_as_of = datetime.combine(day, time(9, 35), tzinfo=EASTERN) + timedelta(minutes=5 * index)
    if side is None:
        return DecisionPointRecord(
            session_date=day,
            signal_as_of=signal_as_of,
            bar_index_in_session=index,
            eligibility=EligibilityStatus.ZERO_EXTENSION,
            session_bars_complete=session_bars_complete,
            regime=regime,
            scenario_horizon=ScenarioHorizon.INDETERMINATE,
            time_of_day_bucket=time_of_day,
            outcomes=[HorizonOutcome(horizon=h, available=False) for h in ForwardHorizon],
        )
    outcomes = []
    for horizon in ForwardHorizon:
        touch = touched.get(horizon, False) if isinstance(touched, Mapping) else touched
        outcomes.append(
            outcome(
                horizon,
                _per_horizon(signed, horizon),
                touched=touch,
                mfe=_per_horizon(mfe, horizon) or "0",
                mae=_per_horizon(mae, horizon) or "0",
                pct=_per_horizon(pct, horizon) or "0",
            )
        )
    return DecisionPointRecord(
        session_date=day,
        signal_as_of=signal_as_of,
        bar_index_in_session=index,
        eligibility=EligibilityStatus.ELIGIBLE,
        session_bars_complete=session_bars_complete,
        regime=regime,
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        time_of_day_bucket=time_of_day,
        extension_side=side,
        close_to_vwap_distance_bps=Decimal("10")
        if side == ExtensionSide.ABOVE_VWAP
        else Decimal("-10"),
        normalized_vwap_extension=None
        if normalized_extension is None
        else Decimal(normalized_extension),
        signal_vwap=Decimal(signal_vwap),
        signal_close=Decimal(signal_close),
        outcomes=outcomes,
    )


def session(
    day: date,
    points_by_index: Mapping[int, Mapping[str, object]],
    *,
    complete: bool = True,
    n_points: int = 78,
) -> list[DecisionPointRecord]:
    """A session of ``n_points`` decision points (indices 0..n-1). Indices in
    ``points_by_index`` are eligible points built from those kwargs; every
    other index is ineligible filler. ``complete`` sets
    ``session_bars_complete`` on the final point."""
    points = []
    for index in range(n_points):
        last = index == n_points - 1
        flag = complete if last else True
        kwargs = dict(points_by_index.get(index, {}))
        points.append(point(day, index, session_bars_complete=flag, **kwargs))  # type: ignore[arg-type]
    return points


def _empty_bundle() -> HorizonDescriptiveBundle:
    empty = DescriptiveStats(status=SampleStatus.INSUFFICIENT_SAMPLE, sample_size=0)
    return HorizonDescriptiveBundle(
        eligible_count=0,
        available_count=0,
        missing_count=0,
        touch_rate=empty,
        pct_extension_retraced=empty,
        signed_return_toward_vwap_bps=empty,
        max_favorable_excursion_bps=empty,
        max_adverse_excursion_bps=empty,
    )


def make_record(
    decision_points: Sequence[DecisionPointRecord],
    *,
    regime_thresholds: RegimeThresholdsSnapshot | None = None,
    sample_thresholds: SampleThresholdsSnapshot | None = None,
    unique_session_count: int | None = None,
) -> SpyVwapReversionEvaluationRecord:
    """A validated evaluation record around ``decision_points`` (in the
    order given). Aggregate horizon summaries are empty placeholders -- the
    confirmation analysis never reads them."""
    eligible = [dp for dp in decision_points if dp.eligibility == EligibilityStatus.ELIGIBLE]
    bundle = _empty_bundle()
    missing: Counter = Counter()
    for dp in eligible:
        for item in dp.outcomes:
            if not item.available:
                missing[item.horizon] += 1
    return SpyVwapReversionEvaluationRecord(
        symbol="SPY",
        generated_at=GENERATED_AT,
        regime_thresholds=regime_thresholds or _default_regime_thresholds_snapshot(),
        sample_thresholds=sample_thresholds
        or SampleThresholdsSnapshot(min_observations_for_summary=50, min_sessions_for_summary=20),
        unique_session_count=(
            unique_session_count
            if unique_session_count is not None
            else len({dp.session_date for dp in decision_points})
        ),
        candidate_decision_point_count=len(decision_points),
        eligible_observation_count=len(eligible),
        no_signal_vwap_unavailable_count=0,
        no_signal_zero_extension_count=len(decision_points) - len(eligible),
        regime_counts_all_candidates={
            r: sum(1 for dp in decision_points if dp.regime == r) for r in Regime
        },
        regime_counts_eligible_only={
            r: sum(1 for dp in eligible if dp.regime == r) for r in Regime
        },
        extension_side_counts={
            s: sum(1 for dp in eligible if dp.extension_side == s) for s in ExtensionSide
        },
        missing_horizon_counts={h: missing.get(h, 0) for h in ForwardHorizon},
        horizon_summaries=[
            HorizonSummary(
                horizon=h,
                above_vwap=HorizonSideSummary(
                    extension_side=ExtensionSide.ABOVE_VWAP,
                    observation_level=bundle,
                    session_level=bundle,
                ),
                below_vwap=HorizonSideSummary(
                    extension_side=ExtensionSide.BELOW_VWAP,
                    observation_level=bundle,
                    session_level=bundle,
                ),
            )
            for h in ForwardHorizon
        ],
        decision_points=list(decision_points),
        notes=["no_predictive_edge_established"],
    )


ValueFn = Callable[[int, ExtensionSide, ForwardHorizon], Value | None]


def patterned_value(base: Value, spread: Value = "4") -> ValueFn:
    """A deterministic, non-constant per-session value: ``base`` plus a
    fixed zero-mean-ish pattern scaled by ``spread``. ``None``-free."""
    offsets = ("-1", "0.5", "1", "-0.5", "0", "0.25", "-0.25")

    def fn(session_index: int, side: ExtensionSide, horizon: ForwardHorizon) -> Value:
        offset = Decimal(offsets[session_index % len(offsets)]) * Decimal(spread)
        return Decimal(base) + offset

    return fn


def study_points(
    days: Sequence[date],
    *,
    above: ValueFn,
    below: ValueFn,
    points_per_side: int = 2,
    regime_cycle: Sequence[Regime] = (Regime.RANGE,),
    extension_cycle: Sequence[Value | None] = ("2",),
) -> list[DecisionPointRecord]:
    """A full synthetic study: every session complete, with
    ``points_per_side`` eligible above- and below-VWAP points whose
    signed return at every primary horizon comes from ``above``/``below``.
    A ``None`` value makes that horizon unavailable for that session."""
    decision_points: list[DecisionPointRecord] = []
    for session_index, day in enumerate(days):
        spec: dict[int, dict[str, object]] = {}
        for k in range(points_per_side):
            for offset, side, fn in (
                (0, ExtensionSide.ABOVE_VWAP, above),
                (1, ExtensionSide.BELOW_VWAP, below),
            ):
                index = 10 + 2 * k + offset
                spec[index] = {
                    "side": side,
                    "signed": {h: fn(session_index, side, h) for h in _ALL},
                    "touched": session_index % 2 == 0,
                    "regime": regime_cycle[session_index % len(regime_cycle)],
                    "normalized_extension": extension_cycle[
                        (session_index + k) % len(extension_cycle)
                    ],
                }
        decision_points.extend(session(day, spec))
    return decision_points


def analysis_kwargs(
    record: SpyVwapReversionEvaluationRecord,
    sample: StudySample = StudySample.CONFIRMATION,
) -> dict[str, object]:
    return {
        "sample": sample,
        "input_sha256": INPUT_SHA,
        "evaluation_record_sha256": canonical_record_sha256(record),
        "code_commit_sha": CODE_COMMIT,
        "generated_at": GENERATED_AT,
    }


def default_regime_thresholds() -> RegimeThresholdsSnapshot:
    return _default_regime_thresholds_snapshot()


def reference_result_sha256() -> str:
    """SHA-256 of the canonical JSON of one fixed synthetic holdout-sized
    result. Used to compare result bytes across separate interpreters."""
    import hashlib

    from market_intelligence.evaluation.spy_vwap_reversion_confirmation import (
        analyze_confirmation,
    )
    from market_intelligence.evaluation.spy_vwap_reversion_confirmation_serialization import (
        result_to_json_str,
    )

    record = make_record(
        study_points(
            weekdays(HOLDOUT_START, 40), above=patterned_value("3"), below=patterned_value("1")
        )
    )
    result = analyze_confirmation(record, **analysis_kwargs(record, StudySample.HOLDOUT))
    return hashlib.sha256(result_to_json_str(result).encode("utf-8")).hexdigest()
