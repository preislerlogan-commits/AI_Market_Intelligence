"""The clearly labelled synthetic registries of the offline vertical slice.

**Synthetic, temporary and never production.** These registries exist only
inside a slice workspace's temporary store. They are not registry files in
the repository, and nothing here registers or activates anything outside a
temporary store.

- The evidence registry (``synthetic-offline-slice-registry``) admits the
  offline adapters' producers plus the dashboard's existing synthetic
  producers (selector result, research result, labelled inference, conflict
  detector and setup evaluator).
- Its single machine-decision grant (a synthetic ``setup_ranker``) exists only
  because the store's frozen conflict-severity derivation counts a conflict as
  critical only when it involves an item required by a machine-decision
  purpose. Nothing in the slice uses machine-decision mode.
  **Review decision (PROJECT_STATE item 66):** this grant is accepted only as
  an isolated test fixture for exercising the frozen critical-conflict rule.
  It grants no production authority and must never appear in a production
  registry.
- The setup-definition registry is the structurally empty production format.
  The one synthetic-only definition reaches the store through its test-only
  offline hook, and only in unauthenticated offline mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel

from market_intelligence.dashboard import fixtures as dashboard_fixtures
from market_intelligence.evidence.enums import (
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
from market_intelligence.evidence.selector_boundary import (
    CONTRACT_SELECTOR_PAYLOAD_ID,
    CONTRACT_SELECTOR_PRODUCER_ID,
)
from market_intelligence.evidence_adapters import payloads as P
from market_intelligence.evidence_adapters.market_calculations import REGIME_CONFIGURATION
from market_intelligence.evidence_store.registry_files import (
    SetupDefinitionRegistry,
    compute_setup_registry_version_id,
)
from market_intelligence.setup_cards.definitions import SETUP_EVALUATION_PAYLOAD

REGISTRY_LABEL = "synthetic-offline-slice-registry"
SETUP_REGISTRY = SetupDefinitionRegistry(
    registry_label="synthetic-offline-slice-setup-definitions-empty", definitions=[]
)
SETUP_REGISTRY_ID = compute_setup_registry_version_id(SETUP_REGISTRY)
SYNTHETIC_DEFINITION = dashboard_fixtures.SYNTHETIC_DEFINITION
SYNTHETIC_DEFINITIONS = MappingProxyType({SETUP_REGISTRY_ID: (SYNTHETIC_DEFINITION,)})

BARS = "alpaca_market_bars"
REGIME = "spy_regime_engine"
CLOCK = "system_clock_health"
FRED = "fred_macro_observations"
RESEARCH = dashboard_fixtures.RESEARCH
AGENT = dashboard_fixtures.AGENT
DETECTOR = dashboard_fixtures.DETECTOR
EVALUATOR = dashboard_fixtures.EVALUATOR
SPY = "instrument:us_equity:SPY"

# The adapters' payload schemas plus the dashboard's synthetic ones.
PAYLOAD_MODELS: Mapping[str, type[BaseModel]] = MappingProxyType(
    {**dashboard_fixtures.PAYLOAD_MODELS, **P.ADAPTER_PAYLOAD_MODELS}
)

K = EvidenceKind
S = SubjectType
_READS = sorted(
    [
        ConsumerPermission.READ_CALCULATIONS,
        ConsumerPermission.READ_CONFLICTS,
        ConsumerPermission.READ_FACTS,
        ConsumerPermission.READ_INFERENCES,
        ConsumerPermission.READ_RESEARCH,
    ]
)


def _rule(kind, schema, policy, eligible=True) -> EmissionRule:
    return EmissionRule(
        evidence_kind=kind,
        payload_schema_id=schema,
        freshness_policy_id=policy,
        machine_decision_eligible=eligible,
    )


def _producer(pid, ptype, rules, subjects, *, paths=None, reasons=()) -> ProducerEntry:
    return ProducerEntry(
        producer_id=pid,
        producer_versions=["1.0.0"],
        producer_type=ptype,
        implementation_status=ImplementationStatus.IMPLEMENTED,
        production_paths=paths or [ProductionPath.DETERMINISTIC],
        directional_authority=DirectionalAuthority.NONE,
        allowed_subject_types=sorted(subjects),
        emission_rules=sorted(rules, key=lambda r: r.model_dump_json()),
        reason_codes=sorted(reasons),
        error_categories=["unexpected_error"],
    )


def _policy(pid, kinds, current=None, stale=None, *, timeless=False) -> FreshnessPolicy:
    return FreshnessPolicy(
        policy_id=pid,
        applicable_kinds=sorted(kinds),
        observed_timestamp_basis="source_observed_at",
        timeless=timeless,
        current_until_seconds=current,
        stale_after_seconds=stale,
        future_tolerance_seconds=None if timeless else 5,
        off_hours_mode=OffHoursMode.NOT_APPLICABLE if timeless else OffHoursMode.ELAPSED_WALL_CLOCK,
        configuration_version="1",
    )


def _requirement(rid, producer, kind, schema, subject=SPY, configurations=("cfg_none",)):
    return BundleRequirement(
        requirement_id=rid,
        producer_id=producer,
        producer_versions=["1.0.0"],
        evidence_kind=kind,
        payload_schema_id=schema,
        primary_subject_id=subject,
        configuration_identities=sorted(configurations),
    )


def make_registry() -> EvidenceRegistry:
    from market_intelligence.evidence_adapters.market_data import BARS_CONFIGURATION

    missing = "missing_evidence.v1"
    bars_req = _requirement(
        "req.bars.spy",
        BARS,
        K.CONFIRMED_FACT,
        P.MARKET_BAR_FACT,
        configurations=(BARS_CONFIGURATION,),
    )
    regime_req = _requirement(
        "req.regime.features",
        REGIME,
        K.DETERMINISTIC_CALCULATION,
        P.SPY_REGIME_FEATURES,
        configurations=(REGIME_CONFIGURATION,),
    )
    research_req = _requirement(
        "req.research.vwap",
        RESEARCH,
        K.HISTORICAL_RESEARCH_RESULT,
        "synthetic_research.v1",
        subject="research:spy_vwap_reversion",
    )
    producers = [
        _producer(
            BARS,
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.MARKET_BAR_FACT, "fp.bars_synthetic.v1"),
                _rule(K.MISSING_EVIDENCE, missing, "fp.bars_synthetic.v1"),
            ],
            [S.INSTRUMENT, S.MARKET_SESSION],
            reasons=("bars_missing",),
        ),
        _producer(
            REGIME,
            ProducerType.DETERMINISTIC_ENGINE,
            [
                _rule(K.DETERMINISTIC_CALCULATION, P.SPY_REGIME_FEATURES, "fp.regime_synthetic.v1"),
                _rule(
                    K.DETERMINISTIC_CALCULATION,
                    P.SPY_REGIME_CLASSIFICATION,
                    "fp.regime_synthetic.v1",
                ),
            ],
            [S.INSTRUMENT, S.MARKET_SESSION],
        ),
        _producer(
            CLOCK,
            ProducerType.SOURCE_ADAPTER,
            [_rule(K.CONFIRMED_FACT, "clock_health_fact.v1", "fp.clock.v1")],
            [S.SYSTEM_COMPONENT],
        ),
        _producer(
            FRED,
            ProducerType.SOURCE_ADAPTER,
            [
                _rule(K.CONFIRMED_FACT, P.MACRO_OBSERVATION_FACT, "fp.macro_monthly.v1"),
                _rule(K.MISSING_EVIDENCE, missing, "fp.macro_monthly.v1"),
            ],
            [S.MACRO_SERIES],
            reasons=("missing_observation",),
        ),
        _producer(
            CONTRACT_SELECTOR_PRODUCER_ID,
            ProducerType.DETERMINISTIC_ENGINE,
            [_rule(K.DETERMINISTIC_CALCULATION, CONTRACT_SELECTOR_PAYLOAD_ID, "fp.selector.v1")],
            [S.INSTRUMENT],
        ),
        _producer(
            RESEARCH,
            ProducerType.RESEARCH_RESULT_PUBLISHER,
            [
                _rule(
                    K.HISTORICAL_RESEARCH_RESULT, "synthetic_research.v1", "fp.research_timeless.v1"
                )
            ],
            [S.RESEARCH_STUDY],
        ),
        _producer(
            AGENT,
            ProducerType.MODEL_AGENT,
            [
                _rule(
                    K.CURRENT_INFERENCE, "synthetic_context_inference.v1", "fp.inference.v1", False
                )
            ],
            [S.INSTRUMENT, S.MARKET_SESSION],
            paths=[ProductionPath.DETERMINISTIC, ProductionPath.MODEL_GENERATED],
        ),
        _producer(
            DETECTOR,
            ProducerType.CONFLICT_DETECTOR,
            [_rule(K.MISSING_EVIDENCE, missing, "fp.bars_synthetic.v1", False)],
            [S.INSTRUMENT],
        ),
        _producer(
            EVALUATOR,
            ProducerType.DETERMINISTIC_ENGINE,
            [_rule(K.DETERMINISTIC_CALCULATION, SETUP_EVALUATION_PAYLOAD, "fp.evaluation.v1")],
            [S.INSTRUMENT, S.MARKET_SESSION, S.SETUP_CANDIDATE],
        ),
    ]
    schemas = [
        (P.MARKET_BAR_FACT, True),
        (P.SPY_REGIME_FEATURES, True),
        (P.SPY_REGIME_CLASSIFICATION, True),
        ("clock_health_fact.v1", False),
        (P.MACRO_OBSERVATION_FACT, False),
        (CONTRACT_SELECTOR_PAYLOAD_ID, True),
        ("synthetic_research.v1", False),
        ("synthetic_context_inference.v1", True),
        (SETUP_EVALUATION_PAYLOAD, True),
        (missing, False),
    ]
    live = [K.CONFIRMED_FACT, K.DETERMINISTIC_CALCULATION, K.MISSING_EVIDENCE]
    window = 172_800
    return EvidenceRegistry(
        registry_label=REGISTRY_LABEL,
        producers=sorted(producers, key=lambda p: p.producer_id),
        payload_schemas=sorted(
            [PayloadSchemaEntry(payload_schema_id=s, spy_price_content=p) for s, p in schemas],
            key=lambda s: s.payload_schema_id,
        ),
        freshness_policies=sorted(
            [
                _policy("fp.bars_synthetic.v1", live, 600, 1200),
                _policy("fp.regime_synthetic.v1", [K.DETERMINISTIC_CALCULATION], 3600, 7200),
                _policy("fp.evaluation.v1", [K.DETERMINISTIC_CALCULATION], 1800, 3600),
                _policy("fp.selector.v1", [K.DETERMINISTIC_CALCULATION], 300, 300),
                _policy("fp.clock.v1", [K.CONFIRMED_FACT], 300, 300),
                _policy("fp.macro_monthly.v1", live, 7_776_000, 7_776_000),
                _policy("fp.inference.v1", [K.CURRENT_INFERENCE], 1800, 3600),
                _policy("fp.research_timeless.v1", [K.HISTORICAL_RESEARCH_RESULT], timeless=True),
            ],
            key=lambda p: p.policy_id,
        ),
        clock_policy=ClockPolicy(
            policy_id="clock.synthetic_slice.v1",
            clock_producer_id=CLOCK,
            max_abs_offset_ms=1000,
            max_sync_age_seconds=3600,
            max_measurement_age_seconds=600,
        ),
        consumer_grants=sorted(
            [
                ConsumerGrant(
                    consumer_id=ConsumerId.DASHBOARD,
                    purposes=sorted(
                        [
                            BundlePurpose.LIVE_MARKET_STATE,
                            BundlePurpose.PREMARKET_BRIEFING,
                            BundlePurpose.SETUP_DETAIL,
                        ]
                    ),
                    permissions=_READS,
                    machine_decision_mode_allowed=False,
                ),
                # Isolated test fixture only (review decision, item 66): lets the
                # frozen severity derivation mark a provider disagreement on a
                # required bar as critical. No production authority; must never
                # appear in a production registry.
                ConsumerGrant(
                    consumer_id=ConsumerId.SETUP_RANKER,
                    purposes=sorted([BundlePurpose.LIVE_MARKET_STATE, BundlePurpose.SETUP_DETAIL]),
                    permissions=sorted(
                        [
                            ConsumerPermission.READ_CALCULATIONS,
                            ConsumerPermission.READ_CONFLICTS,
                            ConsumerPermission.READ_FACTS,
                            ConsumerPermission.READ_RESEARCH,
                        ]
                    ),
                    machine_decision_mode_allowed=True,
                ),
            ],
            key=lambda g: g.consumer_id,
        ),
        selection_rules=sorted(
            [
                SelectionRule(
                    selection_rule_id="sel.slice.live.v1",
                    purpose=BundlePurpose.LIVE_MARKET_STATE,
                    requirements=[bars_req, regime_req],
                    max_window_seconds=window,
                ),
                SelectionRule(
                    selection_rule_id="sel.slice.premarket.v1",
                    purpose=BundlePurpose.PREMARKET_BRIEFING,
                    requirements=[research_req],
                    max_window_seconds=window,
                ),
                SelectionRule(
                    selection_rule_id="sel.slice.setup.v1",
                    purpose=BundlePurpose.SETUP_DETAIL,
                    requirements=[bars_req, research_req],
                    max_window_seconds=window,
                ),
            ],
            key=lambda r: r.purpose,
        ),
        uncertainty_codes=["iex_partial_volume", "vintage_revisable"],
    )
