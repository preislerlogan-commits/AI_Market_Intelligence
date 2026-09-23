"""Read-only builder CLI: stored SPY 5-minute bars -> VWAP-reversion evaluation input.

Reads the exact-provenance SPY ``5Min`` bars already stored in the local
``market_bars`` table (read-only DuckDB connection -- no write, no
migration) for an explicit ``--start-date``/``--end-date`` range, keeps only
complete, gapless, grid-aligned, OHLC-consistent 78-bar regular sessions,
and assembles one ``SpyVwapReversionEvaluationInput`` -- the file
``scripts/evaluate_spy_vwap_reversion.py --input`` accepts directly. See
``market_intelligence/orchestration/spy_vwap_reversion_input_builder.py``
for the exact rules.

**This is an input-building tool only.** It runs no evaluation and its
output is not an evaluation result, a finding, or evidence of any edge.

Dry run by default: the input is built and validated and a sanitized,
count-only summary is printed; nothing is written. Pass ``--write`` to
serialize the input to ``--output``, which must resolve strictly inside the
gitignored ``data/evaluations/local/`` directory. A ``..`` path segment, a
symlink anywhere between that directory and the target, an existing target
(never overwritten), and a missing parent directory (never created) are all
refused.

Makes **zero** network requests (no Alpaca, FRED, OpenAI, or other
provider). Only sanitized JSON is printed -- never a price, bar timestamp,
database path, SQL text, or raw exception.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

from market_intelligence.config.settings import Settings
from market_intelligence.evaluation.serialization import EvaluationSerializationError
from market_intelligence.evaluation.spy_vwap_reversion_serialization import write_input
from market_intelligence.orchestration.spy_vwap_reversion_input_builder import (
    SpyVwapInputBuildError,
    SpyVwapInputValidationError,
    build_spy_vwap_reversion_input,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory a --write output may land in (gitignored).
_ALLOWED_OUTPUT_DIR = _REPO_ROOT / "data" / "evaluations" / "local"

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_NOTES = (
    "input_building_only_not_an_evaluation_result",
    "not_evidence_of_edge",
    "point_in_time_context_narrowed",
    "next_session_unavailable_by_evaluator_design",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build a SPY VWAP-reversion evaluation input from stored 5-minute "
            "bars (read-only; dry-run by default; zero network access)."
        )
    )
    parser.add_argument(
        "--start-date",
        required=True,
        help="First America/New_York session date, YYYY-MM-DD (inclusive).",
    )
    parser.add_argument(
        "--end-date",
        required=True,
        help="Last America/New_York session date, YYYY-MM-DD (inclusive).",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help=(
            "Explicit path for the SpyVwapReversionEvaluationInput JSON. Only "
            "used with --write; must resolve inside data/evaluations/local/, "
            "must not exist, and its parent directory must already exist."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write the input to --output. Omitted by default (dry run).",
    )
    return parser.parse_args(argv)


def _parse_date(value: str) -> date:
    if not _DATE_PATTERN.fullmatch(value):
        raise SpyVwapInputValidationError("date must be YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SpyVwapInputValidationError("date must be YYYY-MM-DD") from None


def _output_is_refused(output: Path) -> bool:
    """True unless ``output`` is a not-yet-existing file path strictly inside
    ``data/evaluations/local/``, reached without any ``..`` segment and
    without any symlink between that directory and the target."""
    if ".." in output.parts:
        return True
    try:
        allowed = _ALLOWED_OUTPUT_DIR.resolve(strict=True)
        absolute = output if output.is_absolute() else Path.cwd() / output
        resolved = absolute.resolve()
    except OSError:
        return True
    if resolved == allowed:
        return True
    try:
        resolved.relative_to(allowed)
    except ValueError:
        return True
    # Refuse a symlink at the target or at any component of the given path
    # (resolve() alone would silently follow one that stays inside).
    for candidate in (absolute, *absolute.parents):
        if candidate.is_symlink():
            return True
    if absolute.exists() or not absolute.parent.is_dir():
        return True
    return False


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Build (and optionally write) one input. ``settings`` is a test injection point."""
    args = _parse_args(argv)
    output_path = Path(args.output_path)

    try:
        start_date = _parse_date(args.start_date)
        end_date = _parse_date(args.end_date)
    except SpyVwapInputValidationError:
        print(json.dumps({"error": "invalid_input"}))
        return 2

    if args.write and _output_is_refused(output_path):
        print(json.dumps({"error": "output_path_refused"}))
        return 2

    try:
        result = build_spy_vwap_reversion_input(
            start_date=start_date, end_date=end_date, settings=settings
        )
    except SpyVwapInputValidationError:
        print(json.dumps({"error": "invalid_input"}))
        return 2
    except SpyVwapInputBuildError:
        print(json.dumps({"error": "storage_error"}))
        return 1
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    payload: dict[str, object] = {"mode": "write" if args.write else "dry_run"}
    payload.update(result.summary.to_dict())
    payload["notes"] = list(_NOTES)
    payload["output_written"] = False

    if result.evaluation_input is None:
        payload["error"] = "no_complete_sessions"
        print(json.dumps(payload, indent=2))
        return 1

    if args.write:
        try:
            write_input(result.evaluation_input, output_path)
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
