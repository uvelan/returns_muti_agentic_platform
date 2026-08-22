"""The one place an issued return becomes an authoritative SQL row.

Two paths issue RMAs and only one of them wrote the authoritative store. The
Temporal case workflow assembled `CaseReturnRecordsWrite` inside
`ReturnCaseActivities._persist_records_to_return_store` and called
`persist_case_return_records`; the Support RMA-ticket path
(`RmaTicketService.set_status`) moved a ticket row in
`integration.return_support_ticket` and stopped there. So an RMA issued from the
Support console produced no `dbo.return_record`, no `dbo.return_record_item` and
no `dbo.return_tracking`, while the screen said all three were written.

This module is the seam the two share. It owns the part that must not differ
between them:

* how a return item's identity is derived, so a retry rewrites its row instead
  of inserting a second one under a fresh key;
* the mapping onto the SQL store's write contract;
* the rule that issuance never writes tracking.

It does **not** own how either caller discovers its records. The workflow reads a
Mongo case and a merge plan; Support reads its own ticket rows. Those stay where
they are -- forcing one shape on both would put the workflow's case model into
the Support path, which is the coupling this seam exists to avoid.

**Issuance writes no `dbo.return_tracking` row.** Support states a carrier and a
tracking number on an RMA before any carrier has filed a scan, and that row
requires a `tracking_type` and an `event_at` that nothing has observed yet. The
`dbo.return_record.carrier` and `.tracking_reference` columns carry Support's
statement; `dbo.return_tracking` carries observations, written by
`record_shipment_update` when one actually arrives. `case_projection/assembly.py`
and `sql_business_state.py` both say so at the point they decline to write it,
and `sql_migrations/008_return_record_carrier.sql` is where the distinction
landed. A screen that claims tracking was written at issuance is wrong about the
store, not about the schema.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from return_platform.operations.sql_business_state import CaseReturnRecordsWrite

# `sql_business_state` imports `pymssql` at module scope, so it is imported for
# real only inside the one function that builds its types. That keeps this module
# -- and therefore `ReturnRecordStorePort` and everything that declares a
# dependency on it -- importable by `workflows`, which must not learn what a
# connection pool is just by being imported.

#: Namespace for derived return-item ids.
#:
#: `uuid5` over (record id, order line) rather than a minted `uuid4`: the record
#: id is supplied by the caller and is stable across a retry, so the item id has
#: to be stable too. A minted id would insert a second item row under a new
#: primary key on the second attempt, and the RMA would carry the same line
#: twice.
_ITEM_NAMESPACE = uuid.NAMESPACE_URL


def derive_return_item_id(return_record_id: str, order_line_id: str) -> str:
    """The stable identity of one line on one RMA."""
    return str(uuid.uuid5(_ITEM_NAMESPACE, f"return-item:{return_record_id}:{order_line_id}"))


@dataclass(frozen=True, slots=True)
class IssuanceItem:
    """One order line coming back on one RMA.

    Quantity defaults to 1 because both callers reach fields that may be absent
    -- a Mongo case item without a recorded quantity, a ticket line the agent
    could not establish -- and one unit is the reading that matches what the
    workflow already did rather than a new guess.
    """

    order_line_id: str
    quantity: int = 1
    product_id: str | None = None
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class IssuanceRecord:
    """One RMA, with the fulfilment identity that belongs to it and not the case."""

    return_record_id: str
    return_reference: str
    items: tuple[IssuanceItem, ...] = ()
    label_reference: str | None = None
    #: Support's statement of the tracking number, which is a property of the
    #: record. It is *not* a tracking observation -- see the module docstring.
    tracking_reference: str | None = None
    return_location: str | None = None
    shipping_instruction_reference: str | None = None
    return_method: str | None = None
    carrier: str | None = None


@dataclass(frozen=True, slots=True)
class IssuanceIntent:
    """Everything one issuance adds to the authoritative store.

    A whole-case unit rather than a per-record one, because T-14 requires the
    records of one outcome to commit together: a case with RMA-A persisted and
    RMA-B lost is exactly the partial state the single transaction prevents.
    """

    case_id: str
    tenant_id: str
    principal_id: str
    order_reference: str | None = None
    records: tuple[IssuanceRecord, ...] = field(default_factory=tuple)


class ReturnRecordStorePort(Protocol):
    """The authoritative SQL return store, as issuance needs it (T-14).

    Structural, so a caller does not import the SQL package to declare a
    dependency on it; the adapter is
    `operations/sql_business_state.py::SQLBusinessStateRepository`.

    It must persist every record of one outcome in a single transaction and be
    idempotent on the supplied ids, because both callers retry.

    Declared here rather than in `workflows/return_case_activities.py`, where it
    used to live. Issuance is an operations concern that the workflow calls, not
    a workflow concern the operations layer borrows, and there must be exactly
    one declaring port -- `test_return_persistence_paths_stay_partitioned`
    enforces that, because a second port is how a second persistence path starts.
    """

    async def persist_case_return_records(self, write: Any) -> tuple[str, ...]: ...


def build_case_return_records_write(intent: IssuanceIntent) -> CaseReturnRecordsWrite:
    """Map an intent onto the SQL store's write contract.

    Separate from `issue` so a caller can assert what would be written without
    writing it, and so the mapping is testable without a database.
    """
    from return_platform.operations.sql_business_state import (
        CaseReturnRecordsWrite,
        ReturnRecordItemWrite,
        ReturnRecordWrite,
    )

    records = tuple(
        ReturnRecordWrite(
            return_record_id=record.return_record_id,
            return_reference=record.return_reference,
            label_reference=record.label_reference,
            tracking_reference=record.tracking_reference,
            return_location=record.return_location,
            shipping_instruction_reference=record.shipping_instruction_reference,
            return_method=record.return_method,
            carrier=record.carrier,
            items=tuple(
                ReturnRecordItemWrite(
                    return_item_id=derive_return_item_id(
                        record.return_record_id, item.order_line_id
                    ),
                    order_line_id=item.order_line_id,
                    quantity=item.quantity,
                    product_id=item.product_id,
                    reason_code=item.reason_code,
                )
                for item in record.items
            ),
        )
        for record in intent.records
    )
    return CaseReturnRecordsWrite(
        case_id=intent.case_id,
        tenant_id=intent.tenant_id,
        principal_id=intent.principal_id,
        order_reference=intent.order_reference,
        records=records,
    )


class ReturnIssuance:
    """Persist issued returns, idempotently, in one transaction."""

    def __init__(self, store: ReturnRecordStorePort) -> None:
        self._store = store

    async def issue(self, intent: IssuanceIntent) -> tuple[str, ...]:
        """Write the case and all of its RMAs, and answer with the record ids.

        An intent carrying no records writes nothing and answers with nothing,
        matching `persist_case_return_records` -- an outcome that issued no RMA
        is a real outcome, not an error.

        The returned ids are the records that committed, so a caller
        synchronizes exactly those and never a wider set.
        """
        if not intent.records:
            return ()
        return await self._store.persist_case_return_records(
            build_case_return_records_write(intent)
        )
