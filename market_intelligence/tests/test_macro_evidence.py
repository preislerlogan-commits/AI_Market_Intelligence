"""Tests for market_intelligence.market_features.macro_evidence.

These tests never make a network request. Repository fixtures (macro
observations) are stored through the existing, already-reviewed
``MacroObservationRepository`` against an isolated temporary DuckDB
database (``Settings`` whose ``project_data_path`` is ``tmp_path``),
mirroring ``market_intelligence/tests/test_news_evidence.py``. The
snapshot builder itself is never pointed at the real repository database.
"""

from __future__ import annotations

import inspect
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import FredObservation, FredSeriesMetadata
from market_intelligence.market_features import macro_evidence
from market_intelligence.market_features.macro_evidence import (
    CHANGE_UNAVAILABLE_INCOMPARABLE_VINTAGE,
    CHANGE_UNAVAILABLE_INSUFFICIENT_HISTORY,
    CHANGE_UNAVAILABLE_LATEST_MISSING,
    CHANGE_UNAVAILABLE_PREVIOUS_MISSING,
    DEFAULT_RECENT_OBSERVATIONS_LIMIT,
    DEFAULT_SERIES_IDS,
    MAX_RECENT_OBSERVATIONS_LIMIT,
    MAX_SERIES_IDS,
    MIN_RECENT_OBSERVATIONS_LIMIT,
    STALE_AFTER_DAYS,
    STALE_AFTER_DAYS_QUARTERLY,
    MacroEvidenceBuilder,
    MacroEvidenceError,
    MacroEvidenceValidationError,
    normalize_recent_observations_limit,
    normalize_series_ids,
)
from market_intelligence.storage.database import DuckDBManager, default_database_path
from market_intelligence.storage.macro_observation_repository import MacroObservationRepository
from market_intelligence.storage.macro_series_metadata_repository import (
    MacroSeriesMetadataRepository,
)

CREDENTIAL_ENV_VARS = [
    "ALPACA_API_KEY",
    "ALPACA_API_SECRET",
    "FRED_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
]


@pytest.fixture(autouse=True)
def clear_credential_env(monkeypatch):
    for var in CREDENTIAL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def isolated_env_file(tmp_path) -> Path:
    return tmp_path / "does-not-exist.env"


def isolated_settings(tmp_path: Path, isolated_env_file: Path) -> Settings:
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def fixed_clock(value: datetime):
    def _clock() -> datetime:
        return value

    return _clock


DEFAULT_AS_OF = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def make_observation(
    *,
    series_id: str = "FEDFUNDS",
    observation_date: str = "2026-08-01",
    value: Decimal | None = Decimal("5.330000"),
    is_missing: bool = False,
    realtime_start: str = "1776-07-04",
    realtime_end: str = "9999-12-31",
    retrieved_at: str = "2026-08-21T00:00:00Z",
) -> FredObservation:
    return FredObservation(
        provider="fred",
        series_id=series_id,
        observation_date=observation_date,
        value=value,
        is_missing=is_missing,
        realtime_start=realtime_start,
        realtime_end=realtime_end,
        retrieved_at=retrieved_at,
    )


def initialized_builder(
    tmp_path: Path, isolated_env_file: Path, *, as_of: datetime = DEFAULT_AS_OF
):
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return settings, MacroEvidenceBuilder(settings=settings, clock=fixed_clock(as_of))


def make_metadata(
    *,
    series_id: str = "FEDFUNDS",
    title: str = "Federal Funds Effective Rate",
    frequency: str = "Monthly",
    frequency_short: str = "M",
    units: str = "Percent",
    seasonal_adjustment: str = "Not Seasonally Adjusted",
) -> FredSeriesMetadata:
    return FredSeriesMetadata(
        provider="fred",
        series_id=series_id,
        title=title,
        observation_start="1954-07-01",
        observation_end="2026-08-01",
        frequency=frequency,
        frequency_short=frequency_short,
        units=units,
        units_short="%",
        seasonal_adjustment=seasonal_adjustment,
        seasonal_adjustment_short="NSA",
        last_updated="2026-08-20T13:35:01Z",
        popularity=84,
        notes="Averages of daily figures.",
        retrieved_at_utc="2026-08-20T09:35:00Z",
    )


# --- Pure validation: normalize_series_ids -----------------------------------------


def test_normalize_series_ids_rejects_bare_string():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids("FEDFUNDS")


def test_normalize_series_ids_rejects_non_sequence_bool():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids(True)


def test_normalize_series_ids_rejects_empty_selection():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids([])


def test_normalize_series_ids_rejects_excessive_count():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids([f"SERIES{i}" for i in range(MAX_SERIES_IDS + 1)])


def test_normalize_series_ids_rejects_malformed_id():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids(["not a valid series id!"])


def test_normalize_series_ids_rejects_boolean_entry():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids(["FEDFUNDS", True])


def test_normalize_series_ids_rejects_duplicate_after_normalization():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_series_ids(["FEDFUNDS", "fedfunds"])


def test_normalize_series_ids_preserves_requested_order():
    assert normalize_series_ids(["UNRATE", "FEDFUNDS"]) == ("UNRATE", "FEDFUNDS")


def test_default_series_ids_is_fedfunds():
    assert DEFAULT_SERIES_IDS == ("FEDFUNDS",)


# --- build_snapshot input validation --------------------------------------------


def test_build_snapshot_validation_error_does_not_create_database_file(
    tmp_path, isolated_env_file
):
    settings = isolated_settings(tmp_path, isolated_env_file)
    builder = MacroEvidenceBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))
    with pytest.raises(MacroEvidenceValidationError):
        builder.build_snapshot([])
    assert not default_database_path(settings).exists()


# --- Missing / empty database ----------------------------------------------------


def test_build_snapshot_missing_database_reports_missing_and_stale(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    assert not default_database_path(settings).exists()
    builder = MacroEvidenceBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    assert snapshot["series"][0]["has_stored_observation"] is False
    assert snapshot["series"][0]["freshness"]["missing"] is True
    assert snapshot["series"][0]["freshness"]["stale"] is True
    assert snapshot["flags"]["missing_series"] == ["FEDFUNDS"]


def test_build_snapshot_initialized_empty_database_reports_missing(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    assert snapshot["series"][0]["has_stored_observation"] is False
    assert snapshot["flags"]["missing_series"] == ["FEDFUNDS"]


def test_build_snapshot_missing_macro_observations_table_reports_missing(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    connection = duckdb.connect(str(default_database_path(settings)))
    try:
        connection.execute("DROP TABLE macro_observations")
    finally:
        connection.close()

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    assert snapshot["series"][0]["has_stored_observation"] is False
    assert snapshot["series"][0]["freshness"]["missing"] is True


# --- Ordering / partial coverage -------------------------------------------------


def test_build_snapshot_series_returned_in_requested_order(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(series_id="FEDFUNDS")])
    repo.store_observations([make_observation(series_id="UNRATE")])

    snapshot = builder.build_snapshot(["UNRATE", "FEDFUNDS"])

    assert [entry["series_id"] for entry in snapshot["series"]] == ["UNRATE", "FEDFUNDS"]


def test_build_snapshot_partial_missing_series_among_multiple(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(series_id="FEDFUNDS")])

    snapshot = builder.build_snapshot(["FEDFUNDS", "UNRATE"])

    by_id = {entry["series_id"]: entry for entry in snapshot["series"]}
    assert by_id["FEDFUNDS"]["has_stored_observation"] is True
    assert by_id["UNRATE"]["has_stored_observation"] is False
    assert snapshot["flags"]["missing_series"] == ["UNRATE"]


# --- Vintage selection -------------------------------------------------------------


def test_build_snapshot_selects_latest_observation_date(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.100000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["latest_observation_date"] == "2026-07-01"
    assert entry["latest_value"] == "5.100000"


def test_build_snapshot_selects_deterministic_latest_vintage_for_same_date(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    # Two revisions of the same observation_date, distinguished only by
    # realtime_start -- the later revision's realtime window must win.
    repo.store_observations(
        [
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.000000"),
                realtime_start="2026-07-02",
                realtime_end="2026-07-31",
            ),
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.050000"),
                realtime_start="2026-08-01",
                realtime_end="9999-12-31",
            ),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["latest_value"] == "5.050000"
    assert entry["realtime_start"] == "2026-08-01"
    assert entry["realtime_end"] == "9999-12-31"


def test_build_snapshot_does_not_combine_vintages(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.000000"),
                realtime_start="2026-07-02",
                realtime_end="2026-07-31",
                retrieved_at="2026-07-02T00:00:00Z",
            ),
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.050000"),
                realtime_start="2026-08-01",
                realtime_end="9999-12-31",
                retrieved_at="2026-08-01T00:00:00Z",
            ),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    # Every field must come from exactly the chosen row -- retrieved_at must
    # match the winning vintage's own retrieval time, not the other row's.
    assert entry["retrieved_at_utc"] == "2026-08-01T00:00:00Z"


# --- Missing values ------------------------------------------------------------------


def test_build_snapshot_latest_is_missing_with_null_value(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(value=None, is_missing=True)])

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["latest_is_missing"] is True
    assert entry["latest_value"] is None


# --- Freshness / staleness -----------------------------------------------------------


def test_build_snapshot_monthly_fresh_at_exactly_90_days(tmp_path, isolated_env_file):
    """A monthly-frequency series (frequency_short="M") at exactly STALE_AFTER_DAYS
    (90) elapsed days is not stale -- the boundary itself is inclusive of freshness."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS)).isoformat()
    obs_repo.store_observations([make_observation(observation_date=observation_date)])
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(frequency_short="M"))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["stale"] is False
    assert entry["freshness"]["stale_after_days"] == STALE_AFTER_DAYS


def test_build_snapshot_monthly_stale_at_91_days(tmp_path, isolated_env_file):
    """A monthly-frequency series one day beyond STALE_AFTER_DAYS (91 elapsed
    days) is stale."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (
        DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS + 1)
    ).isoformat()
    obs_repo.store_observations([make_observation(observation_date=observation_date)])
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(frequency_short="M"))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["stale"] is True
    assert entry["freshness"]["stale_after_days"] == STALE_AFTER_DAYS


def test_build_snapshot_quarterly_fresh_at_exactly_180_days(tmp_path, isolated_env_file):
    """A quarterly-frequency series (frequency_short="Q") at exactly
    STALE_AFTER_DAYS_QUARTERLY (180) elapsed days is not stale."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (
        DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS_QUARTERLY)
    ).isoformat()
    obs_repo.store_observations(
        [make_observation(series_id="GDPC1", observation_date=observation_date)]
    )
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(
        make_metadata(series_id="GDPC1", frequency="Quarterly", frequency_short="Q")
    )

    snapshot = builder.build_snapshot(["GDPC1"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["stale"] is False
    assert entry["freshness"]["stale_after_days"] == STALE_AFTER_DAYS_QUARTERLY


def test_build_snapshot_quarterly_stale_at_181_days(tmp_path, isolated_env_file):
    """A quarterly-frequency series one day beyond STALE_AFTER_DAYS_QUARTERLY
    (181 elapsed days) is stale."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (
        DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS_QUARTERLY + 1)
    ).isoformat()
    obs_repo.store_observations(
        [make_observation(series_id="GDPC1", observation_date=observation_date)]
    )
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(
        make_metadata(series_id="GDPC1", frequency="Quarterly", frequency_short="Q")
    )

    snapshot = builder.build_snapshot(["GDPC1"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["stale"] is True
    assert entry["freshness"]["stale_after_days"] == STALE_AFTER_DAYS_QUARTERLY


def test_build_snapshot_gdpc1_shaped_quarterly_observation_not_stale(
    tmp_path, isolated_env_file
):
    """Reproduces the reported false-positive: a quarterly GDPC1 observation
    dated the first day of a calendar quarter, evaluated on August 25 of the
    following quarter, must not be flagged stale under the 180-day quarterly
    threshold (elapsed: April 1 -> August 25 is 146 days)."""
    as_of = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)
    settings, builder = initialized_builder(tmp_path, isolated_env_file, as_of=as_of)
    obs_repo = MacroObservationRepository(settings=settings)
    obs_repo.store_observations(
        [
            make_observation(
                series_id="GDPC1",
                observation_date="2026-04-01",
                realtime_start="2026-07-30",
                retrieved_at="2026-07-30T15:08:06Z",
            )
        ]
    )
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(
        make_metadata(series_id="GDPC1", frequency="Quarterly", frequency_short="Q")
    )

    snapshot = builder.build_snapshot(["GDPC1"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["missing"] is False
    assert entry["freshness"]["future_date_detected"] is False
    assert entry["freshness"]["stale"] is False
    assert entry["freshness"]["stale_after_days"] == STALE_AFTER_DAYS_QUARTERLY


def test_build_snapshot_missing_frequency_metadata_fails_closed(tmp_path, isolated_env_file):
    """No stored series metadata at all means frequency_short is unknown --
    the series must fail closed (stale=True) even though the observation
    itself is recent, and no threshold is silently assigned."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() - timedelta(days=1)).isoformat()
    obs_repo.store_observations([make_observation(observation_date=observation_date)])
    # Deliberately no metadata stored for FEDFUNDS.

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["metadata_available"] is False
    assert entry["freshness"]["stale"] is True
    assert entry["freshness"]["stale_after_days"] is None


def test_build_snapshot_unrecognized_frequency_metadata_fails_closed(
    tmp_path, isolated_env_file
):
    """Stored metadata with a frequency_short outside the narrow {"M", "Q"}
    set (e.g. weekly "W") must also fail closed rather than reuse the
    monthly or quarterly threshold, even though the observation itself is
    recent."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() - timedelta(days=1)).isoformat()
    obs_repo.store_observations([make_observation(observation_date=observation_date)])
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(
        make_metadata(frequency="Weekly, Ending Friday", frequency_short="W")
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["metadata_available"] is True
    assert entry["freshness"]["stale"] is True
    assert entry["freshness"]["stale_after_days"] is None


# --- Future observation dates ---------------------------------------------------------


def test_build_snapshot_future_date_within_tolerance_not_flagged(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() + timedelta(days=1)).isoformat()
    obs_repo.store_observations([make_observation(observation_date=observation_date)])
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(frequency_short="M"))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["future_date_detected"] is False
    assert entry["freshness"]["stale"] is False


def test_build_snapshot_future_date_beyond_tolerance_flags_and_preserves(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() + timedelta(days=2)).isoformat()
    repo.store_observations([make_observation(observation_date=observation_date)])

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["future_date_detected"] is True
    assert entry["freshness"]["stale"] is True
    # The implausible observation date is preserved exactly, never discarded.
    assert entry["latest_observation_date"] == observation_date
    assert snapshot["flags"]["future_dated_series"] == ["FEDFUNDS"]


# --- evidence_id ------------------------------------------------------------------------


def test_build_snapshot_evidence_id_stable_and_prefixed(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation()])

    first_snapshot = builder.build_snapshot(["FEDFUNDS"])
    second_snapshot = builder.build_snapshot(["FEDFUNDS"])

    first_id = first_snapshot["series"][0]["evidence_id"]
    second_id = second_snapshot["series"][0]["evidence_id"]
    assert first_id == second_id
    assert first_id.startswith("macro_")


def test_build_snapshot_evidence_id_differs_by_identity_not_value(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.000000")),
        ]
    )
    first_snapshot = builder.build_snapshot(["FEDFUNDS"])
    first_id = first_snapshot["series"][0]["evidence_id"]

    # A later ingestion with a distinct observation_date/value must produce a
    # distinct evidence_id.
    repo.store_observations(
        [
            make_observation(observation_date="2026-07-01", value=Decimal("5.100000")),
        ]
    )
    second_snapshot = builder.build_snapshot(["FEDFUNDS"])
    second_id = second_snapshot["series"][0]["evidence_id"]

    assert first_id != second_id
    expected_second_id = macro_evidence._evidence_id(
        "fred", "FEDFUNDS", date(2026, 7, 1), date(1776, 7, 4), date(9999, 12, 31)
    )
    assert second_id == expected_second_id


# --- Aggregate flags -----------------------------------------------------------------


def test_build_snapshot_flags_aggregate_missing_stale_future(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    stale_date = (DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS + 1)).isoformat()
    future_date = (DEFAULT_AS_OF.date() + timedelta(days=2)).isoformat()
    repo.store_observations([make_observation(series_id="FEDFUNDS", observation_date=stale_date)])
    repo.store_observations([make_observation(series_id="DFF", observation_date=future_date)])
    # "UNRATE" is requested but never stored.

    snapshot = builder.build_snapshot(["FEDFUNDS", "DFF", "UNRATE"])

    assert snapshot["flags"]["missing_series"] == ["UNRATE"]
    assert set(snapshot["flags"]["stale_series"]) == {"FEDFUNDS", "DFF", "UNRATE"}
    assert snapshot["flags"]["future_dated_series"] == ["DFF"]


# --- Coverage counts -------------------------------------------------------------------


def test_build_snapshot_coverage_counts_and_missing_observation_count(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-05-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-06-01", value=None, is_missing=True),
            make_observation(observation_date="2026-07-01", value=Decimal("5.100000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    coverage = snapshot["series"][0]["coverage"]
    assert coverage["row_count"] == 3
    assert coverage["earliest_observation_date"] == "2026-05-01"
    assert coverage["latest_observation_date"] == "2026-07-01"
    assert coverage["missing_observation_count"] == 1


# --- Series metadata --------------------------------------------------------------------


def test_build_snapshot_metadata_available_with_stored_metadata(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    obs_repo.store_observations([make_observation(series_id="FEDFUNDS")])
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(series_id="FEDFUNDS"))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["metadata_available"] is True
    assert entry["title"] == "Federal Funds Effective Rate"
    assert entry["frequency"] == "Monthly"
    assert entry["frequency_short"] == "M"
    assert entry["units"] == "Percent"
    assert entry["seasonal_adjustment"] == "Not Seasonally Adjusted"
    assert snapshot["flags"]["missing_metadata_series"] == []


def test_build_snapshot_metadata_unavailable_when_never_stored(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    obs_repo.store_observations([make_observation(series_id="FEDFUNDS")])

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["metadata_available"] is False
    assert entry["title"] is None
    assert entry["frequency"] is None
    assert entry["frequency_short"] is None
    assert entry["units"] is None
    assert entry["seasonal_adjustment"] is None
    assert snapshot["flags"]["missing_metadata_series"] == ["FEDFUNDS"]


def test_build_snapshot_metadata_unavailable_when_missing_observation_too(
    tmp_path, isolated_env_file
):
    """A series with neither a stored observation nor stored metadata is valid input."""
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["has_stored_observation"] is False
    assert entry["metadata_available"] is False
    assert snapshot["flags"]["missing_metadata_series"] == ["FEDFUNDS"]


def test_build_snapshot_metadata_available_even_without_stored_observation(
    tmp_path, isolated_env_file
):
    """Metadata and observations are read independently -- neither is inferred from the other."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(series_id="FEDFUNDS"))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["has_stored_observation"] is False
    assert entry["metadata_available"] is True
    assert entry["title"] == "Federal Funds Effective Rate"


def test_build_snapshot_missing_database_reports_metadata_unavailable(
    tmp_path, isolated_env_file
):
    settings = isolated_settings(tmp_path, isolated_env_file)
    assert not default_database_path(settings).exists()
    builder = MacroEvidenceBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["metadata_available"] is False
    assert snapshot["flags"]["missing_metadata_series"] == ["FEDFUNDS"]


def test_build_snapshot_missing_macro_series_metadata_table_reports_unavailable(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    obs_repo.store_observations([make_observation(series_id="FEDFUNDS")])
    connection = duckdb.connect(str(default_database_path(settings)))
    try:
        connection.execute("DROP TABLE macro_series_metadata")
    finally:
        connection.close()

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["has_stored_observation"] is True  # observations still readable
    assert entry["metadata_available"] is False


def test_build_snapshot_metadata_partial_across_multiple_series(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    obs_repo.store_observations([make_observation(series_id="FEDFUNDS")])
    obs_repo.store_observations([make_observation(series_id="UNRATE")])
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(series_id="FEDFUNDS"))
    # UNRATE metadata deliberately never stored.

    snapshot = builder.build_snapshot(["FEDFUNDS", "UNRATE"])

    by_id = {entry["series_id"]: entry for entry in snapshot["series"]}
    assert by_id["FEDFUNDS"]["metadata_available"] is True
    assert by_id["UNRATE"]["metadata_available"] is False
    assert snapshot["flags"]["missing_metadata_series"] == ["UNRATE"]


def test_build_snapshot_metadata_does_not_alter_observation_values(tmp_path, isolated_env_file):
    """Values remain unchanged decimal strings regardless of metadata presence."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    obs_repo = MacroObservationRepository(settings=settings)
    obs_repo.store_observations(
        [make_observation(series_id="FEDFUNDS", value=Decimal("5.330000"))]
    )
    meta_repo = MacroSeriesMetadataRepository(settings=settings)
    meta_repo.store_metadata(make_metadata(series_id="FEDFUNDS"))

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    assert snapshot["series"][0]["latest_value"] == "5.330000"


def test_build_snapshot_does_not_infer_metadata_from_series_id(tmp_path, isolated_env_file):
    """A well-known series ID with no stored metadata must never be silently populated."""
    _, builder = initialized_builder(tmp_path, isolated_env_file)

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["metadata_available"] is False
    assert entry["title"] is None
    assert entry["frequency"] is None
    assert entry["frequency_short"] is None
    assert entry["units"] is None
    assert entry["seasonal_adjustment"] is None


# --- Connection-close error handling ------------------------------------------------

_CLOSE_FAILURE_MARKER = "simulated close failure C:\\secret\\path SELECT * FROM macro_observations"
_READ_FAILURE_MARKER = "simulated read failure C:\\secret\\path SELECT * FROM macro_observations"


class _CloseFailingConnection:
    """Wraps a real DuckDB connection but always fails on close()."""

    def __init__(self, real_connection):
        self._real_connection = real_connection

    def __getattr__(self, name):
        return getattr(self._real_connection, name)

    def close(self):
        raise RuntimeError(_CLOSE_FAILURE_MARKER)


class _ReadThenCloseFailingConnection:
    """The first execute() (table-existence check) succeeds, every subsequent
    execute() raises duckdb.Error, and close() also always fails -- used to
    prove a close() failure never masks an already-sanitized read error."""

    def __init__(self, real_connection):
        self._real_connection = real_connection
        self._execute_count = 0

    def __getattr__(self, name):
        return getattr(self._real_connection, name)

    def execute(self, *args, **kwargs):
        self._execute_count += 1
        if self._execute_count == 1:
            return self._real_connection.execute(*args, **kwargs)
        raise duckdb.Error(_READ_FAILURE_MARKER)

    def close(self):
        raise RuntimeError(_CLOSE_FAILURE_MARKER)


def test_build_snapshot_close_failure_after_successful_read_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation()])

    real_connect = macro_evidence.duckdb.connect

    def fake_connect(*args, **kwargs):
        return _CloseFailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(macro_evidence.duckdb, "connect", fake_connect)

    with pytest.raises(MacroEvidenceError) as exc_info:
        builder.build_snapshot(["FEDFUNDS"])

    message = str(exc_info.value)
    assert message == "Failed to close local storage connection."
    assert _CLOSE_FAILURE_MARKER not in message
    assert "RuntimeError" not in message
    assert "secret" not in message


def test_build_snapshot_close_failure_does_not_mask_sanitized_read_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)

    real_connect = macro_evidence.duckdb.connect

    def fake_connect(*args, **kwargs):
        return _ReadThenCloseFailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(macro_evidence.duckdb, "connect", fake_connect)

    with pytest.raises(MacroEvidenceError) as exc_info:
        builder.build_snapshot(["FEDFUNDS"])

    message = str(exc_info.value)
    assert message == "Failed to read macro evidence data from local storage."
    assert _READ_FAILURE_MARKER not in message
    assert _CLOSE_FAILURE_MARKER not in message
    assert "RuntimeError" not in message
    assert "secret" not in message


def test_build_snapshot_open_failure_reports_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    open_failure_marker = "simulated open failure C:\\secret\\path"

    def raising_connect(*args, **kwargs):
        raise duckdb.Error(open_failure_marker)

    monkeypatch.setattr(macro_evidence.duckdb, "connect", raising_connect)

    with pytest.raises(MacroEvidenceError) as exc_info:
        builder.build_snapshot(["FEDFUNDS"])

    message = str(exc_info.value)
    assert message == "Failed to open local storage for reading."
    assert open_failure_marker not in message
    assert "secret" not in message


# --- recent_observations_limit validation (before any DuckDB access) -------------------


def test_normalize_recent_observations_limit_rejects_non_int():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_recent_observations_limit("6")


def test_normalize_recent_observations_limit_rejects_bool():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_recent_observations_limit(True)


def test_normalize_recent_observations_limit_rejects_below_min():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_recent_observations_limit(MIN_RECENT_OBSERVATIONS_LIMIT - 1)


def test_normalize_recent_observations_limit_rejects_above_max():
    with pytest.raises(MacroEvidenceValidationError):
        normalize_recent_observations_limit(MAX_RECENT_OBSERVATIONS_LIMIT + 1)


def test_normalize_recent_observations_limit_accepts_bounds():
    assert normalize_recent_observations_limit(MIN_RECENT_OBSERVATIONS_LIMIT) == (
        MIN_RECENT_OBSERVATIONS_LIMIT
    )
    assert normalize_recent_observations_limit(MAX_RECENT_OBSERVATIONS_LIMIT) == (
        MAX_RECENT_OBSERVATIONS_LIMIT
    )


def test_default_recent_observations_limit_is_six():
    assert DEFAULT_RECENT_OBSERVATIONS_LIMIT == 6


def test_build_snapshot_recent_limit_validation_error_does_not_create_database_file(
    tmp_path, isolated_env_file
):
    settings = isolated_settings(tmp_path, isolated_env_file)
    builder = MacroEvidenceBuilder(settings=settings, clock=fixed_clock(DEFAULT_AS_OF))
    with pytest.raises(MacroEvidenceValidationError):
        builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=1)
    assert not default_database_path(settings).exists()


def test_build_snapshot_echoes_recent_observations_limit_in_request(tmp_path, isolated_env_file):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=4)
    assert snapshot["request"]["recent_observations_limit"] == 4


# --- recent_observations: vintage selection, ordering, and bounding --------------------


def test_build_snapshot_recent_observations_ordered_newest_first(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-05-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-06-01", value=Decimal("5.100000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.200000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=6)

    dates = [item["observation_date"] for item in snapshot["series"][0]["recent_observations"]]
    assert dates == ["2026-07-01", "2026-06-01", "2026-05-01"]


def test_build_snapshot_recent_observations_respects_limit(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date=f"2026-0{i}-01", value=Decimal("5.000000"))
            for i in range(1, 6)
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=2)

    assert len(snapshot["series"][0]["recent_observations"]) == 2


def test_build_snapshot_recent_observations_one_vintage_per_date_deterministic(
    tmp_path, isolated_env_file
):
    """Two vintages of the same date must collapse to exactly one chosen row."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.000000"),
                realtime_start="2026-07-02",
                realtime_end="2026-07-31",
            ),
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.050000"),
                realtime_start="2026-08-01",
                realtime_end="9999-12-31",
            ),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=6)

    recent = snapshot["series"][0]["recent_observations"]
    assert len(recent) == 1
    assert recent[0]["value"] == "5.050000"
    assert recent[0]["realtime_start"] == "2026-08-01"


def test_build_snapshot_recent_observations_never_combines_fields_across_vintages(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(
                observation_date="2026-06-01",
                value=Decimal("4.000000"),
                realtime_start="1776-07-04",
                realtime_end="9999-12-31",
            ),
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.000000"),
                realtime_start="2026-07-02",
                realtime_end="2026-07-31",
            ),
            make_observation(
                observation_date="2026-07-01",
                value=Decimal("5.050000"),
                realtime_start="2026-08-01",
                realtime_end="9999-12-31",
            ),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=6)

    recent = snapshot["series"][0]["recent_observations"]
    latest_item = next(item for item in recent if item["observation_date"] == "2026-07-01")
    assert latest_item["value"] == "5.050000"
    assert latest_item["realtime_start"] == "2026-08-01"
    assert latest_item["realtime_end"] == "9999-12-31"


def test_build_snapshot_recent_observations_no_interpolation_or_forward_fill(
    tmp_path, isolated_env_file
):
    """A gap between stored dates must never be filled in with a synthetic entry."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-05-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.200000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=6)

    dates = [item["observation_date"] for item in snapshot["series"][0]["recent_observations"]]
    assert dates == ["2026-07-01", "2026-05-01"]
    assert "2026-06-01" not in dates


def test_build_snapshot_recent_observations_evidence_id_matches_top_level_latest(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(observation_date="2026-07-01")])

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=6)

    entry = snapshot["series"][0]
    assert entry["recent_observations"][0]["evidence_id"] == entry["evidence_id"]


def test_build_snapshot_recent_observations_empty_when_no_observations(
    tmp_path, isolated_env_file
):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    snapshot = builder.build_snapshot(["FEDFUNDS"])
    assert snapshot["series"][0]["recent_observations"] == []


def test_build_snapshot_coverage_separate_from_bounded_recent_excerpt(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date=f"2026-{i:02d}-01", value=Decimal("5.000000"))
            for i in range(1, 6)
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"], recent_observations_limit=2)

    entry = snapshot["series"][0]
    assert len(entry["recent_observations"]) == 2
    assert entry["coverage"]["row_count"] == 5


# --- latest_change_from_previous ---------------------------------------------------------


def test_build_snapshot_latest_change_available_increased(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.330000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["available"] is True
    assert change["unavailable_reason"] is None
    assert change["latest_observation_date"] == "2026-07-01"
    assert change["latest_value"] == "5.330000"
    assert change["previous_observation_date"] == "2026-06-01"
    assert change["previous_value"] == "5.000000"
    assert change["direction"] == "increased"
    assert change["absolute_change_native_units"] == "0.330000"
    assert change["latest_evidence_id"] == snapshot["series"][0]["evidence_id"]
    assert change["previous_evidence_id"] != change["latest_evidence_id"]


def test_build_snapshot_latest_change_available_decreased(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.330000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.000000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["direction"] == "decreased"
    assert change["absolute_change_native_units"] == "0.330000"


def test_build_snapshot_latest_change_available_unchanged(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.330000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.330000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["direction"] == "unchanged"
    assert change["absolute_change_native_units"] == "0.000000"


def test_build_snapshot_latest_change_exact_decimal_subtraction(tmp_path, isolated_env_file):
    """The change must be an exact Decimal subtraction, never a rounded/float result."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("0.100000")),
            make_observation(observation_date="2026-07-01", value=Decimal("0.300000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    # Decimal("0.300000") - Decimal("0.100000") is exactly Decimal("0.200000");
    # the equivalent float subtraction (0.3 - 0.1) is not exactly 0.2.
    assert change["absolute_change_native_units"] == "0.200000"


def test_build_snapshot_latest_change_never_computes_percentage_or_extra_fields(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-07-01", value=Decimal("5.330000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert set(change.keys()) == {
        "available",
        "unavailable_reason",
        "latest_observation_date",
        "latest_value",
        "latest_evidence_id",
        "previous_observation_date",
        "previous_value",
        "previous_evidence_id",
        "absolute_change_native_units",
        "direction",
    }


def test_build_snapshot_latest_change_unavailable_insufficient_history(
    tmp_path, isolated_env_file
):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations([make_observation(observation_date="2026-07-01")])

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["available"] is False
    assert change["unavailable_reason"] == CHANGE_UNAVAILABLE_INSUFFICIENT_HISTORY
    assert change["latest_value"] is None
    assert change["direction"] is None


def test_build_snapshot_latest_change_unavailable_no_stored_observation_at_all(
    tmp_path, isolated_env_file
):
    _, builder = initialized_builder(tmp_path, isolated_env_file)
    snapshot = builder.build_snapshot(["FEDFUNDS"])
    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["available"] is False
    assert change["unavailable_reason"] == CHANGE_UNAVAILABLE_INSUFFICIENT_HISTORY


def test_build_snapshot_latest_change_unavailable_latest_missing(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=Decimal("5.000000")),
            make_observation(observation_date="2026-07-01", value=None, is_missing=True),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["available"] is False
    assert change["unavailable_reason"] == CHANGE_UNAVAILABLE_LATEST_MISSING


def test_build_snapshot_latest_change_unavailable_previous_missing(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(observation_date="2026-06-01", value=None, is_missing=True),
            make_observation(observation_date="2026-07-01", value=Decimal("5.330000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["available"] is False
    assert change["unavailable_reason"] == CHANGE_UNAVAILABLE_PREVIOUS_MISSING


def test_build_snapshot_latest_change_unavailable_incomparable_vintage(
    tmp_path, isolated_env_file
):
    """A chosen vintage that is not FRED's currently valid (open-ended) revision --
    e.g. only a superseded, bounded-window vintage is on file for that date --
    must not be compared."""
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    repo.store_observations(
        [
            make_observation(
                observation_date="2026-06-01",
                value=Decimal("5.000000"),
                realtime_start="2026-06-02",
                realtime_end="2026-07-31",  # bounded -- not the open-ended sentinel
            ),
            make_observation(observation_date="2026-07-01", value=Decimal("5.330000")),
        ]
    )

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    change = snapshot["series"][0]["latest_change_from_previous"]
    assert change["available"] is False
    assert change["unavailable_reason"] == CHANGE_UNAVAILABLE_INCOMPARABLE_VINTAGE


# --- No network or model usage ---------------------------------------------------------


def test_module_source_has_no_network_or_model_imports():
    source = inspect.getsource(macro_evidence)
    forbidden_imports = (
        "import httpx",
        "import openai",
        "import anthropic",
        "from openai",
        "from anthropic",
    )
    for forbidden in forbidden_imports:
        assert forbidden not in source
