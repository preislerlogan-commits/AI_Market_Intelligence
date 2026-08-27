"""Deterministic rubric-completeness validator for an evaluation-run record.

This checks one thing only: does an ``EvaluationRunRecord`` have **exactly one**
human adjudication for **every** expected (claim, citation) pair, with no
unexpected pair and no duplicate? It is pure, offline, and deterministic --
the same record always yields the same result, and the ordered pair lists are
sorted lexicographically so a diff of two results is stable.

It deliberately does **not**:

- score, grade, or rank anything;
- read or judge claim / evidence content;
- treat any ``CitationClassification`` (including ``unable_to_determine``) as a
  pass or a failure -- classifications are tallied for visibility only;
- imply that a *complete* rubric means the agent is validated or that the run
  universally passes. See ``COMPLETION_IS_NOT_VALIDATION`` and
  ``docs/AGENT_EVALUATION_HARNESS.md``.

A characterization whose adjudications are entirely ``unsupported`` /
``partially_supported`` / ``unable_to_determine``, and one carrying
``FAILURE`` findings, is still a valid *completed* characterization when every
expected pair has exactly one adjudication.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from market_intelligence.evaluation.contracts import (
    CitationClassification,
    EvaluationRunRecord,
)

COMPLETION_IS_NOT_VALIDATION = (
    "A complete rubric means every expected claim/citation pair has exactly one "
    "recorded human adjudication. It does not mean the agent is validated, that "
    "its claims are factually accurate, that its output is repeatable, or that "
    "the run passes in any universal sense."
)

_Pair = tuple[str, str]


@dataclass(frozen=True)
class RubricCompletenessResult:
    """Outcome of :func:`check_rubric_completeness`.

    ``complete`` is ``True`` only when there are no missing, unexpected, or
    duplicate pairs and the expected-pair list itself has no duplicates. It is
    intentionally named ``complete`` (not ``passed``) -- see
    ``COMPLETION_IS_NOT_VALIDATION``.
    """

    complete: bool
    ordered_expected_pairs: tuple[_Pair, ...]
    ordered_adjudicated_pairs: tuple[_Pair, ...]
    missing_pairs: tuple[_Pair, ...]
    unexpected_pairs: tuple[_Pair, ...]
    duplicate_adjudication_pairs: tuple[_Pair, ...]
    duplicate_expected_pairs: tuple[_Pair, ...]
    classification_tally: dict[CitationClassification, int] = field(default_factory=dict)

    def summary_line(self) -> str:
        state = "complete" if self.complete else "incomplete"
        return (
            f"rubric {state}: {len(self.ordered_expected_pairs)} expected, "
            f"{len(self.missing_pairs)} missing, {len(self.unexpected_pairs)} unexpected, "
            f"{len(self.duplicate_adjudication_pairs)} duplicated"
        )


def _sorted_unique(pairs: list[_Pair]) -> tuple[_Pair, ...]:
    return tuple(sorted(set(pairs)))


def check_rubric_completeness(record: EvaluationRunRecord) -> RubricCompletenessResult:
    """Return a deterministic completeness result for ``record``.

    Completeness depends only on the *set* of expected pairs and the
    *multiset* of adjudicated pairs -- never on the classifications, the
    reasons, or the findings.
    """
    expected_list: list[_Pair] = [p.as_tuple() for p in record.expected_pairs]
    adjudicated_list: list[_Pair] = [a.as_pair() for a in record.adjudications]

    expected_set = set(expected_list)
    adjudicated_counts = Counter(adjudicated_list)

    duplicate_expected = _sorted_unique(
        [pair for pair, count in Counter(expected_list).items() if count > 1]
    )
    duplicate_adjudication = _sorted_unique(
        [pair for pair, count in adjudicated_counts.items() if count > 1]
    )
    missing = _sorted_unique([pair for pair in expected_set if adjudicated_counts[pair] == 0])
    unexpected = _sorted_unique(
        [pair for pair in adjudicated_counts if pair not in expected_set]
    )

    tally: dict[CitationClassification, int] = {}
    for adjudication in record.adjudications:
        tally[adjudication.classification] = tally.get(adjudication.classification, 0) + 1

    complete = not (missing or unexpected or duplicate_adjudication or duplicate_expected)

    return RubricCompletenessResult(
        complete=complete,
        ordered_expected_pairs=tuple(sorted(expected_set)),
        ordered_adjudicated_pairs=tuple(sorted(adjudicated_counts)),
        missing_pairs=missing,
        unexpected_pairs=unexpected,
        duplicate_adjudication_pairs=duplicate_adjudication,
        duplicate_expected_pairs=duplicate_expected,
        classification_tally=tally,
    )
