"""Release lifecycle service — owns DRAFT, VALIDATED, and APPROVED transitions.

Transition ownership:
    DRAFT → VALIDATED : validate_release()    (runs ConfigurationValidator)
    VALIDATED → APPROVED : approve_release()
    VALIDATED → DRAFT : reject_release()      (optional, resets failed validation)

APPROVED → ACTIVE and ACTIVE → SUPERSEDED are owned exclusively by ActivationService.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from pymongo import AsyncMongoClient

from return_platform.configuration.application.validator import ConfigurationValidator
from return_platform.configuration.domain.errors import (
    ConfigurationReleaseNotFoundError,
    InvalidTransitionError,
)
from return_platform.configuration.application.snapshot import compute_checksum
from return_platform.configuration.domain.release import (
    RELEASE_SERVICE_TRANSITIONS,
    ReleaseStatus,
)
from return_platform.configuration.domain.release_model import (
    ConfigurationRelease,
    RuntimeSnapshot,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReleaseService:
    """Manages configuration release creation and pre-activation lifecycle."""

    def __init__(self, client: AsyncMongoClient[dict[str, object]]) -> None:
        self._client = client
        self._db = client.get_database("platform")
        self._releases = self._db.get_collection("configuration_releases")
        self._validator = ConfigurationValidator()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Create — always starts DRAFT
    # ------------------------------------------------------------------

    async def create_release(
        self,
        release_id: str,
        snapshot: RuntimeSnapshot,
    ) -> ConfigurationRelease:
        """Persist a new release in DRAFT status.

        The snapshot checksum is computed once and persisted with the release.
        Raises ValueError if release_id already exists.
        """
        checksum = compute_checksum(snapshot)
        now = _now()

        release = ConfigurationRelease(
            release_id=release_id,
            status=ReleaseStatus.DRAFT,
            snapshot=snapshot,
            checksum=checksum,
            created_at=now,
            updated_at=now,
        )

        try:
            await self._releases.insert_one({
                "release_id": release_id,
                "status": ReleaseStatus.DRAFT.value,
                "snapshot": snapshot.model_dump(),
                "checksum": checksum,
                "created_at": now,
                "updated_at": now,
                "validated_at": None,
                "approved_at": None,
                "approved_by": None,
                "activated_at": None,
                "superseded_by": None,
            })
        except Exception as exc:
            # Let duplicate-key errors propagate with context
            raise ValueError(
                f"Failed to create release '{release_id}': {exc}"
            ) from exc

        logger.info("release_created_as_draft release_id=%s", release_id)
        return release

    # ------------------------------------------------------------------
    # Validate — DRAFT → VALIDATED
    # ------------------------------------------------------------------

    async def validate_release(self, release_id: str) -> None:
        """Transition a DRAFT release to VALIDATED.

        Algorithm:
          1. Load release; require status == DRAFT.
          2. Recompute snapshot checksum and verify it matches persisted value.
          3. Run ConfigurationValidator.validate_snapshot().
          4. Only if validation passes: CAS DRAFT → VALIDATED.
          5. Any failure leaves the release in DRAFT.

        Raises:
            ConfigurationReleaseNotFoundError: release does not exist.
            InvalidTransitionError: release is not in DRAFT status, or CAS conflict.
            ConfigurationValidationError: semantic validation failure (from validator).
        """
        doc = await self._releases.find_one({"release_id": release_id})
        if not doc:
            raise ConfigurationReleaseNotFoundError(
                f"Release '{release_id}' not found"
            )

        current_status = ReleaseStatus(doc["status"])
        if current_status != ReleaseStatus.DRAFT:
            raise InvalidTransitionError(
                f"Release '{release_id}' is in status {current_status.value!r}; "
                f"validate_release() requires DRAFT"
            )

        # Verify snapshot integrity
        snapshot = RuntimeSnapshot.model_validate(doc["snapshot"])
        recomputed = compute_checksum(snapshot)
        if recomputed != doc.get("checksum", ""):
            raise InvalidTransitionError(
                f"Release '{release_id}' checksum mismatch — snapshot may have been tampered"
            )

        # Run semantic validation — failure raises ConfigurationValidationError
        # and leaves the release in DRAFT (no DB write happens)
        self._validator.validate_snapshot(snapshot)

        # CAS: DRAFT → VALIDATED
        now = _now()
        result = await self._releases.update_one(
            {"release_id": release_id, "status": ReleaseStatus.DRAFT.value},
            {"$set": {
                "status": ReleaseStatus.VALIDATED.value,
                "validated_at": now,
                "updated_at": now,
            }},
        )

        if result.modified_count != 1:
            # Re-read to report accurate current status
            refreshed = await self._releases.find_one({"release_id": release_id})
            actual = refreshed["status"] if refreshed else "unknown"
            raise InvalidTransitionError(
                f"Release '{release_id}' CAS conflict during validate_release(): "
                f"current status is {actual!r}"
            )

        logger.info("release_validated release_id=%s", release_id)

    # ------------------------------------------------------------------
    # Approve — VALIDATED → APPROVED
    # ------------------------------------------------------------------

    async def approve_release(self, release_id: str, approved_by: str = "system") -> None:
        """Transition a VALIDATED release to APPROVED.

        Only explicitly VALIDATED releases may be approved.
        DRAFT → APPROVED is rejected; the caller must call validate_release() first.

        Raises:
            ConfigurationReleaseNotFoundError: release does not exist.
            InvalidTransitionError: release is not in VALIDATED status, or CAS conflict.
        """
        doc = await self._releases.find_one({"release_id": release_id})
        if not doc:
            raise ConfigurationReleaseNotFoundError(
                f"Release '{release_id}' not found"
            )

        current_status = ReleaseStatus(doc["status"])
        if current_status != ReleaseStatus.VALIDATED:
            raise InvalidTransitionError(
                f"Release '{release_id}' is in status {current_status.value!r}; "
                f"approve_release() requires VALIDATED"
            )

        now = _now()
        result = await self._releases.update_one(
            {"release_id": release_id, "status": ReleaseStatus.VALIDATED.value},
            {"$set": {
                "status": ReleaseStatus.APPROVED.value,
                "approved_at": now,
                "approved_by": approved_by,
                "updated_at": now,
            }},
        )

        if result.modified_count != 1:
            refreshed = await self._releases.find_one({"release_id": release_id})
            actual = refreshed["status"] if refreshed else "unknown"
            raise InvalidTransitionError(
                f"Release '{release_id}' CAS conflict during approve_release(): "
                f"current status is {actual!r}"
            )

        logger.info(
            "release_approved release_id=%s approved_by=%s", release_id, approved_by
        )

    # ------------------------------------------------------------------
    # Reject — VALIDATED → DRAFT (optional rejection path)
    # ------------------------------------------------------------------

    async def reject_release(self, release_id: str) -> None:
        """Transition a VALIDATED release back to DRAFT.

        Allows re-validation after corrections.  Does not affect DRAFT or
        APPROVED/ACTIVE/SUPERSEDED releases.

        Raises:
            ConfigurationReleaseNotFoundError: release does not exist.
            InvalidTransitionError: release is not in VALIDATED status.
        """
        doc = await self._releases.find_one({"release_id": release_id})
        if not doc:
            raise ConfigurationReleaseNotFoundError(
                f"Release '{release_id}' not found"
            )

        current_status = ReleaseStatus(doc["status"])
        if current_status != ReleaseStatus.VALIDATED:
            raise InvalidTransitionError(
                f"Release '{release_id}' is in status {current_status.value!r}; "
                f"reject_release() requires VALIDATED"
            )

        now = _now()
        result = await self._releases.update_one(
            {"release_id": release_id, "status": ReleaseStatus.VALIDATED.value},
            {"$set": {
                "status": ReleaseStatus.DRAFT.value,
                "validated_at": None,
                "updated_at": now,
            }},
        )

        if result.modified_count != 1:
            raise InvalidTransitionError(
                f"Release '{release_id}' CAS conflict during reject_release()"
            )

        logger.info("release_rejected_to_draft release_id=%s", release_id)
