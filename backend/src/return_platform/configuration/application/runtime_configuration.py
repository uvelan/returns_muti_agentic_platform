from typing import Any, Dict
from pymongo import AsyncMongoClient
from return_platform.configuration.domain.handle import ConfigurationView, ConfigurationHandle
from return_platform.configuration.domain.release import RuntimeSnapshot
from return_platform.platform.contracts.epoch import RuntimeEpoch

class ReleaseNotRetained(RuntimeError):
    pass

class RuntimeConfigurationViewImpl(ConfigurationView):
    def __init__(self, release_id: str, snapshot: RuntimeSnapshot, checksum: str):
        self._release_id = release_id
        self._snapshot = snapshot
        self._checksum = checksum
        
    @property
    def release_id(self) -> str:
        return self._release_id
        
    @property
    def checksum(self) -> str:
        return self._checksum
        
    def section(self, name: str) -> Any:
        return getattr(self._snapshot, name, None)

class RuntimeConfigurationHandleImpl(ConfigurationHandle):
    def __init__(self, client: AsyncMongoClient[dict[str, object]], load_snapshot_fn) -> None:
        self._client = client
        self._db = client.get_database("platform")
        self._load_snapshot_fn = load_snapshot_fn
        self._views: Dict[str, ConfigurationView] = {}
        # Mapping from epoch number to release ID
        self._epoch_releases: Dict[int, str] = {}
        self._adopted_release_id: str = ""
        self._pending_release_id: str | None = None
        self._requires_restart: bool = False
        
    def set_current(self, epoch: RuntimeEpoch, view: ConfigurationView) -> None:
        self._views[view.release_id] = view
        self._epoch_releases[epoch.epoch] = view.release_id
        
    def current(self, epoch: RuntimeEpoch) -> ConfigurationView:
        release_id = self._epoch_releases.get(epoch.epoch)
        if not release_id:
            raise RuntimeError(f"Configuration handle has no view for epoch {epoch.epoch}")
        return self._views[release_id]
        
    async def pinned(self, release_id: str) -> ConfigurationView:
        if release_id in self._views:
            return self._views[release_id]
            
        snapshot_doc = await self._db.get_collection("configuration_releases").find_one({"release_id": release_id})
        if not snapshot_doc:
            raise ReleaseNotRetained(f"Configuration release {release_id} not available or not retained")
            
        snapshot = self._load_snapshot_fn(snapshot_doc["snapshot"])
        checksum = snapshot_doc.get("checksum", "")
        
        view = RuntimeConfigurationViewImpl(release_id, snapshot, checksum)
        self._views[release_id] = view
        return view

    @property
    def adopted_release_id(self) -> str:
        # Not fully tracking this here, assume reconciler updates an external object or we can compute it.
        # Wait, the simplest way is to expose these properties.
        # Actually, let's just add the setters/getters.
        return self._adopted_release_id

    @adopted_release_id.setter
    def adopted_release_id(self, val: str) -> None:
        self._adopted_release_id = val

    @property
    def pending_release_id(self) -> str | None:
        return self._pending_release_id

    @pending_release_id.setter
    def pending_release_id(self, val: str | None) -> None:
        self._pending_release_id = val

    @property
    def requires_restart(self) -> bool:
        return self._requires_restart

    @requires_restart.setter
    def requires_restart(self, val: bool) -> None:
        self._requires_restart = val
