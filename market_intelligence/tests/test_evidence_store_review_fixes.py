"""Regression tests for the final source review of the offline evidence store.
Synthetic data, temporary databases and temporary directories only."""

from __future__ import annotations

from datetime import timedelta

import duckdb
import pytest

from market_intelligence.config.settings import REPO_ROOT
from market_intelligence.evidence.bundle import build_bundle
from market_intelligence.evidence.enums import BundlePurpose
from market_intelligence.evidence_store import store_io
from market_intelligence.evidence_store.checkpoint import (
    CheckpointConfig,
    read_checkpoint,
    sign_checkpoint,
    write_checkpoint,
)
from market_intelligence.evidence_store.contracts import (
    BundleBuilderIdentity,
    StoreCheckpointContent,
)
from market_intelligence.evidence_store.enums import (
    ActivationReason,
    CheckpointMode,
    IntegrityFinding,
    RegistryKind,
    StoreState,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.recovery import BackupManifest, verify_backup
from market_intelligence.evidence_store.registry_service import (
    ActivationRequest,
    git_source_verifier,
    plan_activation,
    plan_registration,
)
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx

BUILDER = BundleBuilderIdentity(
    builder_version="1.0.0",
    builder_code_commit_sha="d" * 40,
    builder_configuration_identity="cfg_none",
)


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _stop(fn, *args, **kwargs) -> IntegrityFinding:
    with pytest.raises(IntegrityStop) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.finding


def _env(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    env.evidence_id, _ = fx.setup_registries(env)
    env.bar = f.bar_item()
    env.store.append_envelope(fx.envelope([env.bar], env.evidence_id))
    return env


# --- Reconciliation can never lift an integrity stop -----------------------------------------


def test_reconcile_refuses_in_read_refused_mode(tmp_path):
    env = _env(tmp_path)
    env.store.state = StoreState.READ_REFUSED  # as after any integrity stop
    assert _reason(env.store.reconcile) == "store_read_refused"
    assert env.store.state is StoreState.READ_REFUSED


def test_reconcile_refuses_during_unfinished_recovery(tmp_path):
    env = _env(tmp_path)
    env.store.state = StoreState.RECOVERY_IN_PROGRESS
    assert _reason(env.store.reconcile) == "recovery_in_progress"


def test_reconcile_checks_the_database_identity(tmp_path):
    env = _env(tmp_path)
    checkpoint, _ = read_checkpoint(env.config)
    foreign = sign_checkpoint(
        StoreCheckpointContent(
            store_instance_id="f" * 32,
            commit_seq=checkpoint.commit_seq,
            commit_digest=checkpoint.commit_digest,
            created_at_utc=checkpoint.created_at_utc,
        ),
        env.config,
    )
    write_checkpoint(env.config, foreign)
    assert _stop(env.store.reconcile) is IntegrityFinding.CHECKPOINT_STORE_MISMATCH
    assert env.store.state is StoreState.READ_REFUSED


def test_second_identity_row_is_an_integrity_stop(tmp_path):
    env = _env(tmp_path)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "INSERT INTO store_instance SELECT ?, initialized_at_utc, record_json, "
            "record_sha256, commit_seq, recorded_at_utc, row_sha256 FROM store_instance",
            ["e" * 32],
        )
    assert _stop(fx.reopen, env) is IntegrityFinding.AUTHORITATIVE_CHAIN_AMBIGUITY


# --- Duplicates are idempotent only for identical canonical content ---------------------------


def _bundle(env):
    with env.store.read_connection() as conn:
        registry = env.store.load_registry(conn, env.evidence_id)
        items, conflicts, _ = env.store.records_as_of(conn, f.T0 + timedelta(minutes=1), registry)
    q = f.query()
    return build_bundle(
        registry=registry,
        purpose=BundlePurpose.LIVE_MARKET_STATE,
        as_of=f.T0 + timedelta(minutes=1),
        consumer_context=f.context(q),
        query=q,
        recorded_items=items,
        recorded_conflicts=conflicts,
        built_at=f.T0 + timedelta(minutes=1, seconds=1),
    )


def test_bundle_duplicate_with_different_stored_identity_is_a_stop(tmp_path):
    env = _env(tmp_path)
    env.clock.set(f.T0 + timedelta(minutes=5))
    bundle = _bundle(env)
    env.store.append_bundle(bundle, BUILDER)
    env.store.append_bundle(bundle, BUILDER)  # identical: idempotent
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE evidence_bundles SET record_json = replace(record_json, "
            "'\"machine_decision_ready\":false', '\"machine_decision_ready\":true')"
        )
    with pytest.raises(IntegrityStop):
        env.store.append_bundle(bundle, BUILDER)
    assert env.store.state is StoreState.READ_REFUSED


def test_registration_duplicate_with_different_content_is_a_stop(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    registry = fx.no_machine_decision_registry()
    version = fx.register(env, RegistryKind.EVIDENCE_REGISTRY, registry)
    with duckdb.connect(str(env.store.database_path)) as conn:
        conn.execute(
            "UPDATE registry_versions SET record_json = replace(record_json, "
            "'synthetic-test', 'synthetic-tesT') WHERE registry_version_id = ?",
            [version],
        )
    with pytest.raises(IntegrityStop):
        fx.register(env, RegistryKind.EVIDENCE_REGISTRY, registry)


# --- Registry verifier and pending activations -------------------------------------------------


def test_commit_sha_is_validated_before_any_verifier_runs(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    path = fx.write_registry_file(
        env.registries, RegistryKind.EVIDENCE_REGISTRY, fx.no_machine_decision_registry()
    )
    called = []

    def verifier(*args):
        called.append(args)
        return b""

    for bad in ("--output=x", "HEAD", "c" * 39, "C" * 40):
        assert (
            _reason(
                plan_registration,
                env.store,
                RegistryKind.EVIDENCE_REGISTRY,
                path,
                source_commit_sha=bad,
                registries_root=env.registries,
                source_verifier=verifier,
            )
            == "registry_not_committed"
        )
    assert called == []
    assert _reason(git_source_verifier, "registries/evidence/x.json", "--output=x") == (
        "registry_not_committed"
    )
    assert _reason(git_source_verifier, "../secret.json", "c" * 40) == "registry_not_committed"


def test_no_activation_while_a_future_activation_is_pending(tmp_path):
    env = fx.make_env(tmp_path, production=True)
    first = fx.register(env, RegistryKind.EVIDENCE_REGISTRY, fx.no_machine_decision_registry())
    fx.activate(env, RegistryKind.EVIDENCE_REGISTRY, first)
    second = fx.register(
        env,
        RegistryKind.EVIDENCE_REGISTRY,
        fx.no_machine_decision_registry(registry_label="synthetic-test-2"),
    )
    env.clock.advance(minutes=1)
    fx.activate(
        env,
        RegistryKind.EVIDENCE_REGISTRY,
        second,
        reason=ActivationReason.VERSION_UPGRADE,
        effective=env.clock() + timedelta(hours=1),
    )
    md = fx.register(
        env,
        RegistryKind.EVIDENCE_REGISTRY,
        fx.machine_decision_registry(registry_label="synthetic-md"),
    )
    env.clock.advance(minutes=1)
    plan = plan_activation(
        env.store,
        ActivationRequest(
            RegistryKind.EVIDENCE_REGISTRY, md, ActivationReason.VERSION_UPGRADE, None, fx.AUTH
        ),
    )
    assert plan.evaluation.refusal == "activation_pending"


# --- Real-data isolation at every entry point -----------------------------------------------


def test_checkpoint_functions_refuse_real_data_and_repository_directories():
    for directory in (store_io.REAL_DATA_DIR, REPO_ROOT / "market_intelligence"):
        config = CheckpointConfig(directory, CheckpointMode.OFFLINE_DEVELOPMENT_UNAUTHENTICATED)
        assert _reason(read_checkpoint, config) == "checkpoint_path_unsafe"
        assert _reason(write_checkpoint, config, None) == "checkpoint_path_unsafe"


def test_backup_verification_refuses_the_real_database_before_reading(tmp_path, monkeypatch):
    env = fx.make_env(tmp_path)
    from market_intelligence.evidence_store import recovery

    def forbidden(_path):
        raise AssertionError("the real database must not be read")

    monkeypatch.setattr(recovery, "_file_sha256", forbidden)
    manifest = BackupManifest(store_io.REAL_DATABASE_PATH, "0" * 64, 1, "0" * 64, "0" * 32)
    assert _reason(verify_backup, env.store, manifest) == "real_database_refused"


def test_configured_project_data_directory_is_also_refused(tmp_path, monkeypatch):
    configured = tmp_path / "configured_real_data"
    configured.mkdir()
    monkeypatch.setenv("PROJECT_DATA_PATH", str(configured))
    store_io.real_data_directories.cache_clear()
    try:
        assert _reason(
            store_io.refuse_real_database_path, configured / "market_intelligence.duckdb"
        ) == ("real_database_refused")
        assert store_io.refuse_real_database_path(tmp_path / "other" / "x.duckdb")
    finally:
        monkeypatch.delenv("PROJECT_DATA_PATH")
        store_io.real_data_directories.cache_clear()
