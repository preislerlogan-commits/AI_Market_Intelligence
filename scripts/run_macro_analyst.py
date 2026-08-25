"""Macro Analyst CLI.

Default mode is a **dry run**: it builds the local macro-evidence snapshot
(via ``MacroEvidenceBuilder``, read-only DuckDB access only) and evaluates
the deterministic preflight gate. It makes **zero OpenAI requests** in this
mode and prints only eligibility, the fixed reason categories, the requested
series IDs, series count, and metadata/freshness flags -- never the evidence
contents themselves.

Pass ``--execute`` to allow one real, billed OpenAI request -- and only if
the deterministic preflight passes for **every** requested series; an
ineligible run under ``--execute`` still makes zero model calls and returns
a truthful ``status="abstained"`` report. Execute output prints the
validated structured report plus sanitized model/token metadata (model name
and token counts only). It never prints the raw provider response, a
response ID, the full evidence payload, credentials, or a database path.

``--series`` is repeatable (e.g. ``--series FEDFUNDS --series UNRATE``) and
defaults to ``FEDFUNDS`` when omitted.

See ``docs/MACRO_ANALYST.md`` for the full contract.
"""

from __future__ import annotations

import argparse
import json

from market_intelligence.agents.macro_analyst import (
    DEFAULT_SERIES_IDS,
    MacroAnalyst,
    MacroAnalystAgentError,
    MacroAnalystIncompleteError,
    MacroAnalystRefusalError,
    MacroAnalystValidationError,
)
from market_intelligence.market_features.macro_evidence import (
    MacroEvidenceError,
    MacroEvidenceValidationError,
)
from market_intelligence.model_clients.openai_structured import OpenAIStructuredError

_VALIDATION_ERRORS = (MacroAnalystValidationError, MacroEvidenceValidationError)
_SANITIZED_AGENT_ERRORS = (MacroAnalystAgentError, MacroEvidenceError, OpenAIStructuredError)


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
        description="Run the Macro Analyst (dry-run by default; zero OpenAI requests)."
    )
    parser.add_argument(
        "--series",
        dest="series",
        action="append",
        default=None,
        help=(
            "FRED series ID to analyze. Repeatable (e.g. --series FEDFUNDS "
            "--series UNRATE). Defaults to FEDFUNDS if omitted."
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Make one real, billed OpenAI request if the deterministic "
            "preflight passes for every requested series. Omitted by "
            "default (dry run, zero OpenAI requests)."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, *, agent: MacroAnalyst | None = None) -> int:
    """Run the agent in dry-run or execute mode. ``agent`` is an injection point for tests."""
    args = _parse_args(argv)
    series_ids = args.series if args.series is not None else list(DEFAULT_SERIES_IDS)

    if not args.execute:
        try:
            agent_instance = agent or MacroAnalyst()
            preflight = agent_instance.build_preflight(series_ids)
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
                    "series_ids": list(preflight.series_ids),
                    "series_count": preflight.source_series_count,
                    "flags": preflight.flags,
                },
                indent=2,
            )
        )
        return 0

    try:
        agent_instance = agent or MacroAnalyst()
        result = agent_instance.run(series_ids)
    except _VALIDATION_ERRORS as exc:
        print(json.dumps({"error": "invalid_input", "detail": str(exc)}))
        return 2
    except MacroAnalystRefusalError:
        print(json.dumps({"error": "model_refusal"}))
        return 1
    except MacroAnalystIncompleteError as exc:
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
