"""Tests for market_intelligence.storage.macro_series_metadata_repository.

These tests never touch the real repository database or any network/API.
Every repository under test is pointed at an isolated temporary directory
via a Settings whose project_data_path is tmp_path, mirroring
market_intelligence/tests/test_macro_observation_repository.py. Metadata
inputs are constructed directly here -- never fetched live.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import FredSeriesMetadata
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_series_metadata_repository import (
    MacroSeriesMetadataRepository,
    MacroSeriesMetadataStorageError,
    MacroSeriesMetadataStorageValidationError,
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


def initialized_repository(
    tmp_path: Path, isolated_env_file: Path
) -> MacroSeriesMetadataRepository:
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return MacroSeriesMetadataRepository(settings=settings)


def make_metadata(
    *,
    provider: str = "fred",
    series_id: str = "FEDFUNDS",
    title: str = "Federal Funds Effective Rate",
    observation_start: str = "1954-07-01",
    observation_end: str = "2026-08-01",
    frequency: str = "Monthly",
    frequency_short: str = "M",
    units: str = "Percent",
    units_short: str = "%",
    seasonal_adjustment: str = "Not Seasonally Adjusted",
    seasonal_adjustment_short: str = "NSA",
    last_updated: str = "2026-08-20T13:35:01Z",
    popularity: int = 84,
    notes: str | None = "Averages of daily figures.",
    retrieved_at_utc: str = "2026-08-20T09:35:00Z",
) -> FredSeriesMetadata:
    return FredSeriesMetadata(
        provider=provider,
        series_id=series_id,
        title=title,
        observation_start=observation_start,
        observation_end=observation_end,
        frequency=frequency,
        frequency_short=frequency_short,
        units=units,
        units_short=units_short,
        seasonal_adjustment=seasonal_adjustment,
        seasonal_adjustment_short=seasonal_adjustment_short,
        last_updated=last_updated,
        popularity=popularity,
        notes=notes,
        retrieved_at_utc=retrieved_at_utc,
    )


def fetch_metadata(connection: duckdb.DuckDBPyConnection, series_id: str):
    return connection.execute(
        "SELECT provider, series_id, title, observation_start, observation_end, frequency, "
        "frequency_short, units, units_short, seasonal_adjustment, "
        "seasonal_adjustment_short, last_updated, popularity, notes, retrieved_at_utc, "
        "first_ingested_at, last_seen_at, ingestion_run_id "
        "FROM macro_series_metadata WHERE series_id = ?",
        [series_id],
    ).fetchone()


def count_metadata(connection: duckdb.DuckDBPyConnection) -> int:
    return connection.execute("SELECT count(*) FROM macro_series_metadata").fetchone()[0]


def fetch_run(connection: duckdb.DuckDBPyConnection, run_id: str):
    return connection.execute(
        "SELECT provider, dataset_name, status, records_received, error_category, "
        "code_version, schema_version FROM ingestion_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()


def read_only_connection(repository: MacroSeriesMetadataRepository) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(repository.database_path), read_only=True)


# --- successful insertion -----------------------------------------------------


def test_store_metadata_inserts_unseen_series(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_metadata(make_metadata())

    assert result.inserted is True
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_metadata(connection) == 1
        row = fetch_metadata(connection, "FEDFUNDS")
    finally:
        connection.close()
    assert row is not None
    assert row[2] == "Federal Funds Effective Rate"  # title
    assert row[12] == 84  # popularity
    assert row[13] == "Averages of daily figures."  # notes


def test_store_metadata_null_notes_stored_as_null(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_metadata(make_metadata(notes=None))

    connection = read_only_connection(repository)
    try:
        row = fetch_metadata(connection, "FEDFUNDS")
    finally:
        connection.close()

    assert row[13] is None  # notes


# --- matching repeat refreshes mutable metadata/provenance ---------------------


def test_repeated_ingestion_refreshes_mutable_fields_not_a_conflict(
    tmp_path, isolated_env_file
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    first = repository.store_metadata(make_metadata(title="Old Title", popularity=1))
    second = repository.store_metadata(make_metadata(title="New Title", popularity=99))

    assert first.inserted is True
    assert second.inserted is False
    assert second.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_metadata(connection) == 1
        row = fetch_metadata(connection, "FEDFUNDS")
    finally:
        connection.close()

    assert row[2] == "New Title"
    assert row[12] == 99


def test_first_ingested_at_preserved_last_seen_at_refreshed(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_metadata(make_metadata())

    connection = read_only_connection(repository)
    try:
        first_ingested_before = fetch_metadata(connection, "FEDFUNDS")[15]
    finally:
        connection.close()

    repository.store_metadata(make_metadata(retrieved_at_utc="2026-08-21T00:00:00Z"))

    connection = read_only_connection(repository)
    try:
        row = fetch_metadata(connection, "FEDFUNDS")
    finally:
        connection.close()

    assert row[15] == first_ingested_before  # first_ingested_at unchanged
    assert row[16] != first_ingested_before  # last_seen_at refreshed


# --- validation: required nonblank fields ---------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "title",
        "frequency",
        "frequency_short",
        "units",
        "units_short",
        "seasonal_adjustment",
        "seasonal_adjustment_short",
    ],
)
def test_blank_required_field_rejected_zero_writes(tmp_path, isolated_env_file, field_name):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(**{field_name: "   "}))

    connection = read_only_connection(repository)
    try:
        assert count_metadata(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("bad_date", ["2026-08-01T00:00:00Z", "not-a-date", "2026-13-01"])
def test_malformed_observation_start_rejected(tmp_path, isolated_env_file, bad_date):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(observation_start=bad_date))


def test_unnormalized_series_id_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(series_id="fedfunds"))


@pytest.mark.parametrize("bad_popularity", [-1, True, False, "84", 1.5, None])
def test_invalid_popularity_rejected(tmp_path, isolated_env_file, bad_popularity):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(popularity=bad_popularity))


@pytest.mark.parametrize("bad_notes", [123, True, ["notes"]])
def test_invalid_notes_type_rejected(tmp_path, isolated_env_file, bad_notes):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(notes=bad_notes))


@pytest.mark.parametrize(
    "bad_value",
    ["2026-08-20T13:35:01", "2026-08-20", "not-a-timestamp", 12345, None],
)
def test_ambiguous_last_updated_rejected(tmp_path, isolated_env_file, bad_value):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(last_updated=bad_value))


@pytest.mark.parametrize(
    "bad_value",
    ["2026-08-20T09:35:00", "2026-08-20", "not-a-timestamp", 12345, None],
)
def test_ambiguous_retrieved_at_rejected(tmp_path, isolated_env_file, bad_value):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(retrieved_at_utc=bad_value))


def test_non_metadata_element_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata({"not": "a FredSeriesMetadata"})


# --- fixed provenance / structural validation -------------------------------------


def test_wrong_item_provider_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(provider="alpaca"), provider="fred")


@pytest.mark.parametrize(
    "bad_provider",
    [
        pytest.param("alpaca", id="alternate"),
        pytest.param("FRED", id="wrong-case"),
        pytest.param(" fred", id="leading-space"),
        pytest.param("", id="blank"),
        pytest.param("fred; DROP TABLE macro_series_metadata;--", id="malformed-sql-like"),
        pytest.param(123, id="non-string-int"),
        pytest.param(None, id="non-string-none"),
        pytest.param(True, id="non-string-bool"),
    ],
)
def test_provider_argument_must_be_exactly_fred_zero_writes(
    tmp_path, isolated_env_file, bad_provider
):
    repository = initialized_repository(tmp_path, isolated_env_file)

    with pytest.raises(MacroSeriesMetadataStorageValidationError):
        repository.store_metadata(make_metadata(), provider=bad_provider)

    connection = read_only_connection(repository)
    try:
        assert count_metadata(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_provider_argument_error_never_echoes_untrusted_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-PROVIDER-MARKER"

    with pytest.raises(MacroSeriesMetadataStorageValidationError) as exc_info:
        repository.store_metadata(make_metadata(), provider=f"not-fred-{secret_marker}")

    assert secret_marker not in str(exc_info.value)


# --- ingestion_runs success/failure metadata --------------------------------------


def test_ingestion_run_success_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_metadata(make_metadata())

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[0] == "fred"
    assert run[1] == "macro_series_metadata"
    assert run[2] == "succeeded"
    assert run[3] == 1
    assert run[4] is None
    assert run[5] is not None  # code_version


# --- rollback when the final succeeded-status update fails -------------------------


def test_success_status_update_failure_rolls_back_and_marks_run_failed(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    original_complete_run = MacroSeriesMetadataRepository._complete_run

    def fake_complete_run(connection, *, run_id, status, records_received, error_category):
        if status == "succeeded":
            raise RuntimeError("simulated failure completing the run as succeeded")
        original_complete_run(
            connection,
            run_id=run_id,
            status=status,
            records_received=records_received,
            error_category=error_category,
        )

    monkeypatch.setattr(
        MacroSeriesMetadataRepository, "_complete_run", staticmethod(fake_complete_run)
    )

    result = repository.store_metadata(make_metadata())

    assert result.inserted is False
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_metadata(connection) == 0  # write rolled back
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "failed"
    assert run[4] == "storage_error"


# --- failures before a durable running ingestion run exists ------------------------


def test_connection_failure_raises_sanitized_error(tmp_path, isolated_env_file, monkeypatch):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-CONNECT-DETAIL"

    def fake_connect(*args, **kwargs):
        raise RuntimeError(f"boom {secret_marker}")

    with monkeypatch.context() as m:
        m.setattr(duckdb, "connect", fake_connect)
        with pytest.raises(MacroSeriesMetadataStorageError) as exc_info:
            repository.store_metadata(make_metadata())

    assert secret_marker not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroSeriesMetadataStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_metadata(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


# --- no secret/value/SQL/path/raw-exception leakage ----------------------------------


def test_result_repr_never_contains_title_or_notes(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_metadata(
        make_metadata(title="SECRET-TITLE-VALUE", notes="SECRET-NOTES-VALUE")
    )

    assert "SECRET-TITLE-VALUE" not in repr(result)
    assert "SECRET-NOTES-VALUE" not in repr(result)


def test_macro_series_metadata_repository_module_has_no_network_dependency():
    import market_intelligence.storage.macro_series_metadata_repository as module

    assert not hasattr(module, "httpx")
