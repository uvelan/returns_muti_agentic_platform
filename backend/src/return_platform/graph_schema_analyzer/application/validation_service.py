"""Every named validation check from design doc section 10.4.

Two structural decisions worth stating up front:

**Every check always runs.** Nothing short-circuits on first error. An analyst
fixing one problem should see all of them, not discover the next one on the next
attempt -- and each attempt costs a validation budget slot in the reasoning loop.

**An unevaluable check is a failure, not a skip.** If the graph target is
unreachable, CYPHER_COMPILES records an ERROR rather than being omitted.
`ValidationResult.passed` additionally requires every required check to appear in
`checks_run`, so "we could not tell" can never be mistaken for "it is fine".

The count discrepancy in the design doc (it says 13, names 14) is resolved in
`domain/validation_result.py`'s docstring.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import uuid4

from return_platform.graph_schema_analyzer.domain.mutation import (
    Cardinality,
    PropertyType,
    TransformationKind,
)
from return_platform.graph_schema_analyzer.domain.schema_draft import GraphSchemaShape
from return_platform.graph_schema_analyzer.domain.source_snapshot import SourceSchemaSnapshot
from return_platform.graph_schema_analyzer.domain.validation_result import (
    Severity,
    ValidationCheck,
    ValidationFinding,
    ValidationResult,
)
from return_platform.graph_schema_analyzer.ports.graph_target_port import GraphTargetPort

__all__ = ["ValidationService", "is_compatible_source_type", "property_type_for_source_type"]

logger = logging.getLogger(__name__)

# Which declared source types a graph property type may be drawn from. Narrow on
# purpose: a silent string->integer coercion at sync time is a data corruption
# that surfaces months later, so the mapping has to be declared, not inferred.
#
# The Unicode SQL Server spellings are here for the same reason the ASCII ones
# are: `nvarchar` is what every string column of `platform.bay_configuration`
# declares, and without them `property_type_for_source_type` answered None and
# re-analysis reported those columns as "could not be typed and are left out" --
# a silently narrower entity than the source, which is the failure this table
# exists to prevent rather than cause.
_COMPATIBLE_SOURCE_TYPES: Mapping[PropertyType, frozenset[str]] = {
    PropertyType.STRING: frozenset(
        {"string", "str", "text", "varchar", "char", "uuid", "nvarchar", "nchar", "ntext"}
    ),
    PropertyType.INTEGER: frozenset({"int", "integer", "long", "bigint", "smallint"}),
    PropertyType.FLOAT: frozenset({"float", "double", "decimal", "numeric", "real"}),
    PropertyType.BOOLEAN: frozenset({"bool", "boolean", "bit"}),
    PropertyType.DATE: frozenset({"date"}),
    PropertyType.DATETIME: frozenset({"datetime", "timestamp", "datetime2", "date"}),
}

# Transformations that can rescue an otherwise-incompatible mapping.
_COERCING_TRANSFORMATIONS = frozenset(
    {TransformationKind.PARSE_DATE.value, TransformationKind.PARSE_NUMBER.value}
)


def is_compatible_source_type(property_type: str, declared_source_type: str) -> bool:
    """Whether a source's declared type may back this property type.

    Exported so re-analysis asks the same question TYPE_COMPATIBILITY asks. A
    second table would let a re-analysis propose retyping a property validation
    is perfectly happy with, or leave one alone that it will reject.
    """
    try:
        resolved = PropertyType(property_type)
    except ValueError:
        return False
    return declared_source_type.lower() in _COMPATIBLE_SOURCE_TYPES[resolved]


def property_type_for_source_type(declared_source_type: str) -> PropertyType | None:
    """The property type a source's declared type maps onto, or nothing.

    First match in `PropertyType`'s own declaration order, which is what makes
    `date` resolve to DATE rather than DATETIME -- both accept it, and the
    narrower reading is the one that cannot silently widen a stored value.
    Nothing for a type the authoring vocabulary does not cover (`mixed`,
    `object`), which callers report rather than guess at.
    """
    declared = declared_source_type.lower()
    for property_type in PropertyType:
        if declared in _COMPATIBLE_SOURCE_TYPES[property_type]:
            return property_type
    return None


class ValidationService:
    def __init__(self, graph_target: GraphTargetPort) -> None:
        self._graph_target = graph_target

    async def validate(
        self,
        *,
        draft_id: str,
        revision_id: str,
        shape: GraphSchemaShape,
        snapshot: SourceSchemaSnapshot,
        validated_at: datetime,
    ) -> ValidationResult:
        findings: list[ValidationFinding] = []
        checks_run: set[ValidationCheck] = set()

        known = _SourceIndex(snapshot)

        findings.extend(_check_sources_exist(shape, known, checks_run))
        findings.extend(_check_datasets_exist(shape, known, checks_run))
        findings.extend(_check_fields_exist(shape, known, checks_run))
        findings.extend(_check_type_compatibility(shape, known, checks_run))
        findings.extend(_check_identifiers_available(shape, checks_run))
        findings.extend(_check_relationships_resolvable(shape, checks_run))
        findings.extend(_check_cardinality_plausible(shape, checks_run))
        findings.extend(_check_transformation_supported(shape, checks_run))
        findings.extend(_check_search_anchors_viable(shape, checks_run))
        findings.extend(_check_graph_index_definitions(shape, checks_run))
        findings.extend(_check_graph_constraints(shape, checks_run))
        findings.extend(_check_sync_projection_executable(shape, checks_run))
        findings.extend(await self._check_graph_target(shape, checks_run))

        return ValidationResult(
            result_id=f"validation-{uuid4()}",
            draft_id=draft_id,
            revision_id=revision_id,
            findings=tuple(findings),
            checks_run=frozenset(checks_run),
            validated_at=validated_at,
        )

    async def _check_graph_target(
        self, shape: GraphSchemaShape, checks_run: set[ValidationCheck]
    ) -> list[ValidationFinding]:
        """CYPHER_COMPILES and QUERY_SAFETY_PASSES, both owned by the target.

        Compilation is the target's job precisely because the model never writes
        Cypher: the compiler turns the validated structure into statements, so
        "does it compile" is a question about the structure, asked of the thing
        that will do the compiling.
        """
        findings: list[ValidationFinding] = []
        try:
            await self._graph_target.compile_schema(draft=shape.model_dump(mode="json"))
            checks_run.add(ValidationCheck.CYPHER_COMPILES)
        except Exception as exc:  # noqa: BLE001 - any failure is a validation failure
            checks_run.add(ValidationCheck.CYPHER_COMPILES)
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.CYPHER_COMPILES,
                    severity=Severity.ERROR,
                    element="draft",
                    message=f"the graph target could not compile this schema: {exc}",
                )
            )
        try:
            target_findings = await self._graph_target.validate_schema(
                draft=shape.model_dump(mode="json")
            )
            checks_run.add(ValidationCheck.QUERY_SAFETY_PASSES)
            findings.extend(
                ValidationFinding(
                    check=ValidationCheck.QUERY_SAFETY_PASSES,
                    severity=Severity.ERROR,
                    element=str(item.get("element", "draft")),
                    message=str(item.get("message", item)),
                )
                for item in target_findings
            )
        except Exception as exc:  # noqa: BLE001
            checks_run.add(ValidationCheck.QUERY_SAFETY_PASSES)
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.QUERY_SAFETY_PASSES,
                    severity=Severity.ERROR,
                    element="draft",
                    message=f"the graph target could not evaluate query safety: {exc}",
                )
            )
        return findings


class _SourceIndex:
    """Fast lookups over a snapshot's metadata, built once per validation."""

    def __init__(self, snapshot: SourceSchemaSnapshot) -> None:
        self.source_ids = {dataset.source_id for dataset in snapshot.datasets}
        self.dataset_names = {dataset.dataset_name for dataset in snapshot.datasets}
        self.qualified = {
            f"{dataset.source_id}.{dataset.dataset_name}" for dataset in snapshot.datasets
        }
        self.field_types: dict[str, str] = {}
        for dataset in snapshot.datasets:
            for field in dataset.fields:
                self.field_types[f"{dataset.dataset_name}.{field.field_name}"] = (
                    field.declared_type.lower()
                )
                self.field_types[field.field_name] = field.declared_type.lower()

    def dataset_exists(self, reference: str) -> bool:
        return reference in self.qualified or reference in self.dataset_names

    def source_of(self, reference: str) -> str:
        return reference.split(".", 1)[0] if "." in reference else reference


def _entities(shape: GraphSchemaShape) -> Mapping[str, Mapping[str, Any]]:
    return shape.entities


def _check_sources_exist(
    shape: GraphSchemaShape, known: _SourceIndex, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.SOURCE_EXISTS)
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        reference = str(entity.get("source_dataset", ""))
        source_id = known.source_of(reference)
        if source_id and source_id not in known.source_ids and not known.dataset_exists(reference):
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.SOURCE_EXISTS,
                    severity=Severity.ERROR,
                    element=label,
                    message=f"source {source_id!r} is not present in the analysed snapshot.",
                )
            )
    return findings


def _check_datasets_exist(
    shape: GraphSchemaShape, known: _SourceIndex, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.DATASET_EXISTS)
    return [
        ValidationFinding(
            check=ValidationCheck.DATASET_EXISTS,
            severity=Severity.ERROR,
            element=label,
            message=f"dataset {entity.get('source_dataset')!r} is not in the analysed snapshot.",
        )
        for label, entity in _entities(shape).items()
        if not known.dataset_exists(str(entity.get("source_dataset", "")))
    ]


def _check_fields_exist(
    shape: GraphSchemaShape, known: _SourceIndex, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.FIELD_EXISTS)
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        for name, definition in (entity.get("properties") or {}).items():
            source_field = str(definition.get("source_field", ""))
            if source_field not in known.field_types:
                findings.append(
                    ValidationFinding(
                        check=ValidationCheck.FIELD_EXISTS,
                        severity=Severity.ERROR,
                        element=f"{label}.{name}",
                        message=f"source field {source_field!r} is not in the analysed snapshot.",
                    )
                )
    return findings


def _check_type_compatibility(
    shape: GraphSchemaShape, known: _SourceIndex, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.TYPE_COMPATIBILITY)
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        for name, definition in (entity.get("properties") or {}).items():
            declared = known.field_types.get(str(definition.get("source_field", "")))
            if declared is None:
                continue  # FIELD_EXISTS already reported this.
            try:
                property_type = PropertyType(str(definition.get("type")))
            except ValueError:
                findings.append(
                    ValidationFinding(
                        check=ValidationCheck.TYPE_COMPATIBILITY,
                        severity=Severity.ERROR,
                        element=f"{label}.{name}",
                        message=f"unknown property type {definition.get('type')!r}.",
                    )
                )
                continue
            if declared in _COMPATIBLE_SOURCE_TYPES[property_type]:
                continue
            # A declared transformation can legitimately bridge the gap.
            if str(definition.get("transformation")) in _COERCING_TRANSFORMATIONS:
                continue
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.TYPE_COMPATIBILITY,
                    severity=Severity.ERROR,
                    element=f"{label}.{name}",
                    message=(
                        f"source type {declared!r} is not compatible with {property_type.value}; "
                        "declare a parsing transformation if the conversion is intended."
                    ),
                )
            )
    return findings


def _check_identifiers_available(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.IDENTIFIERS_AVAILABLE)
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        identifiers = entity.get("identifier_properties") or []
        if not identifiers:
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.IDENTIFIERS_AVAILABLE,
                    severity=Severity.ERROR,
                    element=label,
                    message=(
                        "no identifier property; without one, sync cannot tell an update from "
                        "an insert and will duplicate nodes on every run."
                    ),
                )
            )
    return findings


def _check_relationships_resolvable(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.RELATIONSHIPS_RESOLVABLE)
    labels = set(_entities(shape))
    findings: list[ValidationFinding] = []
    for item in shape.relationships:
        for side in ("from_label", "to_label"):
            label = str(item.get(side, ""))
            if label not in labels:
                findings.append(
                    ValidationFinding(
                        check=ValidationCheck.RELATIONSHIPS_RESOLVABLE,
                        severity=Severity.ERROR,
                        element=str(item.get("relationship_type", "?")),
                        message=f"{side} {label!r} is not an entity in this draft.",
                    )
                )
    return findings


def _check_cardinality_plausible(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.CARDINALITY_PLAUSIBLE)
    findings: list[ValidationFinding] = []
    for item in shape.relationships:
        value = str(item.get("cardinality", ""))
        try:
            cardinality = Cardinality(value)
        except ValueError:
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.CARDINALITY_PLAUSIBLE,
                    severity=Severity.ERROR,
                    element=str(item.get("relationship_type", "?")),
                    message=f"unknown cardinality {value!r}.",
                )
            )
            continue
        # A self-relationship that claims ONE_TO_ONE describes a node joined to
        # itself exactly once, which is never what is meant and produces a graph
        # nobody can traverse usefully.
        if item.get("from_label") == item.get("to_label") and cardinality is Cardinality.ONE_TO_ONE:
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.CARDINALITY_PLAUSIBLE,
                    severity=Severity.WARNING,
                    element=str(item.get("relationship_type", "?")),
                    message="a self-relationship declared ONE_TO_ONE is rarely intended.",
                )
            )
    return findings


def _check_transformation_supported(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.TRANSFORMATION_SUPPORTED)
    supported = {kind.value for kind in TransformationKind}
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        for name, definition in (entity.get("properties") or {}).items():
            transformation = str(definition.get("transformation", "NONE"))
            if transformation not in supported:
                findings.append(
                    ValidationFinding(
                        check=ValidationCheck.TRANSFORMATION_SUPPORTED,
                        severity=Severity.ERROR,
                        element=f"{label}.{name}",
                        message=(
                            f"transformation {transformation!r} is not one the platform "
                            "implements; transformations are a closed set."
                        ),
                    )
                )
    return findings


def _check_search_anchors_viable(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    """An entity nothing can be searched by is unreachable in practice.

    WARNING rather than ERROR: a pure join entity legitimately has no anchor of
    its own, so this is a design smell to surface, not a build blocker.
    """
    checks_run.add(ValidationCheck.SEARCH_ANCHORS_VIABLE)
    indexed = {str(item.get("label")) for item in shape.graph_indexes}
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        if label in indexed or entity.get("identifier_properties"):
            continue
        findings.append(
            ValidationFinding(
                check=ValidationCheck.SEARCH_ANCHORS_VIABLE,
                severity=Severity.WARNING,
                element=label,
                message="no identifier or graph index; this entity cannot be looked up directly.",
            )
        )
    return findings


def _check_graph_index_definitions(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.GRAPH_INDEX_DEFINITION_VALID)
    entities = _entities(shape)
    findings: list[ValidationFinding] = []
    for item in shape.graph_indexes:
        label = str(item.get("label", ""))
        entity = entities.get(label)
        if entity is None:
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.GRAPH_INDEX_DEFINITION_VALID,
                    severity=Severity.ERROR,
                    element=label,
                    message="index targets an entity that does not exist in this draft.",
                )
            )
            continue
        properties = entity.get("properties") or {}
        for name in item.get("properties", []):
            if name not in properties:
                findings.append(
                    ValidationFinding(
                        check=ValidationCheck.GRAPH_INDEX_DEFINITION_VALID,
                        severity=Severity.ERROR,
                        element=f"{label}.{name}",
                        message="index targets a property that does not exist.",
                    )
                )
    return findings


def _check_graph_constraints(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    checks_run.add(ValidationCheck.GRAPH_CONSTRAINT_VALID)
    entities = _entities(shape)
    findings: list[ValidationFinding] = []
    for item in shape.graph_constraints:
        label = str(item.get("label", ""))
        entity = entities.get(label)
        if entity is None:
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.GRAPH_CONSTRAINT_VALID,
                    severity=Severity.ERROR,
                    element=label,
                    message="constraint targets an entity that does not exist in this draft.",
                )
            )
            continue
        name = str(item.get("property_name", ""))
        if name not in (entity.get("properties") or {}):
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.GRAPH_CONSTRAINT_VALID,
                    severity=Severity.ERROR,
                    element=f"{label}.{name}",
                    message="constraint targets a property that does not exist.",
                )
            )
    return findings


def _check_sync_projection_executable(
    shape: GraphSchemaShape, checks_run: set[ValidationCheck]
) -> list[ValidationFinding]:
    """An INCREMENTAL entity needs an identifier to project changes onto."""
    checks_run.add(ValidationCheck.SYNC_PROJECTION_EXECUTABLE)
    findings: list[ValidationFinding] = []
    for label, entity in _entities(shape).items():
        if str(entity.get("sync_mode")) == "INCREMENTAL" and not entity.get(
            "identifier_properties"
        ):
            findings.append(
                ValidationFinding(
                    check=ValidationCheck.SYNC_PROJECTION_EXECUTABLE,
                    severity=Severity.ERROR,
                    element=label,
                    message=(
                        "INCREMENTAL sync needs an identifier to match existing nodes against; "
                        "without one every run would re-insert."
                    ),
                )
            )
    return findings
