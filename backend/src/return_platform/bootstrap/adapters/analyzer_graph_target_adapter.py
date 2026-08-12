"""Binds the analyzer's `GraphTargetPort` to Neo4j.

**This module is the compiler the design doc means** when it says "the compiler --
never the model -- turns validated structures into graph operations". The model
emits typed `MutationCommand`s; those become a `GraphSchemaShape`; this file is
the only place that shape becomes Cypher.

Two properties make that safe rather than merely stated:

* **Identifiers are re-validated here**, not trusted. They were already
  pattern-constrained when the mutation was parsed, but this is the last gate
  before string interpolation, and a compiler that assumes its input was
  validated upstream is one refactor away from not being. Anything failing the
  identifier pattern raises instead of being escaped.
* **Only DDL for the graph target is emitted** -- `CREATE INDEX` and
  `CREATE CONSTRAINT`. There is no code path here that writes to, or emits any
  statement for, a source system; source systems are read-only to the analyzer.

`compile_schema` returns statements rather than executing them, so validation can
ask "would this compile" without touching the database.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from neo4j import AsyncDriver

from return_platform.bootstrap.adapters.analyzer_release_compiler import (
    ReleaseCompilationError,
    compile_active_schema,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.release_store import (
    ReleaseAlreadyPublished,
    SchemaReleaseStore,
)
from return_platform.graph_schema_analyzer.ports.graph_target_port import (
    BuildHandle,
    GraphTargetPort,
)

__all__ = ["Neo4jGraphTargetAdapter", "build_neo4j_graph_target_adapter"]

logger = logging.getLogger(__name__)

# The same shape the mutation commands enforce. Duplicated deliberately: this is
# a defence-in-depth boundary, and a compiler that imports its validation from
# the layer it is defending against is not a second gate.
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class GraphCompilationError(RuntimeError):
    """A shape that cannot be turned into safe Cypher."""


class Neo4jGraphTargetAdapter:
    """Structurally satisfies `GraphTargetPort`."""

    def __init__(
        self,
        driver: AsyncDriver,
        *,
        releases: SchemaReleaseStore | None = None,
        baseline_path: Path | None = None,
    ) -> None:
        self._driver = driver
        # Optional so the adapter still binds where publishing has no store to
        # write to. `publish_release` refuses in that case rather than the
        # whole analyzer failing to start: validation and compilation are
        # useful on their own.
        self._releases = releases
        self._baseline_path = baseline_path

    async def compile_schema(self, *, draft: Mapping[str, Any]) -> Sequence[str]:
        """Turn a validated shape into graph-side DDL. Never executes it."""
        return compile_graph_ddl(draft)

    async def validate_schema(self, *, draft: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        """Graph-side feasibility findings.

        Uses Neo4j's own `EXPLAIN` so the *database* decides whether a statement
        is valid, rather than this adapter re-implementing Cypher's grammar and
        drifting from it. A driver error is returned as a finding, not raised:
        the analyzer's ValidationService distinguishes "found problems" from
        "could not evaluate" and needs the former shape here.
        """
        try:
            statements = compile_graph_ddl(draft)
        except GraphCompilationError as exc:
            return ({"element": "draft", "message": str(exc)},)

        findings: list[Mapping[str, Any]] = []
        async with self._driver.session() as session:
            for statement in statements:
                try:
                    await session.run(f"EXPLAIN {statement}")
                except Exception as exc:  # noqa: BLE001 - any rejection is a finding
                    findings.append(
                        {
                            "element": _element_of(statement),
                            "message": f"the graph target rejected this definition: {exc}",
                        }
                    )
        return tuple(findings)

    async def request_build(self, *, schema_id: str, activate: bool) -> BuildHandle:
        """Not implemented until C4 owns the generation lifecycle.

        Refuses rather than half-building. The analyzer already treats a build as
        something it delegates and never performs, so an adapter that quietly
        did something partial here would be worse than one that says no.
        """
        raise NotImplementedError(
            "graph generation lifecycle (build/fence/activate/drain/retire) is Wave C4; "
            "the analyzer delegates builds and never performs them."
        )

    async def publish_release(
        self,
        *,
        draft: Mapping[str, Any],
        draft_id: str,
        approver: str,
        activate: bool,
    ) -> BuildHandle:
        """Compile the approved shape into an `ActiveSchema` and store it.

        `accepted=False` with a reason rather than an exception for a shape
        that cannot compile: the analyst needs to read which element was
        ambiguous, and a 500 would tell them only that something went wrong.
        """
        if self._releases is None or self._baseline_path is None:
            raise RuntimeError("no release store is configured, so a schema cannot be published")

        now = datetime.now(UTC)
        # The release id carries the draft it came from and the moment it was
        # cut. Derived rather than random so a release can be traced back to an
        # approval without a lookup table, and unique so an immutable store
        # never has to reject an honest second publish of a changed draft.
        release_id = f"draft_{draft_id.replace('-', '_')}_{now.strftime('%Y%m%d%H%M%S')}"
        try:
            release = compile_active_schema(
                draft,
                baseline=load_active_schema(self._baseline_path),
                configuration_release_id=release_id,
                schema_version=release_id,
                approved_by=approver,
                approved_at=now,
            )
        except (ReleaseCompilationError, ValueError) as exc:
            return BuildHandle(generation_id=release_id, accepted=False, detail=str(exc))

        try:
            await self._releases.publish(release, published_by=approver)
        except ReleaseAlreadyPublished as exc:
            return BuildHandle(generation_id=release_id, accepted=False, detail=str(exc))
        if activate:
            await self._releases.activate(release_id)
        return BuildHandle(
            generation_id=release_id,
            accepted=True,
            detail="activated" if activate else "published",
        )


def compile_graph_ddl(draft: Mapping[str, Any]) -> tuple[str, ...]:
    """The whole model-output-to-statement boundary, in one pure function.

    Pure and importable on its own so it can be tested exhaustively without a
    database -- which matters more here than anywhere else in the module.
    """
    statements: list[str] = []

    entities: Mapping[str, Mapping[str, Any]] = draft.get("entities", {}) or {}
    for label, entity in sorted(entities.items()):
        _require_identifier(label, "entity label")
        identifiers = entity.get("identifier_properties") or []
        for name in identifiers:
            _require_identifier(name, f"identifier property of {label}")
        if identifiers:
            # An identifier is what sync matches on, so it must be unique or
            # every run risks duplicating nodes.
            joined = ", ".join(f"n.{name}" for name in identifiers)
            statements.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE ({joined}) IS UNIQUE"
            )

    for item in draft.get("graph_constraints", ()) or ():
        label = str(item.get("label", ""))
        name = str(item.get("property_name", ""))
        _require_identifier(label, "constraint label")
        _require_identifier(name, "constraint property")
        if item.get("unique"):
            statements.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{name} IS UNIQUE"
            )
        if item.get("required"):
            statements.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.{name} IS NOT NULL"
            )

    for item in draft.get("graph_indexes", ()) or ():
        label = str(item.get("label", ""))
        _require_identifier(label, "index label")
        properties = [str(name) for name in item.get("properties", ())]
        for name in properties:
            _require_identifier(name, f"index property of {label}")
        if properties:
            joined = ", ".join(f"n.{name}" for name in properties)
            statements.append(f"CREATE INDEX IF NOT EXISTS FOR (n:{label}) ON ({joined})")

    return tuple(statements)


def _require_identifier(value: str, what: str) -> None:
    if not _IDENTIFIER.match(value):
        raise GraphCompilationError(
            f"{what} {value!r} is not a valid identifier; refusing to compile it into Cypher."
        )


def _element_of(statement: str) -> str:
    match = re.search(r"FOR \(n:([A-Za-z][A-Za-z0-9_]*)\)", statement)
    return match.group(1) if match else "draft"


def build_neo4j_graph_target_adapter(
    driver: AsyncDriver,
    *,
    releases: SchemaReleaseStore | None = None,
    baseline_path: Path | None = None,
) -> GraphTargetPort:
    """Typed factory; the annotation is what proves conformance to mypy."""
    return Neo4jGraphTargetAdapter(driver, releases=releases, baseline_path=baseline_path)
