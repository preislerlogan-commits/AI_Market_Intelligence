"""Adapter for clock-health facts (registry ``system_clock_health``).

Translates an **injected** clock reading into the core
``clock_health_fact.v1`` payload. The adapter never reads a clock, a time
server or the network: the caller measures, then passes the reading in.
Health thresholds stay in the registry's clock policy; nothing is judged here.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, Field, model_validator

from market_intelligence.evidence.contracts import (
    DocumentReference,
    EvidenceItem,
    EvidenceSubject,
    EvidenceTemporalScope,
)
from market_intelligence.evidence.enums import EvidenceKind, SourceTier, SubjectType
from market_intelligence.evidence.payloads import CLOCK_HEALTH_PAYLOAD, ClockHealthFactPayload
from market_intelligence.evidence.primitives import STRICT_FROZEN, UtcTimestamp
from market_intelligence.evidence_adapters.common import (
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    fail,
    parse_model,
    require_configuration,
)
from market_intelligence.evidence_adapters.payloads import ADAPTER_PAYLOAD_MODELS

CLOCK_PRODUCER = "system_clock_health"
CLOCK_SUBJECT_ID = "system:clock"
CLOCK_CONFIGURATION = adapter_configuration_identity(
    {"adapter": CLOCK_PRODUCER, "payload_schema": CLOCK_HEALTH_PAYLOAD}
)


class ClockHealthReading(BaseModel):
    """One measurement taken by the caller, never by this adapter."""

    model_config = STRICT_FROZEN

    measured_at_utc: UtcTimestamp
    offset_ms: Annotated[int, Field(ge=-86_400_000, le=86_400_000)]
    last_sync_at_utc: UtcTimestamp

    @model_validator(mode="after")
    def _check(self) -> ClockHealthReading:
        if self.last_sync_at_utc > self.measured_at_utc:
            raise ValueError("a sync cannot follow the measurement")
        return self


def adapt_clock_health(
    reading: Any, *, reference: DocumentReference, context: AdapterContext
) -> EvidenceItem:
    """An injected ``ClockHealthReading`` as a ``confirmed_fact`` effective at
    the measurement time. ``reference`` documents the measurement procedure."""
    require_configuration(context, CLOCK_CONFIGURATION)
    if not isinstance(reading, ClockHealthReading):
        raise fail("source_invalid")
    if not isinstance(reference, DocumentReference):
        raise fail("source_reference_type_not_allowed")
    payload = parse_model(
        ClockHealthFactPayload,
        {"offset_ms": reading.offset_ms, "last_sync_at_utc": reading.last_sync_at_utc},
        "source_invalid",
    )
    return build_item(
        context,
        producer_id=CLOCK_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=CLOCK_HEALTH_PAYLOAD,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[
            EvidenceSubject(subject_type=SubjectType.SYSTEM_COMPONENT, subject_id=CLOCK_SUBJECT_ID)
        ],
        effective_at=reading.measured_at_utc,
        observed_at=reading.measured_at_utc,
        temporal_scope=EvidenceTemporalScope(),
        references=[reference],
        tier=SourceTier.NOT_APPLICABLE,
    )
