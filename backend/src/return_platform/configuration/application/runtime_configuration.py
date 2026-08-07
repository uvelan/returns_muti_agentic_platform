from typing import Any, Dict
from return_platform.configuration.domain.handle import ConfigurationView, ConfigurationHandle
from return_platform.configuration.domain.release import RuntimeSnapshot

class RuntimeConfigurationViewImpl(ConfigurationView):
    def __init__(self, release_id: str, snapshot: RuntimeSnapshot):
        self._release_id = release_id
        self._snapshot = snapshot
        
    @property
    def release_id(self) -> str:
        return self._release_id
        
    def section(self, name: str) -> Any:
        return getattr(self._snapshot, name, None)

class RuntimeConfigurationHandleImpl(ConfigurationHandle):
    def __init__(self) -> None:
        self._current_view: ConfigurationView | None = None
        self._views: Dict[str, ConfigurationView] = {}
        
    def set_current(self, view: ConfigurationView) -> None:
        self._current_view = view
        self._views[view.release_id] = view
        
    def current(self) -> ConfigurationView:
        if not self._current_view:
            raise RuntimeError("Configuration handle has no current view initialized")
        return self._current_view
        
    def pinned(self, release_id: str) -> ConfigurationView:
        if release_id not in self._views:
            raise KeyError(f"Configuration release {release_id} not available or not retained")
        return self._views[release_id]
