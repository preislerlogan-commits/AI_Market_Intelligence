"""Safe, local, offline JSON round trip for the deterministic SPY
options-contract eligibility selector -- Phase 1 step e.

Mirrors ``market_intelligence/evaluation/spy_vwap_reversion_serialization.py``
exactly (same no-overwrite / no-symlink / atomic-write / bounded-read /
sanitized-error guarantees), with its own self-contained error type so this
package stays fully independent of ``market_intelligence.evaluation`` -- see
``market_intelligence/contract_selection/__init__.py``.

Boundary (all enforced below):

- **Pure helpers.** ``*_to_json_str`` / ``*_from_json_str`` do no I/O.
- **Explicit path only.** ``write_record`` / ``read_record`` / ``read_input``
  take a caller-provided ``Path``. There is no default location and no
  automatic output directory.
- **Parent must already exist.** ``write_record`` never creates directories.
- **No symlinks.** Both the target file and its parent directory are refused
  if either is a symlink.
- **No silent overwrite.** ``write_record`` refuses an existing target unless
  ``overwrite=True``.
- **Atomic write.** Same temp-file-then-publish strategy as
  ``evaluation/serialization.py``.
- **Bounded read.** Both ``read_record`` and ``read_input`` refuse a file
  larger than their respective byte ceiling before reading it.
- **Sanitized errors.** Every failure raises the shared
  ``ContractSelectorSerializationError`` with a fixed message -- never the
  path, the file content, the record/input content, or a raw exception
  message.
- **No database.** Nothing here opens DuckDB or any database.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from pydantic import ValidationError

from market_intelligence.contract_selection.contracts import (
    ContractSelectorInput,
    ContractSelectorResult,
)

# A full bounded option-chain batch is the largest object this module reads;
# bounded generously above the contract's own MAX_CONTRACTS_PER_BATCH
# ceiling, never unbounded.
MAX_INPUT_BYTES = 20_000_000

# The output record additionally carries one row per eligible contract;
# bounded the same way.
MAX_RECORD_BYTES = 20_000_000


class ContractSelectorSerializationError(RuntimeError):
    """Sanitized serialization failure.

    Never includes a filesystem path, the file's bytes, the record/input
    content, or a raw underlying exception message.
    """


# --- Selector result (output) --------------------------------------------------------


def to_json_str(record: ContractSelectorResult) -> str:
    """Serialize ``record`` to deterministic, byte-stable JSON: sorted keys,
    two-space indent, ``ensure_ascii=False``, one trailing newline."""
    payload = record.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def from_json_str(text: str) -> ContractSelectorResult:
    """Parse and validate a ``ContractSelectorResult`` from JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise ContractSelectorSerializationError("selector record is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ContractSelectorSerializationError("selector record must be a JSON object")
    try:
        return ContractSelectorResult.model_validate(payload)
    except ValidationError:
        raise ContractSelectorSerializationError(
            "selector record failed schema validation"
        ) from None


def _validated_directory(path: Path) -> Path:
    parent = path.parent
    if not parent.exists():
        raise ContractSelectorSerializationError("parent directory does not exist")
    if not parent.is_dir():
        raise ContractSelectorSerializationError("parent path is not a directory")
    if parent.is_symlink():
        raise ContractSelectorSerializationError("parent directory is a symlink")
    if path.is_symlink():
        raise ContractSelectorSerializationError("target path is a symlink")
    return parent


def write_record(
    record: ContractSelectorResult,
    path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write ``record`` to ``path`` as JSON.

    Same no-overwrite / no-symlink / atomic-publish guarantees as
    ``evaluation.serialization.write_record``. The temporary file is always
    removed.
    """
    target = Path(path)
    directory = _validated_directory(target)

    if target.exists() and not overwrite:
        raise ContractSelectorSerializationError("target file already exists")

    text = to_json_str(record)
    tmp_name = f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = directory / tmp_name
    try:
        with open(tmp_path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if overwrite:
            os.replace(tmp_path, target)
        else:
            try:
                os.link(tmp_path, target)
            except FileExistsError:
                raise ContractSelectorSerializationError(
                    "target file already exists"
                ) from None
            finally:
                tmp_path.unlink(missing_ok=True)
    except ContractSelectorSerializationError:
        raise
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ContractSelectorSerializationError("failed to write selector record") from None


def read_record(path: str | os.PathLike[str]) -> ContractSelectorResult:
    """Read and validate a ``ContractSelectorResult`` from ``path``.

    Refuses a symlinked file or parent, and refuses a file larger than
    ``MAX_RECORD_BYTES`` before reading it.
    """
    source = Path(path)
    parent = source.parent
    if parent.is_symlink():
        raise ContractSelectorSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise ContractSelectorSerializationError("target path is a symlink")
    if not source.exists():
        raise ContractSelectorSerializationError("selector record file does not exist")
    if not source.is_file():
        raise ContractSelectorSerializationError("selector record path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise ContractSelectorSerializationError("failed to stat selector record") from None
    if size > MAX_RECORD_BYTES:
        raise ContractSelectorSerializationError("selector record file is too large")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ContractSelectorSerializationError("failed to read selector record") from None
    return from_json_str(text)


# --- Selector input (read-only; a local, gitignored fixture-shaped file) ------------


def input_to_json_str(model: ContractSelectorInput) -> str:
    """Serialize a ``ContractSelectorInput`` to deterministic, byte-stable
    JSON (same formatting as ``to_json_str``)."""
    payload = model.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def input_from_json_str(text: str) -> ContractSelectorInput:
    """Parse and validate a ``ContractSelectorInput`` from JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise ContractSelectorSerializationError("selector input is not valid JSON") from None
    if not isinstance(payload, dict):
        raise ContractSelectorSerializationError("selector input must be a JSON object")
    try:
        return ContractSelectorInput.model_validate(payload)
    except ValidationError:
        raise ContractSelectorSerializationError(
            "selector input failed schema validation"
        ) from None


def write_input(
    selector_input: ContractSelectorInput,
    path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write ``selector_input`` to ``path`` as JSON.

    Same no-overwrite / no-symlink / atomic-publish guarantees as
    ``write_record`` -- used by a live capture coordinator (see
    ``market_features.spy_contract_capture`` /
    ``scripts/capture_spy_contract_selector_input.py``) to persist a real,
    already-validated ``ContractSelectorInput`` before it is separately fed
    to ``scripts/select_spy_option_contracts.py``. The temporary file is
    always removed.
    """
    target = Path(path)
    directory = _validated_directory(target)

    if target.exists() and not overwrite:
        raise ContractSelectorSerializationError("target file already exists")

    text = input_to_json_str(selector_input)
    tmp_name = f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = directory / tmp_name
    try:
        with open(tmp_path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        if overwrite:
            os.replace(tmp_path, target)
        else:
            try:
                os.link(tmp_path, target)
            except FileExistsError:
                raise ContractSelectorSerializationError(
                    "target file already exists"
                ) from None
            finally:
                tmp_path.unlink(missing_ok=True)
    except ContractSelectorSerializationError:
        raise
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise ContractSelectorSerializationError("failed to write selector input") from None


def read_input(path: str | os.PathLike[str]) -> ContractSelectorInput:
    """Read and validate a ``ContractSelectorInput`` from a local file.

    Refuses a symlinked file or parent, and refuses a file larger than
    ``MAX_INPUT_BYTES`` before reading it. This module never reads the real
    DuckDB database or makes a network request -- it only ever reads a
    local JSON file the caller supplies an explicit path to.
    """
    source = Path(path)
    parent = source.parent
    if parent.is_symlink():
        raise ContractSelectorSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise ContractSelectorSerializationError("target path is a symlink")
    if not source.exists():
        raise ContractSelectorSerializationError("selector input file does not exist")
    if not source.is_file():
        raise ContractSelectorSerializationError("selector input path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise ContractSelectorSerializationError("failed to stat selector input") from None
    if size > MAX_INPUT_BYTES:
        raise ContractSelectorSerializationError("selector input file is too large")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ContractSelectorSerializationError("failed to read selector input") from None
    return input_from_json_str(text)
