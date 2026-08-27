"""Loader for the small hand-authored synthetic evaluation fixtures.

Every fixture in this directory is **synthetic and hand-authored**. None
contains real article text, real URLs, real evidence IDs from live runs, real
observation values from live runs, credentials, provider response IDs, or
copied model output. They exist only to exercise the contracts, the rubric
validator, and the serialization boundary. See ``PROVENANCE.md`` in this
directory. They are not evidence of any agent's quality.
"""

from __future__ import annotations

from pathlib import Path

from market_intelligence.evaluation.contracts import EvaluationRunRecord
from market_intelligence.evaluation.serialization import read_record

FIXTURE_DIR = Path(__file__).parent

FIXTURE_NAMES: tuple[str, ...] = (
    "fully_supported",
    "partially_supported",
    "unsupported",
    "unable_to_determine",
    "incomplete_rubric",
    "duplicate_unexpected_adjudication",
)


def fixture_path(name: str) -> Path:
    if name not in FIXTURE_NAMES:
        raise KeyError(f"unknown fixture: {name!r}")
    return FIXTURE_DIR / f"{name}.json"


def load_fixture(name: str) -> EvaluationRunRecord:
    """Load one synthetic fixture as a validated ``EvaluationRunRecord``."""
    return read_record(fixture_path(name))


def load_all_fixtures() -> dict[str, EvaluationRunRecord]:
    return {name: load_fixture(name) for name in FIXTURE_NAMES}
