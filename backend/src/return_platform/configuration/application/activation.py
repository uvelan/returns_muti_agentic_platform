"""Activation service — exclusively owns APPROVED→ACTIVE and ACTIVE→SUPERSEDED.

The full activation is a single atomic MongoDB transaction:
    1. Require target release status == APPROVED.
    2. CAS current ACTIVE → SUPERSEDED (sets superseded_by = target_id).
    3. CAS target APPROVED → ACTIVE (sets activated_at = now).
    4. CAS configuration_active_pointer {_id:"active"} with version increment.

All three changes succeed or none succeed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from pymongo import ASCENDING, AsyncMongoClient, IndexModel, ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

from return_platform.configuration.application.snapshot import verify_snapshot_integrity
from return_platform.configuration.domain.release import ReleaseStatus
from return_platform.configuration.domain.release_model import RuntimeSnapshot

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


class ActivationConflictError(Exception):
    """Raised when activation loses a concurrent CAS race."""


class ActivationService:
    """Transactional activation — the ONLY path into ACTIVE status."""

    def __init__(self, client: AsyncMongoClient[dict[str, object]]) -> None:
        self._client = client
        self._db = client.get_database("platform")
        self._releases = self._db.get_collection("configuration_releases")
        self._pointer = self._db.get_collection("configuration_active_pointer")

    async def initialize_indexes(self) -> None:
        """Create the partial unique index that prevents two concurrent ACTIVE releases."""
        await self._releases.create_indexes(
            [
                IndexModel(
                    [("status", ASCENDING)],
                    unique=True,
                    partialFilterExpression={"status": ReleaseStatus.ACTIVE.value},
                    name="unique_active_release",
                )
            ]
        )

    async def activate_release(self, target_release_id: str) -> None:
        """Atomically activate an APPROVED release.

        Transaction:
            SUPERSEDE current ACTIVE (if any)
            ACTIVATE target (must be APPROVED)
            CAS-update active pointer _id="active"

        Raises ActivationConflictError if:
          - target is not in APPROVED status,
          - a concurrent activation wins the CAS race,
          - the unique partial index is violated.

        Raises ConfigurationIntegrityError if the target release's persisted snapshot
        no longer matches its stored checksum -- a distinct failure class from an
        ordinary CAS conflict.
        """
        async with self._client.start_session() as session:
            try:
                async with await session.start_transaction():
                    now = _now()

                    # Load the target release; require APPROVED
                    target_doc = await self._releases.find_one(
                        {"release_id": target_release_id},
                        session=session,
                    )
                    if not target_doc:
                        raise ActivationConflictError(
                            f"Target release '{target_release_id}' not found"
                        )
                    if target_doc["status"] == ReleaseStatus.ACTIVE.value:
                        # Already active — idempotent return
                        return
                    if target_doc["status"] != ReleaseStatus.APPROVED.value:
                        raise ActivationConflictError(
                            f"Target release '{target_release_id}' is in status "
                            f"'{target_doc['status']}'; activation requires APPROVED"
                        )

                    checksum = str(target_doc.get("checksum", ""))

                    # Re-verify integrity before activation -- never trust a stored
                    # checksum alone. A mismatch is an integrity violation, not an
                    # ordinary CAS conflict, so it propagates as
                    # ConfigurationIntegrityError rather than ActivationConflictError.
                    target_snapshot = RuntimeSnapshot.model_validate(target_doc["snapshot"])
                    verify_snapshot_integrity(target_snapshot, checksum)

                    # Supersede the current ACTIVE release (if any)
                    current_active = await self._releases.find_one(
                        {"status": ReleaseStatus.ACTIVE.value},
                        session=session,
                    )
                    if current_active:
                        if current_active["release_id"] == target_release_id:
                            return  # Already active
                        supersede_result = await self._releases.update_one(
                            {
                                "release_id": current_active["release_id"],
                                "status": ReleaseStatus.ACTIVE.value,
                            },
                            {
                                "$set": {
                                    "status": ReleaseStatus.SUPERSEDED.value,
                                    "superseded_by": target_release_id,
                                    "updated_at": now,
                                }
                            },
                            session=session,
                        )
                        if supersede_result.modified_count != 1:
                            raise ActivationConflictError(
                                "Current ACTIVE release changed concurrently during supersede"
                            )

                    # Activate the target
                    activate_result = await self._releases.update_one(
                        {
                            "release_id": target_release_id,
                            "status": ReleaseStatus.APPROVED.value,
                        },
                        {
                            "$set": {
                                "status": ReleaseStatus.ACTIVE.value,
                                "activated_at": now,
                                "updated_at": now,
                            }
                        },
                        session=session,
                    )
                    if activate_result.modified_count != 1:
                        raise ActivationConflictError(
                            f"Failed to activate release '{target_release_id}': "
                            f"not found or not in APPROVED status"
                        )

                    # CAS-update the active pointer — _id = "active"
                    current_pointer = await self._pointer.find_one(
                        {"_id": "active"},
                        session=session,
                    )
                    pointer_version = current_pointer.get("version", 0) if current_pointer else 0

                    pointer_result = await self._pointer.find_one_and_update(
                        {"_id": "active", "version": pointer_version},
                        {
                            "$set": {
                                "release_id": target_release_id,
                                "checksum": checksum,
                                "version": pointer_version + 1,
                                "updated_at": now,
                            }
                        },
                        upsert=(pointer_version == 0),
                        return_document=ReturnDocument.AFTER,
                        session=session,
                    )

                    if not pointer_result or pointer_result.get("release_id") != target_release_id:
                        raise ActivationConflictError(
                            "Failed to CAS-update active pointer (version mismatch)"
                        )

                    logger.info(
                        "release_activated release_id=%s pointer_version=%d",
                        target_release_id,
                        pointer_version + 1,
                    )

            except DuplicateKeyError as exc:
                raise ActivationConflictError(
                    "Concurrent activation detected via unique partial index"
                ) from exc
            except OperationFailure as exc:
                # WriteConflict (112) and transaction errors (251, 244, 258)
                if exc.code in (112, 251, 244, 258):
                    raise ActivationConflictError(
                        f"Concurrent activation detected: {exc.details}"
                    ) from exc
                raise
