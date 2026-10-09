"""Temporary, self-cleaning workspaces for the offline vertical slice.

A workspace lives only under the system temporary directory. It refuses the
repository, the repository's real data directory, the configured project
data path and any non-temporary location, and it refuses settings that carry
provider credentials. Its stores run in the offline, unauthenticated
checkpoint mode, which the store itself refuses for the real database, so no
key is ever created. Everything is removed when the workspace closes.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.evidence_store.checkpoint import CheckpointConfig
from market_intelligence.evidence_store.enums import CheckpointMode
from market_intelligence.evidence_store.errors import StoreRefusal

_CREDENTIAL_FIELDS = (
    "alpaca_api_key",
    "alpaca_api_secret",
    "fred_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "evidence_store_checkpoint_hmac_key",
    "evidence_store_checkpoint_previous_hmac_key",
)


class SliceRefusal(StoreRefusal):
    """The slice refused to run; carries one bounded reason token."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def require_temporary_parent(parent: Path) -> Path:
    """Only a location under the system temporary directory, and never the
    repository or its data directory."""
    resolved = Path(parent).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if _inside(resolved, REPO_ROOT) or _inside(REPO_ROOT, resolved):
        raise SliceRefusal("repository_directory_refused")
    if _inside(resolved, REPO_ROOT / "data"):
        raise SliceRefusal("real_data_directory_refused")
    if not _inside(resolved, temp_root):
        raise SliceRefusal("non_temporary_directory_refused")
    return resolved


def offline_settings(data_dir: Path) -> Settings:
    """Settings for one temporary store: no ``.env``, and every provider and
    checkpoint credential explicitly blank, whatever the environment holds."""
    settings = Settings(
        _env_file=None,
        project_data_path=data_dir,
        **{name: None for name in _CREDENTIAL_FIELDS},
    )
    require_offline_settings(settings)
    return settings


def require_offline_settings(settings: Settings) -> None:
    """Refuse live credentials and any non-temporary data location."""
    for name in _CREDENTIAL_FIELDS:
        if getattr(settings, name) is not None:
            raise SliceRefusal("live_credentials_refused")
    require_temporary_parent(settings.project_data_path)


@dataclass(frozen=True)
class StorePaths:
    root: Path
    data: Path
    state: Path
    registries: Path

    @property
    def settings(self) -> Settings:
        return offline_settings(self.data)

    @property
    def checkpoint(self) -> CheckpointConfig:
        return CheckpointConfig(
            state_dir=self.state, mode=CheckpointMode.OFFLINE_DEVELOPMENT_UNAUTHENTICATED
        )


def store_paths(workspace: Path, name: str) -> StorePaths:
    root = require_temporary_parent(workspace) / name
    paths = StorePaths(root, root / "data", root / "state", root / "registries")
    for directory in (paths.data, paths.state):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


@contextmanager
def temporary_workspace(parent: Path | None = None) -> Iterator[Path]:
    """A fresh temporary directory, always removed afterwards."""
    base = require_temporary_parent(parent if parent is not None else Path(tempfile.gettempdir()))
    path = Path(tempfile.mkdtemp(prefix="mi-slice-", dir=base))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=False)
