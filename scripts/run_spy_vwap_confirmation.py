"""Offline SPY VWAP-reversion confirmation-analysis CLI (preregistration
``docs/SPY_VWAP_REVERSION_PREREGISTRATION.md``, clarification C1).

Dry-run by default. Reads one local, canonical
``SpyVwapReversionEvaluationInput`` JSON file, runs the existing, unmodified
evaluator in-process, re-parses the record from its exact canonical bytes,
and runs the pure confirmation analysis on that record. It makes **zero**
network requests and **zero** database access. Underlying setup only: no
option, contract, P&L, recommendation, alert, agent, or execution output.

Required: ``--sample confirmation|holdout``, ``--input``, ``--record-output``,
``--confirmation-output``, and ``--code-commit`` (exactly 40 lowercase
hexadecimal characters; validated before anything else runs, never inferred).

Provenance: the input file's bytes must equal the canonical serialization
of the validated input, and the input SHA-256 is taken over those canonical
bytes. The evaluation-record SHA-256 is taken over the record's canonical
bytes -- the same bytes that are analyzed and, with ``--write``, saved.
Neither hash is computed from a path or from reformatted JSON. The hashes
identify the claimed source bytes; they do not prove authenticity. The
``--code-commit`` value is operator-supplied provenance, validated for shape
only -- this CLI does not (and cannot) attest which code actually ran.
A written result can be checked for statistical correctness with
``spy_vwap_reversion_confirmation.verify_confirmation_result``.

``--write`` publishes two files, both of which must resolve strictly inside
gitignored ``data/evaluations/local/``: any ``..`` segment (even one that
resolves back inside), a symlink or junction component, an existing target,
a missing parent directory, or two identical paths is refused. Both paths
are preflighted before any evaluation and again immediately before writing.
Neither file is ever overwritten, and no directory is ever created.

**Publication order and partial failure.** The two files cannot be published
as one atomic unit. The evaluation record is written first, then the
confirmation result, each atomically with no overwrite. If the record write
fails, nothing is written (``record_write_failed``). If the result write
fails after the record was written, the record is left in place -- it is a
complete, valid evaluation record -- nothing is deleted, and the CLI reports
``confirmation_write_failed`` with ``record_written: true`` and
``confirmation_written: false``, exit code 1.

Output is sanitized: counts, gate states, statuses, and labels only. It
never prints paths, hashes, estimates, prices, bars, decision-point dates,
or record content. Errors are fixed markers. Exit codes: ``0`` success,
``2`` refused or invalid input/arguments, ``1`` unexpected or write failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.evaluation.spy_vwap_reversion_confirmation import (
    ConfirmationAnalysisError,
    analyze_confirmation,
    is_valid_commit_sha,
)
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_contracts import (
    SpyVwapConfirmationResult,
    StudySample,
)
from market_intelligence.evaluation.spy_vwap_reversion_confirmation_serialization import (
    write_result,
)
from market_intelligence.evaluation.spy_vwap_reversion_contracts import SampleStatus
from market_intelligence.evaluation.spy_vwap_reversion_evaluator import (
    evaluate_spy_vwap_reversion,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    MAX_INPUT_BYTES,
    EvaluationSerializationError,
    from_json_str,
    input_to_json_str,
    read_input,
    to_json_str,
    write_record,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory --write outputs may land in (gitignored).
_ALLOWED_OUTPUT_DIR = _REPO_ROOT / "data" / "evaluations" / "local"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline, preregistered SPY VWAP confirmation analysis (dry-run by "
            "default; zero network or database access)."
        )
    )
    parser.add_argument("--sample", required=True, choices=[sample.value for sample in StudySample])
    parser.add_argument("--input", dest="input_path", required=True)
    parser.add_argument("--record-output", dest="record_output", required=True)
    parser.add_argument("--confirmation-output", dest="confirmation_output", required=True)
    parser.add_argument("--code-commit", dest="code_commit", required=True)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Publish both outputs (never overwrites; never creates directories).",
    )
    return parser.parse_args(argv)


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, indent=2))


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _output_refusal(path: Path) -> str | None:
    """A fixed refusal reason for one ``--write`` output path, or ``None``."""
    if ".." in path.parts:
        return "traversal"
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(_ALLOWED_OUTPUT_DIR)
    except ValueError:
        return "outside_allowed_directory"
    if not relative.parts:
        return "outside_allowed_directory"
    # No link anywhere from the repository's data/ directory down to the target.
    chain = [_REPO_ROOT / "data", _REPO_ROOT / "data" / "evaluations", _ALLOWED_OUTPUT_DIR]
    current = _ALLOWED_OUTPUT_DIR
    for part in relative.parts:
        current = current / part
        chain.append(current)
    if any(_is_link(link) for link in chain):
        return "symlink_component"
    try:
        resolved = absolute.resolve()
        resolved.relative_to(_ALLOWED_OUTPUT_DIR.resolve())
    except (OSError, ValueError):
        return "outside_allowed_directory"
    if absolute.exists():
        return "exists"
    if not absolute.parent.is_dir():
        return "missing_parent"
    return None


def _preflight(record_output: Path, confirmation_output: Path) -> str | None:
    if Path(os.path.abspath(record_output)) == Path(os.path.abspath(confirmation_output)):
        return "same_path"
    return _output_refusal(record_output) or _output_refusal(confirmation_output)


def _read_bounded_bytes(path: Path) -> bytes:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise EvaluationSerializationError("evaluation input file is too large")
    return path.read_bytes()


def _summary(result: SpyVwapConfirmationResult, *, write: bool) -> dict[str, object]:
    """Counts, gate states, statuses, and labels only."""
    counts = result.counts
    return {
        "mode": "write" if write else "dry_run",
        "sample": result.sample.value,
        "unique_session_count": counts.unique_session_count,
        "complete_session_count": counts.complete_session_count,
        "incomplete_session_count": counts.incomplete_session_count,
        "eligible_above_vwap_count": counts.eligible_above_vwap_count,
        "eligible_below_vwap_count": counts.eligible_below_vwap_count,
        "overall_gate": result.overall_gate.status.value,
        "primary": {
            item.horizon.value: {
                "cell_gate": item.cell.status.value,
                "session_count": item.cell.session_count,
                "status": item.status.value,
            }
            for item in result.primary.horizons
        },
        "study_label": result.primary.study_label.value,
        "below_vwap": {
            item.horizon.value: {
                "cell_gate": item.cell.status.value,
                "session_count": item.cell.session_count,
                "status": item.status.value,
            }
            for item in result.secondary.below_horizons
        },
        "close_minus_30m_contrast": {
            "cell_gate": result.secondary.close_minus_30m_contrast.cell.status.value,
            "paired_session_count": (
                result.secondary.close_minus_30m_contrast.cell.paired_session_count
            ),
            "status": result.secondary.close_minus_30m_contrast.status.value,
        },
        "secondary_label": result.secondary.secondary_label.value,
        "subgroup_cells_ok": sum(
            1 for item in result.subgroups if item.cell.status == SampleStatus.OK
        ),
        "subgroup_cells_insufficient_sample": sum(
            1 for item in result.subgroups if item.cell.status != SampleStatus.OK
        ),
        "notes": list(result.notes),
        "record_written": False,
        "confirmation_written": False,
    }


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    args = _parse_args(argv)
    if not is_valid_commit_sha(args.code_commit):
        _emit({"error": "invalid_code_commit"})
        return 2
    sample = StudySample(args.sample)
    input_path = Path(args.input_path)
    record_output = Path(args.record_output)
    confirmation_output = Path(args.confirmation_output)

    if args.write:
        refusal = _preflight(record_output, confirmation_output)
        if refusal is not None:
            _emit({"error": "output_path_refused", "reason": refusal})
            return 2

    moment = now or datetime.now(UTC)

    try:
        evaluation_input = read_input(input_path)
        canonical_input = input_to_json_str(evaluation_input).encode("utf-8")
        supplied_input = _read_bounded_bytes(input_path)
    except (EvaluationSerializationError, OSError):
        _emit({"error": "invalid_input"})
        return 2
    except Exception:
        _emit({"error": "unexpected_error"})
        return 1
    if supplied_input != canonical_input:
        _emit({"error": "input_not_canonical"})
        return 2
    input_sha256 = hashlib.sha256(canonical_input).hexdigest()

    try:
        record_text = to_json_str(
            evaluate_spy_vwap_reversion(evaluation_input, generated_at=moment)
        )
        record = from_json_str(record_text)
        if to_json_str(record) != record_text:
            raise ValueError("record serialization is not byte-stable")
        record_sha256 = hashlib.sha256(record_text.encode("utf-8")).hexdigest()
        result = analyze_confirmation(
            record,
            sample=sample,
            input_sha256=input_sha256,
            evaluation_record_sha256=record_sha256,
            code_commit_sha=args.code_commit,
            generated_at=moment,
        )
    except ConfirmationAnalysisError as exc:
        _emit({"error": "analysis_refused", "reason": exc.reason.value})
        return 2
    except Exception:
        _emit({"error": "unexpected_error"})
        return 1

    summary = _summary(result, write=args.write)

    if args.write:
        refusal = _preflight(record_output, confirmation_output)
        if refusal is not None:
            _emit({"error": "output_path_refused", "reason": refusal})
            return 2
        try:
            write_record(record, record_output)
        except Exception:
            _emit(
                {
                    "error": "record_write_failed",
                    "record_written": False,
                    "confirmation_written": False,
                }
            )
            return 1
        try:
            write_result(result, confirmation_output)
        except Exception:
            _emit(
                {
                    "error": "confirmation_write_failed",
                    "record_written": True,
                    "confirmation_written": False,
                }
            )
            return 1
        summary["record_written"] = True
        summary["confirmation_written"] = True

    _emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
