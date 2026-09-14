"""Tests for market_intelligence.storage.macro_observation_repository.

These tests never touch the real repository database or any network/API.
Every repository under test is pointed at an isolated temporary directory
via a Settings whose project_data_path is tmp_path, mirroring
market_intelligence/tests/test_bar_repository.py. Observation inputs are
constructed directly here -- never fetched live.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.fred_macro_data import FredObservation
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.macro_observation_repository import (
    MacroObservationRepository,
    MacroObservationStorageError,
    MacroObservationStorageValidationError,
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


def initialized_repository(tmp_path: Path, isolated_env_file: Path) -> MacroObservationRepository:
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return MacroObservationRepository(settings=settings)


def make_observation(
    *,
    provider: str = "fred",
    series_id: str = "FEDFUNDS",
    observation_date: str = "2026-08-01",
    value: Decimal | None = Decimal("5.33"),
    is_missing: bool = False,
    realtime_start: str = "2026-08-20",
    realtime_end: str = "2026-08-20",
    retrieved_at: str = "2026-08-20T09:35:00Z",
) -> FredObservation:
    return FredObservation(
        provider=provider,
        series_id=series_id,
        observation_date=observation_date,
        value=value,
        is_missing=is_missing,
        realtime_start=realtime_start,
        realtime_end=realtime_end,
        retrieved_at=retrieved_at,
    )


def fetch_observation(connection: duckdb.DuckDBPyConnection, series_id: str, observation_date: str):
    return connection.execute(
        "SELECT provider, series_id, observation_date, realtime_start, realtime_end, value, "
        "is_missing, retrieved_at, first_ingested_at, last_seen_at, ingestion_run_id "
        "FROM macro_observations WHERE series_id = ? AND observation_date = ?",
        [series_id, date.fromisoformat(observation_date)],
    ).fetchone()


def count_observations(connection: duckdb.DuckDBPyConnection) -> int:
    return connection.execute("SELECT count(*) FROM macro_observations").fetchone()[0]


def fetch_run(connection: duckdb.DuckDBPyConnection, run_id: str):
    return connection.execute(
        "SELECT provider, dataset_name, status, records_received, error_category, "
        "code_version, schema_version FROM ingestion_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()


def read_only_connection(repository: MacroObservationRepository) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(repository.database_path), read_only=True)


# --- successful insertion -----------------------------------------------------


def test_store_observations_inserts_unseen_observation(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations([make_observation()])

    assert result.received == 1
    assert result.inserted == 1
    assert result.existing_or_updated == 0
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 1
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
    finally:
        connection.close()
    assert row is not None
    assert row[5] == Decimal("5.33")  # value


# --- deterministic duplicate handling within a batch ---------------------------


def test_store_observations_deterministic_duplicate_within_batch(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    observation = make_observation()
    result = repository.store_observations([observation, observation, observation])

    assert result.received == 3
    assert result.inserted == 1
    assert result.existing_or_updated == 2
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 1
    finally:
        connection.close()


def test_store_observations_conflicting_duplicate_within_batch_rolls_back(
    tmp_path, isolated_env_file
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations(
        [make_observation(value=Decimal("5.33")), make_observation(value=Decimal("9.99"))]
    )

    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 2
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
    finally:
        connection.close()


# --- repeated ingestion without duplicate rows ----------------------------------


def test_repeated_ingestion_no_duplicate_rows(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation()])
    result = repository.store_observations([make_observation()])

    assert result.received == 1
    assert result.inserted == 0
    assert result.existing_or_updated == 1
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 1
    finally:
        connection.close()


def test_exact_duplicate_refreshes_retrieval_provenance_only(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(retrieved_at="2026-08-20T09:35:00Z")])
    repository.store_observations([make_observation(retrieved_at="2026-08-20T18:00:00Z")])

    connection = read_only_connection(repository)
    try:
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
    finally:
        connection.close()

    assert row[7].isoformat().startswith("2026-08-20T18:00:00")  # retrieved_at
    assert row[5] == Decimal("5.33")  # value unchanged


def test_first_ingested_at_preserved_last_seen_at_refreshed(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation()])

    connection = read_only_connection(repository)
    try:
        first_ingested_before = fetch_observation(connection, "FEDFUNDS", "2026-08-01")[8]
    finally:
        connection.close()

    repository.store_observations([make_observation(retrieved_at="2026-08-21T00:00:00Z")])

    connection = read_only_connection(repository)
    try:
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
    finally:
        connection.close()

    assert row[8] == first_ingested_before  # first_ingested_at unchanged
    assert row[9] != first_ingested_before  # last_seen_at refreshed


# --- conflicting observation rollback -------------------------------------------


def test_conflicting_existing_observation_does_not_overwrite(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=Decimal("5.33"))])

    result = repository.store_observations([make_observation(value=Decimal("999.99"))])

    assert result.received == 1
    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert row[5] == Decimal("5.33")  # value unchanged
    assert run[2] == "failed"
    assert run[4] == "content_conflict"


def test_conflicting_is_missing_is_also_detected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=Decimal("5.33"), is_missing=False)])

    result = repository.store_observations([make_observation(value=None, is_missing=True)])

    assert result.failed == 1
    assert result.ingestion_run_status == "failed"


# --- mixed insert/conflict rollback ---------------------------------------------


def test_mixed_insert_and_conflict_rolls_back_entire_batch(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations(
        [make_observation(observation_date="2026-08-01", value=Decimal("5.33"))]
    )

    result = repository.store_observations(
        [
            make_observation(observation_date="2026-08-02", value=Decimal("5.34")),
            make_observation(observation_date="2026-08-01", value=Decimal("999.0")),  # conflict
            make_observation(observation_date="2026-08-03", value=Decimal("5.35")),
        ]
    )

    assert result.received == 3
    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 3
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 1  # only the original pre-existing row
        assert fetch_observation(connection, "FEDFUNDS", "2026-08-02") is None
        assert fetch_observation(connection, "FEDFUNDS", "2026-08-03") is None
    finally:
        connection.close()


# --- missing-value handling -------------------------------------------------------


def test_missing_observation_stores_null_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=None, is_missing=True)])

    connection = read_only_connection(repository)
    try:
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
    finally:
        connection.close()

    assert row[5] is None  # value
    assert row[6] is True  # is_missing


def test_missing_observation_with_non_null_value_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(value=Decimal("5.33"), is_missing=True)])


def test_present_observation_with_null_value_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(value=None, is_missing=False)])


# --- vintage/revision provenance preservation -------------------------------------


def test_different_realtime_vintage_is_a_distinct_row(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations(
        [
            make_observation(
                value=Decimal("5.30"), realtime_start="2026-08-05", realtime_end="2026-08-05"
            )
        ]
    )
    result = repository.store_observations(
        [
            make_observation(
                value=Decimal("5.33"), realtime_start="2026-08-20", realtime_end="2026-08-20"
            )
        ]
    )

    assert result.inserted == 1
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 2
    finally:
        connection.close()


# --- Decimal precision behavior --------------------------------------------------


def test_decimal_values_stored_precisely(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=Decimal("100.123456"))])

    connection = read_only_connection(repository)
    try:
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
    finally:
        connection.close()

    assert row[5] == Decimal("100.123456")


def test_exactly_six_fractional_digits_succeeds_unchanged(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations([make_observation(value=Decimal("5.123456"))])

    assert result.ingestion_run_status == "succeeded"


def test_more_than_six_fractional_digits_rejected_zero_writes(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(value=Decimal("5.1234567"))])

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_positive_overflow_rejected_zero_writes(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations(
            [make_observation(value=Decimal("100000000000000.000000"))]
        )

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_negative_overflow_rejected_zero_writes(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations(
            [make_observation(value=Decimal("-100000000000000.000000"))]
        )

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_boundary_max_decimal_value_accepted(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations(
        [make_observation(value=Decimal("99999999999999.999999"))]
    )

    assert result.ingestion_run_status == "succeeded"


def test_decimal_precision_error_never_exposes_rejected_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError) as exc_info:
        repository.store_observations([make_observation(value=Decimal("123.1234567"))])

    assert "123.1234567" not in str(exc_info.value)


def test_decimal_overflow_error_never_exposes_rejected_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError) as exc_info:
        repository.store_observations(
            [make_observation(value=Decimal("100000000000000.000000"))]
        )

    assert "100000000000000" not in str(exc_info.value)


def test_non_finite_value_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(value=Decimal("NaN"))])


# --- date/series_id normalization enforcement -------------------------------------


def test_unnormalized_series_id_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(series_id="fedfunds")])


def test_malformed_series_id_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(series_id="../../etc/passwd")])


@pytest.mark.parametrize(
    "field_name", ["observation_date", "realtime_start", "realtime_end"]
)
@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param("2026-08-01T00:00:00Z", id="datetime-string"),
        pytest.param("2026-13-01", id="invalid-month"),
        pytest.param("2026-02-30", id="invalid-day"),
        pytest.param("not-a-date", id="malformed"),
        pytest.param("   ", id="blank"),
        pytest.param(20260801, id="non-string"),
        pytest.param(None, id="none"),
    ],
)
def test_ambiguous_dates_rejected(tmp_path, isolated_env_file, field_name, bad_value):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(**{field_name: bad_value})])


# --- timestamp rejection -----------------------------------------------------------


_AMBIGUOUS_TIMESTAMPS = [
    pytest.param("2026-08-20T09:35:00", id="naive"),
    pytest.param("2026-08-20", id="date-only"),
    pytest.param("not-a-timestamp", id="malformed"),
    pytest.param("   ", id="blank"),
    pytest.param(12345, id="non-string"),
    pytest.param(None, id="none"),
]


@pytest.mark.parametrize("bad_value", _AMBIGUOUS_TIMESTAMPS)
def test_ambiguous_retrieved_at_rejected(tmp_path, isolated_env_file, bad_value):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(retrieved_at=bad_value)])


def test_validation_error_never_contains_timestamp_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-TIMESTAMP-MARKER"
    with pytest.raises(MacroObservationStorageValidationError) as exc_info:
        repository.store_observations(
            [make_observation(retrieved_at=f"not-a-timestamp-{secret_marker}")]
        )

    assert secret_marker not in str(exc_info.value)


# --- fixed provenance / structural validation -------------------------------------


def test_wrong_provider_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(provider="alpaca")], provider="fred")


def test_provider_mismatch_against_requested_provider_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(provider="fred")], provider="other")


# --- provider argument must be exactly "fred" (zero writes for any deviation) ------


@pytest.mark.parametrize(
    "bad_provider",
    [
        pytest.param("alpaca", id="alternate"),
        pytest.param("FRED", id="wrong-case"),
        pytest.param(" fred", id="leading-space"),
        pytest.param("fred ", id="trailing-space"),
        pytest.param("", id="blank"),
        pytest.param("   ", id="blank-whitespace"),
        pytest.param("fred; DROP TABLE macro_observations;--", id="malformed-sql-like"),
        pytest.param("../../etc/passwd", id="malformed-path-like"),
        pytest.param(123, id="non-string-int"),
        pytest.param(None, id="non-string-none"),
        pytest.param(True, id="non-string-bool"),
        pytest.param(["fred"], id="non-string-list"),
    ],
)
def test_provider_argument_must_be_exactly_fred_zero_writes(
    tmp_path, isolated_env_file, bad_provider
):
    repository = initialized_repository(tmp_path, isolated_env_file)

    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation()], provider=bad_provider)

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_provider_argument_error_never_echoes_untrusted_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-PROVIDER-MARKER"

    with pytest.raises(MacroObservationStorageValidationError) as exc_info:
        repository.store_observations([make_observation()], provider=f"not-fred-{secret_marker}")

    assert secret_marker not in str(exc_info.value)


def test_non_observation_element_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([{"not": "a FredObservation"}])


def test_generator_input_rejected_before_len_zero_writes(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)

    def observation_generator():
        yield make_observation()

    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations(observation_generator())

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_empty_batch_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([])

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_zero_writes_after_validation_failure(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(MacroObservationStorageValidationError):
        repository.store_observations([make_observation(series_id="not normalized")])

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


# --- ingestion_runs success/failure metadata --------------------------------------


def test_ingestion_run_success_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations(
        [
            make_observation(observation_date="2026-08-01"),
            make_observation(observation_date="2026-08-02"),
        ],
        dataset_name="macro_observations",
    )

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[0] == "fred"
    assert run[1] == "macro_observations"
    assert run[2] == "succeeded"
    assert run[3] == 2
    assert run[4] is None
    assert run[5] is not None  # code_version
    assert run[6] == "0009"  # schema_version


def test_ingestion_run_failure_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=Decimal("5.33"))])
    result = repository.store_observations([make_observation(value=Decimal("999.0"))])

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "failed"
    assert run[3] == 1
    assert run[4] == "content_conflict"


# --- rollback when the final succeeded-status update fails -------------------------


def test_success_status_update_failure_rolls_back_observations_and_marks_run_failed(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    original_complete_run = MacroObservationRepository._complete_run

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
        MacroObservationRepository, "_complete_run", staticmethod(fake_complete_run)
    )

    result = repository.store_observations([make_observation()])

    assert result.received == 1
    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0  # write rolled back
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "failed"
    assert run[4] == "storage_error"


# --- failure-status recording itself failing ----------------------------------------


def test_failed_status_recording_failure_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)

    def always_fail_complete_run(connection, *, run_id, status, records_received, error_category):
        raise RuntimeError("simulated failure with SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr(
        MacroObservationRepository, "_complete_run", staticmethod(always_fail_complete_run)
    )

    with pytest.raises(MacroObservationStorageError) as exc_info:
        repository.store_observations([make_observation()])

    assert "SECRET-INTERNAL-DETAIL" not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroObservationStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        statuses = [
            row[0] for row in connection.execute("SELECT status FROM ingestion_runs").fetchall()
        ]
    finally:
        connection.close()

    assert statuses == ["running"]  # neither status update ever committed


# --- no secret/value/SQL/path/raw-exception leakage ----------------------------------


def test_result_repr_never_contains_observation_values(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations([make_observation(value=Decimal("123.456789"))])

    assert "123.456789" not in repr(result)


def test_conflict_result_repr_never_contains_observation_values(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=Decimal("111.111111"))])
    result = repository.store_observations([make_observation(value=Decimal("222.222222"))])

    assert "111.111111" not in repr(result)
    assert "222.222222" not in repr(result)


def test_macro_observation_repository_module_has_no_network_dependency():
    import market_intelligence.storage.macro_observation_repository as module

    assert not hasattr(module, "httpx")


# --- successful atomic storage ------------------------------------------------------


def test_successful_store_is_atomic_with_succeeded_run_status(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_observations(
        [
            make_observation(observation_date="2026-08-01"),
            make_observation(observation_date="2026-08-02"),
        ]
    )

    assert result.ingestion_run_status == "succeeded"
    assert result.inserted == 2
    assert result.failed == 0

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 2
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "succeeded"


# --- failures before a durable running ingestion run exists ------------------------


def test_connection_failure_raises_sanitized_error(tmp_path, isolated_env_file, monkeypatch):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-CONNECT-DETAIL"

    def fake_connect(*args, **kwargs):
        raise RuntimeError(f"boom {secret_marker}")

    with monkeypatch.context() as m:
        m.setattr(duckdb, "connect", fake_connect)
        with pytest.raises(MacroObservationStorageError) as exc_info:
            repository.store_observations([make_observation()])

    assert secret_marker not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroObservationStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_schema_version_read_failure_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-SCHEMA-READ-DETAIL"

    def fake_schema_version(connection):
        raise RuntimeError(f"boom {secret_marker}")

    monkeypatch.setattr(
        MacroObservationRepository, "_current_schema_version", staticmethod(fake_schema_version)
    )

    with pytest.raises(MacroObservationStorageError) as exc_info:
        repository.store_observations([make_observation()])

    assert secret_marker not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroObservationStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_initial_running_row_insertion_failure_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    fixed_run_id = "11111111-1111-1111-1111-111111111111"

    connection = duckdb.connect(str(repository.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version) "
            "VALUES (?, 'fred', 'macro_observations', now(), 'running', 'preexisting')",
            [fixed_run_id],
        )
    finally:
        connection.close()

    monkeypatch.setattr(uuid, "uuid4", lambda: uuid.UUID(fixed_run_id))

    with pytest.raises(MacroObservationStorageError) as exc_info:
        repository.store_observations([make_observation()])

    assert fixed_run_id not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroObservationStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 0
        rows = connection.execute("SELECT run_id, status FROM ingestion_runs").fetchall()
    finally:
        connection.close()

    assert rows == [(fixed_run_id, "running")]


# --- rollback failure ---------------------------------------------------------------


class _RollbackFailingConnection:
    """Wraps a real DuckDB connection, forwarding everything except ROLLBACK."""

    def __init__(self, real: duckdb.DuckDBPyConnection, secret_marker: str) -> None:
        self._real = real
        self._secret_marker = secret_marker

    def execute(self, query, *args, **kwargs):
        if isinstance(query, str) and query.strip().upper() == "ROLLBACK":
            raise RuntimeError(f"boom {self._secret_marker}")
        return self._real.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_rollback_failure_raises_sanitized_error(tmp_path, isolated_env_file, monkeypatch):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_observations([make_observation(value=Decimal("5.33"))])

    secret_marker = "SECRET-ROLLBACK-DETAIL"
    real_connect = duckdb.connect

    def fake_connect(*args, **kwargs):
        return _RollbackFailingConnection(real_connect(*args, **kwargs), secret_marker)

    with monkeypatch.context() as m:
        m.setattr(duckdb, "connect", fake_connect)
        with pytest.raises(MacroObservationStorageError) as exc_info:
            repository.store_observations([make_observation(value=Decimal("999.0"))])

    assert secret_marker not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroObservationStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 1
        row = fetch_observation(connection, "FEDFUNDS", "2026-08-01")
        statuses = [
            r[0] for r in connection.execute("SELECT status FROM ingestion_runs").fetchall()
        ]
    finally:
        connection.close()

    assert row[5] == Decimal("5.33")  # original observation unchanged
    assert statuses.count("succeeded") == 1
    assert statuses.count("running") == 1


# --- connection-close failure ---------------------------------------------------------


class _CloseFailingConnection:
    """Wraps a real DuckDB connection, forwarding everything except close()."""

    def __init__(self, real: duckdb.DuckDBPyConnection, secret_marker: str) -> None:
        self._real = real
        self._secret_marker = secret_marker

    def close(self):
        raise RuntimeError(f"boom {self._secret_marker}")

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_connection_close_failure_does_not_override_successful_result(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-CLOSE-SUCCESS-DETAIL"
    real_connect = duckdb.connect

    def fake_connect(*args, **kwargs):
        return _CloseFailingConnection(real_connect(*args, **kwargs), secret_marker)

    with monkeypatch.context() as m:
        m.setattr(duckdb, "connect", fake_connect)
        result = repository.store_observations([make_observation()])

    assert result.ingestion_run_status == "succeeded"
    assert result.inserted == 1

    connection = read_only_connection(repository)
    try:
        assert count_observations(connection) == 1
    finally:
        connection.close()


def test_connection_close_failure_does_not_replace_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    close_secret = "SECRET-CLOSE-ERROR-DETAIL"
    schema_secret = "SECRET-SCHEMA-ERROR-DETAIL"
    real_connect = duckdb.connect

    def fake_connect(*args, **kwargs):
        return _CloseFailingConnection(real_connect(*args, **kwargs), close_secret)

    def fake_schema_version(connection):
        raise RuntimeError(f"boom {schema_secret}")

    with monkeypatch.context() as m:
        m.setattr(duckdb, "connect", fake_connect)
        m.setattr(
            MacroObservationRepository,
            "_current_schema_version",
            staticmethod(fake_schema_version),
        )
        with pytest.raises(MacroObservationStorageError) as exc_info:
            repository.store_observations([make_observation()])

    assert close_secret not in str(exc_info.value)
    assert schema_secret not in str(exc_info.value)
    assert not isinstance(exc_info.value, MacroObservationStorageValidationError)
