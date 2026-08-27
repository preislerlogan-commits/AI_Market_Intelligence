"""Offline Macro characterization *completion* CLI.

Dry-run / validate by default. Reads two local JSON files:

- ``--record`` -- the scaffold ``EvaluationRunRecord`` produced offline by
  ``scripts/characterize_macro_report.py --write`` (empty ``adjudications``);
- ``--adjudications`` -- a ``MacroAdjudicationInput`` (the scaffold's ``run_id``
  plus the completed human ``CitationAdjudication`` list).

It requires the adjudication ``run_id`` to match the scaffold ``run_id``,
attaches the completed human adjudications via
``macro_characterization_workflow.complete_macro_characterization`` (which
refuses a missing, duplicate, or unexpected pair), and prints a **sanitized**
summary: counts and classification / finding-severity tallies only. It never
prints a reviewer note, a claim id, a citation id, a filesystem path, or any
record / evidence text.

Boundaries (all enforced below):

- **Offline only.** Zero network requests, zero database access, zero OpenAI /
  agent-runtime calls, no live Macro Analyst run.
- **Records human decisions only.** This command never generates, recommends,
  or second-guesses a citation classification, and there is no LLM judge
  anywhere in it. The four classifications ``supported`` /
  ``partially_supported`` / ``unsupported`` / ``unable_to_determine`` are
  preserved exactly as the reviewer recorded them. **Completion is not
  validation** (see ``COMPLETION_IS_NOT_VALIDATION``).
- **Path boundary.** All three of ``--record``, ``--adjudications`` and
  ``--output`` must resolve to a location strictly inside the gitignored
  ``data/evaluations/local/`` directory. A repository-root, tracked
  (``docs/`` / ``tests/`` / ``fixtures/``), ``data/evaluations/``-itself,
  outside-repository, ``..``-traversal, or symlink-escape path is refused.
- **Write only with ``--write``.** The dry run writes nothing. ``--write``
  serializes the completed ``EvaluationRunRecord`` to ``--output`` via
  ``evaluation.serialization.write_record``: atomic, never overwrites an
  existing file, refuses a symlinked target/parent, never creates a directory.

No real characterization has been performed anywhere in this repository, and
running this CLI on a synthetic fixture does not constitute one. Phase 0 remains
open (P0-7 unmet).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from market_intelligence.evaluation.contracts import (
    CitationClassification,
    FindingSeverity,
)
from market_intelligence.evaluation.macro_characterization_input import (
    read_adjudication_input,
)
from market_intelligence.evaluation.macro_characterization_workflow import (
    COMPLETION_IS_NOT_VALIDATION,
    MacroCharacterizationError,
    complete_macro_characterization,
)
from market_intelligence.evaluation.rubric import check_rubric_completeness
from market_intelligence.evaluation.serialization import (
    EvaluationSerializationError,
    read_record,
    write_record,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory any of the three paths may resolve into. It is gitignored,
# so real characterization scaffolds, adjudications, and completed records stay
# local and uncommitted.
_ALLOWED_DIR = _REPO_ROOT / "data" / "evaluations" / "local"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline Macro characterization completion (dry-run by default; "
            "zero network, database, or OpenAI access). Records human citation "
            "adjudications onto a scaffold; it never generates or recommends a "
            "classification, and completion is not validation."
        )
    )
    parser.add_argument(
        "--record",
        dest="record_path",
        required=True,
        help=(
            "Explicit path to the scaffold EvaluationRunRecord JSON. Its "
            "resolved location must be inside data/evaluations/local/."
        ),
    )
    parser.add_argument(
        "--adjudications",
        dest="adjudications_path",
        required=True,
        help=(
            "Explicit path to the MacroAdjudicationInput JSON (run_id + the "
            "completed human CitationAdjudication list). Its resolved location "
            "must be inside data/evaluations/local/."
        ),
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help=(
            "Explicit path for the completed EvaluationRunRecord JSON output. "
            "Only used with --write. Its resolved location must be inside "
            "data/evaluations/local/, and its parent directory must already "
            "exist."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Serialize the completed EvaluationRunRecord to --output. Omitted "
            "by default (dry run: nothing is written). Never overwrites an "
            "existing file and never creates a directory."
        ),
    )
    return parser.parse_args(argv)


def _path_is_refused(path: Path) -> bool:
    """True unless ``path`` resolves strictly inside ``data/evaluations/local/``.

    ``resolve()`` collapses ``..`` segments and follows symlinks, so a target
    that escapes the allowed directory by either route lands outside it and is
    refused. The allowed directory itself (a non-file target) is also refused.
    """
    try:
        resolved = path.resolve()
        allowed = _ALLOWED_DIR.resolve()
    except OSError:
        return True
    if resolved == allowed:
        return True
    try:
        resolved.relative_to(allowed)
    except ValueError:
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    record_path = Path(args.record_path)
    adjudications_path = Path(args.adjudications_path)
    output_path = Path(args.output_path)

    if _path_is_refused(record_path):
        print(json.dumps({"error": "record_path_refused"}))
        return 2
    if _path_is_refused(adjudications_path):
        print(json.dumps({"error": "adjudications_path_refused"}))
        return 2
    if _path_is_refused(output_path):
        print(json.dumps({"error": "output_path_refused"}))
        return 2

    try:
        scaffold = read_record(record_path)
    except EvaluationSerializationError:
        print(json.dumps({"error": "invalid_record"}))
        return 2
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    try:
        adjudication_input = read_adjudication_input(adjudications_path)
    except EvaluationSerializationError:
        print(json.dumps({"error": "invalid_adjudications"}))
        return 2
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    if adjudication_input.run_id != scaffold.run_id:
        print(json.dumps({"error": "run_id_mismatch"}))
        return 2

    try:
        completed = complete_macro_characterization(
            scaffold, adjudication_input.adjudications
        )
    except MacroCharacterizationError:
        print(json.dumps({"error": "completion_failed"}))
        return 2
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    rubric = check_rubric_completeness(completed)
    classification_tally = {
        classification.value: rubric.classification_tally.get(classification, 0)
        for classification in CitationClassification
    }
    finding_severity_tally = {
        severity.value: sum(1 for f in completed.findings if f.severity is severity)
        for severity in FindingSeverity
    }

    payload: dict[str, object] = {
        "mode": "write" if args.write else "dry_run",
        "expected_pair_count": len(completed.expected_pairs),
        "adjudication_count": len(completed.adjudications),
        "classification_tally": classification_tally,
        "finding_severity_tally": finding_severity_tally,
        "rubric_complete": rubric.complete,
        "completion_is_not_validation": COMPLETION_IS_NOT_VALIDATION,
        "output_written": False,
    }

    if args.write:
        try:
            write_record(completed, output_path)
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
