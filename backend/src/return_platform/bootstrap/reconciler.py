import asyncio
import logging
from pymongo import AsyncMongoClient
from datetime import datetime, timezone

from return_platform.bootstrap.epoch import EpochAdmission, EpochAllocator
from return_platform.platform.modules.contracts import ModuleRuntime, ReconfigureOutcome
from return_platform.configuration.application.runtime_configuration import RuntimeConfigurationHandleImpl, RuntimeConfigurationViewImpl

logger = logging.getLogger(__name__)

class ConfigurationReconciler:
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        instance_id: str,
        epoch_admission: EpochAdmission,
        epoch_allocator: EpochAllocator,
        modules: list[ModuleRuntime],
        config_handle: RuntimeConfigurationHandleImpl,
        load_snapshot_fn
    ):
        self._client = client
        self._db = client.get_database("platform")
        self._pointer = self._db.get_collection("configuration_active_pointer")
        self._adoption = self._db.get_collection("configuration_adoption")
        
        self._instance_id = instance_id
        self._epoch_admission = epoch_admission
        self._epoch_allocator = epoch_allocator
        self._modules = modules
        self._config_handle = config_handle
        self._load_snapshot_fn = load_snapshot_fn
        
        # Determine initial active release id
        try:
            self._active_release_id = epoch_admission.current.release_id
        except Exception:
            self._active_release_id = None
        self._pending_release_id: str | None = None
        self._status = "ACTIVE"
        
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
        doc = await self._pointer.find_one({"_id": "singleton"})
        if not doc:
            return
            
        target_release_id = doc.get("active_release_id")
        if target_release_id and target_release_id != self._active_release_id and target_release_id != self._pending_release_id:
            await self._adopt_release(target_release_id)
            
    async def _adopt_release(self, target_release_id: str) -> None:
        try:
            snapshot_doc = await self._db.get_collection("configuration_releases").find_one({"release_id": target_release_id})
            if not snapshot_doc:
                logger.error(f"Target release {target_release_id} not found")
                return
                
            snapshot = self._load_snapshot_fn(snapshot_doc["snapshot"])
            new_view = RuntimeConfigurationViewImpl(target_release_id, snapshot)
            
            # The two phase protocol
            new_epoch = self._epoch_allocator.next(target_release_id)
            
            prepared = []
            abort = False
            for module in self._modules:
                try:
                    outcome = await module.prepare_reconfigure(new_epoch)
                    if outcome == ReconfigureOutcome.RESTART_REQUIRED:
                        abort = True
                        break
                    prepared.append(module)
                except Exception as e:
                    logger.error(f"Module {module} prepare raised: {e}")
                    abort = True
                    break
                    
            if abort:
                self._pending_release_id = target_release_id
                self._status = "DEGRADED"
                for module in prepared:
                    await module.abort_reconfigure(new_epoch)
                return
                
            self._epoch_admission.begin_swap(new_epoch, self._epoch_admission.current)
            try:
                for module in self._modules:
                    await module.commit_reconfigure(new_epoch)
            except Exception as e:
                logger.error(f"Module {module} commit raised: {e}. Replica is now UNAVAILABLE.")
                self._status = "UNAVAILABLE"
                self._epoch_admission.close()
                return
                
            self._config_handle.set_current(new_view)
            self._epoch_admission.finish_swap(new_epoch)
            
            self._active_release_id = target_release_id
            self._pending_release_id = None
            self._status = "ACTIVE"
            
        except Exception as e:
            logger.error(f"Failed to adopt {target_release_id}: {e}")
            self._pending_release_id = target_release_id
            self._status = "DEGRADED"
            
    async def _heartbeat(self) -> None:
        if not self._active_release_id:
            return
            
        await self._adoption.update_one(
            {"instance_id": self._instance_id},
            {"$set": {
                "active_release_id": self._active_release_id,
                "pending_release_id": self._pending_release_id,
                "status": self._status,
                "last_heartbeat": datetime.now(timezone.utc)
            }},
            upsert=True
        )
