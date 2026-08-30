"""The B→A relay's transcript half (DR-3, contracts.md sect. 9).

Support says something; the associate working the case in Channel A has to see
it without going and looking. DR-3 chose *both* surfaces -- transcript and panel
-- and this is the transcript one: a **typed system entry** on the Order
Discovery conversation.

**The entry is not a turn, and the whole design follows from that.** The Order
Discovery agent's contract is turn-based: `state["transcript"]` alternates one
associate message with one agent reply, and `_transcript_of` zips it against
`turns` by position on exactly that assumption. An entry appended into that list
would either be read as something the associate said or would break the zip and
silently change what the history endpoint serves. So system entries live in
their own list, `state["systemEntries"]`, which nothing in the turn contract
reads. The agent's replay is byte-identical with or without them; the panel and
the conversation pane render them from their own key.

Append-once is by **derived id**: `(case, support event, record, kind)` through
uuid5. The classify command is dispatched at least once, so a relay that minted
a fresh id per call would put the same support update on the transcript twice
after any redelivery -- and a duplicate on a person's screen is the failure the
whole delivery-identity discipline exists to prevent.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Final, Protocol

#: Where system entries live on the conversation document's state. Its own key,
#: never `transcript`: see the module docstring -- the turn contract reads that
#: list positionally.
SYSTEM_ENTRIES_KEY: Final = "systemEntries"

#: How many system entries one conversation keeps. A cap rather than unbounded
#: growth, because the conversation document is read whole on every turn and an
#: unbounded list would make a long-running case's turns progressively slower.
#: Oldest are dropped; they remain readable as case facts, which is the durable
#: record -- the transcript is a view.
SYSTEM_ENTRY_LIMIT: Final = 200

_ENTRY_NAMESPACE: Final = uuid.UUID("2b1f7d84-6a53-4c1e-9f02-7d5e3c6a8b91")


class ConversationScopeLike(Protocol):
    def filter(self) -> dict[str, str]: ...


class ConversationDocumentStorePort(Protocol):
    """`AtomicConversationDocumentStore`, structurally.

    Structural rather than imported so `operations/` does not depend on
    `dynamic_knowledge/`: the dependency runs the other way everywhere else in
    the tree, and reversing it here for one adapter would be a cycle waiting for
    its second edge.
    """

    async def read(
        self, conversation_id: str, *, scope: Any
    ) -> dict[str, Any] | None: ...

    async def compare_and_set(
        self,
        *,
        conversation_id: str,
        expected_version: int,
        replacement: dict[str, Any],
        scope: Any,
    ) -> bool: ...


class CaseLookupPort(Protocol):
    async def get_case(self, case_id: str) -> dict[str, Any] | None: ...


def system_entry_id(
    *, case_id: str, support_event_id: str, entry_kind: str, return_record_id: str | None
) -> str:
    """The derived identity that makes appending idempotent.

    All four parts, length-prefixed for the reason `derive_support_event_id`
    gives: a message that fans out to two records produces two entries, and
    those two must not be able to collide with one entry about a differently
    named record.
    """
    parts = (case_id, support_event_id, entry_kind, return_record_id or "")
    encoded = "|".join(f"{len(part)}:{part}" for part in parts)
    return str(uuid.uuid5(_ENTRY_NAMESPACE, encoded))


class SupportTranscriptRelay:
    """Append typed system entries to a case's Order Discovery conversation."""

    def __init__(
        self,
        *,
        store: ConversationDocumentStorePort,
        cases: CaseLookupPort,
        scope_factory: Any,
    ) -> None:
        self._store = store
        self._cases = cases
        # `ConversationScope(tenant_id=..., principal_id=...)`, injected rather
        # than imported for the same layering reason as the store port. A scope
        # is half of the tenant-isolation guarantee, so it is built from the
        # *case document's* own tenant and principal and never from a caller's.
        self._scope_factory = scope_factory

    async def append_system_entry(
        self,
        *,
        case_id: str,
        support_event_id: str,
        entry_kind: str,
        return_record_id: str | None,
        payload: Mapping[str, Any],
    ) -> bool:
        """Append one entry. Returns whether this call wrote it.

        `False` for a redelivery, and `False` when the case has no Channel A
        conversation at all -- a case raised through a path that never opened
        one is not an error here, it is a case with nowhere to relay to, and
        failing the dispatch over it would dead-letter an analysis that
        committed perfectly well.
        """
        case = await self._cases.get_case(case_id)
        if case is None:
            return False
        conversation_id = case.get("channelAConversationId")
        if not conversation_id:
            return False
        scope = self._scope_factory(
            tenant_id=str(case.get("tenantId", "default")),
            principal_id=str(case.get("principalId", "")),
        )
        entry_id = system_entry_id(
            case_id=case_id,
            support_event_id=support_event_id,
            entry_kind=entry_kind,
            return_record_id=return_record_id,
        )
        entry = {
            "entryId": entry_id,
            "kind": entry_kind,
            "supportEventId": support_event_id,
            "returnRecordId": return_record_id,
            "payload": dict(payload),
            "recordedAt": datetime.now(UTC).isoformat(),
        }

        # Two attempts, not a loop. One retry absorbs the ordinary race with a
        # concurrent turn commit; a second conflict means real contention, and
        # spinning here would hold a dispatcher worker against a conversation
        # somebody is actively typing into. The command retries instead.
        for _ in range(2):
            document = await self._store.read(str(conversation_id), scope=scope)
            if document is None:
                return False
            state = dict(document.get("state") or {})
            existing = list(state.get(SYSTEM_ENTRIES_KEY) or [])
            if any(str(item.get("entryId")) == entry_id for item in existing):
                return False
            existing.append(entry)
            state[SYSTEM_ENTRIES_KEY] = existing[-SYSTEM_ENTRY_LIMIT:]
            replacement = dict(document)
            replacement["state"] = state
            replacement["version"] = int(document.get("version", 0)) + 1
            if await self._store.compare_and_set(
                conversation_id=str(conversation_id),
                expected_version=int(document.get("version", 0)),
                replacement=replacement,
                scope=scope,
            ):
                return True
        return False
