"""Manual, one-shot Alpaca historical-bars ingestion into the local
market_bars table.

Makes at most one bounded, explicit, read-only Alpaca bars request (via
``AlpacaBarsClient.get_bars``, always with the connector's fixed IEX/raw/USD
provenance -- see ``market_intelligence/data_connectors/alpaca_bars.py``)
for a single supplied symbol/timeframe/window, then stores the normalized
results through ``BarRepository``. This script performs no indicator,
prediction, or trading logic -- storage only.

Command-line ``--symbol``, ``--timeframe``, ``--start``, ``--end``,
``--limit``, and ``--max-pages`` are all strictly validated before any
network request is constructed: an invalid ``--limit``/``--max-pages`` value
is rejected by argparse itself (before ``Settings``/the bars client are ever
created), and an invalid ``--symbol``/``--timeframe``/``--start``/``--end``
is rejected by the same normalization the connector uses internally, with
zero HTTP requests and zero database writes made in any invalid-input case.
The normalized ``start``/``end`` are also compared here (rejecting
``start >= end``) before ``Settings``, the bars client, any network request,
or any database construction happens -- not left to be discovered later
inside the connector or the repository.

Database initialization and repository storage are also wrapped: a DuckDB
initialization failure or a ``BarStorageError`` from storage is caught and
reported as a fixed, sanitized outcome/category and a nonzero exit code --
never a raw traceback containing SQL, paths, prices, or other internals, and
never a falsely reported success.

If ``--start``/``--end`` are omitted, a small, clearly bounded, fully
completed historical window is used by default: the DEFAULT_LOOKBACK_DAYS
calendar days immediately preceding the start of the current UTC day. Using
the start of the current UTC day (rather than "now") as the default window's
end deliberately excludes today's still-in-progress trading session, so the
default window only ever covers completed history, never a partial/live bar.

Only sanitized metadata is ever printed: configured, fetch outcome, symbol,
timeframe, feed, adjustment, currency, and received/inserted/existing-or-
updated/failed counts plus the ingestion-run status. Never OHLCV values,
individual bar timestamps, database rows, credentials, headers, URLs, raw
responses, or page tokens.

This script is not run live as part of implementing this storage layer --
live ingestion requires separate, explicit authorization.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import (
    DATA_ADJUSTMENT,
    DATA_CURRENCY,
    DATA_FEED,
    AlpacaBarsClient,
    AlpacaBarsCredentialsMissingError,
    AlpacaBarsError,
    AlpacaBarsInvalidInputError,
    normalize_limit,
    normalize_max_pages,
    normalize_timeframe,
    normalize_timestamp,
)
from market_intelligence.data_connectors.alpaca_market_data import (
    AlpacaInvalidSymbolError,
    normalize_symbol,
)
from market_intelligence.storage.bar_repository import BarRepository, BarStorageError
from market_intelligence.storage.database import DuckDBManager

DEFAULT_SYMBOL = "SPY"
DEFAULT_TIMEFRAME = "5Min"
DEFAULT_LOOKBACK_DAYS = 5
DEFAULT_LIMIT = 500
DEFAULT_MAX_PAGES = 1


def _default_window() -> tuple[str, str]:
    """A small, bounded, fully completed historical window ending at the start of today (UTC)."""
    end_dt = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start_dt = end_dt - timedelta(days=DEFAULT_LOOKBACK_DAYS)
    return (
        start_dt.isoformat().replace("+00:00", "Z"),
        end_dt.isoformat().replace("+00:00", "Z"),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-shot, bounded, read-only Alpaca historical-bars ingestion."
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, settings: Settings | None = None) -> int:
    """Run one ingestion pass. ``settings`` is an injection point for tests only.

    Real usage never passes ``settings`` -- it always defaults to a fresh
    ``Settings()`` reading the process environment/local ``.env``.
    """
    args = _parse_args(argv)

    # Validate before constructing Settings/any client, so malformed input
    # never reaches the network and never triggers a database write.
    try:
        symbol = normalize_symbol(args.symbol)
        timeframe = normalize_timeframe(args.timeframe)
        default_start, default_end = _default_window()
        start = normalize_timestamp(args.start if args.start is not None else default_start,
                                     field_name="start")
        end = normalize_timestamp(args.end if args.end is not None else default_end,
                                   field_name="end")
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        if start_dt >= end_dt:
            raise AlpacaBarsInvalidInputError(
                "Invalid start/end: start must be strictly before end."
            )
        limit = normalize_limit(args.limit)
        max_pages = normalize_max_pages(args.max_pages)
    except (AlpacaInvalidSymbolError, AlpacaBarsInvalidInputError) as exc:
        print("configured: unknown")
        print("fetch outcome: invalid_input")
        print("symbol: (invalid)")
        print(f"error: {exc}")
        return 2

    settings = settings or Settings()
    client = AlpacaBarsClient(settings=settings)
    configured = client.is_configured()
    print(f"configured: {configured}")

    if not configured:
        print("fetch outcome: not_configured")
        print(f"symbol: {symbol}")
        print(f"timeframe: {timeframe}")
        return 1

    try:
        bars = client.get_bars(
            symbol, timeframe, start, end, limit=limit, max_pages=max_pages
        )
    except AlpacaBarsCredentialsMissingError:
        print("fetch outcome: not_configured")
        print(f"symbol: {symbol}")
        print(f"timeframe: {timeframe}")
        return 1
    except AlpacaBarsError as exc:
        print("fetch outcome: failed")
        print(f"symbol: {symbol}")
        print(f"timeframe: {timeframe}")
        print(f"error category: {type(exc).__name__}")
        return 1

    print("fetch outcome: success")
    print(f"symbol: {symbol}")
    print(f"timeframe: {timeframe}")
    print(f"feed: {DATA_FEED}")
    print(f"adjustment: {DATA_ADJUSTMENT}")
    print(f"currency: {DATA_CURRENCY}")
    print(f"received: {len(bars)}")

    try:
        DuckDBManager(settings=settings).initialize()
    except Exception:
        print("storage outcome: failed")
        print("error category: database_initialization_failed")
        return 1

    repository = BarRepository(settings=settings)
    try:
        result = repository.store_bars(bars, provider="alpaca")
    except BarStorageError:
        print("storage outcome: failed")
        print("error category: storage_error")
        return 1

    print(f"inserted: {result.inserted}")
    print(f"existing/updated: {result.existing_or_updated}")
    print(f"failed: {result.failed}")
    print(f"ingestion-run status: {result.ingestion_run_status}")

    return 0 if result.ingestion_run_status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
