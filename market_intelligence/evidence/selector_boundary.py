"""Permanent Contract Selector boundary validators (design §H.3).

Contract eligibility belongs exclusively to the existing deterministic
Contract Selector (``spy-contract-selector-1``). For each evaluated contract
the selector produces exactly one outcome:

- **returned as eligible** (in ``eligible_contracts``);
- **returned as research_only** (in ``research_only_contracts``);
- **rejected**: counted under a bounded ``RejectionReason`` in the aggregate
  ``rejection_counts`` only. No per-contract record exists for a rejected
  contract, and nothing here creates one.

These validators check *proposed downstream views and rankings* against a
selector result. The authoritative ``ContractView`` must cover the returned
sets exactly. Pagination is allowed: each ``ContractViewPage`` references the
complete view by hash, and the pages together must reconstruct it exactly.
They implement no consumer UI and no ranker. No authorization record type can
relax them.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from market_intelligence.contract_selection.contracts import (
    ContractSelectorResult,
    RejectionReason,
)
from market_intelligence.evidence.canonical import canonical_json_bytes, canonical_sha256
from market_intelligence.evidence.enums import ConsumerId
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.primitives import STRICT_FROZEN, Sha256Hex

CONTRACT_SELECTOR_PRODUCER_ID = "deterministic_contract_selector"
CONTRACT_SELECTOR_PAYLOAD_ID = "contract_selector_result.v1"
CONTRACT_CLASSIFICATION_PAYLOADS = frozenset({CONTRACT_SELECTOR_PAYLOAD_ID})

_ContractSymbol = Annotated[str, Field(pattern=r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")]


class ReturnedContractSets(BaseModel):
    """The only contract-level facts downstream consumers may see: the two
    returned sets and the aggregate rejection counts."""

    model_config = STRICT_FROZEN

    eligible: frozenset[str]
    research_only: frozenset[str]
    rejection_counts: dict[RejectionReason, int]

    @classmethod
    def from_result(cls, result: ContractSelectorResult) -> ReturnedContractSets:
        return cls(
            eligible=frozenset(c.contract_symbol for c in result.eligible_contracts),
            research_only=frozenset(c.contract_symbol for c in result.research_only_contracts),
            rejection_counts=dict(result.rejection_counts),
        )

    @property
    def returned(self) -> frozenset[str]:
        return self.eligible | self.research_only


class ContractView(BaseModel):
    """A proposed downstream display of one selector result (dashboard,
    assistant, contract-review UI, or notification)."""

    model_config = STRICT_FROZEN

    consumer_id: ConsumerId
    eligible_contracts: Annotated[list[_ContractSymbol], Field(max_length=5000)]
    research_only_contracts: Annotated[list[_ContractSymbol], Field(max_length=5000)]
    rejection_counts: dict[RejectionReason, int]


class ContractRanking(BaseModel):
    """The shape a future, separately authorized contract ranker's output
    would have to take: one ranking per returned set, never mixed."""

    model_config = STRICT_FROZEN

    eligible_ranking: Annotated[list[_ContractSymbol], Field(max_length=5000)]
    research_only_ranking: Annotated[list[_ContractSymbol], Field(max_length=5000)]

    @model_validator(mode="after")
    def _check_no_repeats(self) -> ContractRanking:
        combined = self.eligible_ranking + self.research_only_ranking
        if len(set(combined)) != len(combined):
            raise ValueError("a contract may appear once, in exactly one ranking")
        return self


def _counts_bytes(counts: dict[RejectionReason, int]) -> bytes:
    return canonical_json_bytes({str(k): v for k, v in counts.items()})


def validate_contract_view(result: ContractSelectorResult, view: ContractView) -> None:
    """Refuse any view that changes, suppresses, mixes or extends the
    selector's outcomes, or that materializes a rejected contract."""
    sets = ReturnedContractSets.from_result(result)
    eligible = view.eligible_contracts
    research = view.research_only_contracts
    if len(set(eligible)) != len(eligible) or len(set(research)) != len(research):
        raise EvidenceValidationError("contract_listed_twice")
    shown = set(eligible) | set(research)
    if not shown <= sets.returned:
        # Covers rejected contracts materialized downstream and contracts
        # the selector never evaluated.
        raise EvidenceValidationError("contract_not_returned_by_selector")
    if set(eligible) != sets.eligible or set(research) != sets.research_only:
        raise EvidenceValidationError("selector_outcome_changed")
    if _counts_bytes(view.rejection_counts) != _counts_bytes(sets.rejection_counts):
        raise EvidenceValidationError("rejection_counts_changed")


def validate_contract_ranking(result: ContractSelectorResult, ranking: ContractRanking) -> None:
    """Refuse any ranking that is not a separate, complete ordering of each
    returned set. Rankings never touch rejected contracts or their counts."""
    sets = ReturnedContractSets.from_result(result)
    ranked = set(ranking.eligible_ranking) | set(ranking.research_only_ranking)
    if not ranked <= sets.returned:
        raise EvidenceValidationError("contract_not_returned_by_selector")
    if set(ranking.eligible_ranking) != sets.eligible:
        raise EvidenceValidationError("eligible_ranking_changes_selector_set")
    if set(ranking.research_only_ranking) != sets.research_only:
        raise EvidenceValidationError("research_only_ranking_changes_selector_set")


# --- Pagination of the authoritative view ----------------------------------------------


def compute_contract_view_sha256(view: ContractView) -> str:
    """Identity of one complete, authoritative contract view."""
    return canonical_sha256(view.model_dump(mode="json"))


class ContractViewPage(BaseModel):
    """One page of a complete contract view. Pages never carry rejection
    counts; those live only on the complete view."""

    model_config = STRICT_FROZEN

    view_sha256: Sha256Hex
    page_index: Annotated[int, Field(ge=0, le=4999)]
    page_count: Annotated[int, Field(ge=1, le=5000)]
    eligible_contracts: Annotated[list[_ContractSymbol], Field(max_length=5000)]
    research_only_contracts: Annotated[list[_ContractSymbol], Field(max_length=5000)]

    @model_validator(mode="after")
    def _check_page(self) -> ContractViewPage:
        if self.page_index >= self.page_count:
            raise ValueError("page_index must be below page_count")
        return self


def validate_contract_view_pages(
    result: ContractSelectorResult, view: ContractView, pages: list[ContractViewPage]
) -> None:
    """The complete view must itself satisfy ``validate_contract_view``; its
    pages must all reference it and reconstruct it exactly, with no omission,
    addition, duplicate or status change."""
    validate_contract_view(result, view)
    if not pages:
        raise EvidenceValidationError("contract_pages_missing")
    view_hash = compute_contract_view_sha256(view)
    if any(page.view_sha256 != view_hash for page in pages):
        raise EvidenceValidationError("page_not_of_this_view")
    if sorted(page.page_index for page in pages) != list(range(len(pages))) or any(
        page.page_count != len(pages) for page in pages
    ):
        raise EvidenceValidationError("page_sequence_incomplete")
    ordered = sorted(pages, key=lambda page: page.page_index)
    eligible = [symbol for page in ordered for symbol in page.eligible_contracts]
    research = [symbol for page in ordered for symbol in page.research_only_contracts]
    combined = eligible + research
    if len(set(combined)) != len(combined):
        raise EvidenceValidationError("contract_listed_twice")
    if not set(combined) <= set(view.eligible_contracts) | set(view.research_only_contracts):
        raise EvidenceValidationError("contract_not_returned_by_selector")
    if set(eligible) != set(view.eligible_contracts) or set(research) != set(
        view.research_only_contracts
    ):
        raise EvidenceValidationError("page_reconstruction_mismatch")
