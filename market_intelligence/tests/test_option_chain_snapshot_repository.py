"""Tests for market_intelligence.storage.option_chain_snapshot_repository.

These tests never touch the real repository database or any network/API.
Every repository under test is pointed at an isolated temporary directory
via a Settings whose project_data_path is tmp_path, mirroring
market_intelligence/tests/test_bar_repository.py. Snapshot inputs are
constructed directly here -- never fetched live.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_options_chain import (
    MAX_EXPIRATION_RANGE_DAYS,
    MAX_LIMIT,
    MAX_PAGES,
    MAX_STRIKE_RANGE_WIDTH,
    MAX_TOTAL_CONTRACTS,
    OptionChainRequest,
    OptionChainSnapshot,
    normalize_option_chain_request,
)
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.option_chain_snapshot_repository import (
    OptionChainSnapshotRepository,
    OptionChainSnapshotStorageError,
    OptionChainSnapshotStorageValidationError,
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


def initialized_repository(tmp_path, isolated_env_file) -> OptionChainSnapshotRepository:
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return OptionChainSnapshotRepository(settings=settings)


def make_request(**overrides):
    kwargs = dict(
        underlying="SPY",
        feed="opra",
        expiration_date_gte="2026-09-01",
        expiration_date_lte="2026-09-30",
        strike_price_gte=Decimal("400"),
        strike_price_lte=Decimal("600"),
        option_type=None,
        limit=1000,
        max_pages=1,
        max_total_contracts=1000,
    )
    kwargs.update(overrides)
    return normalize_option_chain_request(**kwargs)


def make_snapshot(
    *,
    contract_symbol: str = "SPY260918C00500000",
    feed: str = "opra",
    expiration_date: str = "2026-09-18",
    option_type: str = "call",
    strike_price: Decimal = Decimal("500"),
    quote_timestamp: str | None = "2026-09-02T15:30:00Z",
    bid_price: Decimal | None = Decimal("5.25"),
    bid_size: int | None = 10,
    ask_price: Decimal | None = Decimal("5.35"),
    ask_size: int | None = 12,
    trade_timestamp: str | None = "2026-09-02T15:29:55Z",
    trade_price: Decimal | None = Decimal("5.30"),
    trade_size: int | None = 3,
    implied_volatility: Decimal | None = Decimal("0.1543"),
    delta: Decimal | None = Decimal("0.42"),
    gamma: Decimal | None = Decimal("0.03"),
    theta: Decimal | None = Decimal("-0.06"),
    vega: Decimal | None = Decimal("0.11"),
    rho: Decimal | None = Decimal("-0.02"),
    retrieved_at: str = "2026-09-02T15:30:05Z",
) -> OptionChainSnapshot:
    return OptionChainSnapshot(
        provider="alpaca",
        underlying="SPY",
        feed=feed,
        contract_symbol=contract_symbol,
        expiration_date=expiration_date,
        option_type=option_type,
        strike_price=strike_price,
        quote_timestamp=quote_timestamp,
        bid_price=bid_price,
        bid_size=bid_size,
        ask_price=ask_price,
        ask_size=ask_size,
        trade_timestamp=trade_timestamp,
        trade_price=trade_price,
        trade_size=trade_size,
        implied_volatility=implied_volatility,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        retrieved_at=retrieved_at,
    )


def read_only_connection(repository) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(repository.database_path), read_only=True)


def count_rows(connection) -> int:
    return connection.execute("SELECT count(*) FROM option_chain_snapshots").fetchone()[0]


def fetch_run(connection, run_id: str):
    return connection.execute(
        "SELECT provider, dataset_name, status, records_received, error_category, "
        "code_version, schema_version FROM ingestion_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()


def fetch_batch(connection, run_id: str):
    return connection.execute(
        "SELECT provider, underlying, requested_feed, requested_expiration_date_gte, "
        "requested_expiration_date_lte, requested_strike_price_gte, "
        "requested_strike_price_lte, requested_option_type, retrieved_at, contract_count, "
        "outcome FROM option_chain_snapshot_batches WHERE ingestion_run_id = ?",
        [run_id],
    ).fetchone()


def count_batch_rows(connection) -> int:
    return connection.execute("SELECT count(*) FROM option_chain_snapshot_batches").fetchone()[0]


def count_batch_item_rows(connection, run_id: str | None = None) -> int:
    if run_id is None:
        return connection.execute(
            "SELECT count(*) FROM option_chain_snapshot_batch_items"
        ).fetchone()[0]
    return connection.execute(
        "SELECT count(*) FROM option_chain_snapshot_batch_items WHERE ingestion_run_id = ?",
        [run_id],
    ).fetchone()[0]


def fetch_batch_item_symbols(connection, run_id: str) -> set[str]:
    rows = connection.execute(
        "SELECT contract_symbol FROM option_chain_snapshot_batch_items "
        "WHERE ingestion_run_id = ?",
        [run_id],
    ).fetchall()
    return {r[0] for r in rows}


def fetch_snapshot_ingestion_run_id(connection, contract_symbol: str = "SPY260918C00500000") -> str:
    return connection.execute(
        "SELECT ingestion_run_id FROM option_chain_snapshots WHERE contract_symbol = ?",
        [contract_symbol],
    ).fetchone()[0]


# --- successful insertion ---------------------------------------------------


def test_store_inserts_unseen_snapshot(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    result = repository.store_snapshots([make_snapshot()], request=request)

    assert result.received == 1
    assert result.inserted == 1
    assert result.existing_or_updated == 0
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 1
        row = connection.execute(
            "SELECT strike_price, bid_price, implied_volatility, delta, theta, "
            "ingestion_run_id FROM option_chain_snapshots"
        ).fetchone()
        batch = fetch_batch(connection, result.ingestion_run_id)
    finally:
        connection.close()
    assert row[0] == Decimal("500.000000")
    assert row[1] == Decimal("5.250000")
    assert row[2] == Decimal("0.1543000000")
    assert row[3] == Decimal("0.4200000000")
    assert row[4] == Decimal("-0.0600000000")
    assert row[5] == result.ingestion_run_id
    # request-level fields live once on the batch row, not duplicated per snapshot
    assert batch[2] == "opra"  # requested_feed
    assert batch[9] == 1  # contract_count
    assert batch[10] == "succeeded"  # outcome


def test_store_transactional_run_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_snapshots([make_snapshot()], request=make_request())

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()
    assert run[0] == "alpaca"
    assert run[1] == "option_chain_snapshots"
    assert run[2] == "succeeded"
    assert run[3] == 1
    assert run[4] is None
    assert run[6] == "0010"
    assert result.batch_outcome == "succeeded"


# --- idempotency ----------------------------------------------------------


def test_restoring_identical_snapshot_refreshes_provenance_only(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots([make_snapshot()], request=request)
    result = repository.store_snapshots([make_snapshot()], request=request)

    assert result.inserted == 0
    assert result.existing_or_updated == 1
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 1
        # the snapshot row is never relinked to the later run -- it still
        # attributes creation to the run that first inserted it
        assert fetch_snapshot_ingestion_run_id(connection) == first.ingestion_run_id
        assert fetch_snapshot_ingestion_run_id(connection) != result.ingestion_run_id
    finally:
        connection.close()


def test_restoring_identical_snapshot_does_not_mutate_or_relink_original_row(
    tmp_path, isolated_env_file
):
    """Requirement: re-storing an identical snapshot must not mutate/relink the original row."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots([make_snapshot()], request=request)

    connection = read_only_connection(repository)
    try:
        before = connection.execute(
            "SELECT provider, underlying, feed, contract_symbol, retrieved_at, "
            "expiration_date, option_type, strike_price, bid_price, first_ingested_at, "
            "ingestion_run_id FROM option_chain_snapshots"
        ).fetchone()
    finally:
        connection.close()

    second = repository.store_snapshots([make_snapshot()], request=request)
    assert second.ingestion_run_id != first.ingestion_run_id

    connection = read_only_connection(repository)
    try:
        after = connection.execute(
            "SELECT provider, underlying, feed, contract_symbol, retrieved_at, "
            "expiration_date, option_type, strike_price, bid_price, first_ingested_at, "
            "ingestion_run_id FROM option_chain_snapshots"
        ).fetchone()
    finally:
        connection.close()
    # every column except the (untested here) last_seen_at bookkeeping column
    # is byte-identical -- the row was never mutated or relinked
    assert before == after


def test_conflicting_values_same_identity_rolls_back(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    repository.store_snapshots([make_snapshot(bid_price=Decimal("5.25"))], request=request)
    result = repository.store_snapshots(
        [make_snapshot(bid_price=Decimal("9.99"))], request=request
    )

    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        row = connection.execute(
            "SELECT bid_price FROM option_chain_snapshots"
        ).fetchone()
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()
    assert row[0] == Decimal("5.250000")  # unchanged
    assert run[2] == "failed"
    assert run[4] == "content_conflict"


def test_different_retrieved_at_stored_as_new_observation(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    repository.store_snapshots(
        [make_snapshot(retrieved_at="2026-09-02T15:30:05Z", bid_price=Decimal("5.25"))],
        request=request,
    )
    repository.store_snapshots(
        [make_snapshot(retrieved_at="2026-09-02T15:45:05Z", bid_price=Decimal("5.40"))],
        request=request,
    )

    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 2
    finally:
        connection.close()


# --- batch membership (option_chain_snapshot_batch_items) ---------------


def test_first_batch_retains_membership_after_identical_reingestion(tmp_path, isolated_env_file):
    """Re-ingesting an identical snapshot must not move it out of its first batch."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots([make_snapshot()], request=request)
    repository.store_snapshots([make_snapshot()], request=request)

    connection = read_only_connection(repository)
    try:
        first_batch = fetch_batch(connection, first.ingestion_run_id)
        first_membership = fetch_batch_item_symbols(connection, first.ingestion_run_id)
        first_membership_count = count_batch_item_rows(connection, first.ingestion_run_id)
    finally:
        connection.close()
    assert first_batch[9] == 1  # contract_count unchanged
    assert first_membership == {"SPY260918C00500000"}
    assert first_membership_count == 1


def test_second_batch_has_its_own_complete_membership(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    repository.store_snapshots([make_snapshot()], request=request)
    second = repository.store_snapshots([make_snapshot()], request=request)

    connection = read_only_connection(repository)
    try:
        second_batch = fetch_batch(connection, second.ingestion_run_id)
        second_membership = fetch_batch_item_symbols(connection, second.ingestion_run_id)
    finally:
        connection.close()
    assert second_batch[9] == 1  # contract_count
    assert second_membership == {"SPY260918C00500000"}


def test_immutable_snapshot_may_be_referenced_by_both_batches(tmp_path, isolated_env_file):
    """One immutable option_chain_snapshots row may be referenced by two batches' membership."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots([make_snapshot()], request=request)
    second = repository.store_snapshots([make_snapshot()], request=request)

    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 1  # exactly one immutable snapshot row
        assert count_batch_rows(connection) == 2  # two distinct batches
        run_ids_referencing = {
            r[0]
            for r in connection.execute(
                "SELECT ingestion_run_id FROM option_chain_snapshot_batch_items "
                "WHERE contract_symbol = 'SPY260918C00500000'"
            ).fetchall()
        }
    finally:
        connection.close()
    assert run_ids_referencing == {first.ingestion_run_id, second.ingestion_run_id}


def test_mixed_existing_and_new_snapshots_produce_truthful_membership_counts(
    tmp_path, isolated_env_file
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots(
        [make_snapshot(contract_symbol="SPY260918C00500000")], request=request
    )
    second = repository.store_snapshots(
        [
            make_snapshot(contract_symbol="SPY260918C00500000"),  # already exists
            make_snapshot(contract_symbol="SPY260918C00520000", strike_price=Decimal("520")),
        ],
        request=request,
    )

    assert second.inserted == 1
    assert second.existing_or_updated == 1

    connection = read_only_connection(repository)
    try:
        first_batch = fetch_batch(connection, first.ingestion_run_id)
        second_batch = fetch_batch(connection, second.ingestion_run_id)
        first_membership = fetch_batch_item_symbols(connection, first.ingestion_run_id)
        second_membership = fetch_batch_item_symbols(connection, second.ingestion_run_id)
        total_snapshot_rows = count_rows(connection)
    finally:
        connection.close()

    assert first_batch[9] == 1
    assert first_membership == {"SPY260918C00500000"}
    assert second_batch[9] == 2
    assert second_membership == {"SPY260918C00500000", "SPY260918C00520000"}
    assert total_snapshot_rows == 2  # the shared identity is stored exactly once


def test_conflict_rollback_leaves_no_new_batch_or_membership(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots(
        [make_snapshot(bid_price=Decimal("5.25"))], request=request
    )
    result = repository.store_snapshots(
        [make_snapshot(bid_price=Decimal("9.99"))], request=request
    )

    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_batch_rows(connection) == 1
        assert count_batch_item_rows(connection) == 1
        assert count_batch_item_rows(connection, first.ingestion_run_id) == 1
        assert count_batch_item_rows(connection, result.ingestion_run_id) == 0
    finally:
        connection.close()


def test_empty_batch_has_zero_membership_rows(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    result = repository.store_snapshots(
        [], request=request, retrieved_at="2026-09-02T15:30:05Z"
    )

    assert result.batch_outcome == "skipped_empty"
    connection = read_only_connection(repository)
    try:
        assert count_batch_rows(connection) == 1
        assert count_batch_item_rows(connection) == 0
        assert count_batch_item_rows(connection, result.ingestion_run_id) == 0
    finally:
        connection.close()


def test_batch_membership_count_matches_batch_contract_count(tmp_path, isolated_env_file):
    """Requirement: contract_count must equal the membership row count for a non-empty batch."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    result = repository.store_snapshots(
        [
            make_snapshot(contract_symbol="SPY260918C00500000"),
            make_snapshot(contract_symbol="SPY260918C00520000", strike_price=Decimal("520")),
            make_snapshot(contract_symbol="SPY260918C00540000", strike_price=Decimal("540")),
        ],
        request=request,
    )

    connection = read_only_connection(repository)
    try:
        batch = fetch_batch(connection, result.ingestion_run_id)
        membership_count = count_batch_item_rows(connection, result.ingestion_run_id)
    finally:
        connection.close()
    assert batch[9] == 3  # contract_count
    assert membership_count == 3
    assert batch[9] == membership_count


def test_feed_separation_in_batch_membership(tmp_path, isolated_env_file):
    """OPRA and indicative membership rows for the same contract/instant stay distinct."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    opra_result = repository.store_snapshots(
        [make_snapshot(feed="opra")], request=make_request(feed="opra")
    )
    indicative_result = repository.store_snapshots(
        [make_snapshot(feed="indicative")], request=make_request(feed="indicative")
    )

    connection = read_only_connection(repository)
    try:
        feeds = {
            r[0]
            for r in connection.execute(
                "SELECT feed FROM option_chain_snapshot_batch_items"
            ).fetchall()
        }
        opra_membership = count_batch_item_rows(connection, opra_result.ingestion_run_id)
        indicative_membership = count_batch_item_rows(
            connection, indicative_result.ingestion_run_id
        )
    finally:
        connection.close()
    assert feeds == {"opra", "indicative"}
    assert opra_membership == 1
    assert indicative_membership == 1


# --- run-level batch provenance -----------------------------------------


def test_nonempty_batch_persists_batch_row_referencing_run(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    result = repository.store_snapshots([make_snapshot()], request=request)

    assert result.batch_outcome == "succeeded"
    connection = read_only_connection(repository)
    try:
        assert count_batch_rows(connection) == 1
        batch = fetch_batch(connection, result.ingestion_run_id)
        snapshot_run_id = connection.execute(
            "SELECT ingestion_run_id FROM option_chain_snapshots"
        ).fetchone()[0]
    finally:
        connection.close()
    (
        provider, underlying, requested_feed, exp_gte, exp_lte, strike_gte, strike_lte,
        requested_type, retrieved_at, contract_count, outcome,
    ) = batch
    assert provider == "alpaca"
    assert underlying == "SPY"
    assert requested_feed == "opra"
    assert str(exp_gte) == "2026-09-01"
    assert str(exp_lte) == "2026-09-30"
    assert strike_gte == Decimal("400.000000")
    assert strike_lte == Decimal("600.000000")
    assert requested_type is None
    assert retrieved_at is not None
    assert contract_count == 1
    assert outcome == "succeeded"
    # the snapshot row references the same run/batch id
    assert snapshot_run_id == result.ingestion_run_id


def test_empty_batch_persists_batch_row_with_zero_contract_count(tmp_path, isolated_env_file):
    """A successful empty chain persists exactly one batch row and zero snapshot rows."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    retrieved_at = "2026-09-02T15:30:05Z"
    result = repository.store_snapshots([], request=request, retrieved_at=retrieved_at)

    assert result.received == 0
    assert result.inserted == 0
    assert result.ingestion_run_status == "succeeded"
    assert result.batch_outcome == "skipped_empty"

    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 0
        assert count_batch_rows(connection) == 1
        batch = fetch_batch(connection, result.ingestion_run_id)
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()
    assert batch[9] == 0  # contract_count
    assert batch[10] == "skipped_empty"  # outcome
    assert run[2] == "succeeded"
    assert run[3] == 0  # records_received


def test_batch_and_snapshot_rows_written_atomically_on_conflict(tmp_path, isolated_env_file):
    """A mid-batch storage conflict leaves neither a batch row nor a snapshot row."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    repository.store_snapshots([make_snapshot(bid_price=Decimal("5.25"))], request=request)
    first_batch_count = None
    connection = read_only_connection(repository)
    try:
        first_batch_count = count_batch_rows(connection)
    finally:
        connection.close()
    assert first_batch_count == 1

    result = repository.store_snapshots(
        [make_snapshot(bid_price=Decimal("9.99"))], request=request
    )
    assert result.ingestion_run_status == "failed"
    assert result.batch_outcome == "failed"

    connection = read_only_connection(repository)
    try:
        # still only the first run's batch row -- the failed run's rolled back
        assert count_batch_rows(connection) == 1
        assert connection.execute(
            "SELECT count(*) FROM option_chain_snapshot_batches WHERE ingestion_run_id = ?",
            [result.ingestion_run_id],
        ).fetchone()[0] == 0
        assert count_rows(connection) == 1  # only the original snapshot row
    finally:
        connection.close()


def test_empty_batch_validation_failure_writes_no_batch_row(tmp_path, isolated_env_file):
    """An invalid request still fails before any write, even for an empty batch."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [], request={"feed": "opra"}, retrieved_at="2026-09-02T15:30:05Z"
        )
    connection = read_only_connection(repository)
    try:
        assert count_batch_rows(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_deterministic_batch_provenance_across_repeated_retrievals(tmp_path, isolated_env_file):
    """Two stores of the same bounded request produce two batch rows with identical bounds."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    first = repository.store_snapshots(
        [make_snapshot(retrieved_at="2026-09-02T15:30:05Z")], request=request
    )
    second = repository.store_snapshots(
        [make_snapshot(retrieved_at="2026-09-02T15:45:05Z", bid_price=Decimal("5.40"))],
        request=request,
    )

    connection = read_only_connection(repository)
    try:
        assert count_batch_rows(connection) == 2
        first_batch = fetch_batch(connection, first.ingestion_run_id)
        second_batch = fetch_batch(connection, second.ingestion_run_id)
    finally:
        connection.close()
    # same requested bounds, different retrieval instants and run ids
    assert first_batch[2:8] == second_batch[2:8]  # requested_feed .. requested_option_type
    assert first_batch[8] != second_batch[8]  # retrieved_at
    assert first.ingestion_run_id != second.ingestion_run_id


def test_retrieved_at_must_match_every_item(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(retrieved_at="2026-09-02T15:30:05Z")],
            request=make_request(),
            retrieved_at="2026-09-02T16:00:00Z",
        )


def test_items_with_disagreeing_retrieved_at_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [
                make_snapshot(
                    contract_symbol="SPY260918C00500000", retrieved_at="2026-09-02T15:30:05Z"
                ),
                make_snapshot(
                    contract_symbol="SPY260918C00520000", strike_price=Decimal("520"),
                    retrieved_at="2026-09-02T15:45:05Z",
                ),
            ],
            request=make_request(),
        )


# --- feed separation ----------------------------------------------------


def test_opra_and_indicative_never_merged(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_snapshots(
        [make_snapshot(feed="opra", bid_price=Decimal("5.25"))],
        request=make_request(feed="opra"),
    )
    repository.store_snapshots(
        [make_snapshot(feed="indicative", bid_price=Decimal("5.10"))],
        request=make_request(feed="indicative"),
    )

    connection = read_only_connection(repository)
    try:
        feeds = [
            r[0]
            for r in connection.execute(
                "SELECT feed FROM option_chain_snapshots ORDER BY feed"
            ).fetchall()
        ]
    finally:
        connection.close()
    assert feeds == ["indicative", "opra"]


def test_item_feed_must_match_request_feed(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(feed="indicative")], request=make_request(feed="opra")
        )


# --- missing optional fields stay null --------------------------------


def test_missing_optional_fields_stored_as_null(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    snap = make_snapshot(
        quote_timestamp=None, bid_price=None, bid_size=None, ask_price=None, ask_size=None,
        trade_timestamp=None, trade_price=None, trade_size=None, implied_volatility=None,
        delta=None, gamma=None, theta=None, vega=None, rho=None,
    )
    repository.store_snapshots([snap], request=make_request())

    connection = read_only_connection(repository)
    try:
        row = connection.execute(
            "SELECT quote_timestamp, bid_price, bid_size, implied_volatility, delta, "
            "gamma, theta, vega, rho FROM option_chain_snapshots"
        ).fetchone()
    finally:
        connection.close()
    assert all(v is None for v in row)


# --- validation rejections -----------------------------------------


def test_empty_batch_without_retrieved_at_rejected(tmp_path, isolated_env_file):
    """An empty batch needs an explicit retrieved_at -- there is no item to derive it from."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([], request=make_request())


def test_non_snapshot_element_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([{"not": "a snapshot"}], request=make_request())


def test_unnormalized_request_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request={"feed": "opra"})


# --- request boundary hardening: forged OptionChainRequest ----------------


def forged_request(**overrides) -> OptionChainRequest:
    """Build an OptionChainRequest directly, bypassing normalize_option_chain_request.

    Used only to prove that ``store_snapshots`` re-validates every field
    itself and cannot be bypassed by a hand-constructed request.
    """
    kwargs = dict(
        underlying="SPY",
        feed="opra",
        expiration_date_gte="2026-09-01",
        expiration_date_lte="2026-09-30",
        strike_price_gte=Decimal("400"),
        strike_price_lte=Decimal("600"),
        option_type=None,
        limit=1000,
        max_pages=1,
        max_total_contracts=1000,
    )
    kwargs.update(overrides)
    return OptionChainRequest(**kwargs)


def assert_nothing_written(repository) -> None:
    connection = read_only_connection(repository)
    try:
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
        assert count_batch_rows(connection) == 0
        assert count_batch_item_rows(connection) == 0
        assert count_rows(connection) == 0
    finally:
        connection.close()


def test_forged_request_above_expiration_span_ceiling_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    gte = datetime(2026, 9, 1)
    lte = gte + timedelta(days=MAX_EXPIRATION_RANGE_DAYS + 1)
    bad = forged_request(
        expiration_date_gte=gte.strftime("%Y-%m-%d"),
        expiration_date_lte=lte.strftime("%Y-%m-%d"),
    )
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request=bad)
    assert_nothing_written(repository)


def test_forged_request_above_strike_width_ceiling_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    bad = forged_request(
        strike_price_gte=Decimal("400"),
        strike_price_lte=Decimal("400") + MAX_STRIKE_RANGE_WIDTH + Decimal("1"),
    )
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request=bad)
    assert_nothing_written(repository)


def test_forged_request_above_max_pages_ceiling_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    bad = forged_request(max_pages=MAX_PAGES + 1)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request=bad)
    assert_nothing_written(repository)


def test_forged_request_above_max_total_contracts_ceiling_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    bad = forged_request(max_total_contracts=MAX_TOTAL_CONTRACTS + 1)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request=bad)
    assert_nothing_written(repository)


def test_forged_request_above_per_page_limit_ceiling_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    bad = forged_request(limit=MAX_LIMIT + 1)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request=bad)
    assert_nothing_written(repository)


def test_forged_request_at_every_ceiling_boundary_is_accepted(tmp_path, isolated_env_file):
    """The ceilings themselves are valid, inclusive bounds -- only exceeding them fails."""
    repository = initialized_repository(tmp_path, isolated_env_file)
    gte = datetime(2026, 9, 1)
    lte = gte + timedelta(days=MAX_EXPIRATION_RANGE_DAYS)
    exactly_at_ceiling = forged_request(
        expiration_date_gte=gte.strftime("%Y-%m-%d"),
        expiration_date_lte=lte.strftime("%Y-%m-%d"),  # exactly MAX_EXPIRATION_RANGE_DAYS
        strike_price_gte=Decimal("400"),
        strike_price_lte=Decimal("400") + MAX_STRIKE_RANGE_WIDTH,
        max_pages=MAX_PAGES,
        max_total_contracts=MAX_TOTAL_CONTRACTS,
        limit=MAX_LIMIT,
    )
    result = repository.store_snapshots([make_snapshot()], request=exactly_at_ceiling)
    assert result.ingestion_run_status == "succeeded"


def test_forged_request_with_reassembled_but_unequal_fields_rejected(tmp_path, isolated_env_file):
    """A request whose fields are individually legal but disagree with its own canonical form fails.

    Here ``limit`` is a bool, which ``normalize_limit`` rejects outright even
    though ``isinstance(True, int)`` is true in Python -- this exercises the
    canonical-equality re-validation path, not just the ceiling checks.
    """
    repository = initialized_repository(tmp_path, isolated_env_file)
    bad = forged_request(limit=True)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([make_snapshot()], request=bad)
    assert_nothing_written(repository)


def test_non_spy_underlying_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    bad = make_snapshot()
    object.__setattr__(bad, "underlying", "QQQ")
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([bad], request=make_request())


def test_symbol_field_disagreement_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    # symbol says strike 500, field says 505
    bad = make_snapshot(strike_price=Decimal("505"))
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots([bad], request=make_request())


def test_malformed_contract_symbol_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(contract_symbol="NOT-AN-OCC-SYMBOL")], request=make_request()
        )


def test_negative_bid_price_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(bid_price=Decimal("-1"))], request=make_request()
        )


def test_negative_gamma_rejected_but_negative_theta_ok(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(gamma=Decimal("-0.01"))], request=make_request()
        )
    result = repository.store_snapshots(
        [make_snapshot(theta=Decimal("-0.5"), delta=Decimal("-0.3"))], request=make_request()
    )
    assert result.ingestion_run_status == "succeeded"


def test_over_precise_greek_rejected_zero_writes(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(delta=Decimal("0.12345678901"))], request=make_request()
        )
    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 0
        assert count_batch_rows(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_duplicate_contract_in_batch_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(), make_snapshot()], request=make_request()
        )


def test_strike_outside_requested_range_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot()], request=make_request(strike_price_gte=Decimal("510"),
                                                    strike_price_lte=Decimal("520"))
        )


def test_naive_timestamp_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(quote_timestamp="2026-09-02T15:30:00")], request=make_request()
        )


# --- partial-write safety ---------------------------------------------


def test_validation_failure_writes_nothing(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(OptionChainSnapshotStorageValidationError):
        repository.store_snapshots(
            [make_snapshot(contract_symbol="SPY260918C00500000"),
             make_snapshot(contract_symbol="BADSYMBOL")],
            request=make_request(),
        )
    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 0
        assert count_batch_rows(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_conflict_mid_batch_rolls_back_all(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    request = make_request()
    repository.store_snapshots(
        [make_snapshot(contract_symbol="SPY260918C00500000", bid_price=Decimal("5.25"))],
        request=request,
    )
    result = repository.store_snapshots(
        [
            make_snapshot(contract_symbol="SPY260918C00520000", strike_price=Decimal("520")),
            make_snapshot(contract_symbol="SPY260918C00500000", bid_price=Decimal("1.11")),
        ],
        request=request,
    )
    assert result.ingestion_run_status == "failed"
    assert result.batch_outcome == "failed"
    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 1  # only the original
        assert count_batch_rows(connection) == 1  # only the first (successful) run's batch
    finally:
        connection.close()


# --- sanitization ---------------------------------------------------


def test_result_repr_never_contains_greek_or_quote_values(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_snapshots(
        [make_snapshot(delta=Decimal("0.987654"))], request=make_request()
    )
    assert "0.987654" not in repr(result)


def test_validation_error_never_contains_symbol_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret = "SECRETSYMBOL999"
    with pytest.raises(OptionChainSnapshotStorageValidationError) as exc_info:
        repository.store_snapshots(
            [make_snapshot(contract_symbol=secret)], request=make_request()
        )
    assert secret not in str(exc_info.value)


def test_repository_module_has_no_network_dependency():
    import market_intelligence.storage.option_chain_snapshot_repository as module

    assert not hasattr(module, "httpx")


def test_connection_failure_raises_sanitized_error(tmp_path, isolated_env_file, monkeypatch):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-CONNECT-DETAIL"

    def fake_connect(*args, **kwargs):
        raise RuntimeError(f"boom {secret_marker}")

    with monkeypatch.context() as m:
        m.setattr(duckdb, "connect", fake_connect)
        with pytest.raises(OptionChainSnapshotStorageError) as exc_info:
            repository.store_snapshots([make_snapshot()], request=make_request())

    assert secret_marker not in str(exc_info.value)
    assert not isinstance(exc_info.value, OptionChainSnapshotStorageValidationError)


def test_failed_status_recording_failure_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)

    def always_fail(connection, *, run_id, status, records_received, error_category):
        raise RuntimeError("simulated failure with SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr(
        OptionChainSnapshotRepository, "_complete_run", staticmethod(always_fail)
    )
    with pytest.raises(OptionChainSnapshotStorageError) as exc_info:
        repository.store_snapshots([make_snapshot()], request=make_request())
    assert "SECRET-INTERNAL-DETAIL" not in str(exc_info.value)

    connection = read_only_connection(repository)
    try:
        assert count_rows(connection) == 0
    finally:
        connection.close()


def test_uses_uuid_for_run_id(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_snapshots([make_snapshot()], request=make_request())
    uuid.UUID(result.ingestion_run_id)  # does not raise
