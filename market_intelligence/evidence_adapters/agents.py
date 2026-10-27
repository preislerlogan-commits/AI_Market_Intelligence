"""Adapters for the existing analysis agents' validated reports (registry
``market_evidence_agent``, ``news_agent``, ``macro_agent``).

A completed, already-validated agent report becomes labelled,
non-directional ``current_inference``; an abstained report becomes
``missing_evidence``. Nothing else is emitted:

- **One item per observation or claim** (``market_evidence_observation.v1``,
  ``news_event_claim.v1``, ``macro_claim.v1``).
- **An abstention is missing evidence, never inference** (registry design):
  the agent produced no interpretation. It becomes one ``missing_evidence``
  item with the existing ``missing_evidence.v1`` payload: the agent as the
  expected producer, the expected subject, a hash of the agent's request as
  the query, the agent's as-of time and its first reason code. The full
  reason list and the agent's limitations are kept as bounded display text,
  and the evidence the agent was given stays as parents. It carries no
  conclusion, direction, probability or value, cannot satisfy a requirement
  (status kinds never do) and is never machine-decision eligible. Only an
  abstention made before any model call is accepted: missing evidence cannot
  carry a ``model_run`` reference, so a post-model abstention is refused
  (``post_model_abstention_not_representable``) until the Evidence Envelope
  contract is amended.
- **Explicit source context.** The caller supplies the evidence bundle the
  agent was given, its items, and the mapping from each agent citation key to
  an item in that bundle. Every claim must cite at least one key; every key
  must resolve to an item in the bundle; those items become the parents.
  Uncited claims, unresolvable keys, evidence outside the bundle and
  inference parents are refused.
- **Never a fact, calculation, research result, probability or
  machine-decision input.** Claims are always ``current_inference`` and
  abstentions always ``missing_evidence``; the registry must give these
  producers only non-eligible rules of those two kinds, and
  inference is machine-decision eligible only under an inference-input
  authorization, which is structurally absent. The model's reported
  ``evidence_quality`` is kept in the payload as
  ``model_reported_evidence_quality``; item quality is ``not_assessed``.
- **Non-directional.** Payloads carry ``non_directional = true`` and the
  agents' fixed ``directional_assessment`` / ``trade_recommendation`` of
  ``not_performed``. Claim and summary text is re-checked with the agents'
  shared non-directional policy. A completed report's limitations are not
  re-checked here: each agent already validated them, including the Macro
  Analyst's narrow negated-disclaimer allowance. An abstention's limitations
  become display text, which the core always checks against that policy.
- **Provenance.** The agent (producer), the registry's producer version, the
  agent's own generation time (``effective_at`` and payload), the cited keys
  and the adapter configuration identity are preserved. A model-generated
  item carries exactly one ``model_run`` reference.

This module never imports an agent, model client, provider or database. It
reads the report's fields only.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from market_intelligence.agents.non_directional_output_policy import (
    find_prohibited_content_category,
)
from market_intelligence.evidence.canonical import canonical_sha256
from market_intelligence.evidence.contracts import (
    MISSING_EVIDENCE_PAYLOAD,
    CalculationInputReference,
    EvidenceAvailability,
    EvidenceBundleManifest,
    EvidenceInferenceBasis,
    EvidenceItem,
    EvidenceSubject,
    EvidenceTemporalScope,
    ModelRunReference,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    ClaimBasis,
    DirectionalContent,
    EvidenceKind,
    InferenceMethod,
    ProductionPath,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence.primitives import format_utc
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence_adapters.common import (
    SPY_SUBJECT_ID,
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    fail,
    parse_date,
    parse_utc,
    require_configuration,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    MACRO_CLAIM,
    MARKET_EVIDENCE_OBSERVATION,
    NEWS_EVENT_CLAIM,
)

MARKET_EVIDENCE_PRODUCER = "market_evidence_agent"
NEWS_PRODUCER = "news_agent"
MACRO_PRODUCER = "macro_agent"
_ALLOWED_PARENT_KINDS = frozenset(
    {
        EvidenceKind.CONFIRMED_FACT,
        EvidenceKind.DETERMINISTIC_CALCULATION,
        EvidenceKind.MISSING_EVIDENCE,
        EvidenceKind.STALE_EVIDENCE,
    }
)
_SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=SPY_SUBJECT_ID)


def _configuration(producer: str, claim_schema: str) -> str:
    return adapter_configuration_identity(
        {
            "adapter": producer,
            "claims": [EvidenceKind.CURRENT_INFERENCE.value, claim_schema],
            "abstentions": [EvidenceKind.MISSING_EVIDENCE.value, MISSING_EVIDENCE_PAYLOAD],
            "non_directional_policy": "agents.non_directional_output_policy",
            "model_reported_evidence_quality": "payload_only",
        }
    )


AGENT_CONFIGURATIONS: Mapping[str, str] = MappingProxyType(
    {
        MARKET_EVIDENCE_PRODUCER: _configuration(
            MARKET_EVIDENCE_PRODUCER, MARKET_EVIDENCE_OBSERVATION
        ),
        NEWS_PRODUCER: _configuration(NEWS_PRODUCER, NEWS_EVENT_CLAIM),
        MACRO_PRODUCER: _configuration(MACRO_PRODUCER, MACRO_CLAIM),
    }
)


# --- Report mirrors -----------------------------------------------------------------------------
#
# Strict mirrors of the agents' final report models (``extra="forbid"``, the
# same literals and bounds), so this module can re-validate a report without
# importing an agent. Citation lists may be empty here only so that an
# uncited claim is refused by name rather than as a generic shape error.

_Text = Annotated[str, Field(min_length=1, max_length=800)]
_Key = Annotated[str, Field(min_length=1, max_length=64)]
_Keys = Annotated[list[_Key], Field(max_length=5)]
_Limitations = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=300)]], Field(max_length=6)
]
_Quality = Literal["sufficient", "limited", "insufficient"]


class _Mirror(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Observation(_Mirror):
    category: Literal["data_quality", "price", "volume", "news", "macro"]
    statement: Annotated[str, Field(min_length=1, max_length=400)]
    evidence_ids: _Keys


class _MarketReport(_Mirror):
    status: Literal["completed", "abstained"]
    symbol: str
    session_date_et: str | None
    evidence_quality: _Quality | None = None
    evidence_summary: Annotated[str | None, Field(max_length=800)] = None
    observations: Annotated[list[_Observation], Field(max_length=6)] = Field(default_factory=list)
    limitations: _Limitations = Field(default_factory=list)
    directional_assessment: Literal["not_performed"] = "not_performed"
    trade_recommendation: Literal["not_performed"] = "not_performed"
    abstained_reasons: list[str] = Field(default_factory=list)


class _EventClaim(_Mirror):
    event_type: str
    claim_summary: Annotated[str, Field(min_length=1, max_length=400)]
    evidence_ids: _Keys
    content_basis: str
    transmission_channels: list[str] = Field(default_factory=list)
    relevance: str
    relevance_rationale: Annotated[str, Field(min_length=1, max_length=300)]
    conditional_mechanism: Annotated[str | None, Field(max_length=300)] = None


class _NewsReport(_Mirror):
    status: Literal["completed", "abstained"]
    symbol: str
    snapshot_created_at_utc: str
    source_article_count: int
    evidence_quality: _Quality | None = None
    event_claims: Annotated[list[_EventClaim], Field(max_length=6)] = Field(default_factory=list)
    limitations: _Limitations = Field(default_factory=list)
    directional_assessment: Literal["not_performed"] = "not_performed"
    trade_recommendation: Literal["not_performed"] = "not_performed"
    abstained_reasons: list[str] = Field(default_factory=list)


class _MacroClaim(_Mirror):
    series_id: Annotated[str, Field(min_length=1, max_length=64)]
    claim_summary: Annotated[str, Field(min_length=1, max_length=400)]
    evidence_ids: Annotated[list[_Key], Field(max_length=2)]
    content_basis: Literal["stored_observation_and_official_metadata"]
    economic_category: str
    transmission_channels: list[str] = Field(default_factory=list)
    conditional_mechanism: Annotated[str | None, Field(max_length=300)] = None


class _MacroReport(_Mirror):
    status: Literal["completed", "abstained"]
    series_ids: list[str]
    snapshot_created_at_utc: str
    source_series_count: int
    evidence_quality: _Quality | None = None
    macro_claims: Annotated[list[_MacroClaim], Field(max_length=16)] = Field(default_factory=list)
    limitations: _Limitations = Field(default_factory=list)
    directional_assessment: Literal["not_performed"] = "not_performed"
    trade_recommendation: Literal["not_performed"] = "not_performed"
    abstained_reasons: list[str] = Field(default_factory=list)


# --- Source context -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentSourceContext:
    """The evidence an agent run was given, supplied explicitly.

    ``citation_keys`` maps each agent citation key (the ``evidence_ids`` the
    agent cites) to an evidence item ID in ``bundle``; ``items`` holds exactly
    that bundle's items. ``model_run`` is ``None`` only for a preflight
    abstention, where no model was called."""

    bundle: EvidenceBundleManifest
    items: Mapping[str, EvidenceItem]
    citation_keys: Mapping[str, str]
    agent_generated_at_utc: datetime
    model_run: ModelRunReference | None


def _check_source(source: Any) -> None:
    if not isinstance(source, AgentSourceContext):
        raise fail("source_invalid")
    entries = {e.item_id for e in source.bundle.entries}
    if not set(source.items) <= entries or not set(source.citation_keys.values()) <= entries:
        raise fail("evidence_outside_bundle")
    for item_id, item in source.items.items():
        if not isinstance(item, EvidenceItem) or item.item_id != item_id:
            raise fail("evidence_outside_bundle")
    if not set(source.citation_keys.values()) <= set(source.items):
        raise fail("missing_parent")
    moment = source.agent_generated_at_utc
    if not isinstance(moment, datetime) or moment.utcoffset() is None:
        raise fail("timestamp_invalid")
    if source.model_run is not None and not isinstance(source.model_run, ModelRunReference):
        raise fail("source_invalid")


def _require_agent_permissions(registry: EvidenceRegistry, producer_id: str) -> None:
    """The registry may let an agent emit only non-eligible inference (its
    claims) and non-eligible missing evidence (its abstentions)."""
    producer = registry.producer(producer_id)
    if producer is None:
        raise fail("unknown_producer")
    allowed = {EvidenceKind.CURRENT_INFERENCE, EvidenceKind.MISSING_EVIDENCE}
    if registry.inference_input_authorizations or any(
        rule.evidence_kind not in allowed or rule.machine_decision_eligible
        for rule in producer.emission_rules
    ):
        raise fail("agent_permissions_invalid")


def _report_fields(report: Any) -> dict[str, Any]:
    if isinstance(report, BaseModel):
        return report.model_dump(mode="json")
    if isinstance(report, Mapping):
        return dict(report)
    raise fail("malformed_output")


def _parse(mirror: type[_Mirror], report: Any) -> Any:
    try:
        return mirror.model_validate(_report_fields(report))
    except ValidationError:
        raise fail("malformed_output") from None


def _check_shape(parsed: Any, claims: Sequence[Any]) -> None:
    if parsed.status == "completed":
        if not claims or parsed.abstained_reasons or parsed.evidence_quality is None:
            raise fail("malformed_output")
        if parsed.evidence_quality == "insufficient":
            raise fail("malformed_output")  # the agents pair "insufficient" with abstention
    elif claims or not parsed.abstained_reasons:
        raise fail("malformed_output")
    if any(not claim.evidence_ids for claim in claims):
        raise fail("uncited_claim")


def _check_policy(texts: Sequence[str | None]) -> None:
    for text in texts:
        if text and find_prohibited_content_category(text) is not None:
            raise fail("directional_content")


def _parents(keys: Sequence[str], source: AgentSourceContext) -> list[str]:
    parents = []
    for key in keys:
        item_id = source.citation_keys.get(key)
        if item_id is None:
            raise fail("missing_parent")
        parent = source.items[item_id]
        if parent.evidence_kind not in _ALLOWED_PARENT_KINDS:
            raise fail("parent_kind_not_allowed")
        if parent.provenance.generated_at_utc > source.agent_generated_at_utc:
            raise fail("parent_generated_later")
        parents.append(item_id)
    return sorted(set(parents))


@dataclass(frozen=True)
class _Spec:
    producer: str
    claim_schema: str
    mirror: type[_Mirror]
    claims: Callable[[Any], list[Any]]
    claim_payload: Callable[[Any, Any], dict[str, Any]]
    claim_texts: Callable[[Any], list[str | None]]
    report_texts: Callable[[Any], list[str | None]]
    claim_basis: ClaimBasis
    subjects: Callable[[Any], list[EvidenceSubject]]
    request: Callable[[Any], dict[str, Any]]


def _abstention_text(reasons: Sequence[str], limitations: Sequence[str]) -> list[str]:
    """The reasons and limitations as at most four bounded display lines."""
    lines = ["Abstained: no interpretation was produced. Reasons: " + ", ".join(reasons) + "."]
    current = ""
    for limitation in limitations:
        piece = f"Limitation: {limitation}"
        if current and len(current) + 1 + len(piece) <= 400:
            current = f"{current} {piece}"
        else:
            if current:
                lines.append(current)
            current = piece
    if current:
        lines.append(current)
    if len(lines) > 4:
        raise fail("abstention_too_large")
    return lines


def _abstention(
    spec: _Spec, parsed: Any, source: AgentSourceContext, context: AdapterContext, report_hash: str
) -> EvidenceItem:
    """The agent produced no interpretation: one ``missing_evidence`` item.

    Only a pre-model (preflight) abstention is representable. Missing
    evidence cannot carry a ``model_run`` reference under the current
    Evidence Envelope contract, so an abstention made after a model call is
    refused rather than recorded without that call."""
    if source.model_run is not None:
        raise fail("post_model_abstention_not_representable")
    keys = sorted(source.citation_keys)
    if not keys:
        raise fail("missing_parent")  # an abstention still cites what was seen
    reasons = list(parsed.abstained_reasons)
    producer = context.registry.producer(spec.producer)
    if not set(reasons) <= set(producer.reason_codes):
        raise fail("unknown_reason_code")
    at = source.agent_generated_at_utc
    query = canonical_sha256({"agent": spec.producer, "request": spec.request(parsed)})
    try:
        return build_item(
            context,
            producer_id=spec.producer,
            kind=EvidenceKind.MISSING_EVIDENCE,
            payload_schema_id=MISSING_EVIDENCE_PAYLOAD,
            payload={
                "checked_at_utc": format_utc(at),
                "expected_component": None,
                "expected_producer_id": spec.producer,
                "expected_subject_id": SPY_SUBJECT_ID,
                "query_sha256": query,
                "reason_code": reasons[0],
            },
            payload_models=ADAPTER_PAYLOAD_MODELS,
            subjects=[_SPY],
            effective_at=at,
            observed_at=at,
            temporal_scope=EvidenceTemporalScope(),
            references=[
                CalculationInputReference(
                    reference_type="calculation_input",
                    input_schema_id=f"{spec.producer}-report",
                    input_sha256=report_hash,
                    input_record_count=1,
                )
            ],
            tier=SourceTier.NOT_APPLICABLE,
            parents=_parents(keys, source),
            availability=EvidenceAvailability(
                state=AvailabilityState.UNAVAILABLE, reason_code=reasons[0]
            ),
            display_text=_abstention_text(reasons, parsed.limitations),
        )
    except ValidationError:
        raise fail("malformed_output") from None


def _adapt(spec: _Spec, report: Any, source: Any, context: AdapterContext) -> list[EvidenceItem]:
    require_configuration(context, AGENT_CONFIGURATIONS[spec.producer])
    _require_agent_permissions(context.registry, spec.producer)
    _check_source(source)
    parsed = _parse(spec.mirror, report)
    claims = spec.claims(parsed)
    _check_shape(parsed, claims)
    _check_policy([*spec.report_texts(parsed), *(t for c in claims for t in spec.claim_texts(c))])
    report_hash = canonical_sha256(parsed.model_dump(mode="json"))  # normalized form
    if parsed.status == "abstained":
        return [_abstention(spec, parsed, source, context, report_hash)]
    if source.model_run is None:
        raise fail("model_run_missing")  # a completed report always came from a model call
    references: list[Any] = [
        CalculationInputReference(
            reference_type="calculation_input",
            input_schema_id=f"{spec.producer}-report",
            input_sha256=report_hash,
            input_record_count=len(claims),
        ),
        source.model_run,
    ]
    common: dict[str, Any] = {
        "agent": spec.producer,
        "agent_generated_at_utc": source.agent_generated_at_utc,
        "model_reported_evidence_quality": parsed.evidence_quality,
        "limitations": list(parsed.limitations),
        "non_directional": True,
        "directional_assessment": "not_performed",
        "trade_recommendation": "not_performed",
    }

    def item(schema, payload, parents, basis, subjects) -> EvidenceItem:
        observed = min(source.items[p].provenance.source_observed_at_utc for p in parents)
        return build_item(
            context,
            producer_id=spec.producer,
            kind=EvidenceKind.CURRENT_INFERENCE,
            payload_schema_id=schema,
            payload=ADAPTER_PAYLOAD_MODELS[schema]
            .model_validate({**common, **payload})
            .model_dump(mode="json"),
            payload_models=ADAPTER_PAYLOAD_MODELS,
            subjects=subjects,
            effective_at=source.agent_generated_at_utc,
            observed_at=observed,
            temporal_scope=EvidenceTemporalScope(),
            references=references,
            tier=SourceTier.NOT_APPLICABLE,
            parents=parents,
            production_path=ProductionPath.MODEL_GENERATED,
            inference_basis=EvidenceInferenceBasis(
                inference_method=InferenceMethod.MODEL_STRUCTURED_OUTPUT,
                input_evidence_ids=parents,
                claim_basis=basis,
                directional_content=DirectionalContent.NONE,
            ),
        )

    try:
        return [
            item(
                spec.claim_schema,
                {
                    "report_status": "completed",
                    "claim_index": index,
                    "claim_count": len(claims),
                    "cited_evidence_keys": list(claim.evidence_ids),
                    **spec.claim_payload(parsed, claim),
                },
                _parents(claim.evidence_ids, source),
                spec.claim_basis,
                spec.subjects(claim),
            )
            for index, claim in enumerate(claims)
        ]
    except ValidationError:
        raise fail("malformed_output") from None


def _macro_subjects(claim: Any) -> list[EvidenceSubject]:
    return [
        _SPY,
        EvidenceSubject(
            subject_type=SubjectType.MACRO_SERIES,
            subject_id=f"macro_series:fred:{claim.series_id}",
        ),
    ]


_MARKET = _Spec(
    producer=MARKET_EVIDENCE_PRODUCER,
    claim_schema=MARKET_EVIDENCE_OBSERVATION,
    mirror=_MarketReport,
    claims=lambda r: list(r.observations),
    claim_payload=lambda r, c: {
        "symbol": r.symbol,
        "session_date_et": None if r.session_date_et is None else parse_date(r.session_date_et),
        "evidence_summary": r.evidence_summary,
        "category": c.category,
        "statement": c.statement,
    },
    claim_texts=lambda c: [c.statement],
    report_texts=lambda r: [r.evidence_summary],
    claim_basis=ClaimBasis.SUMMARIZES_INPUTS,
    subjects=lambda c: [_SPY],
    request=lambda r: {"symbol": r.symbol, "session_date_et": r.session_date_et},
)
_NEWS = _Spec(
    producer=NEWS_PRODUCER,
    claim_schema=NEWS_EVENT_CLAIM,
    mirror=_NewsReport,
    claims=lambda r: list(r.event_claims),
    claim_payload=lambda r, c: {
        "symbol": r.symbol,
        "snapshot_created_at_utc": parse_utc(r.snapshot_created_at_utc),
        "source_article_count": r.source_article_count,
        "event_type": c.event_type,
        "claim_summary": c.claim_summary,
        "content_basis": c.content_basis,
        "transmission_channels": list(c.transmission_channels),
        "relevance": c.relevance,
        "relevance_rationale": c.relevance_rationale,
        "conditional_mechanism": c.conditional_mechanism,
    },
    claim_texts=lambda c: [c.claim_summary, c.relevance_rationale, c.conditional_mechanism],
    report_texts=lambda r: [],
    claim_basis=ClaimBasis.INTERPRETS_MECHANISM,
    subjects=lambda c: [_SPY],
    request=lambda r: {
        "symbol": r.symbol,
        "snapshot_created_at_utc": r.snapshot_created_at_utc,
    },
)
_MACRO = _Spec(
    producer=MACRO_PRODUCER,
    claim_schema=MACRO_CLAIM,
    mirror=_MacroReport,
    claims=lambda r: list(r.macro_claims),
    claim_payload=lambda r, c: {
        "series_ids": list(r.series_ids),
        "snapshot_created_at_utc": parse_utc(r.snapshot_created_at_utc),
        "source_series_count": r.source_series_count,
        "series_id": c.series_id,
        "claim_summary": c.claim_summary,
        "content_basis": c.content_basis,
        "economic_category": c.economic_category,
        "transmission_channels": list(c.transmission_channels),
        "conditional_mechanism": c.conditional_mechanism,
    },
    claim_texts=lambda c: [c.claim_summary, c.conditional_mechanism],
    report_texts=lambda r: [],
    claim_basis=ClaimBasis.INTERPRETS_MECHANISM,
    subjects=_macro_subjects,
    request=lambda r: {
        "series_ids": list(r.series_ids),
        "snapshot_created_at_utc": r.snapshot_created_at_utc,
    },
)


def adapt_market_evidence_report(
    report: Any, *, source: AgentSourceContext, context: AdapterContext
) -> list[EvidenceItem]:
    """A validated ``MarketEvidenceReport`` as labelled inference items."""
    return _adapt(_MARKET, report, source, context)


def adapt_news_report(
    report: Any, *, source: AgentSourceContext, context: AdapterContext
) -> list[EvidenceItem]:
    """A validated ``NewsAnalystReport`` as labelled inference items."""
    return _adapt(_NEWS, report, source, context)


def adapt_macro_report(
    report: Any, *, source: AgentSourceContext, context: AdapterContext
) -> list[EvidenceItem]:
    """A validated ``MacroAnalystReport`` as labelled inference items."""
    return _adapt(_MACRO, report, source, context)
