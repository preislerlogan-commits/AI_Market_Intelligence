"""Point-in-time bundle construction, readiness, conflicts, supersession and
citations (design §B.13-§B.16, §I, §M, §N). Synthetic data only."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from market_intelligence.evidence.bundle import RecordedConflict, RecordedItem
from market_intelligence.evidence.contracts import (
    ConflictResolution,
    EvidenceBundleManifest,
    EvidenceCitation,
    EvidenceConflictContent,
    EvidenceRevision,
    compute_bundle_id,
    seal_conflict,
)
from market_intelligence.evidence.enums import (
    AmbiguityReason,
    BundlePurpose,
    CitationRole,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    ConsumerId,
    ConsumerPermission,
    DetectionMethod,
    EvidenceKind,
    FreshnessState,
    MissingProducerReason,
    ResolutionKind,
    RevisionReason,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.validation import (
    expected_severity,
    validate_citations,
    validate_conflict,
)
from market_intelligence.tests import evidence_fixtures as f

REGISTRY = f.make_registry()
AS_OF = f.T0 + timedelta(minutes=1)


def _core():
    bar = f.bar_item()
    calc = f.calc_item([bar])
    clock = f.clock_item(f.T0)
    return bar, calc, clock


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


# --- Readiness ---------------------------------------------------------------------------


def test_ready_bundle_with_current_required_evidence():
    bar, calc, clock = _core()
    manifest = f.bundle(f.recorded(bar, calc, clock))
    assert manifest.machine_decision_ready
    required = {e.producer_id for e in manifest.entries if e.required}
    assert required == {f.BARS, f.CALC}
    assert manifest.bundle_id.startswith("evb1_")
    assert manifest.missing_required_producers == []


def test_stale_required_evidence_makes_the_bundle_not_ready():
    bar, calc, clock = _core()
    later_clock = f.clock_item(f.T0 + timedelta(minutes=20))
    manifest = f.bundle(
        f.recorded(bar, calc, later_clock),
        as_of=f.T0 + timedelta(minutes=20, seconds=30),
    )
    assert not manifest.machine_decision_ready
    states = {e.producer_id: e.freshness.state for e in manifest.entries if e.required}
    assert states[f.BARS] is FreshnessState.STALE
    reasons = {m.producer_id: m.reason for m in manifest.missing_required_producers}
    assert reasons[f.BARS] is MissingProducerReason.ONLY_STALE_ITEMS


def test_unknown_freshness_is_not_ready():
    bar, calc, _ = _core()
    manifest = f.bundle(f.recorded(bar, calc))  # no clock-health fact at all
    assert not manifest.machine_decision_ready
    assert all(
        e.freshness.state is FreshnessState.UNKNOWN for e in manifest.entries if e.required
    )


def test_kind_stays_static_across_bundles_at_different_as_of():
    bar, calc, _ = _core()
    clocks = [f.clock_item(f.T0 + timedelta(minutes=m)) for m in (0, 10, 20)]
    seen = []
    for minutes, clock in zip((1, 10, 20), clocks):
        manifest = f.bundle(
            f.recorded(bar, calc, clock, at=f.T0 + timedelta(minutes=minutes - 1)),
            as_of=f.T0 + timedelta(minutes=minutes),
        )
        entry = next(e for e in manifest.entries if e.item_id == bar.item_id)
        seen.append((entry.evidence_kind, entry.freshness.state))
    assert seen == [
        (EvidenceKind.CONFIRMED_FACT, FreshnessState.CURRENT),
        (EvidenceKind.CONFIRMED_FACT, FreshnessState.AGING),
        (EvidenceKind.CONFIRMED_FACT, FreshnessState.STALE),
    ]


def test_status_items_never_satisfy_a_requirement():
    bar = f.bar_item()
    missing = f.missing_item()
    calc = f.calc_item([bar])
    manifest = f.bundle(f.recorded(missing, calc, f.clock_item(f.T0)))
    reasons = {m.producer_id: m.reason for m in manifest.missing_required_producers}
    assert reasons[f.BARS] is MissingProducerReason.PRODUCER_UNAVAILABLE
    assert not manifest.machine_decision_ready
    stale_status = f.stale_item(bar, f.T0)
    manifest = f.bundle(f.recorded(stale_status, calc, f.clock_item(f.T0)))
    reasons = {m.producer_id: m.reason for m in manifest.missing_required_producers}
    assert reasons[f.BARS] is MissingProducerReason.ONLY_STALE_ITEMS


def test_missing_and_unimplemented_producers_are_listed():
    manifest = f.bundle([], purpose=BundlePurpose.SETUP_DETAIL)
    assert [(m.producer_id, m.reason) for m in manifest.missing_required_producers] == [
        (f.FUTURE, MissingProducerReason.PRODUCER_NOT_IMPLEMENTED)
    ]
    manifest = f.bundle(f.recorded(f.clock_item(f.T0)))
    assert {m.reason for m in manifest.missing_required_producers} == {
        MissingProducerReason.NO_ITEM_RECORDED
    }


def test_filters_cannot_hide_required_absence_or_staleness():
    bar, calc, clock = _core()
    narrow = f.query(evidence_kinds=[EvidenceKind.HISTORICAL_RESEARCH_RESULT])
    manifest = f.bundle(f.recorded(bar, calc, clock), q=narrow)
    assert {e.item_id for e in manifest.entries if e.required} == {bar.item_id, calc.item_id}
    assert manifest.machine_decision_ready


def test_machine_mode_excludes_inference():
    bar, calc, clock = _core()
    inference = f.inference_item([bar, calc], f.T0 + timedelta(seconds=30))
    permissions = [
        ConsumerPermission.READ_CALCULATIONS,
        ConsumerPermission.READ_CONFLICTS,
        ConsumerPermission.READ_FACTS,
        ConsumerPermission.READ_RESEARCH,
    ]
    manifest = f.bundle(
        f.recorded(bar, calc, clock, inference),
        consumer=ConsumerId.SETUP_RANKER,
        machine=True,
        permissions=permissions,
    )
    assert inference.item_id not in {e.item_id for e in manifest.entries}
    assert all(e.machine_decision_eligible for e in manifest.entries)


def test_dashboard_sees_labelled_inference():
    bar, calc, clock = _core()
    inference = f.inference_item([bar, calc], f.T0 + timedelta(seconds=30))
    manifest = f.bundle(f.recorded(bar, calc, clock, inference))
    entry = next(e for e in manifest.entries if e.item_id == inference.item_id)
    assert entry.evidence_kind is EvidenceKind.CURRENT_INFERENCE
    assert entry.machine_decision_eligible is False


# --- Consumer boundaries --------------------------------------------------------------------


def test_unregistered_consumer_and_ungranted_purpose_are_refused():
    bar, calc, clock = _core()
    assert _reason(f.bundle, f.recorded(bar), consumer=ConsumerId.ASSISTANT) == (
        "consumer_not_registered"
    )
    assert _reason(
        f.bundle, f.recorded(bar), purpose=BundlePurpose.PREMARKET_BRIEFING
    ) == "purpose_not_granted"


def test_machine_mode_requires_a_grant():
    with pytest.raises(ValidationError):
        f.bundle([], machine=True)  # dashboard cannot even form a machine context


def test_superseded_reads_require_permission():
    q = f.query(include_superseded=True)
    assert _reason(f.bundle, [], q=q) == "superseded_read_not_permitted"


def test_query_window_is_bounded_by_the_selection_rule():
    q = f.query(effective_to_utc=f.T0 + timedelta(days=3))
    assert _reason(f.bundle, [], q=q) == "query_window_too_large"


def test_bundle_refuses_rather_than_truncates():
    bar, calc, clock = _core()
    q = f.query(max_entries=1)
    assert _reason(f.bundle, f.recorded(bar, calc, clock), q=q) == "bundle_exceeds_max_entries"


# --- Point in time and supersession --------------------------------------------------------


def test_items_recorded_after_as_of_are_invisible():
    bar, calc, clock = _core()
    late = RecordedItem(item=f.bar_item(f.T0 + timedelta(minutes=5)),
                        recorded_at_utc=AS_OF + timedelta(seconds=1))
    manifest = f.bundle([*f.recorded(bar, calc, clock), late])
    assert late.item.item_id not in {e.item_id for e in manifest.entries}


def test_later_correction_does_not_change_an_earlier_bundle():
    bar, calc, clock = _core()
    earlier = f.bundle(f.recorded(bar, calc, clock))
    correction = f.bar_item(
        close="500.2",
        revision=EvidenceRevision(
            revision_number=2,
            supersedes_item_id=bar.item_id,
            revision_reason=RevisionReason.SOURCE_REVISION,
        ),
    )
    records = [
        *f.recorded(bar, calc, clock),
        RecordedItem(item=correction, recorded_at_utc=f.T0 + timedelta(minutes=3)),
    ]
    rebuilt = f.bundle(records)
    assert rebuilt.bundle_id == earlier.bundle_id
    later = f.bundle(records, as_of=f.T0 + timedelta(minutes=4))
    ids = {e.item_id for e in later.entries}
    assert correction.item_id in ids and bar.item_id not in ids


def test_audit_can_see_the_whole_revision_chain():
    bar, calc, clock = _core()
    correction = f.bar_item(
        close="500.2",
        revision=EvidenceRevision(
            revision_number=2,
            supersedes_item_id=bar.item_id,
            revision_reason=RevisionReason.SOURCE_REVISION,
        ),
    )
    q = f.query(include_superseded=True)
    manifest = f.bundle(
        f.recorded(bar, calc, clock, correction),
        consumer=ConsumerId.AUDIT_EXPORT,
        permissions=sorted([*f.ALL_READS, ConsumerPermission.READ_SUPERSEDED]),
        q=q,
    )
    entries = {e.item_id: e for e in manifest.entries}
    assert entries[bar.item_id].superseded_as_of
    assert entries[bar.item_id].latest_revision_item_id == correction.item_id
    assert not entries[correction.item_id].superseded_as_of


def test_revision_branches_are_not_resolved_by_the_builder():
    bar, calc, clock = _core()
    branches = [
        f.bar_item(
            close=close,
            revision=EvidenceRevision(
                revision_number=2,
                supersedes_item_id=bar.item_id,
                revision_reason=RevisionReason.SOURCE_REVISION,
            ),
        )
        for close in ("500.2", "500.3")
    ]
    manifest = f.bundle(f.recorded(bar, calc, clock, *branches))
    ids = {e.item_id for e in manifest.entries}
    assert {b.item_id for b in branches} <= ids


def test_duplicate_append_is_idempotent():
    bar, calc, clock = _core()
    records = [*f.recorded(bar, calc, clock), *f.recorded(bar, at=f.T0 + timedelta(seconds=20))]
    assert f.bundle(records).bundle_id == f.bundle(f.recorded(bar, calc, clock)).bundle_id


# --- Bundle hashes --------------------------------------------------------------------------


def test_bundle_hash_is_reproducible_and_order_independent():
    bar, calc, clock = _core()
    first = f.bundle(f.recorded(bar, calc, clock))
    shuffled = f.bundle(f.recorded(clock, bar, calc))
    assert first.bundle_id == shuffled.bundle_id
    assert first.bundle_id == compute_bundle_id(first)


def test_bundle_as_of_participates_but_built_at_does_not():
    bar, calc, clock = _core()
    first = f.bundle(f.recorded(bar, calc, clock))
    later = f.bundle(f.recorded(bar, calc, clock), as_of=AS_OF + timedelta(seconds=1))
    assert first.bundle_id != later.bundle_id
    fields = {n: getattr(first, n) for n in EvidenceBundleManifest.model_fields}
    fields["built_at_utc"] = fields["built_at_utc"] + timedelta(hours=1)
    assert EvidenceBundleManifest(**fields).bundle_id == first.bundle_id


def test_manifest_cannot_claim_readiness_with_missing_producers():
    manifest = f.bundle([])
    fields = {n: getattr(manifest, n) for n in EvidenceBundleManifest.model_fields}
    fields.pop("bundle_id")
    fields["machine_decision_ready"] = True
    with pytest.raises(ValidationError):
        from market_intelligence.evidence.contracts import EvidenceBundleManifestContent

        EvidenceBundleManifestContent(**fields)


# --- Conflicts --------------------------------------------------------------------------------


def _conflict(a, b, **overrides):
    fields = dict(
        conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
        involved_item_ids=sorted([a.item_id, b.item_id]),
        detection_method=DetectionMethod.DETERMINISTIC_RULE,
        detector_producer_id=f.DETECTOR,
        detector_version="1.0.0",
        rule_id="provider-disagreement.v1",
        severity=ConflictSeverity.CRITICAL,
        status=ConflictStatus.UNRESOLVED,
        evaluated_as_of_utc=f.T0 + timedelta(seconds=30),
        detected_at_utc=f.T0 + timedelta(seconds=31),
    )
    fields.update(overrides)
    return seal_conflict(EvidenceConflictContent(**fields))


def test_severity_table():
    assert expected_severity(ConflictType.PROVIDER_DISAGREEMENT, True) is ConflictSeverity.CRITICAL
    assert expected_severity(ConflictType.FRESHNESS_MISMATCH, True) is ConflictSeverity.CRITICAL
    assert expected_severity(ConflictType.REVISION_DISAGREEMENT, True) is ConflictSeverity.MATERIAL
    assert (
        expected_severity(ConflictType.INFERENCE_VS_OBSERVATION, False)
        is ConflictSeverity.MATERIAL
    )
    assert (
        expected_severity(ConflictType.PROVIDER_DISAGREEMENT, False)
        is ConflictSeverity.INFORMATIONAL
    )


def test_conflict_validation():
    bar, calc, _ = _core()
    items = {bar.item_id: bar, calc.item_id: calc}
    conflict = _conflict(bar, calc)
    validate_conflict(conflict, items, REGISTRY, required_item_ids=frozenset(items))
    assert _reason(validate_conflict, conflict, items, REGISTRY) == "severity_mismatch"
    early = _conflict(bar, calc, evaluated_as_of_utc=f.T0 - timedelta(seconds=1))
    assert _reason(
        validate_conflict, early, items, REGISTRY, required_item_ids=frozenset(items)
    ) == "conflict_before_evidence"
    assert _reason(validate_conflict, conflict, {}, REGISTRY) == "unknown_involved_item"


def test_only_registered_conflict_detectors():
    bar, calc, _ = _core()
    items = {bar.item_id: bar, calc.item_id: calc}
    wrong = _conflict(bar, calc, detector_producer_id=f.BARS)
    assert _reason(validate_conflict, wrong, items, REGISTRY) == "not_a_conflict_detector"


def test_unresolved_critical_conflict_blocks_readiness_and_is_listed():
    bar, calc, clock = _core()
    conflict = _conflict(bar, calc)
    manifest = f.bundle(
        f.recorded(bar, calc, clock),
        conflicts=[
            RecordedConflict(conflict=conflict, recorded_at_utc=f.T0 + timedelta(seconds=40))
        ],
    )
    assert conflict.conflict_id in manifest.conflict_ids
    assert manifest.unresolved_material_conflict_count == 1
    assert not manifest.machine_decision_ready


def test_resolution_supersedes_only_the_prior_conflict_status_record():
    bar, calc, clock = _core()
    corrected = f.bar_item(
        close="500.2",
        revision=EvidenceRevision(
            revision_number=2,
            supersedes_item_id=bar.item_id,
            revision_reason=RevisionReason.SOURCE_REVISION,
        ),
    )
    prior = _conflict(bar, calc)
    resolved = _conflict(
        bar,
        calc,
        status=ConflictStatus.RESOLVED,
        resolution=ConflictResolution(
            resolution_kind=ResolutionKind.UNDERLYING_ITEM_REVISED,
            resolution_evidence_ids=[corrected.item_id],
            supersedes_conflict_id=prior.conflict_id,
        ),
        evaluated_as_of_utc=f.T0 + timedelta(minutes=2),
    )
    items = {i.item_id: i for i in (bar, calc, corrected)}
    validate_conflict(
        resolved, items, REGISTRY, required_item_ids=frozenset(items), prior=prior
    )
    # The evidence itself is untouched: the original bar is unchanged and
    # still stored; only its own revision supersedes it.
    assert bar.item_id == f.bar_item().item_id
    before = bar.model_dump_json()
    records = [
        RecordedConflict(conflict=prior, recorded_at_utc=f.T0 + timedelta(seconds=40)),
        RecordedConflict(conflict=resolved, recorded_at_utc=f.T0 + timedelta(minutes=2)),
    ]
    manifest = f.bundle(
        f.recorded(bar, calc, f.clock_item(f.T0 + timedelta(minutes=2))),
        as_of=f.T0 + timedelta(minutes=3),
        conflicts=records,
    )
    assert bar.model_dump_json() == before
    assert {prior.conflict_id, resolved.conflict_id} <= set(manifest.conflict_ids)
    assert manifest.unresolved_material_conflict_count == 0


def test_resolution_must_name_the_prior_status_record():
    bar, calc, _ = _core()
    prior = _conflict(bar, calc)
    other = _conflict(bar, calc, evaluated_as_of_utc=f.T0 + timedelta(seconds=45))
    resolved = _conflict(
        bar,
        calc,
        status=ConflictStatus.ACKNOWLEDGED_UNRESOLVABLE,
        resolution=ConflictResolution(
            resolution_kind=ResolutionKind.MANUAL_ACKNOWLEDGEMENT,
            supersedes_conflict_id=other.conflict_id,
        ),
        evaluated_as_of_utc=f.T0 + timedelta(minutes=1),
    )
    items = {bar.item_id: bar, calc.item_id: calc}
    assert _reason(
        validate_conflict,
        resolved,
        items,
        REGISTRY,
        required_item_ids=frozenset(items),
        prior=prior,
    ) == "resolution_must_supersede_prior_status"


def test_underlying_item_revised_must_cite_a_revision_of_involved_evidence():
    bar, calc, _ = _core()
    unrelated = f.bar_item(f.T0 + timedelta(minutes=5))
    prior = _conflict(bar, calc)
    resolved = _conflict(
        bar,
        calc,
        status=ConflictStatus.RESOLVED,
        resolution=ConflictResolution(
            resolution_kind=ResolutionKind.UNDERLYING_ITEM_REVISED,
            resolution_evidence_ids=[unrelated.item_id],
            supersedes_conflict_id=prior.conflict_id,
        ),
        evaluated_as_of_utc=f.T0 + timedelta(minutes=6),
    )
    items = {i.item_id: i for i in (bar, calc, unrelated)}
    assert _reason(
        validate_conflict,
        resolved,
        items,
        REGISTRY,
        required_item_ids=frozenset({bar.item_id, calc.item_id}),
        prior=prior,
    ) == "cited_item_is_not_a_revision_of_involved_evidence"


# --- Citations ---------------------------------------------------------------------------------


def test_citations_resolve_only_inside_their_bundle():
    bar, calc, clock = _core()
    manifest = f.bundle(f.recorded(bar, calc, clock))
    good = EvidenceCitation(
        bundle_id=manifest.bundle_id,
        claim_index=0,
        cited_item_ids=sorted([bar.item_id, calc.item_id]),
        citation_role=CitationRole.SUPPORTS_CLAIM,
        cited_kinds=sorted([EvidenceKind.CONFIRMED_FACT, EvidenceKind.DETERMINISTIC_CALCULATION]),
    )
    validate_citations(manifest, [good])
    stranger = f.bar_item(f.T0 + timedelta(hours=1))
    outside = good.model_copy(update={"cited_item_ids": [stranger.item_id]})
    assert _reason(validate_citations, manifest, [outside]) == "citation_outside_bundle"
    wrong_kind = good.model_copy(update={"cited_kinds": [EvidenceKind.CONFIRMED_FACT]})
    assert _reason(validate_citations, manifest, [wrong_kind]) == "citation_kind_mismatch"
    other_bundle = good.model_copy(update={"bundle_id": "evb1_" + "0" * 64})
    assert _reason(validate_citations, manifest, [other_bundle]) == "citation_outside_bundle"


# --- Requirement identity, tie-breaking and query isolation --------------------------


def _other_subject_bar():
    """A bar fact from the same producer and schema, about a different
    instrument; it must never satisfy the SPY requirement."""
    qqq = f.EvidenceSubject(
        subject_type=f.SubjectType.INSTRUMENT, subject_id="instrument:us_equity:QQQ"
    )
    return f.bar_item(subjects=[qqq, f.session_subject(f.SESSION)])


def test_requirements_are_matched_by_full_identity_not_producer():
    bar, calc, clock = _core()
    other = _other_subject_bar()
    manifest = f.bundle(f.recorded(other, calc, clock))
    reasons = {m.requirement_id: m.reason for m in manifest.missing_required_producers}
    assert reasons == {"req.bars.spy": MissingProducerReason.NO_ITEM_RECORDED}
    assert other.item_id not in {e.item_id for e in manifest.entries if e.required}


def test_cross_subject_selection_picks_only_the_matching_subject():
    bar, calc, clock = _core()
    later_other = f.bar_item(
        f.T0 + timedelta(seconds=30),
        subjects=[
            f.EvidenceSubject(
                subject_type=f.SubjectType.INSTRUMENT, subject_id="instrument:us_equity:QQQ"
            ),
            f.session_subject(f.SESSION),
        ],
    )
    manifest = f.bundle(f.recorded(bar, later_other, calc, clock))
    required = {e.item_id for e in manifest.entries if e.required}
    assert bar.item_id in required and later_other.item_id not in required
    assert manifest.machine_decision_ready


def test_requirement_checks_version_and_configuration():
    bar, calc, clock = _core()
    other_config = f.bar_item(
        provenance=f.rebuild_provenance(
            bar, configuration_identity="cfg1_" + "9" * 64
        )
    )
    manifest = f.bundle(f.recorded(other_config, calc, clock))
    reasons = {m.requirement_id: m.reason for m in manifest.missing_required_producers}
    assert "req.bars.spy" in reasons


def test_tie_break_prefers_later_observation_then_revision():
    bar, calc, clock = _core()
    later_observed = f.bar_item(
        close="500.3",
        provenance=f.rebuild_provenance(bar, source_observed_at_utc=f.T0),
    )
    earlier_observed = f.bar_item(
        close="500.4",
        provenance=f.rebuild_provenance(
            bar, source_observed_at_utc=f.T0 - timedelta(seconds=10)
        ),
    )
    manifest = f.bundle(f.recorded(earlier_observed, later_observed, calc, clock))
    required = [e.item_id for e in manifest.entries if e.required and e.producer_id == f.BARS]
    assert required == [later_observed.item_id]


def test_query_filters_do_not_change_requirement_selection():
    bar, calc, clock = _core()
    other = _other_subject_bar()
    records = f.recorded(bar, other, calc, clock)
    only_qqq = f.query(subject_ids=["instrument:us_equity:QQQ"])
    only_facts = f.query(evidence_kinds=[EvidenceKind.CONFIRMED_FACT])
    required_sets = []
    for q in (f.query(), only_qqq, only_facts):
        manifest = f.bundle(records, q=q)
        required_sets.append({e.item_id for e in manifest.entries if e.required})
        assert manifest.machine_decision_ready
    assert required_sets[0] == required_sets[1] == required_sets[2] == {bar.item_id, calc.item_id}


def test_query_selection_is_isolated_from_other_queries():
    bar, calc, clock = _core()
    other = _other_subject_bar()
    records = f.recorded(bar, other, calc, clock)
    qqq = f.bundle(records, q=f.query(subject_ids=["instrument:us_equity:QQQ"]))
    spy_only = f.bundle(records, q=f.query(subject_ids=["system:clock"]))
    assert other.item_id in {e.item_id for e in qqq.entries}
    assert other.item_id not in {e.item_id for e in spy_only.entries}
    assert qqq.bundle_id != spy_only.bundle_id


def test_status_items_explain_only_their_own_requirement():
    calc = f.calc_item([f.bar_item()])
    qqq_missing = f.missing_item(
        subjects=[
            f.EvidenceSubject(
                subject_type=f.SubjectType.INSTRUMENT, subject_id="instrument:us_equity:QQQ"
            )
        ],
        payload={
            "checked_at_utc": f.format_utc(f.T0),
            "expected_component": None,
            "expected_producer_id": f.BARS,
            "expected_subject_id": "instrument:us_equity:QQQ",
            "query_sha256": "0" * 64,
            "reason_code": "bars_missing",
        },
    )
    manifest = f.bundle(f.recorded(qqq_missing, calc, f.clock_item(f.T0)))
    reasons = {m.requirement_id: m.reason for m in manifest.missing_required_producers}
    assert reasons["req.bars.spy"] is MissingProducerReason.NO_ITEM_RECORDED


def test_requirements_cannot_name_status_kinds_or_unregistered_pairs():
    with pytest.raises(ValidationError):
        f.requirement("req.bad", f.BARS, EvidenceKind.MISSING_EVIDENCE, "missing_evidence.v1")
    with pytest.raises(ValidationError):
        f.make_registry(
            selection_rules=[
                f.SelectionRule(
                    selection_rule_id="sel.bad.v1",
                    purpose=BundlePurpose.LIVE_MARKET_STATE,
                    requirements=[
                        f.requirement(
                            "req.bad", f.BARS, EvidenceKind.DETERMINISTIC_CALCULATION,
                            "synthetic_calc.v1",
                        )
                    ],
                    max_window_seconds=86_400,
                )
            ]
        )


# --- Ambiguous latest evidence ------------------------------------------------------------


def _bars_ambiguity(manifest):
    return {a.requirement_id: a for a in manifest.ambiguous_requirements}.get("req.bars.spy")


def _revision(of, number, close, **overrides):
    return f.bar_item(
        close=close,
        revision=EvidenceRevision(
            revision_number=number,
            supersedes_item_id=of.item_id,
            revision_reason=RevisionReason.SOURCE_REVISION,
        ),
        **overrides,
    )


def test_identical_retries_collapse_to_one_item_and_are_not_ambiguous():
    bar, calc, clock = _core()
    retry = f.bar_item()  # same substantive content, so the same content-addressed ID
    assert retry.item_id == bar.item_id
    records = [
        *f.recorded(bar, calc, clock),
        *f.recorded(retry, at=f.T0 + timedelta(seconds=20)),
    ]
    manifest = f.bundle(records)
    assert manifest.ambiguous_requirements == []
    assert [e.item_id for e in manifest.entries if e.required and e.producer_id == f.BARS] == [
        bar.item_id
    ]
    assert manifest.machine_decision_ready


def test_single_remaining_item_is_selected_normally():
    bar, calc, clock = _core()
    manifest = f.bundle(f.recorded(bar, calc, clock))
    assert manifest.ambiguous_requirements == []
    assert manifest.machine_decision_ready


def test_linear_supersession_chain_selects_its_unique_latest_item():
    bar, calc, clock = _core()
    second = _revision(bar, 2, "500.2")
    third = _revision(second, 3, "500.3")
    manifest = f.bundle(f.recorded(bar, second, third, calc, clock))
    required = [e.item_id for e in manifest.entries if e.required and e.producer_id == f.BARS]
    assert required == [third.item_id]
    assert manifest.ambiguous_requirements == []
    assert manifest.machine_decision_ready


def test_two_same_rank_non_superseding_items_are_ambiguous():
    bar, calc, clock = _core()
    twin = f.bar_item(close="500.2")  # same times and revision number, different content
    manifest = f.bundle(f.recorded(bar, twin, calc, clock))
    ambiguity = _bars_ambiguity(manifest)
    assert ambiguity is not None
    assert ambiguity.reason is AmbiguityReason.COMPETING_OBSERVATIONS
    assert ambiguity.competing_item_ids == sorted([bar.item_id, twin.item_id])
    assert "req.bars.spy" not in {m.requirement_id for m in manifest.missing_required_producers}


def test_branched_revisions_are_ambiguous_even_when_one_branch_ranks_higher():
    bar, calc, clock = _core()
    branch_a = _revision(bar, 2, "500.2")
    branch_b = _revision(
        bar,
        2,
        "500.3",
        provenance=f.rebuild_provenance(
            bar, source_observed_at_utc=f.T0 - timedelta(seconds=10)
        ),
    )
    manifest = f.bundle(f.recorded(bar, branch_a, branch_b, calc, clock))
    ambiguity = _bars_ambiguity(manifest)
    assert ambiguity is not None
    assert ambiguity.reason is AmbiguityReason.BRANCHED_REVISIONS
    assert ambiguity.competing_item_ids == sorted([branch_a.item_id, branch_b.item_id])
    assert not manifest.machine_decision_ready


def test_ambiguity_blocks_readiness_and_marks_every_competitor_required():
    bar, calc, clock = _core()
    twin = f.bar_item(close="500.2")
    manifest = f.bundle(f.recorded(bar, twin, calc, clock))
    assert not manifest.machine_decision_ready
    required = {e.item_id for e in manifest.entries if e.required}
    assert {bar.item_id, twin.item_id} <= required
    assert all(e.freshness.state is FreshnessState.CURRENT for e in manifest.entries)


def test_manifest_refuses_readiness_with_an_ambiguous_requirement():
    bar, calc, clock = _core()
    twin = f.bar_item(close="500.2")
    manifest = f.bundle(f.recorded(bar, twin, calc, clock))
    fields = {n: getattr(manifest, n) for n in EvidenceBundleManifest.model_fields}
    fields.pop("bundle_id")
    fields["machine_decision_ready"] = True
    from market_intelligence.evidence.contracts import EvidenceBundleManifestContent

    with pytest.raises(ValidationError):
        EvidenceBundleManifestContent(**fields)


def test_competing_ids_are_retained_in_deterministic_order():
    bar, calc, clock = _core()
    twins = [f.bar_item(close=c) for c in ("500.2", "500.3", "500.4")]
    manifest = f.bundle(f.recorded(bar, *twins, calc, clock))
    ambiguity = _bars_ambiguity(manifest)
    expected = sorted([bar.item_id, *(t.item_id for t in twins)])
    assert ambiguity.competing_item_ids == expected


def test_input_order_does_not_change_the_result():
    bar, calc, clock = _core()
    twin = f.bar_item(close="500.2")
    forward = f.bundle(f.recorded(bar, twin, calc, clock))
    backward = f.bundle(f.recorded(clock, calc, twin, bar))
    assert forward.bundle_id == backward.bundle_id
    assert forward.ambiguous_requirements == backward.ambiguous_requirements


def test_item_id_never_decides_a_winner():
    bar, calc, clock = _core()
    twin = f.bar_item(close="500.2")
    manifest = f.bundle(f.recorded(bar, twin, calc, clock))
    # Neither twin is singled out: both are competitors and nothing is chosen.
    ambiguity = _bars_ambiguity(manifest)
    assert set(ambiguity.competing_item_ids) == {bar.item_id, twin.item_id}
    assert not manifest.machine_decision_ready


def test_ambiguity_links_a_matching_unresolved_conflict_but_stays_not_ready():
    bar, calc, clock = _core()
    twin = f.bar_item(close="500.2")
    conflict = seal_conflict(
        EvidenceConflictContent(
            conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
            involved_item_ids=sorted([bar.item_id, twin.item_id]),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=f.DETECTOR,
            detector_version="1.0.0",
            severity=ConflictSeverity.CRITICAL,
            status=ConflictStatus.UNRESOLVED,
            evaluated_as_of_utc=f.T0 + timedelta(seconds=30),
            detected_at_utc=f.T0 + timedelta(seconds=31),
        )
    )
    without = f.bundle(f.recorded(bar, twin, calc, clock))
    assert _bars_ambiguity(without).matching_unresolved_conflict_ids == []
    with_conflict = f.bundle(
        f.recorded(bar, twin, calc, clock),
        conflicts=[
            RecordedConflict(conflict=conflict, recorded_at_utc=f.T0 + timedelta(seconds=40))
        ],
    )
    assert _bars_ambiguity(with_conflict).matching_unresolved_conflict_ids == [
        conflict.conflict_id
    ]
    assert not with_conflict.machine_decision_ready


# --- Ambiguous clock facts at bundle level -------------------------------------------------


def test_ambiguous_clock_blocks_readiness_and_is_order_independent():
    bar, calc, clock = _core()
    other_clock = f.clock_item(f.T0, offset_ms=5000)
    forward = f.bundle(f.recorded(bar, calc, clock, other_clock))
    backward = f.bundle(f.recorded(other_clock, clock, calc, bar))
    assert forward.bundle_id == backward.bundle_id
    assert not forward.machine_decision_ready
    expected = sorted([clock.item_id, other_clock.item_id])
    for entry in forward.entries:
        if entry.required:
            assert entry.freshness.state is FreshnessState.UNKNOWN
            assert entry.freshness.clock_health_item_id is None
            assert entry.freshness.competing_clock_item_ids == expected


def test_a_uniquely_newer_clock_fact_keeps_the_bundle_ready():
    bar, calc, _ = _core()
    older_a = f.clock_item(f.T0 - timedelta(minutes=2), offset_ms=7)
    older_b = f.clock_item(f.T0 - timedelta(minutes=2), offset_ms=9)
    newer = f.clock_item(f.T0)
    manifest = f.bundle(f.recorded(bar, calc, older_a, older_b, newer))
    assert manifest.machine_decision_ready
