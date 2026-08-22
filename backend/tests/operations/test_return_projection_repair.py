"""The repair, and the four ways a repair goes wrong.

A repair is the most dangerous code in a remediation programme: it runs once,
against real data, usually under time pressure, and its mistakes are the kind
you cannot test after the fact. So these assert the safety properties before
they assert the behaviour -- it refuses a plan it was not approved for, it
refuses when its premise stops holding, it is idempotent, and it is reversible.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.operations.repair.return_projections import (
    REPAIR_MARKER,
    REPAIRED_STATUS,
    apply_repair,
    plan_repair,
    rollback_manifest,
)


class _Projections:
    """An in-memory `return_records`, with the conditional write that matters."""

    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents
        self.writes: list[tuple[str, str]] = []

    async def find_issued_without_items(self) -> list[dict[str, Any]]:
        return [
            document
            for document in self.documents
            if document.get("status") == "ISSUED" and not document.get("approvedItems")
        ]

    async def reclassify(
        self, return_record_id: str, *, status: str, marker: dict[str, Any]
    ) -> bool:
        for document in self.documents:
            if document.get("returnRecordId") != return_record_id:
                continue
            # Conditional: only a document still reading ISSUED is rewritten.
            if document.get("status") != "ISSUED":
                return False
            document["status"] = status
            document.update(marker)
            self.writes.append((return_record_id, status))
            return True
        return False


class _Authoritative:
    def __init__(self, records: int = 0, items: int = 0) -> None:
        self._records = records
        self._items = items

    async def count_records(self) -> int:
        return self._records

    async def count_items(self) -> int:
        return self._items


def _document(identifier: str, **overrides: Any) -> dict[str, Any]:
    return {
        "returnRecordId": identifier,
        "returnReference": f"RMA-{identifier}",
        "caseId": f"case-{identifier}",
        "status": "ISSUED",
        "approvedItems": [],
        "createdAt": "2026-08-15T15:56:07Z",
        **overrides,
    }


@pytest.mark.asyncio
async def test_it_finds_only_the_records_that_claim_more_than_the_store_holds() -> None:
    projections = _Projections(
        [
            _document("a"),
            _document("b", approvedItems=[{"orderLineId": "L1"}]),
            _document("c", status="AWAITING_RECEIPT"),
        ]
    )

    plan = await plan_repair(projections, _Authoritative())

    assert [target.return_record_id for target in plan.targets] == ["a"]
    assert plan.applicable


@pytest.mark.asyncio
async def test_it_refuses_when_the_authoritative_store_is_not_empty() -> None:
    """Then these are stale projections, and rebuilding them is a different repair.

    The premise of reclassifying is that SQL says nothing. If SQL says
    something, the honest repair is to re-project from it -- and quietly
    reclassifying instead would destroy the distinction between "we lost the
    projection" and "it was never written".
    """
    projections = _Projections([_document("a")])

    plan = await plan_repair(projections, _Authoritative(records=5, items=12))

    assert not plan.applicable
    assert plan.refusal is not None
    assert "stale" in plan.refusal

    with pytest.raises(ValueError, match="not applicable"):
        await apply_repair(plan, projections, approved_digest=plan.digest)


@pytest.mark.asyncio
async def test_it_refuses_a_manifest_it_was_not_approved_for() -> None:
    """The whole point of a dry run is that the apply is the plan that was read."""
    projections = _Projections([_document("a")])
    plan = await plan_repair(projections, _Authoritative())

    with pytest.raises(ValueError, match="approved manifest does not match"):
        await apply_repair(plan, projections, approved_digest="not-the-digest")

    assert projections.writes == [], "a refused apply must write nothing"


@pytest.mark.asyncio
async def test_the_digest_changes_when_the_targets_change() -> None:
    """Otherwise approving one plan would approve any later one."""
    first = await plan_repair(_Projections([_document("a")]), _Authoritative())
    second = await plan_repair(
        _Projections([_document("a"), _document("b")]), _Authoritative()
    )

    assert first.digest != second.digest


@pytest.mark.asyncio
async def test_the_digest_is_stable_across_dry_runs_over_the_same_data() -> None:
    """Otherwise `--apply` can never match, and someone reaches for a skip flag.

    The digest first included the timestamp the plan was taken, so two dry runs
    over identical data disagreed and the digest handed to an operator could
    never match the plan the apply run recomputed.
    """
    first = await plan_repair(_Projections([_document("a")]), _Authoritative())
    second = await plan_repair(_Projections([_document("a")]), _Authoritative())

    assert first.digest == second.digest


@pytest.mark.asyncio
async def test_it_reclassifies_rather_than_deleting() -> None:
    """These documents are the only trace of a promise made to a customer."""
    documents = [_document("a"), _document("b")]
    projections = _Projections(documents)
    plan = await plan_repair(projections, _Authoritative())

    outcome = await apply_repair(plan, projections, approved_digest=plan.digest)

    assert outcome.reclassified == 2
    assert outcome.complete
    assert len(projections.documents) == 2, "no document may be removed"
    for document in documents:
        assert document["status"] == REPAIRED_STATUS
        assert document["status"] != "ISSUED"
        # Identity survives, so the record is still evidence.
        assert document["returnReference"].startswith("RMA-")
        assert document["caseId"]
        assert document[REPAIR_MARKER]


@pytest.mark.asyncio
async def test_it_never_writes_ISSUED_back() -> None:
    """The one substitution that would be dangerous, asserted directly."""
    projections = _Projections([_document("a")])
    plan = await plan_repair(projections, _Authoritative())

    await apply_repair(plan, projections, approved_digest=plan.digest)

    assert all(status != "ISSUED" for _, status in projections.writes)


@pytest.mark.asyncio
async def test_applying_the_same_manifest_twice_changes_nothing() -> None:
    """A repair run under pressure gets run twice. It has to be safe to."""
    projections = _Projections([_document("a"), _document("b")])
    plan = await plan_repair(projections, _Authoritative())

    first = await apply_repair(plan, projections, approved_digest=plan.digest)
    second = await apply_repair(plan, projections, approved_digest=plan.digest)

    assert first.reclassified == 2
    assert second.reclassified == 0
    assert second.skipped == ("a", "b")
    assert len(projections.writes) == 2, "the second run must write nothing"


@pytest.mark.asyncio
async def test_a_record_that_changed_since_the_dry_run_is_skipped() -> None:
    """An operator reads a manifest between the plan and the apply.

    If a record moved on in that window, the plan is describing something that
    no longer exists and overwriting it would undo whatever moved it.
    """
    documents = [_document("a"), _document("b")]
    projections = _Projections(documents)
    plan = await plan_repair(projections, _Authoritative())

    documents[0]["status"] = "AWAITING_RECEIPT"
    outcome = await apply_repair(plan, projections, approved_digest=plan.digest)

    assert outcome.reclassified == 1
    assert outcome.skipped == ("a",)
    assert not outcome.complete, "a partial run must not report itself complete"
    assert documents[0]["status"] == "AWAITING_RECEIPT", "the newer state stands"


@pytest.mark.asyncio
async def test_the_rollback_manifest_restores_every_status() -> None:
    projections = _Projections([_document("a"), _document("b")])
    plan = await plan_repair(projections, _Authoritative())

    rollback = rollback_manifest(plan)

    assert {entry["returnRecordId"] for entry in rollback["restore"]} == {"a", "b"}
    assert all(entry["status"] == "ISSUED" for entry in rollback["restore"])
    # And the marker is removed, so a rolled-back document is indistinguishable
    # from one this never touched.
    assert REPAIR_MARKER in rollback["unset"]


@pytest.mark.asyncio
async def test_a_plan_with_nothing_to_do_is_not_applicable() -> None:
    """Nothing to repair is a result, not an error -- and not an apply run."""
    plan = await plan_repair(_Projections([]), _Authoritative())

    assert plan.targets == ()
    assert not plan.applicable
    assert plan.refusal is None, "an empty plan is not a refused one"


@pytest.mark.asyncio
async def test_the_manifest_carries_the_counts_the_decision_rested_on() -> None:
    """A manifest that records only the targets cannot be reviewed later."""
    projections = _Projections([_document("a")])
    plan = await plan_repair(projections, _Authoritative())

    manifest = plan.manifest

    assert manifest["authoritativeRecords"] == 0
    assert manifest["authoritativeItems"] == 0
    assert manifest["statusAfter"] == REPAIRED_STATUS
    assert manifest["targets"][0]["statusBefore"] == "ISSUED"
    assert manifest["targets"][0]["itemCount"] == 0
