"""Safe, local, offline JSON round trip for the SPY VWAP confirmation result.

Reuses the existing SPY VWAP evaluation writer
(``spy_vwap_reversion_serialization._write_text_atomically``) and its shared,
sanitized ``EvaluationSerializationError``, so the confirmation result gets
exactly the same guarantees as the evaluation record and input:

- **Pure helpers.** ``result_to_json_str`` / ``result_from_json_str`` do no I/O.
- **Explicit path only**, no default location, no directory creation.
- **No symlinks** for the target or its parent.
- **Never overwrites.** ``write_result`` has no ``overwrite`` parameter.
- **Atomic publish** via a same-directory temporary file and ``os.link``.
- **Bounded read** (``MAX_RESULT_BYTES``) checked before the file is read.
- **Sanitized errors** that never contain a path, value, or raw message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import ValidationError

from market_intelligence.evaluation.serialization import EvaluationSerializationError
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    SpyVwapConfirmationResult,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    _write_text_atomically,
)

# A confirmation result is a fixed-shape summary (a few hundred cells), far
# smaller than an evaluation record; bounded generously but finitely.
MAX_RESULT_BYTES = 5_000_000


def result_to_json_str(result: SpyVwapConfirmationResult) -> str:
    """Deterministic, byte-stable JSON: sorted keys, two-space indent,
    ``ensure_ascii=False``, one trailing newline."""
    payload = result.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def result_from_json_str(text: str) -> SpyVwapConfirmationResult:
    """Parse and validate a ``SpyVwapConfirmationResult`` from JSON."""
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise EvaluationSerializationError("confirmation result is not valid JSON") from None
    if not isinstance(payload, dict):
        raise EvaluationSerializationError("confirmation result must be a JSON object")
    try:
        return SpyVwapConfirmationResult.model_validate(payload)
    except ValidationError:
        raise EvaluationSerializationError("confirmation result failed schema validation") from None


def write_result(result: SpyVwapConfirmationResult, path: str | os.PathLike[str]) -> None:
    """Atomically write ``result`` to ``path``. Never overwrites an existing
    file, never creates a directory, and refuses a symlinked target/parent."""
    _write_text_atomically(
        result_to_json_str(result),
        Path(path),
        overwrite=False,
        failure_message="failed to write confirmation result",
    )


def read_result(path: str | os.PathLike[str]) -> SpyVwapConfirmationResult:
    """Read and validate a ``SpyVwapConfirmationResult``. Refuses a
    symlinked file or parent and a file larger than ``MAX_RESULT_BYTES``
    before reading it."""
    source = Path(path)
    if source.parent.is_symlink():
        raise EvaluationSerializationError("parent directory is a symlink")
    if source.is_symlink():
        raise EvaluationSerializationError("target path is a symlink")
    if not source.exists():
        raise EvaluationSerializationError("confirmation result file does not exist")
    if not source.is_file():
        raise EvaluationSerializationError("confirmation result path is not a file")
    try:
        size = source.stat().st_size
    except OSError:
        raise EvaluationSerializationError("failed to stat confirmation result") from None
    if size > MAX_RESULT_BYTES:
        raise EvaluationSerializationError("confirmation result file is too large")
    try:
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise EvaluationSerializationError("failed to read confirmation result") from None
    return result_from_json_str(text)
