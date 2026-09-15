"""Offline SPY VWAP-extension/reversion evaluation CLI -- step d of Phase 1.

Dry-run / validate by default: reads one local ``SpyVwapReversionEvaluationInput``
JSON file (explicit ``--input`` path -- one or more chronological SPY sessions
of validated 5-minute bars), runs the existing offline evaluator
(``market_intelligence.evaluation.spy_vwap_reversion_evaluator``) against the
existing, unmodified regime-engine thresholds, and prints a sanitized summary.
It makes **zero** network requests and **zero** database access -- it never
reads the real DuckDB database, only a local JSON file the caller supplies an
explicit path to (see requirement to use synthetic fixtures only for this
implementation step).

This evaluates the **underlying SPY setup only** -- there is no option,
contract, recommendation, alert, agent, or execution output anywhere in this
script.

Pass ``--write`` to serialize the resulting ``SpyVwapReversionEvaluationRecord``
to an explicit ``--output`` path, reusing
``spy_vwap_reversion_serialization.write_record``: it refuses to overwrite an
existing file, refuses a symlinked target/parent, and never creates a
directory. The resolved output path must be **inside**
``data/evaluations/local/`` (the gitignored local-only home for real
evaluation inputs and outputs); any path outside that directory -- including
one reached via ``..`` traversal or a symlink -- is refused.

No real historical evaluation has been performed anywhere in this repository
by running this CLI on a synthetic fixture. No threshold is tuned by this
script -- it always uses the existing published ``RegimeThresholds`` and a
fixed sample-size gate, never CLI-supplied overrides.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.evaluation.spy_vwap_reversion_evaluator import (
    evaluate_spy_vwap_reversion,
)
from market_intelligence.evaluation.spy_vwap_reversion_serialization import (
    EvaluationSerializationError,
    read_input,
    write_record,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory a --write output may land in. It is gitignored, so real
# evaluation inputs and outputs stay local and uncommitted; everything else
# (the repository root, docs/, tests/, fixtures/, data/evaluations/ itself,
# and any path outside the repository) is refused.
_ALLOWED_OUTPUT_DIR = _REPO_ROOT / "data" / "evaluations" / "local"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline SPY VWAP-extension/reversion evaluation (dry-run by "
            "default; zero network or database access)."
        )
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        required=True,
        help="Explicit path to a local SpyVwapReversionEvaluationInput JSON file.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help=(
            "Explicit path for the SpyVwapReversionEvaluationRecord JSON "
            "output. Only used with --write. Its resolved location must be "
            "inside data/evaluations/local/, and its parent directory must "
            "already exist."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Serialize the evaluation record to --output. Omitted by "
            "default (dry run: nothing is written). Never overwrites an "
            "existing file and never creates a directory."
        ),
    )
    return parser.parse_args(argv)


def _output_is_refused(output: Path) -> bool:
    """True unless ``output`` resolves to a path inside
    ``data/evaluations/local/``. ``resolve()`` collapses ``..`` segments and
    follows symlinks, so a target that escapes the allowed directory by
    either route lands outside it and is refused. The allowed directory
    itself (a non-file target) is also refused."""
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
        evaluation_input = read_input(input_path)
    except EvaluationSerializationError:
        print(json.dumps({"error": "invalid_input"}))
        return 2
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    try:
        record = evaluate_spy_vwap_reversion(evaluation_input, generated_at=moment)
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    payload: dict[str, object] = {
        "mode": "write" if args.write else "dry_run",
        "symbol": record.symbol,
        "unique_session_count": record.unique_session_count,
        "candidate_decision_point_count": record.candidate_decision_point_count,
        "eligible_observation_count": record.eligible_observation_count,
        "no_signal_vwap_unavailable_count": record.no_signal_vwap_unavailable_count,
        "no_signal_zero_extension_count": record.no_signal_zero_extension_count,
        "regime_counts_eligible_only": {
            regime.value: count for regime, count in record.regime_counts_eligible_only.items()
        },
        "extension_side_counts": {
            side.value: count for side, count in record.extension_side_counts.items()
        },
        "missing_horizon_counts": {
            horizon.value: count for horizon, count in record.missing_horizon_counts.items()
        },
        "notes": record.notes,
        "output_written": False,
    }

    if args.write:
        try:
            write_record(record, output_path)
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
