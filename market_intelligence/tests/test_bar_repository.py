"""Tests for market_intelligence.storage.bar_repository.

These tests never touch the real repository database or any network/API.
Every repository under test is pointed at an isolated temporary directory
via a Settings whose project_data_path is tmp_path, mirroring
market_intelligence/tests/test_news_repository.py. Bar inputs are
constructed directly here -- never fetched live.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_bars import Bar
from market_intelligence.storage.bar_repository import (
    BarRepository,
    BarStorageError,
    BarStorageValidationError,
)
from market_intelligence.storage.database import DuckDBManager

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


def initialized_repository(tmp_path: Path, isolated_env_file: Path) -> BarRepository:
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return BarRepository(settings=settings)


def make_bar(
    *,
    provider: str = "alpaca",
    symbol: str = "SPY",
    timeframe: str = "5Min",
    feed: str = "iex",
    adjustment: str = "raw",
    currency: str = "USD",
    timestamp: str = "2026-08-19T09:30:00Z",
    open: Decimal | float = Decimal("100.0"),
    high: Decimal | float = Decimal("100000.0"),
    low: Decimal | float = Decimal("0.01"),
    close: Decimal | float = Decimal("100.5"),
    volume: int = 1000,
    trade_count: int | None = 50,
    vwap: Decimal | float | None = Decimal("100.2"),
    retrieved_at: str = "2026-08-19T09:35:00+00:00",
) -> Bar:
    return Bar(
        provider=provider,
        symbol=symbol,
        timeframe=timeframe,
        feed=feed,
        adjustment=adjustment,
        currency=currency,
        timestamp=timestamp,
        open=open if isinstance(open, Decimal) else Decimal(str(open)),
        high=high if isinstance(high, Decimal) else Decimal(str(high)),
        low=low if isinstance(low, Decimal) else Decimal(str(low)),
        close=close if isinstance(close, Decimal) else Decimal(str(close)),
        volume=volume,
        trade_count=trade_count,
        vwap=(vwap if vwap is None or isinstance(vwap, Decimal) else Decimal(str(vwap))),
        retrieved_at=retrieved_at,
    )


def fetch_bar(connection: duckdb.DuckDBPyConnection, symbol: str, timestamp_iso: str):
    return connection.execute(
        "SELECT provider, symbol, timeframe, feed, adjustment, currency, bar_timestamp, "
        "open, high, low, close, volume, trade_count, vwap, retrieved_at, first_ingested_at, "
        "last_seen_at, ingestion_run_id FROM market_bars "
        "WHERE symbol = ? AND bar_timestamp = ?",
        [symbol, timestamp_iso.replace("Z", "+00:00")],
    ).fetchone()


def count_bars(connection: duckdb.DuckDBPyConnection) -> int:
    return connection.execute("SELECT count(*) FROM market_bars").fetchone()[0]


def fetch_run(connection: duckdb.DuckDBPyConnection, run_id: str):
    return connection.execute(
        "SELECT provider, dataset_name, status, records_received, error_category, "
        "code_version, schema_version FROM ingestion_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()


def read_only_connection(repository: BarRepository) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(repository.database_path), read_only=True)


# --- successful insertion -----------------------------------------------------


def test_store_bars_inserts_unseen_bar(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_bars([make_bar()])

    assert result.received == 1
    assert result.inserted == 1
    assert result.existing_or_updated == 0
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 1
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
    finally:
        connection.close()
    assert row is not None
    assert row[7] == Decimal("100.0")  # open


# --- deterministic duplicate handling within a batch ---------------------------


def test_store_bars_deterministic_duplicate_within_batch(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    bar = make_bar()
    result = repository.store_bars([bar, bar, bar])

    assert result.received == 3
    assert result.inserted == 1
    assert result.existing_or_updated == 2
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 1
    finally:
        connection.close()


def test_store_bars_conflicting_duplicate_within_batch_rolls_back(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_bars(
        [make_bar(close=Decimal("100.5")), make_bar(close=Decimal("200.0"))]
    )

    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 2
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 0
    finally:
        connection.close()


# --- repeated ingestion without duplicate rows ----------------------------------


def test_repeated_ingestion_no_duplicate_rows(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar()])
    result = repository.store_bars([make_bar()])

    assert result.received == 1
    assert result.inserted == 0
    assert result.existing_or_updated == 1
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 1
    finally:
        connection.close()


def test_exact_duplicate_refreshes_retrieval_provenance_only(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(retrieved_at="2026-08-19T09:35:00+00:00")])
    repository.store_bars([make_bar(retrieved_at="2026-08-19T18:00:00+00:00")])

    connection = read_only_connection(repository)
    try:
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
    finally:
        connection.close()

    assert row[14].isoformat().startswith("2026-08-19T18:00:00")  # retrieved_at
    assert row[7] == Decimal("100.0")  # open unchanged


def test_first_ingested_at_preserved_last_seen_at_refreshed(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar()])

    connection = read_only_connection(repository)
    try:
        first_ingested_before = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")[15]
    finally:
        connection.close()

    repository.store_bars([make_bar(retrieved_at="2026-08-20T00:00:00+00:00")])

    connection = read_only_connection(repository)
    try:
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
    finally:
        connection.close()

    assert row[15] == first_ingested_before  # first_ingested_at unchanged
    assert row[16] != first_ingested_before  # last_seen_at refreshed


# --- conflicting bar rollback ---------------------------------------------------


def test_conflicting_existing_bar_does_not_overwrite(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(close=Decimal("100.5"))])

    result = repository.store_bars([make_bar(close=Decimal("999.99"))])

    assert result.received == 1
    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert row[10] == Decimal("100.5")  # close unchanged
    assert run[2] == "failed"
    assert run[4] == "content_conflict"


def test_conflicting_volume_is_also_detected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(volume=1000)])

    result = repository.store_bars([make_bar(volume=2000)])

    assert result.failed == 1
    assert result.ingestion_run_status == "failed"


def test_conflicting_trade_count_or_vwap_is_detected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(trade_count=50, vwap=Decimal("100.2"))])

    result = repository.store_bars([make_bar(trade_count=75, vwap=Decimal("100.2"))])

    assert result.failed == 1
    assert result.ingestion_run_status == "failed"


# --- mixed insert/conflict rollback ---------------------------------------------


def test_mixed_insert_and_conflict_rolls_back_entire_batch(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(timestamp="2026-08-19T09:30:00Z", close=Decimal("100.5"))])

    result = repository.store_bars(
        [
            make_bar(timestamp="2026-08-19T09:31:00Z", close=Decimal("101.5")),
            make_bar(timestamp="2026-08-19T09:30:00Z", close=Decimal("999.0")),  # conflict
            make_bar(timestamp="2026-08-19T09:32:00Z", close=Decimal("102.5")),
        ]
    )

    assert result.received == 3
    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 3
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 1  # only the original pre-existing row
        assert fetch_bar(connection, "SPY", "2026-08-19T09:31:00Z") is None
        assert fetch_bar(connection, "SPY", "2026-08-19T09:32:00Z") is None
    finally:
        connection.close()


# --- Decimal precision behavior --------------------------------------------------


def test_decimal_values_stored_precisely(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars(
        [make_bar(open=Decimal("100.123456"), high=Decimal("101.999999"), vwap=Decimal("100.5"))]
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
    finally:
        connection.close()

    assert row[7] == Decimal("100.123456")
    assert row[8] == Decimal("101.999999")
    assert row[13] == Decimal("100.5")


# --- nullable trade_count/vwap ---------------------------------------------------


def test_null_trade_count_and_vwap_stored_as_null(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(trade_count=None, vwap=None)])

    connection = read_only_connection(repository)
    try:
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
    finally:
        connection.close()

    assert row[12] is None  # trade_count
    assert row[13] is None  # vwap


# --- timestamp normalization and rejection ---------------------------------------


def test_offset_timestamps_normalized_to_utc(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars(
        [make_bar(timestamp="2026-08-19T05:30:00-04:00", retrieved_at="2026-08-19T09:35:00Z")]
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_bar(connection, "SPY", "2026-08-19T09:30:00Z")
    finally:
        connection.close()

    assert row is not None
    assert row[6].isoformat().startswith("2026-08-19T09:30:00")


_AMBIGUOUS_TIMESTAMPS = [
    pytest.param("2026-08-19T09:30:00", id="naive"),
    pytest.param("2026-08-19", id="date-only"),
    pytest.param("not-a-timestamp", id="malformed"),
    pytest.param("   ", id="blank"),
    pytest.param(12345, id="non-string"),
    pytest.param(None, id="none"),
]


@pytest.mark.parametrize("field_name", ["timestamp", "retrieved_at"])
@pytest.mark.parametrize("bad_value", _AMBIGUOUS_TIMESTAMPS)
def test_ambiguous_timestamps_rejected(tmp_path, isolated_env_file, field_name, bad_value):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(**{field_name: bad_value})])


# --- candle validation -------------------------------------------------------------


_CANDLE_INCONSISTENT = [
    dict(high=Decimal("99.0"), low=Decimal("100.0")),  # high < low
    dict(high=Decimal("100.0"), open=Decimal("101.0"), low=Decimal("99.0"), close=Decimal("99.5")),
    dict(high=Decimal("100.0"), open=Decimal("99.5"), low=Decimal("99.0"), close=Decimal("101.0")),
    dict(high=Decimal("100.0"), open=Decimal("98.0"), low=Decimal("99.0"), close=Decimal("99.5")),
]


@pytest.mark.parametrize("overrides", _CANDLE_INCONSISTENT)
def test_candle_inconsistent_bars_rejected(tmp_path, isolated_env_file, overrides):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(**overrides)])


# --- fixed provenance validation ---------------------------------------------------


def test_wrong_feed_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(feed="sip")])


def test_wrong_adjustment_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(adjustment="split")])


def test_wrong_currency_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(currency="EUR")])


def test_unapproved_timeframe_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(timeframe="1Hour")])


def test_provider_mismatch_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(provider="alpaca")], provider="other")


def test_non_bar_element_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([{"not": "a Bar"}])


def test_zero_writes_after_validation_failure(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(BarStorageValidationError):
        repository.store_bars([make_bar(feed="sip")])

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


# --- ingestion_runs success/failure metadata --------------------------------------


def test_ingestion_run_success_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_bars(
        [
            make_bar(timestamp="2026-08-19T09:30:00Z"),
            make_bar(timestamp="2026-08-19T09:31:00Z"),
        ],
        dataset_name="bars",
    )

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[0] == "alpaca"
    assert run[1] == "bars"
    assert run[2] == "succeeded"
    assert run[3] == 2
    assert run[4] is None
    assert run[5] is not None  # code_version
    assert run[6] == "0005"  # schema_version


def test_ingestion_run_failure_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(close=Decimal("100.5"))])
    result = repository.store_bars([make_bar(close=Decimal("999.0"))])

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "failed"
    assert run[3] == 1
    assert run[4] == "content_conflict"


# --- rollback when the final succeeded-status update fails -------------------------


def test_success_status_update_failure_rolls_back_bars_and_marks_run_failed(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    original_complete_run = BarRepository._complete_run

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

    monkeypatch.setattr(BarRepository, "_complete_run", staticmethod(fake_complete_run))

    result = repository.store_bars([make_bar()])

    assert result.received == 1
    assert result.inserted == 0
    assert result.existing_or_updated == 0
    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 0  # bar write rolled back
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

    monkeypatch.setattr(BarRepository, "_complete_run", staticmethod(always_fail_complete_run))

    with pytest.raises(BarStorageError) as exc_info:
        repository.store_bars([make_bar()])

    assert "SECRET-INTERNAL-DETAIL" not in str(exc_info.value)
    assert not isinstance(exc_info.value, BarStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 0  # bar write rolled back
        statuses = [
            row[0] for row in connection.execute("SELECT status FROM ingestion_runs").fetchall()
        ]
    finally:
        connection.close()

    assert statuses == ["running"]  # neither status update ever committed


# --- no secret/OHLCV/SQL/path/raw-exception leakage ----------------------------------


def test_result_repr_never_contains_ohlcv_values(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_bars([make_bar(close=Decimal("123.456789"))])

    assert "123.456789" not in repr(result)


def test_conflict_result_repr_never_contains_ohlcv_values(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_bars([make_bar(close=Decimal("111.111111"))])
    result = repository.store_bars([make_bar(close=Decimal("222.222222"))])

    assert "111.111111" not in repr(result)
    assert "222.222222" not in repr(result)


def test_validation_error_never_contains_timestamp_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-TIMESTAMP-MARKER"
    with pytest.raises(BarStorageValidationError) as exc_info:
        repository.store_bars([make_bar(timestamp=f"not-a-timestamp-{secret_marker}")])

    assert secret_marker not in str(exc_info.value)


def test_bar_repository_module_has_no_network_dependency():
    import market_intelligence.storage.bar_repository as module

    assert not hasattr(module, "httpx")


# --- successful atomic storage ------------------------------------------------------


def test_successful_store_is_atomic_with_succeeded_run_status(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_bars(
        [
            make_bar(timestamp="2026-08-19T09:30:00Z"),
            make_bar(timestamp="2026-08-19T09:31:00Z"),
        ]
    )

    assert result.ingestion_run_status == "succeeded"
    assert result.inserted == 2
    assert result.failed == 0

    connection = read_only_connection(repository)
    try:
        assert count_bars(connection) == 2
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "succeeded"
