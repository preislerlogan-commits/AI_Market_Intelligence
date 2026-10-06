"""Committed, deterministic synthetic scenarios for the offline dashboard prototype.

**Everything here is synthetic.** No value is market data. Sessions are dated
January 2027, outside the sealed SPY holdout window (2026-09-23 through
2026-12-04), except the one scenario that deliberately records an in-window
item to show that the bundle builder withholds it.

Every object is built through the reviewed contracts, never hand-assembled:
items are sealed with ``seal_item``, bundles come from ``build_bundle``, cards
from ``build_setup_card`` / ``build_no_qualified_setup_card``, and contract
sets from the real deterministic ``select_eligible_contracts``. Nothing here
opens a database, reads a file, calls a provider or a model, or uses the
network.

The setup definition used by the card scenarios is named
``synthetic-only-...`` and exists only in memory to exercise future contract
behavior. It is never registered: the production definition table
(``REGISTERED_SETUP_DEFINITIONS``) stays empty, and the dashboard labels every
card built from it as synthetic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    ContractSelectorResult,
    OptionChainBatch,
    OptionContractQuote,
    OptionType,
    SelectorConfig,
)
from market_intelligence.contract_selection.selector import select_eligible_contracts
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import FIXED_NOTES
from market_intelligence.evidence.bundle import (
    RecordedConflict,
    RecordedItem,
    build_bundle,
    query_request_sha256,
)
from market_intelligence.evidence.contracts import (
    ORIGINAL_REVISION,
    CalculationInputReference,
    DocumentReference,
    EvidenceAvailability,
    EvidenceBundleManifest,
    EvidenceConflict,
    EvidenceConflictContent,
    EvidenceConsumerContext,
    EvidenceInferenceBasis,
    EvidenceItem,
    EvidenceItemContent,
    EvidenceProvenance,
    EvidenceQualityProfile,
    EvidenceQuery,
    EvidenceResearchReference,
    EvidenceSubject,
    EvidenceTemporalScope,
    ModelRunReference,
    ResearchResultReference,
    seal_conflict,
    seal_item,
    session_subject,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    CalculationCompleteness,
    ClaimBasis,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    ConsumerId,
    ConsumerPermission,
    DataQuality,
    DetectionMethod,
    DirectionalAuthority,
    DirectionalContent,
    EvidenceKind,
    GradedStrength,
    HoldoutState,
    ImplementationStatus,
    InferenceMethod,
    InferenceSupport,
    LocatorKind,
    OffHoursMode,
    ProducerType,
    ProductionPath,
    ResearchStatus,
    ResultKind,
    SourceBasis,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence.payloads import CORE_PAYLOAD_MODELS
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    CanonicalDecimal,
    Token,
    UtcTimestamp,
    format_utc,
)
from market_intelligence.evidence.registry import (
    BundleRequirement,
    ClockPolicy,
    ConsumerGrant,
    EmissionRule,
    EvidenceRegistry,
    FreshnessPolicy,
    PayloadSchemaEntry,
    ProducerEntry,
    SelectionRule,
)
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_SELECTOR_PAYLOAD_ID,
    CONTRACT_SELECTOR_PRODUCER_ID,
)
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon
from market_intelligence.setup_cards.builder import (
    build_no_qualified_setup_card,
    build_setup_card,
)
from market_intelligence.setup_cards.contracts import CardProvenance, SetupCard
from market_intelligence.setup_cards.definitions import (
    SETUP_EVALUATION_PAYLOAD,
    SetupDefinition,
    SetupEvaluationPayload,
)
from market_intelligence.setup_cards.enums import (
    CardKind,
    CardRevisionReason,
    Lane,
    NoSetupReason,
    SetupLifecycle,
    SetupQualification,
)
from market_intelligence.setup_cards.validation import CardContext

_EASTERN = ZoneInfo("America/New_York")

COMMIT = "a" * 40
SESSION = date(2027, 1, 12)
T0 = datetime(2027, 1, 12, 15, 0, tzinfo=UTC)  # 10:00 America/New_York
AS_OF = T0 + timedelta(minutes=1)
PREMARKET_AS_OF = datetime(2027, 1, 12, 13, 30, tzinfo=UTC)  # 08:30 America/New_York
PRIOR_CLOSE = datetime(2027, 1, 11, 21, 0, tzinfo=UTC)  # 16:00 America/New_York
HOLDOUT_T0 = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # inside the sealed window

SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id="instrument:us_equity:SPY")
STUDY = EvidenceSubject(
    subject_type=SubjectType.RESEARCH_STUDY, subject_id="research:spy_vwap_reversion"
)
CLOCK = EvidenceSubject(subject_type=SubjectType.SYSTEM_COMPONENT, subject_id="system:clock")

BARS = "synthetic_bars"
CALC = "synthetic_session_calc"
PRIOR_CALC = "synthetic_prior_session_calc"
AGENT = "synthetic_context_agent"
RESEARCH = "spy_vwap_confirmation_result"
CLOCK_PRODUCER = "system_clock_health"
DETECTOR = "synthetic_conflict_detector"
EVALUATOR = "synthetic_setup_evaluator"

SYNTHETIC_DEFINITION = SetupDefinition(
    setup_definition_id="synthetic-only-vwap-def",
    lane=Lane.VWAP_REVERSION,
    evaluation_producer_id=EVALUATOR,
)
SETUP_SUBJECT = "setup:vwap_reversion:synthetic_only_def:SPY:20270112T150000Z"


# --- Synthetic payload schemas (prototype only; not production adapters) ------------------


class SyntheticBarFact(BaseModel):
    model_config = STRICT_FROZEN

    bar_timestamp: UtcTimestamp
    close: CanonicalDecimal


class SyntheticSessionCalc(BaseModel):
    model_config = STRICT_FROZEN

    metric: Token
    value: CanonicalDecimal


class SyntheticContextInference(BaseModel):
    model_config = STRICT_FROZEN

    claim_code: Token


class SyntheticResearch(BaseModel):
    model_config = STRICT_FROZEN

    label: Token


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    **CORE_PAYLOAD_MODELS,
    "synthetic_bar_fact.v1": SyntheticBarFact,
    "synthetic_session_calc.v1": SyntheticSessionCalc,
    "synthetic_context_inference.v1": SyntheticContextInference,
    "synthetic_research.v1": SyntheticResearch,
    SETUP_EVALUATION_PAYLOAD: SetupEvaluationPayload,
}


# --- Synthetic registry ---------------------------------------------------------------------

K = EvidenceKind
ALL_READS = sorted(
    [
        ConsumerPermission.READ_CALCULATIONS,
        ConsumerPermission.READ_CONFLICTS,
        ConsumerPermission.READ_FACTS,
        ConsumerPermission.READ_INFERENCES,
        ConsumerPermission.READ_RESEARCH,
    ]
)


def _rule(kind, payload, policy, eligible) -> EmissionRule:
    return EmissionRule(
        evidence_kind=kind,
        payload_schema_id=payload,
        freshness_policy_id=policy,
        machine_decision_eligible=eligible,
    )


def _producer(producer_id, ptype, rules, subjects, *, paths=None, reasons=()) -> ProducerEntry:
    return ProducerEntry(
        producer_id=producer_id,
        producer_versions=["1.0.0"],
        producer_type=ptype,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        production_paths=paths or [ProductionPath.DETERMINISTIC],
        directional_authority=DirectionalAuthority.NONE,
        allowed_subject_types=sorted(subjects),
        emission_rules=sorted(rules, key=lambda r: r.model_dump_json()),
        reason_codes=sorted(reasons),
        error_categories=["unexpected_error"],
    )


def _policy(policy_id, kinds, *, timeless=False, current=None, stale=None, tol=None):
    return FreshnessPolicy(
        policy_id=policy_id,
        applicable_kinds=sorted(kinds),
        observed_timestamp_basis="source_observed_at",
        timeless=timeless,
        current_until_seconds=current,
        stale_after_seconds=stale,
        future_tolerance_seconds=tol,
        off_hours_mode=OffHoursMode.NOT_APPLICABLE if timeless else OffHoursMode.ELAPSED_WALL_CLOCK,
        configuration_version="1",
    )


def _requirement(requirement_id, producer_id, kind, payload_schema_id, subject_id=SPY.subject_id):
    return BundleRequirement(
        requirement_id=requirement_id,
        producer_id=producer_id,
        producer_versions=["1.0.0"],
        evidence_kind=kind,
        payload_schema_id=payload_schema_id,
        primary_subject_id=subject_id,
        configuration_identities=["cfg_none"],
    )


BARS_REQUIREMENT = _requirement("req.bars.spy", BARS, K.CONFIRMED_FACT, "synthetic_bar_fact.v1")
CALC_REQUIREMENT = _requirement(
    "req.calc.spy", CALC, K.DETERMINISTIC_CALCULATION, "synthetic_session_calc.v1"
)
PRIOR_REQUIREMENT = _requirement(
    "req.prior_session.spy", PRIOR_CALC, K.DETERMINISTIC_CALCULATION, "synthetic_session_calc.v1"
)
RESEARCH_REQUIREMENT = _requirement(
    "req.research.vwap",
    RESEARCH,
    K.HISTORICAL_RESEARCH_RESULT,
    "synthetic_research.v1",
    subject_id=STUDY.subject_id,
)


def make_registry() -> EvidenceRegistry:
    """The synthetic registry the prototype's bundles are built under. It
    grants the dashboard read-only access to four purposes and nothing else:
    no assistant grant, no machine-decision mode."""
    live_kinds = [K.CONFIRMED_FACT, K.DETERMINISTIC_CALCULATION, K.MISSING_EVIDENCE]
    calc_subjects = [SubjectType.INSTRUMENT, SubjectType.MARKET_SESSION]
    producers = [
        _producer(
            BARS,
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, "synthetic_bar_fact.v1", "fp.bars.v1", True)],
            calc_subjects,
            reasons=("bars_missing", "holdout_restricted"),
        ),
        _producer(
            CALC,
            ProducerType.DETERMINISTIC_ENGINE,
            [_rule(K.DETERMINISTIC_CALCULATION, "synthetic_session_calc.v1", "fp.bars.v1", True)],
            calc_subjects,
        ),
        _producer(
            PRIOR_CALC,
            ProducerType.DETERMINISTIC_ENGINE,
            [
                _rule(
                    K.DETERMINISTIC_CALCULATION,
                    "synthetic_session_calc.v1",
                    "fp.prior_session.v1",
                    True,
                )
            ],
            calc_subjects,
        ),
        _producer(
            AGENT,
            ProducerType.MODEL_AGENT,
            [
                _rule(
                    K.CURRENT_INFERENCE,
                    "synthetic_context_inference.v1",
                    "fp.inference.v1",
                    False,
                )
            ],
            calc_subjects,
            paths=[ProductionPath.DETERMINISTIC, ProductionPath.MODEL_GENERATED],
        ),
        _producer(
            RESEARCH,
            ProducerType.RESEARCH_RESULT_PUBLISHER,
            [
                _rule(
                    K.HISTORICAL_RESEARCH_RESULT,
                    "synthetic_research.v1",
                    "fp.research_timeless.v1",
                    True,
                )
            ],
            [SubjectType.RESEARCH_STUDY],
        ),
        _producer(
            CONTRACT_SELECTOR_PRODUCER_ID,
            ProducerType.DETERMINISTIC_ENGINE,
            [
                _rule(
                    K.DETERMINISTIC_CALCULATION,
                    CONTRACT_SELECTOR_PAYLOAD_ID,
                    "fp.selector.v1",
                    True,
                )
            ],
            [SubjectType.INSTRUMENT],
        ),
        _producer(
            CLOCK_PRODUCER,
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, "clock_health_fact.v1", "fp.clock.v1", True)],
            [SubjectType.SYSTEM_COMPONENT],
        ),
        _producer(
            DETECTOR,
            ProducerType.CONFLICT_DETECTOR,
            [_rule(K.MISSING_EVIDENCE, "missing_evidence.v1", "fp.bars.v1", False)],
            [SubjectType.INSTRUMENT],
        ),
        _producer(
            EVALUATOR,
            ProducerType.DETERMINISTIC_ENGINE,
            [_rule(K.DETERMINISTIC_CALCULATION, SETUP_EVALUATION_PAYLOAD, "fp.bars.v1", True)],
            [SubjectType.INSTRUMENT, SubjectType.MARKET_SESSION, SubjectType.SETUP_CANDIDATE],
        ),
    ]
    schemas = [
        ("synthetic_bar_fact.v1", True),
        ("synthetic_session_calc.v1", True),
        ("synthetic_context_inference.v1", True),
        ("synthetic_research.v1", False),
        (CONTRACT_SELECTOR_PAYLOAD_ID, True),
        ("clock_health_fact.v1", False),
        ("missing_evidence.v1", False),
        (SETUP_EVALUATION_PAYLOAD, True),
    ]
    rules = [
        SelectionRule(
            selection_rule_id="sel.live.v1",
            purpose=BundlePurpose.LIVE_MARKET_STATE,
            requirements=[BARS_REQUIREMENT, CALC_REQUIREMENT],
            max_window_seconds=86_400,
        ),
        SelectionRule(
            selection_rule_id="sel.premarket.v1",
            purpose=BundlePurpose.PREMARKET_BRIEFING,
            requirements=[PRIOR_REQUIREMENT, RESEARCH_REQUIREMENT],
            max_window_seconds=172_800,
        ),
        SelectionRule(
            selection_rule_id="sel.setup.v1",
            purpose=BundlePurpose.SETUP_DETAIL,
            requirements=[BARS_REQUIREMENT, RESEARCH_REQUIREMENT],
            max_window_seconds=86_400,
        ),
    ]
    return EvidenceRegistry(
        registry_label="synthetic-dashboard-prototype",
        producers=sorted(producers, key=lambda p: p.producer_id),
        payload_schemas=sorted(
            [PayloadSchemaEntry(payload_schema_id=s, spy_price_content=p) for s, p in schemas],
            key=lambda s: s.payload_schema_id,
        ),
        freshness_policies=sorted(
            [
                _policy("fp.bars.v1", live_kinds, current=300, stale=900, tol=5),
                _policy("fp.inference.v1", [K.CURRENT_INFERENCE], current=300, stale=900, tol=5),
                _policy(
                    "fp.prior_session.v1",
                    [K.DETERMINISTIC_CALCULATION],
                    current=86_400,
                    stale=172_800,
                    tol=5,
                ),
                _policy("fp.research_timeless.v1", [K.HISTORICAL_RESEARCH_RESULT], timeless=True),
                _policy(
                    "fp.selector.v1", [K.DETERMINISTIC_CALCULATION], current=300, stale=300, tol=0
                ),
                _policy("fp.clock.v1", [K.CONFIRMED_FACT], current=60, stale=60, tol=0),
            ],
            key=lambda p: p.policy_id,
        ),
        clock_policy=ClockPolicy(
            policy_id="clock.synthetic.v1",
            clock_producer_id=CLOCK_PRODUCER,
            max_abs_offset_ms=1000,
            max_sync_age_seconds=3600,
            max_measurement_age_seconds=600,
        ),
        consumer_grants=[
            ConsumerGrant(
                consumer_id=ConsumerId.DASHBOARD,
                purposes=sorted(
                    [
                        BundlePurpose.LIVE_MARKET_STATE,
                        BundlePurpose.PREMARKET_BRIEFING,
                        BundlePurpose.SETUP_DETAIL,
                    ]
                ),
                permissions=ALL_READS,
                machine_decision_mode_allowed=False,
            )
        ],
        selection_rules=sorted(rules, key=lambda r: r.purpose),
    )


# --- Item builders ----------------------------------------------------------------------------


def _quality(basis: SourceBasis, **overrides: Any) -> EvidenceQualityProfile:
    fields: dict[str, Any] = dict(
        data_quality=DataQuality.COMPLETE,
        source_basis=basis,
        source_tier=SourceTier.LICENSED_MARKET_DATA,
        calculation_completeness=CalculationCompleteness.NOT_APPLICABLE,
        inference_support=InferenceSupport.NOT_APPLICABLE,
        research_status=ResearchStatus.NOT_APPLICABLE,
        graded_strength=GradedStrength.CONFIDENCE_NOT_AVAILABLE,
    )
    fields.update(overrides)
    return EvidenceQualityProfile(**fields)


def _provenance(producer_id, ptype, *, generated, observed, refs, parents=(), **overrides):
    fields: dict[str, Any] = dict(
        producer_id=producer_id,
        producer_version="1.0.0",
        producer_type=ptype,
        production_path=ProductionPath.DETERMINISTIC,
        code_commit_sha=COMMIT,
        code_tree_clean=True,
        configuration_identity="cfg_none",
        generated_at_utc=generated,
        source_observed_at_utc=observed,
        source_references=sorted(refs, key=lambda r: r.model_dump_json()),
        parent_evidence_ids=sorted(parents),
    )
    fields.update(overrides)
    return EvidenceProvenance(**fields)


def _calc_ref() -> CalculationInputReference:
    return CalculationInputReference(
        reference_type="calculation_input",
        input_schema_id="synthetic-input-1",
        input_sha256="c" * 64,
        input_record_count=1,
    )


def _document_ref() -> DocumentReference:
    return DocumentReference(
        reference_type="document",
        locator_kind=LocatorKind.REPO_DOCUMENT,
        document_path="docs/EVIDENCE_REGISTRY.md",
        document_commit_sha=COMMIT,
        section_token="clock",
    )


def _model_ref() -> ModelRunReference:
    return ModelRunReference(
        reference_type="model_run",
        provider="synthetic",
        model_name="synthetic-model-1",
        request_schema_id="synthetic-request-1",
        prompt_template_id="synthetic-prompt-1",
        prompt_template_sha256="d" * 64,
        evidence_package_sha256="e" * 64,
        output_sha256="f" * 64,
        response_id_sha256=None,
        input_tokens=None,
        output_tokens=None,
    )


def _session(at: datetime) -> date:
    return at.astimezone(_EASTERN).date()


def bar_item(close_at: datetime = T0, *, close: str = "500.1") -> EvidenceItem:
    """A synthetic five-minute bar fact. Its source reference is a document
    locator, never a database row: the prototype reads no database."""
    session = _session(close_at)
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.CONFIRMED_FACT,
            subjects=[SPY, session_subject(session)],
            effective_at_utc=close_at,
            temporal_scope=EvidenceTemporalScope(session_date=session),
            payload_schema_id="synthetic_bar_fact.v1",
            payload={
                "bar_timestamp": format_utc(close_at - timedelta(minutes=5)),
                "close": close,
            },
            provenance=_provenance(
                BARS,
                ProducerType.SOURCE_ADAPTER,
                generated=close_at + timedelta(seconds=1),
                observed=close_at,
                refs=[_document_ref()],
            ),
            freshness_policy_id="fp.bars.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(SourceBasis.DIRECTLY_OBSERVED),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


def calc_item(
    parents: Sequence[EvidenceItem],
    at: datetime = T0,
    *,
    metric: str = "distance_from_session_vwap",
    value: str = "0.42",
    producer_id: str = CALC,
    policy: str = "fp.bars.v1",
) -> EvidenceItem:
    session = _session(at)
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.DETERMINISTIC_CALCULATION,
            subjects=[SPY, session_subject(session)],
            effective_at_utc=at,
            temporal_scope=EvidenceTemporalScope(session_date=session),
            payload_schema_id="synthetic_session_calc.v1",
            payload={"metric": metric, "value": value},
            provenance=_provenance(
                producer_id,
                ProducerType.DETERMINISTIC_ENGINE,
                generated=at + timedelta(seconds=2),
                observed=min(p.provenance.source_observed_at_utc for p in parents),
                refs=[_calc_ref()],
                parents=[p.item_id for p in parents],
            ),
            freshness_policy_id=policy,
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(
                SourceBasis.DERIVED, calculation_completeness=CalculationCompleteness.COMPLETE
            ),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


def inference_item(
    parents: Sequence[EvidenceItem], at: datetime = T0, *, claim: str = "macro_context_mixed"
) -> EvidenceItem:
    """A labelled, non-directional synthetic inference (no model was called)."""
    parent_ids = sorted(p.item_id for p in parents)
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.CURRENT_INFERENCE,
            subjects=[SPY],
            effective_at_utc=at,
            temporal_scope=EvidenceTemporalScope(),
            payload_schema_id="synthetic_context_inference.v1",
            payload={"claim_code": claim},
            provenance=_provenance(
                AGENT,
                ProducerType.MODEL_AGENT,
                generated=at + timedelta(seconds=3),
                observed=min(p.provenance.source_observed_at_utc for p in parents),
                refs=[_model_ref()],
                parents=parent_ids,
                production_path=ProductionPath.MODEL_GENERATED,
            ),
            freshness_policy_id="fp.inference.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(
                SourceBasis.MODEL_INFERRED, inference_support=InferenceSupport.NOT_ASSESSED
            ),
            inference_basis=EvidenceInferenceBasis(
                inference_method=InferenceMethod.MODEL_STRUCTURED_OUTPUT,
                input_evidence_ids=parent_ids,
                claim_basis=ClaimBasis.SUMMARIZES_INPUTS,
                directional_content=DirectionalContent.NONE,
            ),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=False,
        )
    )


def _confirmation_reference() -> EvidenceResearchReference:
    """The recorded confirmation result's identifying values and fixed
    caveats, copied from committed constants (no result file is read)."""
    return EvidenceResearchReference(
        study_id="spy_vwap_reversion",
        result_kind=ResultKind.CONFIRMATION_RESULT,
        result_schema_version="spy-vwap-reversion-confirmation-1",
        result_sha256="c8d838f441005a4122cda93f4572e8f1b6eca429b130ed545c1fdcb9ad69877b",
        result_document_path="docs/SPY_VWAP_REVERSION_CONFIRMATION_RESULT.md",
        result_document_commit_sha="fa1395c" + "0" * 33,
        protocol_commit_shas=[
            "f77d30f8e6e90a6b77eeca11fd11c3da9c9540c1",
            "1ff654de6dbbd54aeecb471010d1a612ca5abda6",
        ],
        analysis_code_commit_sha="cd587f132b914208ef95c892a74bea68f4bd35ef",
        primary_label="supported_for_further_shadow_research",
        secondary_labels=["below_horizon_dependent"],
        research_status=ResearchStatus.SHADOW_PENDING,
        sample_scope="confirmation",
        holdout_state=HoldoutState.SEALED,
        fixed_caveats=sorted(FIXED_NOTES),
    )


def research_item() -> EvidenceItem:
    reference = _confirmation_reference()
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.HISTORICAL_RESEARCH_RESULT,
            subjects=[STUDY],
            effective_at_utc=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
            temporal_scope=EvidenceTemporalScope(),
            payload_schema_id="synthetic_research.v1",
            payload={"label": reference.primary_label},
            provenance=_provenance(
                RESEARCH,
                ProducerType.RESEARCH_RESULT_PUBLISHER,
                generated=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
                observed=None,
                refs=[
                    ResearchResultReference(
                        reference_type="research_result",
                        study_id=reference.study_id,
                        result_sha256=reference.result_sha256,
                        result_schema_version=reference.result_schema_version,
                        document_path=reference.result_document_path,
                        document_commit_sha=reference.result_document_commit_sha,
                    )
                ],
            ),
            freshness_policy_id="fp.research_timeless.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(SourceBasis.DERIVED, research_status=reference.research_status),
            research_reference=reference,
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


def clock_item(at: datetime = T0, *, offset_ms: int = 5) -> EvidenceItem:
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.CONFIRMED_FACT,
            subjects=[CLOCK],
            effective_at_utc=at,
            temporal_scope=EvidenceTemporalScope(),
            payload_schema_id="clock_health_fact.v1",
            payload={
                "last_sync_at_utc": format_utc(at - timedelta(minutes=5)),
                "offset_ms": offset_ms,
            },
            provenance=_provenance(
                CLOCK_PRODUCER,
                ProducerType.SOURCE_ADAPTER,
                generated=at,
                observed=at,
                refs=[_document_ref()],
            ),
            freshness_policy_id="fp.clock.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(SourceBasis.DIRECTLY_OBSERVED, source_tier=SourceTier.NOT_APPLICABLE),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


def evaluation_item(
    *,
    lifecycle: SetupLifecycle = SetupLifecycle.EVALUATION_COMPLETE,
    qualification: SetupQualification = SetupQualification.QUALIFIED,
    setup_subject_id: str | None = SETUP_SUBJECT,
    at: datetime = T0,
    no_setup_reason: NoSetupReason | None = None,
) -> EvidenceItem:
    """A synthetic ``setup_evaluation.v1`` from the synthetic evaluator, under
    the synthetic-only definition."""
    session = _session(at)
    parent = bar_item(at)
    secondary = [session_subject(session)]
    if setup_subject_id is not None:
        secondary.append(
            EvidenceSubject(subject_type=SubjectType.SETUP_CANDIDATE, subject_id=setup_subject_id)
        )
    payload = SetupEvaluationPayload(
        setup_definition_id=SYNTHETIC_DEFINITION.setup_definition_id,
        lane=Lane.VWAP_REVERSION,
        setup_subject_id=setup_subject_id,
        lifecycle_state=lifecycle,
        qualification=qualification,
        no_setup_reason=no_setup_reason,
    ).model_dump(mode="json")
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.DETERMINISTIC_CALCULATION,
            subjects=[SPY, *sorted(secondary, key=lambda s: s.model_dump_json())],
            effective_at_utc=at,
            temporal_scope=EvidenceTemporalScope(session_date=session),
            payload_schema_id=SETUP_EVALUATION_PAYLOAD,
            payload=payload,
            provenance=_provenance(
                EVALUATOR,
                ProducerType.DETERMINISTIC_ENGINE,
                generated=at + timedelta(seconds=3),
                observed=parent.provenance.source_observed_at_utc,
                refs=[_calc_ref()],
                parents=[parent.item_id],
            ),
            freshness_policy_id="fp.bars.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(
                SourceBasis.DERIVED, calculation_completeness=CalculationCompleteness.COMPLETE
            ),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


# --- The deterministic contract selector over synthetic chains -------------------------------

EXPIRY = date(2027, 1, 12)


def _quote(symbol: str, strike: str, ask: str = "1.10") -> OptionContractQuote:
    return OptionContractQuote(
        contract_symbol=symbol,
        option_type=OptionType.CALL,
        expiration_date=EXPIRY,
        strike_price=Decimal(strike),
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal(ask),
        ask_size=10,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.10"),
        rho=Decimal("0.01"),
    )


def selector_result(*, feed: str = "opra", all_wide: bool = False) -> ContractSelectorResult:
    """A genuine run of the deterministic selector over a synthetic chain.
    Its status, returned sets and rejection counts are the selector's own."""
    wide = "3.00"
    batch = OptionChainBatch(
        provider="alpaca",
        feed=feed,
        retrieved_at=T0 - timedelta(minutes=2),
        contracts=[
            _quote("SPY270112C00680000", "680", wide if all_wide else "1.10"),
            _quote("SPY270112C00681000", "681", wide if all_wide else "1.10"),
            _quote("SPY270112C00682000", "682", wide),
        ],
    )
    selector_input = ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        regime_as_of_timestamp=batch.retrieved_at,
        underlying_price_timestamp=batch.retrieved_at,
        as_of_timestamp=T0,
        underlying_price=Decimal("680"),
        batch=batch,
        config=SelectorConfig(allow_indicative_for_research=feed == "indicative"),
    )
    return select_eligible_contracts(selector_input, generated_at=T0 + timedelta(seconds=1))


def selector_item(result: ContractSelectorResult) -> EvidenceItem:
    return seal_item(
        EvidenceItemContent(
            evidence_kind=K.DETERMINISTIC_CALCULATION,
            subjects=[SPY],
            effective_at_utc=T0,
            temporal_scope=EvidenceTemporalScope(session_date=SESSION),
            payload_schema_id=CONTRACT_SELECTOR_PAYLOAD_ID,
            payload=result.model_dump(mode="json"),
            provenance=_provenance(
                CONTRACT_SELECTOR_PRODUCER_ID,
                ProducerType.DETERMINISTIC_ENGINE,
                generated=T0 + timedelta(seconds=2),
                observed=result.retrieved_at,
                refs=[_calc_ref()],
            ),
            freshness_policy_id="fp.selector.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(
                SourceBasis.DERIVED, calculation_completeness=CalculationCompleteness.COMPLETE
            ),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


# --- Conflicts --------------------------------------------------------------------------------


def _conflict(
    items: Sequence[EvidenceItem], ctype: ConflictType, severity: ConflictSeverity, at: datetime
) -> RecordedConflict:
    conflict = seal_conflict(
        EvidenceConflictContent(
            conflict_type=ctype,
            involved_item_ids=sorted(i.item_id for i in items),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=DETECTOR,
            detector_version="1.0.0",
            severity=severity,
            status=ConflictStatus.UNRESOLVED,
            evaluated_as_of_utc=at + timedelta(seconds=30),
            detected_at_utc=at + timedelta(seconds=31),
        )
    )
    return RecordedConflict(conflict=conflict, recorded_at_utc=at + timedelta(seconds=40))


# --- Bundles ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleInput:
    """One Evidence Bundle with exactly the items and conflict records it
    names. Nothing outside the bundle is carried, so withheld (for example
    holdout-restricted) items never reach the presentation layer."""

    bundle: EvidenceBundleManifest
    items: Mapping[str, EvidenceItem]
    conflicts: Mapping[str, EvidenceConflict]


def _build(
    registry: EvidenceRegistry,
    purpose: BundlePurpose,
    as_of: datetime,
    items: Sequence[EvidenceItem],
    *,
    conflicts: Sequence[RecordedConflict] = (),
    recorded_at: datetime = T0,
    window: timedelta = timedelta(hours=6),
) -> BundleInput:
    query = EvidenceQuery(
        effective_from_utc=as_of - window,
        effective_to_utc=as_of + window,
        max_entries=100,
    )
    context = EvidenceConsumerContext(
        consumer_id=ConsumerId.DASHBOARD,
        purpose=purpose,
        permissions=ALL_READS,
        machine_decision_mode=False,
        request_sha256=query_request_sha256(query),
    )
    bundle = build_bundle(
        registry=registry,
        purpose=purpose,
        as_of=as_of,
        consumer_context=context,
        query=query,
        recorded_items=[
            RecordedItem(item=i, recorded_at_utc=recorded_at + timedelta(seconds=5)) for i in items
        ],
        recorded_conflicts=conflicts,
        built_at=as_of + timedelta(seconds=1),
    )
    by_id = {i.item_id: i for i in items}
    records = {r.conflict.conflict_id: r.conflict for r in conflicts}
    return BundleInput(
        bundle=bundle,
        items={e.item_id: by_id[e.item_id] for e in bundle.entries},
        conflicts={cid: records[cid] for cid in bundle.conflict_ids},
    )


def card_context(
    registry: EvidenceRegistry,
    bundle_input: BundleInput,
    definitions: Sequence[SetupDefinition],
) -> CardContext:
    return CardContext(
        bundle=bundle_input.bundle,
        items=bundle_input.items,
        conflicts=bundle_input.conflicts,
        registry=registry,
        setup_definitions=tuple(definitions),
    )


PROVENANCE = CardProvenance(
    card_builder_id="synthetic-card-builder",
    card_builder_version="1.0.0",
    code_commit_sha=COMMIT,
    code_tree_clean=True,
    configuration_identity="cfg_none",
    generated_at_utc=AS_OF + timedelta(seconds=5),
)


# --- Scenarios --------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardRecord:
    """One sealed card and the exact bundle it was built from."""

    card: SetupCard
    source: BundleInput


@dataclass(frozen=True)
class LaneInput:
    """The VWAP-reversion lane's synthetic inputs. ``definitions`` is empty
    in the production-truthful scenario; otherwise it holds only the
    in-memory ``synthetic-only-`` definition. ``history`` is append-only:
    the last card supersedes the earlier ones."""

    context: BundleInput
    definitions: tuple[SetupDefinition, ...]
    history: tuple[CardRecord, ...] = ()


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    title: str
    summary: str
    market: BundleInput
    premarket: BundleInput
    vwap: LaneInput
    registry: EvidenceRegistry = field(repr=False)


def _market(registry: EvidenceRegistry, variant: str = "normal") -> BundleInput:
    """A ``live_market_state`` bundle. Its normal form holds a bar fact, a
    session calculation, a labelled non-directional inference, a clock fact
    and one unresolved material conflict between the inference and the
    calculation."""
    bar = bar_item()
    calc = calc_item([bar])
    inference = inference_item([bar, calc])
    clock = clock_item()
    items: list[EvidenceItem] = [bar, calc, inference, clock]
    conflicts = [
        _conflict(
            [calc, inference], ConflictType.INFERENCE_VS_OBSERVATION, ConflictSeverity.MATERIAL, T0
        )
    ]
    as_of = AS_OF
    if variant == "stale":
        as_of = T0 + timedelta(minutes=20)
        items = [bar, calc, inference, clock_item(as_of)]
    elif variant == "missing":
        items = [calc, inference, clock]
    elif variant == "ambiguous":
        items.append(bar_item(close="500.2"))
    elif variant == "clock":
        items.append(clock_item(offset_ms=5000))
    elif variant == "critical":
        earlier = bar_item(T0 - timedelta(minutes=5))
        items.append(earlier)
        conflicts.append(
            _conflict(
                [bar, earlier], ConflictType.PROVIDER_DISAGREEMENT, ConflictSeverity.CRITICAL, T0
            )
        )
    elif variant == "holdout":
        restricted = bar_item(HOLDOUT_T0, close="611.11")
        items = [restricted, calc, inference, clock]
    return _build(registry, BundlePurpose.LIVE_MARKET_STATE, as_of, items, conflicts=conflicts)


def _premarket(registry: EvidenceRegistry) -> BundleInput:
    """A ``premarket_briefing`` bundle: the prior session's calculation, the
    recorded research result, a labelled inference and a clock fact."""
    prior_bar = bar_item(PRIOR_CLOSE, close="499.8")
    prior_calc = calc_item(
        [prior_bar],
        PRIOR_CLOSE,
        metric="prior_session_range",
        value="3.15",
        producer_id=PRIOR_CALC,
        policy="fp.prior_session.v1",
    )
    overnight = inference_item(
        [prior_calc], PREMARKET_AS_OF - timedelta(minutes=2), claim="overnight_context_quiet"
    )
    items = [prior_calc, research_item(), overnight, clock_item(PREMARKET_AS_OF)]
    return _build(
        registry,
        BundlePurpose.PREMARKET_BRIEFING,
        PREMARKET_AS_OF,
        items,
        recorded_at=PREMARKET_AS_OF - timedelta(minutes=1),
        window=timedelta(hours=20),
    )


def _setup_items(evaluation: EvidenceItem | None = None) -> dict[str, EvidenceItem]:
    bar = bar_item()
    calc = calc_item([bar])
    items = {
        "bar": bar,
        "calc": calc,
        "research": research_item(),
        "clock": clock_item(),
        "inference": inference_item([bar, calc], claim="volatility_context_elevated"),
    }
    if evaluation is not None:
        items["evaluation"] = evaluation
    return items


def _setup_card(
    registry: EvidenceRegistry,
    source: BundleInput,
    items: Mapping[str, EvidenceItem],
    *,
    supporting: Sequence[str] = ("bar",),
    selector: bool = True,
    prior: SetupCard | None = None,
    reason: CardRevisionReason | None = None,
    provenance: CardProvenance = PROVENANCE,
) -> CardRecord:
    """A synthetic setup card. The calculation is cited as contradicting
    evidence and the inference only as labelled context."""
    ctx = card_context(registry, source, (SYNTHETIC_DEFINITION,))
    card = build_setup_card(
        ctx,
        card_kind=CardKind.SETUP,
        lane=Lane.VWAP_REVERSION,
        provenance=provenance,
        setup_subject_id=SETUP_SUBJECT,
        supporting_ids=[items[k].item_id for k in supporting if items[k].item_id in source.items],
        contradicting_ids=[items["calc"].item_id],
        context_ids=[items["research"].item_id, items["inference"].item_id],
        selector_requested=selector,
        prior_card=prior,
        revision_reason=reason,
    )
    return CardRecord(card=card, source=source)


def _card_scenario(
    registry: EvidenceRegistry,
    *,
    extra: Sequence[EvidenceItem] = (),
    drop: Sequence[str] = (),
    conflicts_for=None,
    as_of: datetime = AS_OF,
    clock_at: datetime | None = None,
    selector: ContractSelectorResult | None = None,
    request_selector: bool = True,
) -> LaneInput:
    items = _setup_items(evaluation_item())
    if clock_at is not None:
        items["clock"] = clock_item(clock_at)
    chosen = [v for k, v in items.items() if k not in drop]
    if selector is not None:
        chosen.append(selector_item(selector))
    conflicts = conflicts_for(items) if conflicts_for else []
    source = _build(
        registry, BundlePurpose.SETUP_DETAIL, as_of, [*chosen, *extra], conflicts=conflicts
    )
    record = _setup_card(registry, source, items, selector=request_selector)
    return LaneInput(context=source, definitions=(SYNTHETIC_DEFINITION,), history=(record,))


def _critical_conflicts(items: Mapping[str, EvidenceItem]) -> list[RecordedConflict]:
    earlier = bar_item(T0 - timedelta(minutes=5))
    return [
        _conflict(
            [items["bar"], earlier],
            ConflictType.PROVIDER_DISAGREEMENT,
            ConflictSeverity.CRITICAL,
            T0,
        )
    ]


def _not_authorized(registry: EvidenceRegistry) -> LaneInput:
    """The production-truthful lane: no setup definition is registered."""
    items = _setup_items()
    source = _build(registry, BundlePurpose.SETUP_DETAIL, AS_OF, list(items.values()))
    return LaneInput(context=source, definitions=())


def _no_qualified_setup(registry: EvidenceRegistry) -> LaneInput:
    conclusion = evaluation_item(
        setup_subject_id=None,
        qualification=SetupQualification.NOT_QUALIFIED,
        no_setup_reason=NoSetupReason.CRITERIA_NOT_MET,
    )
    items = _setup_items(conclusion)
    source = _build(registry, BundlePurpose.SETUP_DETAIL, AS_OF, list(items.values()))
    result = build_no_qualified_setup_card(
        card_context(registry, source, (SYNTHETIC_DEFINITION,)),
        lane=Lane.VWAP_REVERSION,
        provenance=PROVENANCE,
        supporting_ids=[items["bar"].item_id],
        context_ids=[items["research"].item_id],
    )
    assert result.card is not None  # the synthetic conclusion is ready by construction
    return LaneInput(
        context=source,
        definitions=(SYNTHETIC_DEFINITION,),
        history=(CardRecord(card=result.card, source=source),),
    )


def _superseded(registry: EvidenceRegistry) -> LaneInput:
    """Two revisions of one synthetic setup: a developing observation, then
    its completed evaluation. The first is superseded; the second is outdated
    because the view-age policy is unset."""
    records: list[CardRecord] = []
    prior = None
    for minutes, lifecycle, qualification in (
        (0, SetupLifecycle.DEVELOPING, SetupQualification.NOT_EVALUATED),
        (5, SetupLifecycle.EVALUATION_COMPLETE, SetupQualification.QUALIFIED),
    ):
        at = T0 + timedelta(minutes=minutes)
        bar = bar_item(at)
        calc = calc_item([bar], at)
        items = {
            "bar": bar,
            "calc": calc,
            "research": research_item(),
            "clock": clock_item(at),
            "inference": inference_item([bar, calc], at, claim="volatility_context_elevated"),
            "evaluation": evaluation_item(lifecycle=lifecycle, qualification=qualification, at=at),
        }
        source = _build(
            registry,
            BundlePurpose.SETUP_DETAIL,
            at + timedelta(minutes=1),
            list(items.values()),
            recorded_at=at,
        )
        record = _setup_card(
            registry,
            source,
            items,
            selector=False,
            prior=prior,
            reason=None if prior is None else CardRevisionReason.LIFECYCLE_CHANGE,
            provenance=PROVENANCE.model_copy(
                update={"generated_at_utc": at + timedelta(minutes=1, seconds=5)}
            ),
        )
        records.append(record)
        prior = record.card
    return LaneInput(
        context=records[-1].source, definitions=(SYNTHETIC_DEFINITION,), history=tuple(records)
    )


SCENARIO_ORDER = (
    "no_authorized_live_qualification",
    "ready_for_manual_review",
    "no_qualified_setup",
    "stale_required_evidence",
    "missing_evidence",
    "ambiguous_evidence",
    "ambiguous_clock_facts",
    "unresolved_critical_conflict",
    "selector_unavailable",
    "no_eligible_contracts",
    "research_only_contracts",
    "outdated_and_superseded",
    "holdout_restricted",
)


@cache
def build_scenarios() -> dict[str, Scenario]:
    """Every synthetic scenario, in display order. Deterministic: the same
    inputs always produce the same bundle and card identities."""
    registry = make_registry()
    premarket = _premarket(registry)
    normal = _market(registry)
    eligible = selector_result()

    def scenario(scenario_id, title, summary, vwap, market=normal) -> Scenario:
        return Scenario(scenario_id, title, summary, market, premarket, vwap, registry)

    later = T0 + timedelta(minutes=20)
    built = [
        scenario(
            "no_authorized_live_qualification",
            "No authorized live qualification",
            "Production-truthful: no setup definition is registered, so no card exists.",
            _not_authorized(registry),
        ),
        scenario(
            "ready_for_manual_review",
            "Ready for manual review (synthetic definition)",
            "A synthetic qualified setup with current evidence and eligible OPRA contracts.",
            _card_scenario(registry, selector=eligible),
        ),
        scenario(
            "no_qualified_setup",
            "No qualified setup (synthetic conclusion)",
            "An authorized synthetic lane evaluation concluded not_qualified.",
            _no_qualified_setup(registry),
        ),
        scenario(
            "stale_required_evidence",
            "Stale required evidence",
            "The required bar is beyond its stale boundary at the bundle as-of time.",
            _card_scenario(registry, as_of=later, clock_at=later, selector=eligible),
            _market(registry, "stale"),
        ),
        scenario(
            "missing_evidence",
            "Missing required evidence",
            "No bar fact was recorded for the required bars requirement.",
            _card_scenario(registry, drop=("bar",), selector=eligible),
            _market(registry, "missing"),
        ),
        scenario(
            "ambiguous_evidence",
            "Ambiguous evidence",
            "Two competing bar facts share the same time; no winner is chosen.",
            _card_scenario(registry, extra=[bar_item(close="500.2")], selector=eligible),
            _market(registry, "ambiguous"),
        ),
        scenario(
            "ambiguous_clock_facts",
            "Ambiguous clock facts",
            "Two clock-health facts disagree at the same time; freshness is unknown.",
            _card_scenario(registry, extra=[clock_item(offset_ms=5000)], selector=eligible),
            _market(registry, "clock"),
        ),
        scenario(
            "unresolved_critical_conflict",
            "Unresolved critical conflict",
            "A provider disagreement involving the required bar is unresolved.",
            _card_scenario(
                registry,
                extra=[bar_item(T0 - timedelta(minutes=5))],
                conflicts_for=_critical_conflicts,
                selector=eligible,
            ),
            _market(registry, "critical"),
        ),
        scenario(
            "selector_unavailable",
            "Contract selector unavailable",
            "The selector was requested but no valid selector result exists.",
            _card_scenario(registry),
        ),
        scenario(
            "no_eligible_contracts",
            "No eligible contracts",
            "The selector ran and rejected every contract; only rejection counts are shown.",
            _card_scenario(registry, selector=selector_result(all_wide=True)),
        ),
        scenario(
            "research_only_contracts",
            "Research-only contracts",
            "An indicative-feed selector run: research-only contracts, never eligible.",
            _card_scenario(registry, selector=selector_result(feed="indicative")),
        ),
        scenario(
            "outdated_and_superseded",
            "Outdated and superseded cards",
            "A developing card superseded by its completed evaluation.",
            _superseded(registry),
        ),
        scenario(
            "holdout_restricted",
            "Holdout restricted",
            "The only bar falls in the sealed holdout window and is withheld.",
            _card_scenario(
                registry,
                drop=("bar",),
                extra=[bar_item(HOLDOUT_T0, close="611.11")],
                selector=eligible,
            ),
            _market(registry, "holdout"),
        ),
    ]
    scenarios = {s.scenario_id: s for s in built}
    assert tuple(scenarios) == SCENARIO_ORDER
    return scenarios
