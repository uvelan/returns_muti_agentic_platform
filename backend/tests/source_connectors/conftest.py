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
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import pymssql
import pytest

from return_platform.configuration.settings import Settings

#: Wall-clock bound on opening the `master` connection below, enforced outside
#: the driver.
#:
#: `login_timeout` is not sufficient, and that is not a theory. On 2026-08-13 the
#: SQL Server container died mid-run with a non-yielding-scheduler assertion
#: (`sosschedmon.cpp:202`) and began writing a core dump. A crashed `sqlservr`
#: leaves the container's PID 1 alive, so the port stays *open* and nothing
#: restarts it: TCP connect succeeds, the prelogin response never comes, and
#: `login_timeout` -- which bounds reaching the server, not hearing back from it
#: -- never fires. `pymssql.connect` blocked forever, this autouse fixture runs
#: before every test in the package, and the whole 2,637-test suite stopped dead
#: at 50% on ~0% CPU. That had happened three times before and been recorded as
#: an undiagnosed hang; `pytest --timeout` finally caught the stack in this
#: exact call.
#:
#: Generous on purpose. A healthy server answers in milliseconds, so this only
#: has to be shorter than "forever" to convert a wedged suite into one named
#: failure per test in this package.
_CONNECT_DEADLINE_SECONDS = 30


def _connect_to_master(settings: Settings) -> pymssql.Connection:
    # `master`, because a connection cannot be opened against a database that
    # does not exist yet -- which is the whole failure being fixed.
    return pymssql.connect(
        server=settings.sqlserver_host,
        port=str(settings.sqlserver_port),
        user=settings.sqlserver_user,
        password=settings.sqlserver_password.get_secret_value(),
        database="master",
        login_timeout=10,
        timeout=10,
        autocommit=True,
    )


@pytest.fixture
def sqlserver_test_database(test_settings: Settings) -> str:
    """Ensure the throwaway database exists, and leave it in place afterwards.

    Never dropped: two pytest processes against the same server would otherwise
    race, one dropping the database the other is still connected to. Tables are
    per-test and are dropped; an empty database costs nothing to keep.
    """
    name = test_settings.sqlserver_database
    # The connect runs on a worker thread only so that the wait can be
    # abandoned. The thread itself cannot be cancelled -- it is blocked inside a
    # C extension -- so it is left to leak deliberately rather than joined: one
    # stranded thread per test in this package is the price of the suite
    # reaching its own summary, and the process is failing this package anyway.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlserver-connect")
    future = executor.submit(_connect_to_master, test_settings)
    try:
        connection = future.result(timeout=_CONNECT_DEADLINE_SECONDS)
    except FutureTimeoutError:
        raise RuntimeError(
            f"SQL Server at {test_settings.sqlserver_host}:{test_settings.sqlserver_port} "
            f"accepted a connection but did not complete login within "
            f"{_CONNECT_DEADLINE_SECONDS}s. The port being open does not mean the server is "
            "alive -- check `docker logs` for a fatal error and whether sqlservr is writing a "
            "core dump, then restart it with `docker stop -t 60` and `docker start` "
            "(`docker restart` has been observed to wedge it in startup)."
        ) from None
    finally:
        executor.shutdown(wait=False)

    with connection:
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
