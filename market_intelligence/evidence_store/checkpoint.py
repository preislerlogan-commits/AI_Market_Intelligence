"""External anti-rollback checkpoint (docs/EVIDENCE_CARD_STORAGE_DESIGN.md §10.6).

The checkpoint is a **witness only**: the highest durably committed
(``commit_seq``, ``commit_digest``) of one store instance, plus its
authentication. It holds no evidence, record ID, path or key material, and
it never supplies, restores or overrides a record.

Policy (frozen):

- it lives in a separately configured state directory outside the database
  directory (this offline core also refuses any directory inside the
  repository, so it can never be Git-tracked), never a symlink, never a
  traversal;
- production mode requires HMAC-SHA-256 with a key from the project's secret
  settings (``SecretStr``); keys never appear in output, records or logs;
- an unauthenticated checkpoint exists only in an explicitly selected
  offline development or test mode; a missing production key never falls
  back to it;
- replacement is atomic: exclusive temporary file, flush and sync, atomic
  rename, then a directory sync where the platform supports it.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import SecretStr, ValidationError

from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.evidence.canonical import canonical_json_bytes
from market_intelligence.evidence_store.contracts import (
    CheckpointAuthentication,
    StoreCheckpoint,
    StoreCheckpointContent,
    checkpoint_mac_payload,
)
from market_intelligence.evidence_store.enums import (
    CheckpointAuthAlgorithm,
    CheckpointMode,
    IntegrityFinding,
)
from market_intelligence.evidence_store.errors import IntegrityStop, StoreRefusal
from market_intelligence.evidence_store.registry_files import strict_parse

CHECKPOINT_FILENAME = "store_checkpoint.json"
_TMP_PREFIX = f".{CHECKPOINT_FILENAME}."
_TMP_SUFFIX = ".tmp"
MIN_KEY_BYTES = 32
_KEY_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@dataclass(frozen=True)
class CheckpointKeyring:
    """The active key (used to sign) and every key accepted for verification.

    Values are ``SecretStr``; ``repr`` never shows them.
    """

    active_key_id: str
    keys: Mapping[str, SecretStr] = field(repr=False)

    def __post_init__(self) -> None:
        if self.active_key_id not in self.keys:
            raise StoreRefusal("checkpoint_key_missing")
        for key_id, secret in self.keys.items():
            if not _KEY_ID.fullmatch(key_id):
                raise StoreRefusal("checkpoint_key_id_unknown")
            if len(secret.get_secret_value().encode("utf-8")) < MIN_KEY_BYTES:
                raise StoreRefusal("checkpoint_key_missing")

    @classmethod
    def from_settings(cls, settings: Settings) -> CheckpointKeyring:
        """Production keys from the secret settings. Fails closed when absent."""
        key_id = settings.evidence_store_checkpoint_key_id
        key = settings.evidence_store_checkpoint_hmac_key
        if not key_id or key is None or not key.get_secret_value().strip():
            raise StoreRefusal("checkpoint_key_missing")
        keys: dict[str, SecretStr] = {key_id: key}
        previous_id = settings.evidence_store_checkpoint_previous_key_id
        previous = settings.evidence_store_checkpoint_previous_hmac_key
        if previous_id and previous is not None and previous.get_secret_value().strip():
            keys[previous_id] = previous
        return cls(active_key_id=key_id, keys=keys)

    def mac(self, key_id: str, payload: bytes) -> str:
        secret = self.keys.get(key_id)
        if secret is None:
            raise StoreRefusal("checkpoint_key_id_unknown")
        return hmac.new(
            secret.get_secret_value().encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()

    def with_active(self, key_id: str) -> CheckpointKeyring:
        return CheckpointKeyring(active_key_id=key_id, keys=self.keys)


@dataclass(frozen=True)
class CheckpointConfig:
    state_dir: Path
    mode: CheckpointMode
    keyring: CheckpointKeyring | None = None

    @property
    def path(self) -> Path:
        return self.state_dir / CHECKPOINT_FILENAME


def validate_checkpoint_config(config: CheckpointConfig, database_path: Path) -> None:
    """Path safety and mode rules. Refusals are ordinary (no data is wrong)."""
    raw = Path(config.state_dir)
    if ".." in raw.parts:
        raise StoreRefusal("checkpoint_path_unsafe")
    probe = raw
    while True:
        if probe.is_symlink():
            raise StoreRefusal("checkpoint_path_unsafe")
        if probe.parent == probe:
            break
        probe = probe.parent
    state = _require_safe_state_dir(config)
    if not state.is_dir():
        raise StoreRefusal("checkpoint_path_unsafe")
    database_dir = database_path.resolve().parent
    if state == database_dir or database_dir in state.parents or state in database_dir.parents:
        raise StoreRefusal("checkpoint_path_unsafe")
    repo = REPO_ROOT.resolve()
    if state == repo or repo in state.parents:
        raise StoreRefusal("checkpoint_path_unsafe")
    if config.mode is CheckpointMode.PRODUCTION_AUTHENTICATED and config.keyring is None:
        # Never a silent fallback to unauthenticated mode.
        raise StoreRefusal("checkpoint_key_missing")


def _require_safe_state_dir(config: CheckpointConfig) -> Path:
    """Every checkpoint read and write: never a real data directory, never the
    repository, never a symlinked or traversing path."""
    from market_intelligence.evidence_store.store_io import refuse_real_database_path

    raw = Path(config.state_dir)
    if ".." in raw.parts or raw.is_symlink():
        raise StoreRefusal("checkpoint_path_unsafe")
    try:
        state = refuse_real_database_path(raw)
    except StoreRefusal:
        raise StoreRefusal("checkpoint_path_unsafe") from None
    repo = REPO_ROOT.resolve()
    if state == repo or repo in state.parents:
        raise StoreRefusal("checkpoint_path_unsafe")
    return state


def render_checkpoint(checkpoint: StoreCheckpoint) -> bytes:
    return canonical_json_bytes(checkpoint.model_dump(mode="json")) + b"\n"


def sign_checkpoint(content: StoreCheckpointContent, config: CheckpointConfig) -> StoreCheckpoint:
    fields = {name: getattr(content, name) for name in StoreCheckpointContent.model_fields}
    if config.mode is CheckpointMode.OFFLINE_DEVELOPMENT_UNAUTHENTICATED and config.keyring is None:
        return StoreCheckpoint(**fields, authentication=None)
    keyring = config.keyring
    if keyring is None:
        raise StoreRefusal("checkpoint_key_missing")
    algorithm = CheckpointAuthAlgorithm.HMAC_SHA256.value
    payload = checkpoint_mac_payload(content, algorithm, keyring.active_key_id)
    return StoreCheckpoint(
        **fields,
        authentication=CheckpointAuthentication(
            algorithm=algorithm,
            key_id=keyring.active_key_id,
            mac=keyring.mac(keyring.active_key_id, payload),
        ),
    )


def verify_checkpoint_authentication(checkpoint: StoreCheckpoint, config: CheckpointConfig) -> None:
    auth = checkpoint.authentication
    production = config.mode is CheckpointMode.PRODUCTION_AUTHENTICATED
    if auth is None:
        if production:
            raise IntegrityStop(IntegrityFinding.CHECKPOINT_INVALID)
        return
    if auth.algorithm not in {a.value for a in CheckpointAuthAlgorithm}:
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_ALGORITHM_UNSUPPORTED)
    keyring = config.keyring
    if keyring is None:
        raise StoreRefusal("checkpoint_key_missing")
    if auth.key_id not in keyring.keys:
        raise StoreRefusal("checkpoint_key_id_unknown")
    content = StoreCheckpointContent(
        **{name: getattr(checkpoint, name) for name in StoreCheckpointContent.model_fields}
    )
    expected = keyring.mac(
        auth.key_id, checkpoint_mac_payload(content, auth.algorithm, auth.key_id)
    )
    if not hmac.compare_digest(expected, auth.mac):
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_AUTHENTICATION_FAILED)


def read_checkpoint(config: CheckpointConfig) -> tuple[StoreCheckpoint | None, bytes | None]:
    """The current checkpoint and its exact bytes, or ``(None, None)``.

    A checkpoint that does not parse strictly, or is not in canonical form, is
    an integrity stop.
    """
    _require_safe_state_dir(config)
    path = config.path
    if path.is_symlink():
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_INVALID)
    if not path.exists():
        return None, None
    try:
        raw = path.read_bytes()
    except OSError:
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_INVALID) from None
    try:
        strict_parse(raw.rstrip(b"\n"))
        checkpoint = StoreCheckpoint.model_validate_json(raw)
    except (StoreRefusal, ValidationError, ValueError):
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_INVALID) from None
    if render_checkpoint(checkpoint) != raw:
        raise IntegrityStop(IntegrityFinding.CHECKPOINT_INVALID)
    verify_checkpoint_authentication(checkpoint, config)
    return checkpoint, raw


def _sync_directory(directory: Path) -> None:
    """Best effort: directory fsync is unsupported on some platforms."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _remove_stale_temporaries(directory: Path) -> None:
    for leftover in directory.glob(f"{_TMP_PREFIX}*{_TMP_SUFFIX}"):
        if leftover.is_file() and not leftover.is_symlink():
            try:
                leftover.unlink()
            except OSError:
                pass


def write_checkpoint(config: CheckpointConfig, checkpoint: StoreCheckpoint) -> None:
    """Atomically replace the checkpoint. A partial write never replaces a
    good checkpoint. Raises ``StoreRefusal('checkpoint_write_failed')``."""
    directory = _require_safe_state_dir(config)
    _remove_stale_temporaries(directory)
    tmp = directory / f"{_TMP_PREFIX}{secrets.token_hex(8)}{_TMP_SUFFIX}"
    data = render_checkpoint(checkpoint)
    try:
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
        try:
            written = os.write(fd, data)
            if written != len(data):
                raise OSError("short write")
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(str(tmp), str(config.path))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise StoreRefusal("checkpoint_write_failed") from None
    _sync_directory(directory)


def checkpoint_file_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
