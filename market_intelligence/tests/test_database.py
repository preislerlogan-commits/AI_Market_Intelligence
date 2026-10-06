"""Tests for market_intelligence.storage.database.

These tests never touch the real repository database or any network/API.
Every manager under test is pointed at an isolated temporary directory by
constructing a ``Settings`` whose ``project_data_path`` is ``tmp_path`` (or a
subdirectory of it), mirroring how the connector tests isolate ``Settings``
via an env file that does not exist.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest

from market_intelligence.config.settings import REPO_ROOT, Settings
from market_intelligence.storage.database import (
    DATABASE_FILENAME,
    DatabasePathSafetyError,
    DuckDBManager,
    MigrationError,
    default_database_path,
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
    """Settings scoped to an isolated project data directory under tmp_path."""
    return Settings(project_data_path=tmp_path / "data", _env_file=isolated_env_file)


def write_migration(migrations_dir: Path, filename: str, sql: str) -> None:
    migrations_dir.mkdir(parents=True, exist_ok=True)
    (migrations_dir / filename).write_text(sql, encoding="utf-8")


def table_names(connection: duckdb.DuckDBPyConnection) -> set[str]:
    rows = connection.execute("SELECT table_name FROM information_schema.tables").fetchall()
    return {row[0] for row in rows}


MIGRATION_0001 = (
    "0001_create_schema_migrations.sql",
    "CREATE TABLE schema_migrations (\n"
    "    version VARCHAR PRIMARY KEY,\n"
    "    filename VARCHAR NOT NULL,\n"
    "    checksum VARCHAR NOT NULL,\n"
    "    applied_at_utc TIMESTAMP NOT NULL\n"
    ");\n",
)

MIGRATION_0002 = (
    "0002_create_ingestion_runs.sql",
    "CREATE TABLE ingestion_runs (\n"
    "    run_id VARCHAR PRIMARY KEY,\n"
    "    provider VARCHAR NOT NULL,\n"
    "    dataset_name VARCHAR NOT NULL,\n"
    "    started_at_utc TIMESTAMP NOT NULL,\n"
    "    completed_at_utc TIMESTAMP,\n"
    "    status VARCHAR NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),\n"
    "    records_received BIGINT,\n"
    "    error_category VARCHAR,\n"
    "    code_version VARCHAR NOT NULL\n"
    ");\n",
)

MIGRATION_0003 = (
    "0003_add_schema_version_to_ingestion_runs.sql",
    "ALTER TABLE ingestion_runs ADD COLUMN schema_version VARCHAR;\n",
)


def real_migrations_manager(tmp_path: Path, isolated_env_file: Path) -> DuckDBManager:
    """A manager wired to the project's real (non-test-fixture) migration files."""
    return DuckDBManager(settings=isolated_settings(tmp_path, isolated_env_file))


# --- first initialization ---------------------------------------------------


def test_initialize_creates_database_file(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    assert result.database_path.exists()
    assert result.database_path.name == DATABASE_FILENAME
    assert result.applied_migration_count == 10
    assert result.schema_version == "0010"


def test_initialize_creates_required_tables_and_columns(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        tables = table_names(connection)
        assert "schema_migrations" in tables
        assert "ingestion_runs" in tables

        migration_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'schema_migrations'"
            ).fetchall()
        }
        assert migration_columns == {"version", "filename", "checksum", "applied_at_utc"}

        run_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ingestion_runs'"
            ).fetchall()
        }
        assert run_columns == {
            "run_id",
            "provider",
            "dataset_name",
            "started_at_utc",
            "completed_at_utc",
            "status",
            "records_received",
            "error_category",
            "code_version",
            "schema_version",
        }

        assert "news_articles" in tables
        news_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'news_articles'"
            ).fetchall()
        }
        assert news_columns == {
            "provider",
            "provider_article_id",
            "headline",
            "source",
            "article_url",
            "summary",
            "created_at",
            "updated_at",
            "related_symbols",
            "retrieved_at",
            "first_ingested_at",
            "last_seen_at",
            "ingestion_run_id",
        }

        assert "market_bars" in tables
        bars_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'market_bars'"
            ).fetchall()
        }
        assert bars_columns == {
            "provider",
            "symbol",
            "timeframe",
            "feed",
            "adjustment",
            "currency",
            "bar_timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "trade_count",
            "vwap",
            "retrieved_at",
            "first_ingested_at",
            "last_seen_at",
            "ingestion_run_id",
        }

        assert "macro_observations" in tables
        macro_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'macro_observations'"
            ).fetchall()
        }
        assert macro_columns == {
            "provider",
            "series_id",
            "observation_date",
            "realtime_start",
            "realtime_end",
            "value",
            "is_missing",
            "retrieved_at",
            "first_ingested_at",
            "last_seen_at",
            "ingestion_run_id",
        }

        assert "macro_series_metadata" in tables
        metadata_columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'macro_series_metadata'"
            ).fetchall()
        }
        assert metadata_columns == {
            "provider",
            "series_id",
            "title",
            "observation_start",
            "observation_end",
            "frequency",
            "frequency_short",
            "units",
            "units_short",
            "seasonal_adjustment",
            "seasonal_adjustment_short",
            "last_updated",
            "popularity",
            "notes",
            "retrieved_at_utc",
            "first_ingested_at",
            "last_seen_at",
            "ingestion_run_id",
        }
    finally:
        connection.close()


def test_initialize_creates_parent_directory(tmp_path, isolated_env_file):
    settings = Settings(project_data_path=tmp_path / "nested" / "data", _env_file=isolated_env_file)
    manager = DuckDBManager(settings=settings)

    assert not settings.project_data_path.exists()
    manager.initialize()
    assert settings.project_data_path.exists()


# --- repeated initialization (idempotency) ----------------------------------


def test_repeated_initialize_applies_zero_new_migrations(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    first = manager.initialize()
    second = manager.initialize()

    assert first.applied_migration_count == 10
    assert second.applied_migration_count == 0
    assert second.schema_version == first.schema_version


def test_repeated_initialize_does_not_duplicate_rows(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        count = connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    finally:
        connection.close()
    assert count == 10


# --- migration order ---------------------------------------------------------


def test_migrations_apply_in_ascending_order_and_respect_dependencies(tmp_path, isolated_env_file):
    """0002 depends on a table created by 0001; wrong order would fail."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(
        migrations_dir,
        "0002_add_ingestion_runs_referencing_schema_migrations.sql",
        "CREATE TABLE ingestion_runs (\n"
        "    run_id VARCHAR PRIMARY KEY,\n"
        "    code_version VARCHAR NOT NULL REFERENCES schema_migrations(version)\n"
        ");\n",
    )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)

    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        rows = connection.execute(
            "SELECT version, filename FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        connection.close()
    assert [row[0] for row in rows] == ["0001", "0002"]


def test_duplicate_migration_version_is_rejected(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, "0001_create_schema_migrations.sql", MIGRATION_0001[1])
    write_migration(migrations_dir, "0001_duplicate.sql", "CREATE TABLE dup (id INTEGER);\n")
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)

    with pytest.raises(MigrationError, match="Duplicate"):
        manager.initialize()


def test_malformed_migration_filename_is_rejected(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(
        migrations_dir, "not_a_versioned_migration.sql", "CREATE TABLE x (id INTEGER);\n"
    )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)

    with pytest.raises(MigrationError, match="does not match"):
        manager.initialize()


# --- checksum mismatch --------------------------------------------------------


def test_checksum_mismatch_on_already_applied_migration_is_rejected(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    manager.initialize()

    # Mutate the already-applied migration's content.
    write_migration(
        migrations_dir, MIGRATION_0001[0], "CREATE TABLE schema_migrations_altered (id INTEGER);\n"
    )

    with pytest.raises(MigrationError, match="Checksum mismatch"):
        manager.initialize()


def test_checksum_mismatch_does_not_reapply_or_corrupt_state(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()

    write_migration(migrations_dir, MIGRATION_0001[0], "CREATE TABLE altered (id INTEGER);\n")
    with pytest.raises(MigrationError):
        manager.initialize()

    connection = duckdb.connect(str(first.database_path), read_only=True)
    try:
        tables = table_names(connection)
    finally:
        connection.close()
    assert "altered" not in tables
    assert "schema_migrations" in tables


# --- transaction rollback -----------------------------------------------------


def test_failed_migration_rolls_back_all_its_ddl(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(
        migrations_dir,
        "0002_partially_fails.sql",
        "CREATE TABLE should_not_persist (id INTEGER);\n"
        "CREATE TABLE should_not_persist (id INTEGER);\n",  # duplicate -> fails
    )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)

    with pytest.raises(MigrationError, match="0002_partially_fails.sql"):
        manager.initialize()

    connection = duckdb.connect(str(default_database_path(settings)), read_only=True)
    try:
        tables = table_names(connection)
        applied_versions = {
            row[0] for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
    finally:
        connection.close()

    assert "should_not_persist" not in tables
    assert applied_versions == {"0001"}


def test_fixing_a_failed_migration_allows_recovery_on_next_run(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(
        migrations_dir,
        "0002_partially_fails.sql",
        "CREATE TABLE should_not_persist (id INTEGER);\n"
        "CREATE TABLE should_not_persist (id INTEGER);\n",
    )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    with pytest.raises(MigrationError):
        manager.initialize()

    # Replace the broken migration with a valid one under the same version+name is not
    # allowed once applied would-be, but 0002 was never recorded as applied, so it can
    # simply be corrected in place and re-run.
    write_migration(
        migrations_dir, "0002_partially_fails.sql", "CREATE TABLE fixed (id INTEGER);\n"
    )
    result = manager.initialize()

    assert result.applied_migration_count == 1
    assert result.schema_version == "0002"


# --- path safety ---------------------------------------------------------


def test_default_path_is_inside_configured_project_data_path(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings)
    assert manager.database_path.parent == settings.project_data_path.resolve()


def test_explicit_path_outside_project_data_path_is_rejected(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    outside_path = tmp_path / "elsewhere" / DATABASE_FILENAME

    with pytest.raises(DatabasePathSafetyError):
        DuckDBManager(settings=settings, database_path=outside_path)


def test_path_traversal_outside_project_data_path_is_rejected(tmp_path, isolated_env_file):
    settings = isolated_settings(tmp_path, isolated_env_file)
    traversal_path = settings.project_data_path / ".." / "escaped.duckdb"

    with pytest.raises(DatabasePathSafetyError):
        DuckDBManager(settings=settings, database_path=traversal_path)


def test_test_injected_temp_path_via_settings_is_accepted(tmp_path, isolated_env_file):
    """A test pointing project_data_path at tmp_path is the sanctioned override path."""
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings)  # no explicit database_path override needed
    result = manager.initialize()
    assert tmp_path.resolve() in result.database_path.parents


def test_real_default_database_path_is_under_repo_data_directory(isolated_env_file):
    """Constructing (not initializing) a manager with default settings must never touch disk."""
    settings = Settings(_env_file=isolated_env_file)
    manager = DuckDBManager(settings=settings)
    assert manager.database_path == (REPO_ROOT / "data" / DATABASE_FILENAME).resolve()


# --- check_health -------------------------------------------------------------


def test_check_health_before_initialization_reports_unhealthy_without_creating_file(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    health = manager.check_health()

    assert health.healthy is False
    assert health.database_exists is False
    assert health.required_tables_present is False
    assert health.required_columns_present is False
    assert health.migration_history_valid is False
    assert health.checksums_valid is False
    assert health.is_current is False
    assert health.schema_version is None
    assert health.applied_migration_count is None
    assert not health.database_path.exists()


def test_check_health_after_initialization_reports_healthy(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()
    health = manager.check_health()

    assert health.healthy is True
    assert health.database_exists is True
    assert health.required_tables_present is True
    assert health.required_columns_present is True
    assert health.migration_history_valid is True
    assert health.checksums_valid is True
    assert health.is_current is True
    assert health.schema_version == "0010"
    assert health.applied_migration_count == 10


def test_check_health_is_read_only(tmp_path, isolated_env_file):
    """check_health must never write, even implicitly (e.g. via schema creation)."""
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()
    before = manager.database_path.stat().st_mtime_ns

    manager.check_health()

    after = manager.database_path.stat().st_mtime_ns
    assert before == after


# --- migration integrity: missing applied migration file ---------------------


def test_initialize_rejects_missing_applied_migration_file(tmp_path, isolated_env_file):
    """A version recorded as applied whose migration file no longer exists is rejected."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    manager.initialize()

    missing_dir = tmp_path / "migrations_missing"
    missing_dir.mkdir()
    reduced_manager = DuckDBManager(settings=settings, migrations_dir=missing_dir)

    with pytest.raises(MigrationError, match="no corresponding migration file"):
        reduced_manager.initialize()


def test_check_health_missing_applied_migration_file_is_unhealthy(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    manager.initialize()

    missing_dir = tmp_path / "migrations_missing"
    missing_dir.mkdir()
    reduced_manager = DuckDBManager(settings=settings, migrations_dir=missing_dir)

    health = reduced_manager.check_health()

    assert health.database_exists is True
    assert health.migration_history_valid is False
    assert health.healthy is False


# --- migration integrity: stale database behind the latest migration ---------


def test_check_health_stale_database_is_unhealthy(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    manager.initialize()

    # A new migration becomes available, but is never applied.
    write_migration(migrations_dir, *MIGRATION_0002)

    health = manager.check_health()

    assert health.schema_version == "0001"
    assert health.migration_history_valid is True
    assert health.checksums_valid is True
    assert health.is_current is False
    assert health.healthy is False


# --- migration integrity: checksum mismatch -----------------------------------


def test_check_health_checksum_mismatch_is_unhealthy(tmp_path, isolated_env_file):
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    manager.initialize()

    write_migration(
        migrations_dir, MIGRATION_0001[0], "CREATE TABLE schema_migrations_altered (id INTEGER);\n"
    )

    health = manager.check_health()

    assert health.checksums_valid is False
    assert health.is_current is False
    assert health.healthy is False


# --- required-column failure ---------------------------------------------------


def test_check_health_missing_required_column_is_unhealthy(tmp_path, isolated_env_file):
    """A required column dropped outside the migration runner is caught independently."""
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute("ALTER TABLE ingestion_runs DROP COLUMN code_version")
    finally:
        connection.close()

    health = manager.check_health()

    assert health.required_tables_present is True
    assert health.required_columns_present is False
    assert health.healthy is False


# --- 0002 -> 0003 upgrade -------------------------------------------------------


def test_0002_to_0003_upgrade_preserves_existing_infrastructure_state(tmp_path, isolated_env_file):
    """A database already at 0002 upgrades to 0003 without losing existing rows."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(migrations_dir, *MIGRATION_0002)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()
    assert first.schema_version == "0002"

    # Simulate pre-existing infrastructure state written before the 0003 upgrade.
    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version) "
            "VALUES ('run-1', 'alpaca', 'snapshot', now(), 'succeeded', 'v0')"
        )
    finally:
        connection.close()

    # 0003 becomes available.
    write_migration(migrations_dir, *MIGRATION_0003)
    second = manager.initialize()

    assert second.applied_migration_count == 1
    assert second.schema_version == "0003"

    connection = duckdb.connect(str(manager.database_path), read_only=True)
    try:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'ingestion_runs'"
            ).fetchall()
        }
        assert "schema_version" in columns

        row = connection.execute(
            "SELECT run_id, provider, schema_version FROM ingestion_runs WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row[0] == "run-1"
    assert row[1] == "alpaca"
    assert row[2] is None  # pre-existing row has no backfilled value for the new column

    health = manager.check_health()
    assert health.healthy is True
    assert health.schema_version == "0003"


# --- migration 0005 (market_bars) schema and primary key ---------------------


def test_migration_0005_creates_market_bars_with_expected_primary_key(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        pk_columns = [
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'market_bars' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert pk_columns == [
        "provider",
        "symbol",
        "timeframe",
        "feed",
        "adjustment",
        "currency",
        "bar_timestamp",
    ]


def test_migration_0005_primary_key_rejects_duplicate_identity(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO market_bars (provider, symbol, timeframe, feed, adjustment, currency, "
            "bar_timestamp, open, high, low, close, volume, trade_count, vwap, retrieved_at, "
            "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
            "('alpaca', 'SPY', '5Min', 'iex', 'raw', 'USD', now(), 100, 101, 99, 100.5, 1000, "
            "50, 100.2, now(), now(), now(), 'run-1')"
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO market_bars (provider, symbol, timeframe, feed, adjustment, "
                "currency, bar_timestamp, open, high, low, close, volume, trade_count, vwap, "
                "retrieved_at, first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
                "('alpaca', 'SPY', '5Min', 'iex', 'raw', 'USD', "
                "(SELECT bar_timestamp FROM market_bars LIMIT 1), "
                "200, 201, 199, 200.5, 2000, 60, 200.2, now(), now(), now(), 'run-2')"
            )
    finally:
        connection.close()


def test_migration_0005_nullable_trade_count_and_vwap(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO market_bars (provider, symbol, timeframe, feed, adjustment, currency, "
            "bar_timestamp, open, high, low, close, volume, trade_count, vwap, retrieved_at, "
            "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
            "('alpaca', 'SPY', '5Min', 'iex', 'raw', 'USD', now(), 100, 101, 99, 100.5, 1000, "
            "NULL, NULL, now(), now(), now(), 'run-1')"
        )
        row = connection.execute(
            "SELECT trade_count, vwap FROM market_bars WHERE provider = 'alpaca'"
        ).fetchone()
    finally:
        connection.close()

    assert row == (None, None)


# --- 0004 -> 0005 upgrade ------------------------------------------------------


def test_0004_to_0005_upgrade_preserves_existing_infrastructure_state(tmp_path, isolated_env_file):
    """A database already at 0004 upgrades to 0005 without losing existing rows."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(migrations_dir, *MIGRATION_0002)
    write_migration(migrations_dir, *MIGRATION_0003)
    real_migrations_dir = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    migration_0004_sql = (real_migrations_dir / "0004_create_news_articles.sql").read_text(
        encoding="utf-8"
    )
    write_migration(migrations_dir, "0004_create_news_articles.sql", migration_0004_sql)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()
    assert first.schema_version == "0004"

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
            "schema_version) VALUES ('run-1', 'alpaca', 'news', now(), 'succeeded', 'v0', '0004')"
        )
    finally:
        connection.close()

    migration_0005_sql = (real_migrations_dir / "0005_create_market_bars.sql").read_text(
        encoding="utf-8"
    )
    write_migration(migrations_dir, "0005_create_market_bars.sql", migration_0005_sql)
    second = manager.initialize()

    assert second.applied_migration_count == 1
    assert second.schema_version == "0005"

    connection = duckdb.connect(str(manager.database_path), read_only=True)
    try:
        tables = table_names(connection)
        row = connection.execute(
            "SELECT run_id, provider FROM ingestion_runs WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert "market_bars" in tables
    assert row is not None
    assert row[0] == "run-1"

    health = manager.check_health()
    assert health.healthy is True
    assert health.schema_version == "0005"


# --- migration 0006 (macro_observations) schema and primary key --------------


def test_migration_0006_creates_macro_observations_with_expected_primary_key(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        pk_columns = [
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'macro_observations' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert pk_columns == [
        "provider",
        "series_id",
        "observation_date",
        "realtime_start",
        "realtime_end",
    ]


def test_migration_0006_primary_key_rejects_duplicate_identity(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO macro_observations (provider, series_id, observation_date, "
            "realtime_start, realtime_end, value, is_missing, retrieved_at, first_ingested_at, "
            "last_seen_at, ingestion_run_id) VALUES "
            "('fred', 'FEDFUNDS', '2026-08-01', '2026-08-20', '2026-08-20', 5.33, false, "
            "now(), now(), now(), 'run-1')"
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO macro_observations (provider, series_id, observation_date, "
                "realtime_start, realtime_end, value, is_missing, retrieved_at, "
                "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
                "('fred', 'FEDFUNDS', '2026-08-01', '2026-08-20', '2026-08-20', 9.99, false, "
                "now(), now(), now(), 'run-2')"
            )
    finally:
        connection.close()


def test_migration_0006_missing_value_check_constraint(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO macro_observations (provider, series_id, observation_date, "
            "realtime_start, realtime_end, value, is_missing, retrieved_at, first_ingested_at, "
            "last_seen_at, ingestion_run_id) VALUES "
            "('fred', 'FEDFUNDS', '2026-08-01', '2026-08-20', '2026-08-20', NULL, true, "
            "now(), now(), now(), 'run-1')"
        )
        row = connection.execute(
            "SELECT value, is_missing FROM macro_observations WHERE series_id = 'FEDFUNDS'"
        ).fetchone()
        assert row == (None, True)

        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO macro_observations (provider, series_id, observation_date, "
                "realtime_start, realtime_end, value, is_missing, retrieved_at, "
                "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
                "('fred', 'CPIAUCSL', '2026-08-01', '2026-08-20', '2026-08-20', NULL, false, "
                "now(), now(), now(), 'run-2')"
            )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO macro_observations (provider, series_id, observation_date, "
                "realtime_start, realtime_end, value, is_missing, retrieved_at, "
                "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
                "('fred', 'DGS10', '2026-08-01', '2026-08-20', '2026-08-20', 4.2, true, "
                "now(), now(), now(), 'run-3')"
            )
    finally:
        connection.close()


# --- 0005 -> 0006 upgrade -------------------------------------------------------


def test_0005_to_0006_upgrade_preserves_existing_infrastructure_state(tmp_path, isolated_env_file):
    """A database already at 0005 upgrades to 0006 without losing existing rows."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(migrations_dir, *MIGRATION_0002)
    write_migration(migrations_dir, *MIGRATION_0003)
    real_migrations_dir = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    migration_0004_sql = (real_migrations_dir / "0004_create_news_articles.sql").read_text(
        encoding="utf-8"
    )
    write_migration(migrations_dir, "0004_create_news_articles.sql", migration_0004_sql)
    migration_0005_sql = (real_migrations_dir / "0005_create_market_bars.sql").read_text(
        encoding="utf-8"
    )
    write_migration(migrations_dir, "0005_create_market_bars.sql", migration_0005_sql)
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()
    assert first.schema_version == "0005"

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
            "schema_version) VALUES ('run-1', 'alpaca', 'bars', now(), 'succeeded', 'v0', '0005')"
        )
    finally:
        connection.close()

    migration_0006_sql = (real_migrations_dir / "0006_create_macro_observations.sql").read_text(
        encoding="utf-8"
    )
    write_migration(migrations_dir, "0006_create_macro_observations.sql", migration_0006_sql)
    second = manager.initialize()

    assert second.applied_migration_count == 1
    assert second.schema_version == "0006"

    connection = duckdb.connect(str(manager.database_path), read_only=True)
    try:
        tables = table_names(connection)
        row = connection.execute(
            "SELECT run_id, provider FROM ingestion_runs WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert "macro_observations" in tables
    assert row is not None
    assert row[0] == "run-1"

    health = manager.check_health()
    assert health.healthy is True
    assert health.schema_version == "0006"


# --- migration 0007 (orchestration audit) schema and primary keys ------------


def test_migration_0007_creates_orchestration_runs_and_job_runs_tables(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        tables = table_names(connection)
    finally:
        connection.close()

    assert "orchestration_runs" in tables
    assert "orchestration_job_runs" in tables
    assert result.applied_migration_count == 10
    assert result.schema_version == "0010"


def test_migration_0007_orchestration_runs_primary_key_rejects_duplicate(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO orchestration_runs "
            "(orchestration_run_id, started_at_utc, status, code_version) "
            "VALUES ('run-1', now(), 'running', 'v1')"
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO orchestration_runs "
                "(orchestration_run_id, started_at_utc, status, code_version) "
                "VALUES ('run-1', now(), 'running', 'v1')"
            )
    finally:
        connection.close()


def test_migration_0007_orchestration_runs_status_check_constraint(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO orchestration_runs "
                "(orchestration_run_id, started_at_utc, status, code_version) "
                "VALUES ('run-1', now(), 'not_a_status', 'v1')"
            )
    finally:
        connection.close()


def test_migration_0007_orchestration_job_runs_status_check_constraint(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO orchestration_runs "
            "(orchestration_run_id, started_at_utc, status, code_version) "
            "VALUES ('run-1', now(), 'running', 'v1')"
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO orchestration_job_runs "
                "(orchestration_job_run_id, orchestration_run_id, job_id, job_type, provider, "
                "dataset_name, status, code_version) VALUES "
                "('run-1:job-1', 'run-1', 'job-1', 'alpaca_news', 'alpaca', 'news', "
                "'not_a_status', 'v1')"
            )
    finally:
        connection.close()


def test_migration_0007_orchestration_job_runs_job_type_check_constraint(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO orchestration_job_runs "
                "(orchestration_job_run_id, orchestration_run_id, job_id, job_type, provider, "
                "dataset_name, status, code_version) VALUES "
                "('run-1:job-1', 'run-1', 'job-1', 'robinhood_orders', 'alpaca', 'news', "
                "'planned', 'v1')"
            )
    finally:
        connection.close()


# --- 0006 -> 0007 upgrade -------------------------------------------------------


def test_0006_to_0007_upgrade_preserves_existing_infrastructure_state(tmp_path, isolated_env_file):
    """A database already at 0006 upgrades to 0007 without losing existing rows."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(migrations_dir, *MIGRATION_0002)
    write_migration(migrations_dir, *MIGRATION_0003)
    real_migrations_dir = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    for filename in (
        "0004_create_news_articles.sql",
        "0005_create_market_bars.sql",
        "0006_create_macro_observations.sql",
    ):
        write_migration(
            migrations_dir, filename, (real_migrations_dir / filename).read_text(encoding="utf-8")
        )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()
    assert first.schema_version == "0006"

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
            "schema_version) VALUES ('run-1', 'fred', 'macro_observations', now(), "
            "'succeeded', 'v0', '0006')"
        )
    finally:
        connection.close()

    migration_0007_sql = (real_migrations_dir / "0007_create_orchestration_audit.sql").read_text(
        encoding="utf-8"
    )
    write_migration(migrations_dir, "0007_create_orchestration_audit.sql", migration_0007_sql)
    second = manager.initialize()

    assert second.applied_migration_count == 1
    assert second.schema_version == "0007"

    connection = duckdb.connect(str(manager.database_path), read_only=True)
    try:
        tables = table_names(connection)
        row = connection.execute(
            "SELECT run_id, provider FROM ingestion_runs WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert "orchestration_runs" in tables
    assert "orchestration_job_runs" in tables
    assert row is not None
    assert row[0] == "run-1"

    health = manager.check_health()
    assert health.healthy is True
    assert health.schema_version == "0007"


# --- migration 0008 (macro_series_metadata) schema and primary key -----------


def test_migration_0008_creates_macro_series_metadata_with_expected_primary_key(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        pk_columns = [
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'macro_series_metadata' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert pk_columns == ["provider", "series_id"]


def test_migration_0008_primary_key_rejects_duplicate_identity(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO macro_series_metadata (provider, series_id, title, "
            "observation_start, observation_end, frequency, frequency_short, units, "
            "units_short, seasonal_adjustment, seasonal_adjustment_short, last_updated, "
            "popularity, notes, retrieved_at_utc, first_ingested_at, last_seen_at, "
            "ingestion_run_id) VALUES ('fred', 'FEDFUNDS', 'Federal Funds Effective Rate', "
            "'1954-07-01', '2026-08-01', 'Monthly', 'M', 'Percent', '%', "
            "'Not Seasonally Adjusted', 'NSA', now(), 84, NULL, now(), now(), now(), 'run-1')"
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO macro_series_metadata (provider, series_id, title, "
                "observation_start, observation_end, frequency, frequency_short, units, "
                "units_short, seasonal_adjustment, seasonal_adjustment_short, last_updated, "
                "popularity, notes, retrieved_at_utc, first_ingested_at, last_seen_at, "
                "ingestion_run_id) VALUES ('fred', 'FEDFUNDS', 'Other Title', "
                "'1954-07-01', '2026-08-01', 'Monthly', 'M', 'Percent', '%', "
                "'Not Seasonally Adjusted', 'NSA', now(), 90, NULL, now(), now(), now(), "
                "'run-2')"
            )
    finally:
        connection.close()


def test_migration_0008_notes_column_is_nullable(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO macro_series_metadata (provider, series_id, title, "
            "observation_start, observation_end, frequency, frequency_short, units, "
            "units_short, seasonal_adjustment, seasonal_adjustment_short, last_updated, "
            "popularity, notes, retrieved_at_utc, first_ingested_at, last_seen_at, "
            "ingestion_run_id) VALUES ('fred', 'FEDFUNDS', 'Federal Funds Effective Rate', "
            "'1954-07-01', '2026-08-01', 'Monthly', 'M', 'Percent', '%', "
            "'Not Seasonally Adjusted', 'NSA', now(), 84, NULL, now(), now(), now(), 'run-1')"
        )
        row = connection.execute(
            "SELECT notes FROM macro_series_metadata WHERE series_id = 'FEDFUNDS'"
        ).fetchone()
    finally:
        connection.close()

    assert row == (None,)


# --- 0007 -> 0008 upgrade -------------------------------------------------------


def test_0007_to_0008_upgrade_preserves_existing_infrastructure_state(tmp_path, isolated_env_file):
    """A database already at 0007 upgrades to 0008 without losing existing rows."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(migrations_dir, *MIGRATION_0002)
    write_migration(migrations_dir, *MIGRATION_0003)
    real_migrations_dir = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    for filename in (
        "0004_create_news_articles.sql",
        "0005_create_market_bars.sql",
        "0006_create_macro_observations.sql",
        "0007_create_orchestration_audit.sql",
    ):
        write_migration(
            migrations_dir, filename, (real_migrations_dir / filename).read_text(encoding="utf-8")
        )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()
    assert first.schema_version == "0007"

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
            "schema_version) VALUES ('run-1', 'fred', 'macro_observations', now(), "
            "'succeeded', 'v0', '0007')"
        )
    finally:
        connection.close()

    migration_0008_sql = (
        real_migrations_dir / "0008_create_macro_series_metadata.sql"
    ).read_text(encoding="utf-8")
    write_migration(migrations_dir, "0008_create_macro_series_metadata.sql", migration_0008_sql)
    second = manager.initialize()

    assert second.applied_migration_count == 1
    assert second.schema_version == "0008"

    connection = duckdb.connect(str(manager.database_path), read_only=True)
    try:
        tables = table_names(connection)
        row = connection.execute(
            "SELECT run_id, provider FROM ingestion_runs WHERE run_id = 'run-1'"
        ).fetchone()
    finally:
        connection.close()

    assert "macro_series_metadata" in tables
    assert row is not None
    assert row[0] == "run-1"

    health = manager.check_health()
    assert health.healthy is True
    assert health.schema_version == "0008"


# --- migration 0009 (option_chain_snapshots) schema and primary key ----------


def test_migration_0009_creates_option_chain_snapshots_with_expected_primary_key(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        tables = table_names(connection)
        pk_columns = [
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'option_chain_snapshots' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert "option_chain_snapshots" in tables
    assert pk_columns == ["provider", "underlying", "feed", "contract_symbol", "retrieved_at"]


def test_migration_0009_primary_key_keeps_opra_and_indicative_separate(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    insert = (
        "INSERT INTO option_chain_snapshots (provider, underlying, feed, contract_symbol, "
        "retrieved_at, expiration_date, option_type, strike_price, "
        "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
        "('alpaca', 'SPY', ?, 'SPY260918C00500000', '2026-09-02T15:30:05', '2026-09-18', "
        "'call', 500, now(), now(), 'run-1')"
    )
    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(insert, ["opra"])
        connection.execute(insert, ["indicative"])  # not a conflict: different feed
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(insert, ["opra"])  # exact identity repeat
        count = connection.execute(
            "SELECT count(*) FROM option_chain_snapshots"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 2


def test_migration_0009_optional_columns_are_nullable(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO option_chain_snapshots (provider, underlying, feed, contract_symbol, "
            "retrieved_at, expiration_date, option_type, strike_price, "
            "first_ingested_at, last_seen_at, ingestion_run_id) VALUES "
            "('alpaca', 'SPY', 'opra', 'SPY260918C00500000', now(), '2026-09-18', 'call', 500, "
            "now(), now(), 'run-1')"
        )
        row = connection.execute(
            "SELECT bid_price, delta, implied_volatility, quote_timestamp "
            "FROM option_chain_snapshots"
        ).fetchone()
    finally:
        connection.close()
    assert row == (None, None, None, None)


def test_migration_0009_rejects_unknown_feed_and_type(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                "INSERT INTO option_chain_snapshots (provider, underlying, feed, "
                "contract_symbol, retrieved_at, expiration_date, option_type, strike_price, "
                "first_ingested_at, last_seen_at, ingestion_run_id) "
                "VALUES ('alpaca', 'SPY', 'sip', 'SPY260918C00500000', now(), '2026-09-18', "
                "'call', 500, now(), now(), 'run-1')"
            )
    finally:
        connection.close()


# --- migration 0009 (option_chain_snapshot_batches) schema and primary key --


_BATCH_INSERT_COLUMNS = (
    "ingestion_run_id, provider, underlying, requested_feed, "
    "requested_expiration_date_gte, requested_expiration_date_lte, "
    "requested_strike_price_gte, requested_strike_price_lte, requested_option_type, "
    "retrieved_at, contract_count, outcome, first_ingested_at, last_seen_at"
)


def _batch_insert_values(
    run_id: str = "run-1", *, feed: str = "opra", contract_count: int = 1,
    outcome: str = "succeeded",
) -> str:
    return (
        f"('{run_id}', 'alpaca', 'SPY', '{feed}', '2026-09-01', '2026-09-30', 400, 600, NULL, "
        f"now(), {contract_count}, '{outcome}', now(), now())"
    )


def test_migration_0009_creates_option_chain_snapshot_batches_with_run_id_primary_key(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        tables = table_names(connection)
        pk_columns = [
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'option_chain_snapshot_batches' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert "option_chain_snapshot_batches" in tables
    assert pk_columns == ["ingestion_run_id"]


def test_migration_0009_batch_allows_zero_contract_count_for_skipped_empty(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batches ({_BATCH_INSERT_COLUMNS}) VALUES "
            + _batch_insert_values(contract_count=0, outcome="skipped_empty")
        )
        row = connection.execute(
            "SELECT contract_count, outcome FROM option_chain_snapshot_batches"
        ).fetchone()
    finally:
        connection.close()
    assert row == (0, "skipped_empty")


def test_migration_0009_batch_rejects_negative_contract_count(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                f"INSERT INTO option_chain_snapshot_batches ({_BATCH_INSERT_COLUMNS}) VALUES "
                + _batch_insert_values(contract_count=-1)
            )
    finally:
        connection.close()


def test_migration_0009_batch_rejects_unknown_outcome(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                f"INSERT INTO option_chain_snapshot_batches ({_BATCH_INSERT_COLUMNS}) VALUES "
                + _batch_insert_values(outcome="bogus")
            )
    finally:
        connection.close()


def test_migration_0009_batch_rejects_unknown_requested_feed(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                f"INSERT INTO option_chain_snapshot_batches ({_BATCH_INSERT_COLUMNS}) VALUES "
                + _batch_insert_values(feed="sip")
            )
    finally:
        connection.close()


def test_migration_0009_batch_run_id_is_unique(tmp_path, isolated_env_file):
    """Exactly one batch record per successful logical ingestion (run id)."""
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batches ({_BATCH_INSERT_COLUMNS}) VALUES "
            + _batch_insert_values("run-dup")
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                f"INSERT INTO option_chain_snapshot_batches ({_BATCH_INSERT_COLUMNS}) VALUES "
                + _batch_insert_values("run-dup")
            )
    finally:
        connection.close()


def test_migration_0009_has_no_open_interest_column(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name IN ('option_chain_snapshots', 'option_chain_snapshot_batches', "
                "'option_chain_snapshot_batch_items')"
            ).fetchall()
        }
    finally:
        connection.close()
    assert not any("open_interest" in c or "openinterest" in c.lower() for c in columns)


# --- migration 0009 (option_chain_snapshot_batch_items) schema and primary key --


def test_migration_0009_creates_option_chain_snapshot_batch_items_with_expected_primary_key(
    tmp_path, isolated_env_file
):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    result = manager.initialize()

    connection = duckdb.connect(str(result.database_path), read_only=True)
    try:
        tables = table_names(connection)
        pk_columns = [
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'option_chain_snapshot_batch_items' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert "option_chain_snapshot_batch_items" in tables
    assert pk_columns == [
        "ingestion_run_id", "provider", "underlying", "feed", "contract_symbol", "retrieved_at",
    ]
    assert result.applied_migration_count == 10
    assert result.schema_version == "0010"


_BATCH_ITEM_INSERT_COLUMNS = (
    "ingestion_run_id, provider, underlying, feed, contract_symbol, retrieved_at"
)


def _batch_item_insert_values(
    run_id: str = "run-1", *, feed: str = "opra", contract_symbol: str = "SPY260918C00500000",
    retrieved_at: str = "2026-09-02T15:30:05",
) -> str:
    return f"('{run_id}', 'alpaca', 'SPY', '{feed}', '{contract_symbol}', '{retrieved_at}')"


def test_migration_0009_batch_item_same_run_allows_multiple_contracts(
    tmp_path, isolated_env_file
):
    """One batch's membership can list many distinct contracts."""
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
            "VALUES " + _batch_item_insert_values(contract_symbol="SPY260918C00500000")
        )
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
            "VALUES " + _batch_item_insert_values(contract_symbol="SPY260918C00520000")
        )
        count = connection.execute(
            "SELECT count(*) FROM option_chain_snapshot_batch_items WHERE ingestion_run_id = "
            "'run-1'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 2


def test_migration_0009_batch_item_rejects_exact_duplicate(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
            "VALUES " + _batch_item_insert_values()
        )
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
                "VALUES " + _batch_item_insert_values()
            )
    finally:
        connection.close()


def test_migration_0009_batch_item_allows_same_snapshot_identity_across_two_runs(
    tmp_path, isolated_env_file
):
    """Two different batches (run ids) may both reference the same immutable snapshot identity."""
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
            "VALUES " + _batch_item_insert_values(run_id="run-1")
        )
        connection.execute(
            f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
            "VALUES " + _batch_item_insert_values(run_id="run-2")
        )
        count = connection.execute(
            "SELECT count(*) FROM option_chain_snapshot_batch_items"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 2


def test_migration_0009_batch_item_rejects_unknown_feed(tmp_path, isolated_env_file):
    manager = real_migrations_manager(tmp_path, isolated_env_file)
    manager.initialize()

    connection = duckdb.connect(str(manager.database_path))
    try:
        with pytest.raises(duckdb.ConstraintException):
            connection.execute(
                f"INSERT INTO option_chain_snapshot_batch_items ({_BATCH_ITEM_INSERT_COLUMNS}) "
                "VALUES " + _batch_item_insert_values(feed="sip")
            )
    finally:
        connection.close()


# --- 0008 -> 0009 upgrade ----------------------------------------------------


def test_0008_to_0009_upgrade_preserves_existing_infrastructure_state(
    tmp_path, isolated_env_file
):
    """A database already at 0008 upgrades to 0009 without losing existing rows."""
    migrations_dir = tmp_path / "migrations"
    write_migration(migrations_dir, *MIGRATION_0001)
    write_migration(migrations_dir, *MIGRATION_0002)
    write_migration(migrations_dir, *MIGRATION_0003)
    real_migrations_dir = Path(__file__).resolve().parents[1] / "storage" / "migrations"
    for filename in (
        "0004_create_news_articles.sql",
        "0005_create_market_bars.sql",
        "0006_create_macro_observations.sql",
        "0007_create_orchestration_audit.sql",
        "0008_create_macro_series_metadata.sql",
    ):
        write_migration(
            migrations_dir, filename, (real_migrations_dir / filename).read_text(encoding="utf-8")
        )
    settings = isolated_settings(tmp_path, isolated_env_file)
    manager = DuckDBManager(settings=settings, migrations_dir=migrations_dir)
    first = manager.initialize()
    assert first.schema_version == "0008"

    connection = duckdb.connect(str(manager.database_path))
    try:
        connection.execute(
            "INSERT INTO ingestion_runs "
            "(run_id, provider, dataset_name, started_at_utc, status, code_version, "
            "schema_version) VALUES ('run-1', 'alpaca', 'bars', now(), 'succeeded', 'v0', '0008')"
        )
    finally:
        connection.close()

    migration_0009_sql = (
        real_migrations_dir / "0009_create_option_chain_snapshots.sql"
    ).read_text(encoding="utf-8")
    write_migration(migrations_dir, "0009_create_option_chain_snapshots.sql", migration_0009_sql)
    second = manager.initialize()

    assert second.applied_migration_count == 1
    assert second.schema_version == "0009"

    connection = duckdb.connect(str(manager.database_path), read_only=True)
    try:
        tables = table_names(connection)
        row = connection.execute(
            "SELECT run_id FROM ingestion_runs WHERE run_id = 'run-1'"
        ).fetchone()
        batch_item_pk_columns = [
            r[0]
            for r in connection.execute(
                "SELECT column_name FROM information_schema.key_column_usage "
                "WHERE table_name = 'option_chain_snapshot_batch_items' ORDER BY ordinal_position"
            ).fetchall()
        ]
    finally:
        connection.close()

    assert "option_chain_snapshots" in tables
    assert "option_chain_snapshot_batches" in tables
    assert "option_chain_snapshot_batch_items" in tables
    assert batch_item_pk_columns == [
        "ingestion_run_id", "provider", "underlying", "feed", "contract_symbol", "retrieved_at",
    ]
    assert row == ("run-1",)

    health = manager.check_health()
    assert health.healthy is True
    assert health.schema_version == "0009"


# --- database file remains ignored by Git ------------------------------------


def test_database_file_is_git_ignored():
    if shutil.which("git") is None:
        pytest.skip("git executable not available")

    candidate = REPO_ROOT / "data" / DATABASE_FILENAME
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(candidate)],
        cwd=REPO_ROOT,
        check=False,
    )
    assert result.returncode == 0, "expected data/*.duckdb to be git-ignored"
