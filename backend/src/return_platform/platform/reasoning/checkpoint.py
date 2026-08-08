"""SystemStoreCheckpointSaver: LangGraph's BaseCheckpointSaver backed by SystemStore.

Resolves storage as SystemStore -> logical structure -> configured physical
collection (`reasoning_checkpoints`, `reasoning_checkpoint_writes`) -- never a
hardcoded collection name, so a manifest change is the only way to repoint storage.
`InMemorySaver`/`MemorySaver` are forbidden outside unit tests (see
`tests/reasoning/test_no_langchain_provider_packages.py` and this package's README).

Async-only: every method this backend calls is an `a*` method. The sync counterparts
are left at `BaseCheckpointSaver`'s own unimplemented default, which is correct for an
async-first backend -- implementing sync wrappers nothing calls would be unused code,
not a real capability.

Checkpoints are naturally insert-only: LangGraph allocates a fresh, monotonically
increasing `checkpoint["id"]` on every `put()`, so a checkpoint document is never
updated in place, only ever inserted -- this is why `insert_one()` (the one write path
`SystemStore` permits on an encrypted structure) is sufficient for `aput`. Pending
writes are almost always insert-only too (a (task_id, write_idx) pair is written once);
the four special negative indices in `WRITES_IDX_MAP` (ERROR, SCHEDULED, INTERRUPT,
RESUME) are the one case that legitimately overwrites on a later attempt, handled with
`replace_one(..., upsert=True)`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

from return_platform.platform.secrets.envelope import EnvelopeEncryptor, EnvelopePayload
from return_platform.platform.system_store.repository import SystemStore

_CHECKPOINTS_STRUCTURE = "reasoning_checkpoints"
_WRITES_STRUCTURE = "reasoning_checkpoint_writes"

_CHECKPOINT_METADATA_FIELDS = frozenset(
    {"thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id", "serde_type"}
)
_WRITE_METADATA_FIELDS = frozenset(
    {
        "thread_id",
        "checkpoint_ns",
        "checkpoint_id",
        "task_id",
        "write_idx",
        "task_path",
        "channel",
        "serde_type",
    }
)


def _checkpoint_doc_id(thread_id: str, checkpoint_ns: str, checkpoint_id: str) -> str:
    return f"{thread_id}::{checkpoint_ns}::{checkpoint_id}"


def _write_doc_id(
    thread_id: str, checkpoint_ns: str, checkpoint_id: str, task_id: str, write_idx: int
) -> str:
    return f"{thread_id}::{checkpoint_ns}::{checkpoint_id}::{task_id}::{write_idx}"


class SystemStoreCheckpointSaver(BaseCheckpointSaver[str]):
    """This is durable reasoning position, never the authoritative business record --
    canonical state stays in ReturnSession/Conversation/AnalysisSession/
    GraphSchemaDraft/ConfigurationRelease. No business reasoning lives here."""

    def __init__(
        self,
        system_store: SystemStore,
        encryptor: EnvelopeEncryptor,
        *,
        serde: Any = None,
    ) -> None:
        super().__init__(serde=serde)
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

    def _unseal(self, envelope: dict[str, Any]) -> bytes:
        payload = EnvelopePayload(
            ciphertext=envelope["ciphertext"],
            key_ref=envelope["key_ref"],
            algorithm=envelope["algorithm"],
            version=envelope["version"],
        )
        return self._encryptor.decrypt(payload)

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        del new_versions  # channel-level blob dedup is a storage optimization this
        # saver deliberately does not implement yet -- see the module docstring.
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = checkpoint["id"]
        parent_checkpoint_id = config["configurable"].get("checkpoint_id")

        serde_type, raw = self.serde.dumps_typed(
            {"checkpoint": checkpoint, "metadata": get_checkpoint_metadata(config, metadata)}
        )
        await self._store.insert_one(
            _CHECKPOINTS_STRUCTURE,
            {
                "_id": _checkpoint_doc_id(thread_id, checkpoint_ns, checkpoint_id),
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
                "parent_checkpoint_id": parent_checkpoint_id,
                "serde_type": serde_type,
                "_envelope": self._seal(raw),
            },
            allowed_metadata_fields=_CHECKPOINT_METADATA_FIELDS,
        )
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        for idx, (channel, value) in enumerate(writes):
            write_idx = WRITES_IDX_MAP.get(channel, idx)
            serde_type, raw = self.serde.dumps_typed(value)
            document = {
                "_id": _write_doc_id(thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx),
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
                "task_id": task_id,
                "write_idx": write_idx,
                "task_path": task_path,
                "channel": channel,
                "serde_type": serde_type,
                "_envelope": self._seal(raw),
            }
            if write_idx < 0:
                # ERROR/SCHEDULED/INTERRUPT/RESUME: a later attempt legitimately
                # replaces the prior one.
                await self._store.replace_one(
                    _WRITES_STRUCTURE,
                    {"_id": document["_id"]},
                    document,
                    upsert=True,
                    allowed_metadata_fields=_WRITE_METADATA_FIELDS,
                )
                continue
            existing = await self._store.read_only(_WRITES_STRUCTURE).find_one(
                {"_id": document["_id"]}
            )
            if existing is not None:
                continue  # first write for this (task_id, write_idx) wins
            await self._store.insert_one(
                _WRITES_STRUCTURE, document, allowed_metadata_fields=_WRITE_METADATA_FIELDS
            )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoints = self._store.read_only(_CHECKPOINTS_STRUCTURE)

        checkpoint_id = get_checkpoint_id(config)
        if checkpoint_id:
            doc = await checkpoints.find_one(
                {"_id": _checkpoint_doc_id(thread_id, checkpoint_ns, checkpoint_id)}
            )
        else:
            latest: dict[str, Any] | None = None
            async for candidate in (
                checkpoints.find({"thread_id": thread_id, "checkpoint_ns": checkpoint_ns})
                .sort("checkpoint_id", -1)
                .limit(1)
            ):
                latest = candidate
            doc = latest
        if doc is None:
            return None

        decoded = self.serde.loads_typed((doc["serde_type"], self._unseal(doc["_envelope"])))
        resolved_checkpoint_id = doc["checkpoint_id"]

        pending_writes = []
        async for write_doc in self._store.read_only(_WRITES_STRUCTURE).find(
            {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": resolved_checkpoint_id,
            }
        ):
            value = self.serde.loads_typed(
                (write_doc["serde_type"], self._unseal(write_doc["_envelope"]))
            )
            pending_writes.append((write_doc["task_id"], write_doc["channel"], value))

        parent_checkpoint_id = doc.get("parent_checkpoint_id")
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": resolved_checkpoint_id,
                }
            },
            checkpoint=decoded["checkpoint"],
            metadata=decoded["metadata"],
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
            pending_writes=pending_writes,
        )

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        query: dict[str, Any] = {}
        if config is not None:
            query["thread_id"] = config["configurable"]["thread_id"]
            if checkpoint_ns := config["configurable"].get("checkpoint_ns"):
                query["checkpoint_ns"] = checkpoint_ns
            if checkpoint_id := get_checkpoint_id(config):
                query["checkpoint_id"] = checkpoint_id
        if before is not None and (before_id := get_checkpoint_id(before)):
            query["checkpoint_id"] = {"$lt": before_id}

        checkpoints = self._store.read_only(_CHECKPOINTS_STRUCTURE)
        cursor = checkpoints.find(query).sort("checkpoint_id", -1)
        count = 0
        async for doc in cursor:
            if limit is not None and count >= limit:
                break
            decoded = self.serde.loads_typed((doc["serde_type"], self._unseal(doc["_envelope"])))
            if filter and not all(
                query_value == decoded["metadata"].get(query_key)
                for query_key, query_value in filter.items()
            ):
                continue
            count += 1
            tuple_config: RunnableConfig = {
                "configurable": {
                    "thread_id": doc["thread_id"],
                    "checkpoint_ns": doc["checkpoint_ns"],
                    "checkpoint_id": doc["checkpoint_id"],
                }
            }
            parent_checkpoint_id = doc.get("parent_checkpoint_id")
            yield CheckpointTuple(
                config=tuple_config,
                checkpoint=decoded["checkpoint"],
                metadata=decoded["metadata"],
                parent_config=(
                    {
                        "configurable": {
                            "thread_id": doc["thread_id"],
                            "checkpoint_ns": doc["checkpoint_ns"],
                            "checkpoint_id": parent_checkpoint_id,
                        }
                    }
                    if parent_checkpoint_id
                    else None
                ),
            )

    async def adelete_thread(self, thread_id: str) -> None:
        await self._store.delete_many(_CHECKPOINTS_STRUCTURE, {"thread_id": thread_id})
        await self._store.delete_many(_WRITES_STRUCTURE, {"thread_id": thread_id})

    def get_next_version(self, current: str | None, channel: None) -> str:
        del channel
        current_v = 0 if current is None else int(current.split(".")[0])
        return f"{current_v + 1:032}.0"
