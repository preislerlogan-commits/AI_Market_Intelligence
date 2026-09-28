"""Bounded enums for the Evidence Envelope family (design §B–§O).

Every field that can change behavior uses one of these ``StrEnum`` types,
never an open string.
"""

from __future__ import annotations

from enum import StrEnum


class EvidenceKind(StrEnum):
    CONFIRMED_FACT = "confirmed_fact"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    HISTORICAL_RESEARCH_RESULT = "historical_research_result"
    CURRENT_INFERENCE = "current_inference"
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"


STATUS_KINDS = frozenset({EvidenceKind.MISSING_EVIDENCE, EvidenceKind.STALE_EVIDENCE})


class SubjectType(StrEnum):
    INSTRUMENT = "instrument"
    INDEX = "index"
    MARKET = "market"
    MACRO_SERIES = "macro_series"
    NEWS_ITEM = "news_item"
    SCHEDULED_CATALYST = "scheduled_catalyst"
    MARKET_SESSION = "market_session"
    SETUP_CANDIDATE = "setup_candidate"
    SCENARIO = "scenario"
    OPTION_CONTRACT = "option_contract"
    RESEARCH_STUDY = "research_study"
    PROVIDER_HEALTH = "provider_health"
    SYSTEM_COMPONENT = "system_component"


class ProducerType(StrEnum):
    SOURCE_ADAPTER = "source_adapter"
    DETERMINISTIC_ENGINE = "deterministic_engine"
    RESEARCH_RESULT_PUBLISHER = "research_result_publisher"
    MODEL_AGENT = "model_agent"
    CONFLICT_DETECTOR = "conflict_detector"
    BUNDLE_BUILDER = "bundle_builder"
    EXPLANATION_LAYER = "explanation_layer"
    NOTIFICATION_LAYER = "notification_layer"


class ProductionPath(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_GENERATED = "model_generated"


class AvailabilityState(StrEnum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    REFUSED = "refused"
    ERROR = "error"


class FreshnessState(StrEnum):
    CURRENT = "current"
    AGING = "aging"
    STALE = "stale"
    TIMELESS = "timeless"
    UNKNOWN = "unknown"


class FreshnessReason(StrEnum):
    WITHIN_CURRENT_WINDOW = "within_current_window"
    WITHIN_AGING_WINDOW = "within_aging_window"
    BEYOND_STALE_BOUNDARY = "beyond_stale_boundary"
    OBSERVED_TIMESTAMP_MISSING = "observed_timestamp_missing"
    OBSERVED_TIMESTAMP_IN_FUTURE = "observed_timestamp_in_future"
    CLOCK_UNHEALTHY = "clock_unhealthy"
    CLOCK_HEALTH_UNKNOWN = "clock_health_unknown"
    POLICY_PARAMETERS_UNSET = "policy_parameters_unset"
    OFF_HOURS_RULE_APPLIED = "off_hours_rule_applied"
    TIMELESS_BY_POLICY = "timeless_by_policy"


class DataQuality(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    NOT_ASSESSED = "not_assessed"


class SourceBasis(StrEnum):
    DIRECTLY_OBSERVED = "directly_observed"
    DERIVED = "derived"
    RULE_INFERRED = "rule_inferred"
    MODEL_INFERRED = "model_inferred"


class SourceTier(StrEnum):
    PRIMARY_OFFICIAL = "primary_official"
    LICENSED_MARKET_DATA = "licensed_market_data"
    COMPANY_PRIMARY = "company_primary"
    REPUTABLE_REPORTING = "reputable_reporting"
    SECONDARY_COMMENTARY = "secondary_commentary"
    NOT_APPLICABLE = "not_applicable"


class CalculationCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"


class InferenceSupport(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    UNABLE_TO_DETERMINE = "unable_to_determine"
    NOT_ASSESSED = "not_assessed"
    NOT_APPLICABLE = "not_applicable"


class ResearchStatus(StrEnum):
    EXPLORATORY = "exploratory"
    CONFIRMED = "confirmed"
    SHADOW_PENDING = "shadow_pending"
    UNSUPPORTED = "unsupported"
    NOT_APPLICABLE = "not_applicable"


class GradedStrength(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CONFIDENCE_NOT_AVAILABLE = "confidence_not_available"


class ResultKind(StrEnum):
    DISCOVERY_RESULT = "discovery_result"
    CONFIRMATION_RESULT = "confirmation_result"
    HOLDOUT_RESULT = "holdout_result"
    SHADOW_RESULT = "shadow_result"


class HoldoutState(StrEnum):
    SEALED = "sealed"
    RECORDED = "recorded"
    NOT_APPLICABLE = "not_applicable"


class InferenceMethod(StrEnum):
    MODEL_STRUCTURED_OUTPUT = "model_structured_output"
    DETERMINISTIC_RULE = "deterministic_rule"


class ClaimBasis(StrEnum):
    SUMMARIZES_INPUTS = "summarizes_inputs"
    INTERPRETS_MECHANISM = "interprets_mechanism"
    COMPARES_ITEMS = "compares_items"
    EXPLAINS_SCENARIO_RELATION = "explains_scenario_relation"
    EXPLAINS_ABSENCE = "explains_absence"


class DirectionalContent(StrEnum):
    NONE = "none"
    SCENARIO_RELATION = "scenario_relation"


class ScenarioClass(StrEnum):
    NON_DIRECTIONAL = "non_directional"
    DIRECTIONAL = "directional"


class ScenarioRelationKind(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class RationaleCategory(StrEnum):
    OBSERVED_PRICE_STRUCTURE = "observed_price_structure"
    REGIME_CLASSIFICATION = "regime_classification"
    MACRO_RELEASE = "macro_release"
    NEWS_EVENT = "news_event"
    RESEARCH_CONTEXT = "research_context"
    DATA_QUALITY = "data_quality"
    FRESHNESS = "freshness"
    CONFLICT = "conflict"


class ConflictType(StrEnum):
    INFERENCE_VS_OBSERVATION = "inference_vs_observation"
    CROSS_DOMAIN_INFERENCE_DISAGREEMENT = "cross_domain_inference_disagreement"
    PROVIDER_DISAGREEMENT = "provider_disagreement"
    CURRENT_VS_RESEARCH_CONTEXT = "current_vs_research_context"
    FRESHNESS_MISMATCH = "freshness_mismatch"
    REVISION_DISAGREEMENT = "revision_disagreement"
    SCENARIO_RELATION_DISAGREEMENT = "scenario_relation_disagreement"


class DetectionMethod(StrEnum):
    DETERMINISTIC_RULE = "deterministic_rule"
    MODEL_DETECTED = "model_detected"
    MANUAL_REVIEW = "manual_review"


class ConflictSeverity(StrEnum):
    INFORMATIONAL = "informational"
    MATERIAL = "material"
    CRITICAL = "critical"


class ConflictStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"
    ACKNOWLEDGED_UNRESOLVABLE = "acknowledged_unresolvable"


class ResolutionKind(StrEnum):
    NEW_EVIDENCE = "new_evidence"
    UNDERLYING_ITEM_REVISED = "underlying_item_revised"
    MANUAL_ACKNOWLEDGEMENT = "manual_acknowledgement"


class CitationRole(StrEnum):
    SUPPORTS_CLAIM = "supports_claim"
    CONTRADICTS_CLAIM = "contradicts_claim"
    CONTEXT = "context"
    MISSING_EVIDENCE_NOTICE = "missing_evidence_notice"
    STALE_EVIDENCE_NOTICE = "stale_evidence_notice"
    CONFLICT_NOTICE = "conflict_notice"


class ConsumerId(StrEnum):
    DASHBOARD = "dashboard"
    ASSISTANT = "assistant"
    SETUP_RANKER = "setup_ranker"
    CONTRACT_RANKER = "contract_ranker"
    CONTRACT_REVIEW_UI = "contract_review_ui"
    NOTIFICATION_LAYER = "notification_layer"
    BUNDLE_BUILDER = "bundle_builder"
    AUDIT_EXPORT = "audit_export"


MACHINE_DECISION_CONSUMERS = frozenset(
    {ConsumerId.SETUP_RANKER, ConsumerId.NOTIFICATION_LAYER}
)


class ConsumerPermission(StrEnum):
    READ_FACTS = "read_facts"
    READ_CALCULATIONS = "read_calculations"
    READ_RESEARCH = "read_research"
    READ_INFERENCES = "read_inferences"
    READ_CONFLICTS = "read_conflicts"
    READ_SUPERSEDED = "read_superseded"
    EMIT_CALCULATION = "emit_calculation"
    EMIT_INFERENCE = "emit_inference"
    EMIT_NOTIFICATION_EVENT = "emit_notification_event"


class BundlePurpose(StrEnum):
    PREMARKET_BRIEFING = "premarket_briefing"
    LIVE_MARKET_STATE = "live_market_state"
    SETUP_DETAIL = "setup_detail"
    CONTRACT_REVIEW = "contract_review"
    ASSISTANT_QUESTION_CONTEXT = "assistant_question_context"
    NOTIFICATION_DECISION = "notification_decision"


LIVE_PURPOSES = frozenset(
    {
        BundlePurpose.LIVE_MARKET_STATE,
        BundlePurpose.SETUP_DETAIL,
        BundlePurpose.CONTRACT_REVIEW,
        BundlePurpose.NOTIFICATION_DECISION,
    }
)


class MissingProducerReason(StrEnum):
    NO_ITEM_RECORDED = "no_item_recorded"
    ONLY_STALE_ITEMS = "only_stale_items"
    PRODUCER_UNAVAILABLE = "producer_unavailable"
    PRODUCER_NOT_IMPLEMENTED = "producer_not_implemented"
    HOLDOUT_RESTRICTED = "holdout_restricted"


class RevisionReason(StrEnum):
    ORIGINAL = "original"
    SOURCE_REVISION = "source_revision"
    PRODUCER_CORRECTION = "producer_correction"
    ANALYTICAL_REINTERPRETATION = "analytical_reinterpretation"
    AVAILABILITY_CHANGE = "availability_change"


class DuckDbTable(StrEnum):
    MARKET_BARS = "market_bars"
    NEWS_ARTICLES = "news_articles"
    MACRO_OBSERVATIONS = "macro_observations"
    MACRO_SERIES_METADATA = "macro_series_metadata"
    OPTION_CHAIN_SNAPSHOT_BATCHES = "option_chain_snapshot_batches"
    OPTION_CHAIN_SNAPSHOTS = "option_chain_snapshots"
    OPTION_CHAIN_SNAPSHOT_BATCH_ITEMS = "option_chain_snapshot_batch_items"


class EndpointClass(StrEnum):
    ALPACA_STOCK_BARS = "alpaca_stock_bars"
    ALPACA_STOCK_SNAPSHOT = "alpaca_stock_snapshot"
    ALPACA_NEWS = "alpaca_news"
    ALPACA_OPTION_CHAIN = "alpaca_option_chain"
    FRED_SERIES_OBSERVATIONS = "fred_series_observations"
    FRED_SERIES_METADATA = "fred_series_metadata"


class Feed(StrEnum):
    IEX = "iex"
    SIP = "sip"
    OPRA = "opra"
    INDICATIVE = "indicative"


class ResponseClass(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    AUTH_FAILURE = "auth_failure"
    INVALID_RESPONSE = "invalid_response"
    REFUSED_BY_GUARD = "refused_by_guard"


class IngestionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class LocatorKind(StrEnum):
    REPO_DOCUMENT = "repo_document"
    EXTERNAL_PUBLICATION = "external_publication"


class OffHoursMode(StrEnum):
    ELAPSED_WALL_CLOCK = "elapsed_wall_clock"
    FREEZE_AT_SESSION_CLOSE = "freeze_at_session_close"
    NOT_APPLICABLE = "not_applicable"


class DirectionalAuthority(StrEnum):
    NONE = "none"
    NON_DIRECTIONAL_SCENARIO_RELATIONS = "non_directional_scenario_relations"
    AUTHORIZED_DIRECTIONAL = "authorized_directional"


class ImplementationStatus(StrEnum):
    IMPLEMENTED = "implemented"
    FUTURE = "future"


class ClockHealthReason(StrEnum):
    """Why a freshness evaluation's clock health is what it is. Recorded
    separately from ``clock_health_item_id``, which names a clock fact only
    when a particular fact was evaluated."""

    NOT_EVALUATED = "not_evaluated"
    HEALTHY = "healthy"
    OFFSET_EXCEEDED = "offset_exceeded"
    SYNC_TOO_OLD = "sync_too_old"
    NO_CLOCK_FACT = "no_clock_fact"
    CLOCK_POLICY_UNSET = "clock_policy_unset"
    CLOCK_FACT_TOO_OLD = "clock_fact_too_old"
    CLOCK_FACT_UNREADABLE = "clock_fact_unreadable"


CLOCK_FACT_EVALUATED = frozenset(
    {
        ClockHealthReason.HEALTHY,
        ClockHealthReason.OFFSET_EXCEEDED,
        ClockHealthReason.SYNC_TOO_OLD,
        ClockHealthReason.CLOCK_FACT_TOO_OLD,
        ClockHealthReason.CLOCK_FACT_UNREADABLE,
    }
)
CLOCK_UNHEALTHY_REASONS = frozenset(
    {ClockHealthReason.OFFSET_EXCEEDED, ClockHealthReason.SYNC_TOO_OLD}
)
CLOCK_UNKNOWN_REASONS = frozenset(
    {
        ClockHealthReason.NO_CLOCK_FACT,
        ClockHealthReason.CLOCK_POLICY_UNSET,
        ClockHealthReason.CLOCK_FACT_TOO_OLD,
        ClockHealthReason.CLOCK_FACT_UNREADABLE,
    }
)


class AmbiguityReason(StrEnum):
    """Why a bundle requirement has competing evidence and no winner."""

    COMPETING_OBSERVATIONS = "competing_observations"
    BRANCHED_REVISIONS = "branched_revisions"
