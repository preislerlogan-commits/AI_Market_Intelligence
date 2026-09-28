"""In-memory evidence registry contract (design §J; EVIDENCE_REGISTRY.md).

This module defines the *shape* the validators consume. It deliberately
ships **no populated registry**, no file format, no file location and no
loader: those remain open decisions (design §Q.3). Tests build synthetic
registries in memory.

Fail-closed structure:

- the scenario-definition, strength-rubric, authorization-record and
  inference-input-authorization tables are constrained to be **empty**;
- consequently every producer's directional authority must be ``none`` and
  no ``current_inference`` emission rule can be machine-decision eligible;
- the contract-classification payload may be registered only for the
  deterministic Contract Selector (design §H.3, permanent).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from market_intelligence.evidence.canonical import REGISTRY_PREFIX, prefixed_id
from market_intelligence.evidence.contracts import (
    MISSING_EVIDENCE_PAYLOAD,
    STALE_EVIDENCE_PAYLOAD,
    STATUS_PAYLOADS,
    EvidenceItemContent,
    _require_sorted_unique,
    subject_type_for_id,
)
from market_intelligence.evidence.enums import (
    LIVE_PURPOSES,
    MACHINE_DECISION_CONSUMERS,
    STATUS_KINDS,
    BundlePurpose,
    ConsumerId,
    ConsumerPermission,
    DirectionalAuthority,
    EvidenceKind,
    ImplementationStatus,
    OffHoursMode,
    ProducerType,
    ProductionPath,
    SubjectType,
)
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    ConfigIdentity,
    ProducerId,
    Token,
    VersionLabel,
)
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_CLASSIFICATION_PAYLOADS,
    CONTRACT_SELECTOR_PRODUCER_ID,
)

LIVE_MAX_WINDOW_SECONDS = 7 * 24 * 3600
BRIEFING_MAX_WINDOW_SECONDS = 400 * 24 * 3600


class EmissionRule(BaseModel):
    """What a producer may emit: kind × payload schema, its freshness policy,
    and whether the registry marks the combination machine-decision eligible."""

    model_config = STRICT_FROZEN

    evidence_kind: EvidenceKind
    payload_schema_id: VersionLabel
    freshness_policy_id: VersionLabel
    machine_decision_eligible: bool


class ProducerEntry(BaseModel):
    model_config = STRICT_FROZEN

    producer_id: ProducerId
    producer_versions: Annotated[list[VersionLabel], Field(min_length=1, max_length=16)]
    producer_type: ProducerType
    implementation_status: ImplementationStatus
    production_paths: Annotated[list[ProductionPath], Field(min_length=1, max_length=2)]
    directional_authority: DirectionalAuthority
    allowed_subject_types: Annotated[list[SubjectType], Field(min_length=1, max_length=13)]
    emission_rules: Annotated[list[EmissionRule], Field(min_length=1, max_length=32)]
    reason_codes: Annotated[list[Token], Field(max_length=64)] = Field(default_factory=list)
    error_categories: Annotated[list[Token], Field(max_length=32)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_entry(self) -> ProducerEntry:
        for values, what in (
            (self.producer_versions, "producer_versions"),
            (self.production_paths, "production_paths"),
            (self.allowed_subject_types, "allowed_subject_types"),
            (self.emission_rules, "emission_rules"),
            (self.reason_codes, "reason_codes"),
            (self.error_categories, "error_categories"),
        ):
            _require_sorted_unique(values, what)
        pairs = [(r.evidence_kind, r.payload_schema_id) for r in self.emission_rules]
        if len(set(pairs)) != len(pairs):
            raise ValueError("one emission rule per kind and payload schema")
        if self.directional_authority is not DirectionalAuthority.NONE:
            # Directional authority needs an authorization record; the
            # authorization tables are structurally empty.
            raise ValueError("no producer may hold directional authority")
        for rule in self.emission_rules:
            inference = rule.evidence_kind is EvidenceKind.CURRENT_INFERENCE
            if inference and rule.machine_decision_eligible:
                raise ValueError("inference is default-deny: no input authorization exists")
            status_kind = rule.evidence_kind in (
                EvidenceKind.MISSING_EVIDENCE,
                EvidenceKind.STALE_EVIDENCE,
            )
            if status_kind != (rule.payload_schema_id in STATUS_PAYLOADS):
                raise ValueError("status kinds and status payload schemas must pair")
            if (
                rule.payload_schema_id in CONTRACT_CLASSIFICATION_PAYLOADS
                and self.producer_id != CONTRACT_SELECTOR_PRODUCER_ID
            ):
                raise ValueError("only the deterministic Contract Selector may classify contracts")
        if self.producer_id == CONTRACT_SELECTOR_PRODUCER_ID and self.production_paths != [
            ProductionPath.DETERMINISTIC
        ]:
            raise ValueError("the Contract Selector is deterministic only")
        return self

    def rule_for(self, kind: EvidenceKind, payload_schema_id: str) -> EmissionRule | None:
        for rule in self.emission_rules:
            if rule.evidence_kind is kind and rule.payload_schema_id == payload_schema_id:
                return rule
        return None


class PayloadSchemaEntry(BaseModel):
    """``spy_price_content`` declares whether the schema can carry SPY price,
    volume or trade values (or values derived from them) for the holdout
    guard. It has no default: every schema must declare it."""

    model_config = STRICT_FROZEN

    payload_schema_id: VersionLabel
    spy_price_content: bool


class FreshnessPolicy(BaseModel):
    """A producer-specific freshness policy (design §F.3). Any unset parameter
    makes the policy evaluate every item to ``unknown``."""

    model_config = STRICT_FROZEN

    policy_id: VersionLabel
    applicable_kinds: Annotated[list[EvidenceKind], Field(min_length=1, max_length=6)]
    observed_timestamp_basis: Token
    timeless: bool
    current_until_seconds: Annotated[int, Field(ge=0)] | None = None
    stale_after_seconds: Annotated[int, Field(ge=0)] | None = None
    future_tolerance_seconds: Annotated[int, Field(ge=0)] | None = None
    off_hours_mode: OffHoursMode
    configuration_version: VersionLabel

    @model_validator(mode="after")
    def _check_policy(self) -> FreshnessPolicy:
        _require_sorted_unique(self.applicable_kinds, "applicable_kinds")
        params = (
            self.current_until_seconds,
            self.stale_after_seconds,
            self.future_tolerance_seconds,
        )
        if self.timeless:
            if any(p is not None for p in params):
                raise ValueError("a timeless policy has no age parameters")
            if self.off_hours_mode is not OffHoursMode.NOT_APPLICABLE:
                raise ValueError("a timeless policy has no off-hours rule")
        elif self.off_hours_mode is OffHoursMode.NOT_APPLICABLE:
            raise ValueError("an age-based policy needs an off-hours rule")
        if (
            self.current_until_seconds is not None
            and self.stale_after_seconds is not None
            and self.stale_after_seconds < self.current_until_seconds
        ):
            raise ValueError("stale boundary must not precede the current window")
        return self

    @property
    def parameters_set(self) -> bool:
        return all(
            p is not None
            for p in (
                self.current_until_seconds,
                self.stale_after_seconds,
                self.future_tolerance_seconds,
            )
        )


class ClockPolicy(BaseModel):
    """Clock-health thresholds. The product thresholds are an open decision
    (design §Q.3), so every parameter may be unset; unset means ``unknown``."""

    model_config = STRICT_FROZEN

    policy_id: VersionLabel
    clock_producer_id: ProducerId
    max_abs_offset_ms: Annotated[int, Field(ge=0)] | None = None
    max_sync_age_seconds: Annotated[int, Field(ge=0)] | None = None
    max_measurement_age_seconds: Annotated[int, Field(ge=0)] | None = None

    @property
    def parameters_set(self) -> bool:
        return all(
            p is not None
            for p in (
                self.max_abs_offset_ms,
                self.max_sync_age_seconds,
                self.max_measurement_age_seconds,
            )
        )


class ConsumerGrant(BaseModel):
    model_config = STRICT_FROZEN

    consumer_id: ConsumerId
    purposes: Annotated[list[BundlePurpose], Field(min_length=1, max_length=6)]
    permissions: Annotated[list[ConsumerPermission], Field(max_length=9)]
    machine_decision_mode_allowed: bool

    @model_validator(mode="after")
    def _check_grant(self) -> ConsumerGrant:
        _require_sorted_unique(self.purposes, "purposes")
        _require_sorted_unique(self.permissions, "permissions")
        machine_consumer = self.consumer_id in MACHINE_DECISION_CONSUMERS
        if self.machine_decision_mode_allowed and not machine_consumer:
            raise ValueError("machine-decision mode is only for setup_ranker/notification_layer")
        return self


class BundleRequirement(BaseModel):
    """One required piece of evidence, identified by its full registered
    identity: requirement ID, producer, applicable producer versions, evidence
    kind, payload schema, primary subject and applicable configurations. An
    item satisfies the requirement only if it matches every part; producer
    alone is never enough. Status kinds can never satisfy a requirement."""

    model_config = STRICT_FROZEN

    requirement_id: VersionLabel
    producer_id: ProducerId
    producer_versions: Annotated[list[VersionLabel], Field(min_length=1, max_length=16)]
    evidence_kind: EvidenceKind
    payload_schema_id: VersionLabel
    primary_subject_id: Annotated[str, Field(min_length=1, max_length=160)]
    configuration_identities: Annotated[list[ConfigIdentity], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def _check_requirement(self) -> BundleRequirement:
        _require_sorted_unique(self.producer_versions, "producer_versions")
        _require_sorted_unique(self.configuration_identities, "configuration_identities")
        if self.evidence_kind in STATUS_KINDS:
            raise ValueError("a status kind can never satisfy a requirement")
        if subject_type_for_id(self.primary_subject_id) is None:
            raise ValueError("primary_subject_id must be canonical")
        return self

    def matches(self, item: EvidenceItemContent) -> bool:
        prov = item.provenance
        return (
            prov.producer_id == self.producer_id
            and prov.producer_version in self.producer_versions
            and item.evidence_kind is self.evidence_kind
            and item.payload_schema_id == self.payload_schema_id
            and item.subjects[0].subject_id == self.primary_subject_id
            and prov.configuration_identity in self.configuration_identities
        )

    def concerns_status_item(self, item: EvidenceItemContent) -> bool:
        """A missing/stale status item from the same producer about the same
        primary subject explains why this requirement is unmet."""
        return (
            item.evidence_kind in STATUS_KINDS
            and item.provenance.producer_id == self.producer_id
            and item.subjects[0].subject_id == self.primary_subject_id
        )


class SelectionRule(BaseModel):
    model_config = STRICT_FROZEN

    selection_rule_id: VersionLabel
    purpose: BundlePurpose
    requirements: Annotated[list[BundleRequirement], Field(max_length=32)]
    max_window_seconds: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def _check_rule(self) -> SelectionRule:
        _require_sorted_unique(
            [r.requirement_id for r in self.requirements], "requirement ids"
        )
        limit = (
            LIVE_MAX_WINDOW_SECONDS
            if self.purpose in LIVE_PURPOSES
            else BRIEFING_MAX_WINDOW_SECONDS
        )
        if self.max_window_seconds > limit:
            raise ValueError("query window exceeds the purpose's maximum")
        return self


_EmptyTable = Annotated[list[Token], Field(max_length=0)]


class EvidenceRegistry(BaseModel):
    """One registry version's content. Its identity is ``evr1_`` + SHA-256 of
    this canonical content (design §L.3)."""

    model_config = STRICT_FROZEN

    registry_label: VersionLabel
    producers: Annotated[list[ProducerEntry], Field(min_length=1, max_length=128)]
    payload_schemas: Annotated[list[PayloadSchemaEntry], Field(min_length=1, max_length=256)]
    freshness_policies: Annotated[list[FreshnessPolicy], Field(min_length=1, max_length=128)]
    clock_policy: ClockPolicy
    consumer_grants: Annotated[list[ConsumerGrant], Field(max_length=16)]
    selection_rules: Annotated[list[SelectionRule], Field(max_length=16)]
    uncertainty_codes: Annotated[list[Token], Field(max_length=128)] = Field(default_factory=list)
    limitation_codes: Annotated[list[Token], Field(max_length=128)] = Field(default_factory=list)
    component_tokens: Annotated[list[Token], Field(max_length=128)] = Field(default_factory=list)
    # Structurally empty: nothing is authorized (design §H, §G, EVIDENCE_REGISTRY §5).
    scenario_definitions: _EmptyTable = Field(default_factory=list)
    strength_rubrics: _EmptyTable = Field(default_factory=list)
    authorization_records: _EmptyTable = Field(default_factory=list)
    inference_input_authorizations: _EmptyTable = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_registry(self) -> EvidenceRegistry:
        for values, what in (
            ([p.producer_id for p in self.producers], "producers"),
            ([s.payload_schema_id for s in self.payload_schemas], "payload_schemas"),
            ([f.policy_id for f in self.freshness_policies], "freshness_policies"),
            ([g.consumer_id for g in self.consumer_grants], "consumer_grants"),
            ([r.purpose for r in self.selection_rules], "selection_rules"),
            (self.uncertainty_codes, "uncertainty_codes"),
            (self.limitation_codes, "limitation_codes"),
            (self.component_tokens, "component_tokens"),
        ):
            _require_sorted_unique(values, what)
        schemas = {s.payload_schema_id for s in self.payload_schemas}
        policies = {f.policy_id: f for f in self.freshness_policies}
        for producer in self.producers:
            for rule in producer.emission_rules:
                if rule.payload_schema_id not in schemas:
                    raise ValueError("emission rule references an unregistered payload schema")
                policy = policies.get(rule.freshness_policy_id)
                if policy is None or rule.evidence_kind not in policy.applicable_kinds:
                    raise ValueError("emission rule references an inapplicable freshness policy")
        for rule in self.selection_rules:
            for requirement in rule.requirements:
                producer = self.producer(requirement.producer_id)
                if producer is None:
                    raise ValueError("selection rule requires an unregistered producer")
                if not set(requirement.producer_versions) <= set(producer.producer_versions):
                    raise ValueError("requirement names an unregistered producer version")
                kind, schema_id = requirement.evidence_kind, requirement.payload_schema_id
                if producer.rule_for(kind, schema_id) is None:
                    raise ValueError("requirement names an unregistered kind and payload schema")
        for schema in (MISSING_EVIDENCE_PAYLOAD, STALE_EVIDENCE_PAYLOAD):
            entry = self.payload_schema(schema)
            if entry is not None and entry.spy_price_content:
                raise ValueError("status payloads carry no price content")
        return self

    def producer(self, producer_id: str) -> ProducerEntry | None:
        return next((p for p in self.producers if p.producer_id == producer_id), None)

    def payload_schema(self, payload_schema_id: str) -> PayloadSchemaEntry | None:
        return next(
            (s for s in self.payload_schemas if s.payload_schema_id == payload_schema_id), None
        )

    def freshness_policy(self, policy_id: str) -> FreshnessPolicy | None:
        return next((f for f in self.freshness_policies if f.policy_id == policy_id), None)

    def consumer_grant(self, consumer_id: ConsumerId) -> ConsumerGrant | None:
        return next((g for g in self.consumer_grants if g.consumer_id is consumer_id), None)

    def selection_rule(self, purpose: BundlePurpose) -> SelectionRule | None:
        return next((r for r in self.selection_rules if r.purpose is purpose), None)


def compute_registry_version_id(registry: EvidenceRegistry) -> str:
    """``evr1_`` identity over the registry content only (design §L.3)."""
    return prefixed_id(REGISTRY_PREFIX, registry.model_dump(mode="json"))
