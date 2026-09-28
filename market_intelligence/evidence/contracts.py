"""Strict, immutable Pydantic contracts for ``evidence-envelope-1`` (design §B).

Every ID-bearing record is split in two:

- a ``*Content`` model holding every substantive field, validated for its
  intrinsic rules; and
- a sealed model that adds the content-addressed ID and refuses any ID that
  does not equal the recomputed one.

Use the ``seal_*`` helpers to build sealed records. Registry-dependent rules
(allowed producers, kinds, payload schemas, eligibility derivation,
authorization tables) live in ``validation.py``; lineage rules need the
parent records and also live there.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from market_intelligence.evidence.canonical import (
    BUNDLE_PREFIX,
    CONFLICT_PREFIX,
    ENVELOPE_PREFIX,
    ITEM_PREFIX,
    canonical_json_bytes,
    canonical_sha256,
    prefixed_id,
    remove_paths,
)
from market_intelligence.evidence.enums import (
    CLOCK_FACT_EVALUATED,
    CLOCK_UNHEALTHY_REASONS,
    CLOCK_UNKNOWN_REASONS,
    AmbiguityReason,
    AvailabilityState,
    BundlePurpose,
    CalculationCompleteness,
    CitationRole,
    ClaimBasis,
    ClockHealthReason,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    ConsumerId,
    ConsumerPermission,
    DataQuality,
    DetectionMethod,
    DirectionalContent,
    DuckDbTable,
    EndpointClass,
    EvidenceKind,
    Feed,
    FreshnessReason,
    FreshnessState,
    GradedStrength,
    HoldoutState,
    InferenceMethod,
    InferenceSupport,
    IngestionOutcome,
    LocatorKind,
    MissingProducerReason,
    ProducerType,
    ProductionPath,
    RationaleCategory,
    ResearchStatus,
    ResolutionKind,
    ResponseClass,
    ResultKind,
    RevisionReason,
    ScenarioClass,
    ScenarioRelationKind,
    SourceBasis,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence.primitives import (
    SCHEMA_VERSION,
    STRICT_FROZEN,
    BundleId,
    CommitSha,
    ConfigIdentity,
    ConflictId,
    DisplayText,
    EnvelopeId,
    ExactRational,
    ItemId,
    ProducerId,
    RegistryVersionId,
    SafeText,
    SessionDate,
    Sha256Hex,
    Token,
    UtcTimestamp,
    VersionLabel,
    format_utc,
    sensitive_text_category,
)

EASTERN = ZoneInfo("America/New_York")

MISSING_EVIDENCE_PAYLOAD = "missing_evidence.v1"
STALE_EVIDENCE_PAYLOAD = "stale_evidence.v1"
STATUS_PAYLOADS = frozenset({MISSING_EVIDENCE_PAYLOAD, STALE_EVIDENCE_PAYLOAD})

MAX_ITEMS_PER_ENVELOPE = 512
MAX_ENVELOPE_BYTES = 4 * 1024 * 1024
MAX_PAYLOAD_BYTES = 32 * 1024
MAX_RESEARCH_PAYLOAD_BYTES = 64 * 1024
MAX_PAYLOAD_DEPTH = 16
MAX_BUNDLE_ENTRIES = 2000

_SchemaVersion = Literal["evidence-envelope-1"]


def _key(value: Any) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return canonical_json_bytes(value)


def _require_sorted_unique(values: Sequence[Any], what: str) -> None:
    keys = [_key(v) for v in values]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{what} must not contain duplicates")
    if keys != sorted(keys):
        raise ValueError(f"{what} must be sorted canonically")


# --- §D Subjects ------------------------------------------------------------------

SUBJECT_PATTERNS: dict[SubjectType, re.Pattern[str]] = {
    SubjectType.INSTRUMENT: re.compile(r"^instrument:us_equity:[A-Z][A-Z0-9.]{0,9}$"),
    SubjectType.INDEX: re.compile(r"^index:[A-Z0-9]{1,16}$"),
    SubjectType.MARKET: re.compile(r"^market:[a-z_]{1,32}$"),
    SubjectType.MACRO_SERIES: re.compile(r"^macro_series:fred:[A-Z0-9_]{1,32}$"),
    SubjectType.NEWS_ITEM: re.compile(r"^news_item:[a-z_]{1,32}:[A-Za-z0-9._-]{1,64}$"),
    SubjectType.SCHEDULED_CATALYST: re.compile(
        r"^catalyst:[a-z_]{1,32}:[a-z0-9_]{1,48}:\d{4}-\d{2}-\d{2}$"
    ),
    SubjectType.MARKET_SESSION: re.compile(r"^session:us_equity_regular:\d{4}-\d{2}-\d{2}$"),
    SubjectType.SETUP_CANDIDATE: re.compile(
        r"^setup:(vwap_reversion|trend_continuation):[a-z0-9_]{1,48}:[A-Z]{1,10}:\d{8}T\d{6}Z$"
    ),
    SubjectType.SCENARIO: re.compile(r"^scenario:[a-z0-9_]{1,48}:[A-Z]{1,10}:\d{4}-\d{2}-\d{2}$"),
    SubjectType.OPTION_CONTRACT: re.compile(r"^option:osi:[A-Z]{1,6}\d{6}[CP]\d{8}$"),
    SubjectType.RESEARCH_STUDY: re.compile(r"^research:[a-z0-9_]{1,48}$"),
    SubjectType.PROVIDER_HEALTH: re.compile(r"^provider:[a-z_]{1,32}:[a-z_]{1,32}$"),
    SubjectType.SYSTEM_COMPONENT: re.compile(r"^system:[a-z_]{1,32}$"),
}

_DATED_SUBJECTS = {SubjectType.SCHEDULED_CATALYST, SubjectType.MARKET_SESSION, SubjectType.SCENARIO}


def subject_type_for_id(subject_id: str) -> SubjectType | None:
    for subject_type, pattern in SUBJECT_PATTERNS.items():
        if pattern.fullmatch(subject_id):
            return subject_type
    return None


class EvidenceSubject(BaseModel):
    """§B.4. A canonical subject identifier; never a display name."""

    model_config = STRICT_FROZEN

    subject_type: SubjectType
    subject_id: Annotated[str, Field(min_length=1, max_length=160)]

    @model_validator(mode="after")
    def _check_canonical(self) -> EvidenceSubject:
        if not SUBJECT_PATTERNS[self.subject_type].fullmatch(self.subject_id):
            raise ValueError("subject_id does not match its subject_type's canonical form")
        if self.subject_type in _DATED_SUBJECTS:
            date.fromisoformat(self.subject_id[-10:])
        return self


def session_subject(session_date: date) -> EvidenceSubject:
    return EvidenceSubject(
        subject_type=SubjectType.MARKET_SESSION,
        subject_id=f"session:us_equity_regular:{session_date.isoformat()}",
    )


# --- §B.3a Temporal scope -----------------------------------------------------------


class EvidenceTemporalScope(BaseModel):
    """§B.3a. Substantive time coordinates; every field is in the identity."""

    model_config = STRICT_FROZEN

    session_date: SessionDate | None = None
    observation_period_start: date | None = None
    observation_period_end: date | None = None
    source_vintage_start: date | None = None
    source_vintage_end: date | None = None
    scheduled_event_at_utc: UtcTimestamp | None = None
    valid_from_utc: UtcTimestamp | None = None
    valid_until_utc: UtcTimestamp | None = None

    @model_validator(mode="after")
    def _check_intervals(self) -> EvidenceTemporalScope:
        if self.observation_period_end is not None:
            if self.observation_period_start is None:
                raise ValueError("observation_period_end requires observation_period_start")
            if self.observation_period_end < self.observation_period_start:
                raise ValueError("observation period ends before it starts")
        if (self.source_vintage_start is None) != (self.source_vintage_end is None):
            raise ValueError("source vintage needs both start and end")
        if (
            self.source_vintage_start is not None
            and self.source_vintage_end is not None
            and self.source_vintage_end < self.source_vintage_start
        ):
            raise ValueError("source vintage ends before it starts")
        if (self.valid_from_utc is None) != (self.valid_until_utc is None):
            raise ValueError("validity interval needs both bounds")
        if (
            self.valid_from_utc is not None
            and self.valid_until_utc is not None
            and self.valid_until_utc <= self.valid_from_utc
        ):
            raise ValueError("valid_until_utc must be after valid_from_utc")
        return self


# --- §B.6 Source references ----------------------------------------------------------

DUCKDB_PRIMARY_KEYS: dict[DuckDbTable, tuple[str, ...]] = {
    DuckDbTable.MARKET_BARS: (
        "provider",
        "symbol",
        "timeframe",
        "feed",
        "adjustment",
        "currency",
        "bar_timestamp",
    ),
    DuckDbTable.NEWS_ARTICLES: ("provider", "provider_article_id"),
    DuckDbTable.MACRO_OBSERVATIONS: (
        "provider",
        "series_id",
        "observation_date",
        "realtime_start",
        "realtime_end",
    ),
    DuckDbTable.MACRO_SERIES_METADATA: ("provider", "series_id"),
    DuckDbTable.OPTION_CHAIN_SNAPSHOT_BATCHES: ("ingestion_run_id",),
    DuckDbTable.OPTION_CHAIN_SNAPSHOTS: (
        "provider",
        "underlying",
        "feed",
        "contract_symbol",
        "retrieved_at",
    ),
    DuckDbTable.OPTION_CHAIN_SNAPSHOT_BATCH_ITEMS: (
        "ingestion_run_id",
        "provider",
        "underlying",
        "feed",
        "contract_symbol",
        "retrieved_at",
    ),
}

_RunId = Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]


class PrimaryKeyComponent(BaseModel):
    model_config = STRICT_FROZEN

    column: Token
    value: Annotated[SafeText, Field(min_length=1, max_length=128)]


class DuckDbRowReference(BaseModel):
    model_config = STRICT_FROZEN

    reference_type: Literal["duckdb_row"]
    table: DuckDbTable
    primary_key: Annotated[list[PrimaryKeyComponent], Field(min_length=1, max_length=8)]
    db_schema_version: Annotated[str, Field(pattern=r"^\d{4}$")]
    ingestion_run_id: _RunId | None

    @model_validator(mode="after")
    def _check_primary_key(self) -> DuckDbRowReference:
        columns = tuple(component.column for component in self.primary_key)
        if columns != DUCKDB_PRIMARY_KEYS[self.table]:
            raise ValueError("primary key must exactly match the registered definition")
        return self


class IngestionRunReference(BaseModel):
    model_config = STRICT_FROZEN

    reference_type: Literal["ingestion_run"]
    ingestion_run_id: _RunId
    dataset: Token
    provider: Token
    outcome: IngestionOutcome


class ProviderRequestReference(BaseModel):
    model_config = STRICT_FROZEN

    reference_type: Literal["provider_request"]
    provider: Token
    endpoint_class: EndpointClass
    feed: Feed | None
    request_params_sha256: Sha256Hex
    requested_at_utc: UtcTimestamp
    response_class: ResponseClass


_DocumentPath = Annotated[str, Field(pattern=r"^docs/[A-Z0-9_]+\.md$")]


class ResearchResultReference(BaseModel):
    model_config = STRICT_FROZEN

    reference_type: Literal["research_result"]
    study_id: Token
    result_sha256: Sha256Hex
    result_schema_version: VersionLabel
    document_path: _DocumentPath
    document_commit_sha: CommitSha


class CalculationInputReference(BaseModel):
    model_config = STRICT_FROZEN

    reference_type: Literal["calculation_input"]
    input_schema_id: VersionLabel
    input_sha256: Sha256Hex
    input_record_count: Annotated[int, Field(ge=0, le=1_000_000)]


class ModelRunReference(BaseModel):
    """No prompt text, evidence text, reasoning, or raw response ID."""

    model_config = STRICT_FROZEN

    reference_type: Literal["model_run"]
    provider: Token
    model_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9._:-]{1,64}$")]
    request_schema_id: VersionLabel
    prompt_template_id: VersionLabel
    prompt_template_sha256: Sha256Hex
    evidence_package_sha256: Sha256Hex
    output_sha256: Sha256Hex
    response_id_sha256: Sha256Hex | None
    input_tokens: Annotated[int, Field(ge=0, le=10_000_000)] | None
    output_tokens: Annotated[int, Field(ge=0, le=10_000_000)] | None


_CREDENTIAL_QUERY_NAME = re.compile(
    r"(?i)(key|token|secret|password|passwd|signature|sig|auth|credential|session)"
)


def _require_safe_https_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("external URL must be https with a host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise ValueError("external URL must not contain user information")
    for name, _ in parse_qsl(parts.query, keep_blank_values=True):
        if _CREDENTIAL_QUERY_NAME.search(name):
            raise ValueError("external URL has a credential-like query parameter")
    if sensitive_text_category(value) is not None:
        raise ValueError("external URL contains prohibited content")
    return value


class DocumentReference(BaseModel):
    model_config = STRICT_FROZEN

    reference_type: Literal["document"]
    locator_kind: LocatorKind
    document_path: _DocumentPath | None = None
    document_commit_sha: CommitSha | None = None
    section_token: Token | None = None
    publisher: Token | None = None
    https_url: Annotated[str, Field(max_length=2048)] | None = None
    published_at_utc: UtcTimestamp | None = None
    retrieved_at_utc: UtcTimestamp | None = None

    @model_validator(mode="after")
    def _check_locator(self) -> DocumentReference:
        repo = (self.document_path, self.document_commit_sha, self.section_token)
        external = (self.publisher, self.https_url, self.retrieved_at_utc)
        if self.locator_kind is LocatorKind.REPO_DOCUMENT:
            if any(v is None for v in repo):
                raise ValueError("repo_document needs path, commit and section")
            if any(v is not None for v in (*external, self.published_at_utc)):
                raise ValueError("repo_document must not carry external fields")
        else:
            if any(v is None for v in external):
                raise ValueError("external_publication needs publisher, url and retrieval time")
            if any(v is not None for v in repo):
                raise ValueError("external_publication must not carry repo fields")
            _require_safe_https_url(self.https_url or "")
        return self


EvidenceSourceReference = Annotated[
    DuckDbRowReference
    | IngestionRunReference
    | ProviderRequestReference
    | ResearchResultReference
    | CalculationInputReference
    | ModelRunReference
    | DocumentReference,
    Field(discriminator="reference_type"),
]


# --- §B.5 Provenance ------------------------------------------------------------------


class EvidenceProvenance(BaseModel):
    """§B.5. ``generated_at_utc`` is operational and excluded from identity."""

    model_config = STRICT_FROZEN

    producer_id: ProducerId
    producer_version: VersionLabel
    producer_type: ProducerType
    production_path: ProductionPath
    code_commit_sha: CommitSha
    code_tree_clean: bool
    configuration_identity: ConfigIdentity
    generated_at_utc: UtcTimestamp
    source_observed_at_utc: UtcTimestamp | None
    source_references: Annotated[list[EvidenceSourceReference], Field(max_length=32)]
    parent_evidence_ids: Annotated[list[ItemId], Field(max_length=64)]

    @model_validator(mode="after")
    def _check_lists(self) -> EvidenceProvenance:
        _require_sorted_unique(self.source_references, "source_references")
        _require_sorted_unique(self.parent_evidence_ids, "parent_evidence_ids")
        return self


# --- §B.8 Availability ----------------------------------------------------------------


class EvidenceAvailability(BaseModel):
    model_config = STRICT_FROZEN

    state: AvailabilityState
    reason_code: Token | None = None
    missing_components: Annotated[list[Token], Field(max_length=16)] = Field(
        default_factory=list
    )
    error_category: Token | None = None

    @model_validator(mode="after")
    def _check_state(self) -> EvidenceAvailability:
        _require_sorted_unique(self.missing_components, "missing_components")
        if self.state is not AvailabilityState.AVAILABLE and self.reason_code is None:
            raise ValueError("reason_code is required unless state is available")
        if self.state is AvailabilityState.PARTIAL and not self.missing_components:
            raise ValueError("partial availability must name missing components")
        if (self.state is AvailabilityState.ERROR) != (self.error_category is not None):
            raise ValueError("error_category is required iff state is error")
        return self


# --- §B.9 Quality profile -------------------------------------------------------------


class EvidenceQualityProfile(BaseModel):
    """§B.9. Separate dimensions, never collapsed into one score."""

    model_config = STRICT_FROZEN

    data_quality: DataQuality
    source_basis: SourceBasis
    source_tier: SourceTier
    calculation_completeness: CalculationCompleteness
    inference_support: InferenceSupport
    research_status: ResearchStatus
    graded_strength: GradedStrength
    strength_rubric_id: VersionLabel | None = None
    calibrated_probability: ExactRational | None = None
    uncertainty_codes: Annotated[list[Token], Field(max_length=8)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_profile(self) -> EvidenceQualityProfile:
        _require_sorted_unique(self.uncertainty_codes, "uncertainty_codes")
        graded = self.graded_strength is not GradedStrength.CONFIDENCE_NOT_AVAILABLE
        if graded != (self.strength_rubric_id is not None):
            raise ValueError("graded strength requires a rubric id, and only then")
        if self.inference_support not in (
            InferenceSupport.NOT_ASSESSED,
            InferenceSupport.NOT_APPLICABLE,
        ):
            # Only a recorded human adjudication may grade support; no such
            # mechanism exists in this core, so producers cannot self-grade.
            raise ValueError("inference_support may not be self-graded by a producer")
        return self


# --- §B.10 Research reference ---------------------------------------------------------


class EvidenceResearchReference(BaseModel):
    model_config = STRICT_FROZEN

    study_id: Token
    result_kind: ResultKind
    result_schema_version: VersionLabel
    result_sha256: Sha256Hex
    result_document_path: _DocumentPath
    result_document_commit_sha: CommitSha
    protocol_commit_shas: Annotated[list[CommitSha], Field(min_length=1, max_length=8)]
    analysis_code_commit_sha: CommitSha
    primary_label: Token
    secondary_labels: Annotated[list[Token], Field(max_length=8)] = Field(default_factory=list)
    research_status: ResearchStatus
    sample_scope: Token
    holdout_state: HoldoutState
    fixed_caveats: Annotated[list[Token], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def _check_lists(self) -> EvidenceResearchReference:
        if len(set(self.protocol_commit_shas)) != len(self.protocol_commit_shas):
            raise ValueError("protocol_commit_shas must not repeat")
        _require_sorted_unique(self.secondary_labels, "secondary_labels")
        _require_sorted_unique(self.fixed_caveats, "fixed_caveats")
        return self


# --- §B.11 / §B.12 Inference basis and scenario relations -----------------------------


class EvidenceInferenceBasis(BaseModel):
    model_config = STRICT_FROZEN

    inference_method: InferenceMethod
    rule_id: VersionLabel | None = None
    input_evidence_ids: Annotated[list[ItemId], Field(min_length=1, max_length=64)]
    claim_basis: ClaimBasis
    directional_content: DirectionalContent
    evaluation_record_ref: Sha256Hex | None = None
    limitation_codes: Annotated[list[Token], Field(max_length=8)] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_basis(self) -> EvidenceInferenceBasis:
        _require_sorted_unique(self.input_evidence_ids, "input_evidence_ids")
        _require_sorted_unique(self.limitation_codes, "limitation_codes")
        if (self.inference_method is InferenceMethod.DETERMINISTIC_RULE) != (
            self.rule_id is not None
        ):
            raise ValueError("rule_id is required iff inference_method is deterministic_rule")
        return self


class EvidenceScenarioRelation(BaseModel):
    model_config = STRICT_FROZEN

    scenario: EvidenceSubject
    scenario_class: ScenarioClass
    relation: ScenarioRelationKind
    rationale_category: RationaleCategory
    supporting_evidence_ids: Annotated[list[ItemId], Field(min_length=1, max_length=16)]
    authorization_record_id: VersionLabel

    @model_validator(mode="after")
    def _check_relation(self) -> EvidenceScenarioRelation:
        if self.scenario.subject_type is not SubjectType.SCENARIO:
            raise ValueError("scenario relation must reference a scenario subject")
        _require_sorted_unique(self.supporting_evidence_ids, "supporting_evidence_ids")
        return self


# --- §M Revision --------------------------------------------------------------------------


class EvidenceRevision(BaseModel):
    model_config = STRICT_FROZEN

    revision_number: Annotated[int, Field(ge=1, le=10_000)]
    supersedes_item_id: ItemId | None = None
    revision_reason: RevisionReason

    @model_validator(mode="after")
    def _check_revision(self) -> EvidenceRevision:
        original = self.revision_number == 1
        if original != (self.supersedes_item_id is None):
            raise ValueError("supersedes_item_id is null iff revision_number is 1")
        if original != (self.revision_reason is RevisionReason.ORIGINAL):
            raise ValueError("revision_reason is original iff revision_number is 1")
        return self


ORIGINAL_REVISION = EvidenceRevision(revision_number=1, revision_reason=RevisionReason.ORIGINAL)


# --- Payload bounds ------------------------------------------------------------------------


def _check_json_value(value: Any, depth: int) -> None:
    if depth > MAX_PAYLOAD_DEPTH:
        raise ValueError("payload nesting is too deep")
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float):
        raise ValueError("payload must not contain floats; use canonical decimal strings")
    if isinstance(value, str):
        if sensitive_text_category(value) is not None:
            raise ValueError("payload contains prohibited sensitive content")
        return
    if isinstance(value, list):
        for element in value:
            _check_json_value(element, depth + 1)
        return
    if isinstance(value, dict):
        for key, element in value.items():
            if not isinstance(key, str):
                raise ValueError("payload keys must be strings")
            _check_json_value(element, depth + 1)
        return
    raise ValueError("payload must contain only JSON values")


# --- §B.3 Evidence item -------------------------------------------------------------------

_KIND_PATH_BASIS: dict[EvidenceKind, tuple[set[ProductionPath], set[SourceBasis]]] = {
    EvidenceKind.CONFIRMED_FACT: ({ProductionPath.DETERMINISTIC}, {SourceBasis.DIRECTLY_OBSERVED}),
    EvidenceKind.DETERMINISTIC_CALCULATION: (
        {ProductionPath.DETERMINISTIC},
        {SourceBasis.DERIVED},
    ),
    EvidenceKind.HISTORICAL_RESEARCH_RESULT: (
        {ProductionPath.DETERMINISTIC},
        {SourceBasis.DERIVED},
    ),
    EvidenceKind.CURRENT_INFERENCE: (
        {ProductionPath.DETERMINISTIC, ProductionPath.MODEL_GENERATED},
        {SourceBasis.RULE_INFERRED, SourceBasis.MODEL_INFERRED},
    ),
    EvidenceKind.MISSING_EVIDENCE: (
        {ProductionPath.DETERMINISTIC},
        {SourceBasis.DIRECTLY_OBSERVED},
    ),
    EvidenceKind.STALE_EVIDENCE: ({ProductionPath.DETERMINISTIC}, {SourceBasis.DIRECTLY_OBSERVED}),
}

_FACT_REFERENCE_TYPES = {"duckdb_row", "provider_request", "document"}


class EvidenceItemContent(BaseModel):
    """§B.3. Every substantive field of an evidence item (everything but its ID)."""

    model_config = STRICT_FROZEN

    schema_version: _SchemaVersion = SCHEMA_VERSION
    evidence_kind: EvidenceKind
    subjects: Annotated[list[EvidenceSubject], Field(min_length=1, max_length=8)]
    effective_at_utc: UtcTimestamp
    temporal_scope: EvidenceTemporalScope
    payload_schema_id: VersionLabel
    payload: dict[str, Any]
    provenance: EvidenceProvenance
    freshness_policy_id: VersionLabel
    availability: EvidenceAvailability
    quality: EvidenceQualityProfile
    research_reference: EvidenceResearchReference | None = None
    inference_basis: EvidenceInferenceBasis | None = None
    scenario_relations: Annotated[list[EvidenceScenarioRelation], Field(max_length=8)] = Field(
        default_factory=list
    )
    display_text: Annotated[list[DisplayText], Field(max_length=4)] = Field(
        default_factory=list
    )
    revision: EvidenceRevision
    machine_decision_eligible: bool

    @model_validator(mode="after")
    def _check_item(self) -> EvidenceItemContent:
        self._check_subjects()
        self._check_kind_consistency()
        self._check_kind_specific_parts()
        self._check_quality()
        self._check_temporal()
        self._check_payload()
        return self

    def _check_subjects(self) -> None:
        if self.subjects[0] in self.subjects[1:]:
            raise ValueError("primary subject must not repeat")
        _require_sorted_unique(self.subjects[1:], "secondary subjects")

    def _check_kind_consistency(self) -> None:
        kind = self.evidence_kind
        paths, bases = _KIND_PATH_BASIS[kind]
        prov = self.provenance
        if prov.production_path not in paths:
            raise ValueError("production_path is not allowed for this evidence kind")
        if self.quality.source_basis not in bases:
            raise ValueError("source_basis is not allowed for this evidence kind")
        if kind is EvidenceKind.CURRENT_INFERENCE:
            expected = (
                SourceBasis.MODEL_INFERRED
                if prov.production_path is ProductionPath.MODEL_GENERATED
                else SourceBasis.RULE_INFERRED
            )
            if self.quality.source_basis is not expected:
                raise ValueError("inference source_basis must match its production_path")

        ref_types = [ref.reference_type for ref in prov.source_references]
        model_runs = ref_types.count("model_run")
        if (prov.production_path is ProductionPath.MODEL_GENERATED) != (model_runs == 1):
            raise ValueError("exactly one model_run reference iff production is model_generated")
        if model_runs > 1:
            raise ValueError("at most one model_run reference")
        if kind is EvidenceKind.CONFIRMED_FACT and not _FACT_REFERENCE_TYPES & set(ref_types):
            raise ValueError("a confirmed fact needs a row, provider-request or document reference")
        if kind is EvidenceKind.DETERMINISTIC_CALCULATION and "calculation_input" not in ref_types:
            raise ValueError("a deterministic calculation needs a calculation_input reference")
        if kind is EvidenceKind.HISTORICAL_RESEARCH_RESULT and not ref_types:
            raise ValueError("a research result needs a source reference")

        research = kind is EvidenceKind.HISTORICAL_RESEARCH_RESULT
        if research != (prov.source_observed_at_utc is None):
            raise ValueError("source_observed_at_utc is null iff the item is a research result")

        missing = kind is EvidenceKind.MISSING_EVIDENCE
        state = self.availability.state
        if missing and state is AvailabilityState.AVAILABLE:
            raise ValueError("missing evidence cannot be available")
        if not missing and state not in (AvailabilityState.AVAILABLE, AvailabilityState.PARTIAL):
            raise ValueError("only missing evidence may report an unavailable state")

        if kind is EvidenceKind.MISSING_EVIDENCE:
            expected_payload: str | None = MISSING_EVIDENCE_PAYLOAD
        elif kind is EvidenceKind.STALE_EVIDENCE:
            expected_payload = STALE_EVIDENCE_PAYLOAD
        else:
            expected_payload = None
        if expected_payload is not None and self.payload_schema_id != expected_payload:
            raise ValueError("status kinds must use their fixed payload schema")
        if expected_payload is None and self.payload_schema_id in STATUS_PAYLOADS:
            raise ValueError("status payload schemas are reserved for status kinds")

    def _check_kind_specific_parts(self) -> None:
        kind = self.evidence_kind
        parents = set(self.provenance.parent_evidence_ids)
        if kind is EvidenceKind.HISTORICAL_RESEARCH_RESULT and self.research_reference is None:
            raise ValueError("a research result requires a research_reference")
        if self.research_reference is not None and kind not in (
            EvidenceKind.HISTORICAL_RESEARCH_RESULT,
            EvidenceKind.DETERMINISTIC_CALCULATION,
        ):
            raise ValueError("research_reference is forbidden for this kind")

        inference = kind is EvidenceKind.CURRENT_INFERENCE
        if inference != (self.inference_basis is not None):
            raise ValueError("inference_basis is required iff the item is an inference")
        if self.scenario_relations and not inference:
            raise ValueError("scenario relations are allowed only on inference")
        basis = self.inference_basis
        if basis is not None:
            model = self.provenance.production_path is ProductionPath.MODEL_GENERATED
            if model != (basis.inference_method is InferenceMethod.MODEL_STRUCTURED_OUTPUT):
                raise ValueError("inference_method must match production_path")
            if not set(basis.input_evidence_ids) <= parents:
                raise ValueError("inference inputs must be parents")
            has_relations = bool(self.scenario_relations)
            if has_relations != (basis.directional_content is DirectionalContent.SCENARIO_RELATION):
                raise ValueError("directional_content must reflect scenario relations")
            if basis.claim_basis is ClaimBasis.EXPLAINS_SCENARIO_RELATION and not has_relations:
                raise ValueError("explains_scenario_relation requires a scenario relation")
        _require_sorted_unique(self.scenario_relations, "scenario_relations")
        for relation in self.scenario_relations:
            if not set(relation.supporting_evidence_ids) <= parents:
                raise ValueError("scenario relation evidence must be parents")

    def _check_quality(self) -> None:
        quality = self.quality
        calc = self.evidence_kind is EvidenceKind.DETERMINISTIC_CALCULATION
        completeness = quality.calculation_completeness
        if not calc and completeness is not CalculationCompleteness.NOT_APPLICABLE:
            raise ValueError("calculation_completeness applies only to calculations")
        expected = (
            self.research_reference.research_status
            if self.research_reference is not None
            else ResearchStatus.NOT_APPLICABLE
        )
        if quality.research_status is not expected:
            raise ValueError("research_status must equal the research reference's status")

    def _check_temporal(self) -> None:
        observed = self.provenance.source_observed_at_utc
        if observed is not None and observed > self.effective_at_utc:
            raise ValueError("source_observed_at_utc must not be after effective_at_utc")
        scope = self.temporal_scope
        session_dates = {
            date.fromisoformat(s.subject_id[-10:])
            for s in self.subjects
            if s.subject_type is SubjectType.MARKET_SESSION
        }
        if session_dates:
            if len(session_dates) > 1 or scope.session_date not in session_dates:
                raise ValueError("session_date must equal the market_session subject's date")
        if scope.session_date is not None and self.evidence_kind in (
            EvidenceKind.CONFIRMED_FACT,
            EvidenceKind.DETERMINISTIC_CALCULATION,
        ):
            if self.effective_at_utc.astimezone(EASTERN).date() != scope.session_date:
                raise ValueError("session_date must equal the New York date of effective_at")
        types = {s.subject_type for s in self.subjects}
        if SubjectType.SCHEDULED_CATALYST in types and scope.scheduled_event_at_utc is None:
            raise ValueError("scheduled catalysts require scheduled_event_at_utc")
        if (
            self.evidence_kind is EvidenceKind.CONFIRMED_FACT
            and self.subjects[0].subject_type is SubjectType.MACRO_SERIES
            and (scope.observation_period_start is None or scope.source_vintage_start is None)
        ):
            raise ValueError("economic observations require observation period and vintage")

    def _check_payload(self) -> None:
        _check_json_value(self.payload, 0)
        limit = (
            MAX_RESEARCH_PAYLOAD_BYTES
            if self.evidence_kind is EvidenceKind.HISTORICAL_RESEARCH_RESULT
            else MAX_PAYLOAD_BYTES
        )
        if len(canonical_json_bytes(self.payload)) > limit:
            raise ValueError("payload exceeds its canonical size limit")


ITEM_EXCLUDED_PATHS = (("provenance", "generated_at_utc"),)


def item_identity_payload(item: EvidenceItemContent) -> dict[str, Any]:
    dump = item.model_dump(mode="json")
    dump.pop("item_id", None)
    return remove_paths(dump, ITEM_EXCLUDED_PATHS)


def compute_item_id(item: EvidenceItemContent) -> str:
    return prefixed_id(ITEM_PREFIX, item_identity_payload(item))


class EvidenceItem(EvidenceItemContent):
    """A sealed evidence item: ``item_id`` must equal the recomputed identity."""

    item_id: ItemId

    @model_validator(mode="after")
    def _check_identity(self) -> EvidenceItem:
        if self.item_id != compute_item_id(self):
            raise ValueError("item_id does not match the recomputed identity")
        if self.item_id in self.provenance.parent_evidence_ids:
            raise ValueError("an item cannot be its own parent")
        return self


def _fields(model: BaseModel) -> dict[str, Any]:
    return {name: getattr(model, name) for name in type(model).model_fields}


def seal_item(content: EvidenceItemContent) -> EvidenceItem:
    return EvidenceItem(**_fields(content), item_id=compute_item_id(content))


# --- §B.2 Envelope ------------------------------------------------------------------------


class EvidenceEnvelopeContent(BaseModel):
    model_config = STRICT_FROZEN

    schema_version: _SchemaVersion = SCHEMA_VERSION
    registry_version_id: RegistryVersionId
    producer_id: ProducerId
    producer_version: VersionLabel
    producer_run_key: Sha256Hex
    run_as_of_utc: UtcTimestamp
    availability: EvidenceAvailability
    items: Annotated[list[EvidenceItem], Field(max_length=MAX_ITEMS_PER_ENVELOPE)]
    emitted_at_utc: UtcTimestamp

    @model_validator(mode="after")
    def _check_envelope(self) -> EvidenceEnvelopeContent:
        ids = [item.item_id for item in self.items]
        if len(set(ids)) != len(ids) or ids != sorted(ids):
            raise ValueError("items must be sorted by item_id without duplicates")
        for item in self.items:
            if (
                item.provenance.producer_id != self.producer_id
                or item.provenance.producer_version != self.producer_version
            ):
                raise ValueError("every item must come from the envelope's producer")
        if self.run_as_of_utc > self.emitted_at_utc:
            raise ValueError("run_as_of_utc must not be after emitted_at_utc")
        kinds = [item.evidence_kind for item in self.items]
        if self.availability.state is AvailabilityState.AVAILABLE:
            if not any(k is not EvidenceKind.MISSING_EVIDENCE for k in kinds):
                raise ValueError("an available run needs at least one non-missing item")
        elif self.availability.state is not AvailabilityState.PARTIAL:
            if not kinds or any(k is not EvidenceKind.MISSING_EVIDENCE for k in kinds):
                raise ValueError("an unavailable run must carry only explicit missing evidence")
        if len(canonical_json_bytes(self.model_dump(mode="json"))) > MAX_ENVELOPE_BYTES:
            raise ValueError("envelope exceeds its canonical size limit")
        return self


def envelope_identity_payload(envelope: EvidenceEnvelopeContent) -> dict[str, Any]:
    dump = envelope.model_dump(mode="json")
    dump.pop("envelope_id", None)
    dump.pop("emitted_at_utc")
    dump["items"] = [item.item_id for item in envelope.items]
    return dump


def compute_envelope_id(envelope: EvidenceEnvelopeContent) -> str:
    return prefixed_id(ENVELOPE_PREFIX, envelope_identity_payload(envelope))


def compute_producer_run_key(
    *,
    producer_id: str,
    producer_version: str,
    configuration_identity: str,
    run_as_of_utc: Any,
    input_digest: str,
) -> str:
    """SHA-256 of the canonical run key (design §B.2); retries reproduce it."""
    return canonical_sha256(
        {
            "configuration_identity": configuration_identity,
            "input_digest": input_digest,
            "producer_id": producer_id,
            "producer_version": producer_version,
            "run_as_of_utc": format_utc(run_as_of_utc),
        }
    )


class EvidenceEnvelope(EvidenceEnvelopeContent):
    envelope_id: EnvelopeId

    @model_validator(mode="after")
    def _check_identity(self) -> EvidenceEnvelope:
        if self.envelope_id != compute_envelope_id(self):
            raise ValueError("envelope_id does not match the recomputed identity")
        return self


def seal_envelope(content: EvidenceEnvelopeContent) -> EvidenceEnvelope:
    return EvidenceEnvelope(**_fields(content), envelope_id=compute_envelope_id(content))


# --- §B.13 Conflicts ------------------------------------------------------------------------


class ConflictResolution(BaseModel):
    """Supersedes the prior conflict-status record only, never evidence."""

    model_config = STRICT_FROZEN

    resolution_kind: ResolutionKind
    resolution_evidence_ids: Annotated[list[ItemId], Field(max_length=16)] = Field(
        default_factory=list
    )
    supersedes_conflict_id: ConflictId

    @model_validator(mode="after")
    def _check_resolution(self) -> ConflictResolution:
        _require_sorted_unique(self.resolution_evidence_ids, "resolution_evidence_ids")
        if (
            not self.resolution_evidence_ids
            and self.resolution_kind is not ResolutionKind.MANUAL_ACKNOWLEDGEMENT
        ):
            raise ValueError("only a manual acknowledgement may cite no evidence")
        return self


class EvidenceConflictContent(BaseModel):
    """§B.13. There is deliberately no winner or preferred-item field."""

    model_config = STRICT_FROZEN

    schema_version: _SchemaVersion = SCHEMA_VERSION
    conflict_type: ConflictType
    involved_item_ids: Annotated[list[ItemId], Field(min_length=2, max_length=16)]
    detection_method: DetectionMethod
    detector_producer_id: ProducerId
    detector_version: VersionLabel
    rule_id: VersionLabel | None = None
    severity: ConflictSeverity
    status: ConflictStatus
    resolution: ConflictResolution | None = None
    evaluated_as_of_utc: UtcTimestamp
    detected_at_utc: UtcTimestamp

    @model_validator(mode="after")
    def _check_conflict(self) -> EvidenceConflictContent:
        _require_sorted_unique(self.involved_item_ids, "involved_item_ids")
        if (self.detection_method is DetectionMethod.DETERMINISTIC_RULE) != (
            self.rule_id is not None
        ):
            raise ValueError("rule_id is required iff detection is a deterministic rule")
        if (self.status is ConflictStatus.UNRESOLVED) != (self.resolution is None):
            raise ValueError("resolution is required iff the conflict is not unresolved")
        if self.status is ConflictStatus.ACKNOWLEDGED_UNRESOLVABLE and (
            self.resolution is None
            or self.resolution.resolution_kind is not ResolutionKind.MANUAL_ACKNOWLEDGEMENT
        ):
            raise ValueError("acknowledged_unresolvable requires a manual acknowledgement")
        return self


def conflict_identity_payload(conflict: EvidenceConflictContent) -> dict[str, Any]:
    dump = conflict.model_dump(mode="json")
    dump.pop("conflict_id", None)
    dump.pop("detected_at_utc")
    return dump


def compute_conflict_id(conflict: EvidenceConflictContent) -> str:
    return prefixed_id(CONFLICT_PREFIX, conflict_identity_payload(conflict))


class EvidenceConflict(EvidenceConflictContent):
    conflict_id: ConflictId

    @model_validator(mode="after")
    def _check_identity(self) -> EvidenceConflict:
        if self.conflict_id != compute_conflict_id(self):
            raise ValueError("conflict_id does not match the recomputed identity")
        resolution = self.resolution
        if resolution is not None and resolution.supersedes_conflict_id == self.conflict_id:
            raise ValueError("a conflict record cannot supersede itself")
        return self


def seal_conflict(content: EvidenceConflictContent) -> EvidenceConflict:
    return EvidenceConflict(**_fields(content), conflict_id=compute_conflict_id(content))


# --- §B.7 Freshness (a bundle-time evaluation) ---------------------------------------------

_STATE_REASONS: dict[FreshnessState, set[FreshnessReason]] = {
    FreshnessState.TIMELESS: {FreshnessReason.TIMELESS_BY_POLICY},
    FreshnessState.CURRENT: {FreshnessReason.WITHIN_CURRENT_WINDOW},
    FreshnessState.AGING: {FreshnessReason.WITHIN_AGING_WINDOW},
    FreshnessState.STALE: {FreshnessReason.BEYOND_STALE_BOUNDARY},
    FreshnessState.UNKNOWN: {
        FreshnessReason.OBSERVED_TIMESTAMP_MISSING,
        FreshnessReason.OBSERVED_TIMESTAMP_IN_FUTURE,
        FreshnessReason.CLOCK_UNHEALTHY,
        FreshnessReason.CLOCK_HEALTH_UNKNOWN,
        FreshnessReason.POLICY_PARAMETERS_UNSET,
        FreshnessReason.OFF_HOURS_RULE_APPLIED,
    },
}

# Freshness decided before the clock step (§F.4 steps 1-2): no clock evaluated.
PRE_CLOCK_REASONS = frozenset(
    {FreshnessReason.TIMELESS_BY_POLICY, FreshnessReason.POLICY_PARAMETERS_UNSET}
)


class EvidenceFreshness(BaseModel):
    model_config = STRICT_FROZEN

    policy_id: VersionLabel
    evaluated_at_utc: UtcTimestamp
    source_observed_at_utc: UtcTimestamp | None
    age_seconds: Annotated[int, Field(ge=0)] | None
    state: FreshnessState
    reason: FreshnessReason
    clock_health_item_id: ItemId | None
    clock_health_reason: ClockHealthReason

    @model_validator(mode="after")
    def _check_freshness(self) -> EvidenceFreshness:
        if self.reason not in _STATE_REASONS[self.state]:
            raise ValueError("freshness reason does not match its state")
        clock = self.clock_health_reason
        # The ID names a clock fact only when a particular fact was evaluated;
        # every other clock outcome is carried by the bounded reason alone.
        if (clock in CLOCK_FACT_EVALUATED) != (self.clock_health_item_id is not None):
            raise ValueError("clock_health_item_id is present iff a clock fact was evaluated")
        if (self.reason in PRE_CLOCK_REASONS) != (clock is ClockHealthReason.NOT_EVALUATED):
            raise ValueError("clock is not evaluated only for timeless or unset policies")
        if (self.reason is FreshnessReason.CLOCK_HEALTH_UNKNOWN) != (
            clock in CLOCK_UNKNOWN_REASONS
        ):
            raise ValueError("clock_health_unknown must carry an unknown-clock reason")
        if (self.reason is FreshnessReason.CLOCK_UNHEALTHY) != (clock in CLOCK_UNHEALTHY_REASONS):
            raise ValueError("clock_unhealthy must carry an unhealthy-clock reason")
        if (
            self.reason
            not in PRE_CLOCK_REASONS
            | {FreshnessReason.CLOCK_HEALTH_UNKNOWN, FreshnessReason.CLOCK_UNHEALTHY}
            and clock is not ClockHealthReason.HEALTHY
        ):
            raise ValueError("freshness past the clock step requires a healthy clock")
        observed = self.source_observed_at_utc
        if observed is None or observed > self.evaluated_at_utc:
            if self.age_seconds is not None:
                raise ValueError("age is null when the observed time is missing or future")
        return self


# --- §B.14 - §B.16 Citations, consumer context, query, bundle -------------------------------


class EvidenceCitation(BaseModel):
    model_config = STRICT_FROZEN

    bundle_id: BundleId
    claim_index: Annotated[int, Field(ge=0, le=255)]
    cited_item_ids: Annotated[list[ItemId], Field(min_length=1, max_length=8)]
    citation_role: CitationRole
    cited_kinds: Annotated[list[EvidenceKind], Field(min_length=1, max_length=6)]
    cited_conflict_ids: Annotated[list[ConflictId], Field(max_length=8)] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def _check_citation(self) -> EvidenceCitation:
        _require_sorted_unique(self.cited_item_ids, "cited_item_ids")
        _require_sorted_unique(self.cited_kinds, "cited_kinds")
        _require_sorted_unique(self.cited_conflict_ids, "cited_conflict_ids")
        if self.citation_role is CitationRole.CONFLICT_NOTICE and not self.cited_conflict_ids:
            raise ValueError("a conflict notice must cite a conflict")
        return self


class EvidenceConsumerContext(BaseModel):
    model_config = STRICT_FROZEN

    consumer_id: ConsumerId
    purpose: BundlePurpose
    permissions: Annotated[list[ConsumerPermission], Field(max_length=9)]
    machine_decision_mode: bool
    request_sha256: Sha256Hex

    @model_validator(mode="after")
    def _check_context(self) -> EvidenceConsumerContext:
        _require_sorted_unique(self.permissions, "permissions")
        if self.machine_decision_mode and self.consumer_id not in (
            ConsumerId.SETUP_RANKER,
            ConsumerId.NOTIFICATION_LAYER,
        ):
            raise ValueError("machine_decision_mode is only for setup_ranker/notification_layer")
        return self


class EvidenceQuery(BaseModel):
    """§N.2. Bounded fields only: no SQL, free text, regex, or semantic search."""

    model_config = STRICT_FROZEN

    producer_ids: Annotated[list[ProducerId], Field(max_length=16)] = Field(default_factory=list)
    evidence_kinds: Annotated[list[EvidenceKind], Field(max_length=6)] = Field(
        default_factory=list
    )
    subject_ids: Annotated[list[Annotated[str, Field(max_length=160)]], Field(max_length=32)] = (
        Field(default_factory=list)
    )
    subject_types: Annotated[list[SubjectType], Field(max_length=13)] = Field(
        default_factory=list
    )
    effective_from_utc: UtcTimestamp
    effective_to_utc: UtcTimestamp
    include_superseded: bool = False
    max_entries: Annotated[int, Field(ge=1, le=MAX_BUNDLE_ENTRIES)]

    @model_validator(mode="after")
    def _check_query(self) -> EvidenceQuery:
        for values, what in (
            (self.producer_ids, "producer_ids"),
            (self.evidence_kinds, "evidence_kinds"),
            (self.subject_ids, "subject_ids"),
            (self.subject_types, "subject_types"),
        ):
            _require_sorted_unique(values, what)
        for subject_id in self.subject_ids:
            if subject_type_for_id(subject_id) is None:
                raise ValueError("query subject ids must be canonical")
        if self.effective_to_utc < self.effective_from_utc:
            raise ValueError("query window ends before it starts")
        return self


class EvidenceBundleEntry(BaseModel):
    model_config = STRICT_FROZEN

    item_id: ItemId
    evidence_kind: EvidenceKind
    producer_id: ProducerId
    required: bool
    freshness: EvidenceFreshness
    availability_state: AvailabilityState
    machine_decision_eligible: bool
    superseded_as_of: bool
    latest_revision_item_id: ItemId | None


class MissingProducer(BaseModel):
    """An unmet bundle requirement, named by its full registered
    requirement identity (not by producer alone)."""

    model_config = STRICT_FROZEN

    requirement_id: VersionLabel
    producer_id: ProducerId
    reason: MissingProducerReason


class AmbiguousRequirement(BaseModel):
    """A requirement whose qualifying evidence has no single winner after
    the substantive ordering (effective time, observed time, revision number)
    and revision-chain resolution. Nothing is chosen: every competing item ID
    is kept, sorted by ID for audit only (the ID never decides a winner).
    The builder does not create conflict records; it links any matching
    unresolved conflict already recorded, and an empty list means one is
    still required."""

    model_config = STRICT_FROZEN

    requirement_id: VersionLabel
    reason: AmbiguityReason
    competing_item_ids: Annotated[list[ItemId], Field(min_length=2, max_length=32)]
    matching_unresolved_conflict_ids: Annotated[list[ConflictId], Field(max_length=16)]

    @model_validator(mode="after")
    def _check_ambiguity(self) -> AmbiguousRequirement:
        _require_sorted_unique(self.competing_item_ids, "competing_item_ids")
        _require_sorted_unique(
            self.matching_unresolved_conflict_ids, "matching_unresolved_conflict_ids"
        )
        return self


class EvidenceBundleManifestContent(BaseModel):
    model_config = STRICT_FROZEN

    schema_version: _SchemaVersion = SCHEMA_VERSION
    purpose: BundlePurpose
    as_of_utc: UtcTimestamp
    registry_version_id: RegistryVersionId
    selection_rule_id: VersionLabel
    query: EvidenceQuery
    consumer_context: EvidenceConsumerContext
    entries: Annotated[list[EvidenceBundleEntry], Field(max_length=MAX_BUNDLE_ENTRIES)]
    missing_required_producers: Annotated[list[MissingProducer], Field(max_length=32)]
    ambiguous_requirements: Annotated[list[AmbiguousRequirement], Field(max_length=32)]
    conflict_ids: Annotated[list[ConflictId], Field(max_length=256)]
    unresolved_material_conflict_count: Annotated[int, Field(ge=0)]
    machine_decision_ready: bool
    built_at_utc: UtcTimestamp

    @model_validator(mode="after")
    def _check_manifest(self) -> EvidenceBundleManifestContent:
        ids = [entry.item_id for entry in self.entries]
        if len(set(ids)) != len(ids) or ids != sorted(ids):
            raise ValueError("entries must be sorted by item_id without duplicates")
        requirements = [m.requirement_id for m in self.missing_required_producers]
        if len(set(requirements)) != len(requirements) or requirements != sorted(requirements):
            raise ValueError("missing requirements must be sorted and unique")
        _require_sorted_unique(self.conflict_ids, "conflict_ids")
        ambiguous = [a.requirement_id for a in self.ambiguous_requirements]
        if len(set(ambiguous)) != len(ambiguous) or ambiguous != sorted(ambiguous):
            raise ValueError("ambiguous requirements must be sorted and unique")
        if set(ambiguous) & set(requirements):
            raise ValueError("a requirement is either missing or ambiguous, not both")
        if self.consumer_context.purpose is not self.purpose:
            raise ValueError("consumer context purpose must equal the bundle purpose")
        for entry in self.entries:
            if entry.freshness.evaluated_at_utc != self.as_of_utc:
                raise ValueError("every freshness evaluation must be at the bundle as-of")
        if self.machine_decision_ready and (
            self.missing_required_producers
            or self.ambiguous_requirements
            or any(
                entry.required
                and entry.freshness.state
                not in (FreshnessState.CURRENT, FreshnessState.TIMELESS)
                for entry in self.entries
            )
        ):
            raise ValueError("readiness contradicts missing, ambiguous or stale requirements")
        return self


def bundle_identity_payload(bundle: EvidenceBundleManifestContent) -> dict[str, Any]:
    dump = bundle.model_dump(mode="json")
    dump.pop("bundle_id", None)
    dump.pop("built_at_utc")
    return dump


def compute_bundle_id(bundle: EvidenceBundleManifestContent) -> str:
    return prefixed_id(BUNDLE_PREFIX, bundle_identity_payload(bundle))


class EvidenceBundleManifest(EvidenceBundleManifestContent):
    bundle_id: BundleId

    @model_validator(mode="after")
    def _check_identity(self) -> EvidenceBundleManifest:
        if self.bundle_id != compute_bundle_id(self):
            raise ValueError("bundle_id does not match the recomputed identity")
        return self


def seal_bundle(content: EvidenceBundleManifestContent) -> EvidenceBundleManifest:
    return EvidenceBundleManifest(**_fields(content), bundle_id=compute_bundle_id(content))
