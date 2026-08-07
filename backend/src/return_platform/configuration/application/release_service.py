from pymongo import AsyncMongoClient
from datetime import datetime, timezone
import hashlib
import json
from return_platform.configuration.domain.release import ConfigurationRelease, ReleaseStatus, RuntimeSnapshot

class ReleaseService:
    def __init__(self, client: AsyncMongoClient[dict[str, object]]) -> None:
        self._client = client
        self._db = client.get_database("platform")
        self._releases = self._db.get_collection("configuration_releases")

    def compute_checksum(self, snapshot: RuntimeSnapshot) -> str:
        raw_json = json.dumps(snapshot.model_dump(), sort_keys=True)
        return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()

    async def create_release(self, release_id: str, snapshot: RuntimeSnapshot) -> ConfigurationRelease:
        checksum = self.compute_checksum(snapshot)
        now = datetime.now(timezone.utc)
        release = ConfigurationRelease(
            release_id=release_id,
            status=ReleaseStatus.APPROVED,
            snapshot=snapshot,
            created_at=now,
            updated_at=now,
            checksum=checksum
        )
        await self._releases.insert_one({
            "release_id": release_id,
            "status": release.status.value,
            "snapshot": snapshot.model_dump(),
            "created_at": now,
            "updated_at": now,
            "checksum": checksum
        })
        return release
