"""Synthetic fixtures for the offline producer-adapter tests.

**Test-only.** ``make_test_registry`` builds a clearly labelled synthetic
registry (``synthetic-adapter-test-registry``) in memory; it is not a
production registry file and activates nothing. Source objects are the real
connector dataclasses, built by hand with synthetic values. Sessions are
January 2027, outside the sealed SPY holdout window, except where a test
deliberately builds an in-window value to prove it is refused.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from market_intelligence.data_connectors.alpaca_bars import Bar
from market_intelligence.data_connectors.alpaca_news import NewsItem
from market_intelligence.data_connectors.fred_macro_data import FredObservation, FredSeriesMetadata
from market_intelligence.evidence.contracts import (
    DocumentReference,
    DuckDbRowReference,
    PrimaryKeyComponent,
    ProviderRequestReference,
)
from market_intelligence.evidence.enums import (
    BundlePurpose,
    ConsumerId,
    ConsumerPermission,
    DirectionalAuthority,
    DuckDbTable,
    EndpointClass,
    EvidenceKind,
    Feed,
    ImplementationStatus,
    LocatorKind,
    OffHoursMode,
    ProducerType,
    ProductionPath,
    ResponseClass,
    SubjectType,
)
from market_intelligence.evidence.primitives import format_utc
from market_intelligence.evidence.registry import (
    BundleRequirement,
    ClockPolicy,
    ConsumerGrant,
    EmissionRule,
    EvidenceRegistry,
    FreshnessPolicy,
    PayloadSchemaEntry,
    ProducerEntry,
    SelectionRule,
)
from market_intelligence.evidence_adapters import payloads as P
from market_intelligence.evidence_adapters.common import AdapterContext
from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    RegimeEngineInput,
)

TEST_REGISTRY_LABEL = "synthetic-adapter-test-registry"
COMMIT = "a" * 40
SESSION = date(2027, 1, 12)
OPEN_UTC = datetime(2027, 1, 12, 14, 30, tzinfo=UTC)  # 09:30 America/New_York
RETRIEVED = datetime(2027, 1, 12, 21, 30, tzinfo=UTC)
GENERATED = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)
HOLDOUT_OPEN_UTC = datetime(2026, 10, 14, 13, 30, tzinfo=UTC)
QUERY = "1" * 64
K = EvidenceKind


def _rule(kind, payload, policy, eligible=True) -> EmissionRule:
    return EmissionRule(
        evidence_kind=kind,
        payload_schema_id=payload,
        freshness_policy_id=policy,
        machine_decision_eligible=eligible,
    )


def _producer(pid, ptype, rules, subjects, reasons=()) -> ProducerEntry:
    return ProducerEntry(
        producer_id=pid,
        producer_versions=["1.0.0"],
        producer_type=ptype,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        production_paths=[ProductionPath.DETERMINISTIC],
        directional_authority=DirectionalAuthority.NONE,
        allowed_subject_types=sorted(subjects),
        emission_rules=sorted(rules, key=lambda r: r.model_dump_json()),
        reason_codes=sorted(reasons),
        error_categories=["unexpected_error"],
    )


def _policy(pid, kinds, current=None, stale=None, timeless=False) -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_id=pid,
        applicable_kinds=sorted(kinds),
        observed_timestamp_basis="source_observed_at",
        timeless=timeless,
        current_until_seconds=current,
        stale_after_seconds=stale,
        future_tolerance_seconds=None if timeless else 300,
        off_hours_mode=OffHoursMode.NOT_APPLICABLE if timeless else OffHoursMode.ELAPSED_WALL_CLOCK,
        configuration_version="1",
    )


def make_test_registry(*, price_schemas_declared: bool = True, eligible: bool = True):
    """The synthetic test registry. ``price_schemas_declared=False`` builds a
    deliberately wrong registry to prove the adapters refuse it."""
    S = SubjectType
    missing = "missing_evidence.v1"
    producers = [
        _producer(
            "alpaca_market_bars",
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.MARKET_BAR_FACT, "fp.bars_stored_snapshot.v1", eligible),
                _rule(K.MISSING_EVIDENCE, missing, "fp.bars_stored_snapshot.v1"),
            ],
            [S.INSTRUMENT, S.MARKET_SESSION],
            reasons=("bars_missing", "missing_data"),
        ),
        _producer(
            "alpaca_market_snapshot",
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, P.MARKET_SNAPSHOT_FACT, "fp.snapshot_live.v0", eligible)],
            [S.INSTRUMENT],
            reasons=("missing_data",),
        ),
        _producer(
            "alpaca_news",
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.NEWS_PUBLICATION_FACT, "fp.news_snapshot.v1", eligible),
                _rule(K.MISSING_EVIDENCE, missing, "fp.news_snapshot.v1"),
            ],
            [S.INSTRUMENT, S.NEWS_ITEM],
            reasons=("no_articles_returned",),
        ),
        _producer(
            "fred_macro_observations",
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.MACRO_OBSERVATION_FACT, "fp.macro_monthly.v1", eligible),
                _rule(K.MISSING_EVIDENCE, missing, "fp.macro_monthly.v1"),
            ],
            [S.MACRO_SERIES],
            reasons=("missing_observation",),
        ),
        _producer(
            "fred_macro_series_metadata",
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(
                    K.CONFIRMED_FACT,
                    P.MACRO_SERIES_METADATA_FACT,
                    "fp.reference_metadata.v1",
                    eligible,
                )
            ],
            [S.MACRO_SERIES],
        ),
        _producer(
            "system_clock_health",
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, "clock_health_fact.v1", "fp.clock.v1", eligible)],
            [S.SYSTEM_COMPONENT],
        ),
        _producer(
            "spy_regime_engine",
            ProducerType.DETERMINISTIC_ENGINE,
            [
                _rule(
                    K.DETERMINISTIC_CALCULATION,
                    P.SPY_REGIME_FEATURES,
                    "fp.bars_stored_snapshot.v1",
                    eligible,
                ),
                _rule(
                    K.DETERMINISTIC_CALCULATION,
                    P.SPY_REGIME_CLASSIFICATION,
                    "fp.bars_stored_snapshot.v1",
                    eligible,
                ),
            ],
            [S.INSTRUMENT, S.MARKET_SESSION],
        ),
    ]
    price = price_schemas_declared
    schemas = [
        (P.MARKET_BAR_FACT, price),
        (P.MARKET_SNAPSHOT_FACT, price),
        (P.NEWS_PUBLICATION_FACT, False),
        (P.MACRO_OBSERVATION_FACT, False),
        (P.MACRO_SERIES_METADATA_FACT, False),
        (P.SPY_REGIME_FEATURES, price),
        (P.SPY_REGIME_CLASSIFICATION, price),
        ("clock_health_fact.v1", False),
        (missing, False),
    ]
    facts = [K.CONFIRMED_FACT, K.DETERMINISTIC_CALCULATION, K.MISSING_EVIDENCE]
    return EvidenceRegistry(
        registry_label=TEST_REGISTRY_LABEL,
        producers=sorted(producers, key=lambda p: p.producer_id),
        payload_schemas=sorted(
            [PayloadSchemaEntry(payload_schema_id=s, spy_price_content=p) for s, p in schemas],
            key=lambda s: s.payload_schema_id,
        ),
        freshness_policies=sorted(
            [
                _policy("fp.bars_stored_snapshot.v1", facts, 259_200, 259_200),
                _policy("fp.snapshot_live.v0", [K.CONFIRMED_FACT]),
                _policy("fp.news_snapshot.v1", facts, 604_800, 604_800),
                _policy("fp.macro_monthly.v1", facts, 7_776_000, 7_776_000),
                _policy("fp.reference_metadata.v1", [K.CONFIRMED_FACT]),
                _policy("fp.clock.v1", [K.CONFIRMED_FACT], 60, 60),
            ],
            key=lambda p: p.policy_id,
        ),
        clock_policy=ClockPolicy(
            policy_id="clock.synthetic.v1",
            clock_producer_id="system_clock_health",
            max_abs_offset_ms=1000,
            max_sync_age_seconds=3600,
            max_measurement_age_seconds=600,
        ),
        consumer_grants=[
            ConsumerGrant(
                consumer_id=ConsumerId.DASHBOARD,
                purposes=[BundlePurpose.LIVE_MARKET_STATE],
                permissions=sorted(
                    [
                        ConsumerPermission.READ_CALCULATIONS,
                        ConsumerPermission.READ_FACTS,
                    ]
                ),
                machine_decision_mode_allowed=False,
            )
        ],
        selection_rules=[
            SelectionRule(
                selection_rule_id="sel.adapter.test.v1",
                purpose=BundlePurpose.LIVE_MARKET_STATE,
                requirements=[
                    BundleRequirement(
                        requirement_id="req.regime.features",
                        producer_id="spy_regime_engine",
                        producer_versions=["1.0.0"],
                        evidence_kind=K.DETERMINISTIC_CALCULATION,
                        payload_schema_id=P.SPY_REGIME_FEATURES,
                        primary_subject_id="instrument:us_equity:SPY",
                        configuration_identities=[_regime_configuration()],
                    )
                ],
                max_window_seconds=86_400,
            )
        ],
        uncertainty_codes=sorted(
            [
                "headline_only",
                "iex_partial_volume",
                "provider_reported_unverified",
                "vintage_revisable",
            ]
        ),
        component_tokens=["latest_quote"],
    )


def _regime_configuration() -> str:
    from market_intelligence.evidence_adapters.market_calculations import REGIME_CONFIGURATION

    return REGIME_CONFIGURATION


def context(configuration: str, registry: EvidenceRegistry | None = None, **overrides: Any):
    fields: dict[str, Any] = dict(
        registry=registry or make_test_registry(),
        code_commit_sha=COMMIT,
        code_tree_clean=True,
        configuration_identity=configuration,
        generated_at_utc=GENERATED,
    )
    fields.update(overrides)
    return AdapterContext(**fields)


# --- Source objects (the real connector dataclasses, synthetic values) -----------------------


def bar(start: datetime = OPEN_UTC, *, close: str = "500.2", **overrides: Any) -> Bar:
    fields: dict[str, Any] = dict(
        provider="alpaca",
        symbol="SPY",
        timeframe="5Min",
        feed="iex",
        adjustment="raw",
        currency="USD",
        timestamp=start.isoformat().replace("+00:00", "Z"),
        open=Decimal("500.0"),
        high=Decimal("500.5"),
        low=Decimal("499.8"),
        close=Decimal(close),
        volume=12_000,
        trade_count=150,
        vwap=Decimal("500.15"),
        retrieved_at=RETRIEVED.isoformat().replace("+00:00", "Z"),
    )
    fields.update(overrides)
    return Bar(**fields)


def session_bars(count: int = 6, start: datetime = OPEN_UTC) -> list[Bar]:
    closes = ["500.2", "500.6", "500.9", "500.7", "501.1", "501.4", "501.2", "501.6"]
    bars = []
    for i in range(count):
        c = Decimal(closes[i % len(closes)])
        bars.append(
            bar(
                start + timedelta(minutes=5 * i),
                close=str(c),
                high=c + Decimal("0.4"),
                low=Decimal("499.5"),
                open=Decimal("500.0"),
            )
        )
    return bars


def bar_reference(source: Bar) -> DuckDbRowReference:
    values = [
        source.provider,
        source.symbol,
        source.timeframe,
        source.feed,
        source.adjustment,
        source.currency,
        source.timestamp,
    ]
    columns = ["provider", "symbol", "timeframe", "feed", "adjustment", "currency", "bar_timestamp"]
    return DuckDbRowReference(
        reference_type="duckdb_row",
        table=DuckDbTable.MARKET_BARS,
        primary_key=[PrimaryKeyComponent(column=c, value=v) for c, v in zip(columns, values)],
        db_schema_version="0009",
        ingestion_run_id="run-synthetic-1",
    )


def provider_reference(
    endpoint: EndpointClass, *, feed: str | None = None, at: datetime = RETRIEVED, provider="alpaca"
) -> ProviderRequestReference:
    return ProviderRequestReference(
        reference_type="provider_request",
        provider=provider,
        endpoint_class=endpoint,
        feed=None if feed is None else Feed(feed),
        request_params_sha256="2" * 64,
        requested_at_utc=at,
        response_class=ResponseClass.OK,
    )


def snapshot(*, trade_t: str = "2027-01-12T20:59:58.123456789Z", quote: bool = True) -> dict:
    body: dict[str, Any] = {
        "latestTrade": {"t": trade_t, "p": 501.37, "s": 100, "x": "V", "c": ["@"], "i": 1},
        "minuteBar": {"t": "2027-01-12T20:59:00Z", "o": 501.3, "c": 501.4},
    }
    if quote:
        body["latestQuote"] = {
            "t": "2027-01-12T20:59:59.5Z",
            "bp": 501.36,
            "bs": 3,
            "ap": 501.38,
            "as": 5,
        }
    return body


def news_item(**overrides: Any) -> NewsItem:
    fields: dict[str, Any] = dict(
        provider="alpaca",
        provider_article_id="40001234",
        headline="Synthetic headline about a scheduled index rebalance",
        source="synthetic_wire",
        url="https://example.com/synthetic/article",
        summary="Synthetic summary text reported by the provider.",
        created_at="2027-01-12T15:00:00Z",
        updated_at="2027-01-12T15:10:00Z",
        related_symbols=("SPY", "QQQ"),
        retrieved_at="2027-01-12T16:00:00Z",
    )
    fields.update(overrides)
    return NewsItem(**fields)


def news_reference(article: NewsItem) -> DuckDbRowReference:
    return DuckDbRowReference(
        reference_type="duckdb_row",
        table=DuckDbTable.NEWS_ARTICLES,
        primary_key=[
            PrimaryKeyComponent(column="provider", value=article.provider),
            PrimaryKeyComponent(column="provider_article_id", value=article.provider_article_id),
        ],
        db_schema_version="0009",
        ingestion_run_id="run-synthetic-news",
    )


def observation(**overrides: Any) -> FredObservation:
    fields: dict[str, Any] = dict(
        provider="fred",
        series_id="CPIAUCSL",
        observation_date="2026-12-01",
        value=Decimal("321.456"),
        is_missing=False,
        realtime_start="2027-01-13",
        realtime_end="9999-12-31",
        retrieved_at="2027-01-14T12:00:00Z",
    )
    fields.update(overrides)
    return FredObservation(**fields)


def observation_reference(obs: FredObservation) -> DuckDbRowReference:
    return DuckDbRowReference(
        reference_type="duckdb_row",
        table=DuckDbTable.MACRO_OBSERVATIONS,
        primary_key=[
            PrimaryKeyComponent(column="provider", value="fred"),
            PrimaryKeyComponent(column="series_id", value=obs.series_id),
            PrimaryKeyComponent(column="observation_date", value=obs.observation_date),
            PrimaryKeyComponent(column="realtime_start", value=obs.realtime_start),
            PrimaryKeyComponent(column="realtime_end", value=obs.realtime_end),
        ],
        db_schema_version="0009",
        ingestion_run_id="run-synthetic-fred",
    )


def series_metadata(**overrides: Any) -> FredSeriesMetadata:
    fields: dict[str, Any] = dict(
        provider="fred",
        series_id="CPIAUCSL",
        title="Synthetic Consumer Price Index Title",
        observation_start="1947-01-01",
        observation_end="2026-12-01",
        frequency="Monthly",
        frequency_short="M",
        units="Index 1982-1984=100",
        units_short="Index 1982-1984=100",
        seasonal_adjustment="Seasonally Adjusted",
        seasonal_adjustment_short="SA",
        last_updated="2027-01-13T13:01:02Z",
        popularity=90,
        notes="Synthetic notes that are not carried.",
        retrieved_at_utc="2027-01-14T12:00:00Z",
    )
    fields.update(overrides)
    return FredSeriesMetadata(**fields)


def metadata_reference(series_id: str = "CPIAUCSL") -> DuckDbRowReference:
    return DuckDbRowReference(
        reference_type="duckdb_row",
        table=DuckDbTable.MACRO_SERIES_METADATA,
        primary_key=[
            PrimaryKeyComponent(column="provider", value="fred"),
            PrimaryKeyComponent(column="series_id", value=series_id),
        ],
        db_schema_version="0009",
        ingestion_run_id=None,
    )


def clock_reference() -> DocumentReference:
    return DocumentReference(
        reference_type="document",
        locator_kind=LocatorKind.REPO_DOCUMENT,
        document_path="docs/EVIDENCE_REGISTRY.md",
        document_commit_sha=COMMIT,
        section_token="clock",
    )


def engine_input(bars: list[Bar], **overrides: Any) -> RegimeEngineInput:
    fields: dict[str, Any] = dict(
        session_date=SESSION,
        bars=[
            IntradayBar(
                timestamp=datetime.fromisoformat(b.timestamp.replace("Z", "+00:00")),
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
            )
            for b in bars
        ],
        catalyst_state=CatalystState.UNKNOWN,
        breadth_state=BreadthState.UNAVAILABLE,
    )
    fields.update(overrides)
    return RegimeEngineInput(**fields)


def utc_text(moment: datetime) -> str:
    return format_utc(moment)
