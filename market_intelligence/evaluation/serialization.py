"""Safe, local, offline JSON serialization for one ``EvaluationRunRecord``.

Boundary (all enforced below):

- **Pure helpers.** ``to_json_str`` / ``from_json_str`` do no I/O.
- **Explicit path only.** ``write_record`` / ``read_record`` take a caller-provided
  ``Path``. There is no default location and no automatic output directory.
- **Parent must already exist.** ``write_record`` never creates directories.
- **No symlinks.** Both the target file and its parent directory are refused
  if either is a symlink.
- **No silent overwrite.** ``write_record`` refuses an existing target unless
  ``overwrite=True``.
- **Atomic write.** The record is written to a uniquely named temporary file in
  the same (validated) directory, then ``os.replace``d into place.
- **Bounded read.** ``read_record`` refuses a file larger than
  ``MAX_RECORD_BYTES`` before reading it.
- **Sanitized errors.** Every failure raises ``EvaluationSerializationError``
  with a fixed message -- never the path, the file content, the record content,
  or a raw exception message.
- **No database.** Nothing here opens DuckDB or any database.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from pydantic import ValidationError

from market_intelligence.evaluation.contracts import EvaluationRunRecord

MAX_RECORD_BYTES = 1_000_000


class EvaluationSerializationError(RuntimeError):
    """Sanitized serialization failure.

    Never includes a filesystem path, the file's bytes, the record's content,
    or a raw underlying exception message.
    """


def to_json_str(record: EvaluationRunRecord) -> str:
    """Serialize ``record`` to a deterministic, pretty-printed JSON string.

    Keys are sorted and the output ends with a single newline, so re-serializing
    an unchanged record is byte-stable.
    """
    payload = record.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def from_json_str(text: str) -> EvaluationRunRecord:
    """Parse and validate an ``EvaluationRunRecord`` from a JSON string."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise EvaluationSerializationError("evaluation record is not valid JSON") from None
    if not isinstance(payload, dict):
        raise EvaluationSerializationError("evaluation record must be a JSON object")
    try:
        return EvaluationRunRecord.model_validate(payload)
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


def write_record(
    record: EvaluationRunRecord,
    path: str | os.PathLike[str],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write ``record`` to ``path`` as JSON.

    ``path``'s parent directory must already exist and must not be a symlink;
    ``path`` itself must not be a symlink; and, unless ``overwrite`` is true, it
    must not already exist. The write goes to a temporary file in the same
    directory and is then ``os.replace``d into place.
    """
    target = Path(path)
    directory = _validated_directory(target)

    if target.exists() and not overwrite:
        raise EvaluationSerializationError("target file already exists")

    text = to_json_str(record)
    tmp_name = f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    tmp_path = directory / tmp_name
    try:
        with open(tmp_path, "x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_path, target)
    except OSError:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise EvaluationSerializationError("failed to write evaluation record") from None


def read_record(path: str | os.PathLike[str]) -> EvaluationRunRecord:
    """Read and validate an ``EvaluationRunRecord`` from ``path``.

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
