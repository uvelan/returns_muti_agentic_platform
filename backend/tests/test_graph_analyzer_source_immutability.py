"""Source systems are read-only, enforced by shape rather than by inspection.

The sibling suites check that a mutating statement is *rejected*. These check
something stronger and cheaper to keep true: that the analyzer has nowhere to
put one. A rejection can be bypassed by a caller that does not go through the
checker; an absent capability cannot.

Each test names the specific way a source write could have re-entered the
system, because "the connector is read-only" is only a useful claim if the
routes by which it could stop being read-only are enumerated.
"""

from __future__ import annotations

import inspect
from typing import get_args, get_type_hints

import pytest
from pydantic import ValidationError

from return_platform.graph_analyzer import analysis, discovery
from return_platform.graph_analyzer.agent_port import (
    AgentAnswer,
    ProposedGraphOperation,
)
from return_platform.graph_schema_analyzer.ports.source_port import SourceInspectionPort

#: Every verb that would change a source, in any of the four dialects.
MUTATING_VERBS = (
    "insert",
    "update",
    "delete",
    "upsert",
    "merge",
    "alter",
    "drop",
    "truncate",
    "create",
    "replace",
    "write",
    "save",
    "remove",
    "set_",
    "execute",
    "run_query",
    "command",
)


def test_the_source_port_exposes_no_mutating_method() -> None:
    """The control is the Protocol's shape.

    Every source read in the analyzer goes through `SourceInspectionPort`. If it
    grew a `write`, an `execute`, or any general query method, every other
    guarantee here would be bypassable through it -- so the absence of one is
    the guarantee, and this test is what keeps it absent.
    """
    methods = {
        name
        for name in dir(SourceInspectionPort)
        if not name.startswith("_") and callable(getattr(SourceInspectionPort, name, None))
    }
    offenders = sorted(
        name for name in methods if any(verb in name.casefold() for verb in MUTATING_VERBS)
    )
    assert offenders == [], (
        "SourceInspectionPort gained a method that could change a source: "
        f"{offenders}. Source connectors are read-only by shape."
    )


def test_the_source_port_takes_no_caller_supplied_query() -> None:
    """A `query: str` parameter would be a mutation path with a read-only name."""
    offenders: list[str] = []
    for name in dir(SourceInspectionPort):
        if name.startswith("_"):
            continue
        member = getattr(SourceInspectionPort, name, None)
        if not callable(member):
            continue
        try:
            signature = inspect.signature(member)
        except (TypeError, ValueError):  # pragma: no cover - builtins
            continue
        for parameter in signature.parameters.values():
            if parameter.name in {"query", "statement", "sql", "cypher", "pipeline", "command"}:
                offenders.append(f"{name}.{parameter.name}")
    assert offenders == [], f"SourceInspectionPort accepts a caller-supplied statement: {offenders}"


def test_discovery_reaches_sources_only_through_the_read_only_port() -> None:
    """Discovery must not open its own cursor beside the port it was given.

    The module builds driver handles -- that is its job -- but every *read* has
    to go through the adapter. A `.find(`, `.execute(` or `session.run(` here
    would be a second path that no port controls.
    """
    source = inspect.getsource(discovery)
    for forbidden in (".execute(", ".insert_one(", ".update_one(", ".delete_one(", "session.run("):
        assert forbidden not in source, (
            f"discovery.py performs a direct driver call {forbidden!r} instead of "
            "using SourceInspectionPort"
        )


def test_analysis_reaches_sources_only_through_the_read_only_port() -> None:
    source = inspect.getsource(analysis)
    for forbidden in (".execute(", ".insert_one(", ".update_one(", ".delete_one(", "session.run("):
        assert forbidden not in source, (
            f"analysis.py performs a direct driver call {forbidden!r} instead of "
            "using SourceInspectionPort"
        )


def test_an_agent_operation_can_only_target_the_system_graph() -> None:
    """The target is a one-member Literal, so a source target cannot be built."""
    hints = get_type_hints(ProposedGraphOperation)
    assert get_args(hints["target"]) == ("SYSTEM_GRAPH",)


@pytest.mark.parametrize(
    "target",
    ["SOURCE", "SOURCE_SYSTEM", "POSTGRESQL", "MONGODB", "EXTERNAL_GRAPH", "system_graph"],
)
def test_an_agent_operation_naming_a_source_target_is_refused(target: str) -> None:
    with pytest.raises(ValidationError):
        ProposedGraphOperation(type="ADD_SYSTEM_GRAPH_INDEX", objectId="e1", target=target)


@pytest.mark.parametrize(
    "operation",
    [
        "CREATE_SOURCE_INDEX",
        "ALTER_SOURCE_TABLE",
        "DROP_SOURCE_CONSTRAINT",
        "CREATE_INDEX",
        "UPDATE_SOURCE_RECORDS",
        "NORMALIZE_SOURCE",
    ],
)
def test_the_agent_cannot_express_a_source_side_operation(operation: str) -> None:
    """The operation type is a closed set covering only system-graph edits.

    An open string would let a model name an operation nobody implemented, and
    the natural way to express one is a statement to run.
    """
    with pytest.raises(ValidationError):
        ProposedGraphOperation(type=operation, objectId="e1")


def test_every_agent_operation_type_names_the_system_graph() -> None:
    """Naming is the last line: an operation called CREATE_INDEX would read as
    source-side to anyone auditing a stored recommendation."""
    hints = get_type_hints(ProposedGraphOperation)
    for name in get_args(hints["type"]):
        assert "SYSTEM_GRAPH" in name, f"{name} does not state that it targets the system graph"


def test_an_agent_answer_cannot_carry_an_executable_statement() -> None:
    """`extra="forbid"` is what stops a model returning a `cypher` or `sql` key."""
    with pytest.raises(ValidationError):
        AgentAnswer(message="ok", cypher="MATCH (n) DETACH DELETE n")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        AgentAnswer(message="ok", sql="DROP TABLE orders")  # type: ignore[call-arg]


def test_a_deterministic_proposal_maps_only_onto_discovered_evidence() -> None:
    """A proposal names source objects as *mappings*, never as write targets."""
    evidence = [
        analysis.ObjectEvidence(
            object_id="src1:public.orders",
            source_id="src1",
            source_name="Warehouse",
            engine="POSTGRESQL",
            object_name="public.orders",
            fields=({"name": "order_id", "type": "text", "nullable": False},),
            identifier_fields=("order_id",),
            indexed_fields=("order_id",),
            relationships=(),
            approximate_rows=10,
        )
    ]
    entities, relationships = analysis.deterministic_proposal(evidence)

    assert len(entities) == 1
    assert relationships == []
    entity = entities[0]
    # The mapping points at the source; the entity itself is system-graph state.
    assert entity.properties[0].sourceObjectId == "src1:public.orders"
    assert entity.properties[0].sourceField == "order_id"
    assert entity.properties[0].identifier is True
    assert entity.change == "ADDED"


def test_a_relationship_is_proposed_only_where_the_source_declares_one() -> None:
    """Nothing is inferred from two fields happening to share a name.

    A guessed edge presented beside a declared one is indistinguishable to the
    validation that treats declared edges as fact.
    """
    shared_field = ({"name": "customer_id", "type": "text", "nullable": False},)
    evidence = [
        analysis.ObjectEvidence(
            object_id="src1:orders",
            source_id="src1",
            source_name="W",
            engine="POSTGRESQL",
            object_name="orders",
            fields=shared_field,
            identifier_fields=("customer_id",),
            indexed_fields=(),
            relationships=(),
            approximate_rows=None,
        ),
        analysis.ObjectEvidence(
            object_id="src1:customers",
            source_id="src1",
            source_name="W",
            engine="POSTGRESQL",
            object_name="customers",
            fields=shared_field,
            identifier_fields=("customer_id",),
            indexed_fields=(),
            relationships=(),
            approximate_rows=None,
        ),
    ]

    _entities, relationships = analysis.deterministic_proposal(evidence)

    assert relationships == [], "a shared field name is not a declared relationship"


def test_a_declared_foreign_key_does_become_a_relationship() -> None:
    """The other half of the rule: what the source declares is fact."""
    evidence = [
        analysis.ObjectEvidence(
            object_id="src1:orders",
            source_id="src1",
            source_name="W",
            engine="POSTGRESQL",
            object_name="orders",
            fields=({"name": "customer_id", "type": "text", "nullable": False},),
            identifier_fields=("customer_id",),
            indexed_fields=(),
            relationships=(
                {
                    "kind": "FOREIGN_KEY",
                    "from_object": "orders",
                    "from_fields": ["customer_id"],
                    "to_object": "customers",
                    "to_fields": ["id"],
                },
            ),
            approximate_rows=None,
        ),
        analysis.ObjectEvidence(
            object_id="src1:customers",
            source_id="src1",
            source_name="W",
            engine="POSTGRESQL",
            object_name="customers",
            fields=({"name": "id", "type": "text", "nullable": False},),
            identifier_fields=("id",),
            indexed_fields=(),
            relationships=(),
            approximate_rows=None,
        ),
    ]

    _entities, relationships = analysis.deterministic_proposal(evidence)

    assert len(relationships) == 1
    assert relationships[0].direction == "OUTBOUND"


def test_an_index_declared_by_the_source_becomes_a_system_graph_index() -> None:
    """Source index metadata is evidence, and the index created is the graph's.

    The distinction the whole feature rests on: reading that PostgreSQL indexes
    `order_id` is allowed and useful; recommending that PostgreSQL index
    anything is not.
    """
    evidence = [
        analysis.ObjectEvidence(
            object_id="src1:orders",
            source_id="src1",
            source_name="W",
            engine="POSTGRESQL",
            object_name="orders",
            fields=(
                {"name": "order_id", "type": "text", "nullable": False},
                {"name": "note", "type": "text", "nullable": True},
            ),
            identifier_fields=("order_id",),
            indexed_fields=("order_id",),
            relationships=(),
            approximate_rows=None,
        )
    ]

    entities, _relationships = analysis.deterministic_proposal(evidence)

    indexed = {prop.name: prop.indexed for prop in entities[0].properties}
    assert indexed == {"order_id": True, "note": False}


def test_an_index_declared_field_missing_from_the_description_is_still_mapped() -> None:
    """MongoDB's `_id` is the case that forces this.

    It is the primary index but is not reported by field inference, so an entity
    built from the description alone had no identifier -- which failed validation
    and crashed sync's identifier lookup.
    """
    merged = discovery.merge_declared_identifiers(
        ({"name": "name", "type": "string", "nullable": True},), ("_id",)
    )

    assert [item["name"] for item in merged] == ["_id", "name"]
    assert merged[0]["nullable"] is False
