"""One-shot, read-only session-quality CLI.

Builds exactly one sanitized, JSON-ready session-quality report for stored
SPY-style ``5Min`` bars (see
``market_intelligence/market_features/session_quality.py``) and prints it to
stdout. Makes no network request of any kind and writes nothing to the
database -- read-only end to end.

All arguments are strictly validated before any DuckDB connection is
opened: an unrecognized argument is rejected by argparse itself, and an
invalid ``--symbol``/``--session-date`` is rejected by
``SessionQualityBuilder`` before any storage access. Only sanitized JSON is
ever printed -- never a raw exception, database path, or SQL. See
``docs/SESSION_QUALITY.md`` for the full field contract and known
limitations.
"""

from __future__ import annotations

import argparse
import json

from market_intelligence.market_features.session_quality import (
    SessionQualityBuilder,
    SessionQualityError,
    SessionQualityValidationError,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one read-only session-quality report from local storage."
    )
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument(
        "--session-date",
        default=None,
        help=(
            "Explicit session date as YYYY-MM-DD (America/New_York calendar "
            "date). Omit to auto-select the most recent stored date with at "
            "least one regular-session bar."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, builder: SessionQualityBuilder | None = None) -> int:
    """Build and print one report. ``builder`` is an injection point for tests only."""
    args = _parse_args(argv)

    try:
        report_builder = builder or SessionQualityBuilder()
        report = report_builder.build_report(args.symbol, session_date=args.session_date)
    except SessionQualityValidationError as exc:
        print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
        return 2
    except SessionQualityError as exc:
        print(json.dumps({"error": "storage_error", "detail": str(exc)}))
        return 1
    except Exception:
        # Never print the exception type, message, path, SQL, traceback, or any
        # other untrusted/unsanitized detail here -- only this fixed marker.
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
