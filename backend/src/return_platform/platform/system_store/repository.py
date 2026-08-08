"""The only sanctioned way for business code to reach a system-store collection.

Logical naming is mandatory (implementation plan Phase 3): a repository resolves
`system_store.collection("ai_interceptions")`, never `db["platform_ai_interceptions"]`
directly. Resolution is driven entirely by the manifest's `structures` block
(`configuration.domain.system_store.SystemStoreConfig.structures`), so renaming a
physical collection is a manifest change, not a source change.

Slice 3R.6: a structure declared `encrypted: true` never yields an unrestricted raw
collection handle to business code. `read_only()` returns a genuine wrapper
(`_ReadOnlyCollectionView`) exposing only read operations -- not the raw collection
object under a narrower type annotation, which would still expose every mutation
method to any caller that bypassed static type-checking. `collection()` raises for an
encrypted structure rather than silently handing out the raw handle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pymongo import AsyncMongoClient
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.cursor import AsyncCursor

from return_platform.platform.system_store.encryption import EncryptionGuard


class UnknownStructure(KeyError):
    """Raised when resolving a logical structure name the manifest does not declare."""


class EncryptedStructureRequiresGuardedAccess(RuntimeError):
    """Raised when business code asks `SystemStore.collection()` for an unrestricted
    raw handle to a structure declared `encrypted: true`. Use `read_only()` for reads
    and `insert_one()` (or another guarded write method) for mutations."""


class _StructureLike(Protocol):
    physical_name: str
    encrypted: bool


class ReadOnlyCollection(Protocol):
    """The only interface business code may use to read an encrypted structure
    directly. Excludes every mutation method (insert/replace/update/delete/bulk_write/
    find_one_and_update) and the raw collection handle itself -- not merely by
    convention, but because `_ReadOnlyCollectionView` genuinely does not implement
    them; calling one raises `AttributeError`."""

    async def find_one(
        self, filter: Mapping[str, Any] | None = None, *args: Any, **kwargs: Any
    ) -> Mapping[str, Any] | None: ...

    def find(
        self, filter: Mapping[str, Any] | None = None, *args: Any, **kwargs: Any
    ) -> AsyncCursor[Any]: ...

    async def count_documents(
        self, filter: Mapping[str, Any], *args: Any, **kwargs: Any
    ) -> int: ...

    def aggregate(self, pipeline: Any, *args: Any, **kwargs: Any) -> Any: ...


class _ReadOnlyCollectionView:
    """Wraps a real `AsyncCollection`, implementing only `ReadOnlyCollection`'s methods.
    No `__getattr__` passthrough: an attribute not defined here does not exist on this
    object at all."""

    __slots__ = ("_collection",)

    def __init__(self, collection: AsyncCollection[dict[str, object]]) -> None:
        self._collection = collection

    async def find_one(
        self, filter: Mapping[str, Any] | None = None, *args: Any, **kwargs: Any
    ) -> Mapping[str, Any] | None:
        return await self._collection.find_one(filter, *args, **kwargs)

    def find(
        self, filter: Mapping[str, Any] | None = None, *args: Any, **kwargs: Any
    ) -> AsyncCursor[Any]:
        return self._collection.find(filter, *args, **kwargs)

    async def count_documents(self, filter: Mapping[str, Any], *args: Any, **kwargs: Any) -> int:
        return await self._collection.count_documents(filter, *args, **kwargs)

    def aggregate(self, pipeline: Any, *args: Any, **kwargs: Any) -> Any:
        return self._collection.aggregate(pipeline, *args, **kwargs)


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
        """An unrestricted raw collection handle. Refuses to hand one out for a
        structure declared `encrypted: true` (Slice 3R.6) -- use `read_only()` for
        reads and a guarded write method for mutations instead."""
        definition = self._definition(logical_name)
        if definition.encrypted:
            raise EncryptedStructureRequiresGuardedAccess(
                f"'{logical_name}' is declared encrypted=true; SystemStore.collection() "
                f"never returns an unrestricted raw handle for it -- use read_only() for "
                f"reads and insert_one() (or another guarded write) for mutations"
            )
        return self._db.get_collection(definition.physical_name)

    def read_only(self, logical_name: str) -> ReadOnlyCollection:
        """A restricted read facade usable for any structure, encrypted or not."""
        definition = self._definition(logical_name)
        return _ReadOnlyCollectionView(self._db.get_collection(definition.physical_name))

    def is_encrypted(self, logical_name: str) -> bool:
        return self._definition(logical_name).encrypted

    async def insert_one(
        self,
        logical_name: str,
        document: Mapping[str, Any],
        *,
        allowed_metadata_fields: frozenset[str] = frozenset(),
        **kwargs: Any,
    ) -> Any:
        definition = self._definition(logical_name)
        self._encryption_guard.check_document(
            logical_name,
            document,
            encrypted=definition.encrypted,
            allowed_metadata_fields=allowed_metadata_fields,
        )
        return await self._db.get_collection(definition.physical_name).insert_one(
            dict(document), **kwargs
        )
