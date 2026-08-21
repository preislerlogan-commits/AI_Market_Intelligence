"""Local DuckDB database manager and migration runner.

This module owns exactly one thing: getting a local DuckDB file to a known,
versioned schema state. It never connects to a provider API, never accepts
or stores credentials, and never ingests provider data — it only creates
and verifies the infrastructure-metadata tables defined under
``market_intelligence/storage/migrations/`` (see
``docs/STORAGE_ARCHITECTURE.md``).

Migration application is transactional (each migration's DDL and its
``schema_migrations`` bookkeeping row commit or roll back together),
ordered (applied by ascending zero-padded version prefix), checksum-verified
(an already-applied migration whose file content has since changed is
rejected rather than silently re-applied or ignored), and idempotent
(re-running ``initialize()`` against an up-to-date database applies zero
migrations and does not error).

Both ``initialize()`` and the read-only ``check_health()`` verify that a
database's recorded migration history can actually be reproduced from the
current migration directory: every applied version must have a
corresponding migration file, applied versions must form a contiguous,
correctly ordered prefix of the available migrations (no gaps, no
out-of-sequence history), and stored checksums must match current file
content. A database whose history cannot be reproduced is never treated as
current or healthy.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import duckdb

from market_intelligence.config.settings import Settings

DATABASE_FILENAME = "market_intelligence.duckdb"
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
REQUIRED_TABLES = ("schema_migrations", "ingestion_runs")
REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "schema_migrations": frozenset({"version", "filename", "checksum", "applied_at_utc"}),
    "ingestion_runs": frozenset(
        {
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
    ),
}

_MIGRATION_FILENAME_PATTERN = re.compile(r"^(?P<version>\d{4})_[a-z0-9_]+\.sql$")


class DatabasePathSafetyError(RuntimeError):
    """Raised when a database path would resolve outside the configured project data directory.

    This project's data-safety rules require every database file to live
    under ``Settings.project_data_path`` (which itself defaults to this
    repository's own ``data/`` directory). Tests inject an isolated
    location by pointing ``Settings.project_data_path`` at a temporary
    directory, not by bypassing this check.
    """


class MigrationError(RuntimeError):
    """Raised for a migration ordering, checksum, or application failure.

    Migration SQL never contains credentials, so — unlike the provider
    connectors — this error is allowed to include the underlying database
    exception for debuggability.
    """


@dataclass(frozen=True)
class Migration:
    """A single loaded, checksummed migration file."""

    version: str
    filename: str
    checksum: str
    sql: str


@dataclass(frozen=True)
class InitializationResult:
    """Sanitized result of a database initialization run."""

    database_path: Path
    schema_version: str
    applied_migration_count: int


@dataclass(frozen=True)
class HealthCheckResult:
    """Sanitized result of a read-only database health check.

    Every field is a boolean or plain status value — never raw exception
    text or file content — so this result is always safe to print or log.
    """

    database_path: Path
    database_exists: bool
    schema_version: str | None
    applied_migration_count: int | None
    required_tables_present: bool
    required_columns_present: bool
    migration_history_valid: bool
    checksums_valid: bool
    is_current: bool
    healthy: bool


@dataclass(frozen=True)
class _MigrationIntegrityReport:
    """Whether a database's applied migration history matches the migration directory."""

    missing_migration_files: tuple[str, ...]
    checksum_mismatches: tuple[str, ...]
    sequence_contiguous: bool

    @property
    def history_valid(self) -> bool:
        return not self.missing_migration_files and self.sequence_contiguous

    @property
    def checksums_valid(self) -> bool:
        return not self.checksum_mismatches


def default_database_path(settings: Settings) -> Path:
    """Return the default database path for ``settings.project_data_path``."""
    return settings.project_data_path / DATABASE_FILENAME


def _checksum(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _load_migrations(migrations_dir: Path) -> list[Migration]:
    """Load, checksum, and order every ``*.sql`` file in ``migrations_dir``.

    Filenames must match ``NNNN_description.sql``; sorting the filenames
    lexicographically sorts them by version because the version prefix is
    zero-padded. Raises ``MigrationError`` for a malformed filename, a
    duplicate version, or an out-of-order/non-ascending version sequence.
    """
    migrations = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _MIGRATION_FILENAME_PATTERN.match(path.name)
        if not match:
            raise MigrationError(
                f"Migration filename '{path.name}' does not match the required "
                "'NNNN_description.sql' pattern."
            )
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=match.group("version"),
                filename=path.name,
                checksum=_checksum(sql),
                sql=sql,
            )
        )

    versions = [migration.version for migration in migrations]
    if len(set(versions)) != len(versions):
        raise MigrationError("Duplicate migration version detected.")
    if versions != sorted(versions):
        raise MigrationError("Migration files are not in ascending version order.")

    return migrations


def _diff_migration_history(
    migrations: list[Migration], applied: dict[str, str]
) -> _MigrationIntegrityReport:
    """Compare a database's applied migration history against available migration files.

    ``applied`` maps applied version -> stored checksum, as read from
    ``schema_migrations``. Detects three distinct failure modes: an applied
    version with no corresponding migration file, an applied version whose
    stored checksum no longer matches the current file content, and an
    applied-version sequence that is not a contiguous, correctly ordered
    prefix of the available migrations (a gap or out-of-order history).
    """
    migration_versions = [migration.version for migration in migrations]
    migration_by_version = {migration.version: migration for migration in migrations}

    missing_migration_files = tuple(sorted(set(applied) - set(migration_versions)))
    checksum_mismatches = tuple(
        sorted(
            version
            for version, checksum in applied.items()
            if version in migration_by_version
            and migration_by_version[version].checksum != checksum
        )
    )

    applied_sorted = sorted(applied)
    expected_prefix = migration_versions[: len(applied_sorted)]
    sequence_contiguous = not missing_migration_files and applied_sorted == expected_prefix

    return _MigrationIntegrityReport(
        missing_migration_files=missing_migration_files,
        checksum_mismatches=checksum_mismatches,
        sequence_contiguous=sequence_contiguous,
    )


def _required_columns_present(
    connection: duckdb.DuckDBPyConnection, existing_tables: set[str]
) -> bool:
    """Return True only if every required table exists and has every required column."""
    if not set(REQUIRED_TABLES).issubset(existing_tables):
        return False
    for table, required in REQUIRED_COLUMNS.items():
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                [table],
            ).fetchall()
        }
        if not required.issubset(columns):
            return False
    return True


class DuckDBManager:
    """Manages the local DuckDB file: path safety, migrations, and health checks."""

    def __init__(
        self,
        settings: Settings | None = None,
        database_path: Path | None = None,
        migrations_dir: Path | None = None,
    ) -> None:
        self._settings = settings or Settings()
        self._migrations_dir = migrations_dir or MIGRATIONS_DIR
        candidate = Path(database_path) if database_path is not None else default_database_path(
            self._settings
        )
        self._database_path = self._validated_path(candidate)

    def _validated_path(self, candidate: Path) -> Path:
        resolved = candidate.resolve()
        allowed_root = self._settings.project_data_path.resolve()
        if resolved != allowed_root and allowed_root not in resolved.parents:
            raise DatabasePathSafetyError(
                "Refusing to initialize a database outside the configured "
                "project data directory."
            )
        return resolved

    @property
    def database_path(self) -> Path:
        return self._database_path

    def initialize(self) -> InitializationResult:
        """Bring the database up to the latest migration version.

        Creates the database file and its parent directory if needed.
        Transactional, ordered, checksum-verified, and idempotent — see
        module docstring.
        """
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        migrations = _load_migrations(self._migrations_dir)

        connection = duckdb.connect(str(self._database_path))
        try:
            applied = self._read_applied_versions(connection)
            report = _diff_migration_history(migrations, applied)
            if report.missing_migration_files:
                raise MigrationError(
                    "Applied migration version(s) have no corresponding migration "
                    "file: " + ", ".join(report.missing_migration_files)
                )
            if not report.sequence_contiguous:
                raise MigrationError(
                    "Applied migration history is not a contiguous, correctly "
                    "ordered prefix of the available migrations; the version "
                    "sequence is malformed."
                )
            if report.checksum_mismatches:
                version = report.checksum_mismatches[0]
                filename = next(m.filename for m in migrations if m.version == version)
                raise MigrationError(
                    f"Checksum mismatch for already-applied migration "
                    f"'{filename}': its file content has changed "
                    "since it was applied."
                )

            applied_count = 0
            for migration in migrations:
                if migration.version in applied:
                    continue
                self._apply_migration(connection, migration)
                applied_count += 1
            schema_version = migrations[-1].version if migrations else "0000"
        finally:
            connection.close()

        return InitializationResult(
            database_path=self._database_path,
            schema_version=schema_version,
            applied_migration_count=applied_count,
        )

    def check_health(self) -> HealthCheckResult:
        """Perform a read-only health check. Never runs or applies migrations.

        If the database file does not exist, returns an unhealthy result
        without creating it. Verifies required tables and columns are
        present, that the database's applied migration history can be
        reproduced from the current migration directory (no missing files,
        no checksum mismatches, no gaps or out-of-order versions), and that
        the database is at the latest available migration. ``healthy`` is
        false if any of these checks fail. Every field returned is a
        sanitized boolean or plain status value.
        """
        if not self._database_path.exists():
            return HealthCheckResult(
                database_path=self._database_path,
                database_exists=False,
                schema_version=None,
                applied_migration_count=None,
                required_tables_present=False,
                required_columns_present=False,
                migration_history_valid=False,
                checksums_valid=False,
                is_current=False,
                healthy=False,
            )

        try:
            migrations: list[Migration] | None = _load_migrations(self._migrations_dir)
        except MigrationError:
            migrations = None

        connection = duckdb.connect(str(self._database_path), read_only=True)
        try:
            existing_tables = {
                row[0]
                for row in connection.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            }
            required_tables_present = set(REQUIRED_TABLES).issubset(existing_tables)
            required_columns_present = _required_columns_present(connection, existing_tables)

            schema_version: str | None = None
            applied_migration_count: int | None = None
            migration_history_valid = False
            checksums_valid = False
            is_current = False

            if "schema_migrations" in existing_tables:
                applied = self._read_applied_versions(connection)
                applied_migration_count = len(applied)
                versions_sorted = sorted(applied)
                schema_version = versions_sorted[-1] if versions_sorted else None

                if migrations is not None:
                    report = _diff_migration_history(migrations, applied)
                    migration_history_valid = report.history_valid
                    checksums_valid = report.checksums_valid
                    latest_available = migrations[-1].version if migrations else None
                    is_current = (
                        report.history_valid
                        and report.checksums_valid
                        and schema_version == latest_available
                    )
        finally:
            connection.close()

        healthy = (
            required_tables_present
            and required_columns_present
            and migration_history_valid
            and checksums_valid
            and is_current
        )

        return HealthCheckResult(
            database_path=self._database_path,
            database_exists=True,
            schema_version=schema_version,
            applied_migration_count=applied_migration_count,
            required_tables_present=required_tables_present,
            required_columns_present=required_columns_present,
            migration_history_valid=migration_history_valid,
            checksums_valid=checksums_valid,
            is_current=is_current,
            healthy=healthy,
        )

    @staticmethod
    def _read_applied_versions(connection: duckdb.DuckDBPyConnection) -> dict[str, str]:
        table_exists = connection.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'schema_migrations'"
        ).fetchone()[0]
        if not table_exists:
            return {}
        rows = connection.execute("SELECT version, checksum FROM schema_migrations").fetchall()
        return dict(rows)

    @staticmethod
    def _apply_migration(connection: duckdb.DuckDBPyConnection, migration: Migration) -> None:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(migration.sql)
            connection.execute(
                "INSERT INTO schema_migrations (version, filename, checksum, applied_at_utc) "
                "VALUES (?, ?, ?, ?)",
                [migration.version, migration.filename, migration.checksum, datetime.now(UTC)],
            )
        except Exception as exc:
            connection.execute("ROLLBACK")
            raise MigrationError(
                f"Failed to apply migration '{migration.filename}': {exc}"
            ) from exc
        else:
            connection.execute("COMMIT")
