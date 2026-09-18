"""Dry-run-first, one-shot live capture of one ``ContractSelectorInput`` --
the smallest coordinator needed for the first real Contract Selector
(step e) run. See
``market_intelligence/orchestration/spy_contract_capture.py`` for the full
gate sequence and design rationale.

Default behaviour is a **dry run**: it constructs ``Settings`` and each of
the three read-only Alpaca clients only to report whether they are
configured, prints the fixed (non-data-dependent) capture plan, and makes
**zero** network requests.

``--execute`` runs the real, live, read-only capture sequence exactly once:
bars, then an underlying-price snapshot, then -- only if the regime and
scenario horizon both resolved -- one bounded indicative option-chain
retrieval. It never writes to any database, makes no model/agent call, and
produces no recommendation, ranking, order, or brokerage action of any kind.

``--write`` (only meaningful together with ``--execute``, and only when the
capture status is ``resolved``) persists the resulting
``ContractSelectorInput`` under gitignored ``data/evaluations/local/`` via
``contract_selection.serialization.write_input`` -- refuses to overwrite an
existing file, refuses a symlinked target/parent, and never creates a
directory. The captured input is not itself a selector result: running
``scripts/select_spy_option_contracts.py --allow-indicative-research`` on it
afterward, as a separate explicit step, is what actually applies the
eligibility filters (and can only ever return
``RESEARCH_ONLY`` / ``NO_ELIGIBLE_CONTRACTS`` / ``INDETERMINATE`` for an
indicative-feed batch -- never ``ELIGIBLE``).

Output is sanitized in both modes: never a raw provider payload, a
credential, a URL, or a per-contract quote. ``--execute`` prints only the
capture status, the provenance timestamps, the underlying price, candidate
contract count, and fixed-vocabulary notes.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from market_intelligence.config.settings import Settings
from market_intelligence.contract_selection.contracts import OptionType, SelectorConfig
from market_intelligence.contract_selection.serialization import (
    ContractSelectorSerializationError,
    write_input,
)
from market_intelligence.data_connectors.alpaca_bars import AlpacaBarsClient
from market_intelligence.data_connectors.alpaca_market_data import AlpacaMarketDataClient
from market_intelligence.data_connectors.alpaca_options_chain import AlpacaOptionsChainClient
from market_intelligence.orchestration.spy_contract_capture import (
    CHAIN_MAX_PAGES_REQUESTED,
    CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED,
    EARLIEST_CAPTURE_TIME,
    MIN_COMPLETED_BARS,
    CaptureStatus,
    capture_contract_selector_input,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The only directory a --write output may land in -- see
# scripts/select_spy_option_contracts.py for the identical, already-reviewed
# allowlist pattern this mirrors.
_ALLOWED_OUTPUT_DIR = _REPO_ROOT / "data" / "evaluations" / "local"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Live, read-only, point-in-time-safe capture of one "
            "ContractSelectorInput (dry-run by default; no model, agent, "
            "recommendation, order, or brokerage action of any kind)."
        )
    )
    parser.add_argument(
        "--output",
        dest="output_path",
        help=(
            "Explicit path for the captured ContractSelectorInput JSON. "
            "Only used with --write. Its resolved location must be inside "
            "data/evaluations/local/, and its parent directory must "
            "already exist."
        ),
    )
    parser.add_argument(
        "--option-type",
        dest="option_type",
        default=None,
        choices=["call", "put"],
        help="Optional explicit call/put filter carried onto the captured input.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run the live, read-only capture sequence.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            "Serialize a resolved capture's ContractSelectorInput to "
            "--output. Only takes effect together with --execute and only "
            "when the capture status is 'resolved'. Never overwrites an "
            "existing file and never creates a directory."
        ),
    )
    return parser.parse_args(argv)


def _output_is_refused(output: Path) -> bool:
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


def _print_dry_run_plan(*, configured: bool, selector_config: SelectorConfig) -> None:
    payload = {
        "mode": "dry_run",
        "configured": configured,
        "underlying": "SPY",
        "feed": "indicative",
        "earliest_capture_time_et": EARLIEST_CAPTURE_TIME.isoformat(),
        "min_completed_bars": MIN_COMPLETED_BARS,
        "option_chain_max_pages": CHAIN_MAX_PAGES_REQUESTED,
        "option_chain_max_total_contracts": CHAIN_MAX_TOTAL_CONTRACTS_REQUESTED,
        "price_recency_max_age_seconds": selector_config.max_quote_age_seconds,
        "request_planned": False,
    }
    print(json.dumps(payload, indent=2))


def _sanitized_timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run one dry-run or execute pass. ``settings`` is a test injection point only."""
    args = _parse_args(argv)

    if args.write and not args.output_path:
        print(json.dumps({"error": "output_required_with_write"}))
        return 2

    output_path = Path(args.output_path) if args.output_path else None
    if args.write and _output_is_refused(output_path):
        print(json.dumps({"error": "output_path_refused"}))
        return 2

    resolved_settings = settings or Settings()
    bars_client = AlpacaBarsClient(settings=resolved_settings)
    market_data_client = AlpacaMarketDataClient(settings=resolved_settings)
    options_chain_client = AlpacaOptionsChainClient(settings=resolved_settings)
    configured = bars_client.is_configured()
    selector_config = SelectorConfig()

    if not args.execute:
        _print_dry_run_plan(configured=configured, selector_config=selector_config)
        return 0

    if not configured:
        print(json.dumps({"mode": "execute", "configured": False, "outcome": "not_configured"}))
        return 1

    requested_option_type = OptionType(args.option_type) if args.option_type else None

    result = capture_contract_selector_input(
        bars_client=bars_client,
        market_data_client=market_data_client,
        options_chain_client=options_chain_client,
        selector_config=selector_config,
        requested_option_type=requested_option_type,
    )

    payload: dict[str, object] = {
        "mode": "execute",
        "status": result.status.value,
        "scenario_horizon": (
            result.scenario_horizon.value if result.scenario_horizon else None
        ),
        "price_recency_max_age_seconds": result.price_recency_max_age_seconds,
        "regime_as_of_timestamp": _sanitized_timestamp(result.regime_as_of_timestamp),
        "underlying_price": (
            format(result.underlying_price, "f") if result.underlying_price is not None else None
        ),
        "underlying_price_timestamp": _sanitized_timestamp(result.underlying_price_timestamp),
        "option_chain_retrieved_at": _sanitized_timestamp(result.option_chain_retrieved_at),
        "candidate_contract_count": result.candidate_contract_count,
        "notes": list(result.notes),
        "output_written": False,
    }

    if args.write and result.status == CaptureStatus.RESOLVED and result.selector_input is not None:
        try:
            write_input(result.selector_input, output_path)
        except ContractSelectorSerializationError:
            print(json.dumps({"error": "write_failed"}))
            return 1
        payload["output_written"] = True
    elif args.write:
        payload["write_skipped_reason"] = "capture_not_resolved"

    print(json.dumps(payload, indent=2))
    return 0 if result.status == CaptureStatus.RESOLVED else 1


if __name__ == "__main__":
    raise SystemExit(main())
