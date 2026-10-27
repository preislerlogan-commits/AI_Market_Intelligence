"""The eleven deterministic scenarios of the offline vertical slice.

Every scenario runs in its own temporary store on one fixed synthetic
timeline (Tuesday 2027-01-12, outside the sealed holdout window). Source
objects are the real connector dataclasses filled with synthetic values; they
pass through the producer adapters. Option-chain snapshots pass through the
option-chain adapter; the deterministic Contract Selector runs on the batch
rebuilt from that stored-to-be evidence, and its result passes through the
selector adapter citing exactly those chain items. Items no adapter covers
yet (the recorded research result and a labelled inference) come from the
dashboard prototype's existing synthetic builders. The setup
evaluation is the one synthetic, in-memory, ``synthetic-only-`` definition's
deterministic output. In the agent scenario, synthetic analysis-agent reports
pass through the agent adapters against a bundle read back from the store:
completed claims are stored as labelled inference and a pre-model abstention
as missing evidence, with their parents already stored.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    SelectorConfig,
)
from market_intelligence.contract_selection.selector import select_eligible_contracts
from market_intelligence.dashboard import fixtures as dash
from market_intelligence.dashboard.fixtures import Scenario
from market_intelligence.data_connectors.alpaca_bars import Bar
from market_intelligence.data_connectors.alpaca_options_chain import OptionChainSnapshot
from market_intelligence.data_connectors.fred_macro_data import FredObservation, FredSeriesMetadata
from market_intelligence.evidence.canonical import canonical_sha256, sort_key
from market_intelligence.evidence.contracts import (
    ORIGINAL_REVISION,
    CalculationInputReference,
    EvidenceAvailability,
    EvidenceConflict,
    EvidenceConflictContent,
    EvidenceItem,
    EvidenceItemContent,
    EvidenceProvenance,
    EvidenceQualityProfile,
    EvidenceSubject,
    EvidenceTemporalScope,
    ProviderRequestReference,
    seal_conflict,
    seal_item,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    CalculationCompleteness,
    ConflictSeverity,
    ConflictStatus,
    ConflictType,
    DataQuality,
    DetectionMethod,
    EndpointClass,
    EvidenceKind,
    Feed,
    GradedStrength,
    InferenceSupport,
    ProducerType,
    ProductionPath,
    ResearchStatus,
    ResponseClass,
    SourceBasis,
    SourceTier,
    SubjectType,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence.validation import derive_machine_decision_eligible
from market_intelligence.evidence_adapters import agents as agent_adapters
from market_intelligence.evidence_adapters import clock as clock_adapter
from market_intelligence.evidence_adapters import contract_selector as selector_adapter
from market_intelligence.evidence_adapters import fred as fred_adapter
from market_intelligence.evidence_adapters import market_calculations as calc_adapter
from market_intelligence.evidence_adapters import market_data as bar_adapter
from market_intelligence.evidence_adapters import news as news_adapter
from market_intelligence.evidence_adapters import options as chain_adapter
from market_intelligence.evidence_adapters.common import window_subject
from market_intelligence.integration import registry as R
from market_intelligence.integration.pipeline import ScenarioStore, _stored_bundle, read_back
from market_intelligence.integration.workspace import store_paths
from market_intelligence.market_features.spy_regime_classifier import classify
from market_intelligence.market_features.spy_regime_contracts import (
    BreadthState,
    CatalystState,
    IntradayBar,
    RegimeEngineInput,
    ScenarioHorizon,
)
from market_intelligence.market_features.spy_regime_features import compute_features
from market_intelligence.setup_cards.definitions import (
    SETUP_EVALUATION_PAYLOAD,
    SetupEvaluationPayload,
)
from market_intelligence.setup_cards.enums import (
    Lane,
    NoSetupReason,
    SetupLifecycle,
    SetupQualification,
)

SESSION = date(2027, 1, 12)


def _t(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2027, 1, 12, hour, minute, second, tzinfo=UTC)


START = _t(12, 0)
OPEN = _t(14, 30)  # 09:30 America/New_York
AS_OF = _t(15, 2, 45)
STALE_AS_OF = _t(15, 25)
CORRECTED_AS_OF = _t(15, 12)
HOLDOUT_BAR_START = datetime(2026, 10, 14, 14, 30, tzinfo=UTC)
LOOKBACK = timedelta(hours=40)
_SPY = EvidenceSubject(subject_type=SubjectType.INSTRUMENT, subject_id=R.SPY)


# --- Synthetic source objects -------------------------------------------------------------------


def synthetic_bars(start: datetime = OPEN, count: int = 6) -> list[Bar]:
    """Six synthetic five-minute SPY bars (the real connector dataclass)."""
    closes = ["500.2", "500.6", "500.9", "500.7", "501.1", "501.4"]
    retrieved = start + timedelta(minutes=5 * count, seconds=20)
    return [
        Bar(
            provider="alpaca",
            symbol="SPY",
            timeframe="5Min",
            feed="iex",
            adjustment="raw",
            currency="USD",
            timestamp=(start + timedelta(minutes=5 * i)).isoformat().replace("+00:00", "Z"),
            open=Decimal("500.0"),
            high=Decimal(closes[i]) + Decimal("0.4"),
            low=Decimal("499.5"),
            close=Decimal(closes[i]),
            volume=12_000 + 500 * i,
            trade_count=150 + i,
            vwap=Decimal("500.3"),
            retrieved_at=retrieved.isoformat().replace("+00:00", "Z"),
        )
        for i in range(count)
    ]


def _request(endpoint: EndpointClass, at: datetime, *, feed: Feed | None, provider: str):
    """A synthetic provider-request reference; no request was ever made."""
    return ProviderRequestReference(
        reference_type="provider_request",
        provider=provider,
        endpoint_class=endpoint,
        feed=feed,
        request_params_sha256=canonical_sha256({"synthetic": endpoint.value, "at": at.isoformat()}),
        requested_at_utc=at,
        response_class=ResponseClass.OK,
    )


def _bars_reference(bars: list[Bar]) -> ProviderRequestReference:
    requested = datetime.fromisoformat(bars[-1].retrieved_at.replace("Z", "+00:00"))
    return _request(
        EndpointClass.ALPACA_STOCK_BARS,
        requested - timedelta(seconds=10),
        feed=Feed.IEX,
        provider="alpaca",
    )


CHAIN_RETRIEVED = _t(14, 58)
CHAIN_AS_OF = _t(15, 0)
CHAIN_QUOTED = "2027-01-12T14:57:58.250000Z"


def synthetic_chain(feed: str) -> list[OptionChainSnapshot]:
    """Four synthetic SPY call snapshots from one retrieval (the real
    connector dataclass). Two pass every selector filter; the third has a wide
    spread and the fourth a crossed quote (bid above ask), so the selector
    rejects both."""
    quotes = {
        "680": ("1.00", "1.10"),
        "681": ("1.00", "1.10"),
        "682": ("1.00", "3.00"),
        "683": ("1.20", "1.10"),
    }
    return [
        OptionChainSnapshot(
            provider="alpaca",
            underlying="SPY",
            feed=feed,
            contract_symbol=f"SPY270112C00{strike}000",
            expiration_date="2027-01-12",
            option_type="call",
            strike_price=Decimal(strike),
            quote_timestamp=CHAIN_QUOTED,
            bid_price=Decimal(bid),
            bid_size=10,
            ask_price=Decimal(ask),
            ask_size=10,
            trade_timestamp=None,
            trade_price=None,
            trade_size=None,
            implied_volatility=Decimal("0.2"),
            delta=Decimal("0.40"),
            gamma=Decimal("0.05"),
            theta=Decimal("-0.10"),
            vega=Decimal("0.10"),
            rho=Decimal("0.01"),
            retrieved_at=CHAIN_RETRIEVED.isoformat().replace("+00:00", "Z"),
        )
        for strike, (bid, ask) in quotes.items()
    ]


def series_metadata() -> FredSeriesMetadata:
    return FredSeriesMetadata(
        provider="fred",
        series_id="CPIAUCSL",
        title="Synthetic consumer price index series",
        observation_start="1947-01-01",
        observation_end="2026-12-01",
        frequency="Monthly",
        frequency_short="M",
        units="Index 1982-1984=100",
        units_short="Index 1982-1984=100",
        seasonal_adjustment="Seasonally Adjusted",
        seasonal_adjustment_short="SA",
        last_updated="2027-01-11T13:01:02Z",
        popularity=90,
        notes=None,
        retrieved_at_utc="2027-01-12T13:05:00Z",
    )


def observation(*, vintage: str, value: str, retrieved: str) -> FredObservation:
    return FredObservation(
        provider="fred",
        series_id="CPIAUCSL",
        observation_date="2026-12-01",
        value=Decimal(value),
        is_missing=False,
        realtime_start=vintage,
        realtime_end="9999-12-31",
        retrieved_at=retrieved,
    )


# --- Items no adapter covers yet ------------------------------------------------------------------


def evaluation_item(
    registry,
    *,
    at: datetime,
    generated: datetime,
    parents: list[EvidenceItem],
    qualification: SetupQualification = SetupQualification.QUALIFIED,
    lifecycle: SetupLifecycle = SetupLifecycle.EVALUATION_COMPLETE,
    setup_subject_id: str | None = dash.SETUP_SUBJECT,
    no_setup_reason: NoSetupReason | None = None,
) -> EvidenceItem:
    """The synthetic-only definition's deterministic evaluation, citing the
    stored bars it evaluated."""
    secondary = [window_subject(SESSION)]
    if setup_subject_id is not None:
        secondary.append(
            EvidenceSubject(subject_type=SubjectType.SETUP_CANDIDATE, subject_id=setup_subject_id)
        )
    observed = min((p.provenance.source_observed_at_utc for p in parents), default=at)
    content = EvidenceItemContent(
        evidence_kind=EvidenceKind.DETERMINISTIC_CALCULATION,
        subjects=[_SPY, *sorted(secondary, key=lambda s: sort_key(s.model_dump(mode="json")))],
        effective_at_utc=at,
        temporal_scope=EvidenceTemporalScope(session_date=SESSION),
        payload_schema_id=SETUP_EVALUATION_PAYLOAD,
        payload=SetupEvaluationPayload(
            setup_definition_id=R.SYNTHETIC_DEFINITION.setup_definition_id,
            lane=Lane.VWAP_REVERSION,
            setup_subject_id=setup_subject_id,
            lifecycle_state=lifecycle,
            qualification=qualification,
            no_setup_reason=no_setup_reason,
        ).model_dump(mode="json"),
        provenance=EvidenceProvenance(
            producer_id=R.EVALUATOR,
            producer_version="1.0.0",
            producer_type=ProducerType.DETERMINISTIC_ENGINE,
            production_path=ProductionPath.DETERMINISTIC,
            code_commit_sha="0" * 40,
            code_tree_clean=True,
            configuration_identity="cfg_none",
            generated_at_utc=generated,  # never before the parents it cites
            source_observed_at_utc=observed,
            source_references=[
                CalculationInputReference(
                    reference_type="calculation_input",
                    input_schema_id="synthetic-setup-evaluation-input-1",
                    input_sha256=canonical_sha256(sorted(p.item_id for p in parents)),
                    input_record_count=len(parents),
                )
            ],
            parent_evidence_ids=sorted(p.item_id for p in parents),
        ),
        freshness_policy_id="fp.evaluation.v1",
        availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
        quality=EvidenceQualityProfile(
            data_quality=DataQuality.COMPLETE,
            source_basis=SourceBasis.DERIVED,
            source_tier=SourceTier.NOT_APPLICABLE,
            calculation_completeness=CalculationCompleteness.COMPLETE,
            inference_support=InferenceSupport.NOT_APPLICABLE,
            research_status=ResearchStatus.NOT_APPLICABLE,
            graded_strength=GradedStrength.CONFIDENCE_NOT_AVAILABLE,
        ),
        revision=ORIGINAL_REVISION,
        machine_decision_eligible=False,
    )
    eligible = derive_machine_decision_eligible(content, registry)
    fields = {name: getattr(content, name) for name in EvidenceItemContent.model_fields}
    return seal_item(EvidenceItemContent(**{**fields, "machine_decision_eligible": eligible}))


def provider_disagreement(
    a: EvidenceItem,
    b: EvidenceItem,
    at: datetime,
    severity: ConflictSeverity = ConflictSeverity.CRITICAL,
) -> EvidenceConflict:
    """``severity`` must equal the store's frozen derivation, or the store
    refuses the conflict (``conflict_severity_mismatch``)."""
    return seal_conflict(
        EvidenceConflictContent(
            conflict_type=ConflictType.PROVIDER_DISAGREEMENT,
            involved_item_ids=sorted([a.item_id, b.item_id]),
            detection_method=DetectionMethod.MANUAL_REVIEW,
            detector_producer_id=R.DETECTOR,
            detector_version="1.0.0",
            severity=severity,
            status=ConflictStatus.UNRESOLVED,
            evaluated_as_of_utc=at,
            detected_at_utc=at + timedelta(seconds=1),
        )
    )


# --- The shared timeline ---------------------------------------------------------------------


@dataclass
class Built:
    """What one scenario's run produced, before reading back."""

    store: ScenarioStore
    bars: list[EvidenceItem]
    features: EvidenceItem | None


def _premarket(s: ScenarioStore) -> None:
    s.at(_t(13, 0)).ingest([dash.research_item()])
    reading = clock_adapter.ClockHealthReading(
        measured_at_utc=_t(13, 30), offset_ms=4, last_sync_at_utc=_t(13, 25)
    )
    s.at(_t(13, 30, 10)).ingest([_clock(s, reading)])
    s.at(_t(13, 32)).bundle(
        "premarket", BundlePurpose.PREMARKET_BRIEFING, _t(13, 31), lookback=LOOKBACK
    )


def _clock(s: ScenarioStore, reading) -> EvidenceItem:
    from market_intelligence.evidence.contracts import DocumentReference
    from market_intelligence.evidence.enums import LocatorKind

    return clock_adapter.adapt_clock_health(
        reading,
        reference=DocumentReference(
            reference_type="document",
            locator_kind=LocatorKind.REPO_DOCUMENT,
            document_path="docs/EVIDENCE_REGISTRY.md",
            document_commit_sha="0" * 40,
            section_token="clock",
        ),
        context=s.context(clock_adapter.CLOCK_CONFIGURATION),
    )


def _session(
    s: ScenarioStore,
    *,
    bars: bool = True,
    twin: bool = False,
    selector: str | None = None,
    qualification=SetupQualification.QUALIFIED,
    clock_at: datetime = _t(15, 1, 30),
) -> Built:
    """Bars, regime results, a clock fact, an optional selector result, the
    synthetic evaluation and a labelled inference, in timeline order."""
    bar_items: list[EvidenceItem] = []
    features_item = None
    if bars:
        source = synthetic_bars()
        reference = _bars_reference(source)
        s.at(_t(15, 0, 30))
        context = s.context(bar_adapter.BARS_CONFIGURATION)
        bar_items = [
            bar_adapter.adapt_market_bar(b, reference=reference, context=context) for b in source
        ]
        s.ingest(bar_items)
        if twin:
            twin_bar = dataclasses.replace(source[-1], close=Decimal("501.3"), volume=13_100)
            s.at(_t(15, 0, 40)).ingest(
                [
                    bar_adapter.adapt_market_bar(
                        twin_bar,
                        reference=_bars_reference([twin_bar]),
                        context=s.context(bar_adapter.BARS_CONFIGURATION),
                    )
                ]
            )
        engine_input = RegimeEngineInput(
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
                for b in source
            ],
            catalyst_state=CatalystState.UNKNOWN,
            breadth_state=BreadthState.UNAVAILABLE,
        )
        features = compute_features(engine_input)
        s.at(_t(15, 1))
        regime = s.context(calc_adapter.REGIME_CONFIGURATION)
        features_item = calc_adapter.adapt_regime_features(
            features, engine_input=engine_input, bar_items=bar_items, context=regime
        )
        classification_item = calc_adapter.adapt_regime_classification(
            classify(features), features_item=features_item, context=regime
        )
        s.ingest([features_item, classification_item])
    else:
        s.at(_t(15, 0, 30)).ingest(
            [
                bar_adapter.missing_market_bars(
                    checked_at=_t(15, 0, 30),
                    query_sha256=canonical_sha256({"synthetic_bars_query": SESSION.isoformat()}),
                    context=s.context(bar_adapter.BARS_CONFIGURATION),
                )
            ]
        )
    reading = clock_adapter.ClockHealthReading(
        measured_at_utc=clock_at, offset_ms=5, last_sync_at_utc=clock_at - timedelta(minutes=5)
    )
    s.at(clock_at + timedelta(seconds=10)).ingest([_clock(s, reading)])
    if selector is not None:
        _chain_and_selector(s, selector)
    lane_level = qualification is SetupQualification.NOT_QUALIFIED
    s.at(max(s.clock(), _t(15, 2)))
    evaluation = evaluation_item(
        s.registry,
        at=_t(15, 0),
        generated=s.clock(),
        parents=bar_items[-1:],
        qualification=qualification,
        setup_subject_id=None if lane_level else dash.SETUP_SUBJECT,
        no_setup_reason=NoSetupReason.CRITERIA_NOT_MET if lane_level else None,
    )
    s.ingest([evaluation])
    if bar_items and features_item is not None:
        inference = dash.inference_item(
            # Effective 15:01, so its generation (+3 s) follows both parents.
            [bar_items[-1], features_item],
            _t(15, 1),
            claim="volatility_context_elevated",
        )
        s.at(max(s.clock(), _t(15, 2, 10))).ingest([inference])
    return Built(s, bar_items, features_item)


def _chain_and_selector(s: ScenarioStore, feed: str) -> None:
    """Chain snapshots -> option-chain evidence (stored) -> the deterministic
    Contract Selector on the batch rebuilt from that evidence -> selector
    evidence citing exactly those items (stored). Indicative chains run with
    research explicitly allowed, so their contracts are research-only."""
    s.at(max(s.clock(), _t(15, 1, 45)))
    context = s.context(chain_adapter.OPTION_CHAIN_CONFIGURATION)
    reference = _request(
        EndpointClass.ALPACA_OPTION_CHAIN,
        CHAIN_RETRIEVED - timedelta(seconds=2),
        feed=Feed(feed),
        provider="alpaca",
    )
    chain_items = [
        chain_adapter.adapt_option_quote(snapshot, reference=reference, context=context)
        for snapshot in synthetic_chain(feed)
    ]
    s.ingest(chain_items)
    batch = selector_adapter.option_chain_batch(chain_items)
    selector_input = ContractSelectorInput(
        scenario_horizon=ScenarioHorizon.INTRADAY_30M,
        regime_as_of_timestamp=batch.retrieved_at,
        underlying_price_timestamp=batch.retrieved_at,
        as_of_timestamp=CHAIN_AS_OF,
        underlying_price=Decimal("680"),
        batch=batch,
        config=SelectorConfig(allow_indicative_for_research=feed == "indicative"),
    )
    s.at(max(s.clock(), _t(15, 1, 50)))
    result = select_eligible_contracts(selector_input, generated_at=s.clock())
    identity = selector_adapter.selector_configuration_identity(result.config_snapshot)
    s.ingest(
        [
            selector_adapter.adapt_selector_result(
                result,
                selector_input=selector_input,
                chain_items=chain_items,
                context=s.context(identity),
            )
        ]
    )


def _bundles_and_card(built: Built, as_of: datetime, *, no_setup: bool = False, selector=True):
    s = built.store
    s.at(as_of + timedelta(seconds=15))
    s.bundle("market", BundlePurpose.LIVE_MARKET_STATE, as_of, lookback=LOOKBACK)
    setup = s.bundle("setup", BundlePurpose.SETUP_DETAIL, as_of, lookback=LOOKBACK)
    s.at(as_of + timedelta(seconds=45))
    supporting = [built.bars[-1].item_id] if built.bars else []
    research = next(
        e.item_id for e in setup.entries if e.evidence_kind.value == "historical_research_result"
    )
    if no_setup:
        s.no_setup_card(setup, supporting_ids=supporting, context_ids=[research])
        return
    context = [research]
    # Only the slice's own labelled synthetic inference is card context; the
    # analysis agents' inference is never cited by a card.
    inference = [
        e.item_id
        for e in setup.entries
        if e.evidence_kind.value == "current_inference" and e.producer_id == R.AGENT
    ]
    s.setup_card(
        setup,
        setup_subject_id=dash.SETUP_SUBJECT,
        supporting_ids=supporting,
        context_ids=[*context, *inference],
        selector_requested=selector,
    )


# --- The ten scenarios -----------------------------------------------------------------------


@dataclass(frozen=True)
class SliceScenario:
    scenario_id: str
    title: str
    summary: str
    run: Callable[[ScenarioStore], str]  # returns the market bundle name to show


def _ready(s: ScenarioStore) -> str:
    _premarket(s)
    _bundles_and_card(_session(s, selector="opra"), AS_OF)
    return "market"


def _not_qualified(s: ScenarioStore) -> str:
    _premarket(s)
    built = _session(s, qualification=SetupQualification.NOT_QUALIFIED)
    _bundles_and_card(built, AS_OF, no_setup=True)
    return "market"


def _missing(s: ScenarioStore) -> str:
    _premarket(s)
    _bundles_and_card(_session(s, bars=False, selector="opra"), AS_OF)
    return "market"


def _stale(s: ScenarioStore) -> str:
    _premarket(s)
    built = _session(s, selector="opra", clock_at=_t(15, 24))
    _bundles_and_card(built, STALE_AS_OF)
    return "market"


def _ambiguous(s: ScenarioStore) -> str:
    _premarket(s)
    _bundles_and_card(_session(s, twin=True, selector="opra"), AS_OF)
    return "market"


def _critical(s: ScenarioStore) -> str:
    _premarket(s)
    built = _session(s, selector="opra")
    s.at(_t(15, 2, 20)).conflict(provider_disagreement(built.bars[0], built.bars[-1], _t(15, 2)))
    _bundles_and_card(built, AS_OF)
    return "market"


def _selector_unavailable(s: ScenarioStore) -> str:
    _premarket(s)
    _bundles_and_card(_session(s, selector=None), AS_OF)
    return "market"


def _research_only(s: ScenarioStore) -> str:
    _premarket(s)
    _bundles_and_card(_session(s, selector="indicative"), AS_OF)
    return "market"


def _holdout(s: ScenarioStore) -> str:
    _premarket(s)
    restricted = synthetic_bars(HOLDOUT_BAR_START, count=1)[0]
    s.at(_t(14, 0))
    try:
        bar_adapter.adapt_market_bar(
            restricted,
            reference=_bars_reference([restricted]),
            context=s.context(bar_adapter.BARS_CONFIGURATION),
        )
    except EvidenceValidationError as refusal:
        s.refusals.append(refusal.reason)  # refused before anything reached storage
    _bundles_and_card(_session(s, selector="opra"), AS_OF)
    return "market"


def _correction(s: ScenarioStore) -> str:
    _premarket_with_macro(s)
    _bundles_and_card(_session(s, selector="opra"), AS_OF)
    # A later vintage corrects the observation. The earlier bundle is untouched.
    s.at(_t(15, 10))
    corrected = fred_adapter.adapt_fred_observation(
        observation(vintage="2027-01-12", value="321.9", retrieved="2027-01-12T15:09:00Z"),
        series_metadata=series_metadata(),
        reference=_request(
            EndpointClass.FRED_SERIES_OBSERVATIONS, _t(15, 8, 50), feed=None, provider="fred"
        ),
        context=s.context(fred_adapter.OBSERVATIONS_CONFIGURATION),
        query_sha256=canonical_sha256({"synthetic_fred_query": "CPIAUCSL"}),
        supersedes=s.named_items["original_observation"],
    )
    s.ingest([corrected])
    reading = clock_adapter.ClockHealthReading(
        measured_at_utc=_t(15, 11), offset_ms=5, last_sync_at_utc=_t(15, 6)
    )
    s.at(_t(15, 11, 10)).ingest([_clock(s, reading)])
    s.at(_t(15, 12, 30)).bundle(
        "market_corrected", BundlePurpose.LIVE_MARKET_STATE, CORRECTED_AS_OF, lookback=LOOKBACK
    )
    return "market_corrected"


def _premarket_with_macro(s: ScenarioStore) -> None:
    """The premarket timeline plus the original FRED vintage at 13:10."""
    s.at(_t(13, 0)).ingest([dash.research_item()])
    s.at(_t(13, 10))
    original = fred_adapter.adapt_fred_observation(
        observation(vintage="2027-01-11", value="321.4", retrieved="2027-01-12T13:05:00Z"),
        series_metadata=series_metadata(),
        reference=_request(
            EndpointClass.FRED_SERIES_OBSERVATIONS, _t(13, 4, 50), feed=None, provider="fred"
        ),
        context=s.context(fred_adapter.OBSERVATIONS_CONFIGURATION),
        query_sha256=canonical_sha256({"synthetic_fred_query": "CPIAUCSL"}),
    )
    s.ingest([original])
    s.named_items["original_observation"] = original
    reading = clock_adapter.ClockHealthReading(
        measured_at_utc=_t(13, 30), offset_ms=4, last_sync_at_utc=_t(13, 25)
    )
    s.at(_t(13, 30, 10)).ingest([_clock(s, reading)])
    s.at(_t(13, 32)).bundle(
        "premarket", BundlePurpose.PREMARKET_BRIEFING, _t(13, 31), lookback=LOOKBACK
    )


# --- Analysis-agent evidence through the store -------------------------------------------------

AGENT_RUN_AT = _t(15, 2, 20)
MARKET_REPORT = {
    "status": "completed",
    "symbol": "SPY",
    "session_date_et": "2027-01-12",
    "evidence_quality": "limited",
    "evidence_summary": "Six stored synthetic five-minute bars and one regime-feature calculation "
    "are available.",
    "observations": [
        {
            "category": "price",
            "statement": "The latest stored synthetic bar closed at 501.4.",
            "evidence_ids": ["bar:latest"],
        },
        {
            "category": "data_quality",
            "statement": "Coverage is the first 30 minutes of the session only.",
            "evidence_ids": ["bar:latest", "regime:features"],
        },
    ],
    "limitations": ["Only the opening half hour of the session is stored."],
    "directional_assessment": "not_performed",
    "trade_recommendation": "not_performed",
    "abstained_reasons": [],
}
MACRO_REPORT = {
    "status": "completed",
    "series_ids": ["CPIAUCSL"],
    "snapshot_created_at_utc": "2027-01-12T13:10:00Z",
    "source_series_count": 1,
    "evidence_quality": "limited",
    "macro_claims": [
        {
            "series_id": "CPIAUCSL",
            "claim_summary": "The stored synthetic CPI observation for December 2026 is 321.4.",
            "evidence_ids": ["fred:CPIAUCSL:2026-12-01"],
            "content_basis": "stored_observation_and_official_metadata",
            "economic_category": "inflation",
            "transmission_channels": ["inflation", "rates"],
            "conditional_mechanism": "Inflation readings can matter through interest-rate "
            "expectations.",
        }
    ],
    "limitations": ["One monthly vintage only; later revisions are possible."],
    "directional_assessment": "not_performed",
    "trade_recommendation": "not_performed",
    "abstained_reasons": [],
}
NEWS_REPORT = {
    "status": "abstained",
    "symbol": "SPY",
    "snapshot_created_at_utc": "2027-01-12T15:02:11Z",
    "source_article_count": 0,
    "evidence_quality": None,
    "event_claims": [],
    "limitations": ["No stored news article was returned for this window."],
    "directional_assessment": "not_performed",
    "trade_recommendation": "not_performed",
    "abstained_reasons": ["no_articles_returned"],
}


def _agent_outputs(s: ScenarioStore, built: Built) -> None:
    """Store a missing-news notice and an agent-input bundle, then run the
    three synthetic reports through the agent adapters against that bundle
    as read back from storage, and store their outputs."""
    s.at(_t(15, 2, 11))
    notice = news_adapter.missing_news(
        checked_at=_t(15, 2, 11),
        query_sha256=canonical_sha256({"synthetic_news_query": SESSION.isoformat()}),
        context=s.context(news_adapter.NEWS_CONFIGURATION),
    )
    s.ingest([notice])
    s.named_items["news_notice"] = notice
    s.at(_t(15, 2, 13))
    agent_input = s.bundle(
        "agent_input", BundlePurpose.LIVE_MARKET_STATE, _t(15, 2, 12), lookback=LOOKBACK
    )
    stored = _stored_bundle(s.store, agent_input.bundle_id)

    def source(keys, *, model: bool) -> agent_adapters.AgentSourceContext:
        return agent_adapters.AgentSourceContext(
            bundle=stored.bundle,
            items=stored.items,
            citation_keys=keys,
            agent_generated_at_utc=AGENT_RUN_AT,
            model_run=dash._model_ref() if model else None,
        )

    configs = agent_adapters.AGENT_CONFIGURATIONS
    s.at(_t(15, 2, 25))
    s.ingest(
        agent_adapters.adapt_market_evidence_report(
            MARKET_REPORT,
            source=source(
                {"bar:latest": built.bars[-1].item_id, "regime:features": built.features.item_id},
                model=True,
            ),
            context=s.context(configs[agent_adapters.MARKET_EVIDENCE_PRODUCER]),
        )
    )
    s.ingest(
        agent_adapters.adapt_macro_report(
            MACRO_REPORT,
            source=source(
                {"fred:CPIAUCSL:2026-12-01": s.named_items["original_observation"].item_id},
                model=True,
            ),
            context=s.context(configs[agent_adapters.MACRO_PRODUCER]),
        )
    )
    s.ingest(
        agent_adapters.adapt_news_report(
            NEWS_REPORT,
            source=source({"news:window": notice.item_id}, model=False),  # pre-model abstention
            context=s.context(configs[agent_adapters.NEWS_PRODUCER]),
        )
    )


def _agent_interpretations(s: ScenarioStore) -> str:
    _premarket_with_macro(s)
    built = _session(s, selector="opra")
    _agent_outputs(s, built)
    _bundles_and_card(built, AS_OF)
    return "market"


SCENARIOS: tuple[SliceScenario, ...] = (
    SliceScenario(
        "ready_for_manual_review",
        "Ready evidence, synthetic qualified setup",
        "Stored adapter evidence, eligible OPRA contracts and a qualified card.",
        _ready,
    ),
    SliceScenario(
        "no_qualified_setup",
        "No qualified setup",
        "An authorized synthetic not_qualified conclusion, read back from storage.",
        _not_qualified,
    ),
    SliceScenario(
        "missing_evidence",
        "Missing required evidence",
        "Only an explicit missing-bars record was stored.",
        _missing,
    ),
    SliceScenario(
        "stale_evidence",
        "Stale evidence",
        "The stored bars are past their stale boundary at the bundle as-of.",
        _stale,
    ),
    SliceScenario(
        "ambiguous_evidence",
        "Ambiguous evidence",
        "Two stored bars compete for the same requirement; no winner.",
        _ambiguous,
    ),
    SliceScenario(
        "unresolved_critical_conflict",
        "Unresolved critical conflict",
        "A stored provider disagreement involving the required bar.",
        _critical,
    ),
    SliceScenario(
        "selector_unavailable",
        "Contract selector unavailable",
        "Requested, but no selector result was stored.",
        _selector_unavailable,
    ),
    SliceScenario(
        "research_only_contracts",
        "Research-only contracts",
        "An indicative-feed selector run, kept apart from eligible contracts.",
        _research_only,
    ),
    SliceScenario(
        "holdout_refused",
        "Holdout-restricted bar refused",
        "An in-window SPY bar was refused by the adapter before storage.",
        _holdout,
    ),
    SliceScenario(
        "point_in_time_correction",
        "Later correction, earlier bundle unchanged",
        "A later FRED vintage is stored; the earlier bundle still rebuilds exactly.",
        _correction,
    ),
    SliceScenario(
        "agent_interpretations",
        "Agent interpretations through the store",
        "Market and macro agent inference and a pre-model news abstention, stored and read back.",
        _agent_interpretations,
    ),
)
SCENARIO_IDS = tuple(s.scenario_id for s in SCENARIOS)


def run_scenario(workspace, spec: SliceScenario) -> tuple[ScenarioStore, Scenario]:
    """Run one scenario in its own temporary store and read it back."""
    store = ScenarioStore.create(store_paths(workspace, spec.scenario_id), START)
    market = spec.run(store)
    scenario = read_back(
        store, scenario_id=spec.scenario_id, title=spec.title, summary=spec.summary, market=market
    )
    return store, scenario
