"""First deterministic factual-transcription check for Macro Analyst claims.

This is the *first, deliberately narrow* slice of the "deterministic
factual-transcription checks" named in ``docs/AGENT_EVALUATION_HARNESS.md``
(criterion P0-7). It is **Macro-Analyst-only** and **offline / synthetic only**.

What it does
------------

Given one evaluation-specific input -- a sanitized local claim ID, the claim
summary string, the claim's declared series ID, and one or two cited *synthetic*
evidence facts (series ID, observation date, ``Decimal`` value, reporting
frequency, and -- optionally -- units) -- it:

1. Recognizes **only** a narrow canonical spelling of the two controlled Macro
   Analyst statement forms (see ``market_intelligence/agents/macro_analyst.py``):

   - one stored single-observation statement --
     ``The stored <frequency> observation dated <YYYY-MM-DD> (is|was)
     <value>[ <units>].``
   - an ``increased`` / ``decreased`` / ``unchanged`` comparison between the
     latest and immediately preceding stored observation, in the Macro
     Analyst's canonical wording (the latest observation first, then the
     immediately preceding one, with a single direction verb) --
     ``The stored <frequency> observation dated <latest-date> was
     <latest-value>; it (increased|decreased) from the stored <frequency>
     observation dated <previous-date>, which was <previous-value>.`` (and the
     ``it was unchanged from ...`` variant).

   ``<frequency>`` is one of the seven human-readable words the Macro Analyst's
   ``FREQUENCY_SHORT_WORDS`` maps its recognized ``frequency_short`` codes to,
   in **any letter case** -- its accepted live output capitalizes the word
   (``The stored Monthly observation ...``, ``The stored Quarterly
   observation ...``), which its own ``_validate_frequency_wording`` permits
   because that validator only inspects a ``.lower()`` copy of the claim text.
   The captured word is deterministically case-folded back to its canonical
   spelling before comparison (see :func:`_normalize_frequency`) -- fixed-table
   case normalization, not fuzzy parsing.

   This exact full-sentence grammar is *sufficient* to satisfy the agent's
   instructions and post-response validators
   (``_validate_frequency_wording`` / ``_validate_comparison_claims``), but
   those validators do **not** *require* this precise wording -- they check for
   necessary substrings and forbidden phrasing, not this whole grammar. Any
   other wording a Macro Analyst could legitimately emit and have accepted --
   including the ``Comparing the stored observations dated X and Y, ...``
   phrasing -- is reported as *unrecognized* and routed to human
   citation-support review -- it is **never** treated as a pass, and it is not
   a failure of the agent. There is no generic number scraping and no fuzzy /
   semantic matching: every value/date token is read from a named capture group
   of a fully anchored grammar, so digits appearing anywhere else in the
   sentence are ignored by construction.

2. Deterministically verifies, token by token, against the cited synthetic
   evidence: the series ID (the claim's declared series vs. every cited fact),
   the observation date, the ``Decimal`` value (by numeric, not string,
   equality), the frequency wording (the captured word case-folded to its
   canonical spelling first), the units *when the claim states them*, the
   previous observation's date and value for a comparison, and that a
   comparison's stated direction agrees with the two cited values.

3. Emits exactly one :class:`~market_intelligence.evaluation.contracts.EvaluationFinding`
   per claim:

   - **exact match** -> an ``info`` ``factual_transcription`` finding;
   - **mismatch** -> a ``failure`` finding naming only the broad mismatch
     *categories* (fixed code slugs), never reproducing any claim or evidence
     text;
   - **unrecognized wording** -> a ``warning`` finding requiring human review.

What it is NOT
--------------

- Not a validation, certification, or "pass" of the Macro Analyst or of any
  individual claim. A match confirms only that the recognized tokens transcribe
  the cited synthetic fact -- see :data:`MATCH_IS_NOT_VALIDATION`.
- Not connected to any real agent output. It imports no connector, no database,
  no OpenAI client, and no agent runtime, and makes no network request (see the
  offline-guarantee test in
  ``market_intelligence/tests/test_evaluation_offline.py``). Every fixture it is
  exercised with is synthetic and hand-authored.
- Not the citation-support rubric, the lexical-overlap triage, the abstention
  matrix, cross-agent consistency, repeatability, or the first recorded
  characterization -- all of those remain unimplemented, and Phase 0 remains
  open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from market_intelligence.evaluation.contracts import (
    EvaluationFinding,
    FindingCategory,
    FindingSeverity,
    _reject_leaky_text,
)

# --- Recognized reporting-frequency vocabulary --------------------------------
#
# The same seven human-readable frequency words the Macro Analyst's
# ``FREQUENCY_SHORT_WORDS`` maps its recognized ``frequency_short`` codes to
# (D/W/BW/M/Q/SA/A). Re-declared here as a fixed literal rather than imported,
# because this module must not import the agent package.
MacroFrequencyWord = Literal[
    "daily",
    "weekly",
    "biweekly",
    "monthly",
    "quarterly",
    "semiannual",
    "annual",
]

_FREQUENCY_WORDS: frozenset[str] = frozenset(
    ("daily", "weekly", "biweekly", "monthly", "quarterly", "semiannual", "annual")
)


def _normalize_frequency(token: str) -> str:
    """Case-fold a captured frequency word to its canonical spelling.

    The recognized grammar accepts the frequency word in any letter case: the
    Macro Analyst's ``_validate_frequency_wording`` only inspects a ``.lower()``
    copy of the claim text, so the agent is free to -- and in its accepted live
    output does -- capitalize the word (``The stored Monthly observation ...``).
    Comparison against the lowercase :data:`MacroFrequencyWord` on the
    evaluation input is always done on this normalized form. Deterministic: a
    plain case fold, never fuzzy matching.
    """
    return token.casefold()

# --- Broad, code-authored mismatch categories --------------------------------
#
# A mismatch finding names only these fixed slugs -- never the claim text, the
# evidence value, or any other content.
MISMATCH_SERIES_ID = "series_id"
MISMATCH_OBSERVATION_DATE = "observation_date"
MISMATCH_VALUE = "value"
MISMATCH_FREQUENCY = "frequency"
MISMATCH_UNITS = "units"
MISMATCH_PREVIOUS_OBSERVATION_DATE = "previous_observation_date"
MISMATCH_PREVIOUS_VALUE = "previous_value"
MISMATCH_COMPARISON_DIRECTION = "comparison_direction"
MISMATCH_CITATION_STRUCTURE = "citation_structure"

MISMATCH_CATEGORIES: frozenset[str] = frozenset(
    (
        MISMATCH_SERIES_ID,
        MISMATCH_OBSERVATION_DATE,
        MISMATCH_VALUE,
        MISMATCH_FREQUENCY,
        MISMATCH_UNITS,
        MISMATCH_PREVIOUS_OBSERVATION_DATE,
        MISMATCH_PREVIOUS_VALUE,
        MISMATCH_COMPARISON_DIRECTION,
        MISMATCH_CITATION_STRUCTURE,
    )
)

StatementForm = Literal["single_observation", "comparison", "unrecognized"]
TranscriptionOutcome = Literal["match", "mismatch", "human_review"]

MATCH_IS_NOT_VALIDATION = (
    "A match means only that the recognized value/date/unit/frequency/direction "
    "tokens in this one claim transcribe the cited synthetic evidence fact. It "
    "does not mean the claim is factually accurate beyond those tokens, that the "
    "cited evidence supports the claim (that is the human citation-support "
    "rubric's job), that the output is repeatable, or that the Macro Analyst "
    "passes in any universal sense."
)

# --- Fixed, fully anchored recognized grammar --------------------------------
#
# Both forms are anchored end to end (``^ ... \.$``) with fixed literal
# connective text between named slots, so the ONLY digit-bearing text a match
# can contain sits in a ``date`` / ``value`` capture group. There is no
# free-text region, so "extra" numbers cannot be silently scraped: any sentence
# carrying a number outside these slots simply fails to match and is reported as
# unrecognized wording (human review), never as a pass.
# Case-insensitive on the frequency word only: the Macro Analyst enforces just a
# ``.lower()`` copy of the claim text, and its accepted live output capitalizes
# the word (``The stored Monthly observation ...``). The captured token is
# deterministically case-folded to its canonical spelling before any comparison
# (see ``_normalize_frequency``); this is fixed-table case normalization, not
# fuzzy parsing. Every other token in the grammar stays exact.
_FREQ_ALT = r"(?i:daily|weekly|biweekly|monthly|quarterly|semiannual|annual)"
_DATE = r"\d{4}-\d{2}-\d{2}"
_NUM = r"-?\d+(?:\.\d+)?"

# Closed units vocabulary. A claim stating units outside this set fails to match
# the grammar (fail closed) rather than being fuzzily interpreted.
# Longer phrases first so alternation prefers the most specific match.
_UNITS_ALT = (
    r"percentage points|percent|index points|index|"
    r"billions of dollars|thousands of dollars|thousands of persons|dollars"
)

_TRAILING_NOTE = r"(?:, (?:per official FRED metadata|as stored))?"

# Single stored observation. The value clause is only the copula ``is`` / ``was``
# -- no paraphrase synonyms.
_SINGLE_OBSERVATION_RE = re.compile(
    r"^The stored (?P<freq>" + _FREQ_ALT + r") observation dated (?P<date>" + _DATE + r") "
    r"(?:is|was) (?P<value>" + _NUM + r")"
    r"(?: (?P<units>" + _UNITS_ALT + r"))?" + _TRAILING_NOTE + r"\.$"
)

# Two-observation comparison, in the Macro Analyst's canonical wording: the
# latest stored observation stated first, then the immediately preceding one,
# with a single ``increased`` / ``decreased`` / ``unchanged`` direction verb --
# exactly what its ``_validate_comparison_claims`` / ``_validate_frequency_wording``
# permit (both clauses carry the required "the stored <frequency> observation
# dated <date>" phrasing, both exact dates and both exact values appear, and a
# single unambiguous direction is stated). There is no "Comparing the stored
# observations dated X and Y, ..." form -- that phrasing is not part of this
# canonical wording.
_COMPARISON_INCREASE_DECREASE_RE = re.compile(
    r"^The stored (?P<latest_freq>" + _FREQ_ALT + r") observation dated "
    r"(?P<latest_date>" + _DATE + r") was (?P<latest_value>" + _NUM + r"); "
    r"it (?P<direction>increased|decreased) from the stored "
    r"(?P<previous_freq>" + _FREQ_ALT + r") observation dated "
    r"(?P<previous_date>" + _DATE + r"), which was (?P<previous_value>" + _NUM + r")"
    + _TRAILING_NOTE + r"\.$"
)

_COMPARISON_UNCHANGED_RE = re.compile(
    r"^The stored (?P<latest_freq>" + _FREQ_ALT + r") observation dated "
    r"(?P<latest_date>" + _DATE + r") was (?P<latest_value>" + _NUM + r"); "
    r"it was unchanged from the stored "
    r"(?P<previous_freq>" + _FREQ_ALT + r") observation dated "
    r"(?P<previous_date>" + _DATE + r"), which was (?P<previous_value>" + _NUM + r")"
    + _TRAILING_NOTE + r"\.$"
)


# --- Evaluation input shapes -----------------------------------------------


def _iso_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("observation_date must be an ISO calendar date (YYYY-MM-DD)") from None
    return parsed.isoformat()


_Identifier = Annotated[
    str, Field(min_length=1, max_length=64), AfterValidator(_reject_leaky_text)
]
_SeriesId = Annotated[
    str, Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_]+$")
]
_ClaimSummary = Annotated[
    str, Field(min_length=1, max_length=400), AfterValidator(_reject_leaky_text)
]
_UnitsText = Annotated[
    str, Field(min_length=1, max_length=40), AfterValidator(_reject_leaky_text)
]


class TranscriptionEvidenceFact(BaseModel):
    """One cited *synthetic* stored-observation fact the claim is checked against.

    Every field is exactly what a redacted / synthetic macro-evidence fact would
    carry -- never real live-run data. ``value`` is a ``Decimal`` (pass it as a
    string in fixtures to avoid binary-float artifacts).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: _Identifier
    series_id: _SeriesId
    observation_date: Annotated[str, AfterValidator(_iso_date)]
    value: Decimal
    frequency: MacroFrequencyWord
    units: _UnitsText | None = None


class MacroTranscriptionInput(BaseModel):
    """One Macro Analyst claim plus its cited synthetic evidence, for checking.

    ``cited_facts`` holds exactly one fact for a single-observation claim, or
    exactly two (the previous and latest observation, in any order -- they are
    ordered deterministically by ``observation_date``) for a comparison claim.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: _Identifier
    claim_series_id: _SeriesId
    claim_summary: _ClaimSummary
    cited_facts: Annotated[
        list[TranscriptionEvidenceFact], Field(min_length=1, max_length=2)
    ]


# --- Result shape ---------------------------------------------------------


@dataclass(frozen=True)
class MacroTranscriptionResult:
    """Outcome of :func:`evaluate_macro_transcription` for one claim.

    Deliberately carries no ``passed`` / ``validated`` attribute -- see
    :data:`MATCH_IS_NOT_VALIDATION`. ``mismatch_categories`` is always sorted
    (empty unless ``outcome == "mismatch"``). ``finding`` is the single
    :class:`EvaluationFinding` describing this result.
    """

    claim_id: str
    outcome: TranscriptionOutcome
    statement_form: StatementForm
    mismatch_categories: tuple[str, ...]
    finding: EvaluationFinding


# --- Value / token comparison helpers -----------------------------------------


def _decimal_or_none(token: str) -> Decimal | None:
    try:
        return Decimal(token)
    except InvalidOperation:
        return None


def _normalize_units(text: str) -> str:
    return " ".join(text.casefold().split())


def _direction_from_values(previous: Decimal, latest: Decimal) -> str:
    if latest > previous:
        return "increased"
    if latest < previous:
        return "decreased"
    return "unchanged"


# --- Finding construction (no claim/evidence text ever) ----------------------

_MATCH_SUMMARY = (
    "Deterministic factual-transcription check: claim tokens match the cited "
    "synthetic evidence fact(s)."
)
_HUMAN_REVIEW_SUMMARY = (
    "Claim wording is not a recognized deterministic Macro Analyst statement "
    "form; deterministic transcription not applicable, human review required."
)


def _match_finding(item: MacroTranscriptionInput) -> EvaluationFinding:
    return EvaluationFinding(
        category=FindingCategory.FACTUAL_TRANSCRIPTION,
        severity=FindingSeverity.INFO,
        summary=_MATCH_SUMMARY,
        claim_id=item.claim_id,
        citation_id=(
            item.cited_facts[0].citation_id if len(item.cited_facts) == 1 else None
        ),
    )


def _mismatch_finding(
    item: MacroTranscriptionInput, categories: tuple[str, ...]
) -> EvaluationFinding:
    return EvaluationFinding(
        category=FindingCategory.FACTUAL_TRANSCRIPTION,
        severity=FindingSeverity.FAILURE,
        summary="Deterministic factual-transcription mismatch; categories: "
        + ", ".join(categories),
        claim_id=item.claim_id,
        citation_id=(
            item.cited_facts[0].citation_id if len(item.cited_facts) == 1 else None
        ),
    )


def _human_review_finding(item: MacroTranscriptionInput) -> EvaluationFinding:
    return EvaluationFinding(
        category=FindingCategory.FACTUAL_TRANSCRIPTION,
        severity=FindingSeverity.WARNING,
        summary=_HUMAN_REVIEW_SUMMARY,
        claim_id=item.claim_id,
    )


def _result(
    item: MacroTranscriptionInput,
    outcome: TranscriptionOutcome,
    form: StatementForm,
    categories: set[str],
) -> MacroTranscriptionResult:
    if outcome == "match":
        finding = _match_finding(item)
    elif outcome == "mismatch":
        finding = _mismatch_finding(item, tuple(sorted(categories)))
    else:
        finding = _human_review_finding(item)
    return MacroTranscriptionResult(
        claim_id=item.claim_id,
        outcome=outcome,
        statement_form=form,
        mismatch_categories=tuple(sorted(categories)) if outcome == "mismatch" else (),
        finding=finding,
    )


# --- Per-form checks --------------------------------------------------------


def _check_single_observation(
    item: MacroTranscriptionInput, match: re.Match[str]
) -> MacroTranscriptionResult:
    if len(item.cited_facts) != 1:
        return _result(item, "mismatch", "single_observation", {MISMATCH_CITATION_STRUCTURE})

    fact = item.cited_facts[0]
    categories: set[str] = set()

    if item.claim_series_id != fact.series_id:
        categories.add(MISMATCH_SERIES_ID)
    if match.group("date") != fact.observation_date:
        categories.add(MISMATCH_OBSERVATION_DATE)

    stated_value = _decimal_or_none(match.group("value"))
    if stated_value is None or stated_value != fact.value:
        categories.add(MISMATCH_VALUE)

    if _normalize_frequency(match.group("freq")) != fact.frequency:
        categories.add(MISMATCH_FREQUENCY)

    stated_units = match.group("units")
    if stated_units is not None:
        if fact.units is None or _normalize_units(stated_units) != _normalize_units(fact.units):
            categories.add(MISMATCH_UNITS)

    outcome: TranscriptionOutcome = "match" if not categories else "mismatch"
    return _result(item, outcome, "single_observation", categories)


def _check_comparison(
    item: MacroTranscriptionInput,
    *,
    latest_frequency_word: str,
    previous_frequency_word: str,
    written_latest_date: str,
    written_previous_date: str,
    written_latest_value: str,
    written_previous_value: str,
    stated_direction: str,
) -> MacroTranscriptionResult:
    if len(item.cited_facts) != 2:
        return _result(item, "mismatch", "comparison", {MISMATCH_CITATION_STRUCTURE})

    ordered = sorted(item.cited_facts, key=lambda f: f.observation_date)
    previous_fact, latest_fact = ordered[0], ordered[1]
    if previous_fact.observation_date == latest_fact.observation_date:
        # The two cited observations cannot be ordered into previous/latest.
        return _result(item, "mismatch", "comparison", {MISMATCH_CITATION_STRUCTURE})

    categories: set[str] = set()

    if (
        item.claim_series_id != previous_fact.series_id
        or item.claim_series_id != latest_fact.series_id
    ):
        categories.add(MISMATCH_SERIES_ID)
    if (
        _normalize_frequency(latest_frequency_word) != latest_fact.frequency
        or _normalize_frequency(previous_frequency_word) != previous_fact.frequency
    ):
        categories.add(MISMATCH_FREQUENCY)

    if written_previous_date != previous_fact.observation_date:
        categories.add(MISMATCH_PREVIOUS_OBSERVATION_DATE)
    if written_latest_date != latest_fact.observation_date:
        categories.add(MISMATCH_OBSERVATION_DATE)

    stated_previous_value = _decimal_or_none(written_previous_value)
    stated_latest_value = _decimal_or_none(written_latest_value)
    if stated_previous_value is None or stated_previous_value != previous_fact.value:
        categories.add(MISMATCH_PREVIOUS_VALUE)
    if stated_latest_value is None or stated_latest_value != latest_fact.value:
        categories.add(MISMATCH_VALUE)

    evidence_direction = _direction_from_values(previous_fact.value, latest_fact.value)
    if stated_direction != evidence_direction:
        categories.add(MISMATCH_COMPARISON_DIRECTION)

    outcome: TranscriptionOutcome = "match" if not categories else "mismatch"
    return _result(item, outcome, "comparison", categories)


# --- Public entry points --------------------------------------------------


def evaluate_macro_transcription(item: MacroTranscriptionInput) -> MacroTranscriptionResult:
    """Run the deterministic factual-transcription check for one Macro Analyst claim.

    Pure and deterministic: the same input always yields an equal result. Never
    raises for unrecognized wording -- that is reported as an ``human_review``
    outcome with a ``warning`` finding, never a pass.
    """
    summary = item.claim_summary

    comparison = _COMPARISON_INCREASE_DECREASE_RE.match(summary)
    if comparison is not None:
        return _check_comparison(
            item,
            latest_frequency_word=comparison.group("latest_freq"),
            previous_frequency_word=comparison.group("previous_freq"),
            written_latest_date=comparison.group("latest_date"),
            written_previous_date=comparison.group("previous_date"),
            written_latest_value=comparison.group("latest_value"),
            written_previous_value=comparison.group("previous_value"),
            stated_direction=comparison.group("direction"),
        )

    unchanged = _COMPARISON_UNCHANGED_RE.match(summary)
    if unchanged is not None:
        return _check_comparison(
            item,
            latest_frequency_word=unchanged.group("latest_freq"),
            previous_frequency_word=unchanged.group("previous_freq"),
            written_latest_date=unchanged.group("latest_date"),
            written_previous_date=unchanged.group("previous_date"),
            written_latest_value=unchanged.group("latest_value"),
            written_previous_value=unchanged.group("previous_value"),
            stated_direction="unchanged",
        )

    single = _SINGLE_OBSERVATION_RE.match(summary)
    if single is not None:
        return _check_single_observation(item, single)

    return _result(item, "human_review", "unrecognized", set())


def evaluate_macro_transcription_batch(
    items: list[MacroTranscriptionInput] | tuple[MacroTranscriptionInput, ...],
) -> tuple[MacroTranscriptionResult, ...]:
    """Evaluate a sequence of claims, preserving input order in the result tuple."""
    return tuple(evaluate_macro_transcription(item) for item in items)


def collect_findings(
    results: list[MacroTranscriptionResult] | tuple[MacroTranscriptionResult, ...],
) -> tuple[EvaluationFinding, ...]:
    """Return every result's finding, in the order the results were given."""
    return tuple(result.finding for result in results)
