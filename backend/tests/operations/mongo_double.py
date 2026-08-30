"""A MongoDB double for the S2 delivery-spine tests.

Modelled on the double in `test_durable_support_events.py`, extended with what
the S2 stores exercise and that double does not model: dotted-path queries and
partial indexes (`businessPayload.deliveryId`), `$ne`/`$gte`, upserts,
`update_many`, `$push`, and `insert_many`. A fresh implementation rather than a
subclass so that file's double stays exactly the shape its own assertions were
written against.

Unique indexes are *enforced*, not recorded, and `with_transaction` restores a
snapshot on failure -- both properties the production code under test leans on,
and both of which a permissive fake would silently stop testing.
"""

from __future__ import annotations

import copy
from typing import Any

from pymongo.errors import DuplicateKeyError


def resolve_path(document: dict[str, Any], path: str) -> tuple[bool, Any]:
    """Walk a dotted path. Returns (present, value)."""
    current: Any = document
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _set_path(document: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    current = document
    for part in parts[:-1]:
        current = current.setdefault(part, {})
    current[parts[-1]] = value


def matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(matches(document, sub) for sub in condition):
                return False
            continue
        if key == "$and":
            if not all(matches(document, sub) for sub in condition):
                return False
            continue
        present, actual = resolve_path(document, key)
        if (
            isinstance(condition, dict)
            and condition
            and all(str(k).startswith("$") for k in condition)
        ):
            for operator, operand in condition.items():
                if operator == "$exists":
                    if bool(operand) != present:
                        return False
                elif operator == "$in":
                    if actual not in operand:
                        return False
                elif operator == "$nin":
                    if actual in operand:
                        return False
                elif operator == "$ne":
                    if actual == operand:
                        return False
                elif operator == "$lt":
                    if actual is None or not actual < operand:
                        return False
                elif operator == "$lte":
                    if actual is None or not actual <= operand:
                        return False
                elif operator == "$gt":
                    if actual is None or not actual > operand:
                        return False
                elif operator == "$gte":
                    if actual is None or not actual >= operand:
                        return False
                elif operator == "$type":
                    if operand != "string" or not isinstance(actual, str):
                        return False
                else:  # pragma: no cover - an operator the double has not met
                    raise NotImplementedError(operator)
        elif actual != condition:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: Any, direction: int = 1) -> FakeCursor:
        if isinstance(key, str):
            self._documents.sort(key=lambda item: item.get(key), reverse=direction < 0)
        elif isinstance(key, list):
            for field, field_direction in reversed(key):
                self._documents.sort(key=lambda item: item.get(field), reverse=field_direction < 0)
        return self

    def limit(self, count: int) -> FakeCursor:
        self._documents = self._documents[:count]
        return self

    def __aiter__(self) -> FakeCursor:
        self._iterator = iter(self._documents)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration from None


class _Result:
    def __init__(self, modified_count: int = 0, upserted_id: Any = None) -> None:
        self.modified_count = modified_count
        self.upserted_id = upserted_id


class FakeCollection:
    def __init__(self, name: str, database: FakeDatabase) -> None:
        self.name = name
        self.database = database
        self.documents: dict[str, dict[str, Any]] = {}
        self.unique_indexes: list[tuple[tuple[str, ...], dict[str, Any] | None, str | None]] = []
        self.index_calls: list[tuple[Any, dict[str, Any]]] = []

    async def create_index(self, keys: Any, **options: Any) -> None:
        self.index_calls.append((keys, options))
        if not options.get("unique"):
            return
        fields = tuple(key for key, _ in keys) if isinstance(keys, list) else (str(keys),)
        self.unique_indexes.append(
            (fields, options.get("partialFilterExpression"), options.get("name"))
        )

    def _violates_unique(self, document: dict[str, Any], *, ignore_id: str | None = None) -> bool:
        identifier = str(document.get("_id"))
        if ignore_id != identifier and identifier in self.documents:
            return True
        for fields, partial, _name in self.unique_indexes:
            if partial is not None and not matches(document, partial):
                continue
            candidate = tuple(resolve_path(document, field)[1] for field in fields)
            for stored_id, stored in self.documents.items():
                if stored_id == ignore_id or stored_id == identifier:
                    continue
                if partial is not None and not matches(stored, partial):
                    continue
                if tuple(resolve_path(stored, field)[1] for field in fields) == candidate:
                    return True
        return False

    async def insert_one(self, document: dict[str, Any], session: Any = None) -> None:
        del session
        if self._violates_unique(document):
            raise DuplicateKeyError(f"duplicate key on {self.name}")
        self.documents[str(document["_id"])] = copy.deepcopy(document)

    async def insert_many(self, documents: list[dict[str, Any]], session: Any = None) -> None:
        for document in documents:
            await self.insert_one(document, session=session)

    async def find_one(
        self, query: dict[str, Any], projection: Any = None, sort: Any = None, session: Any = None
    ) -> dict[str, Any] | None:
        del projection, session
        candidates = [
            copy.deepcopy(item) for item in self.documents.values() if matches(item, query)
        ]
        if sort:
            for field, direction in reversed(list(sort)):
                candidates.sort(key=lambda item: item.get(field), reverse=direction < 0)
        return candidates[0] if candidates else None

    def find(
        self, query: dict[str, Any], projection: Any = None, session: Any = None
    ) -> FakeCursor:
        del projection, session
        return FakeCursor(
            [copy.deepcopy(item) for item in self.documents.values() if matches(item, query)]
        )

    def _apply(self, document: dict[str, Any], update: dict[str, Any]) -> None:
        for field, value in update.get("$set", {}).items():
            _set_path(document, field, copy.deepcopy(value))
        for field, value in update.get("$inc", {}).items():
            present, actual = resolve_path(document, field)
            _set_path(document, field, (actual if present and actual is not None else 0) + value)
        for field in update.get("$unset", {}):
            parts = field.split(".")
            current: Any = document
            for part in parts[:-1]:
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            if isinstance(current, dict):
                current.pop(parts[-1], None)
        for field, value in update.get("$push", {}).items():
            present, actual = resolve_path(document, field)
            items = list(actual) if present and isinstance(actual, list) else []
            items.append(copy.deepcopy(value))
            _set_path(document, field, items)
        if self._violates_unique(document, ignore_id=str(document.get("_id"))):
            raise DuplicateKeyError(f"duplicate key on {self.name}")

    @staticmethod
    def _document_from_upsert(query: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        document: dict[str, Any] = {}
        for key, condition in query.items():
            if not str(key).startswith("$") and not (
                isinstance(condition, dict) and any(str(k).startswith("$") for k in condition)
            ):
                _set_path(document, key, copy.deepcopy(condition))
        for field, value in update.get("$setOnInsert", {}).items():
            _set_path(document, field, copy.deepcopy(value))
        if "_id" not in document:
            raise NotImplementedError("the double only upserts with an _id in the filter")
        return document

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        sort: Any = None,
        upsert: bool = False,
        return_document: Any = None,
        session: Any = None,
    ) -> dict[str, Any] | None:
        del return_document, session
        candidates = [item for item in self.documents.values() if matches(item, query)]
        if sort:
            for field, direction in reversed(list(sort)):
                candidates.sort(key=lambda item: item.get(field), reverse=direction < 0)
        if not candidates:
            if not upsert:
                return None
            document = self._document_from_upsert(query, update)
            self._apply(document, update)
            await self.insert_one(document)
            return copy.deepcopy(self.documents[str(document["_id"])])
        self._apply(candidates[0], update)
        return copy.deepcopy(candidates[0])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        upsert: bool = False,
        session: Any = None,
    ) -> _Result:
        del session
        for document in self.documents.values():
            if matches(document, query):
                self._apply(document, update)
                return _Result(modified_count=1)
        if upsert:
            document = self._document_from_upsert(query, update)
            self._apply(document, update)
            await self.insert_one(document)
            return _Result(modified_count=0, upserted_id=document["_id"])
        return _Result(modified_count=0)

    async def update_many(
        self, query: dict[str, Any], update: dict[str, Any], session: Any = None
    ) -> _Result:
        del session
        modified = 0
        for document in self.documents.values():
            if matches(document, query):
                self._apply(document, update)
                modified += 1
        return _Result(modified_count=modified)

    async def count_documents(self, query: dict[str, Any], session: Any = None) -> int:
        del session
        return sum(1 for item in self.documents.values() if matches(item, query))

    async def delete_one(self, query: dict[str, Any], session: Any = None) -> None:
        del session
        for identifier, document in list(self.documents.items()):
            if matches(document, query):
                del self.documents[identifier]
                return


class FakeDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self.collections:
            self.collections[name] = FakeCollection(name, self)
        return self.collections[name]


class FakeSession:
    def __init__(self, client: FakeClient) -> None:
        self._client = client

    async def with_transaction(self, callback: Any) -> Any:
        snapshot = self._client.snapshot()
        try:
            return await callback(self)
        except BaseException:
            self._client.restore(snapshot)
            raise


class FakeClient:
    def __init__(self) -> None:
        self.databases: dict[str, FakeDatabase] = {}

    def __getitem__(self, name: str) -> FakeDatabase:
        if name not in self.databases:
            self.databases[name] = FakeDatabase()
        return self.databases[name]

    def snapshot(self) -> dict[tuple[str, str], dict[str, dict[str, Any]]]:
        return {
            (database_name, collection_name): copy.deepcopy(collection.documents)
            for database_name, database in self.databases.items()
            for collection_name, collection in database.collections.items()
        }

    def restore(self, snapshot: dict[tuple[str, str], dict[str, dict[str, Any]]]) -> None:
        for (database_name, collection_name), documents in snapshot.items():
            self.databases[database_name].collections[collection_name].documents = documents
        for database_name, database in self.databases.items():
            for collection_name, collection in database.collections.items():
                if (database_name, collection_name) not in snapshot:
                    collection.documents = {}

    def start_session(self) -> Any:
        session = FakeSession(self)

        class _Context:
            async def __aenter__(self) -> FakeSession:
                return session

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Context()
