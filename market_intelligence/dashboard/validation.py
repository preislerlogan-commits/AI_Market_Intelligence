"""Fail-closed validation of every bundle and card before anything is rendered.

The dashboard consumes the reviewed Evidence Bundle and ``setup-card-1``
contracts only through this gate. It re-validates each object from its
serialized form (so an object mutated after sealing is refused), re-runs the
Evidence Envelope item and conflict validators, re-derives every card value
from the card's own bundle with ``validate_setup_card``, and checks card
history. Any failure raises ``DashboardInputError`` carrying one bounded
reason token; the page then shows that token and nothing else.

Nothing here opens storage, a provider, a model or the network.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ValidationError

from market_intelligence.evidence.contracts import (
    EvidenceBundleManifest,
    EvidenceConflict,
    EvidenceItem,
)
from market_intelligence.evidence.enums import BundlePurpose, ConsumerId
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.holdout import is_holdout_restricted
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence.validation import validate_conflict_record, validate_item
from market_intelligence.setup_cards.contracts import SetupCard
from market_intelligence.setup_cards.definitions import SetupDefinition
from market_intelligence.setup_cards.supersession import validate_card_history
from market_intelligence.setup_cards.validation import CardContext, validate_setup_card

SYNTHETIC_DEFINITION_PREFIX = "synthetic-only-"


class DashboardInputError(Exception):
    """A bundle or card failed validation. Carries a bounded token only:
    never a stack trace, path, payload or raw text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _revalidated(model: BaseModel) -> BaseModel:
    """Re-run every validator from the serialized form."""
    return type(model).model_validate_json(model.model_dump_json())


def _check_sealed(model: BaseModel, reason: str) -> None:
    try:
        _revalidated(model)
    except ValidationError:
        raise DashboardInputError(reason) from None


def validate_bundle(
    bundle: EvidenceBundleManifest,
    items: Mapping[str, EvidenceItem],
    conflicts: Mapping[str, EvidenceConflict],
    *,
    registry: EvidenceRegistry,
    payload_models: Mapping[str, type[BaseModel]],
    purpose: BundlePurpose,
) -> None:
    """One bundle and exactly the items and conflicts it names."""
    _check_sealed(bundle, "bundle_invalid")
    if bundle.purpose is not purpose:
        raise DashboardInputError("bundle_purpose_mismatch")
    context = bundle.consumer_context
    if context.consumer_id is not ConsumerId.DASHBOARD or context.machine_decision_mode:
        raise DashboardInputError("bundle_consumer_mismatch")
    if bundle.registry_version_id != compute_registry_version_id(registry):
        raise DashboardInputError("bundle_registry_mismatch")
    entries = {entry.item_id: entry for entry in bundle.entries}
    if set(items) != set(entries):
        # Nothing outside the bundle may reach the presentation layer.
        raise DashboardInputError("bundle_items_mismatch")
    if set(conflicts) != set(bundle.conflict_ids):
        raise DashboardInputError("bundle_conflicts_mismatch")
    for item_id, item in items.items():
        _check_sealed(item, "item_invalid")
        if item.item_id != item_id:
            raise DashboardInputError("bundle_items_mismatch")
        entry = entries[item_id]
        if (
            entry.evidence_kind is not item.evidence_kind
            or entry.producer_id != item.provenance.producer_id
        ):
            raise DashboardInputError("bundle_item_mismatch")
        if is_holdout_restricted(item, registry):
            raise DashboardInputError("holdout_restricted_content")
        try:
            validate_item(item, registry, payload_models=payload_models)
        except EvidenceValidationError as error:
            raise DashboardInputError(error.reason) from None
    for conflict_id, conflict in conflicts.items():
        _check_sealed(conflict, "conflict_invalid")
        if conflict.conflict_id != conflict_id:
            raise DashboardInputError("bundle_conflicts_mismatch")
        try:
            # Structure only. The stored severity was frozen by the Evidence
            # Store from the registry in force when the conflict was evaluated;
            # this bundle's own ``required`` flags never re-decide it, and the
            # dashboard never edits, downgrades, upgrades or replaces it.
            validate_conflict_record(conflict, items, registry)
        except EvidenceValidationError as error:
            raise DashboardInputError(error.reason) from None


def check_definitions(definitions: Sequence[SetupDefinition]) -> None:
    """The prototype never uses a production setup definition: only the
    in-memory ``synthetic-only-`` definitions may appear."""
    for definition in definitions:
        if not definition.setup_definition_id.startswith(SYNTHETIC_DEFINITION_PREFIX):
            raise DashboardInputError("non_synthetic_setup_definition")


def validate_card(card: SetupCard, ctx: CardContext) -> None:
    """One sealed card against the exact bundle it names."""
    _check_sealed(card, "card_invalid")
    if card.decision_context_bundle_id != ctx.bundle.bundle_id:
        raise DashboardInputError("card_bundle_mismatch")
    try:
        validate_setup_card(card, ctx)
    except EvidenceValidationError as error:
        raise DashboardInputError(error.reason) from None


def validate_history(cards: Sequence[SetupCard]) -> None:
    try:
        validate_card_history(cards)
    except EvidenceValidationError as error:
        raise DashboardInputError(error.reason) from None
