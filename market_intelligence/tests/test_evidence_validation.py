"""Registry-dependent validation, lineage taint, revisions, research
references, inference default-deny and directional authority (design §B.3,
§C.3, §G, §H, §M, §O). Synthetic data only."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence.contracts import (
    EvidenceAvailability,
    EvidenceEnvelopeContent,
    EvidenceInferenceBasis,
    EvidenceRevision,
    EvidenceScenarioRelation,
    EvidenceSubject,
    seal_envelope,
    seal_item,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    ClaimBasis,
    DirectionalAuthority,
    DirectionalContent,
    EvidenceKind,
    GradedStrength,
    HoldoutState,
    ImplementationStatus,
    InferenceMethod,
    ProducerType,
    ProductionPath,
    RationaleCategory,
    ResearchStatus,
    ResultKind,
    RevisionReason,
    ScenarioClass,
    ScenarioRelationKind,
    SourceBasis,
    SubjectType,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.primitives import ExactRational
from market_intelligence.evidence.registry import (
    EmissionRule,
    ProducerEntry,
    compute_registry_version_id,
)
from market_intelligence.evidence.validation import (
    derive_machine_decision_eligible,
    validate_envelope,
    validate_item,
    validate_lineage,
)
from market_intelligence.tests import evidence_fixtures as f

REGISTRY = f.make_registry()


def _validate(item, registry=REGISTRY):
    validate_item(item, registry, payload_models=f.PAYLOAD_MODELS)


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


# --- Registry contract ------------------------------------------------------------------


def test_registry_authorization_tables_are_structurally_empty():
    for table in (
        "scenario_definitions",
        "strength_rubrics",
        "authorization_records",
        "inference_input_authorizations",
    ):
        with pytest.raises(ValidationError):
            f.make_registry(**{table: ["anything"]})


def test_no_producer_may_hold_directional_authority():
    for authority in (
        DirectionalAuthority.NON_DIRECTIONAL_SCENARIO_RELATIONS,
        DirectionalAuthority.AUTHORIZED_DIRECTIONAL,
    ):
        with pytest.raises(ValidationError):
            _agent_entry(directional_authority=authority)


def test_inference_emission_rules_cannot_be_machine_decision_eligible():
    with pytest.raises(ValidationError):
        _agent_entry(
            emission_rules=[
                EmissionRule(
                    evidence_kind=EvidenceKind.CURRENT_INFERENCE,
                    payload_schema_id="synthetic_inference.v1",
                    freshness_policy_id="fp.inference.v1",
                    machine_decision_eligible=True,
                )
            ]
        )


def test_only_the_selector_may_register_the_contract_classification_payload():
    with pytest.raises(ValidationError):
        _agent_entry(
            producer_id="synthetic_contract_ranker",
            production_paths=[ProductionPath.DETERMINISTIC],
            emission_rules=[
                EmissionRule(
                    evidence_kind=EvidenceKind.DETERMINISTIC_CALCULATION,
                    payload_schema_id="contract_selector_result.v1",
                    freshness_policy_id="fp.selector.v1",
                    machine_decision_eligible=True,
                )
            ],
        )


def test_the_selector_must_be_deterministic_only():
    with pytest.raises(ValidationError):
        _agent_entry(
            producer_id=f.SELECTOR,
            emission_rules=[
                EmissionRule(
                    evidence_kind=EvidenceKind.DETERMINISTIC_CALCULATION,
                    payload_schema_id="contract_selector_result.v1",
                    freshness_policy_id="fp.selector.v1",
                    machine_decision_eligible=True,
                )
            ],
        )


def _agent_entry(**overrides):
    fields = dict(
        producer_id=f.AGENT,
        producer_versions=["1.0.0"],
        producer_type=ProducerType.MODEL_AGENT,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        production_paths=[ProductionPath.DETERMINISTIC, ProductionPath.MODEL_GENERATED],
        directional_authority=DirectionalAuthority.NONE,
        allowed_subject_types=[SubjectType.INSTRUMENT],
        emission_rules=[
            EmissionRule(
                evidence_kind=EvidenceKind.CURRENT_INFERENCE,
                payload_schema_id="synthetic_inference.v1",
                freshness_policy_id="fp.inference.v1",
                machine_decision_eligible=False,
            )
        ],
    )
    fields.update(overrides)
    return ProducerEntry(**fields)


# --- validate_item: registry checks ------------------------------------------------------


def test_valid_synthetic_items_pass():
    bar = f.bar_item()
    for item in (
        bar,
        f.calc_item([bar]),
        f.inference_item([bar]),
        f.missing_item(),
        f.stale_item(bar, f.T0 + timedelta(minutes=20)),
        f.research_item(),
        f.clock_item(),
        f.selector_item(),
        f.option_fact_item(f.ELIGIBLE_A),
    ):
        _validate(item)


def test_unknown_producer_and_version_are_refused():
    content = f.bar_content()
    unknown = f.rebuild(content, provenance=f.rebuild_provenance(content, producer_id="nope_x"))
    assert _reason(_validate, unknown) == "unknown_producer"
    assert _reason(
        _validate,
        f.rebuild(content, provenance=f.rebuild_provenance(content, producer_version="9.9.9")),
    ) == "unknown_producer_version"


def test_unimplemented_producer_is_refused():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    future = f.rebuild(
        calc, provenance=f.rebuild_provenance(calc, producer_id=f.FUTURE)
    )
    assert _reason(_validate, future) == "producer_not_implemented"


def test_unknown_payload_schema_is_refused():
    registry = REGISTRY
    item = f.bar_item()
    with pytest.raises(EvidenceValidationError) as excinfo:
        validate_item(item, registry)  # core models only: synthetic schema unknown
    assert excinfo.value.reason == "unknown_payload_schema"


def test_kind_or_payload_not_registered_for_producer_is_refused():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    as_bars = f.rebuild(
        calc,
        provenance=f.rebuild_provenance(
            calc, producer_id=f.BARS, producer_type=ProducerType.SOURCE_ADAPTER
        ),
    )
    assert _reason(_validate, as_bars) == "kind_or_payload_not_allowed"


def test_subject_not_covered_by_producer_is_refused():
    news = EvidenceSubject(subject_type=SubjectType.NEWS_ITEM, subject_id="news_item:alpaca:1")
    item = f.rebuild(f.missing_item(), subjects=[news])
    assert _reason(_validate, item) == "subject_not_allowed"


def test_payload_must_validate_and_be_canonical():
    assert _reason(_validate, f.bar_content(payload={"close": "1"})) == "payload_invalid"


def test_eligibility_must_equal_the_registry_derivation():
    assert _reason(_validate, f.bar_content(machine_decision_eligible=False)) == (
        "eligibility_mismatch"
    )
    dirty = f.bar_content()
    dirty = f.rebuild(dirty, provenance=f.rebuild_provenance(dirty, code_tree_clean=False))
    assert _reason(_validate, dirty) == "eligibility_mismatch"
    assert derive_machine_decision_eligible(f.rebuild(dirty, machine_decision_eligible=False),
                                            REGISTRY) is False


def test_vocabularies_are_registered():
    assert _reason(
        _validate,
        f.bar_content(quality=f._quality(SourceBasis.DIRECTLY_OBSERVED,
                                         uncertainty_codes=["made_up_code"])),
    ) == "unknown_uncertainty_code"
    missing = f.missing_item()
    assert _reason(
        _validate,
        f.rebuild(
            missing,
            availability=EvidenceAvailability(
                state=AvailabilityState.UNAVAILABLE, reason_code="not_registered"
            ),
            payload={**missing.payload, "reason_code": "not_registered"},
        ),
    ) == "unknown_reason_code"


# --- No fabricated confidence; directional authority (§G, §H) ------------------------------


def test_probability_is_refused_without_authorization():
    item = f.bar_content(
        quality=f._quality(
            SourceBasis.DIRECTLY_OBSERVED,
            calibrated_probability=ExactRational(numerator=3, denominator=5),
        )
    )
    assert _reason(_validate, item) == "unauthorized_probability"


def test_graded_strength_with_an_unregistered_rubric_is_refused():
    item = f.bar_content(
        quality=f._quality(
            SourceBasis.DIRECTLY_OBSERVED,
            graded_strength=GradedStrength.HIGH,
            strength_rubric_id="made-up-rubric",
        )
    )
    assert _reason(_validate, item) == "unregistered_rubric"


def test_scenario_relations_are_refused_without_authorization():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    relation = EvidenceScenarioRelation(
        scenario=EvidenceSubject(
            subject_type=SubjectType.SCENARIO, subject_id="scenario:example:SPY:2027-01-12"
        ),
        scenario_class=ScenarioClass.DIRECTIONAL,
        relation=ScenarioRelationKind.SUPPORTS,
        rationale_category=RationaleCategory.NEWS_EVENT,
        supporting_evidence_ids=[bar.item_id],
        authorization_record_id="made-up-authorization",
    )
    with_relation = f.rebuild(
        inference,
        scenario_relations=[relation],
        inference_basis=EvidenceInferenceBasis(
            inference_method=InferenceMethod.MODEL_STRUCTURED_OUTPUT,
            input_evidence_ids=[bar.item_id],
            claim_basis=ClaimBasis.EXPLAINS_SCENARIO_RELATION,
            directional_content=DirectionalContent.SCENARIO_RELATION,
        ),
    )
    assert _reason(_validate, with_relation) == "scenario_relation_unauthorized"


def test_directional_content_without_relations_is_rejected():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    with pytest.raises(ValidationError):
        f.rebuild(
            inference,
            inference_basis=EvidenceInferenceBasis(
                inference_method=InferenceMethod.MODEL_STRUCTURED_OUTPUT,
                input_evidence_ids=[bar.item_id],
                claim_basis=ClaimBasis.SUMMARIZES_INPUTS,
                directional_content=DirectionalContent.SCENARIO_RELATION,
            ),
        )


# --- Inference default deny (§H.2) -----------------------------------------------------------


def test_every_current_inference_is_machine_decision_ineligible_today():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    assert inference.machine_decision_eligible is False
    assert derive_machine_decision_eligible(inference, REGISTRY) is False
    forced = f.rebuild(inference, machine_decision_eligible=True)
    assert _reason(_validate, forced) == "eligibility_mismatch"


def test_rule_based_inference_is_also_default_deny():
    bar = f.bar_item()
    inference = f.inference_item(
        [bar],
        provenance=f._provenance(
            f.AGENT,
            ProducerType.MODEL_AGENT,
            generated=f.T0,
            observed=f.T0,
            refs=[],
            parents=[bar.item_id],
        ),
        quality=f._quality(SourceBasis.RULE_INFERRED),
        inference_basis=EvidenceInferenceBasis(
            inference_method=InferenceMethod.DETERMINISTIC_RULE,
            rule_id="synthetic-rule-1",
            input_evidence_ids=[bar.item_id],
            claim_basis=ClaimBasis.SUMMARIZES_INPUTS,
            directional_content=DirectionalContent.NONE,
        ),
    )
    _validate(inference)
    assert inference.machine_decision_eligible is False


# --- Lineage taint (§C.3) --------------------------------------------------------------------


def _known(*items):
    return {i.item_id: i for i in items}


def test_calculation_cannot_descend_from_inference_at_any_depth():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    direct = f.calc_item([inference], f.T0 + timedelta(minutes=1))
    assert _reason(validate_lineage, direct, _known(bar, inference), REGISTRY) == (
        "lineage_violation"
    )


def test_deep_inference_ancestry_taints_a_calculation():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    # A status item whose parent is an inference, then a calculation over it.
    stale = f.stale_item(bar, f.T0 + timedelta(minutes=20))
    stale_from_inference = seal_item(
        f.rebuild(
            stale,
            provenance=f.rebuild_provenance(stale, parent_evidence_ids=[inference.item_id]),
            payload={**stale.payload, "stale_item_id": inference.item_id},
        )
    )
    calc = f.calc_item([stale_from_inference], f.T0 + timedelta(minutes=21))
    known = _known(bar, inference, stale_from_inference)
    assert _reason(validate_lineage, calc, known, REGISTRY) == "lineage_violation"


def test_fact_may_have_only_fact_parents():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    fact = f.bar_content(
        provenance=f._provenance(
            f.BARS,
            ProducerType.SOURCE_ADAPTER,
            generated=f.T0 + timedelta(seconds=5),
            observed=f.T0,
            refs=[f.bar_row_ref(f.T0 - timedelta(minutes=5))],
            parents=[calc.item_id],
        )
    )
    assert _reason(validate_lineage, fact, _known(bar, calc), REGISTRY) == "lineage_violation"


def test_research_result_cannot_descend_from_inference():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    research = f.research_item()
    tainted = f.rebuild(
        research,
        provenance=f.rebuild_provenance(research, parent_evidence_ids=[inference.item_id],
                                        generated_at_utc=f.T0 + timedelta(minutes=1)),
    )
    assert _reason(validate_lineage, tainted, _known(bar, inference), REGISTRY) == (
        "lineage_violation"
    )


def test_inference_may_have_parents_of_any_kind():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    validate_lineage(f.inference_item([bar, calc]), _known(bar, calc), REGISTRY)


def test_missing_parent_is_refused():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    assert _reason(validate_lineage, calc, {}, REGISTRY) == "missing_parent"


def test_parent_generated_later_is_refused():
    bar = f.bar_item()
    later = f.rebuild(
        bar,
        provenance=f.rebuild_provenance(bar, generated_at_utc=f.T0 + timedelta(hours=1)),
    )
    later_sealed = seal_item(later)
    calc = f.calc_item([later_sealed])
    assert _reason(validate_lineage, calc, _known(later_sealed), REGISTRY) == (
        "parent_generated_later"
    )


def test_parent_not_yet_stored_is_refused_so_cycles_cannot_form():
    content = f.bar_content()
    would_be_id = seal_item(content).item_id
    calc = f.calc_item([], provenance=f._provenance(
        f.CALC,
        ProducerType.DETERMINISTIC_ENGINE,
        generated=f.T0 + timedelta(seconds=2),
        observed=f.T0,
        refs=[f.calc_ref()],
        parents=[would_be_id],
    ))
    assert _reason(validate_lineage, calc, {}, REGISTRY) == "missing_parent"


# --- Revisions (§M) --------------------------------------------------------------------------


def _revision_of(original, reason=RevisionReason.SOURCE_REVISION, **overrides):
    return f.bar_item(
        revision=EvidenceRevision(
            revision_number=original.revision.revision_number + 1,
            supersedes_item_id=original.item_id,
            revision_reason=reason,
        ),
        close="500.2",
        **overrides,
    )


def test_source_revision_is_accepted():
    original = f.bar_item()
    validate_lineage(_revision_of(original), _known(original), REGISTRY)


def test_revision_must_match_producer_kind_and_primary_subject():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    wrong = f.calc_item(
        [bar],
        revision=EvidenceRevision(
            revision_number=2,
            supersedes_item_id=bar.item_id,
            revision_reason=RevisionReason.PRODUCER_CORRECTION,
        ),
    )
    assert _reason(validate_lineage, wrong, _known(bar, calc), REGISTRY) == "revision_mismatch"


def test_revision_number_must_follow_the_predecessor():
    original = f.bar_item()
    skipped = f.bar_item(
        revision=EvidenceRevision(
            revision_number=3,
            supersedes_item_id=original.item_id,
            revision_reason=RevisionReason.SOURCE_REVISION,
        )
    )
    assert _reason(validate_lineage, skipped, _known(original), REGISTRY) == (
        "revision_number_mismatch"
    )


def test_reinterpretation_never_supersedes_a_fact():
    original = f.bar_item()
    revision = _revision_of(original, RevisionReason.ANALYTICAL_REINTERPRETATION)
    assert _reason(validate_lineage, revision, _known(original), REGISTRY) == (
        "reinterpretation_only_for_inference"
    )


def test_producer_correction_requires_a_new_build():
    original = f.bar_item()
    revision = _revision_of(original, RevisionReason.PRODUCER_CORRECTION)
    assert _reason(validate_lineage, revision, _known(original), REGISTRY) == (
        "correction_requires_new_producer_build"
    )


def test_missing_predecessor_is_refused():
    original = f.bar_item()
    assert _reason(validate_lineage, _revision_of(original), {}, REGISTRY) == (
        "missing_predecessor"
    )


def test_revision_never_changes_the_original():
    original = f.bar_item()
    before = original.model_dump_json()
    _revision_of(original)
    assert original.model_dump_json() == before


# --- Research references (§B.10) ---------------------------------------------------------------


def test_recorded_confirmation_reference_is_accepted():
    _validate(f.research_item())


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"result_schema_version": "unknown-schema-1"}, "unknown_research_schema"),
        ({"study_id": "other_study"}, "research_study_mismatch"),
        ({"result_kind": ResultKind.HOLDOUT_RESULT}, "holdout_result_not_recorded"),
        ({"holdout_state": HoldoutState.RECORDED}, "holdout_state_must_be_sealed"),
        ({"primary_label": "validated"}, "research_label_unknown"),
        ({"secondary_labels": ["made_up"]}, "research_label_unknown"),
        ({"fixed_caveats": ["research_result_not_validation"]}, "research_fixed_caveat_missing"),
        (
            {"research_status": ResearchStatus.CONFIRMED},
            "research_status_mismatch",
        ),
        (
            {"primary_label": "mixed", "research_status": ResearchStatus.UNSUPPORTED},
            "research_status_rule_missing",
        ),
        (
            {"primary_label": "not_supported", "research_status": ResearchStatus.UNSUPPORTED},
            "research_status_rule_missing",
        ),
        (
            {"primary_label": "insufficient_sample", "research_status": ResearchStatus.UNSUPPORTED},
            "research_status_rule_missing",
        ),
    ],
)
def test_research_reference_rules(overrides, reason):
    reference = f.confirmation_reference(**overrides)
    item = f.research_item(reference)
    assert _reason(_validate, item) == reason


# --- Envelope validation ---------------------------------------------------------------------


def _bars_envelope(items, registry=REGISTRY):
    return seal_envelope(
        EvidenceEnvelopeContent(
            registry_version_id=compute_registry_version_id(registry),
            producer_id=f.BARS,
            producer_version="1.0.0",
            producer_run_key="1" * 64,
            run_as_of_utc=f.T0,
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            items=sorted(items, key=lambda i: i.item_id),
            emitted_at_utc=f.T0 + timedelta(seconds=10),
        )
    )


def test_envelope_validates_every_item():
    envelope = _bars_envelope([f.bar_item(), f.bar_item(f.T0 - timedelta(minutes=5))])
    validate_envelope(envelope, REGISTRY, known_items={}, payload_models=f.PAYLOAD_MODELS)


def test_envelope_with_unknown_registry_version_is_refused():
    envelope = _bars_envelope([f.bar_item()], registry=f.make_registry(registry_label="other"))
    assert _reason(
        validate_envelope, envelope, REGISTRY, known_items={}, payload_models=f.PAYLOAD_MODELS
    ) == "registry_version_mismatch"


def test_missing_evidence_carries_its_bounded_reason():
    missing = f.missing_item()
    mismatched = f.rebuild(missing, payload={**missing.payload, "reason_code": "bars_stale"})
    assert _reason(_validate, mismatched) == "status_reason_mismatch"


def test_unregistered_payload_schema_is_refused_first():
    registry = f.make_registry(
        payload_schemas=[
            s for s in REGISTRY.payload_schemas if s.payload_schema_id != "synthetic_option_fact.v1"
        ],
        producers=[p for p in REGISTRY.producers if p.producer_id != f.OPTIONS],
    )
    registry_with_producer = f.make_registry()
    item = f.option_fact_item(f.ELIGIBLE_A)
    # With the producer removed the producer check fires; with the schema
    # alone missing (below) the schema check fires before any rule lookup.
    assert _reason(_validate, item, registry) == "unknown_producer"
    # model_construct: a validated registry cannot reference an unregistered
    # schema, so this simulates the gap directly to prove the check's order.
    fields = type(registry_with_producer).model_fields
    missing_schema = registry_with_producer.model_construct(
        **{
            **{n: getattr(registry_with_producer, n) for n in fields},
            "payload_schemas": [
                s
                for s in registry_with_producer.payload_schemas
                if s.payload_schema_id != "synthetic_option_fact.v1"
            ],
        }
    )
    assert _reason(_validate, item, missing_schema) == "unknown_payload_schema"


def test_only_the_recorded_confirmation_status_mapping_exists():
    from market_intelligence.evidence.research_rules import RESEARCH_SCHEMAS

    rules = RESEARCH_SCHEMAS["spy-vwap-reversion-confirmation-1"].status_by_kind_and_label
    assert dict(rules) == {
        (ResultKind.CONFIRMATION_RESULT, "supported_for_further_shadow_research"): (
            ResearchStatus.SHADOW_PENDING
        )
    }
