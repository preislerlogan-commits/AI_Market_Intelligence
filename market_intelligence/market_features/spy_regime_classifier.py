"""Deterministic, conservative regime/horizon classifier for the offline SPY
intraday regime engine -- step c of Phase 1 (see
``docs/OPTIONS_DECISION_WORKFLOW.md``).

This module contains **no feature math** (see ``spy_regime_features.py``):
it consumes only an already-computed ``RegimeFeatures`` and applies a fixed,
published decision order. Separating the two means the thresholds below can
be evaluated and revised later (step d) without touching how any feature is
computed. Nothing here makes a network request, opens a database
connection, or imports anything from ``market_intelligence.data_connectors``,
``market_intelligence.storage``, ``market_intelligence.agents``,
``market_intelligence.orchestration``, or ``market_intelligence.model_clients``.

**Every threshold below is a provisional hypothesis, not a validated
value** -- see ``PROJECT_STATE.md`` and
``docs/OPTIONS_DECISION_WORKFLOW.md``: no SPY session has been classified
against real data by this engine, and no predictive accuracy or
mean-reversion edge has been established. Step d (offline VWAP-extension /
reversion evaluation) is what would test these thresholds against real
history; this module only fixes them so that evaluation is possible.

## Published decision order (regime)

Evaluated in this exact order; the first matching rule wins.

0. **Incomplete session.** ``session_bars_complete is False`` ->
   ``INDETERMINATE``. This is checked *before every other rule, including
   event-driven* -- a session with a missing 5-minute interval (e.g.
   ``09:30``, ``09:40``, ``09:45``, skipping ``09:35``) is never classified,
   even when a catalyst is active. Grid-aligned input validation
   (``spy_regime_contracts.py``) cannot by itself prove continuity, so this
   engine treats an incomplete session as fail-closed rather than assuming
   the observed bars represent the whole elapsed window. See
   ``spy_regime_features.py`` for how ``session_bars_complete`` /
   ``missing_interval_count`` are computed.
1. **Event-driven.** ``catalyst_state == ACTIVE`` -> ``EVENT_DRIVEN``.
   This is the *only* path to ``EVENT_DRIVEN``, and it never depends on any
   price feature -- see ``DECISION_RULES.md`` ("AI does not... event-driven
   classification must depend on an explicit validated catalyst input").
2. **Insufficient evidence.** ``trend_strength is None`` (fewer than 2 bars
   observed) -> ``INDETERMINATE``. Every later rule depends on
   ``trend_strength``, so this is the single sufficiency gate.
3. **Trend continuation.** Requires *all* of:
   - ``abs(trend_strength) >= trend_strength_min_for_continuation``;
   - ``vwap_slope_bps`` is not ``None``, has the same sign as
     ``trend_strength``, and ``abs(vwap_slope_bps) >=
     vwap_slope_min_bps_for_continuation``;
   - ``opening_range_established`` is ``True`` and ``opening_range_position``
     is ``ABOVE`` when ``trend_strength > 0`` or ``BELOW`` when
     ``trend_strength < 0``;
   - ``close_to_vwap_distance_bps`` is not ``None`` and has the same sign as
     ``trend_strength`` (a large extension *in the trend direction* is
     expected on a trend day -- see ``DECISION_RULES.md``, "A large VWAP
     extension alone is not a trade signal").
   -> ``TREND_CONTINUATION``.
4. **VWAP mean reversion.** Requires *all* of:
   - ``catalyst_state != ACTIVE`` (defensive re-check; rule 1 already
     excludes this);
   - ``normalized_vwap_extension`` is not ``None`` and
     ``abs(normalized_vwap_extension) >= extension_threshold_for_reversion``;
   - ``abs(trend_strength) <= trend_strength_max_for_reversion`` (this is
     *not* a trend day -- see "distinguish likely trend days from
     mean-reversion conditions" in ``docs/OPTIONS_DECISION_WORKFLOW.md``);
   - **at least ``min_corroborators_for_reversion`` (2)** of the following
     four independent corroborating conditions hold:
     - ``vwap_slope_bps`` is not ``None`` and
       ``abs(vwap_slope_bps) <= vwap_slope_max_bps_for_reversion`` (VWAP
       itself is roughly flat);
     - ``relative_volume`` is not ``None`` and
       ``relative_volume <= relative_volume_elevated_min`` (no
       volume-confirmed breakout);
     - ``opening_range_established`` is ``True`` and
       ``opening_range_position == INSIDE`` (no breakout structure);
     - ``time_of_day_bucket != OPEN`` (the first 30 minutes are excluded as
       too noisy for a reversion call).
   -> ``VWAP_MEAN_REVERSION``. A large extension by itself never reaches
   this rule -- see ``DECISION_RULES.md`` ("A large VWAP extension alone is
   not a trade signal") -- because the trend-strength and corroborator
   requirements above are independent of extension magnitude.
5. **Range.** Requires *all* of:
   - ``abs(trend_strength) <= range_trend_strength_max``;
   - ``normalized_vwap_extension`` is not ``None`` and
     ``abs(normalized_vwap_extension) <= range_extension_max``.
   -> ``RANGE``.
6. **Otherwise** -> ``INDETERMINATE`` (conflicting or insufficient evidence).

## Published decision order (scenario horizon)

Evaluated only after the regime above is fixed; the regime is never
reconsidered here.

0. ``session_bars_complete is False`` -> ``INDETERMINATE``. Checked
   directly against the features, independently of whatever ``regime``
   value was passed in -- an incomplete session forces the horizon closed
   even if this function is ever called with a regime derived some other
   way than through ``classify_regime`` above.
1. ``regime == INDETERMINATE`` -> ``INDETERMINATE``.
2. ``minutes_remaining_in_session <= 0`` -> ``INDETERMINATE`` (inadequate
   time remaining, regardless of regime).
3. ``EVENT_DRIVEN``: ``INTRADAY_30M`` if
   ``minutes_remaining_in_session >= intraday_30m_min_minutes_remaining``,
   else ``TO_SESSION_CLOSE``.
4. ``TREND_CONTINUATION``: ``INTRADAY_2H`` if
   ``minutes_remaining_in_session >= intraday_2h_min_minutes_remaining``,
   else ``TO_SESSION_CLOSE``.
5. ``VWAP_MEAN_REVERSION``: ``INTRADAY_30M`` if
   ``minutes_remaining_in_session >= intraday_30m_min_minutes_remaining``,
   else ``TO_SESSION_CLOSE``.
6. ``RANGE``: always ``NEXT_SESSION`` (no bounded intraday edge is claimed
   for a balanced session; see ``docs/OPTIONS_DECISION_WORKFLOW.md``).

No rule here can produce a horizon other than the fixed
``ScenarioHorizon`` enum, and this is the only module in the Phase 1
workflow that produces one -- see ``DECISION_RULES.md``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from market_intelligence.market_features.spy_regime_contracts import (
    CatalystState,
    OpeningRangePosition,
    Regime,
    RegimeClassificationResult,
    RegimeEngineInput,
    RegimeFeatures,
    ScenarioHorizon,
    TimeOfDayBucket,
)
from market_intelligence.market_features.spy_regime_features import compute_features


def _require_finite_nonnegative(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    if value < 0:
        raise ValueError(f"{field_name} must be nonnegative")


@dataclass(frozen=True)
class RegimeThresholds:
    """Every classifier threshold, centralized and explicit.

    All defaults are provisional hypotheses (see the module docstring) --
    none has been evaluated against real SPY history by this project.

    ``__post_init__`` validates every field and fails closed (raises
    ``ValueError``) on construction -- a caller can never silently obtain a
    ``RegimeThresholds`` that would let a horizon describe a period longer
    than the remaining regular session, or that would otherwise weaken a
    published decision-order boundary:

    - every numeric threshold must be finite and nonnegative;
    - ``trend_strength``-bounded thresholds
      (``trend_strength_min_for_continuation``,
      ``trend_strength_max_for_reversion``, ``range_trend_strength_max``)
      must stay within ``trend_strength``'s own mathematically valid range,
      ``[-1, 1]`` (see ``spy_regime_features.py``, "bounded in [-1, 1] by
      construction") -- since every comparison uses ``abs(trend_strength)``,
      the usable range is ``[0, 1]``;
    - ``min_corroborators_for_reversion`` must be between 1 and 4 (there are
      exactly four corroborating conditions; 0 would make the corroborator
      gate meaningless and more than 4 could never be satisfied);
    - ``intraday_30m_min_minutes_remaining`` must be exactly 30 and
      ``intraday_2h_min_minutes_remaining`` must be exactly 120 -- these are
      not tunable hypotheses like the regime thresholds above but a
      structural guarantee (a horizon may never describe more time than
      remains in the regular session), so a caller can never weaken them.
    """

    extension_threshold_for_reversion: Decimal = Decimal("1.5")
    trend_strength_min_for_continuation: Decimal = Decimal("0.6")
    trend_strength_max_for_reversion: Decimal = Decimal("0.35")
    vwap_slope_min_bps_for_continuation: Decimal = Decimal("3")
    vwap_slope_max_bps_for_reversion: Decimal = Decimal("4")
    relative_volume_elevated_min: Decimal = Decimal("1.3")
    range_trend_strength_max: Decimal = Decimal("0.25")
    range_extension_max: Decimal = Decimal("1.0")
    min_corroborators_for_reversion: int = 2
    intraday_30m_min_minutes_remaining: int = 30
    intraday_2h_min_minutes_remaining: int = 120

    def __post_init__(self) -> None:
        for field_name in (
            "extension_threshold_for_reversion",
            "trend_strength_min_for_continuation",
            "trend_strength_max_for_reversion",
            "vwap_slope_min_bps_for_continuation",
            "vwap_slope_max_bps_for_reversion",
            "relative_volume_elevated_min",
            "range_trend_strength_max",
            "range_extension_max",
        ):
            _require_finite_nonnegative(getattr(self, field_name), field_name)

        if not (Decimal(0) < self.trend_strength_min_for_continuation <= Decimal(1)):
            raise ValueError(
                "trend_strength_min_for_continuation must be within (0, 1] -- "
                "trend_strength is bounded in [-1, 1] and compared via abs()"
            )
        if not (Decimal(0) <= self.trend_strength_max_for_reversion < Decimal(1)):
            raise ValueError(
                "trend_strength_max_for_reversion must be within [0, 1) -- "
                "trend_strength is bounded in [-1, 1] and compared via abs()"
            )
        if not (Decimal(0) <= self.range_trend_strength_max < Decimal(1)):
            raise ValueError(
                "range_trend_strength_max must be within [0, 1) -- "
                "trend_strength is bounded in [-1, 1] and compared via abs()"
            )

        if not (1 <= self.min_corroborators_for_reversion <= 4):
            raise ValueError(
                "min_corroborators_for_reversion must be between 1 and 4 "
                "(there are exactly four corroborating conditions)"
            )

        if self.intraday_30m_min_minutes_remaining != 30:
            raise ValueError(
                "intraday_30m_min_minutes_remaining must be exactly 30 -- an "
                "intraday_30m horizon may never describe a period longer "
                "than the remaining regular session"
            )
        if self.intraday_2h_min_minutes_remaining != 120:
            raise ValueError(
                "intraday_2h_min_minutes_remaining must be exactly 120 -- an "
                "intraday_2h horizon may never describe a period longer "
                "than the remaining regular session"
            )


DEFAULT_THRESHOLDS = RegimeThresholds()


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _classify_trend_continuation(
    features: RegimeFeatures, thresholds: RegimeThresholds
) -> bool:
    trend_strength = features.trend_strength
    min_trend_strength = thresholds.trend_strength_min_for_continuation
    if trend_strength is None or abs(trend_strength) < min_trend_strength:
        return False
    trend_sign = _sign(trend_strength)
    if trend_sign == 0:
        return False

    slope = features.vwap_slope_bps
    if (
        slope is None
        or _sign(slope) != trend_sign
        or abs(slope) < thresholds.vwap_slope_min_bps_for_continuation
    ):
        return False

    if not features.opening_range_established:
        return False
    expected_position = (
        OpeningRangePosition.ABOVE if trend_sign > 0 else OpeningRangePosition.BELOW
    )
    if features.opening_range_position != expected_position:
        return False

    distance = features.close_to_vwap_distance_bps
    if distance is None or _sign(distance) != trend_sign:
        return False

    return True


def _count_reversion_corroborators(
    features: RegimeFeatures, thresholds: RegimeThresholds
) -> int:
    corroborators = 0

    if (
        features.vwap_slope_bps is not None
        and abs(features.vwap_slope_bps) <= thresholds.vwap_slope_max_bps_for_reversion
    ):
        corroborators += 1

    if (
        features.relative_volume is not None
        and features.relative_volume <= thresholds.relative_volume_elevated_min
    ):
        corroborators += 1

    if (
        features.opening_range_established
        and features.opening_range_position == OpeningRangePosition.INSIDE
    ):
        corroborators += 1

    if features.time_of_day_bucket != TimeOfDayBucket.OPEN:
        corroborators += 1

    return corroborators


def _classify_vwap_mean_reversion(
    features: RegimeFeatures, thresholds: RegimeThresholds
) -> bool:
    if features.catalyst_state == CatalystState.ACTIVE:
        return False

    extension = features.normalized_vwap_extension
    if extension is None or abs(extension) < thresholds.extension_threshold_for_reversion:
        return False

    trend_strength = features.trend_strength
    if trend_strength is None or abs(trend_strength) > thresholds.trend_strength_max_for_reversion:
        return False

    corroborators = _count_reversion_corroborators(features, thresholds)
    return corroborators >= thresholds.min_corroborators_for_reversion


def _classify_range(features: RegimeFeatures, thresholds: RegimeThresholds) -> bool:
    trend_strength = features.trend_strength
    extension = features.normalized_vwap_extension
    if trend_strength is None or extension is None:
        return False
    return (
        abs(trend_strength) <= thresholds.range_trend_strength_max
        and abs(extension) <= thresholds.range_extension_max
    )


def classify_regime(
    features: RegimeFeatures, thresholds: RegimeThresholds = DEFAULT_THRESHOLDS
) -> tuple[Regime, list[str]]:
    """Apply the fixed, published decision order (see the module
    docstring) to one ``RegimeFeatures``. Returns the regime and a short,
    fixed-vocabulary rationale trace."""
    if not features.session_bars_complete:
        return Regime.INDETERMINATE, ["session_incomplete"]

    if features.catalyst_state == CatalystState.ACTIVE:
        return Regime.EVENT_DRIVEN, ["catalyst_active"]

    if features.trend_strength is None:
        return Regime.INDETERMINATE, ["insufficient_trend_data"]

    if _classify_trend_continuation(features, thresholds):
        return Regime.TREND_CONTINUATION, ["trend_structure_vwap_aligned"]

    if _classify_vwap_mean_reversion(features, thresholds):
        return Regime.VWAP_MEAN_REVERSION, ["extension_with_corroborators"]

    if _classify_range(features, thresholds):
        return Regime.RANGE, ["low_trend_contained_extension"]

    return Regime.INDETERMINATE, ["no_rule_matched"]


def classify_horizon(
    regime: Regime,
    features: RegimeFeatures,
    thresholds: RegimeThresholds = DEFAULT_THRESHOLDS,
) -> tuple[ScenarioHorizon, list[str]]:
    """Apply the fixed, published horizon decision order (see the module
    docstring) given an already-classified regime."""
    if not features.session_bars_complete:
        return ScenarioHorizon.INDETERMINATE, ["horizon_session_incomplete"]

    if regime == Regime.INDETERMINATE:
        return ScenarioHorizon.INDETERMINATE, ["horizon_indeterminate_regime"]

    if features.minutes_remaining_in_session <= 0:
        return ScenarioHorizon.INDETERMINATE, ["horizon_no_time_remaining"]

    remaining = features.minutes_remaining_in_session

    if regime == Regime.EVENT_DRIVEN:
        if remaining >= thresholds.intraday_30m_min_minutes_remaining:
            return ScenarioHorizon.INTRADAY_30M, ["event_driven_30m"]
        return ScenarioHorizon.TO_SESSION_CLOSE, ["event_driven_insufficient_time_for_30m"]

    if regime == Regime.TREND_CONTINUATION:
        if remaining >= thresholds.intraday_2h_min_minutes_remaining:
            return ScenarioHorizon.INTRADAY_2H, ["trend_continuation_2h"]
        return ScenarioHorizon.TO_SESSION_CLOSE, ["trend_continuation_insufficient_time_for_2h"]

    if regime == Regime.VWAP_MEAN_REVERSION:
        if remaining >= thresholds.intraday_30m_min_minutes_remaining:
            return ScenarioHorizon.INTRADAY_30M, ["mean_reversion_30m"]
        return ScenarioHorizon.TO_SESSION_CLOSE, ["mean_reversion_insufficient_time_for_30m"]

    # regime == Regime.RANGE
    return ScenarioHorizon.NEXT_SESSION, ["range_next_session"]


def classify(
    features: RegimeFeatures, thresholds: RegimeThresholds = DEFAULT_THRESHOLDS
) -> RegimeClassificationResult:
    """Classify one ``RegimeFeatures`` into a full ``RegimeClassificationResult``."""
    regime, regime_rationale = classify_regime(features, thresholds)
    horizon, horizon_rationale = classify_horizon(regime, features, thresholds)
    return RegimeClassificationResult(
        symbol=features.symbol,
        session_date=features.session_date,
        as_of_timestamp=features.as_of_timestamp,
        regime=regime,
        scenario_horizon=horizon,
        rationale=[*regime_rationale, *horizon_rationale],
    )


def classify_batch(
    engine_inputs: Sequence[RegimeEngineInput],
    thresholds: RegimeThresholds = DEFAULT_THRESHOLDS,
) -> list[RegimeClassificationResult]:
    """Pure batch interface for offline historical evaluation.

    Computes features and classifies each input independently, in the
    order given -- there is no cross-input state, sorting, or filtering, so
    input order is always preserved and repeated calls with the same input
    always produce the same output list.
    """
    return [classify(compute_features(engine_input), thresholds) for engine_input in engine_inputs]
