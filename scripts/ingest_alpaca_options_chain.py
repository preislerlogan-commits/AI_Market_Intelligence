"""Dry-run-first, one-shot read-only SPY option-chain snapshot ingestion.

Default behaviour is a **dry run**: it validates the command-line arguments,
normalizes them into a bounded ``OptionChainRequest``, constructs
``Settings`` only to report whether the Alpaca provider is configured, and
prints a sanitized plan (intended feed, requested expiration/strike window,
optional call/put filter, page/contract bounds). A dry run makes **no HTTP
request and writes nothing**.

``--execute`` performs exactly one logical bounded ingestion: it initializes
the local database to the latest migration, makes the paginated
``GET /v1beta1/options/snapshots/SPY`` requests required to cover the
bounded chain (no automatic retry), and writes -- in one transaction --
exactly one run-level ``option_chain_snapshot_batches`` provenance row plus
the normalized snapshot rows, through ``OptionChainSnapshotRepository``. A
successful retrieval that returned zero contracts still writes its batch row
(``contract_count = 0``, ``outcome = skipped_empty``) and zero snapshot rows.

Scope: SPY only, ``data.alpaca.markets`` only, read-only. This script has no
account, order, position, portfolio, exercise, or execution surface, and it
never touches ``paper-api.alpaca.markets``, the option-contract/trading API,
or Robinhood.

Output -- in both modes -- is sanitized: mode, configured flag, intended
feed, the requested window bounds, page/contract bounds, and sanitized
fetch/storage counts and status only. It never prints a contract symbol, a
quote, a Greek, an implied volatility, response text, a URL, a query
parameter, a page token, or a credential.

This script is not run live as part of implementing this connector/storage
layer -- a live option-chain request requires separate, explicit
authorization.
"""

from __future__ import annotations

import argparse

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_options_chain import (
    DEFAULT_LIMIT,
    AlpacaOptionsChainClient,
    AlpacaOptionsChainError,
    AlpacaOptionsChainInvalidInputError,
    AlpacaOptionsCredentialsMissingError,
    OptionChainRequest,
    normalize_option_chain_request,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.option_chain_snapshot_repository import (
    OptionChainSnapshotRepository,
    OptionChainSnapshotStorageError,
)

DEFAULT_UNDERLYING = "SPY"
DEFAULT_MAX_PAGES = 1
DEFAULT_MAX_CONTRACTS = 5_000


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Dry-run-first, one-shot read-only SPY option-chain snapshot ingestion."
        )
    )
    parser.add_argument("--feed", required=True, choices=["opra", "indicative"])
    parser.add_argument("--expiration-gte", required=True)
    parser.add_argument("--expiration-lte", required=True)
    parser.add_argument("--strike-gte", required=True)
    parser.add_argument("--strike-lte", required=True)
    parser.add_argument("--type", dest="option_type", default=None, choices=["call", "put"])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--max-contracts", type=int, default=DEFAULT_MAX_CONTRACTS)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually make the bounded request and one transactional local write.",
    )
    return parser.parse_args(argv)


def _print_plan(request: OptionChainRequest, *, configured: bool | None) -> None:
    print("mode: dry_run")
    print(f"configured: {configured}")
    print(f"underlying: {request.underlying}")
    print(f"feed: {request.feed}")
    print(f"expiration_date_gte: {request.expiration_date_gte}")
    print(f"expiration_date_lte: {request.expiration_date_lte}")
    print(f"strike_price_gte: {format(request.strike_price_gte, 'f')}")
    print(f"strike_price_lte: {format(request.strike_price_lte, 'f')}")
    print(f"option_type: {request.option_type or 'any'}")
    print(f"limit: {request.limit}")
    print(f"max_pages: {request.max_pages}")
    print(f"max_total_contracts: {request.max_total_contracts}")
    print("request planned: false (dry run)")


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run one dry-run or execute pass. ``settings`` is a test injection point only."""
    args = _parse_args(argv)

    try:
        request = normalize_option_chain_request(
            underlying=DEFAULT_UNDERLYING,
            feed=args.feed,
            expiration_date_gte=args.expiration_gte,
            expiration_date_lte=args.expiration_lte,
            strike_price_gte=args.strike_gte,
            strike_price_lte=args.strike_lte,
            option_type=args.option_type,
            limit=args.limit,
            max_pages=args.max_pages,
            max_total_contracts=args.max_contracts,
        )
    except AlpacaOptionsChainInvalidInputError as exc:
        print("mode: dry_run" if not args.execute else "mode: execute")
        print("outcome: invalid_input")
        print(f"error: {exc}")
        return 2

    resolved_settings = settings or Settings()
    client = AlpacaOptionsChainClient(settings=resolved_settings)
    configured = client.is_configured()

    if not args.execute:
        _print_plan(request, configured=configured)
        return 0

    print("mode: execute")
    print(f"configured: {configured}")
    print(f"underlying: {request.underlying}")
    print(f"feed: {request.feed}")

    if not configured:
        print("fetch outcome: not_configured")
        return 1

    try:
        batch = client.get_chain_snapshot(request)
    except AlpacaOptionsCredentialsMissingError:
        print("fetch outcome: not_configured")
        return 1
    except AlpacaOptionsChainError as exc:
        print("fetch outcome: failed")
        print(f"error category: {type(exc).__name__}")
        return 1

    snapshots = batch.snapshots
    print("fetch outcome: success")
    print(f"received: {len(snapshots)}")

    try:
        DuckDBManager(settings=resolved_settings).initialize()
    except Exception:
        print("storage outcome: failed")
        print("error category: database_initialization_failed")
        return 1

    repository = OptionChainSnapshotRepository(settings=resolved_settings)
    try:
        result = repository.store_snapshots(
            snapshots, request=request, retrieved_at=batch.retrieved_at
        )
    except OptionChainSnapshotStorageError:
        print("storage outcome: failed")
        print("error category: storage_error")
        return 1
    except Exception:
        print("storage outcome: failed")
        print("error category: unexpected_error")
        return 1

    print("storage outcome: complete")
    print(f"inserted: {result.inserted}")
    print(f"existing/updated: {result.existing_or_updated}")
    print(f"failed: {result.failed}")
    print(f"batch outcome: {result.batch_outcome}")
    print(f"ingestion-run status: {result.ingestion_run_status}")

    return 0 if result.ingestion_run_status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
