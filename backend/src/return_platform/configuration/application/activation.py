from pymongo import AsyncMongoClient, ASCENDING, IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure
from datetime import datetime, timezone
import logging

from return_platform.configuration.domain.release import ReleaseStatus

logger = logging.getLogger(__name__)

class ActivationConflictError(Exception):
    pass

class ActivationService:
    def __init__(self, client: AsyncMongoClient[dict[str, object]]) -> None:
        self._client = client
        self._db = client.get_database("platform")
        self._releases = self._db.get_collection("configuration_releases")
        self._pointer = self._db.get_collection("configuration_active_pointer")

    async def initialize_indexes(self) -> None:
        await self._releases.create_indexes([
            IndexModel(
                [("status", ASCENDING)],
                unique=True,
                partialFilterExpression={"status": ReleaseStatus.ACTIVE},
                name="unique_active_release"
            )
        ])
        
    async def activate_release(self, target_release_id: str) -> None:
        """
        Activates the target release in a transaction, superseding the current active release.
        Raises ActivationConflictError if another activation wins.
        """
        async with self._client.start_session() as session:
            try:
                async with session.start_transaction():
                    current_active = await self._releases.find_one(
                        {"status": ReleaseStatus.ACTIVE},
                        session=session
                    )
                    
                    if current_active and current_active["release_id"] == target_release_id:
                        return
                        
                    if current_active:
                        result = await self._releases.update_one(
                            {"_id": current_active["_id"], "status": ReleaseStatus.ACTIVE},
                            {"$set": {"status": ReleaseStatus.SUPERSEDED}},
                            session=session
                        )
                        if result.modified_count != 1:
                            raise ActivationConflictError("Current active release changed concurrently")

                    target_result = await self._releases.update_one(
                        {"release_id": target_release_id, "status": ReleaseStatus.APPROVED},
                        {"$set": {"status": ReleaseStatus.ACTIVE}},
                        session=session
                    )
                    if target_result.modified_count != 1:
                        raise ActivationConflictError(f"Target release {target_release_id} not found or not in APPROVED state")

                    target_release_doc = await self._releases.find_one({"release_id": target_release_id}, session=session)
                    checksum = target_release_doc.get("checksum", "") if target_release_doc else ""

                    current_pointer = await self._pointer.find_one({"_id": "active"}, session=session)
                    pointer_version = current_pointer.get("version", 0) if current_pointer else 0

                    pointer_result = await self._pointer.find_one_and_update(
                        {"_id": "active", "version": pointer_version},
                        {"$set": {
                            "release_id": target_release_id,
                            "checksum": checksum,
                            "version": pointer_version + 1,
                            "updated_at": datetime.now(timezone.utc)
                        }},
                        upsert=(pointer_version == 0),
                        return_document=ReturnDocument.AFTER,
                        session=session
                    )
                    
                    if not pointer_result or pointer_result["release_id"] != target_release_id:
                        raise ActivationConflictError("Failed to update active pointer (CAS mismatch)")
            except DuplicateKeyError:
                raise ActivationConflictError("Concurrent activation detected via unique index")
            except OperationFailure as e:
                if e.code in (112, 251, 244, 258):
                    raise ActivationConflictError(f"Concurrent activation detected: {e.details}")
                raise
