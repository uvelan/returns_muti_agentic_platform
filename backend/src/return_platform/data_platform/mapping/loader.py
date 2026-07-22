"""Bounded loader for the four versioned data-platform YAML profiles."""

from __future__ import annotations

import hashlib
import stat
from collections.abc import Hashable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Never, cast

import yaml
from pydantic import TypeAdapter, ValidationError
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from return_platform.canonical.base import VersionReference
from return_platform.data_platform.mapping.contracts import (
    DataPlatformMappingBundle,
)

__all__ = [
    "CONFIGURATION_DIGEST_ALGORITHM",
    "CONFIGURATION_FILE_NAMES",
    "MAX_CONFIGURATION_FILE_BYTES",
    "MAX_CONFIGURATION_TOTAL_BYTES",
    "LoadedDataPlatformConfiguration",
    "LoadedDataPlatformFile",
    "MappingConfigurationErrorCode",
    "MappingConfigurationLoadError",
    "load_data_platform_mapping_configuration",
]

MAX_CONFIGURATION_FILE_BYTES: Final = 1_048_576
"""Maximum bytes accepted from any one configuration file."""

MAX_CONFIGURATION_TOTAL_BYTES: Final = 4 * MAX_CONFIGURATION_FILE_BYTES
"""Maximum bytes accepted across all four configuration files."""

CONFIGURATION_DIGEST_ALGORITHM: Final = "sha256"
"""Digest algorithm used for file and merged-configuration evidence."""

_DIGEST_DOMAIN: Final = b"return-platform:data-platform-configuration:v1\x00"


@dataclass(frozen=True, slots=True)
class _ConfigurationFileSpec:
    """One required configuration filename and its exact root collection key."""

    filename: str
    root_keys: tuple[str, ...]


_CONFIGURATION_FILE_SPECS: Final = (
    _ConfigurationFileSpec("sources.yaml", ("source_assets",)),
    _ConfigurationFileSpec("canonical_mappings.yaml", ("canonical_mappings",)),
    _ConfigurationFileSpec(
        "graph_projection.yaml",
        ("graph_nodes", "graph_relationships"),
    ),
    _ConfigurationFileSpec("sync_pipelines.yaml", ("sync_pipelines",)),
)

CONFIGURATION_FILE_NAMES: Final = tuple(spec.filename for spec in _CONFIGURATION_FILE_SPECS)
"""Required files in deterministic digest and load order."""

_VERSION_ADAPTER: Final = TypeAdapter(VersionReference)


class MappingConfigurationErrorCode(StrEnum):
    """Stable public failure codes for configuration loading."""

    DIRECTORY_NOT_FOUND = "DIRECTORY_NOT_FOUND"
    DIRECTORY_NOT_DIRECTORY = "DIRECTORY_NOT_DIRECTORY"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    FILE_SYMLINK = "FILE_SYMLINK"
    FILE_NOT_REGULAR = "FILE_NOT_REGULAR"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    TOTAL_SIZE_EXCEEDED = "TOTAL_SIZE_EXCEEDED"
    READ_FAILED = "READ_FAILED"
    INVALID_ENCODING = "INVALID_ENCODING"
    EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
    YAML_ALIAS_NOT_ALLOWED = "YAML_ALIAS_NOT_ALLOWED"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    INVALID_YAML = "INVALID_YAML"
    INVALID_ROOT = "INVALID_ROOT"
    INVALID_ROOT_KEYS = "INVALID_ROOT_KEYS"
    INVALID_SCHEMA_VERSION = "INVALID_SCHEMA_VERSION"
    SCHEMA_VERSION_MISMATCH = "SCHEMA_VERSION_MISMATCH"
    VALIDATION_FAILED = "VALIDATION_FAILED"


_SAFE_MESSAGES: Final = {
    MappingConfigurationErrorCode.DIRECTORY_NOT_FOUND: (
        "The data-platform configuration directory does not exist."
    ),
    MappingConfigurationErrorCode.DIRECTORY_NOT_DIRECTORY: (
        "The data-platform configuration path is not a directory."
    ),
    MappingConfigurationErrorCode.FILE_NOT_FOUND: (
        "A required data-platform configuration file is missing."
    ),
    MappingConfigurationErrorCode.FILE_SYMLINK: ("Configuration files must not be symbolic links."),
    MappingConfigurationErrorCode.FILE_NOT_REGULAR: (
        "A required configuration path is not a regular file."
    ),
    MappingConfigurationErrorCode.FILE_TOO_LARGE: (
        "A data-platform configuration file exceeds the size limit."
    ),
    MappingConfigurationErrorCode.TOTAL_SIZE_EXCEEDED: (
        "The data-platform configuration exceeds the total size limit."
    ),
    MappingConfigurationErrorCode.READ_FAILED: (
        "A data-platform configuration file could not be read."
    ),
    MappingConfigurationErrorCode.INVALID_ENCODING: (
        "Data-platform configuration files must use UTF-8 encoding."
    ),
    MappingConfigurationErrorCode.EMPTY_DOCUMENT: ("A data-platform configuration file is empty."),
    MappingConfigurationErrorCode.YAML_ALIAS_NOT_ALLOWED: (
        "YAML aliases are not allowed in data-platform configuration."
    ),
    MappingConfigurationErrorCode.DUPLICATE_KEY: ("Duplicate YAML mapping keys are not allowed."),
    MappingConfigurationErrorCode.INVALID_YAML: (
        "A data-platform configuration file contains invalid YAML."
    ),
    MappingConfigurationErrorCode.INVALID_ROOT: (
        "Each data-platform configuration file must contain a YAML mapping."
    ),
    MappingConfigurationErrorCode.INVALID_ROOT_KEYS: (
        "A data-platform configuration file has invalid root keys."
    ),
    MappingConfigurationErrorCode.INVALID_SCHEMA_VERSION: (
        "A data-platform configuration schema version is invalid."
    ),
    MappingConfigurationErrorCode.SCHEMA_VERSION_MISMATCH: (
        "All data-platform configuration files must use the same schema version."
    ),
    MappingConfigurationErrorCode.VALIDATION_FAILED: (
        "The merged data-platform configuration is invalid."
    ),
}


class MappingConfigurationLoadError(RuntimeError):
    """Safe public error raised when configuration loading fails."""

    def __init__(self, code: MappingConfigurationErrorCode) -> None:
        """Initialize one safe load failure from its stable code."""
        self.code = code
        self.safe_message = _SAFE_MESSAGES[code]
        super().__init__(self.safe_message)


@dataclass(frozen=True, slots=True)
class LoadedDataPlatformFile:
    """Immutable evidence for one loaded configuration file."""

    filename: str
    resolved_path: Path
    byte_size: int
    sha256_digest: str


@dataclass(frozen=True, slots=True)
class LoadedDataPlatformConfiguration:
    """Validated bundle plus deterministic multi-file evidence."""

    bundle: DataPlatformMappingBundle
    resolved_directory: Path
    files: tuple[LoadedDataPlatformFile, ...]
    total_byte_size: int
    configuration_digest: str


class _YamlAliasNotAllowedError(yaml.YAMLError):
    """Internal marker used to map aliases to a stable public error code."""


class _DuplicateYamlKeyError(yaml.YAMLError):
    """Internal marker used to map duplicate keys to a stable public code."""


class _UnhashableYamlKeyError(yaml.YAMLError):
    """Internal marker used to reject unsupported mapping-key shapes."""


class _StrictSafeLoader(yaml.SafeLoader):
    """SafeLoader variant rejecting aliases and duplicate mapping keys."""

    def compose_node(self, parent: Node | None, index: int) -> Node:
        """Reject YAML aliases before SafeLoader resolves them."""
        if self.check_event(AliasEvent):  # type: ignore[no-untyped-call]
            self.get_event()  # type: ignore[no-untyped-call]
            raise _YamlAliasNotAllowedError
        return cast("Node", super().compose_node(parent, index))


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: Node,
    *,
    deep: bool = False,
) -> dict[object, object]:
    """Construct a mapping while rejecting duplicate and unhashable keys."""
    if not isinstance(node, MappingNode):
        raise yaml.constructor.ConstructorError(
            None,
            None,
            "expected a mapping node",
            node.start_mark,
        )

    result: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise _UnhashableYamlKeyError
        if key in result:
            raise _DuplicateYamlKeyError
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _raise_load_error(code: MappingConfigurationErrorCode) -> Never:
    """Raise one stable, non-sensitive configuration failure."""
    raise MappingConfigurationLoadError(code)


def _resolve_configuration_directory(directory: Path) -> Path:
    """Resolve and validate the configured directory without creating it."""
    if not isinstance(directory, Path):
        message = "directory must be a pathlib.Path"
        raise TypeError(message)

    try:
        resolved = directory.expanduser().resolve(strict=False)
    except OSError:
        _raise_load_error(MappingConfigurationErrorCode.READ_FAILED)

    if not resolved.exists():
        _raise_load_error(MappingConfigurationErrorCode.DIRECTORY_NOT_FOUND)
    if not resolved.is_dir():
        _raise_load_error(MappingConfigurationErrorCode.DIRECTORY_NOT_DIRECTORY)
    return resolved


def _read_bounded_file(path: Path) -> bytes:
    """Read one regular non-symlink file with a hard byte limit."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        _raise_load_error(MappingConfigurationErrorCode.FILE_NOT_FOUND)
    except OSError:
        _raise_load_error(MappingConfigurationErrorCode.READ_FAILED)

    if stat.S_ISLNK(metadata.st_mode):
        _raise_load_error(MappingConfigurationErrorCode.FILE_SYMLINK)
    if not stat.S_ISREG(metadata.st_mode):
        _raise_load_error(MappingConfigurationErrorCode.FILE_NOT_REGULAR)
    if metadata.st_size > MAX_CONFIGURATION_FILE_BYTES:
        _raise_load_error(MappingConfigurationErrorCode.FILE_TOO_LARGE)

    try:
        with path.open("rb") as stream:
            content = stream.read(MAX_CONFIGURATION_FILE_BYTES + 1)
    except FileNotFoundError:
        _raise_load_error(MappingConfigurationErrorCode.FILE_NOT_FOUND)
    except OSError:
        _raise_load_error(MappingConfigurationErrorCode.READ_FAILED)

    if len(content) > MAX_CONFIGURATION_FILE_BYTES:
        _raise_load_error(MappingConfigurationErrorCode.FILE_TOO_LARGE)
    return content


def _decode_utf8(content: bytes) -> str:
    """Decode UTF-8 and permit a single standard UTF-8 BOM."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        _raise_load_error(MappingConfigurationErrorCode.INVALID_ENCODING)


def _parse_yaml_mapping(text: str) -> dict[str, object]:
    """Parse one strict YAML document into a string-keyed root mapping."""
    try:
        parsed = yaml.load(
            text,
            Loader=_StrictSafeLoader,
        )
    except _YamlAliasNotAllowedError:
        _raise_load_error(MappingConfigurationErrorCode.YAML_ALIAS_NOT_ALLOWED)
    except _DuplicateYamlKeyError:
        _raise_load_error(MappingConfigurationErrorCode.DUPLICATE_KEY)
    except _UnhashableYamlKeyError:
        _raise_load_error(MappingConfigurationErrorCode.INVALID_YAML)
    except yaml.YAMLError:
        _raise_load_error(MappingConfigurationErrorCode.INVALID_YAML)

    if parsed is None:
        _raise_load_error(MappingConfigurationErrorCode.EMPTY_DOCUMENT)
    if not isinstance(parsed, dict):
        _raise_load_error(MappingConfigurationErrorCode.INVALID_ROOT)
    if not all(isinstance(key, str) for key in parsed):
        _raise_load_error(MappingConfigurationErrorCode.INVALID_ROOT_KEYS)
    return cast("dict[str, object]", parsed)


def _validate_root(
    root: dict[str, object],
    spec: _ConfigurationFileSpec,
) -> str:
    """Require exact root keys and return one validated schema version."""
    expected_keys = {"schema_version", *spec.root_keys}
    if set(root) != expected_keys:
        _raise_load_error(MappingConfigurationErrorCode.INVALID_ROOT_KEYS)

    try:
        return _VERSION_ADAPTER.validate_python(
            root["schema_version"],
            strict=True,
        )
    except ValidationError:
        _raise_load_error(MappingConfigurationErrorCode.INVALID_SCHEMA_VERSION)


def _calculate_configuration_digest(
    files: tuple[tuple[_ConfigurationFileSpec, bytes], ...],
) -> str:
    """Hash exact bytes with names, lengths, fixed order, and a domain tag."""
    digest = hashlib.sha256()
    digest.update(_DIGEST_DOMAIN)

    for spec, content in files:
        filename = spec.filename.encode("utf-8")
        digest.update(len(filename).to_bytes(4, "big"))
        digest.update(filename)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)

    return digest.hexdigest()


def load_data_platform_mapping_configuration(
    directory: Path,
) -> LoadedDataPlatformConfiguration:
    """Load, merge, hash, and validate the four fixed YAML profiles."""
    resolved_directory = _resolve_configuration_directory(directory)

    raw_files: list[tuple[_ConfigurationFileSpec, bytes]] = []
    loaded_files: list[LoadedDataPlatformFile] = []
    roots: dict[str, dict[str, object]] = {}
    schema_versions: list[str] = []
    total_byte_size = 0

    for spec in _CONFIGURATION_FILE_SPECS:
        path = resolved_directory / spec.filename
        content = _read_bounded_file(path)
        total_byte_size += len(content)
        if total_byte_size > MAX_CONFIGURATION_TOTAL_BYTES:
            _raise_load_error(MappingConfigurationErrorCode.TOTAL_SIZE_EXCEEDED)

        root = _parse_yaml_mapping(_decode_utf8(content))
        schema_version = _validate_root(root, spec)

        raw_files.append((spec, content))
        roots[spec.filename] = root
        schema_versions.append(schema_version)
        loaded_files.append(
            LoadedDataPlatformFile(
                filename=spec.filename,
                resolved_path=path,
                byte_size=len(content),
                sha256_digest=hashlib.sha256(content).hexdigest(),
            )
        )

    if len(set(schema_versions)) != 1:
        _raise_load_error(MappingConfigurationErrorCode.SCHEMA_VERSION_MISMATCH)

    merged_payload = {
        "schema_version": schema_versions[0],
        "source_assets": roots["sources.yaml"]["source_assets"],
        "canonical_mappings": roots["canonical_mappings.yaml"]["canonical_mappings"],
        "graph_nodes": roots["graph_projection.yaml"]["graph_nodes"],
        "graph_relationships": roots["graph_projection.yaml"].get(
            "graph_relationships",
            [],
        ),
        "sync_pipelines": roots["sync_pipelines.yaml"]["sync_pipelines"],
    }

    try:
        bundle = DataPlatformMappingBundle.model_validate(merged_payload)
    except ValidationError:
        _raise_load_error(MappingConfigurationErrorCode.VALIDATION_FAILED)

    raw_files_tuple = tuple(raw_files)
    return LoadedDataPlatformConfiguration(
        bundle=bundle,
        resolved_directory=resolved_directory,
        files=tuple(loaded_files),
        total_byte_size=total_byte_size,
        configuration_digest=_calculate_configuration_digest(raw_files_tuple),
    )
