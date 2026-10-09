"""Adapters for already-produced SPY regime-engine results (registry
``spy_regime_engine``, ``spy-regime-engine-1``).

The engine's ``RegimeFeatures`` and ``RegimeClassificationResult`` become
``deterministic_calculation`` items with complete parent lineage:

- features cite one ``market_bar_fact.v1`` item for every input bar, plus any
  item supplying the prior-day levels or volume baseline;
- a classification cites exactly the features item it was computed from.

Each result is verified by recomputing it with the engine's own pure
functions, so a result that does not follow from its cited inputs is
refused. A regime label is a calculated label, never a trend signal, a setup
or a direction; no setup subject or definition is created.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import Any

from market_intelligence.evidence.canonical import canonical_sha256
from market_intelligence.evidence.contracts import (
    CalculationInputReference,
    EvidenceItem,
    EvidenceSubject,
    EvidenceTemporalScope,
)
from market_intelligence.evidence.enums import EvidenceKind, SourceTier, SubjectType
from market_intelligence.evidence.primitives import format_utc
from market_intelligence.evidence_adapters.common import (
    SPY_SUBJECT_ID,
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    fail,
    refuse_spy_holdout,
    require_configuration,
    require_price_schema,
    window_subject,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    MARKET_BAR_FACT,
    SPY_REGIME_CLASSIFICATION,
    SPY_REGIME_FEATURES,
)
from market_intelligence.market_features.spy_regime_classifier import DEFAULT_THRESHOLDS, classify
from market_intelligence.market_features.spy_regime_contracts import (
    BAR_INTERVAL_MINUTES,
    SCHEMA_VERSION,
    RegimeClassificationResult,
    RegimeEngineInput,
    RegimeFeatures,
)
from market_intelligence.market_features.spy_regime_features import compute_features

REGIME_PRODUCER = "spy_regime_engine"
INPUT_SCHEMA_ID = "spy-regime-engine-1-input"


def _thresholds() -> dict[str, str]:
    return {k: str(v) for k, v in sorted(dataclasses.asdict(DEFAULT_THRESHOLDS).items())}


REGIME_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": REGIME_PRODUCER,
        "engine": SCHEMA_VERSION,
        "payload_schemas": [SPY_REGIME_CLASSIFICATION, SPY_REGIME_FEATURES],
        "thresholds": _thresholds(),
    }
)
_SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=SPY_SUBJECT_ID)


def _same(a: Any, b: Any) -> bool:
    return a.model_dump(mode="json") == b.model_dump(mode="json")


def _bar_parents(engine_input: RegimeEngineInput, bar_items: Sequence[EvidenceItem]) -> list[str]:
    """One cited bar fact per input bar, matching it exactly."""
    by_start: dict[str, EvidenceItem] = {}
    for item in bar_items:
        if (
            not isinstance(item, EvidenceItem)
            or item.evidence_kind is not EvidenceKind.CONFIRMED_FACT
            or item.payload_schema_id != MARKET_BAR_FACT
            or item.payload.get("symbol") != "SPY"
            or not item.payload.get("within_regular_hours_window")
        ):
            raise fail("parent_not_a_spy_bar_fact")
        start = item.payload["bar_start_utc"]
        if start in by_start:
            raise fail("duplicate_parent_bar")
        by_start[start] = item
    if len(by_start) != len(engine_input.bars):
        raise fail("lineage_incomplete")
    window = window_subject(engine_input.session_date)
    if any(window not in item.subjects for item in by_start.values()):
        raise fail("parent_window_subject_mismatch")
    for bar in engine_input.bars:
        item = by_start.get(format_utc(bar.timestamp))
        if item is None:
            raise fail("lineage_incomplete")
        payload = item.payload
        for field in ("open", "high", "low", "close"):
            if Decimal(payload[field]) != getattr(bar, field):
                raise fail("parent_bar_mismatch")
        if payload["volume"] != bar.volume:
            raise fail("parent_bar_mismatch")
    return [item.item_id for item in by_start.values()]


def _observed(parents: Sequence[EvidenceItem]):
    times = [p.provenance.source_observed_at_utc for p in parents]
    if any(t is None for t in times):
        raise fail("parent_not_observed")
    return min(times)


def adapt_regime_features(
    features: Any,
    *,
    engine_input: RegimeEngineInput,
    bar_items: Sequence[EvidenceItem],
    context: AdapterContext,
    supplementary_parents: Sequence[EvidenceItem] = (),
) -> EvidenceItem:
    """Already-computed ``RegimeFeatures`` as a ``deterministic_calculation``.
    ``bar_items`` must cite every input bar; prior-day levels or a volume
    baseline, when used, need at least one ``supplementary_parents`` item."""
    require_configuration(context, REGIME_CONFIGURATION)
    require_price_schema(context.registry, SPY_REGIME_FEATURES)
    if not isinstance(features, RegimeFeatures) or not isinstance(engine_input, RegimeEngineInput):
        raise fail("source_invalid")
    refuse_spy_holdout(features.session_date, engine_input.session_date)
    if not _same(compute_features(engine_input), features):
        raise fail("calculation_mismatch")
    uses_extra = (
        engine_input.prior_day is not None
        or engine_input.same_time_historical_volume_baseline is not None
    )
    if uses_extra != bool(supplementary_parents):
        raise fail("lineage_incomplete")
    for parent in supplementary_parents:
        # Inference never becomes part of a calculation's inputs.
        if not isinstance(parent, EvidenceItem) or parent.evidence_kind not in (
            EvidenceKind.CONFIRMED_FACT,
            EvidenceKind.DETERMINISTIC_CALCULATION,
        ):
            raise fail("parent_kind_not_allowed")
    parent_ids = _bar_parents(engine_input, bar_items)
    parent_ids += [p.item_id for p in supplementary_parents]
    if len(set(parent_ids)) != len(parent_ids):
        raise fail("duplicate_parent_bar")
    reference = CalculationInputReference(
        reference_type="calculation_input",
        input_schema_id=INPUT_SCHEMA_ID,
        input_sha256=canonical_sha256(engine_input.model_dump(mode="json")),
        input_record_count=len(engine_input.bars),
    )
    expected_end = engine_input.bars[-1].timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES)
    if features.as_of_timestamp != expected_end:
        raise fail("calculation_mismatch")
    return build_item(
        context,
        producer_id=REGIME_PRODUCER,
        kind=EvidenceKind.DETERMINISTIC_CALCULATION,
        payload_schema_id=SPY_REGIME_FEATURES,
        payload=features.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[_SPY, window_subject(features.session_date)],
        effective_at=features.as_of_timestamp,
        observed_at=_observed([*bar_items, *supplementary_parents]),
        temporal_scope=EvidenceTemporalScope(session_date=features.session_date),
        references=[reference],
        tier=SourceTier.NOT_APPLICABLE,
        parents=parent_ids,
    )


def adapt_regime_classification(
    result: Any, *, features_item: EvidenceItem, context: AdapterContext
) -> EvidenceItem:
    """An already-computed ``RegimeClassificationResult`` as a
    ``deterministic_calculation`` citing exactly its features item. The
    regime label is copied unchanged; it is never a signal or a setup."""
    require_configuration(context, REGIME_CONFIGURATION)
    require_price_schema(context.registry, SPY_REGIME_CLASSIFICATION)
    if not isinstance(result, RegimeClassificationResult) or not isinstance(
        features_item, EvidenceItem
    ):
        raise fail("source_invalid")
    if (
        features_item.evidence_kind is not EvidenceKind.DETERMINISTIC_CALCULATION
        or features_item.payload_schema_id != SPY_REGIME_FEATURES
        or features_item.provenance.producer_id != REGIME_PRODUCER
    ):
        raise fail("parent_not_regime_features")
    features = RegimeFeatures.model_validate(features_item.payload)
    refuse_spy_holdout(features.session_date, result.session_date)
    if not _same(classify(features, DEFAULT_THRESHOLDS), result):
        raise fail("calculation_mismatch")
    reference = CalculationInputReference(
        reference_type="calculation_input",
        input_schema_id=SPY_REGIME_FEATURES,
        input_sha256=canonical_sha256(features_item.payload),
        input_record_count=1,
    )
    return build_item(
        context,
        producer_id=REGIME_PRODUCER,
        kind=EvidenceKind.DETERMINISTIC_CALCULATION,
        payload_schema_id=SPY_REGIME_CLASSIFICATION,
        payload=result.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[_SPY, window_subject(result.session_date)],
        effective_at=result.as_of_timestamp,
        observed_at=_observed([features_item]),
        temporal_scope=EvidenceTemporalScope(session_date=result.session_date),
        references=[reference],
        tier=SourceTier.NOT_APPLICABLE,
        parents=[features_item.item_id],
    )
