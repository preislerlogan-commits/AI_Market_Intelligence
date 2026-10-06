"""Synthetic fixtures for the offline evidence store tests.

Temporary directories, temporary DuckDB files and temporary checkpoint state
directories only. Settings ignore ``.env``. Registries are synthetic, test-only
content written to a temporary ``registries/`` root; nothing here is a real
registry, and the setup-definition registry is the structurally empty one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import SecretStr

from market_intelligence.config.settings import Settings
from market_intelligence.evidence.contracts import (
    EvidenceAvailability,
    EvidenceEnvelope,
    EvidenceEnvelopeContent,
    EvidenceItem,
    seal_envelope,
)
from market_intelligence.evidence.enums import AvailabilityState, ConsumerId
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence_store.checkpoint import CheckpointConfig, CheckpointKeyring
from market_intelligence.evidence_store.enums import ActivationReason, CheckpointMode, RegistryKind
from market_intelligence.evidence_store.registry_files import (
    EvidenceRegistryFile,
    SetupDefinitionRegistry,
    SetupDefinitionRegistryFile,
    compute_setup_registry_version_id,
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
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import setup_card_fixtures as s

T0 = f.T0
AUTH = "project-state:63@" + "a" * 40
SOURCE_COMMIT = "c" * 40
PAYLOAD_MODELS = dict(s.PAYLOAD_MODELS)
EMPTY_SETUP_REGISTRY = SetupDefinitionRegistry(
    registry_label="setup-definitions-empty-0", definitions=[]
)
EMPTY_SETUP_REGISTRY_ID = compute_setup_registry_version_id(EMPTY_SETUP_REGISTRY)


class FakeClock:
    """A controllable UTC clock; it never moves by itself."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def set(self, value: datetime) -> None:
        self.now = value

    def advance(self, **kwargs: float) -> datetime:
        self.now = self.now + timedelta(**kwargs)
        return self.now


def keyring(active: str = "key_one", *others: str) -> CheckpointKeyring:
    ids = (active, *others)
    return CheckpointKeyring(
        active_key_id=active,
        keys={key_id: SecretStr(f"synthetic-test-secret-{key_id}-" + "x" * 32) for key_id in ids},
    )


@dataclass
class Env:
    store: EvidenceStore
    clock: FakeClock
    root: Path
    registries: Path
    settings: Settings
    config: CheckpointConfig


def make_settings(root: Path) -> Settings:
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    return Settings(_env_file=None, project_data_path=data)


def make_env(
    root: Path,
    *,
    production: bool = False,
    ring: CheckpointKeyring | None = None,
    synthetic: dict | None = None,
    start: datetime = T0 - timedelta(hours=2),
    initialize: bool = True,
) -> Env:
    settings = make_settings(root)
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    registries = root / "registries"
    mode = (
        CheckpointMode.PRODUCTION_AUTHENTICATED
        if production
        else CheckpointMode.OFFLINE_DEVELOPMENT_UNAUTHENTICATED
    )
    if production and ring is None:
        ring = keyring()
    config = CheckpointConfig(state_dir=state, mode=mode, keyring=ring)
    clock = FakeClock(start)
    store = EvidenceStore(
        settings=settings,
        checkpoint=config,
        payload_models=PAYLOAD_MODELS,
        clock=clock,
        synthetic_setup_definitions_for_tests=synthetic,
    )
    if initialize:
        store.initialize()
    return Env(store, clock, root, registries, settings, config)


def reopen(
    env: Env, *, config: CheckpointConfig | None = None, open_store: bool = True
) -> EvidenceStore:
    store = EvidenceStore(
        settings=env.settings,
        checkpoint=config or env.config,
        payload_models=PAYLOAD_MODELS,
        clock=env.clock,
        synthetic_setup_definitions_for_tests=env.store._synthetic_definitions or None,
    )
    env.store = store
    if open_store:
        store.open()
    return store


def write_registry_file(root: Path, kind: RegistryKind, registry) -> Path:
    folder = "evidence" if kind is RegistryKind.EVIDENCE_REGISTRY else "setup_definitions"
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    if kind is RegistryKind.EVIDENCE_REGISTRY:
        wrapper = EvidenceRegistryFile(
            file_format="evidence-registry-file-1",
            registry=registry,
            registry_version_id=compute_registry_version_id(registry),
        )
    else:
        wrapper = SetupDefinitionRegistryFile(
            file_format="setup-definition-registry-file-1",
            registry=registry,
            registry_version_id=compute_setup_registry_version_id(registry),
        )
    path = directory / f"{registry.registry_label}.json"
    path.write_bytes(render_canonical_file(wrapper))
    return path


def committed_bytes_verifier(root: Path):
    """Test stand-in for the git verifier: the committed bytes are the file."""

    def verify(source_path: str, _commit: str) -> bytes:
        return (root.parent / source_path).read_bytes()

    return verify


def register(env: Env, kind: RegistryKind, registry) -> str:
    path = write_registry_file(env.registries, kind, registry)
    plan = plan_registration(
        env.store,
        kind,
        path,
        source_commit_sha=SOURCE_COMMIT,
        registries_root=env.registries,
        source_verifier=committed_bytes_verifier(env.registries),
    )
    execute_registration(env.store, plan, plan.confirmation_token)
    return plan.record.registry_version_id


def activate(
    env: Env,
    kind: RegistryKind,
    version_id: str,
    *,
    reason: ActivationReason = ActivationReason.INITIAL_ACTIVATION,
    effective: datetime | None = None,
):
    plan = plan_activation(env.store, ActivationRequest(kind, version_id, reason, effective, AUTH))
    execute_activation(env.store, plan, plan.confirmation_token)
    return plan


def no_machine_decision_registry(**overrides) -> EvidenceRegistry:
    """The synthetic card registry with no consumer allowed machine-decision mode."""
    base = s.make_card_registry()
    grants = [g for g in base.consumer_grants if not g.machine_decision_mode_allowed]
    fields = {"consumer_grants": grants, **overrides}
    return s.make_card_registry(**fields)


def machine_decision_registry(**overrides) -> EvidenceRegistry:
    """The synthetic card registry, whose setup ranker holds machine-decision mode."""
    return s.make_card_registry(**overrides)


def setup_registries(env: Env, registry: EvidenceRegistry | None = None) -> tuple[str, str]:
    registry = registry or no_machine_decision_registry()
    evidence_id = register(env, RegistryKind.EVIDENCE_REGISTRY, registry)
    activate(env, RegistryKind.EVIDENCE_REGISTRY, evidence_id)
    setup_id = register(env, RegistryKind.SETUP_DEFINITION_REGISTRY, EMPTY_SETUP_REGISTRY)
    activate(env, RegistryKind.SETUP_DEFINITION_REGISTRY, setup_id)
    return evidence_id, setup_id


def envelope(
    items: list[EvidenceItem],
    registry_id: str,
    *,
    producer: str = f.BARS,
    run_key: str = "1" * 64,
    emitted: datetime | None = None,
) -> EvidenceEnvelope:
    return seal_envelope(
        EvidenceEnvelopeContent(
            registry_version_id=registry_id,
            producer_id=producer,
            producer_version="1.0.0",
            producer_run_key=run_key,
            run_as_of_utc=min(i.effective_at_utc for i in items) if items else T0,
            availability=EvidenceAvailability(state=AvailabilityState.AVAILABLE),
            items=sorted(items, key=lambda i: i.item_id),
            emitted_at_utc=emitted
            or max((i.effective_at_utc for i in items), default=T0) + timedelta(seconds=10),
        )
    )


DASHBOARD = ConsumerId.DASHBOARD
