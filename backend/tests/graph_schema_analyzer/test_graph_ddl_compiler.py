"""The model-output-to-Cypher boundary.

`compile_graph_ddl` is the single place a model-derived structure becomes a
statement, so it gets adversarial tests rather than happy-path ones: the failure
mode is not "wrong index", it is "arbitrary Cypher executed against the graph".
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.bootstrap.adapters.analyzer_graph_target_adapter import (
    GraphCompilationError,
    compile_graph_ddl,
)
from return_platform.bootstrap.adapters.analyzer_source_adapter import _infer_fields


def _draft(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "entities": {
            "Order": {
                "label": "Order",
                "properties": {"order_id": {"type": "STRING"}},
                "identifier_properties": ["order_id"],
            }
        },
        "relationships": (),
        "graph_indexes": (),
        "graph_constraints": (),
    }
    base.update(overrides)
    return base


def test_an_identifier_becomes_a_uniqueness_constraint() -> None:
    """Sync matches on the identifier, so without uniqueness every run risks
    duplicating nodes."""
    statements = compile_graph_ddl(_draft())
    assert statements == (
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:Order) REQUIRE (n.order_id) IS UNIQUE",
    )


def test_indexes_and_constraints_compile_to_graph_ddl_only() -> None:
    statements = compile_graph_ddl(
        _draft(
            graph_indexes=({"label": "Order", "properties": ["order_id"]},),
            graph_constraints=(
                {"label": "Order", "property_name": "order_id", "unique": False, "required": True},
            ),
        )
    )
    joined = " ".join(statements)
    assert "CREATE INDEX IF NOT EXISTS FOR (n:Order) ON (n.order_id)" in statements
    assert "IS NOT NULL" in joined
    # Nothing that could touch a source system.
    for forbidden in ("DROP", "DELETE", "MERGE", "INSERT", "UPDATE", "ALTER TABLE"):
        assert forbidden not in joined.upper()


@pytest.mark.parametrize(
    "label",
    [
        "Order) DETACH DELETE n //",
        "Order`",
        "Order;DROP DATABASE neo4j",
        "Order Name",
        "",
        "1Order",
    ],
)
def test_a_malicious_label_is_refused_not_escaped(label: str) -> None:
    """This is the last gate before string interpolation. Refusing is safer than
    escaping: an escaping bug is silent, a refusal is not."""
    draft = _draft(
        entities={label: {"label": label, "properties": {"x": {}}, "identifier_properties": ["x"]}}
    )
    with pytest.raises(GraphCompilationError):
        compile_graph_ddl(draft)


def test_a_malicious_property_name_is_refused() -> None:
    draft = _draft(
        graph_indexes=({"label": "Order", "properties": ["order_id) YIELD x MATCH (m"]},)
    )
    with pytest.raises(GraphCompilationError):
        compile_graph_ddl(draft)


def test_an_entity_without_an_identifier_emits_no_constraint() -> None:
    """Nothing to enforce uniqueness on. The analyzer's own
    IDENTIFIERS_AVAILABLE check is what reports that as an error; the compiler
    simply has nothing to emit and must not invent one."""
    draft = _draft(
        entities={"Order": {"label": "Order", "properties": {}, "identifier_properties": []}}
    )
    assert compile_graph_ddl(draft) == ()


def test_compilation_is_deterministic() -> None:
    """Statements are used to answer "would this compile"; an unstable order
    would make that answer irreproducible across runs."""
    draft = _draft(
        entities={
            "Zebra": {"label": "Zebra", "properties": {"z": {}}, "identifier_properties": ["z"]},
            "Alpha": {"label": "Alpha", "properties": {"a": {}}, "identifier_properties": ["a"]},
        }
    )
    assert compile_graph_ddl(draft) == compile_graph_ddl(draft)
    assert "Alpha" in compile_graph_ddl(draft)[0]


# --- Mongo field inference --------------------------------------------------


def test_inference_reports_a_conflicting_field_as_mixed_not_a_guess() -> None:
    """Validation treats declared types as fact, so guessing here would produce
    a confident wrong answer downstream."""
    fields = {f["field_name"]: f for f in _infer_fields([{"x": 1}, {"x": "text"}])}
    assert fields["x"]["declared_type"] == "mixed"


def test_inference_marks_a_field_missing_from_some_documents_nullable() -> None:
    fields = {f["field_name"]: f for f in _infer_fields([{"a": 1, "b": 2}, {"a": 1}])}
    assert fields["a"]["nullable"] is False
    assert fields["b"]["nullable"] is True


def test_booleans_are_not_reported_as_integers() -> None:
    """bool is an int subclass in Python; the naive check gets this wrong."""
    fields = {f["field_name"]: f for f in _infer_fields([{"flag": True}])}
    assert fields["flag"]["declared_type"] == "boolean"


def test_an_empty_sample_reports_nothing_rather_than_an_empty_schema() -> None:
    assert _infer_fields([]) == ()


def test_mongo_bookkeeping_is_never_reported_as_a_field() -> None:
    fields = [f["field_name"] for f in _infer_fields([{"_id": "abc", "real": 1}])]
    assert fields == ["real"]
