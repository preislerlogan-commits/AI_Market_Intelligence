"""One-shot, read-only news-evidence snapshot CLI.

Builds exactly one sanitized, JSON-ready news-evidence snapshot from data
already stored in the local DuckDB database (see
``market_intelligence/market_features/news_evidence.py``) and prints it to
stdout. Makes no network request of any kind and writes nothing to the
database -- read-only end to end. This is infrastructure for a future News
Analyst agent, not a model request itself.

All arguments are strictly validated before any DuckDB connection is
opened: an unrecognized argument is rejected by argparse itself, and an
invalid ``--symbol``/``--limit`` is rejected by ``NewsEvidenceBuilder``
before any storage access. Only sanitized JSON is ever printed -- never a
raw exception, database path, or SQL. See
``docs/NEWS_EVIDENCE_SNAPSHOT.md`` for the full field contract and its
limitations.
"""

from __future__ import annotations

import argparse
import json

from market_intelligence.market_features.news_evidence import (
    DEFAULT_LIMIT,
    NewsEvidenceBuilder,
    NewsEvidenceError,
    NewsEvidenceValidationError,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one read-only news-evidence snapshot from local storage."
    )
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, builder: NewsEvidenceBuilder | None = None) -> int:
    """Build and print one snapshot. ``builder`` is an injection point for tests only."""
    args = _parse_args(argv)

    try:
        evidence_builder = builder or NewsEvidenceBuilder()
        snapshot = evidence_builder.build_snapshot(args.symbol, limit=args.limit)
    except NewsEvidenceValidationError as exc:
        print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
        return 2
    except NewsEvidenceError as exc:
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
