"""Adapters for FRED observations and series metadata (registry
``fred_macro_observations`` and ``fred_macro_series_metadata``).

Values are copied as reported, with series ID, observation period, vintage
window, units and frequency preserved. FRED's missing-value marker becomes an
explicit ``missing_evidence`` item, never a zero. Nothing infers economic
meaning or direction, and the observation date is never treated as a release
time.
"""

from __future__ import annotations

from typing import Any

from market_intelligence.evidence.contracts import (
    DuckDbRowReference,
    EvidenceItem,
    EvidenceRevision,
    EvidenceSubject,
    EvidenceTemporalScope,
    ProviderRequestReference,
)
from market_intelligence.evidence.enums import (
    DuckDbTable,
    EndpointClass,
    EvidenceKind,
    ResponseClass,
    RevisionReason,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence_adapters.common import (
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    canonical_decimal,
    fail,
    missing_evidence,
    parse_date,
    parse_model,
    parse_utc,
    require_configuration,
    source_fields,
    start_of_day_utc,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    DISPLAY_TEXT_LIMIT,
    MACRO_OBSERVATION_FACT,
    MACRO_SERIES_METADATA_FACT,
    MacroObservationFact,
    MacroSeriesMetadataFact,
)

OBSERVATIONS_PRODUCER = "fred_macro_observations"
METADATA_PRODUCER = "fred_macro_series_metadata"

OBSERVATIONS_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": OBSERVATIONS_PRODUCER,
        "missing_value": "missing_evidence",
        "payload_schema": MACRO_OBSERVATION_FACT,
        "provider": "fred",
        "units": "lin",
    }
)
METADATA_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": METADATA_PRODUCER,
        "payload_schema": MACRO_SERIES_METADATA_FACT,
        "provider": "fred",
    }
)

_OBSERVATION_FIELDS = (
    "provider",
    "series_id",
    "observation_date",
    "value",
    "is_missing",
    "realtime_start",
    "realtime_end",
    "retrieved_at",
)
_METADATA_FIELDS = (
    "provider",
    "series_id",
    "title",
    "observation_start",
    "observation_end",
    "frequency",
    "frequency_short",
    "units",
    "units_short",
    "seasonal_adjustment",
    "seasonal_adjustment_short",
    "last_updated",
    "retrieved_at_utc",
)


def _series_subject(series_id: Any) -> EvidenceSubject:
    if not isinstance(series_id, str):
        raise fail("source_invalid")
    try:
        return EvidenceSubject(
            subject_type=SubjectType.MACRO_SERIES, subject_id=f"macro_series:fred:{series_id}"
        )
    except ValueError:
        raise fail("series_id_invalid") from None


def _check_reference(
    reference: Any, table: DuckDbTable, endpoint: EndpointClass, key: dict
) -> None:
    if isinstance(reference, DuckDbRowReference):
        stored = {c.column: c.value for c in reference.primary_key}
        if reference.table is not table or stored != key:
            raise fail("source_reference_mismatch")
        return
    if isinstance(reference, ProviderRequestReference):
        if (
            reference.endpoint_class is not endpoint
            or reference.provider != "fred"
            or reference.response_class is not ResponseClass.OK
        ):
            raise fail("source_reference_mismatch")
        return
    raise fail("source_reference_type_not_allowed")


def adapt_fred_observation(
    observation: Any,
    *,
    series_metadata: Any,
    reference: Any,
    context: AdapterContext,
    query_sha256: str,
    supersedes: EvidenceItem | None = None,
) -> EvidenceItem:
    """One validated FRED observation (``fred_macro_data.FredObservation``
    or an object with the same fields).

    - A reported value becomes a ``confirmed_fact`` whose effective time is
      the start of its vintage window and whose observed time is the
      observation date. Frequency and units come from ``series_metadata``.
    - FRED's missing marker becomes ``missing_evidence``
      (``missing_observation``), never a zero.
    - A newer vintage of an observation already adapted is a
      ``source_revision`` of that item (``supersedes``).
    """
    require_configuration(context, OBSERVATIONS_CONFIGURATION)
    values = source_fields(observation, _OBSERVATION_FIELDS)
    metadata = source_fields(
        series_metadata, ("provider", "series_id", "frequency_short", "units_short")
    )
    if values["provider"] != "fred" or metadata["provider"] != "fred":
        raise fail("unknown_provider")
    if metadata["series_id"] != values["series_id"]:
        raise fail("series_metadata_mismatch")
    subject = _series_subject(values["series_id"])
    observed_on = parse_date(values["observation_date"])
    vintage_start = parse_date(values["realtime_start"])
    vintage_end = parse_date(values["realtime_end"])
    retrieved = parse_utc(values["retrieved_at"])
    if vintage_end < vintage_start or vintage_start < observed_on:
        raise fail("vintage_window_invalid")
    if start_of_day_utc(vintage_start) > retrieved:
        raise fail("future_timestamp_detected")
    if not isinstance(values["is_missing"], bool):
        raise fail("source_invalid")
    key = {
        "provider": "fred",
        "series_id": values["series_id"],
        "observation_date": observed_on.isoformat(),
        "realtime_start": vintage_start.isoformat(),
        "realtime_end": vintage_end.isoformat(),
    }
    _check_reference(
        reference, DuckDbTable.MACRO_OBSERVATIONS, EndpointClass.FRED_SERIES_OBSERVATIONS, key
    )
    if values["is_missing"]:
        if values["value"] is not None:
            raise fail("source_invalid")
        return missing_evidence(
            context,
            producer_id=OBSERVATIONS_PRODUCER,
            expected_subject_id=subject.subject_id,
            checked_at=retrieved,
            query_sha256=query_sha256,
            reason_code="missing_observation",
            expected_component="value",
            payload_models=ADAPTER_PAYLOAD_MODELS,
            tier=SourceTier.PRIMARY_OFFICIAL,
        )
    if values["value"] is None:
        raise fail("source_invalid")
    payload = parse_model(
        MacroObservationFact,
        {
            "provider": "fred",
            "series_id": values["series_id"],
            "observation_date": observed_on,
            "realtime_start": vintage_start,
            "realtime_end": vintage_end,
            "value": canonical_decimal(values["value"]),
            "frequency_short": metadata["frequency_short"],
            "units_short": metadata["units_short"],
        },
        "source_invalid",
    )
    revision = _revision(supersedes, values["series_id"], observed_on, vintage_start)
    return build_item(
        context,
        producer_id=OBSERVATIONS_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=MACRO_OBSERVATION_FACT,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[subject],
        effective_at=start_of_day_utc(vintage_start),
        observed_at=start_of_day_utc(observed_on),
        temporal_scope=EvidenceTemporalScope(
            observation_period_start=observed_on,
            source_vintage_start=vintage_start,
            source_vintage_end=vintage_end,
        ),
        references=[reference],
        tier=SourceTier.PRIMARY_OFFICIAL,
        uncertainty=["vintage_revisable"],
        revision=revision,
    )


def _revision(prior: EvidenceItem | None, series_id: str, observed_on, vintage_start):
    if prior is None:
        return EvidenceRevision(revision_number=1, revision_reason=RevisionReason.ORIGINAL)
    payload = prior.payload
    if (
        prior.payload_schema_id != MACRO_OBSERVATION_FACT
        or payload.get("series_id") != series_id
        or payload.get("observation_date") != observed_on.isoformat()
        or parse_date(payload.get("realtime_start")) >= vintage_start
    ):
        raise fail("revision_not_a_later_vintage")
    return EvidenceRevision(
        revision_number=prior.revision.revision_number + 1,
        supersedes_item_id=prior.item_id,
        revision_reason=RevisionReason.SOURCE_REVISION,
    )


def adapt_fred_series_metadata(
    metadata: Any, *, reference: Any, context: AdapterContext
) -> EvidenceItem:
    """Validated FRED series metadata (``fred_macro_data.FredSeriesMetadata``
    or an object with the same fields) as a ``confirmed_fact`` effective at
    its retrieval time, since the stored table keeps no history. The title is
    display-only; ``notes`` and ``popularity`` are not carried."""
    require_configuration(context, METADATA_CONFIGURATION)
    values = source_fields(metadata, _METADATA_FIELDS)
    if values["provider"] != "fred":
        raise fail("unknown_provider")
    subject = _series_subject(values["series_id"])
    last_updated = parse_utc(values["last_updated"])
    retrieved = parse_utc(values["retrieved_at_utc"])
    if last_updated > retrieved:
        raise fail("future_timestamp_detected")
    if parse_date(values["observation_end"]) < parse_date(values["observation_start"]):
        raise fail("source_invalid")
    _check_reference(
        reference,
        DuckDbTable.MACRO_SERIES_METADATA,
        EndpointClass.FRED_SERIES_METADATA,
        {"provider": "fred", "series_id": values["series_id"]},
    )
    title = values["title"]
    if not isinstance(title, str) or len(title) > DISPLAY_TEXT_LIMIT:
        raise fail("source_invalid")
    payload = parse_model(
        MacroSeriesMetadataFact,
        {
            "provider": "fred",
            "series_id": values["series_id"],
            "title": title,
            "frequency": values["frequency"],
            "frequency_short": values["frequency_short"],
            "units": values["units"],
            "units_short": values["units_short"],
            "seasonal_adjustment": values["seasonal_adjustment"],
            "seasonal_adjustment_short": values["seasonal_adjustment_short"],
            "observation_start": parse_date(values["observation_start"]),
            "observation_end": parse_date(values["observation_end"]),
            "last_updated_utc": last_updated,
            "retrieved_at_utc": retrieved,
        },
        "source_invalid",
    )
    return build_item(
        context,
        producer_id=METADATA_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=MACRO_SERIES_METADATA_FACT,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=[subject],
        effective_at=retrieved,
        observed_at=last_updated,
        # The series' reported observation range, and the window over which
        # this metadata was observed current: last update through retrieval.
        temporal_scope=EvidenceTemporalScope(
            observation_period_start=payload.observation_start,
            observation_period_end=payload.observation_end,
            source_vintage_start=last_updated.date(),
            source_vintage_end=retrieved.date(),
        ),
        references=[reference],
        tier=SourceTier.PRIMARY_OFFICIAL,
    )
