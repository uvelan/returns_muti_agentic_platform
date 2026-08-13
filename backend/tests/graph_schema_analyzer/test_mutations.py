"""Typed mutations, the draft state machine, and the validation checks.

The first section is the important one: it asserts that a model *cannot express*
an executable statement through this command set, rather than that we happen not
to execute one.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from return_platform.graph_schema_analyzer.application.mutation_service import (
    MutationRejected,
    apply_mutations,
)
from return_platform.graph_schema_analyzer.application.validation_service import ValidationService
from return_platform.graph_schema_analyzer.domain import mutation as mutation_module
from return_platform.graph_schema_analyzer.domain.errors import InvalidSessionTransition
from return_platform.graph_schema_analyzer.domain.mutation import (
    MUTATION_KINDS,
    AddEntity,
    AddGraphConstraint,
    AddGraphIndex,
    AddProperty,
    AddRelationship,
    Cardinality,
    ChangeIdentifier,
    ChangeSyncRule,
    MutationKind,
    PropertyType,
    RemoveEntity,
    RemoveProperty,
    RenameEntity,
    SyncMode,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import (
    DraftStatus,
    GraphSchemaDraft,
    GraphSchemaShape,
)
from return_platform.graph_schema_analyzer.domain.schema_revision import ChangeType, diff_shapes
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
    SampleClassification,
    SourceSchemaSnapshot,
)
from return_platform.graph_schema_analyzer.domain.validation_result import (
    REQUIRED_CHECKS,
    Severity,
    ValidationCheck,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)

SNAPSHOT = SourceSchemaSnapshot.create(
    snapshot_id="snap-1",
    analysis_id="a1",
    datasets=(
        DatasetMetadata(
            source_id="mongo_main",
            dataset_name="orders",
            fields=(
                FieldMetadata(field_name="order_id", declared_type="string"),
                FieldMetadata(field_name="total", declared_type="decimal"),
                FieldMetadata(field_name="placed_at", declared_type="string"),
            ),
        ),
    ),
    sample_classification=SampleClassification.NONE,
    captured_at=NOW,
)


# --- the model cannot express an executable statement -----------------------


def test_no_mutation_command_has_a_free_form_executable_field() -> None:
    """The guarantee is structural: there is no field a statement could ride in.

    If someone adds `expression` or `cypher` to a command, "the model never
    authors executable statements" silently becomes a convention instead of a
    property of the type system.
    """
    forbidden = {
        "statement",
        "sql",
        "cypher",
        "query",
        "script",
        "expression",
        "predicate",
        "code",
        "command_text",
        "raw",
    }
    offenders: list[tuple[str, str]] = []
    for name, obj in vars(mutation_module).items():
        if not (inspect.isclass(obj) and issubclass(obj, BaseModel)):
            continue
        for field_name in obj.model_fields:
            if field_name in forbidden:
                offenders.append((name, field_name))
    assert not offenders, f"mutation commands must carry no executable payload: {offenders}"


def test_every_command_rejects_unknown_fields() -> None:
    """extra='forbid' everywhere, so a smuggled field fails at parse time."""
    with pytest.raises(ValidationError):
        AddEntity(label="Order", source_dataset="orders", cypher="MATCH (n) DETACH DELETE n")  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "label",
    [
        "Order) DETACH DELETE n //",
        "Order;DROP",
        "Order Name",
        "'Order'",
        "",
        "1Order",
    ],
)
def test_identifiers_that_could_break_out_are_rejected(label: str) -> None:
    with pytest.raises(ValidationError):
        AddEntity(label=label, source_dataset="orders")


def test_the_command_set_matches_the_design_documents_list() -> None:
    """Design doc 10.4 enumerates exactly these 17 commands."""
    assert {kind.value for kind in MUTATION_KINDS} == {
        "AddEntity",
        "RemoveEntity",
        "RenameEntity",
        "AddProperty",
        "RemoveProperty",
        "ChangeIdentifier",
        "AddRelationship",
        "RemoveRelationship",
        "ChangeCardinality",
        "ChangeSourceMapping",
        "ChangeTransformation",
        "AddGraphIndex",
        "RemoveGraphIndex",
        "AddGraphConstraint",
        "RemoveGraphConstraint",
        "ChangeOwnershipPolicy",
        "ChangeSyncRule",
    }
    assert len(MutationKind) == 17


# --- applying mutations -----------------------------------------------------


def _built_shape() -> GraphSchemaShape:
    return apply_mutations(
        GraphSchemaShape(),
        [
            AddEntity(label="Order", source_dataset="orders"),
            AddProperty(
                label="Order",
                property_name="order_id",
                property_type=PropertyType.STRING,
                source_field="order_id",
            ),
            ChangeIdentifier(label="Order", identifier_properties=("order_id",)),
        ],
    )


def test_a_batch_builds_the_expected_shape() -> None:
    shape = _built_shape()
    assert set(shape.entities) == {"Order"}
    assert shape.entities["Order"]["identifier_properties"] == ["order_id"]


def test_a_failing_command_leaves_the_original_shape_untouched() -> None:
    """Half a revision is a shape nobody designed."""
    original = _built_shape()
    with pytest.raises(MutationRejected):
        apply_mutations(
            original,
            [
                AddEntity(label="Customer", source_dataset="customers"),
                AddProperty(
                    label="Nonexistent",
                    property_name="x",
                    property_type=PropertyType.STRING,
                    source_field="order_id",
                ),
            ],
        )
    assert set(original.entities) == {"Order"}


def test_removing_an_entity_takes_its_relationships_with_it() -> None:
    """Otherwise the shape references a label that no longer exists, and
    validation reports an error the analyst never caused."""
    shape = apply_mutations(
        _built_shape(),
        [
            AddEntity(label="Customer", source_dataset="orders"),
            AddRelationship(
                relationship_type="PLACED_BY",
                from_label="Order",
                to_label="Customer",
                cardinality=Cardinality.MANY_TO_ONE,
            ),
        ],
    )
    assert len(shape.relationships) == 1
    pruned = apply_mutations(shape, [RemoveEntity(label="Customer")])
    assert pruned.relationships == ()


def test_renaming_an_entity_rewrites_its_references() -> None:
    shape = apply_mutations(
        _built_shape(),
        [
            AddEntity(label="Customer", source_dataset="orders"),
            AddRelationship(
                relationship_type="PLACED_BY",
                from_label="Order",
                to_label="Customer",
                cardinality=Cardinality.MANY_TO_ONE,
            ),
            RenameEntity(label="Customer", new_label="Buyer"),
        ],
    )
    assert set(shape.entities) == {"Order", "Buyer"}
    assert shape.relationships[0]["to_label"] == "Buyer"


def test_an_identifier_property_cannot_be_removed_out_from_under_the_identifier() -> None:
    with pytest.raises(MutationRejected, match="identifier"):
        apply_mutations(_built_shape(), [RemoveProperty(label="Order", property_name="order_id")])


# --- draft state machine ----------------------------------------------------


def _draft(shape: GraphSchemaShape | None = None) -> GraphSchemaDraft:
    return GraphSchemaDraft(
        draft_id="d1",
        analysis_id="a1",
        shape=shape if shape is not None else _built_shape(),
        created_at=NOW,
        updated_at=NOW,
    )


def test_any_mutation_invalidates_a_validated_draft() -> None:
    """The core rule of 10.4: a validation result describes a specific shape."""
    validated = _draft().validated("validation-1", occurred_at=NOW)
    assert validated.status is DraftStatus.VALIDATED

    mutated = validated.mutated(_built_shape(), occurred_at=NOW)
    assert mutated.status is DraftStatus.DRAFT
    assert mutated.validation_result_id is None
    assert mutated.current_revision == 1


def test_editing_an_approved_draft_withdraws_the_approval() -> None:
    """Keeping it would let a build run against something nobody signed off on."""
    approved = _draft().validated("validation-1", occurred_at=NOW).approved(occurred_at=NOW)
    assert approved.status is DraftStatus.APPROVED
    edited = approved.mutated(_built_shape(), occurred_at=NOW)
    assert edited.status is DraftStatus.DRAFT
    assert edited.validation_result_id is None


def test_an_unvalidated_draft_cannot_be_approved() -> None:
    with pytest.raises(InvalidSessionTransition):
        _draft().approved(occurred_at=NOW)


def test_an_empty_draft_cannot_be_validated() -> None:
    with pytest.raises(InvalidSessionTransition):
        _draft(GraphSchemaShape()).validated("validation-1", occurred_at=NOW)


# The analyzer-owned `Approval` and its "a decision is final" invariant moved to
# the shared proposal kernel in W4.3, where `REJECTED` and `SUPERSEDED` are
# terminal states of one lifecycle serving all three kinds of governed change.
# Its replacement is covered by `tests/platform/test_proposal_kernel.py`.


# --- diff -------------------------------------------------------------------


def test_diff_reports_added_removed_and_modified() -> None:
    before = _built_shape()
    after = apply_mutations(
        before,
        [
            AddEntity(label="Customer", source_dataset="orders"),
            AddProperty(
                label="Order",
                property_name="total",
                property_type=PropertyType.FLOAT,
                source_field="total",
            ),
        ],
    )
    diff = diff_shapes(
        before.model_dump(mode="json"),
        after.model_dump(mode="json"),
        from_sequence=1,
        to_sequence=2,
    )
    kinds = {(entry.change_type, entry.element) for entry in diff.entries}
    assert (ChangeType.ADDED, "Customer") in kinds
    assert (ChangeType.MODIFIED, "Order") in kinds


# --- validation -------------------------------------------------------------


class PassingTarget:
    async def compile_schema(self, *, draft: object) -> tuple[str, ...]:
        return ()

    async def validate_schema(self, *, draft: object) -> tuple[dict[str, str], ...]:
        return ()

    async def request_build(self, *, schema_id: str, activate: bool) -> object:
        raise AssertionError("validation must never build")

    async def publish_release(
        self, *, draft: Mapping[str, object], draft_id: str, approver: str, activate: bool
    ) -> object:
        raise AssertionError("this target must never publish")


class UnreachableTarget:
    async def compile_schema(self, *, draft: object) -> tuple[str, ...]:
        raise ConnectionError("graph unreachable")

    async def validate_schema(self, *, draft: object) -> tuple[dict[str, str], ...]:
        raise ConnectionError("graph unreachable")

    async def request_build(self, *, schema_id: str, activate: bool) -> object:
        raise AssertionError("validation must never build")

    async def publish_release(
        self, *, draft: Mapping[str, object], draft_id: str, approver: str, activate: bool
    ) -> object:
        raise AssertionError("this target must never publish")


async def _validate(shape: GraphSchemaShape, target: object = None):
    service = ValidationService(target or PassingTarget())  # type: ignore[arg-type]
    return await service.validate(
        draft_id="d1",
        revision_id="r1",
        shape=shape,
        snapshot=SNAPSHOT,
        validated_at=NOW,
    )


@pytest.mark.asyncio
async def test_every_required_check_runs_on_every_validation() -> None:
    result = await _validate(_built_shape())
    assert result.checks_run == REQUIRED_CHECKS
    assert result.missing_checks == frozenset()


@pytest.mark.asyncio
async def test_a_clean_schema_passes() -> None:
    shape = apply_mutations(
        _built_shape(),
        [AddGraphIndex(label="Order", properties=("order_id",))],
    )
    result = await _validate(shape)
    assert result.passed, [f.message for f in result.errors]


@pytest.mark.asyncio
async def test_an_unreachable_graph_target_fails_rather_than_skipping() -> None:
    """ "We could not tell" must never be recorded as "it is fine"."""
    result = await _validate(_built_shape(), UnreachableTarget())
    assert not result.passed
    failed = {finding.check for finding in result.errors}
    assert ValidationCheck.CYPHER_COMPILES in failed
    assert ValidationCheck.QUERY_SAFETY_PASSES in failed
    # Still recorded as run, so the gap is visible rather than silent.
    assert result.checks_run == REQUIRED_CHECKS


@pytest.mark.asyncio
async def test_an_unknown_source_field_is_reported() -> None:
    shape = apply_mutations(
        GraphSchemaShape(),
        [
            AddEntity(label="Order", source_dataset="orders"),
            AddProperty(
                label="Order",
                property_name="ghost",
                property_type=PropertyType.STRING,
                source_field="not_a_real_field",
            ),
            ChangeIdentifier(label="Order", identifier_properties=("ghost",)),
        ],
    )
    result = await _validate(shape)
    assert ValidationCheck.FIELD_EXISTS in {f.check for f in result.errors}


@pytest.mark.asyncio
async def test_an_incompatible_type_needs_a_declared_transformation() -> None:
    """placed_at is a string in the source; mapping it to DATETIME without
    saying how is the silent-coercion bug this check exists for."""
    shape = apply_mutations(
        GraphSchemaShape(),
        [
            AddEntity(label="Order", source_dataset="orders"),
            AddProperty(
                label="Order",
                property_name="placed_at",
                property_type=PropertyType.DATETIME,
                source_field="placed_at",
            ),
            ChangeIdentifier(label="Order", identifier_properties=("placed_at",)),
        ],
    )
    result = await _validate(shape)
    assert ValidationCheck.TYPE_COMPATIBILITY in {f.check for f in result.errors}


@pytest.mark.asyncio
async def test_an_entity_with_no_identifier_fails() -> None:
    shape = apply_mutations(GraphSchemaShape(), [AddEntity(label="Order", source_dataset="orders")])
    result = await _validate(shape)
    assert ValidationCheck.IDENTIFIERS_AVAILABLE in {f.check for f in result.errors}


@pytest.mark.asyncio
async def test_incremental_sync_without_an_identifier_fails() -> None:
    shape = apply_mutations(
        GraphSchemaShape(),
        [
            AddEntity(label="Order", source_dataset="orders"),
            AddProperty(
                label="Order",
                property_name="order_id",
                property_type=PropertyType.STRING,
                source_field="order_id",
            ),
            ChangeSyncRule(label="Order", sync_mode=SyncMode.INCREMENTAL),
        ],
    )
    result = await _validate(shape)
    failed = {f.check for f in result.errors}
    assert ValidationCheck.SYNC_PROJECTION_EXECUTABLE in failed


@pytest.mark.asyncio
async def test_a_constraint_on_a_missing_property_is_caught_at_mutation_time() -> None:
    """The mutation layer refuses it, so validation never sees an impossible
    constraint -- defence in depth, with the earlier layer doing the work."""
    with pytest.raises(MutationRejected):
        apply_mutations(
            _built_shape(),
            [AddGraphConstraint(label="Order", property_name="nonexistent")],
        )


@pytest.mark.asyncio
async def test_warnings_alone_do_not_block_approval() -> None:
    """A self-relationship declared ONE_TO_ONE is rarely intended, but it is a
    design smell rather than something that makes the graph unbuildable -- so it
    must surface without blocking."""
    shape = apply_mutations(
        _built_shape(),
        [
            AddRelationship(
                relationship_type="SUPERSEDES",
                from_label="Order",
                to_label="Order",
                cardinality=Cardinality.ONE_TO_ONE,
            )
        ],
    )
    result = await _validate(shape)
    warnings = [f for f in result.findings if f.severity is Severity.WARNING]
    assert [f.check for f in warnings] == [ValidationCheck.CARDINALITY_PLAUSIBLE]
    assert result.passed
