"""Deep validation, and the Wave C gate item it implements.

`VALIDATING -> READY_FOR_ACTIVATION` used to be a bare state transition with a
comment saying real validation was out of scope. A build that projected zero
nodes, or whose edges attached to the *previous* generation, would activate and
start serving.

The gate item is "validation failure keeps N active", and that is the last test
here: it is not enough that a bad candidate is rejected -- the currently-serving
generation must be untouched and the snapshot must not move. A rejection that
also broke the live generation would satisfy a weaker reading and be a worse
outcome than activating the bad build.
"""

from __future__ import annotations

import uuid

import pytest

from return_platform.dynamic_knowledge.graph.generation import (
    ActiveRuntimeSnapshot,
    GraphGenerationStatus,
)
from return_platform.dynamic_knowledge.graph.validation import (
    GenerationValidationReport,
    ValidationCheck,
    ValidationCheckId,
    ValidationFinding,
    ValidationSeverity,
    compile_validation_checks,
    evaluate,
)
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    ActivationError,
    GenerationLifecycleOrchestrator,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema
from tests.dynamic_knowledge.test_generation_drain import (  # reuse the established doubles
    NOW,
    _FakeGenerationWriter,
    _FakeRebuildLeaseStore,
    _FakeSnapshotStore,
    _QuietSyncCoordinator,
)


def _finding(severity: ValidationSeverity) -> ValidationFinding:
    return ValidationFinding(
        check_id=ValidationCheckId.NODE_LABEL_POPULATED,
        severity=severity,
        subject="Order",
        observed_count=0,
        detail="no rows were projected into this generation",
    )


class _Validator:
    def __init__(self, report: GenerationValidationReport) -> None:
        self._report = report
        self.calls = 0
        self.census: object | None = None

    async def validate(
        self,
        *,
        schema: object,
        graph_generation_id: str,
        source_records_read: object | None = None,
        previous_generation_id: str | None = None,
    ) -> object:
        self.calls += 1
        self.census = source_records_read
        return GenerationValidationReport(
            graph_generation_id=graph_generation_id, findings=self._report.findings
        )


class _CountingTokens:
    """Strictly increasing, as `MongoFencingTokenAllocator` is."""

    def __init__(self) -> None:
        self._next = 1

    async def allocate(self, *, scope: str, floor: int = 0) -> int:
        del scope
        self._next = max(self._next, floor) + 1
        return self._next


def _schema() -> object:
    return type("_Schema", (), {"configuration_checksum": "fingerprint-1"})()


def _previous(graph_generation_id: str) -> ActiveRuntimeSnapshot:
    return ActiveRuntimeSnapshot(
        snapshot_name="default",
        configuration_release_id="release-1",
        schema_fingerprint="fingerprint-1",
        graph_generation_id=graph_generation_id,
        search_index_release_id="none",
        activation_id=str(uuid.uuid4()),
        activation_version=1,
        activated_at=NOW,
    )


def _orchestrator(
    writer: _FakeGenerationWriter,
    snapshot_store: _FakeSnapshotStore,
    validator: object | None,
) -> GenerationLifecycleOrchestrator:
    return GenerationLifecycleOrchestrator(
        snapshot_store=snapshot_store,  # type: ignore[arg-type]
        lease_store=_FakeRebuildLeaseStore(),  # type: ignore[arg-type]
        generation_writer=writer,  # type: ignore[arg-type]
        sync_coordinator=_QuietSyncCoordinator(),  # type: ignore[arg-type]
        fencing_tokens=_CountingTokens(),  # type: ignore[arg-type]
        owner_instance_id="test-instance",
        validator=validator,  # type: ignore[arg-type]
        drain_poll_seconds=0.01,
    )


# --- finding evaluation -----------------------------------------------------


def test_a_populated_check_passes_when_rows_exist() -> None:
    from return_platform.dynamic_knowledge.graph.validation import ValidationCheck
    from return_platform.dynamic_knowledge.graph.write_compiler import CompiledWrite

    check = ValidationCheck(
        check_id=ValidationCheckId.NODE_LABEL_POPULATED,
        severity=ValidationSeverity.ERROR,
        subject="Order",
        statement=CompiledWrite(cypher="", parameters={}),
        violation_when_count_is_zero=True,
    )
    assert evaluate(check, 5) is None
    finding = evaluate(check, 0)
    assert finding is not None and finding.severity is ValidationSeverity.ERROR


def test_a_violation_check_passes_when_no_rows_match() -> None:
    """Inverted from the populated checks -- these fail when they *find*
    something. Getting the polarity backwards would report every healthy
    generation as broken and every broken one as healthy."""
    from return_platform.dynamic_knowledge.graph.validation import ValidationCheck
    from return_platform.dynamic_knowledge.graph.write_compiler import CompiledWrite

    check = ValidationCheck(
        check_id=ValidationCheckId.RELATIONSHIP_ENDPOINTS_SAME_GENERATION,
        severity=ValidationSeverity.ERROR,
        subject="order_customer",
        statement=CompiledWrite(cypher="", parameters={}),
    )
    assert evaluate(check, 0) is None
    finding = evaluate(check, 3)
    assert finding is not None and finding.observed_count == 3


def test_warnings_do_not_block_activation() -> None:
    """A sparse source legitimately produces no edges of some type. Failing
    activation on that would make the platform unable to rebuild at all."""
    report = GenerationValidationReport(
        graph_generation_id="gen-1", findings=(_finding(ValidationSeverity.WARNING),)
    )
    assert report.passed is True
    assert report.warnings and not report.errors


def test_errors_block_activation() -> None:
    report = GenerationValidationReport(
        graph_generation_id="gen-1", findings=(_finding(ValidationSeverity.ERROR),)
    )
    assert report.passed is False


# --- the orchestrator gate --------------------------------------------------


@pytest.mark.asyncio
async def test_a_clean_report_activates() -> None:
    writer = _FakeGenerationWriter()
    snapshot_store = _FakeSnapshotStore()
    validator = _Validator(GenerationValidationReport(graph_generation_id="ignored"))

    snapshot = await _orchestrator(writer, snapshot_store, validator).build_and_activate(
        schema=_schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )

    assert validator.calls == 1
    assert snapshot.activation_version == 1
    assert writer.statuses[snapshot.graph_generation_id][0] is GraphGenerationStatus.ACTIVE


@pytest.mark.asyncio
async def test_a_failing_report_keeps_the_previous_generation_active() -> None:
    """The Wave C gate item. Rejecting the candidate is only half of it -- the
    generation that was already serving has to still be serving, and the
    snapshot must not have moved."""
    writer = _FakeGenerationWriter()
    await writer.create_generation(
        graph_generation_id="gen-live", fencing_token=1, status=GraphGenerationStatus.ACTIVE
    )
    snapshot_store = _FakeSnapshotStore(_previous("gen-live"))
    before = snapshot_store.snapshot
    validator = _Validator(
        GenerationValidationReport(
            graph_generation_id="ignored", findings=(_finding(ValidationSeverity.ERROR),)
        )
    )

    with pytest.raises(ActivationError) as caught:
        await _orchestrator(writer, snapshot_store, validator).build_and_activate(
            schema=_schema(),  # type: ignore[arg-type]
            snapshot_name="default",
            configuration_release_id="release-1",
        )

    assert caught.value.stage == "VALIDATE"
    # The live generation is untouched and still pointed at.
    assert writer.statuses["gen-live"][0] is GraphGenerationStatus.ACTIVE
    assert snapshot_store.snapshot is before
    # The candidate was failed, not left mid-flight.
    candidate = next(gid for gid in writer.statuses if gid != "gen-live")
    assert writer.statuses[candidate][0] is GraphGenerationStatus.FAILED


@pytest.mark.asyncio
async def test_a_warning_only_report_still_activates() -> None:
    writer = _FakeGenerationWriter()
    snapshot_store = _FakeSnapshotStore()
    validator = _Validator(
        GenerationValidationReport(
            graph_generation_id="ignored", findings=(_finding(ValidationSeverity.WARNING),)
        )
    )

    snapshot = await _orchestrator(writer, snapshot_store, validator).build_and_activate(
        schema=_schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )

    assert writer.statuses[snapshot.graph_generation_id][0] is GraphGenerationStatus.ACTIVE


@pytest.mark.asyncio
async def test_no_validator_configured_still_activates_but_is_not_a_silent_pass(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ "We validated and it was fine" and "we did not validate" must not look
    the same in an incident."""
    writer = _FakeGenerationWriter()

    with caplog.at_level("WARNING"):
        await _orchestrator(writer, _FakeSnapshotStore(), None).build_and_activate(
            schema=_schema(),  # type: ignore[arg-type]
            snapshot_name="default",
            configuration_release_id="release-1",
        )

    assert any("without deep validation" in record.message for record in caplog.records)


# --- the checks are scoped by endpoint, not by a property nobody writes ------
#
# Both relationship checks used to key on `r.graph_generation_id`. No writer in
# this codebase sets it -- `_compile_relationship_upsert` and
# `compile_relationship_reconciliation` MERGE the edge and set only the
# mutation's own properties -- so RELATIONSHIP_TYPE_POPULATED warned for every
# type on every build, and RELATIONSHIP_ENDPOINTS_SAME_GENERATION, an
# ERROR-severity check, could not match a row and had never once fired.


def test_no_relationship_check_depends_on_a_property_of_the_edge(
    active_schema: ActiveSchema,
) -> None:
    """The regression guard. A check keyed on an edge property the writers do
    not set is not a weaker check, it is a check that cannot fail -- and one
    that reads as green on exactly the builds it no longer understands."""
    checks = compile_validation_checks(active_schema, graph_generation_id="gen-1")
    relationship_checks = [
        check
        for check in checks
        if check.check_id
        in {
            ValidationCheckId.RELATIONSHIP_ENDPOINTS_SAME_GENERATION,
            ValidationCheckId.RELATIONSHIP_TYPE_POPULATED,
        }
    ]
    assert relationship_checks
    for check in relationship_checks:
        assert "r.graph_generation_id" not in check.statement.cypher
        assert "r:" in check.statement.cypher


def test_the_endpoint_check_counts_edges_touching_this_generation_on_one_side(
    active_schema: ActiveSchema,
) -> None:
    check = next(
        check
        for check in compile_validation_checks(active_schema, graph_generation_id="gen-1")
        if check.check_id is ValidationCheckId.RELATIONSHIP_ENDPOINTS_SAME_GENERATION
    )
    cypher = check.statement.cypher
    # One endpoint in, and either endpoint out.
    assert "s.graph_generation_id = $generationId" in cypher
    assert "t.graph_generation_id = $generationId" in cypher
    # `coalesce`, because a node written before generations existed carries no
    # property at all and `null <> $gen` drops the row rather than reporting it.
    assert "coalesce(s.graph_generation_id, '')" in cypher
    assert "coalesce(t.graph_generation_id, '')" in cypher
    assert check.severity is ValidationSeverity.ERROR
    assert check.violation_when_count_is_zero is False


def test_the_populated_relationship_check_requires_both_endpoints_in_generation(
    active_schema: ActiveSchema,
) -> None:
    check = next(
        check
        for check in compile_validation_checks(active_schema, graph_generation_id="gen-1")
        if check.check_id is ValidationCheckId.RELATIONSHIP_TYPE_POPULATED
    )
    assert check.statement.cypher.count("graph_generation_id: $generationId") == 2
    assert check.severity is ValidationSeverity.WARNING


# --- populated-ness severity is derived from the run ------------------------


def _node_check(schema: ActiveSchema, census: dict[str, int] | None) -> ValidationCheck:
    checks = compile_validation_checks(
        schema, graph_generation_id="gen-1", source_records_read=census
    )
    return next(
        check for check in checks if check.check_id is ValidationCheckId.NODE_LABEL_POPULATED
    )


def test_an_empty_label_from_an_empty_source_only_warns(active_schema: ActiveSchema) -> None:
    """`ReturnItem` and `ReturnHandlingUnit` are written by the platform's own
    return workflow. A deployment that has never processed a return projects
    zero of them, and a hardcoded ERROR there means it can never activate a
    generation, never run an order search, and therefore never process the first
    return that would populate them."""
    check = _node_check(active_schema, {"source_a": 0, "source_b": 0})
    assert check.severity is ValidationSeverity.WARNING
    finding = evaluate(check, 0)
    assert finding is not None
    assert "yielded no records" in finding.detail


def test_an_empty_label_from_a_source_that_had_records_is_still_an_error(
    active_schema: ActiveSchema,
) -> None:
    """The distinction that keeps the previous test from being a weakening. The
    build read the source and lost every record on the way to the graph -- a
    broken record_path, an unresolvable natural key, a projection that dropped
    the lot. That is the failure the check exists for."""
    check = _node_check(active_schema, {"source_a": 412, "source_b": 3})
    assert check.severity is ValidationSeverity.ERROR
    finding = evaluate(check, 0)
    assert finding is not None
    assert "read 412 record(s)" in finding.detail
    assert "none of them projected" in finding.detail


def test_no_census_keeps_the_strict_reading(active_schema: ActiveSchema) -> None:
    """Absence of evidence is not evidence of an empty source. A caller that
    cannot say what the run read gets exactly the behaviour that shipped before
    the census existed."""
    assert _node_check(active_schema, None).severity is ValidationSeverity.ERROR


def test_a_source_the_run_never_scanned_keeps_the_strict_reading(
    active_schema: ActiveSchema,
) -> None:
    """Absent and zero must not be the same observation. `GraphSyncService`'s
    counting connector registers a zero for every source it is asked to read, so
    a source missing from the census is one this run never touched -- about
    which it has nothing to say."""
    check = _node_check(active_schema, {"source_b": 0})
    assert check.severity is ValidationSeverity.ERROR
    finding = evaluate(check, 0)
    assert finding is not None
    assert "was not scanned by this run" in finding.detail


def test_a_populated_label_passes_whatever_the_census_says(active_schema: ActiveSchema) -> None:
    """Severity only decides how loudly a *zero* is reported. A label with rows
    in it is not a finding under any census."""
    for census in (None, {"source_a": 0}, {"source_a": 99}):
        assert evaluate(_node_check(active_schema, census), 7) is None


@pytest.mark.asyncio
async def test_the_orchestrator_hands_the_validator_what_the_build_read() -> None:
    """The census is read off the coordinator after both sync passes, so it
    reflects everything the build actually scanned."""

    class _CensusCoordinator(_QuietSyncCoordinator):  # type: ignore[misc, valid-type]
        def source_records_read(self) -> dict[str, int]:
            return {"source_a": 301, "source_return_items": 0}

    writer = _FakeGenerationWriter()
    validator = _Validator(GenerationValidationReport(graph_generation_id="ignored"))
    orchestrator = GenerationLifecycleOrchestrator(
        snapshot_store=_FakeSnapshotStore(),  # type: ignore[arg-type]
        lease_store=_FakeRebuildLeaseStore(),  # type: ignore[arg-type]
        generation_writer=writer,  # type: ignore[arg-type]
        sync_coordinator=_CensusCoordinator(),  # type: ignore[arg-type]
        fencing_tokens=_CountingTokens(),  # type: ignore[arg-type]
        owner_instance_id="test-instance",
        validator=validator,  # type: ignore[arg-type]
        drain_poll_seconds=0.01,
    )

    await orchestrator.build_and_activate(
        schema=_schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )

    assert validator.census == {"source_a": 301, "source_return_items": 0}


@pytest.mark.asyncio
async def test_a_coordinator_with_no_census_validates_without_one() -> None:
    """The census is evidence validation can use, not something a rebuild needs
    in order to run."""
    validator = _Validator(GenerationValidationReport(graph_generation_id="ignored"))
    await _orchestrator(
        _FakeGenerationWriter(), _FakeSnapshotStore(), validator
    ).build_and_activate(
        schema=_schema(),  # type: ignore[arg-type]
        snapshot_name="default",
        configuration_release_id="release-1",
    )
    assert validator.census is None


# --- the guard that keeps the census rule from being a weakening ------------
#
# `test_a_failed_candidate_leaves_n_active_and_still_serving` caught this
# against real infrastructure: the census rule alone let a *dropped* source
# activate an empty generation over a populated one, because a dropped
# collection and a legitimately-empty one both scan as zero. The difference is
# not visible at the connector, so it is taken from the graph instead.


def test_a_predecessor_adds_a_regression_check_per_label(active_schema: ActiveSchema) -> None:
    checks = compile_validation_checks(
        active_schema, graph_generation_id="gen-2", previous_generation_id="gen-1"
    )
    regressed = [
        check for check in checks if check.check_id is ValidationCheckId.NODE_LABEL_REGRESSED
    ]
    assert regressed, "a candidate with a predecessor must be checked against it"
    for check in regressed:
        assert check.severity is ValidationSeverity.ERROR
        assert check.violation_when_count_is_zero is False
        assert check.statement.parameters["previousGenerationId"] == "gen-1"
        assert check.statement.parameters["generationId"] == "gen-2"


def test_a_first_build_has_nothing_to_regress_against(active_schema: ActiveSchema) -> None:
    """No predecessor means no serving generation to damage. Emitting the check
    anyway would refuse every bootstrap, which is the defect this area exists to
    remove."""
    checks = compile_validation_checks(active_schema, graph_generation_id="gen-1")
    assert not [
        check for check in checks if check.check_id is ValidationCheckId.NODE_LABEL_REGRESSED
    ]


def test_a_generation_is_not_compared_against_itself(active_schema: ActiveSchema) -> None:
    checks = compile_validation_checks(
        active_schema, graph_generation_id="gen-1", previous_generation_id="gen-1"
    )
    assert not [
        check for check in checks if check.check_id is ValidationCheckId.NODE_LABEL_REGRESSED
    ]


def test_the_regression_finding_says_what_would_be_dropped(active_schema: ActiveSchema) -> None:
    check = next(
        check
        for check in compile_validation_checks(
            active_schema, graph_generation_id="gen-2", previous_generation_id="gen-1"
        )
        if check.check_id is ValidationCheckId.NODE_LABEL_REGRESSED
    )
    finding = evaluate(check, 600)
    assert finding is not None
    assert finding.severity is ValidationSeverity.ERROR
    assert "600 node(s)" in finding.detail
    assert "drop them from service" in finding.detail
    # And it passes when the candidate kept the label.
    assert evaluate(check, 0) is None


def test_an_emptied_source_warns_but_the_regression_guard_still_errors(
    active_schema: ActiveSchema,
) -> None:
    """The two halves together, which is the only reading that is safe.

    The census says "nothing to project" and warns -- correct, and what lets a
    fresh deployment activate. The regression guard independently says the
    serving generation had rows here -- which is what refuses the build.
    """
    checks = compile_validation_checks(
        active_schema,
        graph_generation_id="gen-2",
        source_records_read={"source_a": 0, "source_b": 0},
        previous_generation_id="gen-1",
    )
    populated = next(
        check for check in checks if check.check_id is ValidationCheckId.NODE_LABEL_POPULATED
    )
    regressed = next(
        check for check in checks if check.check_id is ValidationCheckId.NODE_LABEL_REGRESSED
    )
    assert populated.severity is ValidationSeverity.WARNING
    assert regressed.severity is ValidationSeverity.ERROR

    report = GenerationValidationReport(
        graph_generation_id="gen-2",
        findings=tuple(
            f for f in (evaluate(populated, 0), evaluate(regressed, 42)) if f is not None
        ),
    )
    assert not report.passed, "an emptied source must not activate over a populated generation"
