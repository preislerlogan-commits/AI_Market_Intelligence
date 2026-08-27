"""Offline Macro characterization workflow CLI.

Dry-run / validate by default: reads one local ``MacroCharacterizationInput``
JSON file (explicit ``--input`` path), runs the existing deterministic Macro
factual-transcription check for every claim, and prints a sanitized summary plus
the pending human-adjudication templates (one per expected claim/citation
pair). It makes **zero** network requests, **zero** database access, and
**zero** OpenAI / agent-runtime calls, and it never performs a live Macro
Analyst run.

Pass ``--write`` to serialize the resulting ``EvaluationRunRecord`` (with an
empty ``adjudications`` list -- citation support is never pre-classified) to an
explicit ``--output`` path, reusing ``evaluation.serialization.write_record``:
it refuses to overwrite an existing file, refuses a symlinked target/parent,
and never creates a directory. The resolved output path must be **inside**
``data/evaluations/local/`` (the gitignored local-only home for real
characterization inputs and outputs); any path outside that directory --
including one reached via ``..`` traversal or a symlink -- is refused.

No real characterization has been performed anywhere in this repository, and
running this CLI on a synthetic fixture does not constitute one. Phase 0 remains
open (P0-7 unmet).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.evaluation.macro_characterization_input import (
    read_characterization_input,
)
from market_intelligence.evaluation.macro_characterization_workflow import (
    build_macro_characterization,
)
from market_intelligence.evaluation.serialization import (
    EvaluationSerializationError,
    write_record,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory a ``--write`` output may land in. It is gitignored, so real
# characterization inputs and outputs stay local and uncommitted; everything
# else (the repository root, docs/, tests/, fixtures/, data/evaluations/ itself,
# and any path outside the repository) is refused.
_ALLOWED_OUTPUT_DIR = _REPO_ROOT / "data" / "evaluations" / "local"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline Macro characterization workflow (dry-run by default; zero "
            "network, database, or OpenAI access)."
        )
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        required=True,
        help="Explicit path to a local MacroCharacterizationInput JSON file.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help=(
            "Explicit path for the EvaluationRunRecord JSON output. Only used "
            "with --write. Its resolved location must be inside "
            "data/evaluations/local/, and its parent directory must already "
            "exist."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Serialize the built EvaluationRunRecord to --output. Omitted by "
            "default (dry run: nothing is written). Never overwrites an "
            "existing file and never creates a directory."
        ),
    )
    return parser.parse_args(argv)


def _output_is_refused(output: Path) -> bool:
    """True unless ``output`` resolves to a path inside ``data/evaluations/local/``.

    ``resolve()`` collapses ``..`` segments and follows symlinks, so a target
    that escapes the allowed directory by either route lands outside it and is
    refused. The allowed directory itself (a non-file target) is also refused.
    """
    try:
        resolved = output.resolve()
        allowed = _ALLOWED_OUTPUT_DIR.resolve()
    except OSError:
        return True
    if resolved == allowed:
        return True
    try:
        resolved.relative_to(allowed)
    except ValueError:
        return True
    return False


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    args = _parse_args(argv)
    moment = now or datetime.now(UTC)

    input_path = Path(args.input_path)
    output_path = Path(args.output_path)

    if args.write and _output_is_refused(output_path):
        print(json.dumps({"error": "output_path_refused"}))
        return 2

    try:
        characterization = read_characterization_input(input_path)
    except EvaluationSerializationError:
        print(json.dumps({"error": "invalid_input"}))
        return 2
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    try:
        result = build_macro_characterization(characterization, created_at=moment)
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    match = sum(1 for r in result.transcription_results if r.outcome == "match")
    mismatch = sum(1 for r in result.transcription_results if r.outcome == "mismatch")
    human_review = sum(
        1 for r in result.transcription_results if r.outcome == "human_review"
    )

    payload: dict[str, object] = {
        "mode": "write" if args.write else "dry_run",
        "characterization_label": characterization.characterization_label,
        "claim_count": len(characterization.claims),
        "expected_pair_count": len(characterization.expected_pairs),
        "transcription_outcomes": {
            "match": match,
            "mismatch": mismatch,
            "human_review": human_review,
        },
        "pending_adjudication_count": len(result.pending_adjudications),
        "pending_adjudications": [
            {
                "claim_id": pending.claim_id,
                "citation_id": pending.citation_id,
                "classification": None,
                "reason": None,
            }
            for pending in result.pending_adjudications
        ],
        "output_written": False,
    }

    if args.write:
        try:
            write_record(result.run_record, output_path)
        except EvaluationSerializationError:
            print(json.dumps({"error": "write_failed"}))
            return 1
        except Exception:
            print(json.dumps({"error": "unexpected_error"}))
            return 1
        payload["output_written"] = True

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
