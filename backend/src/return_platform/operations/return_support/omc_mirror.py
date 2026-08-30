"""The real `omc.return.update` mirror (contracts.md sect. 5).

Until this file existed, `enqueue_omc_update` was a `Protocol`, one call site,
and a stub in a test. The analyser's `omc` parameter defaulted to `None`, so a
production wiring that simply forgot it would drop the mirror in silence and
every test would still pass -- the tests supplied the thing production had no
implementation of. The default is gone with this file: `SupportMessageAnalyser`
now requires an `omc`, because the failure mode of an optional port is that
nobody notices it is absent.

**Two writes, in this order, both keyed by the same derived delivery id.**

1. `omc_command_records` -- the mirror row. sect. 5 names this collection, and
   `commandId` and `idempotencyKey` are both already uniquely indexed on it
   (`repository.ensure_indexes`), so once-only is enforced by the database and
   not by a check in this file.
2. `integration_outbox` on topic `omc.return.update` -- the delivery.

Record first, then enqueue. A crash between them leaves a mirror row that has
not been delivered, which a redelivery completes. The other order would let a
delivery go out with nothing on file saying it did, and that one is not
recoverable by retrying.

**Not `record_omc_command`.** The existing session-plane mirror
(`return_support/service.py:1166`) writes through it, and this deliberately does
not: that method requires a `session_id`, and a case-plane support work item has
`sessionId: None` on purpose -- "a case is the thing", as `service.py` puts it
where it sets that field. Passing a case id into a field named `sessionId` would
put a lie in the audit trail to save six lines. The row written here carries
`caseId`, `supportEventId` and `returnRecordId` instead, and keeps every field
name the session-plane row uses so an operator's queries still work across both.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, Final, Protocol

from return_platform.operations.models import utc_now

#: uuid5 namespace for omc mirror delivery identities. Fixed, so the derivation
#: is stable across processes and deployments -- a namespace minted at import
#: would make every restart a new identity for the same business change.
_OMC_DELIVERY_NAMESPACE: Final = uuid.UUID("6f2f3a6e-2c1c-5c4b-9a3d-0b7f4d2e8a11")

#: `operation` on the mirror row. The session-plane rows use
#: `CREATE_CUSTOMER_RETURN` / `RECORD_RETURN_CREATION`; this is the third kind
#: and says what it is: an artifact Support sent, bound to a record.
OMC_RECORD_SUPPORT_ARTIFACT: Final = "RECORD_SUPPORT_ARTIFACT"

#: Human-readable prefix on the derived delivery id. Present so an operator
#: reading the outbox can tell what kind of row this is without a join; the
#: uniqueness is entirely in the uuid5 that follows it.
DELIVERY_ID_PREFIX: Final = "omc-return-update"


def derive_omc_delivery_id(
    *,
    case_id: str,
    support_event_id: str,
    return_record_id: str,
    artifact_type: str,
    value: str,
) -> str:
    """The delivery identity for one mirrored artifact binding.

    **Derivation (design decision 1 of 2, sect. 7: "keyed by delivery identity").**
    Five parts: the case, the support event that carried the artifact, the record
    it bound to, the kind of artifact, and its value.

    *Event-scoped rather than content-only.* sect. 7 defines a delivery identity
    as derived from the operation, not from the payload. Two separate support
    messages that each mention the same tracking number for the same record are
    two deliveries and get two rows; the receiver is what collapses them, which
    is the arrangement sect. 7 already describes everywhere else.

    *Content-keyed rather than position-keyed within the event.* The previous
    derivation used the artifact's index in `bind_artifacts`' output. That is a
    hidden dependence on iteration order: reorder the accepted extraction's
    artifact list and every key moves, so a redelivery mirrors the same bindings
    a second time under different identities. The index is gone. Two artifacts in
    one event that are genuinely identical -- same record, same type, same value
    -- are one change and mirror once, which is what the receiver would do with
    them anyway.

    *Length-prefixed, not merely joined.* `value` is a model's reading of
    support-authored text: it can contain `:` and `|` because a person on the
    other end can type them. Without the prefixes a sender who can influence one
    part can forge a boundary and land a mirror row on another record's identity.
    """
    parts = (case_id, support_event_id, return_record_id, artifact_type, value)
    encoded = "|".join(f"{len(part)}:{part}" for part in parts)
    return f"{DELIVERY_ID_PREFIX}:{uuid.uuid5(_OMC_DELIVERY_NAMESPACE, encoded)}"


def _digest(payload: Mapping[str, Any]) -> str:
    """The same canonical digest shape the session-plane mirror uses."""
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OmcMirrorRepositoryPort(Protocol):
    """The two things the mirror needs from `OperationalRepository`.

    Structural, so this module does not import the repository's construction
    requirements into every test that wants to assert a mirror row.
    `OperationalRepository` satisfies it as it stands.
    """

    omc_command_records: Any

    async def enqueue_integration_command(
        self,
        *,
        topic: str,
        aggregate_type: str,
        aggregate_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]: ...


class DurableOmcMirror:
    """`OmcMirrorPort` over `omc_command_records` + the integration outbox.

    **The transaction claim (design decision 2 of 2).** sect. 5 says the mirror
    row is "enqueued in the artifact-persistence transaction". It is not, and it
    cannot be without changing a module this slice does not own:

    * There is no artifact-persistence transaction. S1's
      `persist_binding_decision` takes no session, and neither
      `ReturnRecordStorePort` nor `ScopedFactAppendPort` has a session
      parameter to thread one through. The record merge is an optimistic
      `expected_version` CAS, not a Mongo transaction.
    * Even given one, it would have to span `return_records`,
      `omc_command_records` and `integration_outbox`. sect. 6's own
      transactional-boundary rule forbids claiming a cross-store transaction
      that is not co-located, and these are three collections reached through
      two different modules.

    **What holds instead, and it is weaker than sect. 5 says.** The mirror is
    *eventually* once, not *atomically* once. Every step is idempotent under the
    same derived key -- the merge writes nothing when the record already holds
    the value, the mirror row is refused by a unique index, the outbox row is a
    `$setOnInsert` upsert -- and they run in an order where every crash point
    leaves either nothing yet written or a state that a rerun completes:

        merge record -> mirror row -> outbox row

    The classify command is acked only after `analyse` returns, so a crash at any
    point is followed by a redelivery, and the redelivery re-derives identical
    keys from the *accepted* extraction frozen on the analysis record. The window
    in which a record is merged and the mirror is not yet enqueued is real and is
    bounded by the outbox's redelivery interval.

    **This is what made the guarantee false before, and it was not the missing
    transaction.** The mirror used to be gated on whether the merge had written
    anything. On the redelivery that was supposed to close the window, the merge
    was a no-op -- the value was already there -- so the mirror was skipped and
    the row was lost for good. The gate is now the binding *decision*, which is a
    pure function of the accepted extraction and therefore the same on every
    attempt. A crash-shaped fault reproduces the old loss and not the new one.
    """

    def __init__(
        self,
        repository: OmcMirrorRepositoryPort,
        *,
        topic: str,
        actor_id: str,
        aggregate_type: str = "RETURN_CASE",
    ) -> None:
        self._repository = repository
        self._topic = topic
        self._actor_id = actor_id
        self._aggregate_type = aggregate_type

    async def enqueue_omc_update(
        self,
        *,
        case_id: str,
        support_event_id: str,
        delivery_id: str,
        payload: Mapping[str, Any],
    ) -> str:
        """Mirror one bound artifact and queue its delivery. Returns the key.

        Returns the delivery id on every path, including the two no-op ones (the
        row already exists; the outbox row already exists), because the caller
        records what was mirrored and "already mirrored" is still mirrored.
        """
        request_payload = dict(payload)
        request_digest = _digest(request_payload)
        command_id = str(uuid.uuid5(_OMC_DELIVERY_NAMESPACE, delivery_id))
        now = utc_now()
        document = {
            "_id": command_id,
            "commandId": command_id,
            "idempotencyKey": delivery_id,
            # `sessionId: None` deliberately -- see the module docstring. A case
            # is the thing, and `service.py` sets the same field the same way
            # when it opens a case-plane work item.
            "sessionId": None,
            "caseId": case_id,
            "supportEventId": support_event_id,
            "returnRecordId": request_payload.get("returnRecordId"),
            "supportWorkItemId": None,
            "operation": OMC_RECORD_SUPPORT_ARTIFACT,
            "requestDigest": request_digest,
            "requestPayload": request_payload,
            "status": "PENDING",
            "attemptCount": 0,
            "authoritativeReturnReference": None,
            "authoritativeVersion": None,
            "responsePayload": None,
            "readbackDigest": None,
            "errorCode": None,
            "errorMessage": None,
            "createdBy": self._actor_id,
            "createdAt": now,
            "updatedAt": now,
        }
        # `$setOnInsert` on the unique `idempotencyKey` rather than an insert and
        # a caught duplicate: the two are equivalent on the happy path, and this
        # one does not depend on the driver double raising exactly the exception
        # the real driver raises.
        await self._repository.omc_command_records.update_one(
            {"idempotencyKey": delivery_id},
            {"$setOnInsert": document},
            upsert=True,
        )
        # Read back, and send *the stored row's* commandId rather than the one
        # just computed. On the crash path this matters: the row was written on
        # an earlier attempt, this attempt's insert is a no-op, and a payload
        # built from a locally-computed id would deliver a `commandId` naming a
        # record that does not exist. Reading back makes the outbox row point at
        # whatever is actually on file, whoever wrote it.
        stored = await self._repository.omc_command_records.find_one(
            {"idempotencyKey": delivery_id}
        )
        if stored is None:  # pragma: no cover - the upsert above just wrote it
            raise RuntimeError(f"omc mirror row vanished for delivery {delivery_id!r}")
        await self._repository.enqueue_integration_command(
            topic=self._topic,
            aggregate_type=self._aggregate_type,
            aggregate_id=case_id,
            idempotency_key=delivery_id,
            payload={
                "commandId": str(stored["commandId"]),
                "deliveryId": delivery_id,
                **request_payload,
            },
        )
        return delivery_id


__all__ = [
    "DELIVERY_ID_PREFIX",
    "OMC_RECORD_SUPPORT_ARTIFACT",
    "DurableOmcMirror",
    "OmcMirrorRepositoryPort",
    "derive_omc_delivery_id",
]
