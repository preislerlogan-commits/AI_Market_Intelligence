"""Pure, offline forward-outcome scoring for the SPY VWAP-extension /
reversion hypothesis -- step d of Phase 1 (see
``docs/OPTIONS_DECISION_WORKFLOW.md``).

``evaluate_spy_vwap_reversion`` is a pure function:
``SpyVwapReversionEvaluationInput -> SpyVwapReversionEvaluationRecord``. It
makes no network request, opens no database connection, and imports nothing
from ``market_intelligence.data_connectors``, ``market_intelligence.storage``,
``market_intelligence.model_clients``, ``market_intelligence.agents``,
``market_intelligence.orchestration``, or ``market_intelligence.config`` --
only the existing, already-offline SPY intraday regime engine
(``market_intelligence.market_features.spy_regime_*``) and this evaluation's
own contracts. See
``market_intelligence/tests/test_spy_vwap_reversion_offline.py`` for the
static and fresh-interpreter proof.

## No lookahead when creating a signal

For each session, and for each bar index ``k`` in that session, the signal
at ``k`` is built from ``bars[: k + 1]`` only -- the exact same prefix
construction whose no-lookahead property is already proven for
``spy_regime_features.compute_features`` (see that module's "No lookahead"
section). The regime, horizon, VWAP, and extension recorded on a
``DecisionPointRecord`` never depend on any bar timestamped after the signal
bar. **Only the outcome-scoring stage below is allowed to look at bars after
the signal** -- and it only ever looks *forward* from the signal bar.

## The frozen VWAP reference

``session_vwap`` at the signal bar (``RegimeFeatures.session_vwap``) is
recorded once, at signal time, as ``DecisionPointRecord.signal_vwap``. Every
``HorizonOutcome`` for that decision point is scored against this same
value -- it is never recomputed at a later bar, so the reversion target
cannot move after the signal.

## Eligibility

A candidate decision point is **eligible** only when the signal-time VWAP is
available (``session_vwap`` and ``close_to_vwap_distance_bps`` are not
``None``) and the extension is non-zero (``close_to_vwap_distance_bps !=
0``, so "toward VWAP" is a well-defined direction). Every other candidate is
still recorded, with ``eligibility`` set to ``VWAP_UNAVAILABLE`` or
``ZERO_EXTENSION`` and no scored outcome -- never silently dropped.
``extension_side`` is ``ABOVE_VWAP`` when the signal-time close is above the
signal-time VWAP, ``BELOW_VWAP`` otherwise.

## Forward horizon windows -- exact, never approximated

Let ``signal_as_of = bars[k].timestamp + BAR_INTERVAL_MINUTES`` (the
signal bar's completion time, matching ``RegimeFeatures.as_of_timestamp``).
For a horizon to be **available**, its window must exist in the supplied
data with **no internal gap** (mirroring the regime engine's own VWAP-slope
and opening-range philosophy of exactness over approximation):

- **intraday_30m** / **intraday_2h**: the target time is ``signal_as_of +
  30`` / ``120`` minutes. If that target time falls after the regular
  session's ``16:00`` close, the horizon does not exist (``None``) -- a 2h
  horizon is never shortened to "whatever remains of the session." The
  evaluator searches the bars strictly after the signal bar, in the same
  session, for the one bar whose own completion time
  (``bar.timestamp + BAR_INTERVAL_MINUTES``) equals the target time
  *exactly*; if no such bar exists (missing due to a gap, or the session's
  data simply stops first), the horizon is unavailable. When a target bar
  is found, the entire chain from the signal bar through it must be
  gapless (every consecutive pair exactly ``BAR_INTERVAL_MINUTES`` apart) --
  a "lucky" bar sitting at the right time with missing bars in between it and
  the signal never counts as available, since a silent gap would corrupt
  touch detection.
- **to_session_close**: available only when the *signal's own session*
  reaches a gapless regular-session close (its last supplied bar is the
  ``15:55`` bar and ``RegimeFeatures.session_bars_complete`` is ``True`` for
  that full session). The window is every bar after the signal bar through
  that session's own last bar.
- **next_session**: available only when (a) the signal's own session reaches
  a gapless close (same check as ``to_session_close``) **and** (b) the very
  next session in ``SpyVwapReversionEvaluationInput.sessions`` also reaches
  a gapless close. The window is every remaining bar of the signal session
  followed by every bar of the next session, in chronological order. There
  is no trading-holiday calendar (see ``spy_vwap_reversion_contracts.py``):
  "next session" means the next entry the caller supplied, which is the
  literal next trading session only if the caller supplied contiguous
  sessions.

## Outcome formulas

Let ``signal_close`` be the signal bar's close, ``signal_vwap`` the frozen
VWAP, and ``extension_sign`` be ``+1`` when ``extension_side ==
ABOVE_VWAP`` or ``-1`` when ``BELOW_VWAP``. Let ``window`` be the ordered
list of bars strictly after the signal bar through the horizon endpoint
(inclusive), as defined above, and ``price_at_horizon`` be the horizon
endpoint bar's close.

- **touched_vwap** / **bars_to_touch** / **minutes_to_touch**: the first bar
  in ``window`` (in order) whose ``[low, high]`` range satisfies ``low <=
  signal_vwap <= high``. ``bars_to_touch`` is that bar's 1-based position in
  ``window``; ``minutes_to_touch`` is the whole-minute difference between
  that bar's completion time and ``signal_as_of``. ``touched_vwap=False``
  and both counts ``None`` if no bar in the window touches or crosses
  ``signal_vwap``.
- **pct_extension_retraced**: let ``original_extension = abs(signal_close -
  signal_vwap)`` and ``toward_vwap_movement = extension_sign *
  (signal_close - price_at_horizon)``. Then ``pct_extension_retraced =
  toward_vwap_movement / original_extension * 100``. Positive means price
  moved toward (or through) VWAP; negative means it extended further away;
  values above 100 mean price overshot past VWAP to the other side.
- **signed_return_toward_vwap_bps**: ``toward_vwap_movement / signal_close *
  10000`` (the same signed, toward-VWAP-positive ``toward_vwap_movement``,
  expressed in basis points of the signal-time close rather than as a
  percentage of the original extension).
- **max_favorable_excursion_bps** / **max_adverse_excursion_bps**: over every
  bar in ``window``, the largest toward-VWAP move and the largest
  away-from-VWAP move reached by that bar's high/low, each relative to
  ``signal_close`` and expressed in bps of ``signal_close``. For
  ``extension_sign == +1`` (price started above VWAP): favorable move for a
  bar is ``signal_close - bar.low`` (the deepest point reached toward VWAP),
  adverse move is ``bar.high - signal_close``. For ``extension_sign == -1``
  the two are swapped. These are price/VWAP-mechanics measurements only --
  never a P&L, option, or trade-direction figure (see
  ``DECISION_RULES.md``).

## No misleading statistics

``eligible_observation_count`` counts every eligible decision point --
these are heavily overlapping five-minute observations from the same
sessions, not independent trials, and every summary below is explicitly
computed at two levels: ``observation_level`` pools them directly;
``session_level`` first reduces each contributing session to the *mean* of
its own eligible observations for that (horizon, side) combination, then
summarizes across sessions -- so a single volatile session can never
dominate a cross-session statistic. Each level uses its own
``DescriptiveStats`` sample-size gate (``SampleSizeThresholds``); below the
gate, the summary is ``insufficient_sample`` rather than a value computed
from too few points. **These thresholds are not tuned to any observed
result** -- they are fixed defaults, and this evaluator never adjusts a
regime-classification threshold either (see ``RegimeThresholdsSnapshot``):
the hypothesis is evaluated exactly as it was published in
``spy_regime_classifier.py``, unmodified.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Decimal

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
    SessionBars,
    SpyVwapReversionEvaluationInput,
    SpyVwapReversionEvaluationRecord,
)
from market_intelligence.market_features.spy_regime_classifier import (
    DEFAULT_THRESHOLDS,
    RegimeThresholds,
    classify,
)
from market_intelligence.market_features.spy_regime_contracts import (
    BAR_INTERVAL_MINUTES,
    EASTERN,
    Regime,
    RegimeEngineInput,
    RegimeFeatures,
)
from market_intelligence.market_features.spy_regime_features import compute_features

# --- Fixed, provisional sample-size gate -------------------------------------------
#
# Not tuned to any observed result: these are chosen before this evaluator is
# ever run against real SPY history, and they never change based on what a
# real run finds. See the module docstring, "No misleading statistics".

_DEFAULT_MIN_OBSERVATIONS_FOR_SUMMARY = 50
_DEFAULT_MIN_SESSIONS_FOR_SUMMARY = 20


@dataclass(frozen=True)
class SampleSizeThresholds:
    """Minimum sample sizes gating ``DescriptiveStats`` at each aggregation
    level. See ``spy_vwap_reversion_contracts.SampleThresholdsSnapshot`` for
    the recorded, audit-trail copy of whichever instance a run used."""

    min_observations_for_summary: int = _DEFAULT_MIN_OBSERVATIONS_FOR_SUMMARY
    min_sessions_for_summary: int = _DEFAULT_MIN_SESSIONS_FOR_SUMMARY

    def __post_init__(self) -> None:
        if self.min_observations_for_summary <= 0:
            raise ValueError("min_observations_for_summary must be positive")
        if self.min_sessions_for_summary <= 0:
            raise ValueError("min_sessions_for_summary must be positive")


DEFAULT_SAMPLE_THRESHOLDS = SampleSizeThresholds()

_SESSION_CLOSE_BAR_TIME = _time(15, 55)
_SESSION_CLOSE_END_TIME = _time(16, 0)

_BPS_QUANT = Decimal("0.0001")
_PCT_QUANT = Decimal("0.0001")
_PRICE_QUANT = Decimal("0.000001")
_STATS_QUANT = Decimal("0.0001")

_FIXED_NOTES = (
    "underlying_setup_only_no_options_or_pnl",
    "thresholds_frozen_unmodified",
    "single_evaluation_run",
    "no_predictive_edge_established",
)


def _quant(value: Decimal, exponent: Decimal) -> Decimal:
    return value.quantize(exponent, rounding=ROUND_HALF_EVEN)


# --- Session-level helpers ----------------------------------------------------------


def _full_session_engine_input(session: SessionBars) -> RegimeEngineInput:
    return RegimeEngineInput(
        session_date=session.session_date,
        bars=session.bars,
        prior_day=session.prior_day,
        same_time_historical_volume_baseline=session.same_time_historical_volume_baseline,
        catalyst_state=session.catalyst_state,
        breadth_state=session.breadth_state,
    )


def _prefix_engine_input(session: SessionBars, k: int) -> RegimeEngineInput:
    return RegimeEngineInput(
        session_date=session.session_date,
        bars=session.bars[: k + 1],
        prior_day=session.prior_day,
        same_time_historical_volume_baseline=session.same_time_historical_volume_baseline,
        catalyst_state=session.catalyst_state,
        breadth_state=session.breadth_state,
    )


def _session_reaches_close(session: SessionBars) -> bool:
    """True only when this session's own supplied bars are gapless from
    09:30 through an actual 15:55 bar (i.e. the true regular-session
    close) -- see the module docstring, "Forward horizon windows"."""
    last_bar = session.bars[-1]
    if last_bar.timestamp.astimezone(EASTERN).time() != _SESSION_CLOSE_BAR_TIME:
        return False
    features = compute_features(_full_session_engine_input(session))
    return features.session_bars_complete


def _is_gapless(ordered_bars: list) -> bool:
    step = timedelta(minutes=BAR_INTERVAL_MINUTES)
    for previous, current in zip(ordered_bars, ordered_bars[1:], strict=False):
        if current.timestamp - previous.timestamp != step:
            return False
    return True


def _horizon_window_bars(
    *,
    session: SessionBars,
    signal_bar_index: int,
    horizon: ForwardHorizon,
    session_reaches_close: bool,
    next_session: SessionBars | None,
    next_session_reaches_close: bool,
) -> list | None:
    """The ordered bars strictly after the signal bar through exactly the
    horizon endpoint (inclusive), or ``None`` if the full horizon does not
    exist gaplessly in the supplied data. See the module docstring."""
    signal_bar = session.bars[signal_bar_index]
    signal_as_of = signal_bar.timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES)
    remaining = session.bars[signal_bar_index + 1 :]

    if horizon == ForwardHorizon.TO_SESSION_CLOSE:
        if not remaining or not session_reaches_close:
            return None
        return remaining

    if horizon == ForwardHorizon.NEXT_SESSION:
        if not session_reaches_close or next_session is None or not next_session_reaches_close:
            return None
        return [*remaining, *next_session.bars]

    # INTRADAY_30M / INTRADAY_2H
    target_minutes = 30 if horizon == ForwardHorizon.INTRADAY_30M else 120
    target_time = signal_as_of + timedelta(minutes=target_minutes)
    session_close_dt = _datetime.combine(
        session.session_date, _SESSION_CLOSE_END_TIME, tzinfo=EASTERN
    )
    if target_time > session_close_dt:
        return None

    target_index = None
    for idx, bar in enumerate(remaining):
        bar_end = bar.timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES)
        if bar_end == target_time:
            target_index = idx
            break
        if bar_end > target_time:
            break
    if target_index is None:
        return None

    window = remaining[: target_index + 1]
    if not _is_gapless([signal_bar, *window]):
        return None
    return window


def _compute_horizon_outcome(
    *,
    horizon: ForwardHorizon,
    window_bars: list | None,
    signal_as_of: _datetime,
    signal_vwap: Decimal,
    signal_close: Decimal,
    extension_sign: int,
) -> HorizonOutcome:
    if not window_bars:
        return HorizonOutcome(horizon=horizon, available=False)

    horizon_endpoint = window_bars[-1]
    horizon_timestamp = horizon_endpoint.timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES)
    price_at_horizon = horizon_endpoint.close

    touched = False
    bars_to_touch: int | None = None
    minutes_to_touch: int | None = None
    for idx, bar in enumerate(window_bars, start=1):
        if bar.low <= signal_vwap <= bar.high:
            touched = True
            bars_to_touch = idx
            bar_end = bar.timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES)
            minutes_to_touch = int((bar_end - signal_as_of).total_seconds() // 60)
            break

    sign = Decimal(extension_sign)
    original_extension = abs(signal_close - signal_vwap)
    toward_vwap_movement = sign * (signal_close - price_at_horizon)
    pct_extension_retraced = _quant(
        toward_vwap_movement / original_extension * Decimal(100), _PCT_QUANT
    )
    signed_return_toward_vwap_bps = _quant(
        toward_vwap_movement / signal_close * Decimal(10000), _BPS_QUANT
    )

    favorable_values: list[Decimal] = []
    adverse_values: list[Decimal] = []
    for bar in window_bars:
        if extension_sign > 0:
            favorable_values.append(signal_close - bar.low)
            adverse_values.append(bar.high - signal_close)
        else:
            favorable_values.append(bar.high - signal_close)
            adverse_values.append(signal_close - bar.low)

    max_favorable_excursion_bps = _quant(
        max(favorable_values) / signal_close * Decimal(10000), _BPS_QUANT
    )
    max_adverse_excursion_bps = _quant(
        max(adverse_values) / signal_close * Decimal(10000), _BPS_QUANT
    )

    return HorizonOutcome(
        horizon=horizon,
        available=True,
        horizon_timestamp=horizon_timestamp,
        price_at_horizon=_quant(price_at_horizon, _PRICE_QUANT),
        touched_vwap=touched,
        bars_to_touch=bars_to_touch,
        minutes_to_touch=minutes_to_touch,
        pct_extension_retraced=pct_extension_retraced,
        signed_return_toward_vwap_bps=signed_return_toward_vwap_bps,
        max_favorable_excursion_bps=max_favorable_excursion_bps,
        max_adverse_excursion_bps=max_adverse_excursion_bps,
    )


def _determine_eligibility(
    features: RegimeFeatures,
) -> tuple[EligibilityStatus, ExtensionSide | None, int | None]:
    if features.session_vwap is None or features.close_to_vwap_distance_bps is None:
        return EligibilityStatus.VWAP_UNAVAILABLE, None, None
    if features.close_to_vwap_distance_bps == 0:
        return EligibilityStatus.ZERO_EXTENSION, None, None
    if features.close_to_vwap_distance_bps > 0:
        return EligibilityStatus.ELIGIBLE, ExtensionSide.ABOVE_VWAP, 1
    return EligibilityStatus.ELIGIBLE, ExtensionSide.BELOW_VWAP, -1


def _build_decision_points(
    evaluation_input: SpyVwapReversionEvaluationInput,
    regime_thresholds: RegimeThresholds,
) -> list[DecisionPointRecord]:
    sessions = evaluation_input.sessions
    reaches_close_by_index = [_session_reaches_close(session) for session in sessions]

    decision_points: list[DecisionPointRecord] = []
    for i, session in enumerate(sessions):
        next_session = sessions[i + 1] if i + 1 < len(sessions) else None
        next_reaches_close = reaches_close_by_index[i + 1] if next_session is not None else False

        for k in range(len(session.bars)):
            engine_input = _prefix_engine_input(session, k)
            features = compute_features(engine_input)
            classification = classify(features, regime_thresholds)

            eligibility, extension_side, extension_sign = _determine_eligibility(features)
            signal_close = session.bars[k].close

            outcomes: list[HorizonOutcome] = []
            for horizon in ForwardHorizon:
                if eligibility != EligibilityStatus.ELIGIBLE or extension_sign is None:
                    outcomes.append(HorizonOutcome(horizon=horizon, available=False))
                    continue
                window = _horizon_window_bars(
                    session=session,
                    signal_bar_index=k,
                    horizon=horizon,
                    session_reaches_close=reaches_close_by_index[i],
                    next_session=next_session,
                    next_session_reaches_close=next_reaches_close,
                )
                outcomes.append(
                    _compute_horizon_outcome(
                        horizon=horizon,
                        window_bars=window,
                        signal_as_of=features.as_of_timestamp,
                        signal_vwap=features.session_vwap,  # type: ignore[arg-type]
                        signal_close=signal_close,
                        extension_sign=extension_sign,
                    )
                )

            decision_points.append(
                DecisionPointRecord(
                    session_date=session.session_date,
                    signal_as_of=features.as_of_timestamp,
                    bar_index_in_session=k,
                    eligibility=eligibility,
                    session_bars_complete=features.session_bars_complete,
                    regime=classification.regime,
                    scenario_horizon=classification.scenario_horizon,
                    time_of_day_bucket=features.time_of_day_bucket,
                    extension_side=extension_side,
                    close_to_vwap_distance_bps=features.close_to_vwap_distance_bps,
                    normalized_vwap_extension=features.normalized_vwap_extension,
                    signal_vwap=features.session_vwap,
                    signal_close=signal_close,
                    outcomes=outcomes,
                )
            )

    return decision_points


# --- Aggregation ---------------------------------------------------------------------


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _median(sorted_values: list[Decimal]) -> Decimal:
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / Decimal(2)


def _descriptive_stats(values: list[Decimal], threshold: int) -> DescriptiveStats:
    n = len(values)
    if n < threshold:
        return DescriptiveStats(status=SampleStatus.INSUFFICIENT_SAMPLE, sample_size=n)
    ordered = sorted(values)
    return DescriptiveStats(
        status=SampleStatus.OK,
        sample_size=n,
        mean=_quant(_mean(values), _STATS_QUANT),
        median=_quant(_median(ordered), _STATS_QUANT),
        minimum=_quant(ordered[0], _STATS_QUANT),
        maximum=_quant(ordered[-1], _STATS_QUANT),
    )


def _outcome_for(dp: DecisionPointRecord, horizon: ForwardHorizon) -> HorizonOutcome:
    return next(outcome for outcome in dp.outcomes if outcome.horizon == horizon)


def _build_observation_bundle(
    eligible: list[DecisionPointRecord],
    horizon: ForwardHorizon,
    side: ExtensionSide,
    sample_thresholds: SampleSizeThresholds,
) -> HorizonDescriptiveBundle:
    relevant = [dp for dp in eligible if dp.extension_side == side]
    outcomes = [_outcome_for(dp, horizon) for dp in relevant]
    available = [o for o in outcomes if o.available]

    threshold = sample_thresholds.min_observations_for_summary
    touch_values = [Decimal(1) if o.touched_vwap else Decimal(0) for o in available]
    retraced_values = [o.pct_extension_retraced for o in available]  # type: ignore[misc]
    signed_return_values = [o.signed_return_toward_vwap_bps for o in available]  # type: ignore[misc]
    mfe_values = [o.max_favorable_excursion_bps for o in available]  # type: ignore[misc]
    mae_values = [o.max_adverse_excursion_bps for o in available]  # type: ignore[misc]

    return HorizonDescriptiveBundle(
        eligible_count=len(relevant),
        available_count=len(available),
        missing_count=len(relevant) - len(available),
        touch_rate=_descriptive_stats(touch_values, threshold),
        pct_extension_retraced=_descriptive_stats(retraced_values, threshold),
        signed_return_toward_vwap_bps=_descriptive_stats(signed_return_values, threshold),
        max_favorable_excursion_bps=_descriptive_stats(mfe_values, threshold),
        max_adverse_excursion_bps=_descriptive_stats(mae_values, threshold),
    )


def _build_session_bundle(
    eligible: list[DecisionPointRecord],
    horizon: ForwardHorizon,
    side: ExtensionSide,
    sample_thresholds: SampleSizeThresholds,
) -> HorizonDescriptiveBundle:
    by_session: dict[_date, list[DecisionPointRecord]] = defaultdict(list)
    for dp in eligible:
        if dp.extension_side == side:
            by_session[dp.session_date].append(dp)

    threshold = sample_thresholds.min_sessions_for_summary
    session_touch: list[Decimal] = []
    session_retraced: list[Decimal] = []
    session_signed_return: list[Decimal] = []
    session_mfe: list[Decimal] = []
    session_mae: list[Decimal] = []
    available_session_count = 0

    for dps in by_session.values():
        available_outcomes = [
            o for o in (_outcome_for(dp, horizon) for dp in dps) if o.available
        ]
        if not available_outcomes:
            continue
        available_session_count += 1
        session_touch.append(
            _mean([Decimal(1) if o.touched_vwap else Decimal(0) for o in available_outcomes])
        )
        session_retraced.append(_mean([o.pct_extension_retraced for o in available_outcomes]))  # type: ignore[misc]
        session_signed_return.append(
            _mean([o.signed_return_toward_vwap_bps for o in available_outcomes])  # type: ignore[misc]
        )
        session_mfe.append(_mean([o.max_favorable_excursion_bps for o in available_outcomes]))  # type: ignore[misc]
        session_mae.append(_mean([o.max_adverse_excursion_bps for o in available_outcomes]))  # type: ignore[misc]

    eligible_session_count = len(by_session)
    return HorizonDescriptiveBundle(
        eligible_count=eligible_session_count,
        available_count=available_session_count,
        missing_count=eligible_session_count - available_session_count,
        touch_rate=_descriptive_stats(session_touch, threshold),
        pct_extension_retraced=_descriptive_stats(session_retraced, threshold),
        signed_return_toward_vwap_bps=_descriptive_stats(session_signed_return, threshold),
        max_favorable_excursion_bps=_descriptive_stats(session_mfe, threshold),
        max_adverse_excursion_bps=_descriptive_stats(session_mae, threshold),
    )


def _regime_thresholds_snapshot(thresholds: RegimeThresholds) -> RegimeThresholdsSnapshot:
    return RegimeThresholdsSnapshot(
        extension_threshold_for_reversion=thresholds.extension_threshold_for_reversion,
        trend_strength_min_for_continuation=thresholds.trend_strength_min_for_continuation,
        trend_strength_max_for_reversion=thresholds.trend_strength_max_for_reversion,
        vwap_slope_min_bps_for_continuation=thresholds.vwap_slope_min_bps_for_continuation,
        vwap_slope_max_bps_for_reversion=thresholds.vwap_slope_max_bps_for_reversion,
        relative_volume_elevated_min=thresholds.relative_volume_elevated_min,
        range_trend_strength_max=thresholds.range_trend_strength_max,
        range_extension_max=thresholds.range_extension_max,
        min_corroborators_for_reversion=thresholds.min_corroborators_for_reversion,
        intraday_30m_min_minutes_remaining=thresholds.intraday_30m_min_minutes_remaining,
        intraday_2h_min_minutes_remaining=thresholds.intraday_2h_min_minutes_remaining,
    )


def evaluate_spy_vwap_reversion(
    evaluation_input: SpyVwapReversionEvaluationInput,
    *,
    generated_at: _datetime,
    regime_thresholds: RegimeThresholds = DEFAULT_THRESHOLDS,
    sample_thresholds: SampleSizeThresholds = DEFAULT_SAMPLE_THRESHOLDS,
) -> SpyVwapReversionEvaluationRecord:
    """Evaluate the VWAP-extension/reversion hypothesis over
    ``evaluation_input``, using the existing, unmodified regime-engine
    thresholds and a fixed sample-size gate. Pure: no I/O, no clock read
    (``generated_at`` is caller-supplied), no randomness. See the module
    docstring for the exact windows and formulas.
    """
    decision_points = _build_decision_points(evaluation_input, regime_thresholds)
    eligible = [dp for dp in decision_points if dp.eligibility == EligibilityStatus.ELIGIBLE]

    no_signal_vwap_unavailable_count = sum(
        1 for dp in decision_points if dp.eligibility == EligibilityStatus.VWAP_UNAVAILABLE
    )
    no_signal_zero_extension_count = sum(
        1 for dp in decision_points if dp.eligibility == EligibilityStatus.ZERO_EXTENSION
    )

    regime_counts_all = Counter(dp.regime for dp in decision_points)
    regime_counts_eligible = Counter(dp.regime for dp in eligible)
    extension_side_counts = Counter(dp.extension_side for dp in eligible)

    missing_horizon_counts: Counter = Counter()
    for dp in eligible:
        for outcome in dp.outcomes:
            if not outcome.available:
                missing_horizon_counts[outcome.horizon] += 1

    horizon_summaries = [
        HorizonSummary(
            horizon=horizon,
            above_vwap=HorizonSideSummary(
                extension_side=ExtensionSide.ABOVE_VWAP,
                observation_level=_build_observation_bundle(
                    eligible, horizon, ExtensionSide.ABOVE_VWAP, sample_thresholds
                ),
                session_level=_build_session_bundle(
                    eligible, horizon, ExtensionSide.ABOVE_VWAP, sample_thresholds
                ),
            ),
            below_vwap=HorizonSideSummary(
                extension_side=ExtensionSide.BELOW_VWAP,
                observation_level=_build_observation_bundle(
                    eligible, horizon, ExtensionSide.BELOW_VWAP, sample_thresholds
                ),
                session_level=_build_session_bundle(
                    eligible, horizon, ExtensionSide.BELOW_VWAP, sample_thresholds
                ),
            ),
        )
        for horizon in ForwardHorizon
    ]

    return SpyVwapReversionEvaluationRecord(
        symbol=evaluation_input.symbol,
        generated_at=generated_at,
        regime_thresholds=_regime_thresholds_snapshot(regime_thresholds),
        sample_thresholds=SampleThresholdsSnapshot(
            min_observations_for_summary=sample_thresholds.min_observations_for_summary,
            min_sessions_for_summary=sample_thresholds.min_sessions_for_summary,
        ),
        unique_session_count=len(evaluation_input.sessions),
        candidate_decision_point_count=len(decision_points),
        eligible_observation_count=len(eligible),
        no_signal_vwap_unavailable_count=no_signal_vwap_unavailable_count,
        no_signal_zero_extension_count=no_signal_zero_extension_count,
        regime_counts_all_candidates={
            regime: regime_counts_all.get(regime, 0) for regime in Regime
        },
        regime_counts_eligible_only={
            regime: regime_counts_eligible.get(regime, 0) for regime in Regime
        },
        extension_side_counts={
            side: extension_side_counts.get(side, 0) for side in ExtensionSide
        },
        missing_horizon_counts={
            horizon: missing_horizon_counts.get(horizon, 0) for horizon in ForwardHorizon
        },
        horizon_summaries=horizon_summaries,
        decision_points=decision_points,
        notes=list(_FIXED_NOTES),
    )
