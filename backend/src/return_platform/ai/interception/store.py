"""Durable storage for held AI requests, replacing the filesystem handoff.

The request payload is **sealed**, not stored in the clear. It is the exact
prompt a provider would have received, which for the schema analyzer includes
block 5 UNTRUSTED SOURCE SAMPLE -- rows read out of a customer's database. The
`ai_interceptions` structure is declared `encrypted: true` for that reason, so
the store layer itself refuses a plaintext write; the metadata left outside the
envelope is deliberately only what an operator console must filter on.

Status transitions are compare-and-set on the same document that holds the
answer, so "record the answer" and "mark it answered" cannot come apart. Two
operators racing to answer the same interception is a real scenario -- the loser
is told the interception is no longer pending rather than silently overwriting
the winner's text.

**A lapsed request is closed in two places, and both are needed.**
`InterceptionExpirySweep` performs the `PENDING -> EXPIRED` transition on a
housekeeping interval, and `list_pending` hides a record whose deadline has
passed regardless of when the sweep last ran. Either alone is wrong: without the
sweep the rows accumulate `PENDING` forever and the status is a lie, and without
the read filter every interval opens a window in which the queue still offers
work whose caller has already given up.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from return_platform.ai.interception.records import (
    Interception,
    InterceptionPoint,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.platform.secrets.envelope import EnvelopeEncryptor, EnvelopePayload
from return_platform.platform.system_store.repository import SystemStore

AI_INTERCEPTIONS = "ai_interceptions"

#: Everything an operator console needs to list and filter, and nothing that
#: describes the request's content. A task id is a routing label; a prompt is
#: not, and letting one leak into plaintext metadata would defeat sealing the
#: payload at all. Public because the resume dispatcher writes through the same
#: guarded path and must declare the identical allowlist.
METADATA_FIELDS = frozenset(
    {
        "interception_id",
        "task_id",
        "status",
        # Which of the two hold points this is. Metadata rather than sealed
        # content for the same reason `task_id` is: it is a routing label an
        # operator filters the queue on, and it says nothing about what the
        # request asked or what the model replied.
        "point",
        "resume",
        "created_at",
        "expires_at",
        "answered_at",
        "answered_by",
    }
)


class InterceptionNotPending(RuntimeError):
    """The interception was already answered, cancelled, or expired.

    Raised rather than returning False so a caller cannot ignore it: answering
    twice means one of the two answers is being discarded, and which one is not
    something the loser should have to infer.
    """


class InterceptionStore(Protocol):
    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: Mapping[str, Any],
        resume: ResumeCommand,
        expires_at: datetime,
        #: Defaulted so every existing caller keeps compiling and keeps meaning
        #: what it meant. Only the response-review policy passes `RESPONSE`.
        point: InterceptionPoint = InterceptionPoint.REQUEST,
    ) -> Interception: ...

    async def get(self, interception_id: str) -> Interception | None: ...

    async def request_payload(self, interception_id: str) -> Mapping[str, Any] | None: ...

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception: ...

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception: ...

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None: ...

    async def list_pending(self, *, limit: int = 100) -> list[Interception]: ...


class SystemStoreInterceptionStore:
    def __init__(self, system_store: SystemStore, encryptor: EnvelopeEncryptor) -> None:
        self._store = system_store
        self._encryptor = encryptor

    def _seal(self, raw: bytes) -> dict[str, Any]:
        payload = self._encryptor.encrypt(raw)
        return {
            "ciphertext": payload.ciphertext,
            "key_ref": payload.key_ref,
            "algorithm": payload.algorithm,
            "version": payload.version,
        }

    def _unseal(self, envelope: Mapping[str, Any]) -> bytes:
        return self._encryptor.decrypt(
            EnvelopePayload(
                ciphertext=envelope["ciphertext"],
                key_ref=envelope["key_ref"],
                algorithm=envelope["algorithm"],
                version=envelope["version"],
            )
        )

    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: Mapping[str, Any],
        resume: ResumeCommand,
        expires_at: datetime,
        point: InterceptionPoint = InterceptionPoint.REQUEST,
    ) -> Interception:
        """Write the held request and its resume command as one document.

        The resume command travels *with* the request precisely so a crash
        between "held the request" and "recorded how to resume it" is not
        representable.

        At `RESPONSE` the sealed payload also carries the model's reply under
        `modelResponse`. The parameter is still called `request_payload` because
        the request is still in there -- an operator judging an answer needs the
        question -- and renaming it would touch every caller to say the same
        thing.
        """
        now = datetime.now(UTC)
        raw = json.dumps(dict(request_payload), sort_keys=True).encode("utf-8")
        await self._store.insert_one(
            AI_INTERCEPTIONS,
            {
                "_id": interception_id,
                "interception_id": interception_id,
                "task_id": task_id,
                "status": InterceptionStatus.PENDING.value,
                "point": point.value,
                "resume": {
                    "run_id": resume.run_id,
                    "thread_id": resume.thread_id,
                    "workflow_id": resume.workflow_id,
                },
                "created_at": now,
                "expires_at": expires_at,
                "answered_at": None,
                "answered_by": None,
                "_envelope": self._seal(raw),
            },
            allowed_metadata_fields=METADATA_FIELDS,
        )
        return Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=now,
            expires_at=expires_at,
            point=point,
        )

    async def get(self, interception_id: str) -> Interception | None:
        document = await self._store.read_only(AI_INTERCEPTIONS).find_one(
            {"interception_id": interception_id}
        )
        return None if document is None else _from_document(document)

    async def request_payload(self, interception_id: str) -> Mapping[str, Any] | None:
        """The prompt a human is being asked to answer, unsealed.

        Separate from `get` on purpose: listing pending work must not decrypt
        every held prompt, so the payload is fetched only when someone actually
        opens one to answer it.
        """
        document = await self._store.read_only(AI_INTERCEPTIONS).find_one(
            {"interception_id": interception_id}
        )
        if document is None or "_envelope" not in document:
            return None
        loaded: Mapping[str, Any] = json.loads(self._unseal(document["_envelope"]))
        return loaded

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        """Record the answer and the transition in one conditional write.

        Filtered on `status: PENDING`, so two operators answering at once
        produce one winner and one `InterceptionNotPending` -- never a silent
        overwrite of somebody's text.

        `response_text` is stored inside the envelope alongside the request: a
        human answer to a prompt containing customer data is itself likely to
        contain customer data, and sealing one while leaving the other in the
        clear would be theatre.

        **The same write serves both points.** At `REQUEST` this records an
        answer a human wrote; at `RESPONSE` it records a human's edit of what
        the model returned. The store does not need to know which -- the
        transition, the sealing and the race are identical -- and the record's
        `point` is what tells a reader how to attribute the text.
        """
        now = datetime.now(UTC)
        document = await self._store.read_only(AI_INTERCEPTIONS).find_one(
            {"interception_id": interception_id}
        )
        if document is None:
            raise InterceptionNotPending(f"interception {interception_id!r} does not exist")
        if document.get("status") != InterceptionStatus.PENDING.value:
            raise InterceptionNotPending(
                f"interception {interception_id!r} is {document.get('status')}, not PENDING"
            )

        payload: dict[str, Any] = json.loads(self._unseal(document["_envelope"]))
        payload["responseText"] = response_text
        updated = dict(document)
        updated.update(
            {
                "status": InterceptionStatus.ANSWERED.value,
                "answered_at": now,
                "answered_by": answered_by,
                "_envelope": self._seal(json.dumps(payload, sort_keys=True).encode("utf-8")),
            }
        )
        result = await self._store.replace_one(
            AI_INTERCEPTIONS,
            {"interception_id": interception_id, "status": InterceptionStatus.PENDING.value},
            updated,
            allowed_metadata_fields=METADATA_FIELDS,
        )
        # `replace_one` returns pymongo's UpdateResult, not a bool; matched_count
        # is what says the compare-and-set filter still held at write time.
        if getattr(result, "matched_count", 0) != 1:
            raise InterceptionNotPending(
                f"interception {interception_id!r} stopped being PENDING during the write"
            )
        return _from_document(updated, response_text=response_text)

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception:
        """Approve the held request unchanged: the model, not a human, answers it.

        The same compare-and-set discipline as `answer`, for the same reason --
        an operator approving while another answers must produce one winner, not
        a request that is both approved and substituted.

        Deliberately writes **no** response text and does not touch the sealed
        envelope. Approval is a statement about the request, not about its
        answer, and inventing an empty answer here is exactly how an approved
        request would later be mistaken for a human-answered one.

        `allowed_by` reuses the `answered_by` column because the question it
        answers is the same -- which operator acted -- and a second near-identical
        column is how an audit query comes to cover half the actions. `status`
        distinguishes what they did.
        """
        now = datetime.now(UTC)
        document = await self._store.read_only(AI_INTERCEPTIONS).find_one(
            {"interception_id": interception_id}
        )
        if document is None:
            raise InterceptionNotPending(f"interception {interception_id!r} does not exist")
        if document.get("status") != InterceptionStatus.PENDING.value:
            raise InterceptionNotPending(
                f"interception {interception_id!r} is {document.get('status')}, not PENDING"
            )
        updated = dict(document)
        updated.update(
            {
                "status": InterceptionStatus.ALLOWED.value,
                "answered_at": now,
                "answered_by": allowed_by,
            }
        )
        result = await self._store.replace_one(
            AI_INTERCEPTIONS,
            {"interception_id": interception_id, "status": InterceptionStatus.PENDING.value},
            updated,
            allowed_metadata_fields=METADATA_FIELDS,
        )
        if getattr(result, "matched_count", 0) != 1:
            raise InterceptionNotPending(
                f"interception {interception_id!r} stopped being PENDING during the write"
            )
        return _from_document(updated)

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        """The operator queue: what is waiting on a human, oldest first.

        Deliberately does **not** unseal any payload. Decrypting every held
        prompt to render a list would defeat sealing them, and an operator
        scanning the queue needs identity and age, not content -- the payload is
        fetched by whoever opens one to answer it.

        Oldest first because the queue is worked in arrival order and the oldest
        item is the one closest to expiring unanswered.

        **Lapsed records are excluded even while they are still `PENDING`.**
        `InterceptionExpirySweep` settles them, but a sweep runs on an interval
        and this read runs on a click, so between the two there is a window in
        which a record whose deadline has passed is still stored `PENDING`.
        Offering one would hand the operator -- or an automated harness taking
        "the first PENDING" -- work whose caller has already given up, and the
        answer they wrote would go nowhere. The status filter alone was what
        made a three-day-old record the head of this queue.
        """
        cursor = (
            self._store.read_only(AI_INTERCEPTIONS)
            .find(
                {
                    "status": InterceptionStatus.PENDING.value,
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            .sort("created_at", 1)
            .limit(limit)
        )
        return [_from_document(document) async for document in cursor]

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        """Close a pending interception without an answer.

        Also filtered on PENDING: cancelling something already answered would
        discard a human's work, and an expiry sweep racing a late answer is
        exactly when that happens.
        """
        document = await self._store.read_only(AI_INTERCEPTIONS).find_one(
            {"interception_id": interception_id}
        )
        if document is None or document.get("status") != InterceptionStatus.PENDING.value:
            return
        updated = dict(document)
        updated["status"] = status.value
        await self._store.replace_one(
            AI_INTERCEPTIONS,
            {"interception_id": interception_id, "status": InterceptionStatus.PENDING.value},
            updated,
            allowed_metadata_fields=METADATA_FIELDS,
        )


def lapsed_filter(now: datetime) -> dict[str, Any]:
    """The one definition of "this interception is over".

    A module-level function rather than a method so the read path and the sweep
    cannot drift: `list_pending` hides exactly the records `expire_lapsed`
    settles, which is the property that makes running both non-redundant instead
    of contradictory.
    """
    return {"status": InterceptionStatus.PENDING.value, "expires_at": {"$lte": now}}


class InterceptionExpirySweep:
    """Settles `PENDING` interceptions whose deadline has passed.

    **No encryptor, deliberately.** Expiry is a metadata transition: it reads
    `status` and `expires_at` and writes `status`, and it never opens the
    envelope. Requiring a key to run it would mean the housekeeping worker had
    to hold the reasoning encryption key to reap records it must never read --
    the exact opposite of what sealing them is for. The sealed payload is
    carried through the replace untouched, and `EncryptionGuard` still checks
    the replacement document's envelope shape on the way out.

    **The transition follows `DurableInterceptionProvider`'s precedent rather
    than inventing a second one.** That provider already closes its *own* record
    with `cancel(..., status=EXPIRED)` when it gives up waiting, and this is the
    same compare-and-set against `status: PENDING` -- so a sweep racing a late
    answer loses, and the human's text is never discarded. What the sweep adds
    is the records no caller is left to close: a provider whose process died
    mid-wait, and every request `DurableInterceptionPolicy` opened, which is
    non-blocking by design and therefore has no coroutine to come back and
    settle it.
    """

    def __init__(self, system_store: SystemStore) -> None:
        self._store = system_store
        self._interceptions = system_store.read_only(AI_INTERCEPTIONS)

    async def count_lapsed(self, *, limit: int) -> int:
        """How many lapsed records this pass could settle, capped at `limit`.

        Bounded rather than a true backlog count: an unbounded
        `count_documents` over a collection that has been accumulating corpses
        for days is a full scan to produce a number nothing acts on.
        """
        return await self._interceptions.count_documents(
            lapsed_filter(datetime.now(UTC)), limit=limit
        )

    async def expire_lapsed(self, *, limit: int) -> int:
        """Move up to `limit` lapsed records to `EXPIRED`, oldest deadline first.

        Returns how many transitions actually landed. A record that stopped
        being `PENDING` between the read and the write -- an operator answering
        the one this pass had just selected -- is simply not counted. Nothing
        went wrong: the compare-and-set is what makes the sweep safe to run
        beside live operators, and the caller reports the difference as
        contention rather than as failure.
        """
        cursor = (
            self._interceptions.find(lapsed_filter(datetime.now(UTC)))
            .sort("expires_at", 1)
            .limit(limit)
        )
        documents = [document async for document in cursor]
        expired = 0
        for document in documents:
            updated = dict(document)
            updated["status"] = InterceptionStatus.EXPIRED.value
            result = await self._store.replace_one(
                AI_INTERCEPTIONS,
                {
                    "interception_id": document["interception_id"],
                    "status": InterceptionStatus.PENDING.value,
                },
                updated,
                allowed_metadata_fields=METADATA_FIELDS,
            )
            if getattr(result, "matched_count", 0) == 1:
                expired += 1
        return expired


def _from_document(
    document: Mapping[str, Any], *, response_text: str | None = None
) -> Interception:
    resume = document.get("resume") or {}
    return Interception(
        interception_id=str(document["interception_id"]),
        task_id=str(document["task_id"]),
        status=InterceptionStatus(document["status"]),
        resume=ResumeCommand(
            run_id=str(resume.get("run_id", "")),
            thread_id=str(resume.get("thread_id", "")),
            workflow_id=resume.get("workflow_id"),
        ),
        created_at=_aware(document["created_at"]),
        expires_at=_aware(document["expires_at"]),
        answered_at=_aware(document["answered_at"]) if document.get("answered_at") else None,
        answered_by=document.get("answered_by"),
        response_text=response_text,
        # A document written before response interception existed has no
        # `point`, and it was a held request. Defaulting rather than raising
        # keeps those readable.
        point=InterceptionPoint(document.get("point") or InterceptionPoint.REQUEST.value),
    )


def _aware(value: datetime) -> datetime:
    """Mongo hands back naive UTC datetimes; comparing one to an aware `now`
    raises rather than returning False."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
