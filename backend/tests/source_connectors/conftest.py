"""Real-infrastructure fixtures for the source connector tests.

`tests/conftest.py::test_settings` has always declared `sqlserver_database="test_db"`,
and nothing in this repository has ever created that database: the SQL migrations
run against `return_platform`, and compose has no init script. Every test in this
package therefore errored in setup with pymssql's `Login failed for user 'sa'`,
whose real cause is only visible in the server's own log --

    Login failed for user 'sa'.
    Reason: Failed to open the explicitly specified database 'test_db'.

-- which is why it read as a credentials or driver problem for as long as it did.

The database is created here rather than by pointing the tests at
`return_platform`, because these tests create and drop tables: a throwaway
database is what keeps a connector test from ever being one typo away from
touching the application's own schema.
"""

from __future__ import annotations

from collections.abc import Iterator

import pymssql
import pytest

from return_platform.configuration.settings import Settings


@pytest.fixture
def sqlserver_test_database(test_settings: Settings) -> str:
    """Ensure the throwaway database exists, and leave it in place afterwards.

    Never dropped: two pytest processes against the same server would otherwise
    race, one dropping the database the other is still connected to. Tables are
    per-test and are dropped; an empty database costs nothing to keep.
    """
    name = test_settings.sqlserver_database
    # `master`, because a connection cannot be opened against a database that
    # does not exist yet -- which is the whole failure being fixed.
    with pymssql.connect(
        server=test_settings.sqlserver_host,
        port=str(test_settings.sqlserver_port),
        user=test_settings.sqlserver_user,
        password=test_settings.sqlserver_password.get_secret_value(),
        database="master",
        login_timeout=10,
        timeout=10,
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            # `CREATE DATABASE` cannot be parameterised and is not allowed in a
            # multi-statement batch, so it goes through EXEC with the name bound
            # as a value rather than concatenated from anything caller-supplied.
            cursor.execute(
                "IF DB_ID(%(name)s) IS NULL EXEC('CREATE DATABASE [' + %(name)s + ']')",
                {"name": name},
            )
    return name


@pytest.fixture(autouse=True)
def _sqlserver_database_exists(request: pytest.FixtureRequest) -> Iterator[None]:
    """Create the database before any test in this package builds a connection.

    Autouse and request-driven rather than declared per test: the failure it
    prevents is a *setup* error inside another fixture, which no test body ever
    reaches in order to declare a dependency of its own.
    """
    if "test_settings" in request.fixturenames:
        request.getfixturevalue("sqlserver_test_database")
    yield
