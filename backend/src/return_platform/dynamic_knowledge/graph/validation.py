"""Pre-activation checks a candidate generation must pass to serve traffic.

Phase 12 asks for "deep validation" between CATCHING_UP and
READY_FOR_ACTIVATION. Until now that step was the state transition itself: the
orchestrator moved VALIDATING -> READY_FOR_ACTIVATION with a comment saying real
validation was out of scope, so a build that produced an empty graph, or one
whose edges pointed into the *previous* generation, would activate and start
serving.

**Every check is derived from the schema, never hand-configured** -- the same
discipline `constraints.py` follows. A check that had to be maintained
separately from the schema would drift from it, and a drifted validator is worse
than none: it reports green on exactly the builds it no longer understands.

**Severity is the interesting part.** An ERROR means the generation is unfit to
serve and activation must not proceed; the candidate is failed and generation N
stays active. A WARNING is recorded on the report and does not block, because
some conditions are legitimately expected -- a relationship type with no edges
is a real possibility for a sparse source, and failing activation over it would
make the platform unable to rebuild at all.

The Cypher lives here as pure compilation so the queries are testable without a
database; `lifecycle/neo4j_validator.py` is the thing that runs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from return_platform.dynamic_knowledge.graph.write_compiler import CompiledWrite
from return_platform.dynamic_knowledge.schema import ActiveSchema


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ValidationCheckId(StrEnum):
    """Named so a failure report is actionable without reading the query."""

    NODE_LABEL_POPULATED = "NODE_LABEL_POPULATED"
    NODE_KEY_COMPLETE = "NODE_KEY_COMPLETE"
    RELATIONSHIP_ENDPOINTS_SAME_GENERATION = "RELATIONSHIP_ENDPOINTS_SAME_GENERATION"
    RELATIONSHIP_TYPE_POPULATED = "RELATIONSHIP_TYPE_POPULATED"


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One compiled check. `violation_when_count_is_zero` inverts the reading:
    most checks fail when they *find* something (a bad row), but the
    populated-ness checks fail when they find nothing."""

    check_id: ValidationCheckId
    severity: ValidationSeverity
    subject: str
    statement: CompiledWrite
    violation_when_count_is_zero: bool = False


@dataclass(frozen=True, slots=True)
class ValidationFinding:
    check_id: ValidationCheckId
    severity: ValidationSeverity
    subject: str
    observed_count: int
    detail: str


@dataclass(frozen=True, slots=True)
class GenerationValidationReport:
    graph_generation_id: str
    findings: tuple[ValidationFinding, ...] = field(default_factory=tuple)

    @property
    def errors(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is ValidationSeverity.ERROR)

    @property
    def warnings(self) -> tuple[ValidationFinding, ...]:
        return tuple(f for f in self.findings if f.severity is ValidationSeverity.WARNING)

    @property
    def passed(self) -> bool:
        """Warnings do not block. See the module docstring for why."""
        return not self.errors

    def summary(self) -> str:
        if self.passed and not self.warnings:
            return f"generation {self.graph_generation_id} passed all checks"
        parts = [
            f"{f.check_id.value}[{f.subject}]: {f.detail}" for f in (*self.errors, *self.warnings)
        ]
        return f"generation {self.graph_generation_id}: " + "; ".join(parts)


def _count(cypher: str, graph_generation_id: str) -> CompiledWrite:
    return CompiledWrite(cypher=cypher, parameters={"generationId": graph_generation_id})


def compile_validation_checks(
    schema: ActiveSchema, *, graph_generation_id: str
) -> tuple[ValidationCheck, ...]:
    """Every check this schema implies, ready to execute.

    Labels and relationship types are interpolated rather than parameterised
    because Cypher does not accept them as parameters. That is safe here and
    only here: both come from `GraphIdentifier`, which is
    pattern-constrained to `^[A-Za-z_][A-Za-z0-9_]*$` at schema-load time, so a
    value that could carry Cypher syntax cannot reach this function. Generation
    ids -- which are *not* so constrained -- are always parameters.
    """
    checks: list[ValidationCheck] = []

    for node in schema.graph.nodes.values():
        entity = schema.entities[node.entity_id]
        label = node.label

        # An entity that projected zero nodes means the build lost a whole
        # slice of the domain. Serving that silently is the failure mode this
        # exists to stop.
        checks.append(
            ValidationCheck(
                check_id=ValidationCheckId.NODE_LABEL_POPULATED,
                severity=ValidationSeverity.ERROR,
                subject=label,
                statement=_count(
                    f"MATCH (n:{label} {{graph_generation_id: $generationId}}) "
                    "RETURN count(n) AS observed",
                    graph_generation_id,
                ),
                violation_when_count_is_zero=True,
            )
        )

        key_properties = [entity.fields[field_id].graph_property for field_id in node.key_fields]
        if key_properties:
            # A node missing part of its composite key is unfindable by the
            # very lookup the constraint exists to serve, and the constraint
            # itself does not catch it: Neo4j treats a null property as absent
            # from a uniqueness constraint rather than as a violation.
            predicate = " OR ".join(f"n.{prop} IS NULL" for prop in key_properties)
            checks.append(
                ValidationCheck(
                    check_id=ValidationCheckId.NODE_KEY_COMPLETE,
                    severity=ValidationSeverity.ERROR,
                    subject=label,
                    statement=_count(
                        f"MATCH (n:{label} {{graph_generation_id: $generationId}}) "
                        f"WHERE {predicate} RETURN count(n) AS observed",
                        graph_generation_id,
                    ),
                )
            )

    for relationship in schema.graph.relationships.values():
        relationship_type = relationship.relationship_type
        source_label = schema.entity_node(relationship.source_entity_id).label
        target_label = schema.entity_node(relationship.target_entity_id).label

        # The blue/green failure that is invisible until it matters: an edge
        # written during the rebuild that attached to a node from the
        # *previous* generation. Once the old generation retires those edges
        # dangle, and until then queries silently mix two generations' data.
        checks.append(
            ValidationCheck(
                check_id=ValidationCheckId.RELATIONSHIP_ENDPOINTS_SAME_GENERATION,
                severity=ValidationSeverity.ERROR,
                subject=relationship.relationship_id,
                statement=_count(
                    f"MATCH (s:{source_label})-[r:{relationship_type}]->(t:{target_label}) "
                    "WHERE r.graph_generation_id = $generationId "
                    "AND (s.graph_generation_id <> $generationId "
                    "OR t.graph_generation_id <> $generationId) "
                    "RETURN count(r) AS observed",
                    graph_generation_id,
                ),
            )
        )

        # A warning, not an error: a sparse source legitimately produces no
        # edges of some type, and blocking activation on that would make the
        # platform unable to rebuild.
        checks.append(
            ValidationCheck(
                check_id=ValidationCheckId.RELATIONSHIP_TYPE_POPULATED,
                severity=ValidationSeverity.WARNING,
                subject=relationship.relationship_id,
                statement=_count(
                    f"MATCH ()-[r:{relationship_type} {{graph_generation_id: $generationId}}]->() "
                    "RETURN count(r) AS observed",
                    graph_generation_id,
                ),
                violation_when_count_is_zero=True,
            )
        )

    return tuple(checks)


def evaluate(check: ValidationCheck, observed_count: int) -> ValidationFinding | None:
    """A finding, or None when the check passed."""
    if check.violation_when_count_is_zero:
        if observed_count > 0:
            return None
        return ValidationFinding(
            check_id=check.check_id,
            severity=check.severity,
            subject=check.subject,
            observed_count=0,
            detail="no rows were projected into this generation",
        )
    if observed_count == 0:
        return None
    return ValidationFinding(
        check_id=check.check_id,
        severity=check.severity,
        subject=check.subject,
        observed_count=observed_count,
        detail=f"{observed_count} row(s) violate this check",
    )
