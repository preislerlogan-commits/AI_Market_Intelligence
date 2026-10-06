"""Strict registry files (docs/REGISTRY_LOADING_DESIGN.md §3-§5, §10).

Registry versions are reviewed, version-controlled JSON files under the
repository's ``registries/`` directory. This module only *loads* and
*identifies* them; it never writes one. No real registry file exists yet.

Every check fails closed with one bounded token:

- path inside ``registries/<kind>/``, no symlink, no traversal;
- size, UTF-8 without BOM, duplicate keys, floats, NaN/Infinity, nesting;
- known ``file_format``; strict models with unknown fields refused;
- the file's bytes equal its own canonical rendering;
- the declared identity equals the recomputed one;
- the file name equals the registry label.

The setup-definition registry is **structurally empty**: its ``definitions``
list has ``max_length=0``, so no file can add a setup definition.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, ValidationError

from market_intelligence.config.settings import REPO_ROOT
from market_intelligence.evidence.canonical import canonical_json_bytes, prefixed_id
from market_intelligence.evidence.primitives import STRICT_FROZEN, RegistryVersionId, VersionLabel
from market_intelligence.evidence.registry import EvidenceRegistry, compute_registry_version_id
from market_intelligence.evidence_store.contracts import SETUP_REGISTRY_PREFIX
from market_intelligence.evidence_store.enums import RegistryKind
from market_intelligence.evidence_store.errors import StoreRefusal
from market_intelligence.setup_cards.definitions import SetupDefinition

DEFAULT_REGISTRIES_ROOT = REPO_ROOT / "registries"
MAX_REGISTRY_FILE_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 32
_FOLDERS = {
    RegistryKind.EVIDENCE_REGISTRY: "evidence",
    RegistryKind.SETUP_DEFINITION_REGISTRY: "setup_definitions",
}
_FORMATS = {
    RegistryKind.EVIDENCE_REGISTRY: "evidence-registry-file-1",
    RegistryKind.SETUP_DEFINITION_REGISTRY: "setup-definition-registry-file-1",
}


def _refuse(reason: str) -> StoreRefusal:
    return StoreRefusal(reason)


class SetupDefinitionRegistry(BaseModel):
    """Registry design §10: structurally empty in this version."""

    model_config = STRICT_FROZEN

    registry_label: VersionLabel
    definitions: Annotated[list[SetupDefinition], Field(max_length=0)]


def compute_setup_registry_version_id(registry: SetupDefinitionRegistry) -> str:
    return prefixed_id(SETUP_REGISTRY_PREFIX, registry.model_dump(mode="json"))


class EvidenceRegistryFile(BaseModel):
    model_config = STRICT_FROZEN

    file_format: Literal["evidence-registry-file-1"]
    registry: EvidenceRegistry
    registry_version_id: RegistryVersionId


class SetupDefinitionRegistryFile(BaseModel):
    model_config = STRICT_FROZEN

    file_format: Literal["setup-definition-registry-file-1"]
    registry: SetupDefinitionRegistry
    registry_version_id: Annotated[str, StringConstraints(pattern=r"^sdr1_[0-9a-f]{64}$")]


@dataclass(frozen=True)
class LoadedRegistry:
    """A verified registry file. ``content_json`` is the identity form."""

    registry_kind: RegistryKind
    registry_label: str
    registry_version_id: str
    file_format: str
    content_json: str
    source_path: str
    source_file_sha256: str
    evidence_registry: EvidenceRegistry | None
    setup_registry: SetupDefinitionRegistry | None


def render_canonical_file(wrapper: BaseModel) -> bytes:
    """The one valid rendering: the existing display form."""
    dump = wrapper.model_dump(mode="json")
    return (json.dumps(dump, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise _refuse("registry_duplicate_key")
    return dict(pairs)


def _refuse_float(_: str) -> Any:
    raise _refuse("registry_float_not_allowed")


def _refuse_constant(_: str) -> Any:
    raise _refuse("registry_float_not_allowed")


def _depth(value: Any, level: int = 1) -> int:
    if isinstance(value, dict):
        return max([level, *(_depth(v, level + 1) for v in value.values())])
    if isinstance(value, list):
        return max([level, *(_depth(v, level + 1) for v in value)])
    return level


def strict_parse(raw: bytes) -> Any:
    """Parse strict JSON: UTF-8 without BOM, no duplicate keys, no floats,
    NaN or Infinity, no trailing data, bounded size and nesting."""
    if len(raw) > MAX_REGISTRY_FILE_BYTES:
        raise _refuse("registry_file_too_large")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise _refuse("registry_file_not_utf8")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise _refuse("registry_file_not_utf8") from None
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_no_duplicates,
            parse_float=_refuse_float,
            parse_constant=_refuse_constant,
        )
    except StoreRefusal:
        raise
    except (ValueError, RecursionError):
        raise _refuse("registry_json_invalid") from None
    if _depth(parsed) > MAX_NESTING_DEPTH:
        raise _refuse("registry_json_invalid")
    return parsed


def _contained_path(path: Path, root: Path, kind: RegistryKind) -> Path:
    """Refuse symlinks, traversal and anything outside ``root/<kind>/``."""
    root_resolved = root.resolve()
    folder = root_resolved / _FOLDERS[kind]
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    # Refuse a symlink at any level between the root and the file.
    probe = candidate
    while True:
        if probe.is_symlink():
            raise _refuse("registry_path_outside_repository")
        if probe == root or probe.parent == probe:
            break
        probe = probe.parent
    resolved = candidate.resolve()
    if resolved.parent != folder:
        raise _refuse("registry_path_outside_repository")
    if not resolved.is_file():
        raise _refuse("registry_file_not_found")
    return resolved


def _validate_wrapper(kind: RegistryKind, raw: bytes) -> BaseModel:
    model = (
        EvidenceRegistryFile
        if kind is RegistryKind.EVIDENCE_REGISTRY
        else (SetupDefinitionRegistryFile)
    )
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        # The validation message can quote input values; it is discarded.
        raise _refuse("registry_schema_invalid") from None


def parse_registry_bytes(kind: RegistryKind, raw: bytes, *, file_name: str) -> LoadedRegistry:
    """Strict parsing steps 2-9 of registry design §4 for already-read bytes."""
    parsed = strict_parse(raw)
    if not isinstance(parsed, dict) or parsed.get("file_format") != _FORMATS[kind]:
        raise _refuse("registry_file_format_unknown")
    wrapper = _validate_wrapper(kind, raw)
    if render_canonical_file(wrapper) != raw:
        raise _refuse("registry_not_canonical_form")
    registry = wrapper.registry  # type: ignore[attr-defined]
    if kind is RegistryKind.EVIDENCE_REGISTRY:
        computed = compute_registry_version_id(registry)
    else:
        computed = compute_setup_registry_version_id(registry)
    if computed != wrapper.registry_version_id:  # type: ignore[attr-defined]
        raise _refuse("registry_id_mismatch")
    label = registry.registry_label
    if file_name != f"{label}.json":
        raise _refuse("registry_label_filename_mismatch")
    content = canonical_json_bytes(registry.model_dump(mode="json")).decode("utf-8")
    return LoadedRegistry(
        registry_kind=kind,
        registry_label=label,
        registry_version_id=computed,
        file_format=_FORMATS[kind],
        content_json=content,
        source_path=f"registries/{_FOLDERS[kind]}/{file_name}",
        source_file_sha256=hashlib.sha256(raw).hexdigest(),
        evidence_registry=registry if kind is RegistryKind.EVIDENCE_REGISTRY else None,
        setup_registry=registry if kind is RegistryKind.SETUP_DEFINITION_REGISTRY else None,
    )


def load_registry_file(
    kind: RegistryKind, path: Path, *, registries_root: Path = DEFAULT_REGISTRIES_ROOT
) -> LoadedRegistry:
    """Load one registry file from inside ``registries_root/<kind>/``."""
    resolved = _contained_path(path, registries_root, kind)
    try:
        raw = resolved.read_bytes()
    except OSError:
        raise _refuse("registry_file_not_found") from None
    return parse_registry_bytes(kind, raw, file_name=resolved.name)


def load_registry_content(kind: RegistryKind, content_json: str) -> BaseModel:
    """Re-parse registered content (the identity form) strictly."""
    model = EvidenceRegistry if kind is RegistryKind.EVIDENCE_REGISTRY else SetupDefinitionRegistry
    raw = content_json.encode("utf-8")
    strict_parse(raw)
    try:
        registry = model.model_validate_json(raw)
    except ValidationError:
        raise _refuse("registry_schema_invalid") from None
    if canonical_json_bytes(registry.model_dump(mode="json")) != raw:
        raise _refuse("registry_not_canonical_form")
    return registry


def registry_identity(kind: RegistryKind, registry: BaseModel) -> str:
    if kind is RegistryKind.EVIDENCE_REGISTRY:
        return compute_registry_version_id(registry)  # type: ignore[arg-type]
    return compute_setup_registry_version_id(registry)  # type: ignore[arg-type]
