"""Manifest-driven configuration loader.

The manifest is authoritative for modular configuration.
Directory globbing is NOT used as the authoritative load mechanism.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict

from return_platform.configuration.domain.release import ReleaseStatus

# ---------------------------------------------------------------------------
# Manifest model
# ---------------------------------------------------------------------------


class ManifestEntry(BaseModel):
    """A single module entry in the configuration manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str


class ConfigurationManifest(BaseModel):
    """Typed representation of manifest.yaml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str
    release_id: str
    status: ReleaseStatus
    modules: Mapping[str, ManifestEntry]


class LoadedManifestModule(BaseModel):
    """A manifest entry together with its loaded and validated document."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manifest_id: str
    path: str
    module_id: str
    module_type: str
    document: Mapping[str, Any]


# ---------------------------------------------------------------------------
# Module type prefix mapping
# ---------------------------------------------------------------------------

# Supported manifest schema versions.  Reject anything not in this set so that
# a future format change cannot silently load as if it were version 2.0.
SUPPORTED_MANIFEST_SCHEMA_VERSIONS: frozenset[str] = frozenset({"2.0"})

MODULE_TYPE_PREFIX: dict[str, str] = {
    "agent": "AGENT",
    "policy": "POLICY",
    "workflow": "WORKFLOW",
    "sync": "SYNC",
    "source": "SOURCE",
    "mapping": "MAPPING",
    "graph": "GRAPH",
    "platform": "PLATFORM",
    "integration": "INTEGRATION",
}


def _expected_module_type(manifest_id: str) -> str | None:
    """Return the expected module_type string for a manifest ID, or None."""
    prefix = manifest_id.split(".")[0]
    return MODULE_TYPE_PREFIX.get(prefix)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class ConfigurationLoader:
    """Manifest-driven configuration loader with security and integrity checks."""

    def __init__(self, config_dir: Path) -> None:
        self._config_dir = config_dir.resolve()

    # ------------------------------------------------------------------
    # Single-file helpers (used by compatibility adapter for singletons)
    # ------------------------------------------------------------------

    def load_file(self, relative_path: str) -> dict[str, Any]:
        """Load and parse a single YAML file relative to config_dir.

        Returns an empty dict if the file does not exist.
        Singleton files (ai_gateway.yaml, returns/production.yaml) are allowed
        to be loaded by name without being declared in the manifest.
        """
        file_path = (self._config_dir / relative_path).resolve()
        if not str(file_path).startswith(str(self._config_dir)):
            raise ValueError(f"Path traversal detected: {relative_path!r}")
        if not file_path.exists():
            return {}
        return self._parse_yaml_file(file_path)

    # ------------------------------------------------------------------
    # Manifest loading
    # ------------------------------------------------------------------

    def load_manifest(self, manifest_path: Path | None = None) -> ConfigurationManifest:
        """Load, parse, and validate manifest.yaml.

        Raises ValueError on any structural problem (missing fields,
        duplicate keys, path traversal, unsupported schema version).
        """
        if manifest_path is None:
            manifest_path = self._config_dir / "manifest.yaml"

        if not manifest_path.exists():
            raise ValueError(f"Manifest not found: {manifest_path}")

        raw = self._parse_yaml_file(manifest_path, detect_duplicates=True)

        if not isinstance(raw, dict):
            raise ValueError("Manifest YAML root must be a mapping")

        schema_ver = str(raw.get("schema_version", ""))
        if not schema_ver:
            raise ValueError("Manifest is missing schema_version")
        if schema_ver not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
            raise ValueError(
                f"Manifest schema_version {schema_ver!r} is not supported. "
                f"Supported versions: {sorted(SUPPORTED_MANIFEST_SCHEMA_VERSIONS)}"
            )

        release_id = str(raw.get("release_id", ""))
        if not release_id:
            raise ValueError("Manifest is missing release_id")

        status_raw = str(raw.get("status", "DRAFT")).upper()
        try:
            status = ReleaseStatus(status_raw)
        except ValueError:
            # `from None`, not `from exc`: the enum's own message says the same thing
            # ("'FOO' is not a valid ReleaseStatus"), so chaining adds a frame that
            # repeats the one above it.
            raise ValueError(
                f"Manifest status {status_raw!r} is not a valid ReleaseStatus"
            ) from None

        modules_raw = raw.get("modules", {})
        if not isinstance(modules_raw, dict):
            raise ValueError("Manifest 'modules' must be a mapping")

        modules: dict[str, ManifestEntry] = {}
        for mod_id, entry_raw in modules_raw.items():
            if not mod_id:
                raise ValueError("Manifest module ID must be non-empty")
            if not isinstance(entry_raw, dict) or "path" not in entry_raw:
                raise ValueError(f"Manifest module {mod_id!r} must have a 'path' field")
            modules[mod_id] = ManifestEntry(path=entry_raw["path"])

        return ConfigurationManifest(
            schema_version=schema_ver,
            release_id=release_id,
            status=status,
            modules=modules,
        )

    # ------------------------------------------------------------------
    # Manifest-driven module loading
    # ------------------------------------------------------------------

    def load_manifest_entries(
        self,
        manifest: ConfigurationManifest,
    ) -> dict[str, LoadedManifestModule]:
        """Load every module declared in the manifest.

        Enforces for each entry:
        1. No absolute paths.
        2. Resolved path remains under config_dir (path traversal prevention).
        3. File exists.
        4. Regular file (not a directory or symlink to a directory).
        5. Parsed YAML document has module_id == manifest ID.
        6. module_type prefix matches manifest ID prefix.

        Returns a mapping of manifest_id → LoadedManifestModule.
        Raises ValueError for any integrity violation.
        """
        result: dict[str, LoadedManifestModule] = {}

        for manifest_id, entry in manifest.modules.items():
            raw_path = entry.path

            # 1. No absolute paths
            if os.path.isabs(raw_path):
                raise ValueError(
                    f"Manifest module {manifest_id!r} declares an absolute path: {raw_path!r}"
                )

            # 2. Resolve and check containment
            resolved = (self._config_dir / raw_path).resolve()
            if (
                not str(resolved).startswith(str(self._config_dir) + os.sep)
                and resolved != self._config_dir
            ):
                raise ValueError(
                    f"Manifest module {manifest_id!r} path escapes config directory: {raw_path!r}"
                )

            # 3. File must exist
            if not resolved.exists():
                raise ValueError(
                    f"Manifest module {manifest_id!r} declares missing file: {raw_path!r}"
                )

            # 4. Must be a regular file
            if not resolved.is_file():
                raise ValueError(
                    f"Manifest module {manifest_id!r} path is not a regular file: {raw_path!r}"
                )

            # 5. Parse document
            document = self._parse_yaml_file(resolved)

            doc_module_id = document.get("module_id", "")
            doc_module_type = document.get("module_type", "")

            # 6. module_id must match manifest ID
            if doc_module_id != manifest_id:
                raise ValueError(
                    f"Manifest module {manifest_id!r}: document module_id is {doc_module_id!r}, "
                    f"expected {manifest_id!r}"
                )

            # 7. module_type prefix must agree with manifest ID prefix
            expected_type = _expected_module_type(manifest_id)
            if expected_type and doc_module_type != expected_type:
                raise ValueError(
                    f"Manifest module {manifest_id!r}: document module_type is {doc_module_type!r}, "
                    f"expected {expected_type!r} based on ID prefix"
                )

            result[manifest_id] = LoadedManifestModule(
                manifest_id=manifest_id,
                path=raw_path,
                module_id=doc_module_id,
                module_type=doc_module_type,
                document=document,
            )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_yaml_file(
        self,
        path: Path,
        detect_duplicates: bool = False,
    ) -> dict[str, Any]:
        with open(path, encoding="utf-8") as f:
            content = f.read()

        if detect_duplicates:
            return self._parse_yaml_detect_duplicates(content, path)

        result = yaml.safe_load(content)
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _parse_yaml_detect_duplicates(content: str, path: Path) -> dict[str, Any]:
        """Parse YAML while detecting duplicate mapping keys."""

        class _DuplicateDetector(yaml.SafeLoader):
            pass

        def _construct_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict:  # type: ignore[override]
            loader.flatten_mapping(node)
            pairs = loader.construct_pairs(node)
            keys_seen: set[str] = set()
            for key, _ in pairs:
                if key in keys_seen:
                    raise ValueError(f"Duplicate manifest key {key!r} in {path}")
                keys_seen.add(key)
            return dict(pairs)

        _DuplicateDetector.add_constructor(
            yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
            _construct_mapping,
        )

        result = yaml.load(content, Loader=_DuplicateDetector)  # noqa: S506
        return result if isinstance(result, dict) else {}
