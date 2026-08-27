"""Apply ordered Neo4j migrations and verify required discovery indexes."""

from __future__ import annotations

import asyncio
import hashlib
from importlib.resources import files

from neo4j import AsyncGraphDatabase

from return_platform.configuration.settings import Settings
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault

_MIGRATION_PACKAGE = "return_platform"
_MIGRATION_PATH = "data_platform/graph/migrations"
_REQUIRED_FULLTEXT_INDEXES = frozenset(
    {
        "customer_name_search_v2",
        "product_description_search_v2",
        # 0016. Listed here for the same reason as the two above: a configured
        # FULLTEXT search whose index is missing or still POPULATING returns
        # nothing and reads to the associate as "no such person", so the run
        # that creates it is the run that has to prove it is ONLINE.
        "contact_name_search_v1",
    }
)


def _statements(text: str) -> tuple[str, ...]:
    without_comments = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )
    return tuple(
        statement.strip() for statement in without_comments.split(";") if statement.strip()
    )


async def main() -> None:
    settings, _resolver = await resolve_runtime_settings_from_vault(Settings())
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
    )
    try:
        last_error: Exception | None = None
        for _ in range(60):
            try:
                await driver.verify_connectivity()
                last_error = None
                break
            except Exception as exc:  # dependency readiness is retried with a bound
                last_error = exc
                await asyncio.sleep(1)
        if last_error is not None:
            raise RuntimeError("Neo4j did not become reachable") from last_error

        await driver.execute_query(
            "CREATE CONSTRAINT uq_configuration_migration_id IF NOT EXISTS "
            "FOR (m:ConfigurationMigration) REQUIRE m.migration_id IS UNIQUE",
            database_=settings.neo4j_database,
        )

        migration_root = files(_MIGRATION_PACKAGE).joinpath(_MIGRATION_PATH)
        migration_names = sorted(
            entry.name
            for entry in migration_root.iterdir()
            if entry.is_file() and entry.name.endswith(".cypher")
        )
        if not migration_names:
            raise RuntimeError("No packaged Neo4j migrations were found")
        for migration_name in migration_names:
            migration = migration_root.joinpath(migration_name)
            migration_text = migration.read_text(encoding="utf-8")
            migration_checksum = hashlib.sha256(migration_text.encode("utf-8")).hexdigest()
            records, _, _ = await driver.execute_query(
                "MATCH (m:ConfigurationMigration {migration_id: $migration_id}) "
                "RETURN m.checksum_sha256 AS checksum_sha256",
                migration_id=migration_name,
                database_=settings.neo4j_database,
            )
            if records:
                applied_checksum = str(records[0]["checksum_sha256"])
                if applied_checksum != migration_checksum:
                    raise RuntimeError(
                        f"Applied migration {migration_name} checksum does not match source"
                    )
                print(f"skipped={migration_name}")
                continue

            for statement in _statements(migration_text):
                await driver.execute_query(statement, database_=settings.neo4j_database)
            await driver.execute_query(
                "CREATE (m:ConfigurationMigration {"
                "migration_id: $migration_id, checksum_sha256: $checksum_sha256, "
                "applied_at: datetime()})",
                migration_id=migration_name,
                checksum_sha256=migration_checksum,
                database_=settings.neo4j_database,
            )
            print(f"applied={migration_name}")

        records, _, _ = await driver.execute_query(
            "SHOW INDEXES YIELD name, state WHERE name IN $required_names RETURN name, state",
            required_names=sorted(_REQUIRED_FULLTEXT_INDEXES),
            database_=settings.neo4j_database,
        )
        states = {str(record["name"]): str(record["state"]) for record in records}
        if set(states) != _REQUIRED_FULLTEXT_INDEXES or any(
            state != "ONLINE" for state in states.values()
        ):
            raise RuntimeError(f"Required full-text indexes are not ONLINE: {states}")
        print("neo4j_schema_status=READY")
    finally:
        await driver.close()


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
