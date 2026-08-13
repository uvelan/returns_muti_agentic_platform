"""PostgreSQL connection settings and the one read path this codebase uses.

New in W4.5, alongside the driver itself -- §5A has always required a PostgreSQL
connector and none was installed (audit D8, W5.11). What exists here is
deliberately less than `sqlserver.py`: connection settings and a bounded read, no
scan connector and no cursor handling, because nothing yet syncs from PostgreSQL.
Adding a speculative `SourceScanConnector` would mean a second incremental-cursor
implementation that no test exercises and no source proves correct.

It lives here rather than beside its only caller for the same reason
`sqlserver.py` does: one place in this codebase knows how we connect to each
store, so a second consumer inherits the timeouts, the read-only posture and the
threading stance instead of inventing them.

**Synchronous psycopg in a worker thread, not `AsyncConnection`**, and the reason
is not stylistic. psycopg 3 refuses to run async on asyncio's `ProactorEventLoop`,
which is the default event loop on Windows and is what uvicorn selects there --
so an async connection works in the Linux containers and raises
`InterfaceError: Psycopg cannot use the 'ProactorEventLoop'` on every developer
machine here. Wrapping the sync driver in `asyncio.to_thread` is what
`SqlServerSourceScanConnector` already does with pymssql, behaves identically on
both platforms, and removes an entire class of works-in-CI-only failure.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import psycopg
from psycopg.rows import dict_row

__all__ = ["PostgresConnectionSettings", "run_read_query"]


class PostgresConnectionSettings:
    """Plain connection parameters -- kept separate from Settings so this module
    has no dependency on the application's configuration layer, matching
    `SqlServerConnectionSettings`."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        timeout_seconds: int = 30,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self.timeout_seconds = timeout_seconds

    def conninfo(self) -> str:
        return psycopg.conninfo.make_conninfo(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            dbname=self.database,
            connect_timeout=max(1, int(self.timeout_seconds)),
        )


def _read(
    connection: PostgresConnectionSettings, query: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """The blocking half of `run_read_query`; never called directly.

    The session is opened `read_only` so that a defect anywhere above this line
    is refused by the server rather than by our own discipline: the analyzer's
    contract says source systems are read-only to it (design doc C3.3), and a
    transaction the server will not let write is the cheapest way to make that
    true rather than merely intended.
    """
    with psycopg.connect(connection.conninfo(), autocommit=False, row_factory=dict_row) as conn:
        # An attribute on the connection, not a conninfo option: passing
        # `read_only=` to `connect()` reaches libpq, which rejects it as an
        # unknown parameter. It has to be set before the first statement opens a
        # transaction, which is here.
        conn.read_only = True
        with conn.cursor() as cursor:
            cursor.execute(query, dict(params))
            return [dict(row) for row in cursor.fetchall()]


async def run_read_query(
    connection: PostgresConnectionSettings, query: str, params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """One statement on a fresh read-only connection, rows as dicts.

    Not a general query API. Every statement passed in is composed inside this
    package from validated identifiers, with all values bound as parameters.
    """
    return await asyncio.to_thread(_read, connection, query, params)
