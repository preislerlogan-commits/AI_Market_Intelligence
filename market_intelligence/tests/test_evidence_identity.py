"""Canonical serialization and content-addressed identities (design §B.0, §L).
Synthetic data only."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence.canonical import (
    canonical_json_bytes,
    configuration_identity,
    remove_paths,
)
from market_intelligence.evidence.contracts import (
    EvidenceAvailability,
    EvidenceConflictContent,
    EvidenceEnvelope,
    EvidenceEnvelopeContent,
    EvidenceRevision,
    EvidenceTemporalScope,
    compute_conflict_id,
    compute_item_id,
    compute_producer_run_key,
    seal_conflict,
    seal_envelope,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    DetectionMethod,
    RevisionReason,
)
from market_intelligence.evidence.primitives import format_utc
from market_intelligence.evidence.registry import compute_registry_version_id
from market_intelligence.tests import evidence_fixtures as f

GOLDEN_BAR_ITEM_ID = "evi1_79bea97e28ae525246ece2fe2ce62697244afcf5f003ef0274c4fe3db63c6687"


# --- Canonical serialization ------------------------------------------------------------


def test_canonical_json_is_compact_sorted_and_utf8():
    assert canonical_json_bytes({"b": 1, "a": [None, "é"]}) == '{"a":[null,"é"],"b":1}'.encode()


def test_canonical_json_refuses_nan():
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": float("nan")})


def test_timestamps_always_have_six_fractional_digits():
    assert format_utc(f.T0) == "2027-01-12T15:00:00.000000Z"
    dumped = f.bar_item().model_dump(mode="json")
    assert dumped["effective_at_utc"] == "2027-01-12T15:00:00.000000Z"


def test_nulls_are_retained_not_omitted():
    dumped = f.bar_item().model_dump(mode="json")
    assert dumped["research_reference"] is None
    assert dumped["temporal_scope"]["valid_until_utc"] is None


def test_remove_paths_removes_only_the_listed_path():
    dump = {"a": {"b": 1, "c": 2}, "d": 3}
    assert remove_paths(dump, [("a", "b")]) == {"a": {"c": 2}, "d": 3}
    assert dump == {"a": {"b": 1, "c": 2}, "d": 3}


def test_golden_item_id_detects_serialization_drift():
    assert f.bar_item().item_id == GOLDEN_BAR_ITEM_ID


def test_configuration_identity():
    assert configuration_identity(None) == "cfg_none"
    first = configuration_identity({"max_abs_spread": "0.5"})
    assert first.startswith("cfg1_") and len(first) == 69
    assert first == configuration_identity({"max_abs_spread": "0.5"})
    assert first != configuration_identity({"max_abs_spread": "0.6"})


# --- Item identity: substantive vs operational fields (§L.1, §L.2) ----------------------


def test_retry_with_same_temporal_identity_gets_same_id():
    first = f.bar_content()
    retry = f.rebuild(
        first,
        provenance=f.rebuild_provenance(
            first, generated_at_utc=first.provenance.generated_at_utc + timedelta(minutes=3)
        ),
    )
    assert compute_item_id(first) == compute_item_id(retry)


def test_identical_observations_at_different_times_get_different_ids():
    ten = f.bar_item(f.T0)
    ten_oh_five = f.bar_item(f.T0 + timedelta(minutes=5))
    assert ten.payload["close"] == ten_oh_five.payload["close"]
    assert ten.item_id != ten_oh_five.item_id


@pytest.mark.parametrize(
    "change",
    [
        "effective_at",
        "source_observed_at",
        "session_scope",
        "validity",
        "payload_timestamp",
        "source_reference_time",
        "producer_version",
        "configuration",
        "commit",
        "parents",
    ],
)
def test_every_substantive_field_changes_the_id(change):
    base = f.bar_content()
    prov = base.provenance
    if change == "effective_at":
        other = f.rebuild(base, effective_at_utc=base.effective_at_utc + timedelta(seconds=1))
    elif change == "source_observed_at":
        other = f.rebuild(
            base,
            provenance=f.rebuild_provenance(
                base, source_observed_at_utc=prov.source_observed_at_utc - timedelta(seconds=1)
            ),
        )
    elif change == "session_scope":
        other = f.rebuild(
            f.bar_content(subjects=[f.SPY]),
            temporal_scope=EvidenceTemporalScope(),
        )
        base = f.bar_content(subjects=[f.SPY])
    elif change == "validity":
        other = f.rebuild(
            base,
            temporal_scope=EvidenceTemporalScope(
                session_date=f.SESSION,
                valid_from_utc=f.T0,
                valid_until_utc=f.T0 + timedelta(minutes=5),
            ),
        )
    elif change == "payload_timestamp":
        other = f.rebuild(
            base,
            payload={**base.payload, "bar_timestamp": format_utc(f.T0 - timedelta(minutes=10))},
        )
    elif change == "source_reference_time":
        other = f.rebuild(
            base,
            provenance=f.rebuild_provenance(
                base, source_references=[f.bar_row_ref(f.T0 - timedelta(minutes=10))]
            ),
        )
    elif change == "producer_version":
        other = f.rebuild(base, provenance=f.rebuild_provenance(base, producer_version="1.0.1"))
    elif change == "configuration":
        other = f.rebuild(
            base,
            provenance=f.rebuild_provenance(
                base, configuration_identity=configuration_identity({"x": "1"})
            ),
        )
    elif change == "commit":
        other = f.rebuild(
            base, provenance=f.rebuild_provenance(base, code_commit_sha=f.OTHER_COMMIT)
        )
    else:
        parent = f.bar_item(f.T0 - timedelta(minutes=5))
        other = f.rebuild(
            base, provenance=f.rebuild_provenance(base, parent_evidence_ids=[parent.item_id])
        )
    assert compute_item_id(base) != compute_item_id(other)


def test_inference_as_of_is_substantive_while_generated_at_is_not():
    bar = f.bar_item()
    first = f.inference_item([bar], f.T0)
    later_as_of = f.inference_item([bar], f.T0 + timedelta(minutes=1))
    assert first.item_id != later_as_of.item_id
    regenerated = f.rebuild(
        first,
        provenance=f.rebuild_provenance(
            first, generated_at_utc=first.provenance.generated_at_utc + timedelta(hours=1)
        ),
    )
    assert compute_item_id(regenerated) == first.item_id


def test_model_rerun_with_different_output_gets_a_new_id():
    bar = f.bar_item()
    first = f.inference_item([bar])
    rerun = f.inference_item([bar], payload={"claim_code": "other"})
    assert first.item_id != rerun.item_id


def test_genuine_revision_gets_a_new_id():
    original = f.bar_item()
    revised = f.bar_item(
        revision=EvidenceRevision(
            revision_number=2,
            supersedes_item_id=original.item_id,
            revision_reason=RevisionReason.SOURCE_REVISION,
        )
    )
    assert revised.item_id != original.item_id


# --- Envelope, conflict and registry identities ------------------------------------------


def _envelope_content(items, *, emitted_offset=0, availability=None):
    registry = f.make_registry()
    return EvidenceEnvelopeContent(
        registry_version_id=compute_registry_version_id(registry),
        producer_id=f.BARS,
        producer_version="1.0.0",
        producer_run_key=compute_producer_run_key(
            producer_id=f.BARS,
            producer_version="1.0.0",
            configuration_identity="cfg_none",
            run_as_of_utc=f.T0,
            input_digest="1" * 64,
        ),
        run_as_of_utc=f.T0,
        availability=availability or EvidenceAvailability(state=AvailabilityState.AVAILABLE),
        items=sorted(items, key=lambda i: i.item_id),
        emitted_at_utc=f.T0 + timedelta(seconds=10 + emitted_offset),
    )


def test_envelope_id_excludes_emission_time_and_uses_item_ids():
    items = [f.bar_item(), f.bar_item(f.T0 + timedelta(minutes=5))]
    first = seal_envelope(_envelope_content(items))
    again = seal_envelope(_envelope_content(items, emitted_offset=30))
    assert first.envelope_id == again.envelope_id
    assert first.envelope_id.startswith("eve1_")
    fewer = seal_envelope(_envelope_content(items[:1]))
    assert fewer.envelope_id != first.envelope_id


def test_envelope_rejects_forged_id_and_unsorted_items():
    items = sorted([f.bar_item(), f.bar_item(f.T0 + timedelta(minutes=5))], key=lambda i: i.item_id)
    content = _envelope_content(items)
    fields = {n: getattr(content, n) for n in type(content).model_fields}
    with pytest.raises(ValidationError):
        EvidenceEnvelope(**fields, envelope_id="eve1_" + "0" * 64)
    with pytest.raises(ValidationError):
        EvidenceEnvelopeContent(**{**fields, "items": list(reversed(items))})


def test_envelope_absence_must_be_explicit():
    unavailable = EvidenceAvailability(
        state=AvailabilityState.UNAVAILABLE, reason_code="bars_missing"
    )
    with pytest.raises(ValidationError):
        _envelope_content([], availability=unavailable)
    with pytest.raises(ValidationError):
        _envelope_content([f.bar_item()], availability=unavailable)
    with pytest.raises(ValidationError):
        _envelope_content([f.missing_item()])
    assert _envelope_content([f.missing_item()], availability=unavailable)


def test_envelope_items_must_come_from_the_envelope_producer():
    content = _envelope_content([f.bar_item()])
    fields = {n: getattr(content, n) for n in EvidenceEnvelopeContent.model_fields}
    fields["items"] = [f.calc_item([f.bar_item()])]
    with pytest.raises(ValidationError):
        EvidenceEnvelopeContent(**fields)


def test_producer_run_key_is_reproducible():
    kwargs = dict(
        producer_id=f.BARS,
        producer_version="1.0.0",
        configuration_identity="cfg_none",
        run_as_of_utc=f.T0,
        input_digest="1" * 64,
    )
    assert compute_producer_run_key(**kwargs) == compute_producer_run_key(**kwargs)
    assert compute_producer_run_key(**kwargs) != compute_producer_run_key(
        **{**kwargs, "run_as_of_utc": f.T0 + timedelta(seconds=1)}
    )


def _conflict(evaluated_offset=0, detected_offset=0):
    a, b = f.bar_item(), f.bar_item(f.T0 + timedelta(minutes=5))
    return EvidenceConflictContent(
        conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
        involved_item_ids=sorted([a.item_id, b.item_id]),
        detection_method=DetectionMethod.DETERMINISTIC_RULE,
        detector_producer_id=f.DETECTOR,
        detector_version="1.0.0",
        rule_id="provider-disagreement.v1",
        severity=ConflictSeverity.INFORMATIONAL,
        status=ConflictStatus.UNRESOLVED,
        evaluated_as_of_utc=f.T0 + timedelta(minutes=10 + evaluated_offset),
        detected_at_utc=f.T0 + timedelta(minutes=11 + detected_offset),
    )


def test_conflict_id_includes_evaluated_as_of_but_not_detection_time():
    base = _conflict()
    assert compute_conflict_id(base) == compute_conflict_id(_conflict(detected_offset=5))
    assert compute_conflict_id(base) != compute_conflict_id(_conflict(evaluated_offset=5))
    assert seal_conflict(base).conflict_id.startswith("evc1_")


def test_registry_version_id_is_content_addressed():
    first = compute_registry_version_id(f.make_registry())
    assert first.startswith("evr1_")
    assert first == compute_registry_version_id(f.make_registry())
    assert first != compute_registry_version_id(f.make_registry(registry_label="other"))
