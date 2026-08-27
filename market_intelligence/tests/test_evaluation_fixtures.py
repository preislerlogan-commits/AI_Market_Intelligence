"""Synthetic evaluation fixtures validate, round-trip, and contain no real data."""

from __future__ import annotations

import re

import pytest

from market_intelligence.evaluation.contracts import CitationClassification
from market_intelligence.evaluation.fixtures import (
    FIXTURE_DIR,
    FIXTURE_NAMES,
    fixture_path,
    load_all_fixtures,
    load_fixture,
)
from market_intelligence.evaluation.rubric import check_rubric_completeness
from market_intelligence.evaluation.serialization import to_json_str

EXPECTED = {
    "fully_supported": True,
    "partially_supported": True,
    "unsupported": True,
    "unable_to_determine": True,
    "incomplete_rubric": False,
    "duplicate_unexpected_adjudication": False,
}


def test_the_six_required_fixtures_exist():
    assert set(FIXTURE_NAMES) == set(EXPECTED)
    for name in FIXTURE_NAMES:
        assert fixture_path(name).is_file()


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_every_fixture_loads_validates_and_round_trips(name):
    record = load_fixture(name)
    assert to_json_str(record) == fixture_path(name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_rubric_completeness_matches_its_purpose(name):
    result = check_rubric_completeness(load_fixture(name))
    assert result.complete is EXPECTED[name]


def test_completed_characterizations_preserve_negative_classifications():
    tally = check_rubric_completeness(load_fixture("unsupported")).classification_tally
    assert tally.get(CitationClassification.UNSUPPORTED) == 1
    tally = check_rubric_completeness(load_fixture("unable_to_determine")).classification_tally
    assert tally.get(CitationClassification.UNABLE_TO_DETERMINE) == 1


def test_incomplete_fixture_reports_the_missing_pair():
    result = check_rubric_completeness(load_fixture("incomplete_rubric"))
    assert result.missing_pairs == (("claim-beta", "cite-2"),)


def test_duplicate_fixture_reports_duplicate_unexpected_and_missing():
    result = check_rubric_completeness(load_fixture("duplicate_unexpected_adjudication"))
    assert result.duplicate_adjudication_pairs == (("claim-alpha", "cite-1"),)
    assert result.unexpected_pairs == (("claim-gamma", "cite-9"),)
    assert result.missing_pairs == (("claim-beta", "cite-2"),)


def test_provenance_note_exists_and_disclaims_agent_quality():
    text = (FIXTURE_DIR / "PROVENANCE.md").read_text(encoding="utf-8").lower()
    assert "synthetic" in text
    assert "not" in text and "evidence of any agent" in text


_FORBIDDEN = re.compile(
    r"https?://|resp_[a-z0-9]{6}|\bsk-[A-Za-z0-9]{8}|[A-Za-z]:\\|\.duckdb|alpaca|benzinga",
    re.IGNORECASE,
)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_fixture_files_contain_no_real_data_markers(name):
    raw = fixture_path(name).read_text(encoding="utf-8")
    assert not _FORBIDDEN.search(raw)


def test_load_all_fixtures_returns_every_fixture():
    assert set(load_all_fixtures()) == set(FIXTURE_NAMES)
