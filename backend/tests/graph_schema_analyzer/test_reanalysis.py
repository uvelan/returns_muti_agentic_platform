"""Re-running discovery on a source that drifted, against a draft someone designed.

The behaviour these pin down is the one the feature exists for: a second
discovery produces a *proposal*, never an edit, and it tells a dataset that
changed apart from a dataset that merely moved. Getting the second wrong is the
expensive failure -- it rewrites a designed schema because a database was
restored somewhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime

from return_platform.graph_schema_analyzer.application.mutation_service import apply_mutations
from return_platform.graph_schema_analyzer.application.reanalysis_service import (
    DriftKind,
    propose_reanalysis,
)
from return_platform.graph_schema_analyzer.domain.mutation import (
    AddEntity,
    AddProperty,
    ChangeIdentifier,
    ChangeTransformation,
    MutationCommand,
    PropertyType,
    TransformationKind,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=UTC)


def _dataset(
    name: str, fields: dict[str, str], *, source_id: str = "mongo_main"
) -> DatasetMetadata:
    return DatasetMetadata(
        source_id=source_id,
        dataset_name=name,
        fields=tuple(
            FieldMetadata(field_name=field, declared_type=declared)
            for field, declared in fields.items()
        ),
    )


def _snapshot(*datasets: DatasetMetadata, snapshot_id: str = "snap") -> SourceSchemaSnapshot:
    return SourceSchemaSnapshot.create(
        snapshot_id=snapshot_id,
        analysis_id="a1",
        datasets=datasets,
        sample_classification=SampleClassification.NONE,
        captured_at=NOW,
    )


def _shape(*commands: MutationCommand) -> GraphSchemaShape:
    """Built through the real applier, so a draft in a test is a draft the
    platform could actually hold."""
    return apply_mutations(GraphSchemaShape(), commands)


ORDER_SHAPE = _shape(
    AddEntity(label="Order", source_dataset="orders"),
    AddProperty(
        label="Order",
        property_name="order_id",
        property_type=PropertyType.STRING,
        source_field="orders.order_id",
    ),
    AddProperty(
        label="Order",
        property_name="total",
        property_type=PropertyType.FLOAT,
        source_field="orders.total",
    ),
    ChangeIdentifier(label="Order", identifier_properties=("order_id",)),
)

ORDERS_BEFORE = _dataset("orders", {"order_id": "string", "total": "float"})


def _propose(
    before: SourceSchemaSnapshot,
    after: SourceSchemaSnapshot,
    *,
    shape: GraphSchemaShape = ORDER_SHAPE,
):
    return propose_reanalysis(
        draft_id="draft-1", shape=shape, before=before, after=after, from_sequence=4
    )


def test_a_source_that_did_not_change_proposes_nothing() -> None:
    """Two captures of the same shape have the same address, so there is nothing
    to re-reason about -- and an empty proposal is what says so."""
    proposal = _propose(_snapshot(ORDERS_BEFORE), _snapshot(ORDERS_BEFORE))

    assert proposal.is_empty
    assert proposal.mutations == ()
    assert proposal.diff.is_empty
    assert proposal.from_content_hash == proposal.to_content_hash


def test_a_new_source_field_is_proposed_as_a_property() -> None:
    after = _dataset("orders", {"order_id": "string", "total": "float", "status": "string"})

    proposal = _propose(_snapshot(ORDERS_BEFORE), _snapshot(after))

    (change,) = proposal.changes
    assert change.drift is DriftKind.FIELD_ADDED
    assert change.element == "Order.status"
    (command,) = change.mutations
    assert isinstance(command, AddProperty)
    assert command.property_name == "status"
    assert command.property_type is PropertyType.STRING
    assert command.source_field == "orders.status"


def test_a_proposal_never_touches_the_draft_it_was_made_against() -> None:
    """The whole point. Re-analysis returns commands; applying them is a
    separate, human act through the ordinary mutations endpoint."""
    after = _dataset("orders", {"order_id": "string", "total": "float", "status": "string"})
    before_shape = ORDER_SHAPE.model_dump(mode="json")

    _propose(_snapshot(ORDERS_BEFORE), _snapshot(after))

    assert ORDER_SHAPE.model_dump(mode="json") == before_shape


def test_a_field_the_source_dropped_is_proposed_for_removal() -> None:
    after = _dataset("orders", {"order_id": "string"})

    proposal = _propose(_snapshot(ORDERS_BEFORE), _snapshot(after))

    (change,) = proposal.changes
    assert change.drift is DriftKind.FIELD_REMOVED
    assert change.element == "Order.total"
    assert [c.kind for c in change.mutations] == ["RemoveProperty"]


def test_dropping_the_field_an_entity_identifies_on_is_reported_not_proposed() -> None:
    """Re-identifying an entity decides which nodes are the same node. Inferring
    that from one discovery run would silently re-key a live graph."""
    after = _dataset("orders", {"total": "float"})

    proposal = _propose(_snapshot(ORDERS_BEFORE), _snapshot(after))

    change = next(c for c in proposal.changes if c.element == "Order.order_id")
    assert change.requires_human_decision
    assert change.mutations == ()


def test_an_incompatible_type_change_is_proposed_as_remove_then_add() -> None:
    """The command set has no retype, and inventing one would be a second
    vocabulary for something the existing one already expresses."""
    after = _dataset("orders", {"order_id": "string", "total": "string"})

    proposal = _propose(_snapshot(ORDERS_BEFORE), _snapshot(after))

    (change,) = proposal.changes
    assert change.drift is DriftKind.FIELD_TYPE_CHANGED
    assert [c.kind for c in change.mutations] == ["RemoveProperty", "AddProperty"]
    added = change.mutations[1]
    assert isinstance(added, AddProperty)
    assert added.property_type is PropertyType.STRING


def test_a_type_change_the_property_still_accepts_proposes_nothing() -> None:
    """`varchar` becoming `text` is a source-side detail a STRING property does
    not care about. Reporting it would bury the one change that matters."""
    before = _snapshot(_dataset("orders", {"order_id": "varchar"}))
    after = _snapshot(_dataset("orders", {"order_id": "text"}))
    shape = _shape(
        AddEntity(label="Order", source_dataset="orders"),
        AddProperty(
            label="Order",
            property_name="order_id",
            property_type=PropertyType.STRING,
            source_field="orders.order_id",
        ),
    )

    assert _propose(before, after, shape=shape).is_empty


def test_a_declared_transformation_survives_a_retype() -> None:
    """AddProperty resets the transformation. Silently dropping one the analyst
    configured would change what sync writes without saying so."""
    shape = apply_mutations(
        ORDER_SHAPE,
        (
            ChangeTransformation(
                label="Order", property_name="total", transformation=TransformationKind.TRIM
            ),
        ),
    )
    after = _dataset("orders", {"order_id": "string", "total": "string"})

    proposal = propose_reanalysis(
        draft_id="draft-1",
        shape=shape,
        before=_snapshot(ORDERS_BEFORE),
        after=_snapshot(after),
        from_sequence=1,
    )

    restored = proposal.mutations[-1]
    assert isinstance(restored, ChangeTransformation)
    assert restored.transformation is TransformationKind.TRIM


def test_a_coercing_transformation_makes_a_type_change_a_non_event() -> None:
    """PARSE_NUMBER is the analyst saying the conversion is intended, and
    validation already accepts it. Re-analysis must not propose undoing it."""
    shape = apply_mutations(
        ORDER_SHAPE,
        (
            ChangeTransformation(
                label="Order",
                property_name="total",
                transformation=TransformationKind.PARSE_NUMBER,
            ),
        ),
    )
    after = _dataset("orders", {"order_id": "string", "total": "string"})

    proposal = propose_reanalysis(
        draft_id="draft-1",
        shape=shape,
        before=_snapshot(ORDERS_BEFORE),
        after=_snapshot(after),
        from_sequence=1,
    )

    assert proposal.is_empty


def test_a_new_dataset_is_proposed_as_a_whole_entity() -> None:
    after = _snapshot(
        ORDERS_BEFORE, _dataset("order_lines", {"line_id": "string", "quantity": "int"})
    )

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    (change,) = proposal.changes
    assert change.drift is DriftKind.DATASET_ADDED
    assert change.element == "OrderLines"
    kinds = [c.kind for c in change.mutations]
    assert kinds == ["AddEntity", "AddProperty", "AddProperty"]


def test_a_new_dataset_proposes_no_identifier() -> None:
    """Which property identifies a thing is a modelling decision. Validation
    will say so loudly if the analyst accepts the proposal and stops there."""
    after = _snapshot(ORDERS_BEFORE, _dataset("order_lines", {"line_id": "string"}))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    assert not any(c.kind == "ChangeIdentifier" for c in proposal.mutations)


def test_a_field_whose_declared_type_nothing_covers_is_left_out() -> None:
    """Mongo reports `mixed` when sampled documents disagree. Guessing a type
    for it is exactly the confident wrong answer discovery refuses to give."""
    after = _snapshot(ORDERS_BEFORE, _dataset("audit", {"payload": "mixed"}))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    (change,) = proposal.changes
    assert [c.kind for c in change.mutations] == ["AddEntity"]
    assert "payload" in change.detail


def test_a_new_dataset_whose_label_is_already_taken_is_reported_not_proposed() -> None:
    after = _snapshot(ORDERS_BEFORE, _dataset("Order", {"x": "string"}))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    (change,) = proposal.changes
    assert change.requires_human_decision
    assert "name it yourself" in change.detail


def test_the_same_dataset_from_a_different_source_is_a_rebinding() -> None:
    """The distinction the module turns on. Where a dataset lives has not been
    part of the graph's shape since bindings became separately editable."""
    after = _snapshot(_dataset("orders", {"order_id": "string", "total": "float"}, source_id="dr"))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    assert proposal.changes == ()
    (rebinding,) = proposal.rebindings
    assert rebinding.dataset == "orders"
    assert (rebinding.from_source_id, rebinding.to_source_id) == ("mongo_main", "dr")


def test_a_dataset_that_reappears_under_a_new_name_is_a_rebinding_not_a_reshaping() -> None:
    """Same fields, new name. Proposing an entity for the new one and abandoning
    the old would leave the draft describing a graph built twice."""
    after = _snapshot(_dataset("orders_v2", {"order_id": "string", "total": "float"}))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    assert proposal.changes == ()
    (rebinding,) = proposal.rebindings
    assert rebinding.dataset == "orders"
    assert rebinding.to_dataset == "orders_v2"


def test_two_candidates_for_the_same_shape_are_not_guessed_between() -> None:
    """With two datasets carrying exactly the old fields there is no evidence
    which one it became, and picking would bind an entity to the wrong data."""
    after = _snapshot(
        _dataset("orders_a", {"order_id": "string", "total": "float"}),
        _dataset("orders_b", {"order_id": "string", "total": "float"}),
    )

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    assert proposal.rebindings == ()
    kinds = {change.drift for change in proposal.changes}
    assert DriftKind.DATASET_REMOVED in kinds


def test_a_dataset_an_entity_reads_that_vanished_is_never_a_removal_proposal() -> None:
    """One absent reading is far more often a move this could not prove than a
    decision to delete a chunk of the graph."""
    after = _snapshot(_dataset("something_else", {"a": "string", "b": "string", "c": "string"}))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    removal = next(c for c in proposal.changes if c.drift is DriftKind.DATASET_REMOVED)
    assert removal.element == "Order"
    assert removal.mutations == ()
    assert not any(c.kind == "RemoveEntity" for c in proposal.mutations)


def test_the_proposed_batch_always_applies_to_the_draft_it_came_from() -> None:
    """A proposal an analyst accepts and the mutation service then rejects is
    worse than no proposal. Everything drifting at once, in one batch."""
    after = _snapshot(
        _dataset("orders", {"order_id": "string", "total": "string", "placed_on": "date"}),
        _dataset("order_lines", {"line_id": "string"}),
    )

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    applied = apply_mutations(ORDER_SHAPE, proposal.mutations)
    assert "OrderLines" in applied.entities
    assert applied.entities["Order"]["properties"]["total"]["type"] == "STRING"


def test_the_diff_reads_as_the_revision_the_proposal_would_become() -> None:
    """Same vocabulary as revision history, so a re-analysis is read the way
    every other change to a draft is read."""
    after = _snapshot(ORDERS_BEFORE, _dataset("order_lines", {"line_id": "string"}))

    proposal = _propose(_snapshot(ORDERS_BEFORE), after)

    assert (proposal.diff.from_sequence, proposal.diff.to_sequence) == (4, 5)
    assert {(e.change_type, e.element) for e in proposal.diff.entries} == {("ADDED", "OrderLines")}


def test_a_dataset_nothing_reads_that_moved_is_judged_on_its_own() -> None:
    """A rebinding asks someone to repoint an entity. With no entity reading it
    there is nothing to repoint, and the new dataset is simply new."""
    before = _snapshot(ORDERS_BEFORE, _dataset("scratch", {"a": "string"}))
    after = _snapshot(ORDERS_BEFORE, _dataset("scratch_v2", {"a": "string"}))

    proposal = _propose(before, after)

    assert proposal.rebindings == ()
    assert {change.drift for change in proposal.changes} == {DriftKind.DATASET_ADDED}
