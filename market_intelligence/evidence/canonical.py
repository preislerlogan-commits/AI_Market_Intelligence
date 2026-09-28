"""Canonical serialization and content-addressed identities (design §B.0, §L).

The identity form is compact JSON:
``json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
allow_nan=False)`` encoded as UTF-8, then SHA-256. Callers pass a JSON-mode
dump from which only the §L.2 operational fields have been removed.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

ITEM_PREFIX = "evi1_"
ENVELOPE_PREFIX = "eve1_"
CONFLICT_PREFIX = "evc1_"
BUNDLE_PREFIX = "evb1_"
REGISTRY_PREFIX = "evr1_"
CONFIG_PREFIX = "cfg1_"
CONFIG_NONE = "cfg_none"


def canonical_json_bytes(obj: Any) -> bytes:
    """Compact, sorted, NaN-free canonical JSON bytes."""
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_sha256(obj: Any) -> str:
    return sha256_hex(canonical_json_bytes(obj))


def prefixed_id(prefix: str, obj: Any) -> str:
    return prefix + canonical_sha256(obj)


def remove_paths(dump: Mapping[str, Any], excluded: Iterable[tuple[str, ...]]) -> dict[str, Any]:
    """Return a deep copy of ``dump`` with each dotted path removed.

    Only the listed paths are removed (design §B.0 step 2); every other
    field, including nulls, stays.
    """
    result: dict[str, Any] = json.loads(json.dumps(dump))
    for path in excluded:
        node: Any = result
        for key in path[:-1]:
            node = node[key]
        node.pop(path[-1])
    return result


def sort_key(value: Any) -> bytes:
    """Deterministic sort key for set-like lists: the element's canonical JSON."""
    return canonical_json_bytes(value)


def configuration_identity(configuration: Mapping[str, Any] | None) -> str:
    """``cfg1_`` + SHA-256 of a canonical configuration snapshot, or ``cfg_none``."""
    if configuration is None:
        return CONFIG_NONE
    return prefixed_id(CONFIG_PREFIX, configuration)
