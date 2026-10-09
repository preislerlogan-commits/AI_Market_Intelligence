"""Adapter for Alpaca news publication metadata (registry ``alpaca_news``).

Only publication metadata is a confirmed fact: provider, article ID,
publisher, publication and update times, and related symbols. The headline
and summary are display-only provider content, explicitly
``provider_reported_unverified``; the article's claim is never a fact.
No sentiment, impact, direction or probability is emitted, and the article
URL is never carried (SOURCE_POLICY).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from market_intelligence.evidence.contracts import (
    DuckDbRowReference,
    EvidenceItem,
    EvidenceSubject,
    EvidenceTemporalScope,
    ProviderRequestReference,
)
from market_intelligence.evidence.enums import (
    DuckDbTable,
    EndpointClass,
    EvidenceKind,
    ResponseClass,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence_adapters.common import (
    SPY_SUBJECT_ID,
    SPY_SYMBOL,
    AdapterContext,
    adapter_configuration_identity,
    build_item,
    fail,
    missing_evidence,
    parse_model,
    parse_utc,
    require_configuration,
    source_fields,
)
from market_intelligence.evidence_adapters.payloads import (
    ADAPTER_PAYLOAD_MODELS,
    DISPLAY_TEXT_LIMIT,
    NEWS_PUBLICATION_FACT,
    NewsPublicationFact,
)

NEWS_PRODUCER = "alpaca_news"
FUTURE_TOLERANCE = timedelta(minutes=5)
NEWS_CONFIGURATION = adapter_configuration_identity(
    {
        "adapter": NEWS_PRODUCER,
        "content_status": "provider_reported_unverified",
        "display_text_limit": DISPLAY_TEXT_LIMIT,
        "future_tolerance_seconds": int(FUTURE_TOLERANCE.total_seconds()),
        "payload_schema": NEWS_PUBLICATION_FACT,
        "url_carried": False,
    }
)
_NEWS_FIELDS = (
    "provider",
    "provider_article_id",
    "headline",
    "source",
    "summary",
    "created_at",
    "updated_at",
    "related_symbols",
    "retrieved_at",
)


def _display_only(text: Any, *, required: bool) -> tuple[str | None, bool]:
    """Provider text kept for display only, cut to the display limit with an
    explicit truncation flag. Never parsed or interpreted."""
    if text is None and not required:
        return None, False
    if not isinstance(text, str) or not text.strip():
        raise fail("source_invalid")
    text = text.strip()
    if len(text) <= DISPLAY_TEXT_LIMIT:
        return text, False
    return text[: DISPLAY_TEXT_LIMIT - 3].rstrip() + "...", True


def adapt_news_publication(
    article: Any, *, reference: Any, context: AdapterContext
) -> EvidenceItem:
    """One validated Alpaca article (``alpaca_news.NewsItem`` or an object
    with the same fields) as a publication-metadata ``confirmed_fact``,
    effective at its publication time."""
    require_configuration(context, NEWS_CONFIGURATION)
    values = source_fields(article, _NEWS_FIELDS)
    if values["provider"] != "alpaca":
        raise fail("unknown_provider")
    if values["created_at"] is None:
        raise fail("publication_time_missing")
    created = parse_utc(values["created_at"])
    updated = None if values["updated_at"] is None else parse_utc(values["updated_at"])
    retrieved = parse_utc(values["retrieved_at"])
    if max(created, updated or created) > retrieved + FUTURE_TOLERANCE:
        raise fail("future_timestamp_detected")
    article_id = values["provider_article_id"]
    try:
        subject = EvidenceSubject(
            subject_type=SubjectType.NEWS_ITEM, subject_id=f"news_item:alpaca:{article_id}"
        )
    except ValueError:
        raise fail("source_invalid") from None
    if isinstance(reference, DuckDbRowReference):
        key = {c.column: c.value for c in reference.primary_key}
        if reference.table is not DuckDbTable.NEWS_ARTICLES or key != {
            "provider": "alpaca",
            "provider_article_id": article_id,
        }:
            raise fail("source_reference_mismatch")
    elif isinstance(reference, ProviderRequestReference):
        if (
            reference.endpoint_class is not EndpointClass.ALPACA_NEWS
            or reference.provider != "alpaca"
            or reference.response_class is not ResponseClass.OK
        ):
            raise fail("source_reference_mismatch")
    else:
        raise fail("source_reference_type_not_allowed")
    symbols = values["related_symbols"]
    if not isinstance(symbols, tuple | list) or not all(isinstance(s, str) for s in symbols):
        raise fail("source_invalid")
    headline, headline_cut = _display_only(values["headline"], required=True)
    summary, summary_cut = _display_only(values["summary"], required=False)
    payload = parse_model(
        NewsPublicationFact,
        {
            "provider": "alpaca",
            "provider_article_id": article_id,
            "publisher": values["source"],
            "created_at_utc": created,
            "updated_at_utc": updated,
            "related_symbols": sorted(set(symbols)),
            "content_status": "provider_reported_unverified",
            "headline": headline,
            "headline_truncated": headline_cut,
            "summary": summary,
            "summary_truncated": summary_cut,
        },
        "source_invalid",
    )
    subjects = [subject]
    if SPY_SYMBOL in symbols:
        subjects.append(
            EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=SPY_SUBJECT_ID)
        )
    uncertainty = ["provider_reported_unverified"] + ([] if summary else ["headline_only"])
    return build_item(
        context,
        producer_id=NEWS_PRODUCER,
        kind=EvidenceKind.CONFIRMED_FACT,
        payload_schema_id=NEWS_PUBLICATION_FACT,
        payload=payload.model_dump(mode="json"),
        payload_models=ADAPTER_PAYLOAD_MODELS,
        subjects=subjects,
        effective_at=created,
        observed_at=created,
        temporal_scope=EvidenceTemporalScope(),
        references=[reference],
        tier=SourceTier.LICENSED_MARKET_DATA,
        uncertainty=uncertainty,
    )


def missing_news(
    *, checked_at: datetime, query_sha256: str, context: AdapterContext
) -> EvidenceItem:
    """No article was returned for a requested window: explicit
    ``missing_evidence`` with the existing ``no_articles_returned`` reason."""
    require_configuration(context, NEWS_CONFIGURATION)
    return missing_evidence(
        context,
        producer_id=NEWS_PRODUCER,
        expected_subject_id=SPY_SUBJECT_ID,
        checked_at=checked_at,
        query_sha256=query_sha256,
        reason_code="no_articles_returned",
        payload_models=ADAPTER_PAYLOAD_MODELS,
        tier=SourceTier.LICENSED_MARKET_DATA,
    )
