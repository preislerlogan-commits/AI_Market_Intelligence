"""Offline deterministic SPY options-contract eligibility selector CLI --
Phase 1 step e.

Dry-run / validate by default: reads one local ``ContractSelectorInput`` JSON
file (explicit ``--input`` path -- SPY, one validated scenario-horizon
bucket, one bounded option-chain batch with one retrieval instant, the
underlying price/as-of time, and bounded selector configuration), runs the
existing pure, offline selector
(``market_intelligence.contract_selection.selector.select_eligible_contracts``),
and prints a sanitized summary. It makes **zero** network requests and
**zero** database access -- it never reads the real DuckDB database or calls
a live provider, only a local JSON file the caller supplies an explicit path
to.

This selector runs **without any AI model** and before any future strategy
agent. There is no option, contract recommendation, ranking, score,
prediction, or trade-action output anywhere in this script or in the
selector it calls -- see ``DECISION_RULES.md``, "AI does not choose an
unrestricted options contract".

## Feed-safety boundary: the CLI is authoritative, not the input file

Operational eligibility (``SelectorStatus.ELIGIBLE``) is OPRA-only by
default. An indicative-feed batch can be processed for offline research
**only** when this CLI is invoked with the explicit
``--allow-indicative-research`` flag -- and even then the result status is
``RESEARCH_ONLY``, never ``ELIGIBLE`` (see
``market_intelligence/contract_selection/contracts.py`` and ``selector.py``
for the full feed-safety boundary and its schema-level enforcement).

**This flag always overrides whatever ``SelectorConfig
.allow_indicative_for_research`` the ``--input`` JSON file itself carries**
-- the CLI, not the input file, is the trust boundary for this decision:
without ``--allow-indicative-research``, indicative contracts are rejected/
filtered deterministically (every candidate counted under
``RejectionReason.FEED_NOT_ALLOWED``, status ``NO_ELIGIBLE_CONTRACTS``)
regardless of what the input file requests; with the flag, the input file's
``allow_indicative_for_research`` value is honored as normal. A JSON input
file alone -- however it is constructed -- can therefore never smuggle
indicative-feed contracts past this CLI into an operationally eligible set.

Pass ``--write`` to serialize the resulting ``ContractSelectorResult`` to an
explicit ``--output`` path, reusing
``contract_selection.serialization.write_record``: it refuses to overwrite
an existing file, refuses a symlinked target/parent, and never creates a
directory. The resolved output path must be **inside**
``data/evaluations/local/`` (the gitignored local-only home for real
evaluation/selector inputs and outputs); any path outside that directory --
including one reached via ``..`` traversal or a symlink -- is refused.

No real selector run has been performed anywhere in this repository by
running this CLI. No filter threshold is tuned by this script -- it always
uses whatever ``SelectorConfig`` the supplied input carries (its own
defaults are the published, provisional, centralized thresholds; see
``market_intelligence/contract_selection/contracts.py``), except for
``allow_indicative_for_research``, which this CLI's own
``--allow-indicative-research`` flag always overrides as described above.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from market_intelligence.contract_selection.selector import select_eligible_contracts
from market_intelligence.contract_selection.serialization import (
    ContractSelectorSerializationError,
    read_input,
    write_record,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory a --write output may land in. It is gitignored, so real
# selector inputs and outputs stay local and uncommitted; everything else
# (the repository root, docs/, tests/, fixtures/, data/evaluations/ itself,
# and any path outside the repository) is refused.
_ALLOWED_OUTPUT_DIR = _REPO_ROOT / "data" / "evaluations" / "local"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Offline deterministic SPY options-contract eligibility selector "
            "(dry-run by default; zero network or database access; no model, "
            "recommendation, ranking, score, or trade action)."
        )
    )
    parser.add_argument(
        "--input",
        dest="input_path",
        required=True,
        help="Explicit path to a local ContractSelectorInput JSON file.",
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        required=True,
        help=(
            "Explicit path for the ContractSelectorResult JSON output. Only "
            "used with --write. Its resolved location must be inside "
            "data/evaluations/local/, and its parent directory must already "
            "exist."
        ),
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Serialize the selector result to --output. Omitted by default "
            "(dry run: nothing is written). Never overwrites an existing "
            "file and never creates a directory."
        ),
    )
    parser.add_argument(
        "--allow-indicative-research",
        action="store_true",
        help=(
            "Required before an indicative-feed batch is processed at all. "
            "Without this flag, indicative contracts are deterministically "
            "rejected/filtered (status no_eligible_contracts) regardless of "
            "what the --input file's SelectorConfig requests. With this "
            "flag, the result status for an indicative batch is "
            "research_only -- never eligible. This flag always overrides "
            "the input file's own allow_indicative_for_research value."
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
        selector_input = read_input(input_path)
    except ContractSelectorSerializationError:
        print(json.dumps({"error": "invalid_input"}))
        return 2
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    # The CLI flag is authoritative over the input file's own
    # allow_indicative_for_research value -- see the module docstring,
    # "Feed-safety boundary: the CLI is authoritative, not the input file".
    forced_config = selector_input.config.model_copy(
        update={"allow_indicative_for_research": args.allow_indicative_research}
    )
    selector_input = selector_input.model_copy(update={"config": forced_config})

    try:
        result = select_eligible_contracts(selector_input, generated_at=moment)
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    payload: dict[str, object] = {
        "mode": "write" if args.write else "dry_run",
        "symbol": result.symbol,
        "status": result.status.value,
        "scenario_horizon": result.scenario_horizon.value,
        "requested_option_type": (
            result.requested_option_type.value if result.requested_option_type else None
        ),
        "feed": result.feed.value,
        "feed_is_live_opra": result.feed_is_live_opra,
        "allow_indicative_research": args.allow_indicative_research,
        "candidate_contract_count": result.candidate_contract_count,
        "eligible_contract_count": result.eligible_contract_count,
        "research_only_contract_count": result.research_only_contract_count,
        "rejection_counts": {
            reason.value: count for reason, count in result.rejection_counts.items()
        },
        "notes": result.notes,
        "output_written": False,
    }

    if args.write:
        try:
            write_record(result, output_path)
        except ContractSelectorSerializationError:
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
