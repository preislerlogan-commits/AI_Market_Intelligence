"""Permanent Contract Selector boundary (design §H.3). The selector result
here is a genuine run of the existing deterministic selector over a synthetic
batch: two returned contracts and one rejected for a wide spread."""

from __future__ import annotations

from datetime import timedelta

import pytest

from market_intelligence.contract_selection.contracts import RejectionReason
from market_intelligence.evidence.enums import BundlePurpose, ConsumerId, ConsumerPermission
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.selector_boundary import (
    ContractRanking,
    ContractView,
    ContractViewPage,
    ReturnedContractSets,
    compute_contract_view_sha256,
    validate_contract_ranking,
    validate_contract_view,
    validate_contract_view_pages,
)
from market_intelligence.evidence.validation import validate_item
from market_intelligence.tests import evidence_fixtures as f

RESULT = f.selector_result()
RESEARCH_RESULT = f.selector_result(feed="indicative")
REGISTRY = f.make_registry()


def _view(result=RESULT, consumer=ConsumerId.DASHBOARD, **overrides):
    sets = ReturnedContractSets.from_result(result)
    fields = dict(
        consumer_id=consumer,
        eligible_contracts=sorted(sets.eligible),
        research_only_contracts=sorted(sets.research_only),
        rejection_counts=dict(sets.rejection_counts),
    )
    fields.update(overrides)
    return ContractView(**fields)


def _reason(fn, *args) -> str:
    with pytest.raises(EvidenceValidationError) as excinfo:
        fn(*args)
    return excinfo.value.reason


def test_fixture_is_a_genuine_selector_outcome():
    sets = ReturnedContractSets.from_result(RESULT)
    assert sets.eligible == {f.ELIGIBLE_A, f.ELIGIBLE_B}
    assert sets.research_only == frozenset()
    assert RESULT.rejection_counts[RejectionReason.SPREAD_TOO_WIDE] == 1
    assert f.REJECTED_WIDE not in sets.returned
    research = ReturnedContractSets.from_result(RESEARCH_RESULT)
    assert research.research_only == {f.ELIGIBLE_A, f.ELIGIBLE_B}
    assert research.eligible == frozenset()


@pytest.mark.parametrize(
    "consumer",
    [ConsumerId.DASHBOARD, ConsumerId.ASSISTANT, ConsumerId.CONTRACT_REVIEW_UI,
     ConsumerId.NOTIFICATION_LAYER],
)
def test_views_that_preserve_selector_outcomes_are_accepted(consumer):
    validate_contract_view(RESULT, _view(consumer=consumer))
    validate_contract_view(RESEARCH_RESULT, _view(RESEARCH_RESULT, consumer=consumer))


@pytest.mark.parametrize(
    "consumer",
    [ConsumerId.DASHBOARD, ConsumerId.ASSISTANT, ConsumerId.NOTIFICATION_LAYER],
)
def test_rejected_contracts_are_never_materialized_individually(consumer):
    listed = [f.ELIGIBLE_A, f.ELIGIBLE_B, f.REJECTED_WIDE]
    view = _view(consumer=consumer, eligible_contracts=listed)
    assert _reason(validate_contract_view, RESULT, view) == "contract_not_returned_by_selector"


def test_contracts_absent_from_the_selector_cannot_be_introduced():
    view = _view(eligible_contracts=[f.ELIGIBLE_A, f.ELIGIBLE_B, "SPY270112C00700000"])
    assert _reason(validate_contract_view, RESULT, view) == "contract_not_returned_by_selector"


def test_research_only_never_appears_as_eligible():
    sets = ReturnedContractSets.from_result(RESEARCH_RESULT)
    view = _view(
        RESEARCH_RESULT, eligible_contracts=sorted(sets.research_only), research_only_contracts=[]
    )
    assert _reason(validate_contract_view, RESEARCH_RESULT, view) == "selector_outcome_changed"


def test_returned_sets_cannot_be_suppressed():
    view = _view(eligible_contracts=[f.ELIGIBLE_A])
    assert _reason(validate_contract_view, RESULT, view) == "selector_outcome_changed"


def test_rejection_counts_and_reasons_remain_unchanged():
    counts = dict(RESULT.rejection_counts)
    counts[RejectionReason.SPREAD_TOO_WIDE] = 0
    assert _reason(validate_contract_view, RESULT, _view(rejection_counts=counts)) == (
        "rejection_counts_changed"
    )
    moved = dict(RESULT.rejection_counts)
    moved[RejectionReason.SPREAD_TOO_WIDE] = 0
    moved[RejectionReason.DELTA_OUTSIDE_RANGE] = 1
    assert _reason(validate_contract_view, RESULT, _view(rejection_counts=moved)) == (
        "rejection_counts_changed"
    )
    dropped = {k: v for k, v in RESULT.rejection_counts.items() if v == 0}
    assert _reason(validate_contract_view, RESULT, _view(rejection_counts=dropped)) == (
        "rejection_counts_changed"
    )


def test_a_contract_cannot_be_listed_twice():
    view = _view(eligible_contracts=[f.ELIGIBLE_A, f.ELIGIBLE_A, f.ELIGIBLE_B])
    assert _reason(validate_contract_view, RESULT, view) == "contract_listed_twice"


# --- Future ranking shape (no ranker is implemented) ------------------------------------------


def test_ranking_within_the_returned_eligible_set_is_accepted():
    validate_contract_ranking(
        RESULT,
        ContractRanking(eligible_ranking=[f.ELIGIBLE_B, f.ELIGIBLE_A], research_only_ranking=[]),
    )


def test_ranking_preserves_status_partitions():
    sets = ReturnedContractSets.from_result(RESEARCH_RESULT)
    mixed = ContractRanking(eligible_ranking=sorted(sets.research_only), research_only_ranking=[])
    assert _reason(validate_contract_ranking, RESEARCH_RESULT, mixed) == (
        "eligible_ranking_changes_selector_set"
    )
    with pytest.raises(ValueError):
        ContractRanking(
            eligible_ranking=[f.ELIGIBLE_A], research_only_ranking=[f.ELIGIBLE_A]
        )


def test_ranking_cannot_rank_or_reconstruct_rejected_contracts():
    ranking = ContractRanking(
        eligible_ranking=[f.ELIGIBLE_A, f.REJECTED_WIDE, f.ELIGIBLE_B], research_only_ranking=[]
    )
    assert _reason(validate_contract_ranking, RESULT, ranking) == (
        "contract_not_returned_by_selector"
    )


def test_ranking_cannot_suppress_returned_contracts():
    ranking = ContractRanking(eligible_ranking=[f.ELIGIBLE_A], research_only_ranking=[])
    assert _reason(validate_contract_ranking, RESULT, ranking) == (
        "eligible_ranking_changes_selector_set"
    )


def test_ranking_has_no_rejection_count_field():
    assert "rejection_counts" not in ContractRanking.model_fields


# --- Only the selector classifies; bundles never materialize rejected contracts --------------


def test_only_the_selector_may_emit_contract_classification():
    item = f.selector_item()
    validate_item(item, REGISTRY, payload_models=f.PAYLOAD_MODELS)
    forged = f.rebuild(
        item,
        provenance=f.rebuild_provenance(item, producer_id=f.CALC),
    )
    with pytest.raises(EvidenceValidationError) as excinfo:
        validate_item(forged, REGISTRY, payload_models=f.PAYLOAD_MODELS)
    # The registry already refuses the pairing; the permanent code check
    # (contract_classification_forbidden) backs it up.
    assert excinfo.value.reason == "kind_or_payload_not_allowed"


def test_contract_review_bundle_holds_facts_only_for_returned_contracts():
    selector = f.selector_item()
    returned = f.option_fact_item(f.ELIGIBLE_A)
    rejected = f.option_fact_item(f.REJECTED_WIDE)
    clock = f.clock_item(f.T0)
    manifest = f.bundle(
        f.recorded(selector, returned, rejected, clock),
        purpose=BundlePurpose.CONTRACT_REVIEW,
        as_of=f.T0 + timedelta(seconds=30),
    )
    ids = {e.item_id for e in manifest.entries}
    assert returned.item_id in ids
    assert rejected.item_id not in ids
    assert selector.item_id in ids


def test_without_a_selector_result_no_contract_facts_are_shown():
    fact = f.option_fact_item(f.ELIGIBLE_A)
    manifest = f.bundle(
        f.recorded(fact, f.clock_item(f.T0)),
        purpose=BundlePurpose.CONTRACT_REVIEW,
        as_of=f.T0 + timedelta(seconds=30),
    )
    assert fact.item_id not in {e.item_id for e in manifest.entries}


def test_only_audit_export_sees_the_full_evaluated_batch():
    selector = f.selector_item()
    rejected = f.option_fact_item(f.REJECTED_WIDE)
    manifest = f.bundle(
        f.recorded(selector, rejected, f.clock_item(f.T0)),
        purpose=BundlePurpose.CONTRACT_REVIEW,
        consumer=ConsumerId.AUDIT_EXPORT,
        permissions=sorted([*f.ALL_READS, ConsumerPermission.READ_SUPERSEDED]),
        as_of=f.T0 + timedelta(seconds=30),
    )
    assert rejected.item_id in {e.item_id for e in manifest.entries}


# --- Complete-view pagination -------------------------------------------------------


def _pages(view, split):
    view_hash = compute_contract_view_sha256(view)
    return [
        ContractViewPage(
            view_sha256=view_hash,
            page_index=index,
            page_count=len(split),
            eligible_contracts=eligible,
            research_only_contracts=research,
        )
        for index, (eligible, research) in enumerate(split)
    ]


def test_pages_reconstructing_the_complete_view_are_accepted():
    view = _view()
    pages = _pages(view, [([f.ELIGIBLE_A], []), ([f.ELIGIBLE_B], [])])
    validate_contract_view_pages(RESULT, view, pages)
    validate_contract_view_pages(RESULT, view, _pages(view, [([f.ELIGIBLE_A, f.ELIGIBLE_B], [])]))


@pytest.mark.parametrize(
    ("split", "reason"),
    [
        ([([f.ELIGIBLE_A], [])], "page_reconstruction_mismatch"),
        ([([f.ELIGIBLE_A], []), ([f.ELIGIBLE_A, f.ELIGIBLE_B], [])], "contract_listed_twice"),
        ([([f.ELIGIBLE_A], []), ([f.ELIGIBLE_B, f.REJECTED_WIDE], [])],
         "contract_not_returned_by_selector"),
        ([([f.ELIGIBLE_A], []), ([], [f.ELIGIBLE_B])], "page_reconstruction_mismatch"),
    ],
    ids=["omission", "duplicate", "addition", "status_change"],
)
def test_pages_cannot_omit_add_duplicate_or_change_status(split, reason):
    view = _view()
    assert _reason(validate_contract_view_pages, RESULT, view, _pages(view, split)) == reason


def test_pages_must_reference_the_complete_view():
    view = _view()
    other = _view(consumer=ConsumerId.ASSISTANT)
    pages = _pages(other, [([f.ELIGIBLE_A, f.ELIGIBLE_B], [])])
    assert _reason(validate_contract_view_pages, RESULT, view, pages) == "page_not_of_this_view"


def test_page_sequence_must_be_complete():
    view = _view()
    pages = _pages(view, [([f.ELIGIBLE_A], []), ([f.ELIGIBLE_B], [])])
    assert _reason(validate_contract_view_pages, RESULT, view, pages[:1]) == (
        "page_sequence_incomplete"
    )
    assert _reason(validate_contract_view_pages, RESULT, view, []) == "contract_pages_missing"


def test_pagination_requires_an_exact_complete_view():
    partial = _view(eligible_contracts=[f.ELIGIBLE_A])
    pages = _pages(partial, [([f.ELIGIBLE_A], [])])
    assert _reason(validate_contract_view_pages, RESULT, partial, pages) == (
        "selector_outcome_changed"
    )


def test_pages_carry_no_rejection_counts():
    assert "rejection_counts" not in ContractViewPage.model_fields
