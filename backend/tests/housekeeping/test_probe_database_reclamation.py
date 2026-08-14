"""`DROP DATABASE` is the most destructive statement in this platform.

The rules that guard it: hard-gated to development and test, a positive suffix
test, the application's own databases refused against that suffix at construction,
and an identifier allowlist at the point the name is composed into DDL.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from return_platform.housekeeping.probe_databases import (
    SYSTEM_DATABASES,
    ProbeDatabaseReclaimer,
)
from return_platform.source_connectors.identifiers import UnsafeIdentifierError

_APPLICATION_DATABASE = "return_platform"


class _Cursor:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def execute(self, statement: str, *_: Any) -> None:
        self._connection.statements.append(statement)
        if statement.startswith("DROP DATABASE"):
            self._connection.instance.dropped.append(statement)
            if statement in self._connection.instance.refuse:
                raise RuntimeError("database is currently in use")

    def fetchall(self) -> list[tuple[str, datetime | None]]:
        return list(self._connection.instance.databases)


class _Connection:
    def __init__(self, instance: _Instance) -> None:
        self.instance = instance
        self.statements: list[str] = []

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def close(self) -> None:
        return None


class _Instance:
    def __init__(
        self,
        databases: list[tuple[str, datetime | None]],
        *,
        refuse: set[str] | None = None,
    ) -> None:
        self.databases = databases
        self.dropped: list[str] = []
        self.refuse = refuse or set()

    def connect(self, database: str) -> _Connection:
        return _Connection(self)


async def _to_thread(func: Any, *args: Any) -> Any:
    return func(*args)


def _reclaimer(
    instance: _Instance,
    *,
    environment: str = "test",
    suffixes: tuple[str, ...] = ("_probe",),
    minimum_age_seconds: float = 3_600,
    protected: tuple[str | None, ...] = (_APPLICATION_DATABASE, None),
) -> ProbeDatabaseReclaimer:
    return ProbeDatabaseReclaimer(
        connect=instance.connect,
        to_thread=_to_thread,
        environment=environment,
        protected_database_names=protected,
        name_suffixes=suffixes,
        minimum_age_seconds=minimum_age_seconds,
        batch_limit=50,
    )


def _old() -> datetime:
    return datetime.now(UTC) - timedelta(days=3)


@pytest.mark.asyncio
async def test_the_probe_databases_the_suites_leave_behind_are_dropped() -> None:
    """The five names measured on this deployment."""
    names = [
        "return_case_probe",
        "return_pool_probe",
        "return_shipment_probe",
        "return_shipment_graph_probe",
        "return_shipment_concurrency_probe",
    ]
    instance = _Instance([(name, _old()) for name in names])
    outcome = await _reclaimer(instance).reclaim_once()

    assert outcome.reclaimed == 5
    assert sorted(outcome.reclaimed_ids) == sorted(names)
    for name in names:
        assert f"DROP DATABASE [{name}]" in instance.dropped


@pytest.mark.asyncio
async def test_the_application_database_is_never_dropped() -> None:
    instance = _Instance([(_APPLICATION_DATABASE, _old()), ("return_case_probe", _old())])
    outcome = await _reclaimer(instance).reclaim_once()

    assert outcome.reclaimed_ids == ("return_case_probe",)
    assert all(_APPLICATION_DATABASE not in statement for statement in instance.dropped)


@pytest.mark.asyncio
@pytest.mark.parametrize("system", sorted(SYSTEM_DATABASES))
async def test_a_system_database_is_never_dropped(system: str) -> None:
    """Protected even if it somehow reached the candidate list.

    `sys.databases` is filtered by `database_id > 4` as well, so this is the
    second of two independent reasons -- and the one that does not depend on SQL
    Server's own numbering.
    """
    instance = _Instance([(system, _old())])
    reclaimer = _reclaimer(instance)

    assert reclaimer.is_reclaimable(system) is False
    outcome = await reclaimer.reclaim_once()
    assert instance.dropped == []
    assert outcome.reclaimed == 0


@pytest.mark.parametrize(
    "suffix",
    ["", "m", "_platform", "return_platform", "master", "b"],
)
def test_a_suffix_matching_a_protected_database_produces_no_reclaimer(suffix: str) -> None:
    """Configuration cannot name a database this platform reads or writes.

    `"b"` is in the list because `msdb` ends with it -- a suffix rule is only as
    safe as the check that no protected name satisfies it.
    """
    with pytest.raises(ValueError):
        _reclaimer(_Instance([]), suffixes=(suffix,))


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", ["staging", "production"])
async def test_dropping_is_hard_gated_outside_development_and_test(environment: str) -> None:
    """Production has no probe databases, so a reclaimer there has only downside."""
    instance = _Instance([("return_case_probe", _old())])
    reclaimer = _reclaimer(instance, environment=environment)

    assert reclaimer.enabled is False
    outcome = await reclaimer.reclaim_once()
    assert instance.dropped == []
    assert outcome.ran is False


@pytest.mark.asyncio
async def test_a_probe_database_created_moments_ago_is_left_for_the_running_suite() -> None:
    instance = _Instance([("return_case_probe", datetime.now(UTC))])
    outcome = await _reclaimer(instance).reclaim_once()

    assert instance.dropped == []
    assert outcome.details["within_minimum_age"] == 1


@pytest.mark.asyncio
async def test_a_database_in_use_survives_and_is_retried_next_pass() -> None:
    """No `SET SINGLE_USER WITH ROLLBACK IMMEDIATE`, deliberately.

    The one thing holding connections to a probe database is a suite running
    against it. Forcing the drop would let housekeeping fail a running test.
    """
    statement = "DROP DATABASE [return_shipment_probe]"
    instance = _Instance(
        [("return_shipment_probe", _old()), ("return_case_probe", _old())],
        refuse={statement},
    )
    outcome = await _reclaimer(instance).reclaim_once()

    assert outcome.failed == 1
    assert outcome.reclaimed_ids == ("return_case_probe",)


@pytest.mark.asyncio
async def test_a_name_that_is_not_a_safe_identifier_is_refused_not_quoted() -> None:
    """The name goes into bracketed DDL, which cannot be parameterized.

    A database literally named `evil]; DROP DATABASE [return_platform` would end
    the bracket. It is refused by the shared allowlist rather than escaped here.
    """
    hostile = "evil]; DROP DATABASE [return_platform_probe"
    instance = _Instance([(hostile, _old())])
    reclaimer = _reclaimer(instance)
    outcome = await reclaimer.reclaim_once()

    assert instance.dropped == []
    assert outcome.details["unsafe_name"] == 1
    with pytest.raises(UnsafeIdentifierError):
        reclaimer._drop_database(hostile)  # noqa: SLF001 - the guard is the assertion
