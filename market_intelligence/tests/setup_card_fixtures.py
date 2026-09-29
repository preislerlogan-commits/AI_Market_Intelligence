"""Synthetic fixtures for the ``setup-card-1`` core tests.

Everything is synthetic and in memory. Sessions are January 2027, outside
the sealed SPY holdout window, except where a test deliberately builds an
in-window item to prove the holdout guard blocks readiness.

The synthetic setup definition is named ``synthetic-only-...`` and is passed
explicitly to each card context. It exists only to exercise the future
contract behavior; it never implies that a live setup definition or
qualification authority exists (production's table is empty).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.contracts import (
    ORIGINAL_REVISION,
    EvidenceAvailability,
    EvidenceItem,
    EvidenceItemContent,
    EvidenceSubject,
    EvidenceTemporalScope,
    seal_item,
    session_subject,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    CalculationCompleteness,
    EvidenceKind,
    ProducerType,
    SourceBasis,
    SubjectType,
)
from market_intelligence.evidence.registry import PayloadSchemaEntry, SelectionRule
from market_intelligence.setup_cards.contracts import CardProvenance
from market_intelligence.setup_cards.definitions import (
    SETUP_EVALUATION_PAYLOAD,
    SetupDefinition,
    SetupEvaluationPayload,
)
from market_intelligence.setup_cards.enums import (
    Lane,
    NoSetupReason,
    SetupLifecycle,
    SetupQualification,
)
from market_intelligence.setup_cards.validation import CardContext
from market_intelligence.tests import evidence_fixtures as f

EVALUATOR = "synthetic_setup_evaluator"
SYNTHETIC_DEFINITION_ID = "synthetic-only-vwap-def"
SYNTHETIC_DEFINITION = SetupDefinition(
    setup_definition_id=SYNTHETIC_DEFINITION_ID,
    lane=Lane.VWAP_REVERSION,
    evaluation_producer_id=EVALUATOR,
)
SETUP_SUBJECT = "setup:vwap_reversion:synthetic_only_def:SPY:20270112T150000Z"
AS_OF = f.T0 + timedelta(minutes=1)

PAYLOAD_MODELS = {**f.PAYLOAD_MODELS, SETUP_EVALUATION_PAYLOAD: SetupEvaluationPayload}

RESEARCH_REQUIREMENT = f.requirement(
    "req.research.vwap",
    f.RESEARCH,
    EvidenceKind.HISTORICAL_RESEARCH_RESULT,
    "synthetic_research.v1",
    subject_id="research:spy_vwap_reversion",
)


def make_card_registry(**overrides: Any):
    base = f.make_registry()
    producers = sorted(
        [
            *base.producers,
            f._producer(
                EVALUATOR,
                ProducerType.DETERMINISTIC_ENGINE,
                [
                    f._rule(
                        EvidenceKind.DETERMINISTIC_CALCULATION,
                        SETUP_EVALUATION_PAYLOAD,
                        "fp.bars.v1",
                        True,
                    )
                ],
                [SubjectType.INSTRUMENT, SubjectType.MARKET_SESSION, SubjectType.SETUP_CANDIDATE],
            ),
        ],
        key=lambda p: p.producer_id,
    )
    schemas = sorted(
        [
            *base.payload_schemas,
            PayloadSchemaEntry(payload_schema_id=SETUP_EVALUATION_PAYLOAD, spy_price_content=True),
        ],
        key=lambda s: s.payload_schema_id,
    )
    rules = sorted(
        [
            *(r for r in base.selection_rules if r.purpose is not BundlePurpose.SETUP_DETAIL),
            SelectionRule(
                selection_rule_id="sel.setup.card.v1",
                purpose=BundlePurpose.SETUP_DETAIL,
                requirements=[f.BARS_REQUIREMENT, RESEARCH_REQUIREMENT],
                max_window_seconds=86_400,
            ),
        ],
        key=lambda r: r.purpose,
    )
    fields: dict[str, Any] = dict(
        producers=producers, payload_schemas=schemas, selection_rules=rules
    )
    fields.update(overrides)
    return f.make_registry(**fields)


REGISTRY = make_card_registry()


def _secondary(*subjects: EvidenceSubject) -> list[EvidenceSubject]:
    return sorted(subjects, key=lambda s: canonical_json_bytes(s.model_dump(mode="json")))


def evaluation_item(
    *,
    lifecycle: SetupLifecycle | None = SetupLifecycle.EVALUATION_COMPLETE,
    qualification: SetupQualification = SetupQualification.QUALIFIED,
    setup_subject_id: str | None = SETUP_SUBJECT,
    at: datetime = f.T0,
    expires_at: datetime | None = None,
    parents: list[EvidenceItem] | None = None,
    producer_id: str = EVALUATOR,
    definition_id: str = SYNTHETIC_DEFINITION_ID,
    lane: Lane = Lane.VWAP_REVERSION,
    no_setup_reason: NoSetupReason | None = None,
) -> EvidenceItem:
    parents = parents if parents is not None else [f.bar_item(at)]
    session = at.astimezone(f._eastern()).date()
    secondary = [session_subject(session)]
    if setup_subject_id is not None:
        secondary.append(
            EvidenceSubject(subject_type=SubjectType.SETUP_CANDIDATE, subject_id=setup_subject_id)
        )
    payload = SetupEvaluationPayload(
        setup_definition_id=definition_id,
        lane=lane,
        setup_subject_id=setup_subject_id,
        lifecycle_state=lifecycle,
        qualification=qualification,
        no_setup_reason=no_setup_reason,
        expires_at_utc=expires_at,
    ).model_dump(mode="json")
    return seal_item(
        EvidenceItemContent(
            evidence_kind=EvidenceKind.DETERMINISTIC_CALCULATION,
            subjects=[f.SPY, *_secondary(*secondary)],
            effective_at_utc=at,
            temporal_scope=EvidenceTemporalScope(session_date=session),
            payload_schema_id=SETUP_EVALUATION_PAYLOAD,
            payload=payload,
            provenance=f._provenance(
                producer_id,
                ProducerType.DETERMINISTIC_ENGINE,
                generated=at + timedelta(seconds=3),
                observed=min(p.provenance.source_observed_at_utc for p in parents),
                refs=[f.calc_ref()],
                parents=[p.item_id for p in parents],
            ),
            freshness_policy_id="fp.bars.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=f._quality(
                SourceBasis.DERIVED, calculation_completeness=CalculationCompleteness.COMPLETE
            ),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


PROVENANCE = CardProvenance(
    card_builder_id="synthetic-card-builder",
    card_builder_version="1.0.0",
    code_commit_sha=f.COMMIT,
    code_tree_clean=True,
    configuration_identity="cfg_none",
    generated_at_utc=AS_OF + timedelta(seconds=5),
)


def card_context(
    *items: EvidenceItem,
    conflicts=(),
    as_of: datetime = AS_OF,
    registry=REGISTRY,
    definitions=(SYNTHETIC_DEFINITION,),
    recorded_at: datetime = f.T0,
    query=None,
) -> CardContext:
    q = query or f.query(
        effective_from_utc=as_of - timedelta(hours=6), effective_to_utc=as_of + timedelta(hours=6)
    )
    manifest = f.bundle(
        f.recorded(*items, at=recorded_at),
        as_of=as_of,
        purpose=BundlePurpose.SETUP_DETAIL,
        registry=registry,
        q=q,
        conflicts=conflicts,
    )
    by_id = {item.item_id: item for item in items}
    conflict_records = {record.conflict.conflict_id: record.conflict for record in conflicts}
    return CardContext(
        bundle=manifest,
        items=by_id,
        conflicts={cid: conflict_records[cid] for cid in manifest.conflict_ids},
        registry=registry,
        setup_definitions=definitions,
    )


def core_items(at: datetime = f.T0) -> dict[str, EvidenceItem]:
    """A bar, a research result, a clock fact and a qualified evaluation."""
    bar = f.bar_item(at)
    return {
        "bar": bar,
        "research": f.research_item(),
        "clock": f.clock_item(at),
        "evaluation": evaluation_item(parents=[bar], at=at),
    }


def lane_conclusion(
    *,
    reason: NoSetupReason = NoSetupReason.CRITERIA_NOT_MET,
    lifecycle: SetupLifecycle = SetupLifecycle.EVALUATION_COMPLETE,
    at: datetime = f.T0,
    **overrides: Any,
) -> EvidenceItem:
    """A synthetic lane-level ``setup_evaluation.v1`` concluding that nothing
    qualified (``not_qualified``), from the synthetic evaluator."""
    return evaluation_item(
        setup_subject_id=None,
        lifecycle=lifecycle,
        qualification=SetupQualification.NOT_QUALIFIED,
        no_setup_reason=reason,
        at=at,
        **overrides,
    )
