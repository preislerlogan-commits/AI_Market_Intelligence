"""Safe, local, offline JSON round trip for the SPY VWAP-extension /
reversion evaluation -- step d of Phase 1.

Mirrors ``market_intelligence/evaluation/serialization.py`` exactly (same
no-overwrite / no-symlink / atomic-write / bounded-read / sanitized-error
guarantees), reusing its ``EvaluationSerializationError`` so every
evaluation-record and evaluation-input error in this repository shares one
sanitized error type. This module additionally round-trips
``SpyVwapReversionEvaluationInput`` (the local JSON file the offline CLI
reads), since that input -- not just the output record -- is itself local,
gitignored data.

Boundary (all enforced below):

- **Pure helpers.** ``*_to_json_str`` / ``*_from_json_str`` do no I/O.
- **Explicit path only.** ``write_record`` / ``write_input`` /
  ``read_record`` / ``read_input`` take a caller-provided ``Path``. There
  is no default location and no automatic output directory.
- **Parent must already exist.** Neither writer ever creates directories.
- **No symlinks.** Both the target file and its parent directory are
  refused if either is a symlink.
- **No silent overwrite.** ``write_record`` refuses an existing target
  unless ``overwrite=True``; ``write_input`` always refuses one.
- **Atomic write.** Same temp-file-then-publish strategy as
  ``evaluation/serialization.py``.
- **Bounded read.** Both ``read_record`` and ``read_input`` refuse a file
  larger than their respective byte ceiling before reading it -- the input
  ceiling is larger than the record's, since a multi-session bar dataset is
  the largest object this evaluation reads.
- **Sanitized errors.** Every failure raises the shared
  ``EvaluationSerializationError`` with a fixed message -- never the path,
  the file content, the record/input content, or a raw exception message.
- **No database.** Nothing here opens DuckDB or any database.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from pydantic import ValidationError

from market_intelligence.evaluation.serialization import EvaluationSerializationError
from market_intelligence.evaluation.spy_vwap_reversion_contracts import (
    SpyVwapReversionEvaluationInput,
    SpyVwapReversionEvaluationRecord,
)

# A multi-session, multi-bar evaluation input is the largest object this
# module reads; bounded generously above the contract's own MAX_SESSIONS /
# MAX_BARS_PER_SESSION ceiling, never unbounded.
MAX_INPUT_BYTES = 20_000_000

# The output record additionally carries one row per candidate decision
# point (bounded by MAX_DECISION_POINTS). Exactly 64 MiB, as approved by
# preregistration clarification C1.4: a ~121-session record is estimated at
# ~23 MB and the contract's 11,700-decision-point maximum at ~29 MB, both
# above the former 20,000,000-byte ceiling. Still a finite local-file bound.
MAX_RECORD_BYTES = 67_108_864


# --- Evaluation record (output) ----------------------------------------------------


def to_json_str(record: SpyVwapReversionEvaluationRecord) -> str:
    """Serialize ``record`` to deterministic, byte-stable JSON: sorted keys,
    two-space indent, ``ensure_ascii=False``, one trailing newline."""
    payload = record.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def from_json_str(text: str) -> SpyVwapReversionEvaluationRecord:
    """Parse and validate a ``SpyVwapReversionEvaluationRecord`` from JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise EvaluationSerializationError("evaluation record is not valid JSON") from None
    if not isinstance(payload, dict):
        raise EvaluationSerializationError("evaluation record must be a JSON object")
    try:
        return SpyVwapReversionEvaluationRecord.model_validate(payload)
    except ValidationError:
        raise EvaluationSerializationError(
            "evaluation record failed schema validation"
        ) from None


def _validated_directory(path: Path) -> Path:
    parent = path.parent
    if not parent.exists():
        raise EvaluationSerializationError("parent directory does not exist")
    if not parent.is_dir():
        raise EvaluationSerializationError("parent path is not a directory")
    if parent.is_symlink():
        raise EvaluationSerializationError("parent directory is a symlink")
    if path.is_symlink():
        raise EvaluationSerializationError("target path is a symlink")
    return parent


def _write_text_atomically(
    text: str,
    target: Path,
    *,
    overwrite: bool,
    failure_message: str,
) -> None:
    """Shared no-overwrite / no-symlink / atomic-publish writer behind
    ``write_record`` and ``write_input``. The temporary file is always
    removed; every failure raises ``EvaluationSerializationError`` with a
    fixed message."""
    directory = _validated_directory(target)

    if target.exists() and not overwrite:
        raise EvaluationSerializationError("target file already exists")

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
                raise EvaluationSerializationError(
                    "target file already exists"
                ) from None
            finally:
                tmp_path.unlink(missing_ok=True)
    except EvaluationSerializationError:
        raise
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise EvaluationSerializationError(failure_message) from None


def write_record(
    record: SpyVwapReversionEvaluationRecord,
    path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write ``record`` to ``path`` as JSON.

    Same no-overwrite / no-symlink / atomic-publish guarantees as
    ``evaluation.serialization.write_record``. The temporary file is always
    removed.
    """
    _write_text_atomically(
        to_json_str(record),
        Path(path),
        overwrite=overwrite,
        failure_message="failed to write evaluation record",
    )


def read_record(path: str | os.PathLike[str]) -> SpyVwapReversionEvaluationRecord:
    """Read and validate a ``SpyVwapReversionEvaluationRecord`` from ``path``.

    Refuses a symlinked file or parent, and refuses a file larger than
    ``MAX_RECORD_BYTES`` before reading it.
    """
    source = Path(path)
    parent = source.parent
    if parent.is_symlink():
        raise EvaluationSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise EvaluationSerializationError("target path is a symlink")
    if not source.exists():
        raise EvaluationSerializationError("evaluation record file does not exist")
    if not source.is_file():
        raise EvaluationSerializationError("evaluation record path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise EvaluationSerializationError("failed to stat evaluation record") from None
    if size > MAX_RECORD_BYTES:
        raise EvaluationSerializationError("evaluation record file is too large")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EvaluationSerializationError("failed to read evaluation record") from None
    return from_json_str(text)


# --- Evaluation input (read-only; a local, gitignored fixture-shaped file) --------


def input_to_json_str(model: SpyVwapReversionEvaluationInput) -> str:
    """Serialize a ``SpyVwapReversionEvaluationInput`` to deterministic,
    byte-stable JSON (same formatting as ``to_json_str``)."""
    payload = model.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def write_input(
    model: SpyVwapReversionEvaluationInput,
    path: str | os.PathLike[str],
) -> None:
    """Atomically write an already-validated
    ``SpyVwapReversionEvaluationInput`` to ``path`` as byte-stable JSON
    (``input_to_json_str``).

    Same no-symlink / atomic-publish guarantees as ``write_record``, and
    **never** overwrites: there is deliberately no ``overwrite`` parameter.
    Used by ``scripts/build_spy_vwap_reversion_input.py`` to persist an input
    built read-only from stored bars; the file is only an evaluation
    *input*, never an evaluation result.
    """
    _write_text_atomically(
        input_to_json_str(model),
        Path(path),
        overwrite=False,
        failure_message="failed to write evaluation input",
    )


def input_from_json_str(text: str) -> SpyVwapReversionEvaluationInput:
    """Parse and validate a ``SpyVwapReversionEvaluationInput`` from JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise EvaluationSerializationError("evaluation input is not valid JSON") from None
    if not isinstance(payload, dict):
        raise EvaluationSerializationError("evaluation input must be a JSON object")
    try:
        return SpyVwapReversionEvaluationInput.model_validate(payload)
    except ValidationError:
        raise EvaluationSerializationError("evaluation input failed schema validation") from None


def read_input(path: str | os.PathLike[str]) -> SpyVwapReversionEvaluationInput:
    """Read and validate a ``SpyVwapReversionEvaluationInput`` from a local
    file. Refuses a symlinked file or parent, and refuses a file larger than
    ``MAX_INPUT_BYTES`` before reading it. This module never reads the real
    DuckDB database -- it only ever reads a local JSON file the caller
    supplies an explicit path to.
    """
    source = Path(path)
    parent = source.parent
    if parent.is_symlink():
        raise EvaluationSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise EvaluationSerializationError("target path is a symlink")
    if not source.exists():
        raise EvaluationSerializationError("evaluation input file does not exist")
    if not source.is_file():
        raise EvaluationSerializationError("evaluation input path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise EvaluationSerializationError("failed to stat evaluation input") from None
    if size > MAX_INPUT_BYTES:
        raise EvaluationSerializationError("evaluation input file is too large")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EvaluationSerializationError("failed to read evaluation input") from None
    return input_from_json_str(text)
