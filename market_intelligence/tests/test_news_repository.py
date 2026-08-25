"""Tests for market_intelligence.storage.news_repository.

These tests never touch the real repository database or any network/API.
Every repository under test is pointed at an isolated temporary directory
via a Settings whose project_data_path is tmp_path, mirroring
market_intelligence/tests/test_database.py. NewsItem inputs are constructed
directly here -- never fetched live.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import Settings
from market_intelligence.data_connectors.alpaca_news import NewsItem
from market_intelligence.storage.database import DuckDBManager
from market_intelligence.storage.news_repository import (
    NewsArticleRepository,
    NewsStorageError,
    NewsStorageValidationError,
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


def initialized_repository(tmp_path: Path, isolated_env_file: Path) -> NewsArticleRepository:
    settings = isolated_settings(tmp_path, isolated_env_file)
    DuckDBManager(settings=settings).initialize()
    return NewsArticleRepository(settings=settings)


def news_item(
    *,
    provider: str = "alpaca",
    provider_article_id: str = "1",
    headline: str = "Fed signals rate pause",
    source: str = "benzinga",
    url: str = "https://example.com/news/1",
    summary: str | None = "A summary.",
    created_at: str | None = "2026-08-20T12:00:00Z",
    updated_at: str | None = "2026-08-20T12:05:00Z",
    related_symbols: tuple[str, ...] = ("SPY",),
    retrieved_at: str = "2026-08-20T12:10:00Z",
) -> NewsItem:
    return NewsItem(
        provider=provider,
        provider_article_id=provider_article_id,
        headline=headline,
        source=source,
        url=url,
        summary=summary,
        created_at=created_at,
        updated_at=updated_at,
        related_symbols=related_symbols,
        retrieved_at=retrieved_at,
    )


def fetch_article(connection: duckdb.DuckDBPyConnection, provider: str, provider_article_id: str):
    return connection.execute(
        "SELECT provider, provider_article_id, headline, source, article_url, summary, "
        "created_at, updated_at, related_symbols, retrieved_at, first_ingested_at, "
        "last_seen_at, ingestion_run_id FROM news_articles "
        "WHERE provider = ? AND provider_article_id = ?",
        [provider, provider_article_id],
    ).fetchone()


def count_articles(connection: duckdb.DuckDBPyConnection) -> int:
    return connection.execute("SELECT count(*) FROM news_articles").fetchone()[0]


def fetch_run(connection: duckdb.DuckDBPyConnection, run_id: str):
    return connection.execute(
        "SELECT provider, dataset_name, status, records_received, error_category, "
        "code_version, schema_version FROM ingestion_runs WHERE run_id = ?",
        [run_id],
    ).fetchone()


def read_only_connection(repository: NewsArticleRepository) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(repository.database_path), read_only=True)


# --- successful insertion -----------------------------------------------------


def test_store_news_items_inserts_unseen_article(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_news_items([news_item()], provider="alpaca")

    assert result.received == 1
    assert result.inserted == 1
    assert result.updated == 0
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 1
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()
    assert row is not None
    assert row[2] == "Fed signals rate pause"


# --- duplicate ingestion (no duplicate rows) -----------------------------------


def test_store_news_items_duplicate_ingestion_no_duplicate_rows(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items([news_item()], provider="alpaca")
    result = repository.store_news_items([news_item()], provider="alpaca")

    assert result.received == 1
    assert result.inserted == 0
    assert result.updated == 1
    assert result.failed == 0
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 1
    finally:
        connection.close()


def test_duplicate_ingestion_refreshes_retrieved_at(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items([news_item(retrieved_at="2026-08-20T12:00:00Z")], provider="alpaca")
    repository.store_news_items([news_item(retrieved_at="2026-08-20T18:00:00Z")], provider="alpaca")

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()

    assert row[9].isoformat().startswith("2026-08-20T18:00:00")


# --- related symbols determinism ------------------------------------------------


def test_related_symbols_stored_deterministically_regardless_of_input_order(
    tmp_path, isolated_env_file
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items(
        [news_item(related_symbols=("QQQ", "spy", "SPY"))],
        provider="alpaca",
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()

    assert row[8] == ["QQQ", "SPY"]


# --- null optional fields -------------------------------------------------------


def test_null_optional_fields_stored_as_null(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items(
        [news_item(summary=None, created_at=None, updated_at=None, related_symbols=())],
        provider="alpaca",
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()

    assert row[5] is None  # summary
    assert row[6] is None  # created_at
    assert row[7] is None  # updated_at
    assert row[8] == []  # related_symbols


# --- timestamp preservation ------------------------------------------------------


def test_first_ingested_at_preserved_across_updates(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items([news_item()], provider="alpaca")

    connection = read_only_connection(repository)
    try:
        first_ingested_before = fetch_article(connection, "alpaca", "1")[10]
    finally:
        connection.close()

    repository.store_news_items(
        [news_item(retrieved_at="2026-08-21T00:00:00Z")], provider="alpaca"
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()

    assert row[10] == first_ingested_before  # first_ingested_at unchanged
    assert row[11] != first_ingested_before  # last_seen_at refreshed


def test_created_at_and_updated_at_preserved_distinctly(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items(
        [news_item(created_at="2026-08-20T12:00:00Z", updated_at="2026-08-20T12:05:00Z")],
        provider="alpaca",
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()

    assert row[6].isoformat().startswith("2026-08-20T12:00:00")
    assert row[7].isoformat().startswith("2026-08-20T12:05:00")
    assert row[6] != row[7]
    assert row[9] != row[6]
    assert row[9] != row[7]


# --- mixed-symbol articles -------------------------------------------------------


def test_mixed_symbol_articles_stored_independently(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_news_items(
        [
            news_item(provider_article_id="1", related_symbols=("SPY",)),
            news_item(provider_article_id="2", related_symbols=("QQQ", "IWM")),
            news_item(provider_article_id="3", related_symbols=()),
        ],
        provider="alpaca",
    )

    assert result.inserted == 3
    assert result.ingestion_run_status == "succeeded"

    connection = read_only_connection(repository)
    try:
        rows = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT provider_article_id, related_symbols FROM news_articles "
                "ORDER BY provider_article_id"
            ).fetchall()
        }
    finally:
        connection.close()

    assert rows["1"] == ["SPY"]
    assert rows["2"] == ["IWM", "QQQ"]
    assert rows["3"] == []


# --- conflicting stable content ---------------------------------------------------


def test_conflicting_stable_content_does_not_overwrite_existing_row(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items([news_item(headline="Original headline")], provider="alpaca")

    result = repository.store_news_items(
        [news_item(headline="Conflicting headline")], provider="alpaca"
    )

    assert result.received == 1
    assert result.inserted == 0
    assert result.updated == 0
    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert row[2] == "Original headline"  # unchanged
    assert run[2] == "failed"
    assert run[4] == "content_conflict"


def test_conflicting_source_is_also_detected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items([news_item(source="benzinga")], provider="alpaca")

    result = repository.store_news_items([news_item(source="reuters")], provider="alpaca")

    assert result.failed == 1
    assert result.ingestion_run_status == "failed"


# --- transactional rollback --------------------------------------------------------


def test_conflict_rolls_back_entire_batch(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items(
        [news_item(provider_article_id="1", headline="Original headline")], provider="alpaca"
    )

    result = repository.store_news_items(
        [
            news_item(provider_article_id="2", headline="Brand new valid article"),
            news_item(provider_article_id="1", headline="Conflicting headline"),
            news_item(provider_article_id="3", headline="Another brand new article"),
        ],
        provider="alpaca",
    )

    assert result.received == 3
    assert result.inserted == 0
    assert result.updated == 0
    assert result.failed == 3
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 1  # only the original pre-existing row
        assert fetch_article(connection, "alpaca", "2") is None
        assert fetch_article(connection, "alpaca", "3") is None
    finally:
        connection.close()


# --- ingestion_runs success metadata ------------------------------------------------


def test_ingestion_run_success_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_news_items(
        [news_item(provider_article_id="1"), news_item(provider_article_id="2")],
        provider="alpaca",
        dataset_name="news",
    )

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[0] == "alpaca"
    assert run[1] == "news"
    assert run[2] == "succeeded"
    assert run[3] == 2
    assert run[4] is None
    assert run[5] is not None  # code_version
    assert run[6] == "0008"  # schema_version


# --- ingestion_runs failure metadata ------------------------------------------------


def test_ingestion_run_failure_metadata(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items([news_item(headline="Original headline")], provider="alpaca")
    result = repository.store_news_items(
        [news_item(headline="Conflicting headline")], provider="alpaca"
    )

    connection = read_only_connection(repository)
    try:
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "failed"
    assert run[3] == 1
    assert run[4] == "content_conflict"


# --- validation ------------------------------------------------------------------


def test_store_news_items_rejects_non_newsitem_elements(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items([{"not": "a NewsItem"}], provider="alpaca")

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


def test_store_news_items_rejects_provider_mismatch(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items([news_item(provider="alpaca")], provider="other")


def test_store_news_items_rejects_blank_headline(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items([news_item(headline="   ")], provider="alpaca")


def test_store_news_items_rejects_non_string_related_symbol(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items(
            [news_item(related_symbols=("SPY", 123))],  # type: ignore[arg-type]
            provider="alpaca",
        )


# --- no secrets or article content in errors/status repr ---------------------------


def test_result_repr_never_contains_article_content(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_headline = "SECRET-HEADLINE-CONTENT"
    result = repository.store_news_items(
        [news_item(headline=secret_headline)], provider="alpaca"
    )

    assert secret_headline not in repr(result)


def test_conflict_result_repr_never_contains_article_content(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_original = "SECRET-ORIGINAL-HEADLINE"
    secret_conflict = "SECRET-CONFLICTING-HEADLINE"
    repository.store_news_items([news_item(headline=secret_original)], provider="alpaca")
    result = repository.store_news_items([news_item(headline=secret_conflict)], provider="alpaca")

    assert secret_original not in repr(result)
    assert secret_conflict not in repr(result)


def test_validation_error_never_contains_article_content(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_headline = "SECRET-HEADLINE-CONTENT"
    with pytest.raises(NewsStorageValidationError) as exc_info:
        repository.store_news_items([news_item(headline=secret_headline, provider_article_id="")])

    assert secret_headline not in str(exc_info.value)


# --- module never imports a network client ------------------------------------------


def test_news_repository_module_has_no_network_dependency():
    import market_intelligence.storage.news_repository as module

    assert not hasattr(module, "httpx")


# --- successful atomic storage ---------------------------------------------------


def test_successful_store_is_atomic_with_succeeded_run_status(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_news_items(
        [news_item(provider_article_id="1"), news_item(provider_article_id="2")],
        provider="alpaca",
    )

    assert result.ingestion_run_status == "succeeded"
    assert result.inserted == 2
    assert result.failed == 0

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 2
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "succeeded"


# --- rollback when the final succeeded-status update fails -----------------------


def test_success_status_update_failure_rolls_back_articles_and_marks_run_failed(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)
    original_complete_run = NewsArticleRepository._complete_run

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

    monkeypatch.setattr(NewsArticleRepository, "_complete_run", staticmethod(fake_complete_run))

    result = repository.store_news_items([news_item()], provider="alpaca")

    assert result.received == 1
    assert result.inserted == 0
    assert result.updated == 0
    assert result.failed == 1
    assert result.ingestion_run_status == "failed"

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 0  # article write rolled back
        run = fetch_run(connection, result.ingestion_run_id)
    finally:
        connection.close()

    assert run[2] == "failed"
    assert run[4] == "storage_error"


# --- failure-status recording itself failing --------------------------------------


def test_failed_status_recording_failure_raises_sanitized_error(
    tmp_path, isolated_env_file, monkeypatch
):
    repository = initialized_repository(tmp_path, isolated_env_file)

    def always_fail_complete_run(connection, *, run_id, status, records_received, error_category):
        raise RuntimeError("simulated failure with SECRET-INTERNAL-DETAIL")

    monkeypatch.setattr(
        NewsArticleRepository, "_complete_run", staticmethod(always_fail_complete_run)
    )

    with pytest.raises(NewsStorageError) as exc_info:
        repository.store_news_items([news_item()], provider="alpaca")

    assert "SECRET-INTERNAL-DETAIL" not in str(exc_info.value)
    assert not isinstance(exc_info.value, NewsStorageValidationError)

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 0  # article write rolled back
        statuses = [
            row[0] for row in connection.execute("SELECT status FROM ingestion_runs").fetchall()
        ]
    finally:
        connection.close()

    assert statuses == ["running"]  # neither status update ever committed


# --- valid UTC / offset timestamps normalize correctly ----------------------------


def test_valid_offset_and_lowercase_z_timestamps_normalized_to_utc(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    repository.store_news_items(
        [
            news_item(
                created_at="2026-08-20T08:00:00-04:00",
                updated_at="2026-08-20T12:05:00+00:00",
                retrieved_at="2026-08-20t12:10:00z",
            )
        ],
        provider="alpaca",
    )

    connection = read_only_connection(repository)
    try:
        row = fetch_article(connection, "alpaca", "1")
    finally:
        connection.close()

    assert row[6].isoformat().startswith("2026-08-20T12:00:00")  # created_at -> UTC
    assert row[7].isoformat().startswith("2026-08-20T12:05:00")  # updated_at -> UTC
    assert row[9].isoformat().startswith("2026-08-20T12:10:00")  # retrieved_at -> UTC


# --- rejection of ambiguous timestamps ---------------------------------------------


_AMBIGUOUS_TIMESTAMPS = [
    pytest.param("2026-08-20T12:00:00", id="naive"),
    pytest.param("2026-08-20", id="date-only"),
    pytest.param("not-a-timestamp", id="malformed"),
    pytest.param("   ", id="blank"),
    pytest.param(12345, id="non-string"),
]


@pytest.mark.parametrize("field_name", ["created_at", "updated_at", "retrieved_at"])
@pytest.mark.parametrize("bad_value", _AMBIGUOUS_TIMESTAMPS)
def test_ambiguous_timestamps_rejected(tmp_path, isolated_env_file, field_name, bad_value):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items([news_item(**{field_name: bad_value})], provider="alpaca")


def test_retrieved_at_none_rejected(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items([news_item(retrieved_at=None)], provider="alpaca")


def test_created_at_and_updated_at_none_accepted(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    result = repository.store_news_items(
        [news_item(created_at=None, updated_at=None)], provider="alpaca"
    )

    assert result.ingestion_run_status == "succeeded"


# --- zero writes/runs after a timestamp validation failure -------------------------


def test_zero_writes_after_timestamp_validation_failure(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    with pytest.raises(NewsStorageValidationError):
        repository.store_news_items(
            [news_item(created_at="2026-08-20T12:00:00")],  # naive, no offset
            provider="alpaca",
        )

    connection = read_only_connection(repository)
    try:
        assert count_articles(connection) == 0
        assert connection.execute("SELECT count(*) FROM ingestion_runs").fetchone()[0] == 0
    finally:
        connection.close()


# --- sanitized timestamp validation errors ------------------------------------------


def test_timestamp_validation_error_never_echoes_raw_value(tmp_path, isolated_env_file):
    repository = initialized_repository(tmp_path, isolated_env_file)
    secret_marker = "SECRET-TIMESTAMP-VALUE"
    with pytest.raises(NewsStorageValidationError) as exc_info:
        repository.store_news_items(
            [news_item(retrieved_at=f"not-a-timestamp-{secret_marker}")],
            provider="alpaca",
        )

    assert secret_marker not in str(exc_info.value)
