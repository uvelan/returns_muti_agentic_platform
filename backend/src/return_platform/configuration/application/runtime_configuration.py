from collections.abc import Callable
from typing import Any

from pymongo import AsyncMongoClient

from return_platform.configuration.application.snapshot import verify_snapshot_integrity
from return_platform.configuration.domain.handle import ConfigurationHandle, ConfigurationView
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.platform.contracts.runtime_configuration import ReleaseNotRetained


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
        attr = getattr(self._snapshot, name, None)
        if attr is not None and hasattr(attr, "model_dump"):
            return attr.model_dump()
        return attr


class RuntimeConfigurationHandleImpl(ConfigurationHandle):
    def __init__(
        self,
        client: AsyncMongoClient[dict[str, object]],
        load_snapshot_fn: Callable[[Any], Any],
    ) -> None:
        self._client = client
        self._db = client.get_database("platform")
        self._load_snapshot_fn = load_snapshot_fn
        self._views: dict[str, ConfigurationView] = {}
        # Mapping from epoch number to release ID
        self._epoch_releases: dict[int, str] = {}
        self._adopted_release_id: str = ""
        self._pending_release_id: str | None = None
        self._requires_restart: bool = False

    def set_current(self, epoch: Any, view: ConfigurationView) -> None:
        epoch_num = getattr(epoch, "epoch", epoch)
        self._views[view.release_id] = view
        self._epoch_releases[epoch_num] = view.release_id

    def current(self, epoch: Any) -> ConfigurationView:
        epoch_num = getattr(epoch, "epoch", epoch)
        release_id = self._epoch_releases.get(epoch_num)
        if not release_id:
            raise RuntimeError(f"Configuration handle has no view for epoch {epoch_num}")
        return self._views[release_id]

    async def pinned(self, release_id: str) -> ConfigurationView:
        if release_id in self._views:
            return self._views[release_id]

        snapshot_doc = await self._db.get_collection("configuration_releases").find_one(
            {"release_id": release_id}
        )
        if not snapshot_doc:
            raise ReleaseNotRetained(
                f"Configuration release {release_id} not available or not retained"
            )

        snapshot = self._load_snapshot_fn(snapshot_doc["snapshot"])
        checksum = str(snapshot_doc.get("checksum", ""))

        verify_snapshot_integrity(snapshot, checksum)

        view = RuntimeConfigurationViewImpl(release_id, snapshot, checksum)
        self._views[release_id] = view
        return view

    @property
    def adopted_release_id(self) -> str:
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
