"""Apply ordered SQL Server migrations, discovered lexicographically, exactly once each.

Mirrors `apply_neo4j_migrations.py`'s pattern: track applied migrations by name with a
checksum, verify an already-applied migration's source hasn't drifted, and never rerun
one that already succeeded. `sqlcmd -i <file>` invocations enumerated by hand in
`compose.yaml` silently stop covering new migration files added after the initial two
were wired up (found live: 003/004 existed on disk but were never referenced anywhere
executable) -- this discovers every `NNN_*.sql` file under the migrations directory, not
a hardcoded list.

SQL Server scripts in this repository use `GO` batch separators (required by
`sqlcmd`/SSMS convention for statements like `CREATE SCHEMA` that must be the only
statement in a batch) -- `GO` is not valid T-SQL and must be split out before executing
each batch individually via a plain DB-API cursor.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from importlib.resources import as_file, files

import pymssql

from return_platform.configuration.settings import Settings
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault

_MIGRATION_PACKAGE = "return_platform"
_MIGRATION_PATH = "configuration/sql_migrations"
_MIGRATION_TABLE = "platform.schema_migrations"
_GO_SEPARATOR = re.compile(r"^\s*GO\s*$", re.IGNORECASE | re.MULTILINE)
_MIGRATION_NAME_PATTERN = re.compile(r"^\d{3}_.+\.sql$")


def _batches(sql_text: str) -> tuple[str, ...]:
    return tuple(batch.strip() for batch in _GO_SEPARATOR.split(sql_text) if batch.strip())


def _connect(settings: Settings) -> pymssql.Connection:
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database=settings.sqlserver_database,
        login_timeout=max(1, int(settings.dependency_connect_timeout_seconds)),
        timeout=max(1, int(settings.operation_timeout_seconds)),
        autocommit=False,
    )


def _wait_for_connectivity(settings: Settings, *, attempts: int = 60) -> None:
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with _connect(settings) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    cursor.fetchall()
            return
        except Exception as exc:  # dependency readiness is retried with a bound
            last_error = exc
            import time

            time.sleep(1)
    raise RuntimeError("SQL Server did not become reachable") from last_error


def _ensure_migration_table(connection: pymssql.Connection) -> None:
    with connection.cursor() as cursor:
        cursor.execute("IF SCHEMA_ID(N'platform') IS NULL EXEC(N'CREATE SCHEMA platform')")
        cursor.execute(
            f"""
            IF OBJECT_ID(N'{_MIGRATION_TABLE}', N'U') IS NULL
            CREATE TABLE {_MIGRATION_TABLE} (
                migration_name VARCHAR(255) NOT NULL PRIMARY KEY,
                checksum_sha256 CHAR(64) NOT NULL,
                applied_at DATETIME2(3) NOT NULL CONSTRAINT DF_schema_migrations_applied_at
                    DEFAULT SYSUTCDATETIME()
            )
            """
        )
    connection.commit()


def _applied_checksum(connection: pymssql.Connection, migration_name: str) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT checksum_sha256 FROM {_MIGRATION_TABLE} WHERE migration_name = %s",
            (migration_name,),
        )
        row = cursor.fetchone()
        return str(row[0]) if row else None


def _apply_migration(
    connection: pymssql.Connection, migration_name: str, migration_text: str, checksum: str
) -> None:
    with connection.cursor() as cursor:
        for batch in _batches(migration_text):
            cursor.execute(batch)
        cursor.execute(
            f"INSERT INTO {_MIGRATION_TABLE} (migration_name, checksum_sha256) VALUES (%s, %s)",
            (migration_name, checksum),
        )
    connection.commit()


def _discover_migrations() -> list[tuple[str, str]]:
    migration_root = files(_MIGRATION_PACKAGE).joinpath(_MIGRATION_PATH)
    names = sorted(
        entry.name
        for entry in migration_root.iterdir()
        if entry.is_file() and _MIGRATION_NAME_PATTERN.match(entry.name)
    )
    discovered: list[tuple[str, str]] = []
    for name in names:
        with as_file(migration_root.joinpath(name)) as path:
            discovered.append((name, path.read_text(encoding="utf-8")))
    return discovered


def _run_sync(settings: Settings) -> None:
    _wait_for_connectivity(settings)
    with _connect(settings) as connection:
        _ensure_migration_table(connection)
        migrations = _discover_migrations()
        if not migrations:
            raise RuntimeError("No packaged SQL Server migrations were found")
        for migration_name, migration_text in migrations:
            checksum = hashlib.sha256(migration_text.encode("utf-8")).hexdigest()
            applied_checksum = _applied_checksum(connection, migration_name)
            if applied_checksum is not None:
                if applied_checksum != checksum:
                    raise RuntimeError(
                        f"Applied migration {migration_name} checksum does not match source"
                    )
                print(f"skipped={migration_name}")
                continue
            _apply_migration(connection, migration_name, migration_text, checksum)
            print(f"applied={migration_name}")
    print("sqlserver_schema_status=READY")


async def main() -> None:
    settings, _resolver = await resolve_runtime_settings_from_vault(Settings())
    await asyncio.to_thread(_run_sync, settings)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
