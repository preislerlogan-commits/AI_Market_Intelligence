"""One-shot, read-only macro-evidence snapshot CLI.

Builds exactly one sanitized, JSON-ready macro-evidence snapshot from data
already stored in the local DuckDB database (see
``market_intelligence/market_features/macro_evidence.py``) and prints it to
stdout. Makes no network request of any kind (no FRED, OpenAI, or Anthropic
call) and writes nothing to the database -- read-only end to end.

All arguments are strictly validated before any DuckDB connection is
opened: an unrecognized argument is rejected by argparse itself, and an
invalid ``--series`` selection (malformed ID, duplicate after
normalization, empty, or excessive count) is rejected by
``MacroEvidenceBuilder`` before any storage access. Only sanitized JSON is
ever printed -- never a raw exception, database path, SQL, or credential.
See ``docs/MACRO_EVIDENCE_SNAPSHOT.md`` for the full field contract and its
limitations.
"""

from __future__ import annotations

import argparse
import json

from market_intelligence.market_features.macro_evidence import (
    DEFAULT_SERIES_IDS,
    MacroEvidenceBuilder,
    MacroEvidenceError,
    MacroEvidenceValidationError,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one read-only macro-evidence snapshot from local storage."
    )
    parser.add_argument(
        "--series",
        action="append",
        default=None,
        dest="series_ids",
        help=(
            "A FRED series ID to include; may be repeated. Defaults to "
            "FEDFUNDS if omitted."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, builder: MacroEvidenceBuilder | None = None) -> int:
    """Build and print one snapshot. ``builder`` is an injection point for tests only."""
    args = _parse_args(argv)
    series_ids = tuple(args.series_ids) if args.series_ids else DEFAULT_SERIES_IDS

    try:
        evidence_builder = builder or MacroEvidenceBuilder()
        snapshot = evidence_builder.build_snapshot(series_ids)
    except MacroEvidenceValidationError as exc:
        print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
        return 2
    except MacroEvidenceError as exc:
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
