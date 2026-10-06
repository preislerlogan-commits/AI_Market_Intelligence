"""Anti-rollback checkpoint: per-commit advancement, reconciliation, authentication,
path safety, valid-prefix rollback detection and key rotation. Temporary
databases and temporary state directories only."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta

import pytest
from pydantic import SecretStr

from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence.enums import ConsumerId
from market_intelligence.evidence_store import checkpoint as checkpoint_module
from market_intelligence.evidence_store import recovery as recovery_module
from market_intelligence.evidence_store import store as store_module
from market_intelligence.evidence_store.checkpoint import (
    CHECKPOINT_FILENAME,
    CheckpointConfig,
    CheckpointKeyring,
    read_checkpoint,
    sign_checkpoint,
    write_checkpoint,
)
from market_intelligence.evidence_store.contracts import StoreCheckpointContent
from market_intelligence.evidence_store.enums import (
    CheckpointMode,
    IntegrityFinding,
    StoreState,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.reads import consumer_read_card, reproduce_bundle
from market_intelligence.evidence_store.recovery import rotate_checkpoint_key
from market_intelligence.evidence_store.store import EvidenceStore
from market_intelligence.evidence_store.store_io import connect, select_rows
from market_intelligence.tests import evidence_fixtures as f
from market_intelligence.tests import evidence_store_fixtures as fx


def _reason(fn, *args, **kwargs) -> str:
    with pytest.raises(StoreRefusal) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.reason


def _stop(fn, *args, **kwargs) -> IntegrityFinding:
    with pytest.raises(IntegrityStop) as excinfo:
        fn(*args, **kwargs)
    return excinfo.value.finding


def _env(tmp_path, ring=None):
    env = fx.make_env(tmp_path, production=True, ring=ring)
    env.evidence_id, _ = fx.setup_registries(env)
    return env


def _append(env, minutes=0, key="1"):
    env.store.append_envelope(
        fx.envelope(
            [f.bar_item(f.T0 - timedelta(minutes=minutes))], env.evidence_id, run_key=key * 64
        )
    )


def _tip(env):
    with connect(env.store.database_path, read_only=True) as conn:
        return select_rows(conn, "SELECT max(commit_seq) AS n FROM store_commits")[0]["n"]


def _events(env, event_type):
    with connect(env.store.database_path, read_only=True) as conn:
        return select_rows(
            conn, "SELECT * FROM store_audit_events WHERE event_type = ?", [event_type]
        )


def _fail_writes(monkeypatch, *, calls_to_fail):
    """Make chosen checkpoint writes fail, by call number (1-based)."""
    real = store_module.write_checkpoint
    counter = {"n": 0}

    def flaky(config, checkpoint):
        counter["n"] += 1
        if counter["n"] in calls_to_fail:
            raise StoreRefusal("checkpoint_write_failed")
        real(config, checkpoint)

    monkeypatch.setattr(store_module, "write_checkpoint", flaky)
    return counter


# --- Per-commit advancement and failure ----------------------------------------------------------


def test_checkpoint_advances_after_every_commit(tmp_path):
    env = _env(tmp_path)
    for i in range(3):
        _append(env, minutes=i, key=str(i + 1))
        checkpoint, _ = read_checkpoint(env.config)
        assert checkpoint.commit_seq == _tip(env)
    assert env.store.checkpoint_equals_tip()


def test_commit_survives_a_failed_checkpoint_write_and_blocks_writes(tmp_path, monkeypatch):
    env = _env(tmp_path)
    before = _tip(env)
    _fail_writes(monkeypatch, calls_to_fail={1})
    assert _reason(_append, env) == "workflow_incomplete"
    assert _tip(env) == before + 1  # preserved, never rolled back or duplicated
    assert env.store.state is StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
    assert _reason(_append, env, 5, "2") == "checkpoint_reconciliation_required"
    assert _tip(env) == before + 1
    # Reads of stored records remain safe (the checkpoint was confirmed earlier) ...
    assert _reason(reproduce_bundle, env.store, "evb1_" + "0" * 64) == "bundle_not_stored"
    # ... but consumer reads, which must write an access event, wait.
    assert _reason(
        consumer_read_card, env.store, "scd1_" + "0" * 64, consumer=ConsumerId.DASHBOARD
    ) == ("checkpoint_reconciliation_required")


def test_reconciliation_follows_the_eight_steps(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _fail_writes(monkeypatch, calls_to_fail={1})
    with pytest.raises(StoreRefusal):
        _append(env)
    tip = _tip(env)
    monkeypatch.undo()
    assert env.store.reconcile() is StoreState.SERVING
    events = _events(env, "checkpoint_reconciled")
    assert len(events) == 1 and events[0]["reference_commit_seq"] == tip
    assert _tip(env) == tip + 1 and env.store.checkpoint_equals_tip()
    _append(env, 5, "2")  # writes resume


def test_second_checkpoint_failure_never_duplicates_the_audit_event(tmp_path, monkeypatch):
    env = _env(tmp_path)
    _fail_writes(monkeypatch, calls_to_fail={1})
    with pytest.raises(StoreRefusal):
        _append(env)
    monkeypatch.undo()
    _fail_writes(monkeypatch, calls_to_fail={2})  # step 6 fails
    assert _reason(env.store.reconcile) == "workflow_incomplete"
    assert env.store.state is StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
    assert len(_events(env, "checkpoint_reconciled")) == 1
    assert not env.store.checkpoint_equals_tip()
    assert _reason(_append, env, 5, "2") == "checkpoint_reconciliation_required"
    monkeypatch.undo()
    assert env.store.reconcile() is StoreState.SERVING
    assert len(_events(env, "checkpoint_reconciled")) == 1
    assert env.store.checkpoint_equals_tip()


def test_restart_after_the_reconciliation_event_completes_without_a_duplicate(
    tmp_path, monkeypatch
):
    env = _env(tmp_path)
    _fail_writes(monkeypatch, calls_to_fail={1})
    with pytest.raises(StoreRefusal):
        _append(env)
    monkeypatch.undo()
    _fail_writes(monkeypatch, calls_to_fail={2})
    with pytest.raises(StoreRefusal):
        env.store.reconcile()
    monkeypatch.undo()
    store = fx.reopen(env)  # startup finds the database ahead of a valid checkpoint
    assert store.state is StoreState.SERVING
    assert len(_events(env, "checkpoint_reconciled")) == 1
    assert store.checkpoint_equals_tip()


# --- Authentication policy -----------------------------------------------------------------------


def test_production_requires_a_key_and_never_falls_back(tmp_path):
    settings = fx.make_settings(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    with pytest.raises(StoreRefusal) as excinfo:
        EvidenceStore(
            settings=settings,
            checkpoint=CheckpointConfig(state, CheckpointMode.PRODUCTION_AUTHENTICATED, None),
            payload_models=fx.PAYLOAD_MODELS,
        )
    assert excinfo.value.reason == "checkpoint_key_missing"
    assert _reason(
        CheckpointKeyring.from_settings,
        Settings(_env_file=None, project_data_path=tmp_path / "data"),
    ) == ("checkpoint_key_missing")
    with pytest.raises(StoreRefusal):  # too short to be a key
        CheckpointKeyring(active_key_id="key_one", keys={"key_one": SecretStr("short")})


def test_keyring_from_settings_uses_secret_values(tmp_path):
    settings = Settings(
        _env_file=None,
        project_data_path=tmp_path / "data",
        evidence_store_checkpoint_key_id="key_one",
        evidence_store_checkpoint_hmac_key=SecretStr("s" * 40),
    )
    ring = CheckpointKeyring.from_settings(settings)
    assert ring.active_key_id == "key_one"
    assert "s" * 40 not in repr(ring)


def test_unknown_key_id_and_failed_hmac_fail_closed(tmp_path):
    env = _env(tmp_path)
    unknown = CheckpointConfig(
        env.config.state_dir, CheckpointMode.PRODUCTION_AUTHENTICATED, fx.keyring("key_two")
    )
    assert _reason(fx.reopen, env, config=unknown) == "checkpoint_key_id_unknown"
    assert env.store.state is StoreState.NOT_SERVICEABLE
    forged_ring = CheckpointKeyring(active_key_id="key_one", keys={"key_one": SecretStr("w" * 40)})
    forged = CheckpointConfig(
        env.config.state_dir, CheckpointMode.PRODUCTION_AUTHENTICATED, forged_ring
    )
    assert _stop(fx.reopen, env, config=forged) is IntegrityFinding.CHECKPOINT_AUTHENTICATION_FAILED
    assert env.store.state is StoreState.READ_REFUSED


def test_unsupported_algorithm_is_an_integrity_stop(tmp_path):
    env = _env(tmp_path)
    path = env.config.path
    data = json.loads(path.read_bytes())
    data["authentication"]["algorithm"] = "hmac_md5"
    path.write_bytes(canonical_json_bytes(data) + b"\n")
    assert _stop(fx.reopen, env) is IntegrityFinding.CHECKPOINT_ALGORITHM_UNSUPPORTED


def test_unauthenticated_mode_only_when_explicitly_selected(tmp_path):
    env = fx.make_env(tmp_path)  # offline development mode, explicitly selected
    checkpoint, raw = read_checkpoint(env.config)
    assert checkpoint.authentication is None
    production = CheckpointConfig(
        env.config.state_dir, CheckpointMode.PRODUCTION_AUTHENTICATED, fx.keyring()
    )
    assert _stop(fx.reopen, env, config=production) is IntegrityFinding.CHECKPOINT_INVALID


def test_checkpoint_holds_no_evidence_or_key_material(tmp_path):
    env = _env(tmp_path)
    _append(env)
    text = env.config.path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert set(data) == {
        "authentication",
        "checkpoint_schema_version",
        "commit_digest",
        "commit_seq",
        "created_at_utc",
        "store_instance_id",
    }
    assert set(data["authentication"]) == {"algorithm", "key_id", "mac"}
    assert "synthetic-test-secret" not in text
    assert "evi1_" not in text and "SPY" not in text


# --- Path safety and atomic writes ---------------------------------------------------------------


def test_state_directory_must_be_safe(tmp_path):
    settings = fx.make_settings(tmp_path)

    def build(state):
        return EvidenceStore(
            settings=settings,
            checkpoint=CheckpointConfig(state, CheckpointMode.OFFLINE_DEVELOPMENT_UNAUTHENTICATED),
            payload_models=fx.PAYLOAD_MODELS,
        )

    assert (
        _reason(build, settings.project_data_path) == "checkpoint_path_unsafe"
    )  # the database directory
    inside = settings.project_data_path / "state"
    inside.mkdir()
    assert _reason(build, inside) == "checkpoint_path_unsafe"
    assert _reason(build, tmp_path / "state" / ".." / "state") == "checkpoint_path_unsafe"
    assert _reason(build, tmp_path / "missing") == "checkpoint_path_unsafe"
    assert _reason(build, REPO_ROOT / "market_intelligence") == "checkpoint_path_unsafe"
    target = tmp_path / "real_state"
    target.mkdir()
    link = tmp_path / "linked_state"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    assert _reason(build, link) == "checkpoint_path_unsafe"


def test_failed_replacement_never_damages_the_good_checkpoint(tmp_path, monkeypatch):
    env = _env(tmp_path)
    good = env.config.path.read_bytes()
    checkpoint, _ = read_checkpoint(env.config)

    def broken_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(checkpoint_module.os, "replace", broken_replace)
    with pytest.raises(StoreRefusal):
        write_checkpoint(env.config, checkpoint)
    assert env.config.path.read_bytes() == good
    assert not list(env.config.state_dir.glob(".*.tmp"))


# --- Valid-prefix rollback and startup table -----------------------------------------------------


def test_older_valid_database_swapped_in_is_detected(tmp_path):
    env = _env(tmp_path)
    older = tmp_path / "older.duckdb"
    shutil.copyfile(env.store.database_path, older)
    _append(env)
    shutil.copyfile(older, env.store.database_path)  # internally valid, but behind
    assert _stop(fx.reopen, env) is IntegrityFinding.DATABASE_BEHIND_CHECKPOINT


def test_digest_mismatch_and_foreign_checkpoint_are_stops(tmp_path):
    env = _env(tmp_path)
    checkpoint, _ = read_checkpoint(env.config)
    forged = sign_checkpoint(
        StoreCheckpointContent(
            store_instance_id=checkpoint.store_instance_id,
            commit_seq=checkpoint.commit_seq,
            commit_digest="0" * 64,
            created_at_utc=checkpoint.created_at_utc,
        ),
        env.config,
    )
    write_checkpoint(env.config, forged)
    assert _stop(fx.reopen, env) is IntegrityFinding.CHECKPOINT_COMMIT_MISMATCH
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
    assert _stop(fx.reopen, env) is IntegrityFinding.CHECKPOINT_STORE_MISMATCH


def test_missing_checkpoint_rules(tmp_path):
    env = _env(tmp_path)
    env.config.path.unlink()
    assert _stop(fx.reopen, env) is IntegrityFinding.CHECKPOINT_MISSING
    # A brand-new empty store may lack one only during initialization; an
    # empty database with a checkpoint present is behind it.
    fresh = fx.make_env(tmp_path / "fresh", initialize=False)
    write_checkpoint(fresh.config, read_checkpoint(fx.make_env(tmp_path / "donor").config)[0])
    with pytest.raises(IntegrityStop) as excinfo:
        fresh.store.initialize()
    assert excinfo.value.finding is IntegrityFinding.DATABASE_BEHIND_CHECKPOINT


# --- Key rotation --------------------------------------------------------------------------------


def test_successful_key_rotation(tmp_path):
    env = _env(tmp_path, ring=fx.keyring("key_one", "key_two"))
    rotate_checkpoint_key(env.store, new_key_id="key_two", authorization_ref=fx.AUTH)
    checkpoint, _ = read_checkpoint(env.config)
    assert checkpoint.authentication.key_id == "key_two"
    assert checkpoint.commit_seq == _tip(env)
    with connect(env.store.database_path, read_only=True) as conn:
        rotation = select_rows(conn, "SELECT * FROM store_checkpoint_key_rotations")[0]
    assert (rotation["previous_key_id"], rotation["new_key_id"]) == ("key_one", "key_two")
    assert "synthetic-test-secret" not in json.dumps(rotation, default=str)
    only_new = CheckpointConfig(
        env.config.state_dir, CheckpointMode.PRODUCTION_AUTHENTICATED, fx.keyring("key_two")
    )
    assert fx.reopen(env, config=only_new).state is StoreState.SERVING
    _append(env)


def test_rotation_refuses_other_writes_until_it_finishes(tmp_path, monkeypatch):
    env = _env(tmp_path, ring=fx.keyring("key_one", "key_two"))
    seen = {}
    real_verify = recovery_module.verify_database

    def spy(*args, **kwargs):
        seen["state"] = env.store.state
        seen["write"] = _reason(_append, env)
        return real_verify(*args, **kwargs)

    monkeypatch.setattr(recovery_module, "verify_database", spy)
    rotate_checkpoint_key(env.store, new_key_id="key_two", authorization_ref=fx.AUTH)
    assert seen == {
        "state": StoreState.CHECKPOINT_KEY_ROTATION_IN_PROGRESS,
        "write": "checkpoint_key_rotation_in_progress",
    }


def test_failed_rotation_completes_through_reconciliation_with_the_new_key(tmp_path, monkeypatch):
    env = _env(tmp_path, ring=fx.keyring("key_one", "key_two"))
    _fail_writes(monkeypatch, calls_to_fail={1})
    assert _reason(
        rotate_checkpoint_key, env.store, new_key_id="key_two", authorization_ref=fx.AUTH
    ) == ("workflow_incomplete")
    assert env.store.state is StoreState.CHECKPOINT_RECONCILIATION_REQUIRED
    monkeypatch.undo()
    env.store.reconcile()
    checkpoint, _ = read_checkpoint(env.config)
    assert checkpoint.authentication.key_id == "key_two"
    with connect(env.store.database_path, read_only=True) as conn:
        assert len(select_rows(conn, "SELECT * FROM store_checkpoint_key_rotations")) == 1


def test_rotation_with_unknown_new_key_writes_nothing(tmp_path):
    env = _env(tmp_path)
    before = _tip(env)
    assert _reason(
        rotate_checkpoint_key, env.store, new_key_id="key_nine", authorization_ref=fx.AUTH
    ) == ("checkpoint_key_id_unknown")
    assert _tip(env) == before and env.store.state is StoreState.SERVING
    assert CHECKPOINT_FILENAME == "store_checkpoint.json"
