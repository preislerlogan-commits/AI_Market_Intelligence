"""Frozen conflict-severity derivation (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §6.3.1).

Accepted stricter-than-code storage requirement. ``involves_required_item`` is
true only when at least one involved item satisfies at least one requirement
of an **authorized machine-decision bundle purpose** in the Evidence Registry
version in force at the conflict's ``evaluated_as_of_utc``. A purpose
qualifies only if the registry grants some consumer
``machine_decision_mode_allowed = true`` for it **and** has a selection rule
for it. Requirements that belong only to dashboard display, assistant
context, historical review, research-only review or an unauthorized purpose
never count. Severity is then the fixed table ``conflict-severity-rules-1``
(``expected_severity``). No bundle's required set is ever an input.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.contracts import EvidenceConflict, EvidenceItem
from market_intelligence.evidence.enums import BundlePurpose
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence.validation import expected_severity
from market_intelligence.evidence_store.contracts import ConflictSeverityDerivation
from market_intelligence.evidence_store.enums import SeverityRuleVersion

CURRENT_SEVERITY_RULE_VERSION = SeverityRuleVersion.CONFLICT_SEVERITY_RULES_1
KNOWN_SEVERITY_RULE_VERSIONS = frozenset(SeverityRuleVersion)


def authorized_machine_decision_purposes(registry: EvidenceRegistry) -> list[BundlePurpose]:
    granted = {
        purpose
        for grant in registry.consumer_grants
        if grant.machine_decision_mode_allowed
        for purpose in grant.purposes
    }
    return sorted(p for p in granted if registry.selection_rule(p) is not None)


def matched_requirement_ids(
    registry: EvidenceRegistry, involved: Iterable[EvidenceItem]
) -> list[str]:
    items = list(involved)
    matched: set[str] = set()
    for purpose in authorized_machine_decision_purposes(registry):
        rule = registry.selection_rule(purpose)
        if rule is None:  # excluded by authorized_machine_decision_purposes
            continue
        for requirement in rule.requirements:
            if any(requirement.matches(item) for item in items):
                matched.add(f"{purpose.value}:{requirement.requirement_id}")
    return sorted(matched)


def derive_conflict_severity(
    conflict: EvidenceConflict,
    involved: Iterable[EvidenceItem],
    registry: EvidenceRegistry,
    *,
    rule_version: SeverityRuleVersion = CURRENT_SEVERITY_RULE_VERSION,
) -> ConflictSeverityDerivation:
    matched = matched_requirement_ids(registry, involved)
    severity = expected_severity(conflict.conflict_type, bool(matched))
    return ConflictSeverityDerivation(
        evaluated_as_of_utc=conflict.evaluated_as_of_utc,
        severity_registry_version_id=compute_registry_version_id(registry),
        matched_requirement_ids=matched,
        involves_required_item=bool(matched),
        severity=severity.value,
        severity_rule_version=rule_version.value,
    )


def required_item_ids(
    registry: EvidenceRegistry, involved: Iterable[EvidenceItem]
) -> frozenset[str]:
    """The involved items that match an authorized machine-decision requirement:
    the ``required_item_ids`` passed to ``validate_conflict``."""
    items = list(involved)
    found: set[str] = set()
    for purpose in authorized_machine_decision_purposes(registry):
        rule = registry.selection_rule(purpose)
        if rule is None:
            continue
        for requirement in rule.requirements:
            found |= {item.item_id for item in items if requirement.matches(item)}
    return frozenset(found)


def machine_decision_signature(registry: EvidenceRegistry) -> bytes:
    """What an activation must not change silently (registry design §8.4):
    the authorized machine-decision purposes, their requirements, and the
    consumers allowed machine-decision mode."""
    purposes = authorized_machine_decision_purposes(registry)
    signature: dict[str, Any] = {
        "purposes": [p.value for p in purposes],
        "requirements": {
            p.value: [
                r.model_dump(mode="json")
                for r in registry.selection_rule(p).requirements  # type: ignore[union-attr]
            ]
            for p in purposes
        },
        "machine_decision_consumers": sorted(
            g.consumer_id.value for g in registry.consumer_grants if g.machine_decision_mode_allowed
        ),
    }
    return canonical_json_bytes(signature)


def derivation_wrapper_matches(
    stored: ConflictSeverityDerivation, recomputed: ConflictSeverityDerivation
) -> bool:
    return stored.model_dump(mode="json") == recomputed.model_dump(mode="json")
