"""ANZ-02: scope refuses object C *before* the connector is touched, and the
analyzer cannot write to a source at all.

`test_source_scope.py` proves the grant object refuses correctly, which is the
static half and was already verified. The half that was missing is adversarial
and about *ordering*: a wrapper that called the connector and then discarded the
result would satisfy every assertion in that file while having already read the
data. The refusal has to happen first, and "first" is only provable by making
any connector call at all a failure.

Hence the tripwire. `_Tripwire` satisfies `SourceInspectionPort` and every one of
its methods raises. Nothing in it can succeed, so a test that ends in
`ScopeViolation` rather than in `_ConnectorReached` is a test in which the
connector was never reached -- and one that ends in `_ConnectorReached` names the
method that got through.

The second half is RG-11's read-only requirement. Source systems are strictly
read-only to this analyzer: it may propose graph-target schema, indexes,
constraints, mappings, transformations and sync configuration, and may never
issue source-side ALTER, CREATE INDEX, INSERT, UPDATE, DELETE, or any other DDL
or DML. That is asserted structurally -- against the port surface, which is the
only way in -- rather than by inspecting call sites, because a call site that
does not exist today is not a guarantee about tomorrow.
"""

from __future__ import annotations

import ast
import inspect
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from return_platform.graph_schema_analyzer.application.source_inspection import (
    build_scoped_source_inspection,
)
from return_platform.graph_schema_analyzer.domain.errors import ScopeViolation
from return_platform.graph_schema_analyzer.domain.source_scope import (
    InspectionScope,
    ObjectScope,
    SourceScope,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    SourceDiscoveryPort,
    SourceInspectionPort,
)

#: The selected objects. A and B are granted; C is the adversary's target and is
#: deliberately a real-looking name in the *same* source, because the interesting
#: failure is a caller that is inside the granted source and reaches one object
#: further -- not one that guesses a source nobody configured.
GRANTED_SOURCE = "warehouse"
OBJECT_A = "dbo.bay"
OBJECT_B = "dbo.warehouse"
OBJECT_C = "dbo.salary"
UNGRANTED_SOURCE = "finance"


class _ConnectorReached(AssertionError):
    """The connector was called. For these tests that is the failure itself."""


class _Tripwire:
    """Satisfies `SourceInspectionPort`; every method is a failure.

    Not a recorder that is asserted on afterwards: raising means the failure is
    attributed to the call that made it, and a wrapper that swallowed the
    exception and returned a default would still fail, because `calls` is checked
    too.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def _tripped(self, method: str) -> Any:
        self.calls.append(method)
        raise _ConnectorReached(
            f"scope let {method!r} reach the connector before refusing {OBJECT_C!r}"
        )

    async def validate(self, *, source_id: str) -> Any:
        return self._tripped("validate")

    async def list_sources(self) -> Sequence[str]:
        return self._tripped("list_sources")

    async def list_objects(self, *, source_id: str) -> Any:
        return self._tripped("list_objects")

    async def describe_object(self, *, source_id: str, object_name: str) -> Any:
        return self._tripped("describe_object")

    async def sample(
        self,
        *,
        source_id: str,
        object_name: str,
        limit: int,
        fields: Sequence[str] | None = None,
    ) -> Sequence[Mapping[str, Any]]:
        return self._tripped("sample")

    async def profile(self, *, source_id: str, object_name: str, sample_size: int) -> Any:
        return self._tripped("profile")

    async def list_indexes(self, *, source_id: str, object_name: str) -> Any:
        return self._tripped("list_indexes")

    async def list_relationships(self, *, source_id: str, object_name: str | None = None) -> Any:
        return self._tripped("list_relationships")


def _scoped() -> tuple[Any, _Tripwire]:
    tripwire = _Tripwire()
    scope = InspectionScope(
        sources=(
            SourceScope(
                source_id=GRANTED_SOURCE,
                objects=(
                    ObjectScope(object_name=OBJECT_A, fields=frozenset({"bay_id", "aisle"})),
                    ObjectScope(object_name=OBJECT_B),
                ),
                max_sample_rows=5,
            ),
        )
    )
    return build_scoped_source_inspection(tripwire, scope=scope), tripwire


# --- object C, every way in --------------------------------------------------


@pytest.mark.asyncio
async def test_sampling_object_c_never_reaches_the_connector() -> None:
    """The one that matters most: `sample` is the method that returns values."""
    scoped, tripwire = _scoped()

    with pytest.raises(ScopeViolation):
        await scoped.sample(source_id=GRANTED_SOURCE, object_name=OBJECT_C, limit=10)

    assert tripwire.calls == []


@pytest.mark.asyncio
async def test_describing_object_c_never_reaches_the_connector() -> None:
    """Structure is disclosure too. Being told what columns `dbo.salary` has is
    the disclosure the object grant was drawn to prevent, even with no rows."""
    scoped, tripwire = _scoped()

    with pytest.raises(ScopeViolation):
        await scoped.describe_object(source_id=GRANTED_SOURCE, object_name=OBJECT_C)

    assert tripwire.calls == []


@pytest.mark.asyncio
async def test_profiling_object_c_never_reaches_the_connector() -> None:
    """`profile` returns only statistics, which is exactly why it is worth
    testing: it looks harmless, and a null rate over an ungranted column is still
    computed from that column."""
    scoped, tripwire = _scoped()

    with pytest.raises(ScopeViolation):
        await scoped.profile(source_id=GRANTED_SOURCE, object_name=OBJECT_C, sample_size=10)

    assert tripwire.calls == []


@pytest.mark.asyncio
async def test_listing_indexes_of_object_c_never_reaches_the_connector() -> None:
    scoped, tripwire = _scoped()

    with pytest.raises(ScopeViolation):
        await scoped.list_indexes(source_id=GRANTED_SOURCE, object_name=OBJECT_C)

    assert tripwire.calls == []


@pytest.mark.asyncio
async def test_listing_relationships_of_object_c_never_reaches_the_connector() -> None:
    scoped, tripwire = _scoped()

    with pytest.raises(ScopeViolation):
        await scoped.list_relationships(source_id=GRANTED_SOURCE, object_name=OBJECT_C)

    assert tripwire.calls == []


# --- an ungranted source, which is the coarser version of the same thing ------


@pytest.mark.asyncio
async def test_an_ungranted_source_is_refused_before_any_call() -> None:
    """`list_objects` and `validate` legitimately call the connector for a source
    that *is* granted, so the adversarial case for them is the source level."""
    scoped, tripwire = _scoped()

    for call in (
        scoped.list_objects(source_id=UNGRANTED_SOURCE),
        scoped.validate(source_id=UNGRANTED_SOURCE),
        scoped.sample(source_id=UNGRANTED_SOURCE, object_name=OBJECT_A, limit=1),
        scoped.describe_object(source_id=UNGRANTED_SOURCE, object_name=OBJECT_A),
    ):
        with pytest.raises(ScopeViolation):
            await call

    assert tripwire.calls == []


@pytest.mark.asyncio
async def test_an_empty_field_grant_refuses_before_building_a_projection() -> None:
    """An object granted with no readable field must be refused, not turned into
    an empty column list -- which reaches a SQL backend as `SELECT  FROM ...` and
    surfaces to the operator as a syntax error rather than as a scope refusal."""
    tripwire = _Tripwire()
    scope = InspectionScope(
        sources=(
            SourceScope(
                source_id=GRANTED_SOURCE,
                objects=(ObjectScope(object_name=OBJECT_A, fields=frozenset()),),
                max_sample_rows=5,
            ),
        )
    )
    scoped = build_scoped_source_inspection(tripwire, scope=scope)

    with pytest.raises(ScopeViolation):
        await scoped.sample(source_id=GRANTED_SOURCE, object_name=OBJECT_A, limit=1)

    assert tripwire.calls == []


@pytest.mark.asyncio
async def test_a_granted_object_still_reaches_the_connector() -> None:
    """The control. Without it every test above would pass against a wrapper that
    refused everything, which would prove nothing about scope."""
    scoped, tripwire = _scoped()

    with pytest.raises(_ConnectorReached):
        await scoped.sample(source_id=GRANTED_SOURCE, object_name=OBJECT_A, limit=1)

    assert tripwire.calls == ["sample"]


# --- RG-11: the source surface cannot express a write ------------------------

#: Verbs that would mutate a source system. Matched against port method names.
MUTATION_VERBS = (
    "alter",
    "create",
    "drop",
    "insert",
    "update",
    "delete",
    "truncate",
    "upsert",
    "merge",
    "write",
    "execute",
    "run",
    "ddl",
    "dml",
    "grant",
    "revoke",
)


def _port_methods(port: type) -> list[str]:
    return [
        name
        for name, member in inspect.getmembers(port)
        if not name.startswith("_")
        and (inspect.isfunction(member) or inspect.iscoroutinefunction(member))
    ]


@pytest.mark.parametrize("port", [SourceInspectionPort, SourceDiscoveryPort])
def test_no_source_port_method_can_mutate_a_source(port: type) -> None:
    """Read-only as a structural fact rather than a rule someone remembers.

    The ports are the analyzer's only way to reach a source, so a surface with no
    mutating method is a surface on which a mutating call cannot be written --
    which is a stronger statement than "no call site does this today".
    """
    offenders = [
        name
        for name in _port_methods(port)
        if any(name == verb or name.startswith(f"{verb}_") for verb in MUTATION_VERBS)
    ]
    assert not offenders, (
        f"{port.__name__} exposes {offenders}; source systems are read-only to the "
        "analyzer, which may only propose graph-target schema, indexes, constraints, "
        "mappings, transformations and sync configuration"
    )


def test_the_analyzer_emits_no_source_side_ddl_or_dml() -> None:
    """The complement to the port check: nothing in the package writes the SQL a
    missing port method would otherwise be needed for.

    Scoped to statements aimed at a *source*. The analyzer legitimately composes
    graph-target DDL -- that is its output, and `domain/graph_ddl.py` is where it
    lives -- so the graph target's own compiler is excluded by name rather than
    by weakening the pattern.
    """
    analyzer_dir = (
        Path(__file__).resolve().parents[2] / "src" / "return_platform" / "graph_schema_analyzer"
    )
    graph_target_files = {"graph_ddl.py", "graph_target_port.py"}
    forbidden = (
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "ALTER TABLE",
        "TRUNCATE ",
        "DROP TABLE",
        "CREATE INDEX",
    )

    offenders: list[tuple[str, str]] = []
    for path in sorted(analyzer_dir.rglob("*.py")):
        if path.name in graph_target_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # String *constants* only, and only ones that BEGIN with the
            # statement. A docstring explaining that these are forbidden
            # mentions them mid-sentence; an executable query starts with the
            # verb. Matching "contains" instead flagged the prose "tell an
            # update from a delete", which is the opposite of a finding.
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            literal = node.value.strip().upper()
            if any(literal.startswith(statement) for statement in forbidden):
                offenders.append((path.name, node.value.strip()[:80]))
    assert not offenders, f"source-side DDL/DML found in the analyzer: {offenders}"
