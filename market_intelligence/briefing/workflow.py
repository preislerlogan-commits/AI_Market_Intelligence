"""A one-time macro-and-news market-context briefing.

Flow (every step reuses an existing component)::

    FRED + Alpaca News (injected sources)
      -> temporary source database (existing storage repositories)
      -> Macro Analyst and News Analyst (existing agents, existing snapshot
         builders reading that temporary database, injected model client)
      -> FRED and news evidence adapters, agent evidence adapters
      -> temporary Evidence Store (the offline slice's store path)
      -> briefing bundle (``premarket_briefing``), reproduced from storage
      -> assistant answers and a dashboard view built from read-back records

**Excluded:** SPY prices and bars, the Market Evidence Agent, VWAP and trend
detection, setup qualification, contract selection, predictions,
probabilities, recommendations and trading. Nothing here imports them.

**Live execution is not authorized.** A run that would contact a provider or a
model (``BriefingDependencies(live=True)``) requires a ``LiveAuthorization``
whose record ID is listed in ``AUTHORIZED_LIVE_RECORDS``, which is empty.
``live_dependencies`` checks that authorization before it builds any client,
and then the non-network ``preflight``. Offline runs inject fakes; passing a
real connector or model client without ``live=True`` is refused.

**Fails closed** (``BriefingRefusal`` with a bounded reason) on: missing
authorization or credentials; a non-temporary, repository or real data
location; malformed source or analyst output; directional analyst output;
an analyst citation that does not resolve to stored evidence; a post-model
abstention (not representable as missing evidence); and stale evidence cited
by an interpretation. The temporary workspace is always deleted.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from market_intelligence.agents.macro_analyst import AGENT_INSTRUCTIONS as MACRO_INSTRUCTIONS
from market_intelligence.agents.macro_analyst import MacroAnalyst, MacroAnalystAgentError
from market_intelligence.agents.news_analyst import AGENT_INSTRUCTIONS as NEWS_INSTRUCTIONS
from market_intelligence.agents.news_analyst import NewsAnalyst, NewsAnalystAgentError
from market_intelligence.assistant import explain as assistant
from market_intelligence.config.settings import Settings
from market_intelligence.dashboard.adapter import present_briefing
from market_intelligence.dashboard.fixtures import BundleInput
from market_intelligence.dashboard.validation import DashboardInputError
from market_intelligence.dashboard.views import DashboardModel
from market_intelligence.evidence.canonical import canonical_sha256
from market_intelligence.evidence.contracts import (
    DocumentReference,
    EvidenceItem,
    ModelRunReference,
    ProviderRequestReference,
)
from market_intelligence.evidence.enums import (
    BundlePurpose,
    EndpointClass,
    EvidenceKind,
    FreshnessState,
    LocatorKind,
    ResponseClass,
)
from market_intelligence.evidence.errors import EvidenceValidationError
from market_intelligence.evidence_adapters import agents as agent_adapters
from market_intelligence.evidence_adapters import clock as clock_adapter
from market_intelligence.evidence_adapters import fred as fred_adapter
from market_intelligence.evidence_adapters import news as news_adapter
from market_intelligence.evidence_adapters.clock import ClockHealthReading
from market_intelligence.evidence_store.errors import IntegrityStop
from market_intelligence.integration import registry as R
from market_intelligence.integration.pipeline import ScenarioStore, _stored_bundle
from market_intelligence.integration.workspace import (
    offline_settings,
    store_paths,
    temporary_workspace,
)
from market_intelligence.market_features import macro_evidence, news_evidence
from market_intelligence.market_features.macro_evidence import MacroEvidenceBuilder
from market_intelligence.market_features.news_evidence import NewsEvidenceBuilder
from market_intelligence.model_clients.openai_structured import OpenAIStructuredError
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_observation_repository import MacroObservationRepository
from market_intelligence.storage.macro_series_metadata_repository import (
    MacroSeriesMetadataRepository,
)
from market_intelligence.storage.news_repository import NewsArticleRepository

from . import registry as briefing_registry

# No live run is authorized. A future authorization is recorded in
# PROJECT_STATE.md first, then its record ID is added here in its own change.
AUTHORIZED_LIVE_RECORDS: frozenset[str] = frozenset()
BRIEFING_LOOKBACK = timedelta(days=399)  # the bundle window stays within 400 days
_READY = (FreshnessState.CURRENT, FreshnessState.TIMELESS)


class BriefingRefusal(Exception):
    """The briefing refused to run or to finish. One bounded reason token;
    never a credential, path, payload or raw provider text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- Request, preflight and authorization --------------------------------------------------------


@dataclass(frozen=True)
class BriefingRequest:
    series_ids: tuple[str, ...] = macro_evidence.DEFAULT_SERIES_IDS
    news_symbol: str = "SPY"  # news linked to the symbol; no price evidence is read
    news_limit: int = 10
    recent_observations_limit: int = macro_evidence.DEFAULT_RECENT_OBSERVATIONS_LIMIT
    observation_lookback_days: int = 365


@dataclass(frozen=True)
class PreflightReport:
    """Whether a live run could start. Booleans and names only; never a
    credential value. No network request is made."""

    fred_configured: bool
    alpaca_configured: bool
    openai_configured: bool
    openai_model_configured: bool
    missing: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.missing


def preflight(settings: Settings) -> PreflightReport:
    status = settings.provider_status()
    checks = {
        "fred_api_key": status["fred"],
        "alpaca_api_key_and_secret": status["alpaca"],
        "openai_api_key": status["openai"],
        "openai_model": bool(settings.openai_model and settings.openai_model.strip()),
    }
    return PreflightReport(
        fred_configured=checks["fred_api_key"],
        alpaca_configured=checks["alpaca_api_key_and_secret"],
        openai_configured=checks["openai_api_key"],
        openai_model_configured=checks["openai_model"],
        missing=tuple(sorted(name for name, ok in checks.items() if not ok)),
    )


@dataclass(frozen=True)
class LiveAuthorization:
    """A reference to a recorded authorization for one live briefing run."""

    record_id: str


def require_live_authorization(authorization: Any) -> None:
    if (
        not isinstance(authorization, LiveAuthorization)
        or authorization.record_id not in AUTHORIZED_LIVE_RECORDS
    ):
        raise BriefingRefusal("live_run_not_authorized")


# --- Injectable dependencies ---------------------------------------------------------------------


class BriefingSources(Protocol):
    """Read-only retrieval. Live: the existing connectors. Tests: fakes."""

    def news(self, symbol: str, limit: int) -> Sequence[Any]: ...

    def fred_observations(self, series_id: str, observation_start: str) -> Sequence[Any]: ...

    def fred_metadata(self, series_id: str) -> Any: ...


@dataclass(frozen=True)
class BriefingDependencies:
    sources: BriefingSources
    model_client: Any  # anything with the structured client's ``generate``
    clock: Callable[[], datetime]
    # A measured clock-health reading. Without one every freshness state is
    # unknown and the briefing refuses (stale_evidence_cited).
    clock_health: Callable[[], ClockHealthReading]
    live: bool = False
    authorization: LiveAuthorization | None = None


class _LiveSources:
    """The existing read-only connectors. Built only by ``live_dependencies``."""

    def __init__(self, settings: Settings) -> None:
        from market_intelligence.data_connectors.alpaca_news import AlpacaNewsClient
        from market_intelligence.data_connectors.fred_macro_data import FredMacroDataClient

        self._news = AlpacaNewsClient(settings=settings)
        self._fred = FredMacroDataClient(settings=settings)

    def news(self, symbol: str, limit: int) -> Sequence[Any]:
        return self._news.get_news(symbol, limit=limit)

    def fred_observations(self, series_id: str, observation_start: str) -> Sequence[Any]:
        return self._fred.get_observations(series_id, observation_start)

    def fred_metadata(self, series_id: str) -> Any:
        return self._fred.get_series_metadata(series_id)


def live_dependencies(
    settings: Settings,
    authorization: Any,
    *,
    clock: Callable[[], datetime],
    clock_health: Callable[[], ClockHealthReading],
) -> BriefingDependencies:
    """The live connectors and model client, only for an authorized run with
    every credential configured. The authorization is checked first; no
    client is built and nothing is requested before both checks pass."""
    require_live_authorization(authorization)
    if not preflight(settings).ready:
        raise BriefingRefusal("credentials_missing")
    from market_intelligence.model_clients.openai_structured import OpenAIStructuredClient

    return BriefingDependencies(
        sources=_LiveSources(settings),
        model_client=OpenAIStructuredClient(settings=settings),
        clock=clock,
        clock_health=clock_health,
        live=True,
        authorization=authorization,
    )


def _refuse_unauthorized_live_clients(deps: BriefingDependencies) -> None:
    from market_intelligence.data_connectors.alpaca_news import AlpacaNewsClient
    from market_intelligence.data_connectors.fred_macro_data import FredMacroDataClient
    from market_intelligence.model_clients.openai_structured import OpenAIStructuredClient

    live_types = (AlpacaNewsClient, FredMacroDataClient, OpenAIStructuredClient, _LiveSources)
    if isinstance(deps.sources, live_types) or isinstance(deps.model_client, live_types):
        raise BriefingRefusal("live_client_requires_authorization")


# --- Result --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BriefingResult:
    """In-memory views only; the temporary workspace is already deleted."""

    registry_label: str
    bundle_id: str
    model: DashboardModel
    answers: Mapping[str, assistant.Answer]
    agent_kinds: Mapping[str, tuple[str, ...]]
    items: Mapping[str, EvidenceItem]  # the briefing bundle's items, read back
    workspace_removed: bool = field(default=True)


# --- The run -------------------------------------------------------------------------------------


def run_briefing(
    request: BriefingRequest,
    deps: BriefingDependencies,
    *,
    workspace_parent: Path | None = None,
) -> BriefingResult:
    """Run one briefing in a fresh temporary workspace, always removed."""
    if deps.live:
        require_live_authorization(deps.authorization)
    else:
        _refuse_unauthorized_live_clients(deps)
    label = briefing_registry.LIVE_LABEL if deps.live else briefing_registry.SYNTHETIC_LABEL
    now = deps.clock()
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise BriefingRefusal("clock_invalid")
    now = now.astimezone(UTC)
    try:
        with temporary_workspace(workspace_parent) as workspace:
            result = _run(request, deps, workspace, label, now)
    except BriefingRefusal:
        raise
    except (MacroAnalystAgentError, NewsAnalystAgentError) as error:
        raise BriefingRefusal(f"analyst_{error.category}") from None
    except OpenAIStructuredError:
        raise BriefingRefusal("model_request_failed") from None
    except EvidenceValidationError as error:
        raise BriefingRefusal(error.reason) from None
    except IntegrityStop as error:
        raise BriefingRefusal(error.reason) from None
    return result


def _sha(value: Any) -> str:
    return canonical_sha256(value)


def _request_reference(endpoint: EndpointClass, provider: str, params: Any, at: datetime):
    return ProviderRequestReference(
        reference_type="provider_request",
        provider=provider,
        endpoint_class=endpoint,
        feed=None,
        request_params_sha256=_sha(params),
        requested_at_utc=at,
        response_class=ResponseClass.OK,
    )


def _model_run(
    agent: str, instructions: str, package: Any, report: Any, metadata: Any
) -> ModelRunReference | None:
    if metadata is None:
        return None  # a pre-model abstention: no model was called
    response = metadata.response_id
    return ModelRunReference(
        reference_type="model_run",
        provider="openai",
        model_name=metadata.model,
        request_schema_id=f"{agent}.analysis.v1",
        prompt_template_id=f"{agent}.instructions.v1",
        prompt_template_sha256=hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
        evidence_package_sha256=_sha(package),
        output_sha256=_sha(report.model_dump(mode="json")),
        response_id_sha256=(
            hashlib.sha256(response.encode("utf-8")).hexdigest() if response else None
        ),
        input_tokens=metadata.input_tokens,
        output_tokens=metadata.output_tokens,
    )


def _run(
    request: BriefingRequest,
    deps: BriefingDependencies,
    workspace: Path,
    label: str,
    now: datetime,
) -> BriefingResult:
    start = (now.date() - timedelta(days=request.observation_lookback_days)).isoformat()

    # 1. Retrieval (read-only, injected) and persistence into a temporary
    #    source database through the existing repositories.
    news_items = list(deps.sources.news(request.news_symbol, request.news_limit))
    fred: dict[str, tuple[list[Any], Any]] = {
        series_id: (
            list(deps.sources.fred_observations(series_id, start)),
            deps.sources.fred_metadata(series_id),
        )
        for series_id in request.series_ids
    }
    source_settings = offline_settings(workspace / "source")
    DuckDBManager(settings=source_settings).initialize()
    if news_items:
        NewsArticleRepository(settings=source_settings).store_news_items(news_items)
    for observations, metadata in fred.values():
        MacroSeriesMetadataRepository(settings=source_settings).store_metadata(metadata)
        if observations:
            MacroObservationRepository(settings=source_settings).store_observations(observations)

    # 2. The existing analysts over that temporary database.
    clock = lambda: now  # noqa: E731 - one fixed as-of for the whole briefing
    macro = MacroAnalyst(
        settings=source_settings,
        evidence_builder=MacroEvidenceBuilder(settings=source_settings, clock=clock),
        model_client=deps.model_client,
    )
    news = NewsAnalyst(
        settings=source_settings,
        evidence_builder=NewsEvidenceBuilder(settings=source_settings, clock=clock),
        model_client=deps.model_client,
    )
    macro_package = macro.build_preflight(
        request.series_ids, request.recent_observations_limit
    ).evidence_package
    news_package = news.build_preflight(request.news_symbol, limit=request.news_limit)
    macro_run = macro.run(request.series_ids, request.recent_observations_limit)
    news_run = news.run(request.news_symbol, limit=request.news_limit)

    # 3. Source evidence into a temporary Evidence Store.
    s = ScenarioStore.create(
        store_paths(workspace, "store"), now, briefing_registry.make_briefing_registry(label)
    )
    s.ingest(
        [
            clock_adapter.adapt_clock_health(
                deps.clock_health(),
                reference=DocumentReference(
                    reference_type="document",
                    locator_kind=LocatorKind.REPO_DOCUMENT,
                    document_path="docs/EVIDENCE_REGISTRY.md",
                    document_commit_sha="0" * 40,
                    section_token="clock",
                ),
                context=s.context(clock_adapter.CLOCK_CONFIGURATION),
            )
        ]
    )
    macro_keys: dict[str, str] = {}
    metadata_items = []
    observation_items = []
    for series_id, (observations, metadata) in fred.items():
        metadata_item = fred_adapter.adapt_fred_series_metadata(
            metadata,
            reference=_request_reference(
                EndpointClass.FRED_SERIES_METADATA, "fred", {"series": series_id}, now
            ),
            context=s.context(fred_adapter.METADATA_CONFIGURATION),
        )
        metadata_items.append(metadata_item)
        # What the analyst saw for this series; an abstention can cite it even
        # when every observation is too old to be in the briefing window.
        macro_keys[f"fred_metadata:{series_id}"] = metadata_item.item_id
        for observation in observations:
            item = fred_adapter.adapt_fred_observation(
                observation,
                series_metadata=metadata,
                reference=_request_reference(
                    EndpointClass.FRED_SERIES_OBSERVATIONS,
                    "fred",
                    {"series": series_id, "start": start},
                    now,
                ),
                context=s.context(fred_adapter.OBSERVATIONS_CONFIGURATION),
                query_sha256=_sha({"series": series_id, "start": start}),
            )
            observation_items.append(item)
            key = macro_evidence._evidence_id(
                "fred",
                series_id,
                date.fromisoformat(observation.observation_date),
                date.fromisoformat(observation.realtime_start),
                date.fromisoformat(observation.realtime_end),
            )
            macro_keys[key] = item.item_id
    s.ingest(metadata_items)
    if observation_items:
        s.ingest(observation_items)
    news_keys: dict[str, str] = {}
    if news_items:
        news_facts = [
            news_adapter.adapt_news_publication(
                article,
                reference=_request_reference(
                    EndpointClass.ALPACA_NEWS,
                    "alpaca",
                    {"symbol": request.news_symbol, "limit": request.news_limit},
                    now,
                ),
                context=s.context(news_adapter.NEWS_CONFIGURATION),
            )
            for article in news_items
        ]
        s.ingest(news_facts)
        for article, item in zip(news_items, news_facts, strict=True):
            news_keys[news_evidence._evidence_id("alpaca", article.provider_article_id)] = (
                item.item_id
            )
    else:
        notice = news_adapter.missing_news(
            checked_at=now,
            query_sha256=_sha({"symbol": request.news_symbol, "limit": request.news_limit}),
            context=s.context(news_adapter.NEWS_CONFIGURATION),
        )
        s.ingest([notice])
        news_keys["news:no_articles_returned"] = notice.item_id

    # 4. The agents' outputs, against the source bundle read back from storage.
    s.at(now + timedelta(seconds=2))
    source_bundle = s.bundle(
        "source",
        BundlePurpose.PREMARKET_BRIEFING,
        now + timedelta(seconds=1),
        lookback=BRIEFING_LOOKBACK,
    )
    stored_source = _stored_bundle(s.store, source_bundle.bundle_id)
    agent_at = now + timedelta(seconds=3)

    def source(keys: Mapping[str, str], model_run) -> agent_adapters.AgentSourceContext:
        return agent_adapters.AgentSourceContext(
            bundle=stored_source.bundle,
            items=stored_source.items,
            citation_keys={k: v for k, v in keys.items() if v in stored_source.items},
            agent_generated_at_utc=agent_at,
            model_run=model_run,
        )

    s.at(now + timedelta(seconds=4))
    configs = agent_adapters.AGENT_CONFIGURATIONS
    macro_items = agent_adapters.adapt_macro_report(
        macro_run.report,
        source=source(
            macro_keys,
            _model_run(
                "macro_analyst",
                MACRO_INSTRUCTIONS,
                macro_package,
                macro_run.report,
                macro_run.model_metadata,
            ),
        ),
        context=s.context(configs[agent_adapters.MACRO_PRODUCER]),
    )
    news_items_evidence = agent_adapters.adapt_news_report(
        news_run.report,
        source=source(
            news_keys,
            _model_run(
                "news_analyst",
                NEWS_INSTRUCTIONS,
                news_package.evidence_package,
                news_run.report,
                news_run.model_metadata,
            ),
        ),
        context=s.context(configs[agent_adapters.NEWS_PRODUCER]),
    )
    s.ingest(macro_items)
    s.ingest(news_items_evidence)

    # 5. The briefing bundle, reproduced from storage, and its views.
    s.at(now + timedelta(seconds=6))
    briefing = s.bundle(
        "briefing",
        BundlePurpose.PREMARKET_BRIEFING,
        now + timedelta(seconds=5),
        lookback=BRIEFING_LOOKBACK,
    )
    stored = _stored_bundle(s.store, briefing.bundle_id)
    _refuse_stale_interpretations(stored)
    with s.store.read_connection() as conn:
        registry = s.store.load_registry(conn, s.evidence_registry_id)
    selection = assistant.BriefingSelection(
        bundle=stored.bundle, items=stored.items, conflicts=stored.conflicts, registry=registry
    )
    try:
        answers = {
            q.value: assistant.answer_briefing(
                selection, q.value, bundle_id=stored.bundle.bundle_id
            )
            for q in assistant.BRIEFING_QUESTIONS
        }
        unavailable = None
    except assistant.AssistantRefusal as refusal:
        # The assistant reads synthetic registries only; a live briefing's
        # registry is not synthetic, so its assistant view stays unavailable.
        answers, unavailable = {}, refusal.reason
    try:
        model = present_briefing(
            BundleInput(stored.bundle, stored.items, stored.conflicts),
            registry,
            answers,
            payload_models=R.PAYLOAD_MODELS,
            assistant_unavailable=unavailable,
        )
    except DashboardInputError as error:
        raise BriefingRefusal(error.reason) from None
    agent_kinds = {
        agent: tuple(
            sorted(
                i.evidence_kind.value
                for i in stored.items.values()
                if i.provenance.producer_id == agent
            )
        )
        for agent in (agent_adapters.MACRO_PRODUCER, agent_adapters.NEWS_PRODUCER)
    }
    return BriefingResult(
        registry_label=label,
        bundle_id=stored.bundle.bundle_id,
        model=model,
        answers=answers,
        agent_kinds=agent_kinds,
        items=dict(stored.items),
    )


def _refuse_stale_interpretations(stored: BundleInput) -> None:
    """An interpretation may never rest on evidence that is not current at
    the briefing's as-of time."""
    entries = {e.item_id: e for e in stored.bundle.entries}
    for item in stored.items.values():
        if item.evidence_kind is not EvidenceKind.CURRENT_INFERENCE:
            continue
        for parent in item.provenance.parent_evidence_ids:
            entry = entries.get(parent)
            if entry is None:
                raise BriefingRefusal("citation_outside_briefing")
            if entry.freshness.state not in _READY:
                raise BriefingRefusal("stale_evidence_cited")
