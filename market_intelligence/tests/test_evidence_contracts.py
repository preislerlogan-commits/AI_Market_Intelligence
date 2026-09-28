"""Contract validation, evidence-kind separation, subjects and sanitization
for the Evidence Envelope core (design §B, §C, §D, §E). Synthetic data only."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from market_intelligence.evidence import enums
from market_intelligence.evidence.contracts import (
    ORIGINAL_REVISION,
    ConflictResolution,
    DocumentReference,
    DuckDbRowReference,
    EvidenceAvailability,
    EvidenceConflictContent,
    EvidenceFreshness,
    EvidenceInferenceBasis,
    EvidenceItem,
    EvidenceQualityProfile,
    EvidenceRevision,
    EvidenceSubject,
    EvidenceTemporalScope,
    PrimaryKeyComponent,
    seal_item,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    DetectionMethod,
    EvidenceKind,
    FreshnessReason,
    FreshnessState,
    GradedStrength,
    InferenceMethod,
    InferenceSupport,
    LocatorKind,
    ProductionPath,
    ResolutionKind,
    RevisionReason,
    SourceBasis,
    SubjectType,
)
from market_intelligence.evidence.primitives import ExactRational
from market_intelligence.tests import evidence_fixtures as f

# --- Primitives -------------------------------------------------------------------------


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValidationError):
        f.bar_content(f.T0.replace(tzinfo=None))


def test_timestamps_are_normalized_to_utc():
    eastern = timezone(timedelta(hours=-5))
    item = f.bar_item(f.T0.astimezone(eastern))
    assert item.effective_at_utc.utcoffset() == timedelta(0)
    assert item.item_id == f.bar_item(f.T0).item_id


def test_timestamps_outside_the_supported_range_are_rejected():
    with pytest.raises(ValidationError):
        EvidenceTemporalScope(scheduled_event_at_utc=datetime(1999, 1, 1, tzinfo=UTC))


@pytest.mark.parametrize("bad", ["1.50", "01", "-0", "1e5", "NaN", "Infinity", "1.", ".5", "+1"])
def test_noncanonical_decimals_are_rejected(bad):
    with pytest.raises(ValidationError):
        f.SyntheticCalc(value=bad)


@pytest.mark.parametrize("good", ["0", "1.5", "-2.25", "500.1"])
def test_canonical_decimals_are_accepted(good):
    assert f.SyntheticCalc(value=good).value == good


@pytest.mark.parametrize("value", [1.5, math.nan, math.inf])
def test_floats_nan_and_infinity_are_rejected_in_payloads(value):
    with pytest.raises(ValidationError):
        f.bar_content(payload={"bar_timestamp": "x", "close": value})


def test_exact_rational_must_be_reduced():
    assert ExactRational(numerator=1, denominator=2)
    with pytest.raises(ValidationError):
        ExactRational(numerator=2, denominator=4)
    with pytest.raises(ValidationError):
        ExactRational(numerator=1, denominator=0)


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        EvidenceSubject(
            subject_type=SubjectType.INSTRUMENT,
            subject_id="instrument:us_equity:SPY",
            display_name="SPDR S&P 500",
        )


def test_strict_types_reject_coercion():
    with pytest.raises(ValidationError):
        EvidenceAvailability(state="available")  # a string, not the enum
    with pytest.raises(ValidationError):
        EvidenceRevision(revision_number="1", revision_reason=RevisionReason.ORIGINAL)


def test_records_are_immutable():
    item = f.bar_item()
    with pytest.raises(ValidationError):
        item.evidence_kind = EvidenceKind.CURRENT_INFERENCE


def test_unknown_schema_version_is_refused():
    content = f.bar_content()
    data = content.model_dump(mode="json")
    data["schema_version"] = "evidence-envelope-2"
    data["item_id"] = "evi1_" + "0" * 64
    with pytest.raises(ValidationError):
        EvidenceItem.model_validate_json(__import__("json").dumps(data))


def test_items_round_trip_through_json():
    item = f.bar_item()
    again = EvidenceItem.model_validate_json(item.model_dump_json())
    assert again == item


# --- Enum coverage ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "enum_type",
    [
        value
        for value in vars(enums).values()
        if isinstance(value, type)
        and issubclass(value, enums.StrEnum)
        and value is not enums.StrEnum
    ],
    ids=lambda e: e.__name__,
)
def test_every_enum_round_trips_and_refuses_unknown_values(enum_type):
    for member in enum_type:
        assert enum_type(member.value) is member
    with pytest.raises(ValueError):
        enum_type("not_a_member_value")


def test_evidence_kind_taxonomy_is_frozen():
    assert {k.value for k in EvidenceKind} == {
        "confirmed_fact",
        "deterministic_calculation",
        "historical_research_result",
        "current_inference",
        "missing_evidence",
        "stale_evidence",
    }


# --- Subjects ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject_type", "subject_id"),
    [
        (SubjectType.INSTRUMENT, "instrument:us_equity:SPY"),
        (SubjectType.OPTION_CONTRACT, "option:osi:SPY260918C00750000"),
        (SubjectType.MARKET_SESSION, "session:us_equity_regular:2027-01-12"),
        (SubjectType.MACRO_SERIES, "macro_series:fred:FEDFUNDS"),
        (SubjectType.NEWS_ITEM, "news_item:alpaca:12345"),
        (SubjectType.SCENARIO, "scenario:example:SPY:2027-01-12"),
        (SubjectType.SETUP_CANDIDATE, "setup:vwap_reversion:spy_vwap_ext_v1:SPY:20270112T150500Z"),
        (SubjectType.RESEARCH_STUDY, "research:spy_vwap_reversion"),
        (SubjectType.PROVIDER_HEALTH, "provider:alpaca:market_data"),
        (SubjectType.SYSTEM_COMPONENT, "system:clock"),
    ],
)
def test_canonical_subject_ids_are_accepted(subject_type, subject_id):
    assert EvidenceSubject(subject_type=subject_type, subject_id=subject_id)


@pytest.mark.parametrize(
    ("subject_type", "subject_id"),
    [
        (SubjectType.INSTRUMENT, "SPY"),
        (SubjectType.INSTRUMENT, "instrument:us_equity:SPDR S&P 500 ETF"),
        (SubjectType.OPTION_CONTRACT, "instrument:us_equity:SPY"),
        (SubjectType.MARKET_SESSION, "session:us_equity_regular:2027-13-40"),
        (SubjectType.SETUP_CANDIDATE, "setup:breakout:x:SPY:20270112T150500Z"),
    ],
)
def test_noncanonical_subject_ids_and_display_names_are_rejected(subject_type, subject_id):
    with pytest.raises(ValidationError):
        EvidenceSubject(subject_type=subject_type, subject_id=subject_id)


def test_secondary_subjects_must_be_sorted_and_unique():
    session = f.session_subject(f.SESSION)
    with pytest.raises(ValidationError):
        f.bar_content(subjects=[f.SPY, session, session])


def test_session_date_must_match_the_session_subject():
    with pytest.raises(ValidationError):
        f.bar_content(temporal_scope=EvidenceTemporalScope(session_date=f.SESSION.replace(day=13)))


# --- Evidence-kind separation (§C.2) --------------------------------------------------


def test_a_fact_cannot_be_model_generated():
    content = f.bar_content()
    with pytest.raises(ValidationError):
        f.rebuild(
            content,
            provenance=f.rebuild_provenance(
                content,
                production_path=ProductionPath.MODEL_GENERATED,
                source_references=sorted(
                    [*content.provenance.source_references, f.model_ref()],
                    key=lambda r: r.model_dump_json(),
                ),
            ),
        )


def test_a_calculation_cannot_claim_model_inferred_basis():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    with pytest.raises(ValidationError):
        f.rebuild(calc, quality=f._quality(SourceBasis.MODEL_INFERRED))


def test_a_calculation_needs_a_calculation_input_reference():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    with pytest.raises(ValidationError):
        f.rebuild(calc, provenance=f.rebuild_provenance(calc, source_references=[]))


def test_a_fact_needs_a_source_reference():
    content = f.bar_content()
    with pytest.raises(ValidationError):
        f.rebuild(content, provenance=f.rebuild_provenance(content, source_references=[]))


def test_model_generated_inference_needs_exactly_one_model_run():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    with pytest.raises(ValidationError):
        f.rebuild(inference, provenance=f.rebuild_provenance(inference, source_references=[]))


def test_inference_requires_basis_and_basis_is_forbidden_elsewhere():
    bar = f.bar_item()
    inference = f.inference_item([bar])
    with pytest.raises(ValidationError):
        f.rebuild(inference, inference_basis=None)
    with pytest.raises(ValidationError):
        f.rebuild(f.bar_content(), inference_basis=inference.inference_basis)


def test_inference_inputs_must_be_parents():
    bar = f.bar_item()
    other = f.bar_item(f.T0 + timedelta(minutes=5))
    inference = f.inference_item([bar])
    with pytest.raises(ValidationError):
        f.rebuild(
            inference,
            inference_basis=EvidenceInferenceBasis(
                inference_method=InferenceMethod.MODEL_STRUCTURED_OUTPUT,
                input_evidence_ids=[other.item_id],
                claim_basis=inference.inference_basis.claim_basis,
                directional_content=inference.inference_basis.directional_content,
            ),
        )


def test_research_result_requires_a_research_reference_and_no_observed_time():
    research = f.research_item()
    with pytest.raises(ValidationError):
        f.rebuild(research, research_reference=None)
    with pytest.raises(ValidationError):
        f.rebuild(research, provenance=f.rebuild_provenance(research, source_observed_at_utc=f.T0))


def test_research_reference_forbidden_on_facts_and_inference():
    research = f.research_item()
    with pytest.raises(ValidationError):
        f.rebuild(f.bar_content(), research_reference=research.research_reference)


def test_missing_evidence_cannot_be_available_or_carry_a_value_schema():
    missing = f.missing_item()
    with pytest.raises(ValidationError):
        f.rebuild(missing, availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE))
    with pytest.raises(ValidationError):
        f.rebuild(missing, payload_schema_id="synthetic_calc.v1", payload={"value": "0"})


def test_status_payload_schemas_are_reserved_for_status_kinds():
    with pytest.raises(ValidationError):
        f.bar_content(payload_schema_id="missing_evidence.v1")


def test_only_missing_evidence_may_be_unavailable():
    with pytest.raises(ValidationError):
        f.bar_content(
            availability=EvidenceAvailability(
                state=AvailabilityState.UNAVAILABLE, reason_code="bars_missing"
            )
        )


def test_observed_time_cannot_follow_effective_time():
    content = f.bar_content()
    with pytest.raises(ValidationError):
        f.rebuild(
            content,
            provenance=f.rebuild_provenance(
                content, source_observed_at_utc=f.T0 + timedelta(seconds=1)
            ),
        )


def test_calculation_completeness_applies_only_to_calculations():
    with pytest.raises(ValidationError):
        f.bar_content(
            quality=f._quality(
                SourceBasis.DIRECTLY_OBSERVED,
                calculation_completeness=enums.CalculationCompleteness.COMPLETE,
            )
        )


# --- No fabricated confidence (§G) -----------------------------------------------------


def test_graded_strength_requires_a_rubric():
    with pytest.raises(ValidationError):
        f._quality(SourceBasis.DIRECTLY_OBSERVED, graded_strength=GradedStrength.HIGH)


def test_producers_cannot_self_grade_inference_support():
    with pytest.raises(ValidationError):
        f._quality(SourceBasis.MODEL_INFERRED, inference_support=InferenceSupport.SUPPORTED)


def test_quality_has_no_single_confidence_score_field():
    assert "confidence" not in EvidenceQualityProfile.model_fields
    assert "score" not in EvidenceQualityProfile.model_fields


# --- Availability, revision, freshness, conflict contracts -----------------------------


def test_availability_rules():
    with pytest.raises(ValidationError):
        EvidenceAvailability(state=AvailabilityState.UNAVAILABLE)
    with pytest.raises(ValidationError):
        EvidenceAvailability(state=AvailabilityState.PARTIAL, reason_code="bars_missing")
    with pytest.raises(ValidationError):
        EvidenceAvailability(state=AvailabilityState.ERROR, reason_code="bars_missing")


def test_revision_rules():
    assert ORIGINAL_REVISION.revision_number == 1
    with pytest.raises(ValidationError):
        EvidenceRevision(revision_number=2, revision_reason=RevisionReason.SOURCE_REVISION)
    with pytest.raises(ValidationError):
        EvidenceRevision(
            revision_number=1,
            supersedes_item_id=f.bar_item().item_id,
            revision_reason=RevisionReason.ORIGINAL,
        )


def test_freshness_state_and_reason_must_agree():
    with pytest.raises(ValidationError):
        EvidenceFreshness(
            policy_id="fp.bars.v1",
            evaluated_at_utc=f.T0,
            source_observed_at_utc=f.T0,
            age_seconds=0,
            state=FreshnessState.CURRENT,
            reason=FreshnessReason.BEYOND_STALE_BOUNDARY,
            clock_health_item_id=f.bar_item().item_id,
            clock_health_reason=enums.ClockHealthReason.HEALTHY,
        )


def test_conflicts_have_no_winner_field():
    fields = set(EvidenceConflictContent.model_fields)
    assert not fields & {"winner", "preferred_item", "resolved_value", "preferred_item_id"}


def test_conflict_resolution_requires_evidence_unless_manual():
    with pytest.raises(ValidationError):
        ConflictResolution(
            resolution_kind=ResolutionKind.NEW_EVIDENCE,
            supersedes_conflict_id="evc1_" + "0" * 64,
        )


def test_unresolved_conflicts_carry_no_resolution():
    ids = sorted([f.bar_item().item_id, f.bar_item(f.T0 + timedelta(minutes=5)).item_id])
    with pytest.raises(ValidationError):
        EvidenceConflictContent(
            conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
            involved_item_ids=ids,
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=f.DETECTOR,
            detector_version="1.0.0",
            severity=ConflictSeverity.MATERIAL,
            status=ConflictStatus.UNRESOLVED,
            resolution=ConflictResolution(
                resolution_kind=ResolutionKind.MANUAL_ACKNOWLEDGEMENT,
                supersedes_conflict_id="evc1_" + "0" * 64,
            ),
            evaluated_as_of_utc=f.T0,
            detected_at_utc=f.T0,
        )


# --- Sanitization (§E, §O) ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "api_key=abcdef",
        "Bearer abcdefghijklmnop",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "C:\\AI_Market_Intelligence\\data\\market_intelligence.duckdb",
        "/home/user/.env",
        'Traceback (most recent call last):\n  File "x.py", line 1',
    ],
)
def test_sensitive_text_is_refused_in_payloads_and_display_text(text):
    with pytest.raises(ValidationError):
        f.bar_content(payload={"bar_timestamp": text, "close": "1"})
    with pytest.raises(ValidationError):
        f.bar_content(display_text=[text])


def test_display_text_must_pass_the_non_directional_policy():
    with pytest.raises(ValidationError):
        f.bar_content(display_text=["SPY will rally into the close"])
    with pytest.raises(ValidationError):
        f.bar_content(display_text=["Consider buying calls at the 680 strike"])
    assert f.bar_item(display_text=["Stored 5-minute bar recorded from the provider."])


def test_display_text_rejects_control_characters():
    with pytest.raises(ValidationError):
        f.bar_content(display_text=["line one\x00"])


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/article",
        "https://user:pw@example.com/article",
        "https://example.com/article?api_key=abc",
        "https://example.com/article?token=abc",
    ],
)
def test_external_urls_must_be_safe_https(url):
    with pytest.raises(ValidationError):
        DocumentReference(
            reference_type="document",
            locator_kind=LocatorKind.EXTERNAL_PUBLICATION,
            publisher="reuters",
            https_url=url,
            retrieved_at_utc=f.T0,
        )


def test_safe_external_url_is_accepted():
    assert DocumentReference(
        reference_type="document",
        locator_kind=LocatorKind.EXTERNAL_PUBLICATION,
        publisher="reuters",
        https_url="https://example.com/article?id=5",
        retrieved_at_utc=f.T0,
    )


def test_duckdb_row_reference_must_match_registered_primary_key():
    with pytest.raises(ValidationError):
        DuckDbRowReference(
            reference_type="duckdb_row",
            table=enums.DuckDbTable.NEWS_ARTICLES,
            primary_key=[PrimaryKeyComponent(column="provider", value="alpaca")],
            db_schema_version="0009",
            ingestion_run_id=None,
        )


def test_model_run_reference_has_no_prompt_or_reasoning_fields():
    fields = set(type(f.model_ref()).model_fields)
    assert not fields & {"prompt", "prompt_text", "reasoning", "response_id", "evidence_text"}


def test_sealed_item_refuses_a_forged_id():
    content = f.bar_content()
    fields = {name: getattr(content, name) for name in type(content).model_fields}
    with pytest.raises(ValidationError):
        EvidenceItem(**fields, item_id="evi1_" + "0" * 64)
    assert seal_item(content).item_id.startswith("evi1_")
