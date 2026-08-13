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
    ValidationCheckId,
    ValidationFinding,
    ValidationSeverity,
    evaluate,
)
from return_platform.dynamic_knowledge.lifecycle.orchestrator import (
    ActivationError,
    GenerationLifecycleOrchestrator,
)
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

    async def validate(self, *, schema: object, graph_generation_id: str) -> object:
        self.calls += 1
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
