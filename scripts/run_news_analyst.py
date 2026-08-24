"""News Analyst CLI.

Default mode is a **dry run**: it builds the local news-evidence snapshot
(via ``NewsEvidenceBuilder``, read-only DuckDB access only) and evaluates the
deterministic preflight gate. It makes **zero OpenAI requests** in this mode
and prints only eligibility, the fixed reason categories, symbol, article
count, freshness flags, headline-only count, and summary-available count --
never the evidence contents themselves.

Pass ``--execute`` to allow one real, billed OpenAI request -- and only if
the deterministic preflight passes; an ineligible run under ``--execute``
still makes zero model calls and returns a truthful ``status="abstained"``
report. Execute output prints the validated structured report plus
sanitized model/token metadata (model name and token counts only). It never
prints the raw provider response, a response ID, article URLs,
``audit_provenance``, the full evidence payload, credentials, or a database
path.

See ``docs/NEWS_ANALYST.md`` for the full contract.
"""

from __future__ import annotations

import argparse
import json

from market_intelligence.agents.news_analyst import (
    NewsAnalyst,
    NewsAnalystAgentError,
    NewsAnalystIncompleteError,
    NewsAnalystRefusalError,
    NewsAnalystValidationError,
)
from market_intelligence.market_features.news_evidence import (
    DEFAULT_LIMIT,
    NewsEvidenceError,
    NewsEvidenceValidationError,
)
from market_intelligence.model_clients.openai_structured import OpenAIStructuredError

_VALIDATION_ERRORS = (NewsAnalystValidationError, NewsEvidenceValidationError)
_SANITIZED_AGENT_ERRORS = (NewsAnalystAgentError, NewsEvidenceError, OpenAIStructuredError)


def _agent_error_payload(exc: Exception) -> dict[str, str]:
    """Build the sanitized JSON error payload for a ``_SANITIZED_AGENT_ERRORS`` exception.

    ``str(exc)`` is always one of this codebase's own fixed, sanitized
    messages (never raw provider output, evidence, a database path, or a
    credential -- see each error class's docstring). ``category``, when
    present, is one of the fixed ``CATEGORY_*``/``AGENT_CATEGORY_*``
    constants defined on the error classes -- included so an operator or a
    future caller can distinguish failure classes without parsing ``detail``
    text.
    """
    payload = {"error": "agent_error", "detail": str(exc)}
    category = getattr(exc, "category", None)
    if isinstance(category, str):
        payload["category"] = category
    return payload


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the News Analyst (dry-run by default; zero OpenAI requests)."
    )
    parser.add_argument("--symbol", default="SPY")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Make one real, billed OpenAI request if the deterministic "
            "preflight passes. Omitted by default (dry run, zero OpenAI "
            "requests)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, agent: NewsAnalyst | None = None) -> int:
    """Run the agent in dry-run or execute mode. ``agent`` is an injection point for tests."""
    args = _parse_args(argv)

    if not args.execute:
        try:
            agent_instance = agent or NewsAnalyst()
            preflight = agent_instance.build_preflight(args.symbol, limit=args.limit)
        except _VALIDATION_ERRORS as exc:
            print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
            return 2
        except _SANITIZED_AGENT_ERRORS as exc:
            print(json.dumps(_agent_error_payload(exc)))
            return 1
        except Exception:
            # Never print the exception type, message, path, SQL, traceback, or any
            # other untrusted/unsanitized detail here -- only this fixed marker.
            print(json.dumps({"error": "unexpected_error"}))
            return 1

        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "eligible": preflight.eligible,
                    "reasons": list(preflight.reasons),
                    "symbol": preflight.symbol,
                    "article_count": preflight.source_article_count,
                    "freshness": preflight.freshness,
                    "headline_only_count": preflight.headline_only_count,
                    "summary_available_count": preflight.summary_available_count,
                },
                indent=2,
            )
        )
        return 0

    try:
        agent_instance = agent or NewsAnalyst()
        result = agent_instance.run(args.symbol, limit=args.limit)
    except _VALIDATION_ERRORS as exc:
        print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
        return 2
    except NewsAnalystRefusalError:
        print(json.dumps({"error": "model_refusal"}))
        return 1
    except NewsAnalystIncompleteError as exc:
        print(json.dumps({"error": "model_incomplete", "detail": str(exc)}))
        return 1
    except _SANITIZED_AGENT_ERRORS as exc:
        print(json.dumps(_agent_error_payload(exc)))
        return 1
    except Exception:
        print(json.dumps({"error": "unexpected_error"}))
        return 1

    output: dict[str, object] = {"mode": "execute", "report": result.report.model_dump()}
    if result.model_metadata is not None:
        output["model_metadata"] = {
            "model": result.model_metadata.model,
            "input_tokens": result.model_metadata.input_tokens,
            "output_tokens": result.model_metadata.output_tokens,
            "total_tokens": result.model_metadata.total_tokens,
        }
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
