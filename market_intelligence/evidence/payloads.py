"""Payload schemas the evidence core itself needs.

Only these are defined here:

- ``missing_evidence.v1`` and ``stale_evidence.v1``: the fixed status-item
  payloads (design §C.2); neither has any value field.
- ``clock_health_fact.v1``: the minimal clock reading that freshness gating
  requires (design §F.4). Its health *thresholds* stay in the registry's
  clock policy, which may be unset.
- ``contract_selector_result.v1``: the existing, unchanged
  ``spy-contract-selector-1`` ``ContractSelectorResult``.

Adapter payload schemas (``market_bar_fact.v1`` and the rest) are deliberately
absent: producer adapters are not authorized in this stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from market_intelligence.contract_selection.contracts import ContractSelectorResult
from market_intelligence.evidence.contracts import (
    MISSING_EVIDENCE_PAYLOAD,
    STALE_EVIDENCE_PAYLOAD,
    subject_type_for_id,
)
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    ItemId,
    ProducerId,
    Sha256Hex,
    Token,
    UtcTimestamp,
    VersionLabel,
)
from market_intelligence.evidence.selector_boundary import CONTRACT_SELECTOR_PAYLOAD_ID

CLOCK_HEALTH_PAYLOAD = "clock_health_fact.v1"


class MissingEvidencePayload(BaseModel):
    """An explicit, bounded absence record. It has no value field of any kind,
    so absence can never become a zero or neutral fact."""

    model_config = STRICT_FROZEN

    checked_at_utc: UtcTimestamp
    expected_producer_id: ProducerId
    expected_subject_id: Annotated[str, Field(min_length=1, max_length=160)]
    query_sha256: Sha256Hex
    expected_component: Token | None = None
    reason_code: Token

    @model_validator(mode="after")
    def _check_subject(self) -> MissingEvidencePayload:
        if subject_type_for_id(self.expected_subject_id) is None:
            raise ValueError("expected_subject_id must be canonical")
        return self


class StaleEvidencePayload(BaseModel):
    """A status item: required evidence was already stale at ``status_as_of_utc``.
    Names the stale item, or the expected producer and query when no item can
    be named. Never carries the stale value."""

    model_config = STRICT_FROZEN

    status_as_of_utc: UtcTimestamp
    stale_item_id: ItemId | None = None
    expected_producer_id: ProducerId | None = None
    query_sha256: Sha256Hex | None = None
    last_observed_at_utc: UtcTimestamp
    policy_id: VersionLabel

    @model_validator(mode="after")
    def _check_reference(self) -> StaleEvidencePayload:
        named_item = self.stale_item_id is not None
        named_query = self.expected_producer_id is not None and self.query_sha256 is not None
        if named_item == named_query:
            raise ValueError("name either the stale item or the expected producer and query")
        if self.last_observed_at_utc > self.status_as_of_utc:
            raise ValueError("last observation cannot be after the status as-of")
        return self


class ClockHealthFactPayload(BaseModel):
    """One clock reading, measured at the item's ``effective_at_utc``."""

    model_config = STRICT_FROZEN

    offset_ms: Annotated[int, Field(ge=-86_400_000, le=86_400_000)]
    last_sync_at_utc: UtcTimestamp


CORE_PAYLOAD_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {
        MISSING_EVIDENCE_PAYLOAD: MissingEvidencePayload,
        STALE_EVIDENCE_PAYLOAD: StaleEvidencePayload,
        CLOCK_HEALTH_PAYLOAD: ClockHealthFactPayload,
        CONTRACT_SELECTOR_PAYLOAD_ID: ContractSelectorResult,
    }
)
