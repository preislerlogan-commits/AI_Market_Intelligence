"""One-shot, read-only market-context snapshot CLI.

Builds exactly one sanitized, JSON-ready market-context snapshot from data
already stored in the local DuckDB database (see
``market_intelligence/market_features/market_context.py``) and prints it to
stdout. Makes no network request of any kind and writes nothing to the
database -- read-only end to end.

All arguments are strictly validated before any DuckDB connection is
opened: an unrecognized argument is rejected by argparse itself, and an
invalid ``--symbol``/``--recent-bars-limit``/``--recent-news-limit``/
``--macro-series`` is rejected by ``MarketContextBuilder`` before any
storage access. Only sanitized JSON is ever printed -- never a raw
exception, database path, or SQL. See
``docs/MARKET_CONTEXT_SNAPSHOT.md`` for the full field contract and its
limitations.
"""

from __future__ import annotations

import argparse
import json

from market_intelligence.market_features.market_context import (
    DEFAULT_MACRO_SERIES_IDS,
    DEFAULT_RECENT_BARS_LIMIT,
    DEFAULT_RECENT_NEWS_LIMIT,
    MarketContextBuilder,
    MarketContextError,
    MarketContextValidationError,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one read-only market-context snapshot from local storage."
    )
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--recent-bars-limit", type=int, default=DEFAULT_RECENT_BARS_LIMIT)
    parser.add_argument("--recent-news-limit", type=int, default=DEFAULT_RECENT_NEWS_LIMIT)
    parser.add_argument(
        "--macro-series",
        action="append",
        default=None,
        help=(
            "A macro series ID to include; may be repeated. Defaults to this "
            "project's currently configured series if omitted."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, builder: MarketContextBuilder | None = None) -> int:
    """Build and print one snapshot. ``builder`` is an injection point for tests only."""
    args = _parse_args(argv)
    macro_series_ids = tuple(args.macro_series) if args.macro_series else DEFAULT_MACRO_SERIES_IDS

    try:
        context_builder = builder or MarketContextBuilder()
        snapshot = context_builder.build_snapshot(
            args.symbol,
            recent_bars_limit=args.recent_bars_limit,
            recent_news_limit=args.recent_news_limit,
            macro_series_ids=macro_series_ids,
        )
    except MarketContextValidationError as exc:
        print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
        return 2
    except MarketContextError as exc:
        print(json.dumps({"error": "storage_error", "detail": str(exc)}))
        return 1
    except Exception:
        # Never print the exception type, message, path, SQL, traceback, or any
        # other untrusted/unsanitized detail here -- only this fixed marker.
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    print(json.dumps(snapshot, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
