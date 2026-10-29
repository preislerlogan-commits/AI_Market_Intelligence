"""The briefing's own temporary evidence registry.

It admits only what a macro-and-news briefing needs: a clock-health fact
(freshness is unknown without one), FRED observations and
series metadata, Alpaca news publication metadata (and their missing-evidence
notices), and the Macro and News analysts' outputs (non-eligible inference
for completed claims, non-eligible missing evidence for pre-model
abstentions). No bar, SPY price, Market Evidence Agent, regime, selector,
setup-evaluation or research producer exists here, so none can be bundled.

Consumers: the dashboard and the offline assistant may read the
``premarket_briefing`` purpose, read-only, with no machine-decision mode. The
registry lives only inside the briefing's temporary store.
"""

from __future__ import annotations

from market_intelligence.dashboard import fixtures as dashboard_fixtures
from market_intelligence.evidence.enums import (
    BundlePurpose,
    ConsumerId,
    EvidenceKind,
    ProducerType,
    ProductionPath,
    SubjectType,
)
from market_intelligence.evidence.registry import (
    ClockPolicy,
    ConsumerGrant,
    EvidenceRegistry,
    PayloadSchemaEntry,
    SelectionRule,
)
from market_intelligence.evidence_adapters import agents as agent_adapters
from market_intelligence.evidence_adapters import clock as clock_adapter
from market_intelligence.evidence_adapters import fred as fred_adapter
from market_intelligence.evidence_adapters import news as news_adapter
from market_intelligence.evidence_adapters import payloads as P
from market_intelligence.integration.registry import _READS, _policy, _producer, _rule

SYNTHETIC_LABEL = "synthetic-macro-news-briefing"  # offline runs with fakes
LIVE_LABEL = "temporary-macro-news-briefing"  # a future authorized live run
BRIEFING_PRODUCERS = frozenset(
    {
        clock_adapter.CLOCK_PRODUCER,
        fred_adapter.OBSERVATIONS_PRODUCER,
        fred_adapter.METADATA_PRODUCER,
        news_adapter.NEWS_PRODUCER,
        agent_adapters.MACRO_PRODUCER,
        agent_adapters.NEWS_PRODUCER,
    }
)
_MISSING = "missing_evidence.v1"
K = EvidenceKind
S = SubjectType
# Freshness windows (seconds). Monthly macro data is observed weeks after its
# period; news is current for a week.
MACRO_CURRENT, MACRO_STALE = 120 * 86_400, 180 * 86_400
NEWS_CURRENT, NEWS_STALE = 7 * 86_400, 14 * 86_400


def make_briefing_registry(label: str = SYNTHETIC_LABEL) -> EvidenceRegistry:
    """The briefing registry. ``label`` is synthetic for offline runs and
    ``temporary-...`` for a future authorized live run; nothing else differs."""
    if label not in (SYNTHETIC_LABEL, LIVE_LABEL):
        raise ValueError("unknown briefing registry label")
    agent_paths = [ProductionPath.DETERMINISTIC, ProductionPath.MODEL_GENERATED]
    producers = [
        _producer(
            clock_adapter.CLOCK_PRODUCER,
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, "clock_health_fact.v1", "fp.briefing_clock.v1", False)],
            [S.SYSTEM_COMPONENT],
        ),
        _producer(
            fred_adapter.OBSERVATIONS_PRODUCER,
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.MACRO_OBSERVATION_FACT, "fp.briefing_macro.v1", False),
                _rule(K.MISSING_EVIDENCE, _MISSING, "fp.briefing_macro.v1", False),
            ],
            [S.MACRO_SERIES],
            reasons=("missing_observation",),
        ),
        _producer(
            fred_adapter.METADATA_PRODUCER,
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, P.MACRO_SERIES_METADATA_FACT, "fp.briefing_macro.v1", False)],
            [S.MACRO_SERIES],
        ),
        _producer(
            news_adapter.NEWS_PRODUCER,
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.NEWS_PUBLICATION_FACT, "fp.briefing_news.v1", False),
                _rule(K.MISSING_EVIDENCE, _MISSING, "fp.briefing_news.v1", False),
            ],
            [S.INSTRUMENT, S.NEWS_ITEM],
            reasons=("no_articles_returned",),
        ),
        *(
            _producer(
                agent,
                ProducerType.MODEL_AGENT,
                [
                    _rule(K.CURRENT_INFERENCE, claim_schema, "fp.briefing_agent.v1", False),
                    _rule(K.MISSING_EVIDENCE, _MISSING, "fp.briefing_agent.v1", False),
                ],
                [S.INSTRUMENT, S.MACRO_SERIES],
                paths=agent_paths,
                reasons=dashboard_fixtures.AGENT_ABSTENTION_REASONS[agent],
            )
            for agent, claim_schema in (
                (agent_adapters.MACRO_PRODUCER, P.MACRO_CLAIM),
                (agent_adapters.NEWS_PRODUCER, P.NEWS_EVENT_CLAIM),
            )
        ),
    ]
    schemas = [
        ("clock_health_fact.v1", False),
        (P.MACRO_OBSERVATION_FACT, False),
        (P.MACRO_SERIES_METADATA_FACT, False),
        (P.NEWS_PUBLICATION_FACT, False),
        (P.MACRO_CLAIM, False),
        (P.NEWS_EVENT_CLAIM, False),
        (_MISSING, False),
    ]
    briefing = [K.CONFIRMED_FACT, K.MISSING_EVIDENCE]
    return EvidenceRegistry(
        registry_label=label,
        producers=sorted(producers, key=lambda p: p.producer_id),
        payload_schemas=sorted(
            [PayloadSchemaEntry(payload_schema_id=s, spy_price_content=p) for s, p in schemas],
            key=lambda s: s.payload_schema_id,
        ),
        freshness_policies=sorted(
            [
                _policy("fp.briefing_clock.v1", [K.CONFIRMED_FACT], 300, 300),
                _policy("fp.briefing_macro.v1", briefing, MACRO_CURRENT, MACRO_STALE),
                _policy("fp.briefing_news.v1", briefing, NEWS_CURRENT, NEWS_STALE),
                _policy(
                    "fp.briefing_agent.v1",
                    [K.CURRENT_INFERENCE, K.MISSING_EVIDENCE],
                    MACRO_CURRENT,
                    MACRO_STALE,
                ),
            ],
            key=lambda p: p.policy_id,
        ),
        clock_policy=ClockPolicy(
            policy_id="clock.briefing.v1",
            clock_producer_id=clock_adapter.CLOCK_PRODUCER,
            max_abs_offset_ms=1000,
            max_sync_age_seconds=3600,
            max_measurement_age_seconds=600,
        ),
        consumer_grants=[
            # Read-only, briefing purpose only, no machine-decision mode. The
            # assistant still reads synthetic registries only.
            ConsumerGrant(
                consumer_id=ConsumerId.ASSISTANT,
                purposes=[BundlePurpose.PREMARKET_BRIEFING],
                permissions=_READS,
                machine_decision_mode_allowed=False,
            ),
            ConsumerGrant(
                consumer_id=ConsumerId.DASHBOARD,
                purposes=[BundlePurpose.PREMARKET_BRIEFING],
                permissions=_READS,
                machine_decision_mode_allowed=False,
            ),
        ],
        selection_rules=[
            SelectionRule(
                selection_rule_id="sel.briefing.v1",
                purpose=BundlePurpose.PREMARKET_BRIEFING,
                requirements=[],
                max_window_seconds=400 * 86_400,
            )
        ],
        uncertainty_codes=["headline_only", "provider_reported_unverified", "vintage_revisable"],
        component_tokens=["value"],
    )
