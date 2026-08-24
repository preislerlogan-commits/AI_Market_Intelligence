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
from market_intelligence.data_connectors.fred_macro_data import FredObservation
from market_intelligence.market_features import macro_evidence
from market_intelligence.market_features.macro_evidence import (
    DEFAULT_SERIES_IDS,
    MAX_SERIES_IDS,
    STALE_AFTER_DAYS,
    MacroEvidenceBuilder,
    MacroEvidenceError,
    MacroEvidenceValidationError,
    normalize_series_ids,
)
from market_intelligence.storage.database import DuckDBManager, default_database_path
from market_intelligence.storage.macro_observation_repository import MacroObservationRepository

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


def test_build_snapshot_fresh_within_threshold(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS)).isoformat()
    repo.store_observations([make_observation(observation_date=observation_date)])

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["stale"] is False
    assert entry["freshness"]["stale_after_days"] == STALE_AFTER_DAYS


def test_build_snapshot_stale_beyond_threshold(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    observation_date = (
        DEFAULT_AS_OF.date() - timedelta(days=STALE_AFTER_DAYS + 1)
    ).isoformat()
    repo.store_observations([make_observation(observation_date=observation_date)])

    snapshot = builder.build_snapshot(["FEDFUNDS"])

    entry = snapshot["series"][0]
    assert entry["freshness"]["stale"] is True


# --- Future observation dates ---------------------------------------------------------


def test_build_snapshot_future_date_within_tolerance_not_flagged(tmp_path, isolated_env_file):
    settings, builder = initialized_builder(tmp_path, isolated_env_file)
    repo = MacroObservationRepository(settings=settings)
    observation_date = (DEFAULT_AS_OF.date() + timedelta(days=1)).isoformat()
    repo.store_observations([make_observation(observation_date=observation_date)])

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
