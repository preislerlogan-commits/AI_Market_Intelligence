"""Shared machinery for the offline Evidence producer adapters.

Every adapter is a pure function: no database, file, environment, network,
clock, randomness or model access. Every time, configuration identity, source
reference, registry and parent item is passed in explicitly, and the same
input always yields the same item.

Adapters translate already-validated source objects into the reviewed
Evidence Envelope contracts. They never create a competing evidence model:
items are built with ``EvidenceItemContent``, sealed with ``seal_item`` and
re-checked with ``validate_item``. They never decide machine-decision
eligibility either: it is copied from the registry's own derivation.

Refusals raise the core's sanitized ``EvidenceValidationError`` carrying one
bounded reason token, never source text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ValidationError

from market_intelligence.evidence.canonical import configuration_identity, sort_key
from market_intelligence.evidence.contracts import (
    MISSING_EVIDENCE_PAYLOAD,
    ORIGINAL_REVISION,
    EvidenceAvailability,
    EvidenceItem,
    EvidenceItemContent,
    EvidenceProvenance,
    EvidenceQualityProfile,
    EvidenceRevision,
    EvidenceSourceReference,
    EvidenceSubject,
    EvidenceTemporalScope,
    seal_item,
    subject_type_for_id,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    CalculationCompleteness,
    DataQuality,
    EvidenceKind,
    GradedStrength,
    InferenceSupport,
    ProductionPath,
    ResearchStatus,
    SourceBasis,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence.errors import EvidenceValidationError, HoldoutRestrictedError
from market_intelligence.evidence.holdout import in_holdout_window
from market_intelligence.evidence.primitives import (
    STRICT_FROZEN,
    CommitSha,
    ConfigIdentity,
    UtcTimestamp,
    format_utc,
)
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence.validation import (
    derive_machine_decision_eligible,
    validate_item,
)

ADAPTER_VERSION = "1.0.0"
SPY_SYMBOL = "SPY"
SPY_SUBJECT_ID = "instrument:us_equity:SPY"
EASTERN = ZoneInfo("America/New_York")
_MAX_DECIMAL_PLACES = 11


def _model_key(model: BaseModel) -> bytes:
    """The core's canonical ordering for set-like lists of models."""
    return sort_key(model.model_dump(mode="json"))


def fail(reason: str) -> EvidenceValidationError:
    return EvidenceValidationError(reason)


class AdapterContext(BaseModel):
    """The explicit, non-source inputs every adapter needs. Nothing here is
    read from the environment, the clock or the repository."""

    model_config = STRICT_FROZEN

    registry: EvidenceRegistry
    code_commit_sha: CommitSha
    code_tree_clean: bool
    configuration_identity: ConfigIdentity
    generated_at_utc: UtcTimestamp


def adapter_configuration_identity(configuration: Mapping[str, Any]) -> str:
    """The ``cfg1_`` identity of one adapter's fixed configuration."""
    return configuration_identity(configuration)


def require_configuration(context: AdapterContext, expected: str) -> None:
    """An unknown configuration identity fails closed."""
    if context.configuration_identity != expected:
        raise fail("unknown_configuration")


# --- Source parsing -----------------------------------------------------------------------


def source_fields(source: Any, names: Sequence[str]) -> dict[str, Any]:
    """Read exactly the named attributes of a source object (for example a
    connector's frozen dataclass). A missing attribute is a refusal."""
    if source is None:
        raise fail("source_missing")
    values: dict[str, Any] = {}
    for name in names:
        if not hasattr(source, name):
            raise fail("source_incomplete")
        values[name] = getattr(source, name)
    return values


def parse_model(model: type[BaseModel], values: Mapping[str, Any], reason: str) -> Any:
    try:
        return model.model_validate(dict(values))
    except ValidationError:
        raise fail(reason) from None


def canonical_decimal(value: Any, *, reason: str = "source_invalid") -> str:
    """A finite ``Decimal`` (or an int) as the core's canonical decimal text.
    Floats are refused: they cannot be represented exactly."""
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise fail(reason)
    number = Decimal(value)
    if not number.is_finite():
        raise fail(reason)
    exponent = number.normalize().as_tuple().exponent
    if isinstance(exponent, int) and -exponent > _MAX_DECIMAL_PLACES:
        raise fail(reason)
    text = format(number.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def decimal_from_json_number(value: Any, *, reason: str = "source_invalid") -> Decimal:
    """A JSON number from a provider response as an exact ``Decimal`` taken
    from its shortest decimal representation; non-numbers are refused."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise fail(reason)
    try:
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        raise fail(reason) from None
    if not number.is_finite():
        raise fail(reason)
    return number


def parse_utc(value: Any, *, reason: str = "timestamp_invalid") -> datetime:
    """An RFC 3339 timestamp with an explicit offset, in UTC. Fractions
    beyond microseconds are truncated, never rounded up."""
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and 20 <= len(value) <= 40:
        text = value.strip()
        if text[-1:] in ("Z", "z"):
            text = text[:-1] + "+00:00"
        head, dot, rest = text.partition(".")
        if dot:  # rest is the fraction followed by a six-character offset
            digits, offset = rest[:-6], rest[-6:]
            if not digits.isdigit() or offset[:1] not in ("+", "-"):
                raise fail(reason)
            text = f"{head}.{digits[:6].ljust(6, '0')}{offset}"
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            raise fail(reason) from None
    else:
        raise fail(reason)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise fail(reason)
    return moment.astimezone(UTC)


def parse_date(value: Any, *, reason: str = "date_invalid") -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise fail(reason)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise fail(reason) from None


def start_of_day_utc(day: date) -> datetime:
    return datetime.combine(day, time(0, 0), tzinfo=UTC)


def eastern_date(moment: datetime) -> date:
    return moment.astimezone(EASTERN).date()


WINDOW_SUBJECT_PREFIX = "session:us_equity_regular_hours_window:"


def window_subject(day: date) -> EvidenceSubject:
    """The adapters' only session subject: the New York date of a bar inside
    the Monday-Friday 09:30-16:00 clock window. It never asserts that the
    exchange was open, that the date was a trading session, or that a
    shortened session was complete; no exchange calendar is approved."""
    return EvidenceSubject(
        subject_type=SubjectType.MARKET_SESSION,
        subject_id=f"{WINDOW_SUBJECT_PREFIX}{day.isoformat()}",
    )


# --- Holdout ---------------------------------------------------------------------------------


def refuse_spy_holdout(*dates: date) -> None:
    """The adapter's own SPY holdout check, applied before any item is built
    and independent of the registry's schema declarations."""
    if any(in_holdout_window(d) for d in dates):
        raise HoldoutRestrictedError()


def require_price_schema(registry: EvidenceRegistry, payload_schema_id: str) -> None:
    """A price-bearing schema must be declared as SPY price content, so the
    core guard applies downstream too. A registry saying otherwise is refused."""
    schema = registry.payload_schema(payload_schema_id)
    if schema is None:
        raise fail("unknown_payload_schema")
    if not schema.spy_price_content:
        raise fail("price_schema_not_declared")


# --- Item construction -------------------------------------------------------------------


def quality(
    kind: EvidenceKind, *, uncertainty: Sequence[str] = (), tier: SourceTier
) -> EvidenceQualityProfile:
    calc = kind is EvidenceKind.DETERMINISTIC_CALCULATION
    return EvidenceQualityProfile(
        data_quality=DataQuality.COMPLETE,
        source_basis=SourceBasis.DERIVED if calc else SourceBasis.DIRECTLY_OBSERVED,
        source_tier=tier,
        calculation_completeness=(
            CalculationCompleteness.COMPLETE if calc else CalculationCompleteness.NOT_APPLICABLE
        ),
        inference_support=InferenceSupport.NOT_APPLICABLE,
        research_status=ResearchStatus.NOT_APPLICABLE,
        graded_strength=GradedStrength.CONFIDENCE_NOT_AVAILABLE,
        uncertainty_codes=sorted(set(uncertainty)),
    )


def build_item(
    context: AdapterContext,
    *,
    producer_id: str,
    kind: EvidenceKind,
    payload_schema_id: str,
    payload: Mapping[str, Any],
    payload_models: Mapping[str, type[BaseModel]],
    subjects: Sequence[EvidenceSubject],
    effective_at: datetime,
    observed_at: datetime,
    temporal_scope: EvidenceTemporalScope,
    references: Sequence[EvidenceSourceReference],
    tier: SourceTier,
    parents: Sequence[str] = (),
    uncertainty: Sequence[str] = (),
    availability: EvidenceAvailability | None = None,
    revision: EvidenceRevision = ORIGINAL_REVISION,
) -> EvidenceItem:
    """Assemble, seal and validate one item. The producer's type, version and
    freshness policy come from the registry; eligibility is the registry's
    derivation, never an adapter decision."""
    registry = context.registry
    producer = registry.producer(producer_id)
    if producer is None:
        raise fail("unknown_producer")
    if ADAPTER_VERSION not in producer.producer_versions:
        raise fail("unknown_producer_version")
    rule = producer.rule_for(kind, payload_schema_id)
    if rule is None:
        raise fail("kind_or_payload_not_allowed")
    if context.generated_at_utc < effective_at:
        raise fail("generated_before_effective")
    try:
        content = EvidenceItemContent(
            evidence_kind=kind,
            subjects=[subjects[0], *sorted(subjects[1:], key=_model_key)],
            effective_at_utc=effective_at,
            temporal_scope=temporal_scope,
            payload_schema_id=payload_schema_id,
            payload=dict(payload),
            provenance=EvidenceProvenance(
                producer_id=producer_id,
                producer_version=ADAPTER_VERSION,
                producer_type=producer.producer_type,
                production_path=ProductionPath.DETERMINISTIC,
                code_commit_sha=context.code_commit_sha,
                code_tree_clean=context.code_tree_clean,
                configuration_identity=context.configuration_identity,
                generated_at_utc=context.generated_at_utc,
                source_observed_at_utc=observed_at,
                source_references=sorted(references, key=_model_key),
                parent_evidence_ids=sorted(set(parents)),
            ),
            freshness_policy_id=rule.freshness_policy_id,
            availability=availability or EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            quality=quality(kind, uncertainty=uncertainty, tier=tier),
            revision=revision,
            machine_decision_eligible=False,
        )
        eligible = derive_machine_decision_eligible(content, registry)
        if eligible:
            fields = {name: getattr(content, name) for name in EvidenceItemContent.model_fields}
            fields["machine_decision_eligible"] = True
            content = EvidenceItemContent(**fields)
        item = seal_item(content)
    except ValidationError:
        raise fail("item_contract_violation") from None
    validate_item(item, registry, payload_models=payload_models)
    # The sealed item must survive its own serialization round trip.
    try:
        EvidenceItem.model_validate_json(item.model_dump_json())
    except ValidationError:
        raise fail("item_contract_violation") from None
    return item


def missing_evidence(
    context: AdapterContext,
    *,
    producer_id: str,
    expected_subject_id: str,
    checked_at: datetime,
    query_sha256: str,
    reason_code: str,
    payload_models: Mapping[str, type[BaseModel]],
    tier: SourceTier,
    expected_component: str | None = None,
) -> EvidenceItem:
    """An explicit absence record. It carries no value field, so missing
    data can never become a zero, a neutral value or a fact."""
    subject_type = subject_type_for_id(expected_subject_id)
    if subject_type is None:
        raise fail("subject_invalid")
    payload = {
        "checked_at_utc": format_utc(checked_at),
        "expected_component": expected_component,
        "expected_producer_id": producer_id,
        "expected_subject_id": expected_subject_id,
        "query_sha256": query_sha256,
        "reason_code": reason_code,
    }
    return build_item(
        context,
        producer_id=producer_id,
        kind=EvidenceKind.MISSING_EVIDENCE,
        payload_schema_id=MISSING_EVIDENCE_PAYLOAD,
        payload=payload,
        payload_models=payload_models,
        subjects=[EvidenceSubject(subject_type=subject_type, subject_id=expected_subject_id)],
        effective_at=checked_at,
        observed_at=checked_at,
        temporal_scope=EvidenceTemporalScope(),
        references=[],
        tier=tier,
        availability=EvidenceAvailability(
            state=AvailabilityState.UNAVAILABLE, reason_code=reason_code
        ),
    )
