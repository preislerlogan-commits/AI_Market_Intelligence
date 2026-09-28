"""Synthetic, in-memory fixtures for the Evidence Envelope core tests.

Every value here is synthetic. Sessions are dated January 2027, outside the
sealed SPY holdout window (2026-09-23 -> 2026-12-04), unless a test builds an
in-window item on purpose to prove the holdout guard refuses it. Nothing here
reads the project database, a provider, a model, or any file.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

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
from market_intelligence.evidence.bundle import (
    RecordedItem,
    build_bundle,
    query_request_sha256,
)
from market_intelligence.evidence.contracts import (
    ORIGINAL_REVISION,
    CalculationInputReference,
    DuckDbRowReference,
    EvidenceAvailability,
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
    PrimaryKeyComponent,
    ResearchResultReference,
    seal_item,
    session_subject,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    CalculationCompleteness,
    ClaimBasis,
    ConsumerId,
    ConsumerPermission,
    DataQuality,
    DirectionalAuthority,
    DirectionalContent,
    DuckDbTable,
    EvidenceKind,
    GradedStrength,
    HoldoutState,
    ImplementationStatus,
    InferenceMethod,
    InferenceSupport,
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
from market_intelligence.market_features.spy_regime_contracts import ScenarioHorizon

COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
SESSION = date(2027, 1, 12)
T0 = datetime(2027, 1, 12, 15, 0, tzinfo=UTC)  # 10:00 America/New_York
HOLDOUT_SESSION = date(2026, 10, 14)
HOLDOUT_T0 = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id="instrument:us_equity:SPY")
STUDY = EvidenceSubject(
    subject_type=SubjectType.RESEARCH_STUDY, subject_id="research:spy_vwap_reversion"
)
CLOCK = EvidenceSubject(subject_type=SubjectType.SYSTEM_COMPONENT, subject_id="system:clock")

BARS = "synthetic_bars"
CALC = "synthetic_calc"
AGENT = "synthetic_agent"
RESEARCH = "spy_vwap_confirmation_result"
SELECTOR = "deterministic_contract_selector"
OPTIONS = "synthetic_option_chain"
CLOCK_PRODUCER = "system_clock_health"
DETECTOR = "synthetic_conflict_detector"
FUTURE = "future_trend_day_engine"


# --- Synthetic payload schemas (test-only; not production adapters) ------------------


class SyntheticBarFact(BaseModel):
    model_config = STRICT_FROZEN

    bar_timestamp: UtcTimestamp
    close: CanonicalDecimal


class SyntheticCalc(BaseModel):
    model_config = STRICT_FROZEN

    value: CanonicalDecimal


class SyntheticInference(BaseModel):
    model_config = STRICT_FROZEN

    claim_code: Token


class SyntheticResearch(BaseModel):
    model_config = STRICT_FROZEN

    label: Token


class SyntheticOptionFact(BaseModel):
    model_config = STRICT_FROZEN

    contract_symbol: str
    bid: CanonicalDecimal


PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    **CORE_PAYLOAD_MODELS,
    "synthetic_bar_fact.v1": SyntheticBarFact,
    "synthetic_calc.v1": SyntheticCalc,
    "synthetic_inference.v1": SyntheticInference,
    "synthetic_research.v1": SyntheticResearch,
    "synthetic_option_fact.v1": SyntheticOptionFact,
}


# --- Synthetic registry ----------------------------------------------------------------


def _rule(kind, payload, policy, eligible):
    return EmissionRule(
        evidence_kind=kind,
        payload_schema_id=payload,
        freshness_policy_id=policy,
        machine_decision_eligible=eligible,
    )


def _producer(producer_id, ptype, rules, subjects, *, paths=None, status=None, reasons=()):
    return ProducerEntry(
        producer_id=producer_id,
        producer_versions=["1.0.0"],
        producer_type=ptype,
        implementation_status=status or ImplementationStatus.IMPLEMENTED,
        production_paths=paths or [ProductionPath.DETERMINISTIC],
        directional_authority=DirectionalAuthority.NONE,
        allowed_subject_types=sorted(subjects),
        emission_rules=sorted(rules, key=lambda r: r.model_dump_json()),
        reason_codes=sorted(reasons),
        error_categories=["unexpected_error"],
    )


def _policy(policy_id, kinds, *, timeless=False, current=None, stale=None, tol=None, mode=None):
    return FreshnessPolicy(
        policy_id=policy_id,
        applicable_kinds=sorted(kinds),
        observed_timestamp_basis="source_observed_at",
        timeless=timeless,
        current_until_seconds=current,
        stale_after_seconds=stale,
        future_tolerance_seconds=tol,
        off_hours_mode=mode
        or (OffHoursMode.NOT_APPLICABLE if timeless else OffHoursMode.ELAPSED_WALL_CLOCK),
        configuration_version="1",
    )


K = EvidenceKind


def requirement(
    requirement_id: str,
    producer_id: str,
    kind: EvidenceKind,
    payload_schema_id: str,
    *,
    subject_id: str = "instrument:us_equity:SPY",
    versions: tuple[str, ...] = ("1.0.0",),
    configurations: tuple[str, ...] = ("cfg_none",),
) -> BundleRequirement:
    return BundleRequirement(
        requirement_id=requirement_id,
        producer_id=producer_id,
        producer_versions=sorted(versions),
        evidence_kind=kind,
        payload_schema_id=payload_schema_id,
        primary_subject_id=subject_id,
        configuration_identities=sorted(configurations),
    )


BARS_REQUIREMENT = requirement(
    "req.bars.spy", BARS, EvidenceKind.CONFIRMED_FACT, "synthetic_bar_fact.v1"
)
CALC_REQUIREMENT = requirement(
    "req.calc.spy", CALC, EvidenceKind.DETERMINISTIC_CALCULATION, "synthetic_calc.v1"
)
ALL_READS = [
    ConsumerPermission.READ_CALCULATIONS,
    ConsumerPermission.READ_CONFLICTS,
    ConsumerPermission.READ_FACTS,
    ConsumerPermission.READ_INFERENCES,
    ConsumerPermission.READ_RESEARCH,
]


def make_registry(**overrides: Any) -> EvidenceRegistry:
    live_kinds = [
        K.CONFIRMED_FACT,
        K.DETERMINISTIC_CALCULATION,
        K.MISSING_EVIDENCE,
        K.STALE_EVIDENCE,
    ]
    fields: dict[str, Any] = dict(
        registry_label="synthetic-test",
        producers=sorted(
            [
                _producer(
                    BARS,
                    ProducerType.SOURCE_ADAPTER,
                    [
                        _rule(K.CONFIRMED_FACT, "synthetic_bar_fact.v1", "fp.bars.v1", True),
                        _rule(K.MISSING_EVIDENCE, "missing_evidence.v1", "fp.bars.v1", True),
                        _rule(K.STALE_EVIDENCE, "stale_evidence.v1", "fp.bars.v1", True),
                    ],
                    [SubjectType.INSTRUMENT, SubjectType.MARKET_SESSION],
                    reasons=("bars_missing", "bars_stale", "holdout_restricted"),
                ),
                _producer(
                    CALC,
                    ProducerType.DETERMINISTIC_ENGINE,
                    [_rule(K.DETERMINISTIC_CALCULATION, "synthetic_calc.v1", "fp.bars.v1", True)],
                    [SubjectType.INSTRUMENT, SubjectType.MARKET_SESSION],
                ),
                _producer(
                    AGENT,
                    ProducerType.MODEL_AGENT,
                    [
                        _rule(
                            K.CURRENT_INFERENCE,
                            "synthetic_inference.v1",
                            "fp.inference.v1",
                            False,
                        )
                    ],
                    [SubjectType.INSTRUMENT, SubjectType.MARKET_SESSION],
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
                    SELECTOR,
                    ProducerType.DETERMINISTIC_ENGINE,
                    [
                        _rule(
                            K.DETERMINISTIC_CALCULATION,
                            "contract_selector_result.v1",
                            "fp.selector.v1",
                            True,
                        )
                    ],
                    [SubjectType.INSTRUMENT],
                ),
                _producer(
                    OPTIONS,
                    ProducerType.SOURCE_ADAPTER,
                    [_rule(K.CONFIRMED_FACT, "synthetic_option_fact.v1", "fp.selector.v1", True)],
                    [SubjectType.OPTION_CONTRACT],
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
                    FUTURE,
                    ProducerType.DETERMINISTIC_ENGINE,
                    [_rule(K.DETERMINISTIC_CALCULATION, "synthetic_calc.v1", "fp.bars.v1", True)],
                    [SubjectType.INSTRUMENT],
                    status=ImplementationStatus.FUTURE,
                ),
            ],
            key=lambda p: p.producer_id,
        ),
        payload_schemas=sorted(
            [
                PayloadSchemaEntry(payload_schema_id=schema_id, spy_price_content=price)
                for schema_id, price in (
                    ("synthetic_bar_fact.v1", True),
                    ("synthetic_calc.v1", True),
                    ("synthetic_inference.v1", True),
                    ("synthetic_research.v1", False),
                    ("synthetic_option_fact.v1", True),
                    ("contract_selector_result.v1", True),
                    ("clock_health_fact.v1", False),
                    ("missing_evidence.v1", False),
                    ("stale_evidence.v1", False),
                )
            ],
            key=lambda s: s.payload_schema_id,
        ),
        freshness_policies=sorted(
            [
                _policy("fp.bars.v1", live_kinds, current=300, stale=900, tol=5),
                _policy("fp.inference.v1", [K.CURRENT_INFERENCE], current=300, stale=900, tol=5),
                _policy(
                    "fp.research_timeless.v1", [K.HISTORICAL_RESEARCH_RESULT], timeless=True
                ),
                _policy(
                    "fp.selector.v1",
                    [K.CONFIRMED_FACT, K.DETERMINISTIC_CALCULATION],
                    current=300,
                    stale=300,
                    tol=0,
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
        consumer_grants=sorted(
            [
                ConsumerGrant(
                    consumer_id=ConsumerId.DASHBOARD,
                    purposes=sorted(
                        [
                            BundlePurpose.LIVE_MARKET_STATE,
                            BundlePurpose.CONTRACT_REVIEW,
                            BundlePurpose.SETUP_DETAIL,
                        ]
                    ),
                    permissions=ALL_READS,
                    machine_decision_mode_allowed=False,
                ),
                ConsumerGrant(
                    consumer_id=ConsumerId.SETUP_RANKER,
                    purposes=sorted([BundlePurpose.LIVE_MARKET_STATE, BundlePurpose.SETUP_DETAIL]),
                    permissions=sorted(
                        [
                            ConsumerPermission.READ_CALCULATIONS,
                            ConsumerPermission.READ_CONFLICTS,
                            ConsumerPermission.READ_FACTS,
                            ConsumerPermission.READ_RESEARCH,
                        ]
                    ),
                    machine_decision_mode_allowed=True,
                ),
                ConsumerGrant(
                    consumer_id=ConsumerId.AUDIT_EXPORT,
                    purposes=sorted(BundlePurpose),
                    permissions=sorted([*ALL_READS, ConsumerPermission.READ_SUPERSEDED]),
                    machine_decision_mode_allowed=False,
                ),
            ],
            key=lambda g: g.consumer_id,
        ),
        selection_rules=sorted(
            [
                SelectionRule(
                    selection_rule_id="sel.live.v1",
                    purpose=BundlePurpose.LIVE_MARKET_STATE,
                    requirements=[BARS_REQUIREMENT, CALC_REQUIREMENT],
                    max_window_seconds=86_400,
                ),
                SelectionRule(
                    selection_rule_id="sel.contract.v1",
                    purpose=BundlePurpose.CONTRACT_REVIEW,
                    requirements=[
                        requirement(
                            "req.selector.spy",
                            SELECTOR,
                            K.DETERMINISTIC_CALCULATION,
                            "contract_selector_result.v1",
                        )
                    ],
                    max_window_seconds=86_400,
                ),
                SelectionRule(
                    selection_rule_id="sel.setup.v1",
                    purpose=BundlePurpose.SETUP_DETAIL,
                    requirements=[
                        requirement(
                            "req.future.spy",
                            FUTURE,
                            K.DETERMINISTIC_CALCULATION,
                            "synthetic_calc.v1",
                        )
                    ],
                    max_window_seconds=86_400,
                ),
            ],
            key=lambda r: r.purpose,
        ),
        uncertainty_codes=["iex_partial_volume"],
        limitation_codes=["headline_only"],
        component_tokens=["bars"],
    )
    fields.update(overrides)
    return EvidenceRegistry(**fields)


# --- Item builders ----------------------------------------------------------------------


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


def bar_row_ref(bar_start: datetime) -> DuckDbRowReference:
    values = ["alpaca", "SPY", "5Min", "iex", "raw", "USD", format_utc(bar_start)]
    columns = ["provider", "symbol", "timeframe", "feed", "adjustment", "currency",
               "bar_timestamp"]
    return DuckDbRowReference(
        reference_type="duckdb_row",
        table=DuckDbTable.MARKET_BARS,
        primary_key=[PrimaryKeyComponent(column=c, value=v) for c, v in zip(columns, values)],
        db_schema_version="0009",
        ingestion_run_id="run-synthetic-1",
    )


def calc_ref(digest: str = "c" * 64) -> CalculationInputReference:
    return CalculationInputReference(
        reference_type="calculation_input",
        input_schema_id="synthetic-input-1",
        input_sha256=digest,
        input_record_count=1,
    )


def model_ref() -> ModelRunReference:
    return ModelRunReference(
        reference_type="model_run",
        provider="openai",
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


def bar_content(
    close_at: datetime = T0, *, close: str = "500.1", **overrides
) -> EvidenceItemContent:
    session = close_at.astimezone(_eastern()).date()
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.CONFIRMED_FACT,
        subjects=[SPY, session_subject(session)],
        effective_at_utc=close_at,
        temporal_scope=EvidenceTemporalScope(session_date=session),
        payload_schema_id="synthetic_bar_fact.v1",
        payload={"bar_timestamp": format_utc(close_at - timedelta(minutes=5)), "close": close},
        provenance=_provenance(
            BARS,
            ProducerType.SOURCE_ADAPTER,
            generated=close_at + timedelta(seconds=1),
            observed=close_at,
            refs=[bar_row_ref(close_at - timedelta(minutes=5))],
        ),
        freshness_policy_id="fp.bars.v1",
        availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
        quality=_quality(SourceBasis.DIRECTLY_OBSERVED),
        revision=ORIGINAL_REVISION,
        machine_decision_eligible=True,
    )
    fields.update(overrides)
    return EvidenceItemContent(**fields)


def bar_item(close_at: datetime = T0, **overrides) -> EvidenceItem:
    return seal_item(bar_content(close_at, **overrides))


def calc_item(parents: list[EvidenceItem], at: datetime = T0, **overrides) -> EvidenceItem:
    observed = min(p.provenance.source_observed_at_utc for p in parents) if parents else at
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.DETERMINISTIC_CALCULATION,
        subjects=[SPY, session_subject(at.astimezone(_eastern()).date())],
        effective_at_utc=at,
        temporal_scope=EvidenceTemporalScope(session_date=at.astimezone(_eastern()).date()),
        payload_schema_id="synthetic_calc.v1",
        payload={"value": "1.5"},
        provenance=_provenance(
            CALC,
            ProducerType.DETERMINISTIC_ENGINE,
            generated=at + timedelta(seconds=2),
            observed=observed,
            refs=[calc_ref()],
            parents=[p.item_id for p in parents],
        ),
        freshness_policy_id="fp.bars.v1",
        availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
        quality=_quality(
            SourceBasis.DERIVED, calculation_completeness=CalculationCompleteness.COMPLETE
        ),
        revision=ORIGINAL_REVISION,
        machine_decision_eligible=True,
    )
    fields.update(overrides)
    return seal_item(EvidenceItemContent(**fields))


def inference_item(parents: list[EvidenceItem], at: datetime = T0, **overrides) -> EvidenceItem:
    parent_ids = sorted(p.item_id for p in parents)
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.CURRENT_INFERENCE,
        subjects=[SPY],
        effective_at_utc=at,
        temporal_scope=EvidenceTemporalScope(),
        payload_schema_id="synthetic_inference.v1",
        payload={"claim_code": "summary"},
        provenance=_provenance(
            AGENT,
            ProducerType.MODEL_AGENT,
            generated=at + timedelta(seconds=3),
            observed=min(p.provenance.source_observed_at_utc for p in parents),
            refs=[model_ref()],
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
    fields.update(overrides)
    return seal_item(EvidenceItemContent(**fields))


def missing_item(at: datetime = T0, **overrides) -> EvidenceItem:
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.MISSING_EVIDENCE,
        subjects=[SPY],
        effective_at_utc=at,
        temporal_scope=EvidenceTemporalScope(),
        payload_schema_id="missing_evidence.v1",
        payload={
            "checked_at_utc": format_utc(at),
            "expected_component": None,
            "expected_producer_id": BARS,
            "expected_subject_id": "instrument:us_equity:SPY",
            "query_sha256": "0" * 64,
            "reason_code": "bars_missing",
        },
        provenance=_provenance(
            BARS,
            ProducerType.SOURCE_ADAPTER,
            generated=at,
            observed=at,
            refs=[],
        ),
        freshness_policy_id="fp.bars.v1",
        availability=EvidenceAvailability(
            state=AvailabilityState.UNAVAILABLE, reason_code="bars_missing"
        ),
        quality=_quality(SourceBasis.DIRECTLY_OBSERVED),
        revision=ORIGINAL_REVISION,
        machine_decision_eligible=True,
    )
    fields.update(overrides)
    return seal_item(EvidenceItemContent(**fields))


def stale_item(stale: EvidenceItem, at: datetime, **overrides) -> EvidenceItem:
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.STALE_EVIDENCE,
        subjects=[SPY],
        effective_at_utc=at,
        temporal_scope=EvidenceTemporalScope(),
        payload_schema_id="stale_evidence.v1",
        payload={
            "expected_producer_id": None,
            "last_observed_at_utc": format_utc(stale.provenance.source_observed_at_utc),
            "policy_id": "fp.bars.v1",
            "query_sha256": None,
            "stale_item_id": stale.item_id,
            "status_as_of_utc": format_utc(at),
        },
        provenance=_provenance(
            BARS,
            ProducerType.SOURCE_ADAPTER,
            generated=at,
            observed=at,
            refs=[],
            parents=[stale.item_id],
        ),
        freshness_policy_id="fp.bars.v1",
        availability=EvidenceAvailability(
            state=AvailabilityState.AVAILABLE, reason_code="bars_stale"
        ),
        quality=_quality(SourceBasis.DIRECTLY_OBSERVED),
        revision=ORIGINAL_REVISION,
        machine_decision_eligible=True,
    )
    fields.update(overrides)
    return seal_item(EvidenceItemContent(**fields))


def confirmation_reference(**overrides) -> EvidenceResearchReference:
    """The recorded confirmation values, copied from the committed result
    document (no result file or holdout data is read)."""
    from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
        FIXED_NOTES,
    )

    fields: dict[str, Any] = dict(
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
    fields.update(overrides)
    return EvidenceResearchReference(**fields)


def research_item(reference: EvidenceResearchReference | None = None, **overrides) -> EvidenceItem:
    reference = reference or confirmation_reference()
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.HISTORICAL_RESEARCH_RESULT,
        subjects=[STUDY],
        effective_at_utc=datetime(2026, 9, 25, 12, 0, tzinfo=UTC),
        temporal_scope=EvidenceTemporalScope(),
        payload_schema_id="synthetic_research.v1",
        payload={"label": reference.primary_label},
        provenance=_provenance(
            RESEARCH,
            ProducerType.RESEARCH_RESULT_PUBLISHER,
            generated=T0,
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
    fields.update(overrides)
    return seal_item(EvidenceItemContent(**fields))


def clock_item(at: datetime = T0, *, offset_ms: int = 5, synced: timedelta = timedelta(minutes=5)):
    return seal_item(
        EvidenceItemContent(
            evidence_kind=EvidenceKind.CONFIRMED_FACT,
            subjects=[CLOCK],
            effective_at_utc=at,
            temporal_scope=EvidenceTemporalScope(),
            payload_schema_id="clock_health_fact.v1",
            payload={"last_sync_at_utc": format_utc(at - synced), "offset_ms": offset_ms},
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


def _document_ref():
    from market_intelligence.evidence.contracts import DocumentReference
    from market_intelligence.evidence.enums import LocatorKind

    return DocumentReference(
        reference_type="document",
        locator_kind=LocatorKind.REPO_DOCUMENT,
        document_path="docs/EVIDENCE_REGISTRY.md",
        document_commit_sha=COMMIT,
        section_token="clock",
    )


def _eastern():
    from zoneinfo import ZoneInfo

    return ZoneInfo("America/New_York")


# --- Contract selector -------------------------------------------------------------------

EXPIRY = date(2027, 1, 12)


def _quote(symbol: str, strike: str, **overrides) -> OptionContractQuote:
    fields: dict[str, Any] = dict(
        contract_symbol=symbol,
        option_type=OptionType.CALL,
        expiration_date=EXPIRY,
        strike_price=Decimal(strike),
        bid_price=Decimal("1.00"),
        bid_size=10,
        ask_price=Decimal("1.10"),
        ask_size=10,
        implied_volatility=Decimal("0.2"),
        delta=Decimal("0.40"),
        gamma=Decimal("0.05"),
        theta=Decimal("-0.10"),
        vega=Decimal("0.10"),
        rho=Decimal("0.01"),
    )
    fields.update(overrides)
    return OptionContractQuote(**fields)


ELIGIBLE_A = "SPY270112C00680000"
ELIGIBLE_B = "SPY270112C00681000"
REJECTED_WIDE = "SPY270112C00682000"


def selector_result(*, feed: str = "opra") -> ContractSelectorResult:
    """A genuine selector run over a synthetic batch: two returned contracts
    and one rejected for a wide spread."""
    batch = OptionChainBatch(
        provider="alpaca",
        feed=feed,
        retrieved_at=T0 - timedelta(minutes=2),
        contracts=[
            _quote(ELIGIBLE_A, "680"),
            _quote(ELIGIBLE_B, "681"),
            _quote(REJECTED_WIDE, "682", ask_price=Decimal("3.00")),
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


def selector_item(result: ContractSelectorResult | None = None, **overrides) -> EvidenceItem:
    result = result or selector_result()
    fields: dict[str, Any] = dict(
        evidence_kind=EvidenceKind.DETERMINISTIC_CALCULATION,
        subjects=[SPY],
        effective_at_utc=T0,
        temporal_scope=EvidenceTemporalScope(session_date=SESSION),
        payload_schema_id="contract_selector_result.v1",
        payload=result.model_dump(mode="json"),
        provenance=_provenance(
            SELECTOR,
            ProducerType.DETERMINISTIC_ENGINE,
            generated=T0 + timedelta(seconds=2),
            observed=result.retrieved_at,
            refs=[calc_ref()],
        ),
        freshness_policy_id="fp.selector.v1",
        availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
        quality=_quality(
            SourceBasis.DERIVED, calculation_completeness=CalculationCompleteness.COMPLETE
        ),
        revision=ORIGINAL_REVISION,
        machine_decision_eligible=True,
    )
    fields.update(overrides)
    return seal_item(EvidenceItemContent(**fields))


def option_fact_item(symbol: str, at: datetime = T0) -> EvidenceItem:
    return seal_item(
        EvidenceItemContent(
            evidence_kind=EvidenceKind.CONFIRMED_FACT,
            subjects=[
                EvidenceSubject(
                    subject_type=SubjectType.OPTION_CONTRACT, subject_id=f"option:osi:{symbol}"
                )
            ],
            effective_at_utc=at - timedelta(minutes=2),
            temporal_scope=EvidenceTemporalScope(),
            payload_schema_id="synthetic_option_fact.v1",
            payload={"bid": "1", "contract_symbol": symbol},
            provenance=_provenance(
                OPTIONS,
                ProducerType.SOURCE_ADAPTER,
                generated=at,
                observed=at - timedelta(minutes=2),
                refs=[_document_ref()],
            ),
            freshness_policy_id="fp.selector.v1",
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=_quality(SourceBasis.DIRECTLY_OBSERVED),
            revision=ORIGINAL_REVISION,
            machine_decision_eligible=True,
        )
    )


# --- Bundle helpers ------------------------------------------------------------------------


def query(**overrides) -> EvidenceQuery:
    fields: dict[str, Any] = dict(
        effective_from_utc=T0 - timedelta(hours=6),
        effective_to_utc=T0 + timedelta(hours=6),
        max_entries=100,
    )
    fields.update(overrides)
    return EvidenceQuery(**fields)


def context(
    q: EvidenceQuery,
    *,
    consumer: ConsumerId = ConsumerId.DASHBOARD,
    purpose: BundlePurpose = BundlePurpose.LIVE_MARKET_STATE,
    permissions: list[ConsumerPermission] | None = None,
    machine: bool = False,
) -> EvidenceConsumerContext:
    return EvidenceConsumerContext(
        consumer_id=consumer,
        purpose=purpose,
        permissions=sorted(ALL_READS if permissions is None else permissions),
        machine_decision_mode=machine,
        request_sha256=query_request_sha256(q),
    )


def recorded(*items: EvidenceItem, at: datetime = T0) -> list[RecordedItem]:
    return [RecordedItem(item=item, recorded_at_utc=at + timedelta(seconds=5)) for item in items]


def bundle(
    items: list[RecordedItem],
    *,
    as_of: datetime = T0 + timedelta(minutes=1),
    purpose: BundlePurpose = BundlePurpose.LIVE_MARKET_STATE,
    consumer: ConsumerId = ConsumerId.DASHBOARD,
    registry: EvidenceRegistry | None = None,
    machine: bool = False,
    permissions: list[ConsumerPermission] | None = None,
    q: EvidenceQuery | None = None,
    conflicts=(),
):
    q = q or query()
    return build_bundle(
        registry=registry or make_registry(),
        purpose=purpose,
        as_of=as_of,
        consumer_context=context(
            q, consumer=consumer, purpose=purpose, machine=machine, permissions=permissions
        ),
        query=q,
        recorded_items=items,
        recorded_conflicts=conflicts,
        built_at=as_of + timedelta(seconds=1),
    )


def rebuild(item: EvidenceItemContent, **changes: Any) -> EvidenceItemContent:
    """Re-validate an item's content with some fields changed (never mutates)."""
    fields = {name: getattr(item, name) for name in EvidenceItemContent.model_fields}
    fields.update(changes)
    return EvidenceItemContent(**fields)


def rebuild_provenance(item: EvidenceItemContent, **changes: Any) -> EvidenceProvenance:
    fields = {name: getattr(item.provenance, name) for name in EvidenceProvenance.model_fields}
    fields.update(changes)
    return EvidenceProvenance(**fields)
