"""The offline Evidence-to-Dashboard pipeline for one scenario.

One scenario runs in one temporary Evidence Store:

1. synthetic, already-validated source objects pass through the producer
   adapters (``evidence_adapters``) into Evidence Envelopes;
2. the store appends each envelope (and any conflict) under the synthetic
   registry, with every commit on a deterministic clock;
3. bundles are built point-in-time from the store's own records and
   appended only after the store rebuilds them to the same ``evb1_``;
4. setup cards are built from the stored bundle's card context and appended
   after the store re-validates them;
5. ``read_back`` reconstructs every bundle and card from storage alone,
   checks that each stored bundle still rebuilds identically, and hands the
   result to the dashboard's existing presentation adapter.

Nothing here touches the real database, a provider, a model or the network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from market_intelligence.dashboard.fixtures import (
    BundleInput,
    CardRecord,
    LaneInput,
    Scenario,
)
from market_intelligence.evidence.bundle import build_bundle, query_request_sha256
from market_intelligence.evidence.canonical import canonical_sha256
from market_intelligence.evidence.contracts import (
    EvidenceAvailability,
    EvidenceBundleManifest,
    EvidenceConflict,
    EvidenceConsumerContext,
    EvidenceEnvelopeContent,
    EvidenceItem,
    EvidenceQuery,
    compute_producer_run_key,
    seal_envelope,
)
from market_intelligence.evidence.enums import (
    AvailabilityState,
    BundlePurpose,
    ConsumerId,
    ConsumerPermission,
    EvidenceKind,
)
from market_intelligence.evidence.registry import EvidenceRegistry
from market_intelligence.evidence_adapters.common import AdapterContext
from market_intelligence.evidence_store import records as rec
from market_intelligence.evidence_store.contracts import BundleBuilderIdentity
from market_intelligence.evidence_store.enums import ActivationReason, RegistryKind
from market_intelligence.evidence_store.reads import read_card, reproduce_bundle
from market_intelligence.evidence_store.registry_files import (
    EvidenceRegistryFile,
    SetupDefinitionRegistryFile,
    render_canonical_file,
)
from market_intelligence.evidence_store.registry_service import (
    ActivationRequest,
    execute_activation,
    execute_registration,
    plan_activation,
    plan_registration,
)
from market_intelligence.evidence_store.store import EvidenceStore
from market_intelligence.integration import registry as R
from market_intelligence.integration.workspace import StorePaths
from market_intelligence.setup_cards.builder import (
    build_no_qualified_setup_card,
    build_setup_card,
)
from market_intelligence.setup_cards.contracts import CardProvenance, SetupCard
from market_intelligence.setup_cards.enums import CardKind, Lane

# Synthetic provenance values: syntactically valid, naming no real commit or
# authorization record.
SYNTHETIC_COMMIT = "0" * 40
SYNTHETIC_AUTHORIZATION = "project-state:66@" + "0" * 40
BUILDER = BundleBuilderIdentity(
    builder_version="1.0.0",
    builder_code_commit_sha=SYNTHETIC_COMMIT,
    builder_configuration_identity="cfg_none",
)
_READS = sorted(
    [
        ConsumerPermission.READ_CALCULATIONS,
        ConsumerPermission.READ_CONFLICTS,
        ConsumerPermission.READ_FACTS,
        ConsumerPermission.READ_INFERENCES,
        ConsumerPermission.READ_RESEARCH,
    ]
)


class SliceClock:
    """A deterministic clock the pipeline sets explicitly; never wall time."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def set(self, moment: datetime) -> None:
        if moment < self.now:
            raise ValueError("the slice clock never moves backwards")
        self.now = moment


def _committed_bytes_verifier(registries_root):
    """The slice's registries are synthetic temporary files, not committed
    repository files: their bytes are verified against themselves."""

    def verify(source_path: str, _commit: str) -> bytes:
        return (registries_root.parent / source_path).read_bytes()

    return verify


@dataclass
class ScenarioStore:
    """One scenario's temporary store and the identities it produced."""

    paths: StorePaths
    clock: SliceClock
    store: EvidenceStore
    registry: EvidenceRegistry
    evidence_registry_id: str
    refusals: list[str] = field(default_factory=list)
    bundles: dict[str, str] = field(default_factory=dict)
    cards: list[str] = field(default_factory=list)
    named_items: dict[str, EvidenceItem] = field(default_factory=dict)

    # --- Setup ---------------------------------------------------------------------------------

    @classmethod
    def create(cls, paths: StorePaths, start: datetime) -> ScenarioStore:
        clock = SliceClock(start)
        store = _open_store(paths, clock, initialize=True)
        registry = R.make_registry()
        evidence_id = _register(store, paths, RegistryKind.EVIDENCE_REGISTRY, registry)
        _activate(store, RegistryKind.EVIDENCE_REGISTRY, evidence_id)
        setup_id = _register(store, paths, RegistryKind.SETUP_DEFINITION_REGISTRY, R.SETUP_REGISTRY)
        _activate(store, RegistryKind.SETUP_DEFINITION_REGISTRY, setup_id)
        return cls(paths, clock, store, registry, evidence_id)

    def reopen(self) -> EvidenceStore:
        """A fresh process's view: a new store object over the same files,
        opened with its full startup verification."""
        self.store = _open_store(self.paths, self.clock, initialize=False)
        return self.store

    def at(self, moment: datetime) -> ScenarioStore:
        self.clock.set(moment)
        return self

    def context(self, configuration: str) -> AdapterContext:
        return AdapterContext(
            registry=self.registry,
            code_commit_sha=SYNTHETIC_COMMIT,
            code_tree_clean=True,
            configuration_identity=configuration,
            generated_at_utc=self.clock(),
        )

    # --- Writes --------------------------------------------------------------------------------

    def ingest(self, items: Sequence[EvidenceItem]) -> None:
        """One envelope per producer run, appended to the store."""
        items = sorted(items, key=lambda i: i.item_id)
        producer = items[0].provenance.producer_id
        only_missing = all(i.evidence_kind is EvidenceKind.MISSING_EVIDENCE for i in items)
        availability = (
            EvidenceAvailability(
                state=AvailabilityState.UNAVAILABLE,
                reason_code=items[0].availability.reason_code,
            )
            if only_missing
            else EvidenceAvailability(state=AvailabilityState.AVAILABLE)
        )
        run_as_of = min(i.effective_at_utc for i in items)
        envelope = seal_envelope(
            EvidenceEnvelopeContent(
                registry_version_id=self.evidence_registry_id,
                producer_id=producer,
                producer_version="1.0.0",
                producer_run_key=compute_producer_run_key(
                    producer_id=producer,
                    producer_version="1.0.0",
                    configuration_identity=items[0].provenance.configuration_identity,
                    run_as_of_utc=run_as_of,
                    input_digest=canonical_sha256([i.item_id for i in items]),
                ),
                run_as_of_utc=run_as_of,
                availability=availability,
                items=items,
                emitted_at_utc=self.clock(),
            )
        )
        self.store.append_envelope(envelope)

    def conflict(self, conflict: EvidenceConflict) -> None:
        self.store.append_conflict(conflict)

    def bundle(
        self, name: str, purpose: BundlePurpose, as_of: datetime, *, lookback: timedelta
    ) -> EvidenceBundleManifest:
        """A point-in-time bundle from the store's own records, appended only
        after the store rebuilds it to the same identity."""
        query = EvidenceQuery(
            effective_from_utc=as_of - lookback,
            effective_to_utc=as_of + timedelta(hours=1),
            max_entries=200,
        )
        with self.store.read_connection() as conn:
            registry, _ = self.store.evidence_registry_at(conn, as_of)
            items, conflicts, _ = self.store.records_as_of(conn, as_of, registry)
        bundle = build_bundle(
            registry=registry,
            purpose=purpose,
            as_of=as_of,
            consumer_context=EvidenceConsumerContext(
                consumer_id=ConsumerId.DASHBOARD,
                purpose=purpose,
                permissions=_READS,
                machine_decision_mode=False,
                request_sha256=query_request_sha256(query),
            ),
            query=query,
            recorded_items=items,
            recorded_conflicts=conflicts,
            built_at=self.clock(),
        )
        self.store.append_bundle(bundle, BUILDER)
        self.bundles[name] = bundle.bundle_id
        return bundle

    def setup_card(self, bundle: EvidenceBundleManifest, **kwargs) -> SetupCard:
        with self.store.read_connection() as conn:
            ctx = self.store.card_context(conn, bundle, R.SETUP_REGISTRY_ID)
        card = build_setup_card(
            ctx,
            card_kind=CardKind.SETUP,
            lane=Lane.VWAP_REVERSION,
            provenance=self._provenance(),
            **kwargs,
        )
        self.store.append_card(card)
        self.cards.append(card.card_id)
        return card

    def no_setup_card(self, bundle: EvidenceBundleManifest, **kwargs) -> SetupCard | None:
        with self.store.read_connection() as conn:
            ctx = self.store.card_context(conn, bundle, R.SETUP_REGISTRY_ID)
        result = build_no_qualified_setup_card(
            ctx, lane=Lane.VWAP_REVERSION, provenance=self._provenance(), **kwargs
        )
        if result.card is not None:
            self.store.append_card(result.card)
            self.cards.append(result.card.card_id)
        return result.card

    def _provenance(self) -> CardProvenance:
        return CardProvenance(
            card_builder_id="synthetic-slice-card-builder",
            card_builder_version="1.0.0",
            code_commit_sha=SYNTHETIC_COMMIT,
            code_tree_clean=True,
            configuration_identity="cfg_none",
            generated_at_utc=self.clock(),
        )


# --- Store and registry setup ------------------------------------------------------------------


def _open_store(paths: StorePaths, clock: SliceClock, *, initialize: bool) -> EvidenceStore:
    store = EvidenceStore(
        settings=paths.settings,
        checkpoint=paths.checkpoint,
        payload_models=dict(R.PAYLOAD_MODELS),
        clock=clock,
        synthetic_setup_definitions_for_tests=dict(R.SYNTHETIC_DEFINITIONS),
    )
    if initialize:
        store.initialize()
    else:
        store.open()
    return store


def _register(store: EvidenceStore, paths: StorePaths, kind: RegistryKind, registry) -> str:
    from market_intelligence.evidence.registry import compute_registry_version_id
    from market_intelligence.evidence_store.registry_files import (
        compute_setup_registry_version_id,
    )

    evidence = kind is RegistryKind.EVIDENCE_REGISTRY
    folder = paths.registries / ("evidence" if evidence else "setup_definitions")
    folder.mkdir(parents=True, exist_ok=True)
    wrapper = (
        EvidenceRegistryFile(
            file_format="evidence-registry-file-1",
            registry=registry,
            registry_version_id=compute_registry_version_id(registry),
        )
        if evidence
        else SetupDefinitionRegistryFile(
            file_format="setup-definition-registry-file-1",
            registry=registry,
            registry_version_id=compute_setup_registry_version_id(registry),
        )
    )
    path = folder / f"{registry.registry_label}.json"
    path.write_bytes(render_canonical_file(wrapper))
    plan = plan_registration(
        store,
        kind,
        path,
        source_commit_sha=SYNTHETIC_COMMIT,
        registries_root=paths.registries,
        source_verifier=_committed_bytes_verifier(paths.registries),
    )
    execute_registration(store, plan, plan.confirmation_token)
    return plan.record.registry_version_id


def _activate(store: EvidenceStore, kind: RegistryKind, version_id: str) -> None:
    plan = plan_activation(
        store,
        ActivationRequest(
            kind, version_id, ActivationReason.INITIAL_ACTIVATION, None, SYNTHETIC_AUTHORIZATION
        ),
    )
    execute_activation(store, plan, plan.confirmation_token)


# --- Read back -------------------------------------------------------------------------------


def _stored_bundle(store: EvidenceStore, bundle_id: str) -> BundleInput:
    """A bundle and exactly its items and conflicts, from storage alone. The
    stored bundle must still rebuild to the same identity."""
    if reproduce_bundle(store, bundle_id).bundle_id != bundle_id:
        raise ValueError("bundle_not_reproducible")
    with store.read_connection() as conn:
        row = store.load_bundle(conn, bundle_id)
        if row is None:
            raise ValueError("bundle_not_stored")
        bundle = rec.parse_bundle(row["record_json"])
        ctx = store.card_context(conn, bundle, R.SETUP_REGISTRY_ID)
    return BundleInput(
        bundle=bundle,
        items={e.item_id: ctx.items[e.item_id] for e in bundle.entries},
        conflicts=dict(ctx.conflicts),
    )


def read_back(
    scenario: ScenarioStore, *, scenario_id: str, title: str, summary: str, market: str
) -> Scenario:
    """The dashboard's scenario rebuilt entirely from the temporary store."""
    store = scenario.store
    cards = [read_card(store, card_id).card for card_id in scenario.cards]
    history = tuple(
        CardRecord(card=card, source=_stored_bundle(store, card.decision_context_bundle_id))
        for card in cards
    )
    lane_context = (
        history[-1].source if history else _stored_bundle(store, scenario.bundles["setup"])
    )
    with store.read_connection() as conn:
        registry = store.load_registry(conn, scenario.evidence_registry_id)
        setup_registry = store.load_registry(conn, R.SETUP_REGISTRY_ID)
    definitions = store.setup_definitions_for(R.SETUP_REGISTRY_ID, setup_registry)
    return Scenario(
        scenario_id=scenario_id,
        title=title,
        summary=summary,
        market=_stored_bundle(store, scenario.bundles[market]),
        premarket=_stored_bundle(store, scenario.bundles["premarket"]),
        vwap=LaneInput(context=lane_context, definitions=tuple(definitions), history=history),
        registry=registry,
        payload_models=R.PAYLOAD_MODELS,
    )
