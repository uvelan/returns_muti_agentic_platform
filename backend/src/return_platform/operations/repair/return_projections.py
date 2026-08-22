"""T19b: return projections that claim ISSUED over nothing.

**What was found.** Five documents in Mongo `return_records` read
`status: "ISSUED"` with an empty `approvedItems`, while `dbo.return_record` and
`dbo.return_record_item` -- the authoritative store -- hold zero rows between
them.

Those are the right two tables to compare against, and which tables are right is
not obvious. ADR-001 was resolved as option B: a console-issued RMA ticket is a
*distinct artifact* whose authoritative home is `dbo.return_requests`,
`dbo.return_items` and `integration.return_support_ticket`, while
`dbo.return_record` and `dbo.return_record_item` remain the case workflow's. The
Mongo collection this repairs is written by `case_repository.py` and keyed by
`caseId`, so it projects the *case* aggregate -- and comparing it against the
console path's tables (which do hold rows) would have concluded, wrongly, that
nothing was missing. So these are not projections that lost their items and can be rebuilt.
They are the only trace of returns that were never durably written, which is
UIAUDIT-010 seen from the other end: the RMA path wrote a projection and no
record.

**What repair means here, and what it does not.**

*Not deletion.* These documents are the sole surviving evidence that someone was
told a return had been issued. Deleting them would destroy the only record of a
promise the platform made to a customer, and the repair rules forbid deleting
return or financial truth outright.

*Not fabrication.* Writing the missing SQL rows would mean inventing a
`return_record_item` per document -- quantities, reason codes, order lines --
that nothing observed. An invented durable record is worse than an honest gap,
because the next reader cannot tell it apart from a real one.

*What is left is the truthful thing:* stop them presenting as issued. `ISSUED`
is a claim about the authoritative store, and the authoritative store says
nothing. `UNKNOWN` is the frozen vocabulary's answer for exactly this -- a status
the platform does not know -- and specifically never `ISSUED`, so a return cannot
present as issued because nobody recorded that it was not.

Every document keeps its identifiers, its case, its reference and its timestamps,
and gains a repair marker naming when and why it was reclassified.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

#: What a repaired document's status becomes.
#:
#: The frozen vocabulary's answer for "the platform does not know", and chosen
#: over any lifecycle status because every lifecycle status is a claim and there
#: is nothing here to base one on.
REPAIRED_STATUS: Final = "UNKNOWN"

#: Marks a document this repair touched, so a later reader can tell a
#: reclassified record from one that was written `UNKNOWN` in the first place.
REPAIR_MARKER: Final = "repairedBy"
REPAIR_ID: Final = "T19b-return-projection-status"


class ProjectionStore(Protocol):
    """The two operations this needs, so the repair is testable without Mongo."""

    async def find_issued_without_items(self) -> Sequence[dict[str, Any]]: ...

    async def reclassify(
        self, return_record_id: str, *, status: str, marker: dict[str, Any]
    ) -> bool:
        """Set the status, but only while the document still looks as planned.

        Returns whether a document was modified. A conditional write, because a
        dry run and its apply are separated by an operator reading a manifest --
        and a record that changed in between must not be overwritten by a plan
        made before it did.
        """


class AuthoritativeStore(Protocol):
    """The authority a projection is supposed to derive from."""

    async def count_records(self) -> int: ...

    async def count_items(self) -> int: ...


@dataclass(frozen=True, slots=True)
class Target:
    """One document, and why it qualifies."""

    return_record_id: str
    return_reference: str | None
    case_id: str | None
    status: str
    item_count: int
    created_at: str | None

    def as_manifest_entry(self) -> dict[str, Any]:
        return {
            "returnRecordId": self.return_record_id,
            "returnReference": self.return_reference,
            "caseId": self.case_id,
            "statusBefore": self.status,
            "itemCount": self.item_count,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class RepairPlan:
    """What an apply run would do, and the counts it was decided from."""

    targets: tuple[Target, ...]
    authoritative_records: int
    authoritative_items: int
    taken_at: str
    #: Set when the repair must not run. Present means the plan is not
    #: applicable, and `apply` refuses rather than proceeding.
    refusal: str | None = None
    _digest: str = field(default="", repr=False)

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "repairId": REPAIR_ID,
            "takenAt": self.taken_at,
            "authoritativeRecords": self.authoritative_records,
            "authoritativeItems": self.authoritative_items,
            "statusAfter": REPAIRED_STATUS,
            "refusal": self.refusal,
            "targets": [target.as_manifest_entry() for target in self.targets],
        }

    @property
    def digest(self) -> str:
        """Identifies this exact plan -- what it would do, not when it was made.

        An apply run quotes it back, so an operator cannot approve one dry run
        and apply a different one, which is the failure mode a repair with a
        manifest exists to prevent.

        `takenAt` is deliberately excluded. With it, two dry runs over identical
        data produced different digests, so the digest an operator was handed
        could never match the plan the apply run recomputed -- `--apply` would
        have refused every time, and the first person to hit that would have
        reached for a flag to skip the check.
        """
        content = {key: value for key, value in self.manifest.items() if key != "takenAt"}
        payload = json.dumps(content, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def applicable(self) -> bool:
        return self.refusal is None and bool(self.targets)


async def plan_repair(
    projections: ProjectionStore, authoritative: AuthoritativeStore
) -> RepairPlan:
    """Decide what to repair, from what both stores currently say.

    The authoritative counts are part of the plan rather than a precondition
    checked and forgotten, because they are what makes the reclassification
    correct. If SQL turns out to hold records after all, these projections are
    stale rather than baseless and the right repair is re-projection -- a
    different operation, which this one refuses to stand in for.
    """
    records = await authoritative.count_records()
    items = await authoritative.count_items()
    documents = await projections.find_issued_without_items()

    targets = tuple(
        Target(
            return_record_id=str(document.get("returnRecordId")),
            return_reference=_optional_str(document.get("returnReference")),
            case_id=_optional_str(document.get("caseId")),
            status=str(document.get("status")),
            item_count=len(document.get("approvedItems") or []),
            created_at=_optional_str(document.get("createdAt")),
        )
        for document in documents
    )

    refusal: str | None = None
    if records or items:
        refusal = (
            f"The authoritative store holds {records} records and {items} items. "
            "These projections may be stale rather than baseless, and rebuilding "
            "them from SQL is a different repair than reclassifying them."
        )

    return RepairPlan(
        targets=targets,
        authoritative_records=records,
        authoritative_items=items,
        taken_at=datetime.now(UTC).isoformat(),
        refusal=refusal,
    )


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    """What an apply run actually changed."""

    attempted: int
    reclassified: int
    skipped: tuple[str, ...]
    manifest_digest: str

    @property
    def complete(self) -> bool:
        return not self.skipped


async def apply_repair(
    plan: RepairPlan, projections: ProjectionStore, *, approved_digest: str
) -> RepairOutcome:
    """Reclassify every target, or refuse.

    Idempotent by construction: the conditional write matches on the status the
    plan recorded, so a document already reclassified is skipped rather than
    written twice, and a second apply of the same manifest changes nothing.
    """
    if plan.refusal is not None:
        raise ValueError(f"This plan is not applicable: {plan.refusal}")
    if approved_digest != plan.digest:
        raise ValueError(
            "The approved manifest does not match this plan. Re-run the dry run "
            "and approve the plan you intend to apply."
        )

    marker = {
        REPAIR_MARKER: REPAIR_ID,
        "repairedAt": datetime.now(UTC).isoformat(),
        "manifestDigest": plan.digest,
        "reason": (
            "Reclassified from ISSUED: the authoritative store held no record or "
            "item for this return, so ISSUED was a claim nothing supported."
        ),
    }

    skipped: list[str] = []
    reclassified = 0
    for target in plan.targets:
        changed = await projections.reclassify(
            target.return_record_id, status=REPAIRED_STATUS, marker=marker
        )
        if changed:
            reclassified += 1
        else:
            skipped.append(target.return_record_id)

    return RepairOutcome(
        attempted=len(plan.targets),
        reclassified=reclassified,
        skipped=tuple(skipped),
        manifest_digest=plan.digest,
    )


def rollback_manifest(plan: RepairPlan) -> dict[str, Any]:
    """Everything needed to put the documents back exactly as they were.

    Statuses only. Nothing else is touched, so nothing else needs restoring --
    which is the property that makes this repair reversible at all.
    """
    return {
        "repairId": REPAIR_ID,
        "manifestDigest": plan.digest,
        "restore": [
            {"returnRecordId": target.return_record_id, "status": target.status}
            for target in plan.targets
        ],
        "unset": [REPAIR_MARKER, "repairedAt", "manifestDigest", "reason"],
    }


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)
