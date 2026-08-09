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
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from return_platform.ai.interception.records import (
    Interception,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.platform.secrets.envelope import EnvelopeEncryptor, EnvelopePayload
from return_platform.platform.system_store.repository import SystemStore

AI_INTERCEPTIONS = "ai_interceptions"

#: Everything an operator console needs to list and filter, and nothing that
#: describes the request's content. A task id is a routing label; a prompt is
#: not, and letting one leak into plaintext metadata would defeat sealing the
#: payload at all.
_METADATA_FIELDS = frozenset(
    {
        "interception_id",
        "task_id",
        "status",
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
    ) -> Interception: ...

    async def get(self, interception_id: str) -> Interception | None: ...

    async def request_payload(self, interception_id: str) -> Mapping[str, Any] | None: ...

    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception: ...

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None: ...


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
    ) -> Interception:
        """Write the held request and its resume command as one document.

        The resume command travels *with* the request precisely so a crash
        between "held the request" and "recorded how to resume it" is not
        representable.
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
            allowed_metadata_fields=_METADATA_FIELDS,
        )
        return Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=now,
            expires_at=expires_at,
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
            allowed_metadata_fields=_METADATA_FIELDS,
        )
        # `replace_one` returns pymongo's UpdateResult, not a bool; matched_count
        # is what says the compare-and-set filter still held at write time.
        if getattr(result, "matched_count", 0) != 1:
            raise InterceptionNotPending(
                f"interception {interception_id!r} stopped being PENDING during the write"
            )
        return _from_document(updated, response_text=response_text)

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
            allowed_metadata_fields=_METADATA_FIELDS,
        )


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
    )


def _aware(value: datetime) -> datetime:
    """Mongo hands back naive UTC datetimes; comparing one to an aware `now`
    raises rather than returning False."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
