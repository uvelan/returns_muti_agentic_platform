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

**Populated-ness severity is derived from the run, not hardcoded.** The same
reasoning that makes an empty relationship type a warning applies verbatim to an
empty node label: `ReturnItem` and `ReturnHandlingUnit` are written by the
platform's own return workflow, so a deployment that has never processed a
return projects zero of them -- and hardcoding ERROR there means such a
deployment can never activate a generation, can therefore never run an order
search, and can therefore never process the first return that would populate
them. See `_populated_severity`: the distinction that keeps this from being a
weakening is that "the source asset yielded no records at all" and "records were
read and none of them projected" are different facts, and only the first is
tolerated. The second is exactly the build-lost-a-slice-of-the-domain failure
this module exists to stop, and stays a hard ERROR.

**Generation scoping is read off the endpoints, never off the edge.** Both
relationship checks used to key on `r.graph_generation_id`, a property no writer
in this codebase has ever set (`write_compiler._compile_relationship_upsert` and
`compile_relationship_reconciliation` both MERGE the edge and set only the
mutation's own properties). The consequence was not a cosmetic one:
RELATIONSHIP_TYPE_POPULATED warned for every type on every build, and
RELATIONSHIP_ENDPOINTS_SAME_GENERATION -- an ERROR-severity check -- could not
match a single row and had never once fired.

The fix is to scope by the endpoints rather than to start stamping the edge, and
that choice is deliberate:

* The endpoints *are* the ground truth. A relationship exists only between two
  nodes, every node carries its generation, and `compile_relationship_writes`
  already matches both endpoints inside one generation. A property on the edge
  would be a second, redundant record of a fact the endpoints already state --
  and a redundant field can disagree with the thing it duplicates, at which
  point the validator is trusting the copy over the original.
* Stamping would not revive the dead check. The only writer that would set the
  stamp is the one that already scopes both endpoints, so a stamped edge can
  never be cross-generation in the first place. Meanwhile the writers that *can*
  produce a bleed -- `knowledge/cypher_compiler.CypherCompiler`'s relationship
  upsert, which matches endpoints on business keys with no generation predicate,
  and any hand-written Cypher -- set no stamp at all, so a stamp-based check
  would stay blind to precisely the edges that matter.
* Scoping by endpoints therefore detects a cross-generation edge *whoever wrote
  it*, which is what an ERROR-severity blue/green safety check has to do.

`compile_relationship_cardinality_checks` in `write_compiler.py` already scopes
this way; this brings validation onto the same footing rather than inventing a
second convention.

The Cypher lives here as pure compilation so the queries are testable without a
database; `lifecycle/neo4j_validator.py` is the thing that runs them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from return_platform.dynamic_knowledge.graph.write_compiler import CompiledWrite
from return_platform.dynamic_knowledge.schema import ActiveSchema, EntityDefinition


class ValidationSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class ValidationCheckId(StrEnum):
    """Named so a failure report is actionable without reading the query."""

    NODE_LABEL_POPULATED = "NODE_LABEL_POPULATED"
    NODE_LABEL_REGRESSED = "NODE_LABEL_REGRESSED"
    NODE_KEY_COMPLETE = "NODE_KEY_COMPLETE"
    RELATIONSHIP_ENDPOINTS_SAME_GENERATION = "RELATIONSHIP_ENDPOINTS_SAME_GENERATION"
    RELATIONSHIP_TYPE_POPULATED = "RELATIONSHIP_TYPE_POPULATED"


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    """One compiled check. `violation_when_count_is_zero` inverts the reading:
    most checks fail when they *find* something (a bad row), but the
    populated-ness checks fail when they find nothing.

    `zero_detail` is what the finding says when a populated-ness check fires.
    It is carried on the check rather than composed in `evaluate` because the
    *reason* a zero is tolerable or not is decided at compile time, from the run
    that produced the generation -- and a report that said "no rows were
    projected" without saying whether anything was read to project from is the
    report an operator cannot act on.
    """

    check_id: ValidationCheckId
    severity: ValidationSeverity
    subject: str
    statement: CompiledWrite
    violation_when_count_is_zero: bool = False
    zero_detail: str = "no rows were projected into this generation"
    #: What a *violation* finding says, with `{count}` filled in. Same reasoning
    #: as `zero_detail`: "600 row(s) violate this check" names no failure an
    #: operator can act on.
    violation_detail: str = "{count} row(s) violate this check"


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


#: How many records each source asset actually yielded on the run that built the
#: generation under validation, keyed by `source_asset_id`.
#:
#: A source that took part and read nothing must appear with a count of ``0``;
#: absence means "this run never scanned that source" and is treated as no
#: evidence at all (see `_populated_severity`). `GraphSyncService`'s counting
#: connector registers every participating source before its first page for
#: exactly this reason -- without it, "scanned and empty" and "never scanned"
#: would be the same observation and the distinction below would be a guess.
SourceRecordCensus = Mapping[str, int]


def _populated_severity(
    entity: EntityDefinition, census: SourceRecordCensus | None
) -> tuple[ValidationSeverity, str]:
    """Whether an empty node label fails the build, and why.

    Three states, and only the middle one is tolerated:

    * **No census** -- nothing told us what this run read. Absence of evidence is
      not evidence of an empty source, so this keeps the strict reading. A caller
      that cannot supply a census gets exactly the behaviour that shipped before
      this existed.
    * **Scanned, zero records** -- the source asset genuinely had nothing to
      project. `ReturnItem` and `ReturnHandlingUnit` on a deployment that has
      never processed a return; any legitimately empty upstream collection.
      Warn, do not block.
    * **Scanned, records read, nothing projected** -- the build read the source
      and lost every one of its records on the way to the graph: a broken
      `record_path`, an unresolvable natural key, a projection that silently
      dropped the lot. This is the failure the check exists for and stays a hard
      ERROR.

    **The middle case is not tolerated on its own.** "Scanned, zero records"
    also describes an upstream source that was dropped or truncated, which reads
    identically here and must not be allowed to replace a populated generation
    with an empty one. This function cannot tell those apart -- nothing at the
    connector can -- so the distinction is made where the evidence actually
    exists: NODE_LABEL_REGRESSED compares the candidate against the generation
    currently being served. See `_compile_regression_check`. Reading this
    function's WARNING as "an empty source is always fine" is exactly the
    weakening that guard exists to prevent.

    The distinction is a recorded fact about the run, not an inference from the
    graph. It cannot be reconstructed from the generation alone -- an empty label
    looks identical either way in Neo4j -- which is why the census is threaded
    down from the run rather than derived here.

    Only the zero/non-zero boundary is load-bearing. A rebuild scans every source
    twice (a build pass and a catch-up pass) and the counts accumulate across
    both, so the number itself is records *read*, not distinct documents; the
    detail text says so rather than implying a document count it does not have.
    """

    if census is None:
        return (
            ValidationSeverity.ERROR,
            "no rows were projected into this generation, and this run recorded no "
            "source record census, so an empty source cannot be ruled out",
        )
    records_read = census.get(entity.source_asset_id)
    if records_read is None:
        return (
            ValidationSeverity.ERROR,
            "no rows were projected into this generation, and source "
            f"{entity.source_asset_id!r} was not scanned by this run",
        )
    if records_read == 0:
        return (
            ValidationSeverity.WARNING,
            f"source {entity.source_asset_id!r} yielded no records on this run, so there "
            "was nothing to project",
        )
    return (
        ValidationSeverity.ERROR,
        f"this run read {records_read} record(s) from source {entity.source_asset_id!r} "
        "and none of them projected into this generation",
    )


def _compile_regression_check(
    label: str, graph_generation_id: str, previous_generation_id: str
) -> ValidationCheck:
    """A label the generation being replaced had, and this candidate does not.

    **This is the check that keeps the census rule from being a weakening**, and
    it exists because the census alone cannot see the difference that matters.
    "Scanned, zero records" covers two very different events: a platform-owned
    collection that has never had a row (`ReturnItem` on a deployment that has
    processed no returns) and an upstream source that was dropped, truncated, or
    silently stopped replicating. Both read as zero at the connector, so
    `_populated_severity` warns for both -- and warning for the second would let
    a build replace a populated generation with an empty one.

    What separates them is not in the source, it is in the graph: whether the
    generation currently *serving* has that label populated. If it does and the
    candidate does not, this build loses a slice of the domain that is being
    served right now, whatever the source says about why. That is an ERROR under
    any reading, and unlike the source census it cannot be spoofed by an empty
    scan.

    Scoped to labels only. A first build has no predecessor and emits none of
    these, which is correct: there is no serving generation to damage, and
    refusing to bootstrap is the defect this whole area exists to remove.

    `sum(CASE ...)` rather than two MATCHes because an aggregation with no
    grouping key returns a single row even over zero input rows -- two MATCHes
    would drop the row entirely when the candidate is empty, which is precisely
    the case being tested.
    """

    cypher = (
        f"MATCH (n:{label}) "
        "WHERE n.graph_generation_id IN [$generationId, $previousGenerationId] "
        "WITH sum(CASE WHEN n.graph_generation_id = $previousGenerationId THEN 1 ELSE 0 END) "
        "AS previous, "
        "sum(CASE WHEN n.graph_generation_id = $generationId THEN 1 ELSE 0 END) AS current "
        "RETURN CASE WHEN previous > 0 AND current = 0 THEN previous ELSE 0 END AS observed"
    )
    return ValidationCheck(
        check_id=ValidationCheckId.NODE_LABEL_REGRESSED,
        severity=ValidationSeverity.ERROR,
        subject=label,
        statement=CompiledWrite(
            cypher=cypher,
            parameters={
                "generationId": graph_generation_id,
                "previousGenerationId": previous_generation_id,
            },
        ),
        violation_detail=(
            "the generation this replaces has {count} node(s) of this label and the "
            "candidate has none; activating would drop them from service"
        ),
    )


def compile_validation_checks(
    schema: ActiveSchema,
    *,
    graph_generation_id: str,
    source_records_read: SourceRecordCensus | None = None,
    previous_generation_id: str | None = None,
) -> tuple[ValidationCheck, ...]:
    """Every check this schema implies, ready to execute.

    Labels and relationship types are interpolated rather than parameterised
    because Cypher does not accept them as parameters. That is safe here and
    only here: both come from `GraphIdentifier`, which is
    pattern-constrained to `^[A-Za-z_][A-Za-z0-9_]*$` at schema-load time, so a
    value that could carry Cypher syntax cannot reach this function. Generation
    ids -- which are *not* so constrained -- are always parameters.

    `source_records_read` is what the run observed at the sources; omitting it
    keeps every populated-ness check at ERROR.

    `previous_generation_id` is the generation this candidate would replace.
    Supplying it adds the NODE_LABEL_REGRESSED guard, which is what stops the
    census rule from letting a dropped source replace a populated generation
    with an empty one -- see `_compile_regression_check`. Omitting it is correct
    only for a first build, which has no predecessor to lose anything to.
    """
    checks: list[ValidationCheck] = []

    for node in schema.graph.nodes.values():
        entity = schema.entities[node.entity_id]
        label = node.label

        # An entity that projected zero nodes *from a source that had records*
        # means the build lost a whole slice of the domain. Serving that
        # silently is the failure mode this exists to stop. An entity whose
        # source had nothing in it is a different fact and only warns -- see
        # `_populated_severity`.
        severity, zero_detail = _populated_severity(entity, source_records_read)
        checks.append(
            ValidationCheck(
                check_id=ValidationCheckId.NODE_LABEL_POPULATED,
                severity=severity,
                subject=label,
                statement=_count(
                    f"MATCH (n:{label} {{graph_generation_id: $generationId}}) "
                    "RETURN count(n) AS observed",
                    graph_generation_id,
                ),
                violation_when_count_is_zero=True,
                zero_detail=zero_detail,
            )
        )

        # The guard that keeps the severity rule above honest. Emitted only when
        # there is a predecessor to regress against.
        if previous_generation_id is not None and previous_generation_id != graph_generation_id:
            checks.append(
                _compile_regression_check(label, graph_generation_id, previous_generation_id)
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
        # that attached a node of this generation to a node of another one.
        # Once the old generation retires those edges dangle, and until then
        # queries silently mix two generations' data.
        #
        # "Touches this generation on exactly one end" is the whole predicate,
        # and it is deliberately expressed with no reference to any property of
        # the edge -- see the module docstring. `coalesce` matters: an endpoint
        # with no `graph_generation_id` at all (a node written by a path that
        # predates generations) compares as null under `<>`, and a null would
        # drop the row rather than report it, which is how a check reports green
        # on exactly the data it least understands.
        checks.append(
            ValidationCheck(
                check_id=ValidationCheckId.RELATIONSHIP_ENDPOINTS_SAME_GENERATION,
                severity=ValidationSeverity.ERROR,
                subject=relationship.relationship_id,
                statement=_count(
                    f"MATCH (s:{source_label})-[r:{relationship_type}]->(t:{target_label}) "
                    "WHERE (s.graph_generation_id = $generationId "
                    "OR t.graph_generation_id = $generationId) "
                    "AND (coalesce(s.graph_generation_id, '') <> $generationId "
                    "OR coalesce(t.graph_generation_id, '') <> $generationId) "
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
                    f"MATCH (:{source_label} {{graph_generation_id: $generationId}})"
                    f"-[r:{relationship_type}]->"
                    f"(:{target_label} {{graph_generation_id: $generationId}}) "
                    "RETURN count(r) AS observed",
                    graph_generation_id,
                ),
                violation_when_count_is_zero=True,
                zero_detail="no edges of this type join two nodes of this generation",
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
            detail=check.zero_detail,
        )
    if observed_count == 0:
        return None
    return ValidationFinding(
        check_id=check.check_id,
        severity=check.severity,
        subject=check.subject,
        observed_count=observed_count,
        detail=check.violation_detail.format(count=observed_count),
    )
