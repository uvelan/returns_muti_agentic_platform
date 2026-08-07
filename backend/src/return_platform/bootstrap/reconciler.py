import asyncio
import logging
from pymongo import AsyncMongoClient
from datetime import datetime, timezone

from return_platform.bootstrap.epoch import EpochAllocator, ReconfigurationCoordinator
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl, RuntimeConfigurationViewImpl

logger = logging.getLogger(__name__)

class ConfigurationReconciler:
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        instance_id: str,
        coordinator: ReconfigurationCoordinator,
        epoch_allocator: EpochAllocator,
        config_handle: RuntimeConfigurationHandleImpl,
        load_snapshot_fn
    ):
        self._client = client
        self._db = client.get_database("platform")
        self._pointer = self._db.get_collection("configuration_active_pointer")
        self._adoption = self._db.get_collection("configuration_adoption")
        
        self._instance_id = instance_id
        self._coordinator = coordinator
        self._epoch_allocator = epoch_allocator
        self._config_handle = config_handle
        self._load_snapshot_fn = load_snapshot_fn
        
        self._active_release_id = None
        self._active_epoch: int = 0
        self._adopted_at: datetime | None = None
        self._pending_release_id: str | None = None
        self._status = "ACTIVE"
        self._draining_epochs: list[int] = []
        
    async def run(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                await self._check_pointer()
                await self._heartbeat()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in reconciler loop: {e}", exc_info=True)
                
    async def _check_pointer(self) -> None:
        doc = await self._pointer.find_one({"_id": "active"})
        if not doc:
            return
            
        target_release_id = doc.get("release_id")
        if target_release_id and target_release_id != self._active_release_id and target_release_id != self._pending_release_id:
            await self._adopt_release(target_release_id, doc.get("checksum", ""))
            
    async def _adopt_release(self, target_release_id: str, checksum: str) -> None:
        try:
            snapshot_doc = await self._db.get_collection("configuration_releases").find_one({"release_id": target_release_id})
            if not snapshot_doc:
                logger.error(f"Target release {target_release_id} not found")
                return
                
            snapshot = self._load_snapshot_fn(snapshot_doc["snapshot"])
            new_view = RuntimeConfigurationViewImpl(target_release_id, snapshot, checksum)
            
            new_epoch = self._epoch_allocator.next(target_release_id)
            self._config_handle.set_current(new_epoch, new_view)
            
            retired_epoch = await self._coordinator.reconfigure(new_epoch)
            
            if retired_epoch is not None:
                # Success
                if self._active_epoch:
                    self._draining_epochs.append(self._active_epoch)
                self._active_release_id = target_release_id
                self._active_epoch = new_epoch.epoch
                self._adopted_at = datetime.now(timezone.utc)
                self._pending_release_id = None
                self._status = "ACTIVE"
                self._config_handle.adopted_release_id = target_release_id
                self._config_handle.pending_release_id = None
                self._config_handle.requires_restart = False
            else:
                # Refused (RESTART_REQUIRED or prepare exception)
                self._pending_release_id = target_release_id
                self._status = "DEGRADED"
                self._config_handle.pending_release_id = target_release_id
                if self._coordinator.status.value == "UNAVAILABLE":
                    self._config_handle.requires_restart = True
            
        except Exception as e:
            logger.error(f"Failed to adopt {target_release_id}: {e}")
            if self._coordinator.status.value == "UNAVAILABLE":
                self._status = "UNAVAILABLE"
                self._config_handle.requires_restart = True
            else:
                self._status = "DEGRADED"
                self._pending_release_id = target_release_id
                self._config_handle.pending_release_id = target_release_id
                
    async def _heartbeat(self) -> None:
        if not self._active_release_id and not self._pending_release_id:
            return
            
        now = datetime.now(timezone.utc)
        await self._adoption.update_one(
            {"instance_id": self._instance_id},
            {"$set": {
                "adopted_release_id": self._active_release_id or "",
                "adopted_epoch": self._active_epoch,
                "adopted_at": self._adopted_at or now,
                "pending_release_id": self._pending_release_id,
                "requires_restart": self._config_handle.requires_restart,
                "draining_epochs": self._draining_epochs,
                "heartbeat_at": now
            }},
            upsert=True
        )
