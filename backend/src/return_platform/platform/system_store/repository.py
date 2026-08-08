"""The only sanctioned way for business code to reach a system-store collection.

Logical naming is mandatory (implementation plan Phase 3): a repository resolves
`system_store.collection("ai_interceptions")`, never `db["platform_ai_interceptions"]`
directly. Resolution is driven entirely by the manifest's `structures` block
(`configuration.domain.system_store.SystemStoreConfig.structures`), so renaming a
physical collection is a manifest change, not a source change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection

from return_platform.platform.system_store.encryption import EncryptionGuard


class UnknownStructure(KeyError):
    """Raised when resolving a logical structure name the manifest does not declare."""


class _StructureLike(Protocol):
    physical_name: str
    encrypted: bool


class SystemStore:
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        structures: Mapping[str, _StructureLike],
        *,
        database: str = "platform",
        encryption_guard: EncryptionGuard | None = None,
    ) -> None:
        self._db = client.get_database(database)
        self._structures = structures
        self._encryption_guard = encryption_guard or EncryptionGuard()

    def _definition(self, logical_name: str) -> _StructureLike:
        definition = self._structures.get(logical_name)
        if definition is None:
            raise UnknownStructure(
                f"'{logical_name}' is not declared in the system_store manifest's structures block"
            )
        return definition

    def collection(self, logical_name: str) -> AsyncCollection[dict[str, object]]:
        return self._db.get_collection(self._definition(logical_name).physical_name)

    def is_encrypted(self, logical_name: str) -> bool:
        return self._definition(logical_name).encrypted

    async def insert_one(
        self, logical_name: str, document: Mapping[str, Any], **kwargs: Any
    ) -> Any:
        self._encryption_guard.check_document(
            logical_name, document, encrypted=self.is_encrypted(logical_name)
        )
        return await self.collection(logical_name).insert_one(dict(document), **kwargs)
