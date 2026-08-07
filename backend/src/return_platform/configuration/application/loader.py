from pathlib import Path
import yaml
from typing import Any, Dict

class ConfigurationLoader:
    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir

    def load_file(self, relative_path: str) -> Dict[str, Any]:
        file_path = self._config_dir / relative_path
        if not file_path.exists():
            return {}
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def load_directory_yaml(self, relative_dir: str) -> Dict[str, Dict[str, Any]]:
        dir_path = self._config_dir / relative_dir
        results = {}
        if dir_path.exists() and dir_path.is_dir():
            for yaml_file in dir_path.glob("*.yaml"):
                content = self.load_file(f"{relative_dir}/{yaml_file.name}")
                if isinstance(content, dict):
                    results[yaml_file.stem] = content
        return results
