"""What the graph knew about one warehouse and its bays (W2.7).

Bay placement read its candidates straight out of SQL Server, which R2 forbids
and which carried a defect the bypass made invisible:

    WHERE (%s IS NULL OR configuration.warehouse_id = %s)

A return whose `processingWarehouseReference` was never set passed `None` there,
the predicate collapsed to true, and the agent was handed **every bay in every
warehouse** to rank -- then staged a parcel into one of them. Nothing anywhere
said the warehouse was unknown; the absence of a reference produced a longer
candidate list, not a shorter one.

That is the same defect class W2.6 removed from fulfillment, in the other
direction. `IN_TRANSIT` was concluded from a tracking reference existing rather
than from a shipment being seen; here a *missing* reference was treated as
"no constraint" rather than as "nothing observed". Both replace an observation
with the presence or absence of a reference.

So a warehouse now has to be **observed**: a `Warehouse` node the graph actually
holds for that id, reached after a targeted sync of the source that backs it. The
three outcomes are kept apart for the same reason `ShipmentEvidence` keeps its
three apart -- "there is no such warehouse", "there is one and it has no eligible
bay", and "we could not look" oblige different things of whoever reads them, and
collapsing them is how the SQL predicate above stayed unnoticed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

__all__ = [
    "BayEvidence",
    "WarehouseBayObservationPort",
    "WarehouseObservation",
]


class BayEvidence(StrEnum):
    """Why the candidate list is what it is.

    OBSERVED     a Warehouse node exists for this reference. The candidates are
                 its bays -- possibly none, which is a real state and not an
                 error: a warehouse with every bay inactive has no candidate.
    ABSENT       no warehouse was observed. Either the return carries no
                 reference at all or the graph holds no such warehouse, and
                 `absent_reason` says which.
    UNAVAILABLE  the graph could not be read. Nothing is known either way, and a
                 caller must not read this as "no bay".
    """

    OBSERVED = "OBSERVED"
    ABSENT = "ABSENT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class WarehouseObservation:
    """One reading of one warehouse's bay capacity.

    `candidates` is bay *configuration* as the graph holds it -- id, type,
    branch, the shipping paths and product types the bay supports, its hazmat and
    oversize flags, and its declared maximum capacity.

    **It carries no live reservation figure, and that is a limit rather than an
    omission.** Available capacity in the SQL query it replaces was
    `max_capacity - SUM(reserved) WHERE expires_at > SYSUTCDATETIME()`: an
    aggregate over unexpired reservations evaluated at the instant of the query.
    A graph node cannot hold that -- the value changes with the clock and not
    with any source write, so no sync however targeted makes it current. Callers
    get the declared maximum and the reservation is enforced where it always was,
    in the transactional `reserve_and_assign_handling_unit`, which is the only
    place that can hold a lock over the decision anyway.
    """

    warehouse_reference: str | None
    evidence: BayEvidence
    graph_generation_id: str | None = None
    candidates: tuple[dict[str, Any], ...] = ()
    #: Which kind of absence, when `evidence` is ABSENT. `NO_WAREHOUSE_REFERENCE`
    #: is the return never having been given one; `WAREHOUSE_NOT_IN_GRAPH` is a
    #: reference that resolves to nothing.
    absent_reason: str | None = None
    #: Set when a targeted sync ran for this warehouse before the read.
    sync_request_id: str | None = None
    #: Why no sync ran, or why it failed. Not fatal -- a scheduled sync may
    #: already have brought the warehouse in, so the read still happens.
    sync_skipped_reason: str | None = None
    #: Why the graph could not be read at all. Only set with UNAVAILABLE.
    unavailable_reason: str | None = None

    @property
    def evidence_reference(self) -> str:
        """One audit string per reading, in the shape the return event log uses.

        Every component is a code rather than prose: these are validated as
        identifiers wherever they are recorded.
        """
        if self.evidence is BayEvidence.OBSERVED:
            return f"WAREHOUSE_OBSERVED:{self.graph_generation_id}:{len(self.candidates)}"
        if self.evidence is BayEvidence.ABSENT:
            return f"WAREHOUSE_ABSENT:{self.absent_reason or 'UNKNOWN'}"
        return f"WAREHOUSE_UNAVAILABLE:{self.unavailable_reason or 'UNKNOWN'}"


class WarehouseBayObservationPort(Protocol):
    """What placement needs from the graph, and nothing more.

    Structural, so `operations` neither imports `dynamic_knowledge` nor learns
    what a generation lease is -- the adapter is
    `dynamic_knowledge/integration/bay_observations.py`.

    It raises only when the graph cannot be read. "No such warehouse" is a
    returned observation, because it is an answer.
    """

    async def observe(self, warehouse_reference: str | None) -> WarehouseObservation: ...
