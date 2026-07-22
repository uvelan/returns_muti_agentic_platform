"""Secure loading and validation for the data asset catalog."""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Final, Protocol, cast

import yaml
from pydantic import ValidationError
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from return_platform.shared.governance import AssetCatalog

DEFAULT_MAX_CATALOG_BYTES: Final[int] = 256 * 1024

_ALLOWED_CATALOG_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        ".yaml",
        ".yml",
    }
)


class CatalogLoadErrorCode(StrEnum):
    """Stable error classification for catalog-loading failures."""

    PATH_NOT_FOUND = "PATH_NOT_FOUND"
    PATH_NOT_FILE = "PATH_NOT_FILE"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    READ_FAILED = "READ_FAILED"
    INVALID_ENCODING = "INVALID_ENCODING"
    EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
    INVALID_YAML = "INVALID_YAML"
    INVALID_ROOT = "INVALID_ROOT"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class CatalogLoadError(RuntimeError):
    """Raised when the catalog cannot be safely loaded."""

    code: CatalogLoadErrorCode
    path: Path

    def __init__(
        self,
        *,
        code: CatalogLoadErrorCode,
        path: Path,
        message: str,
    ) -> None:
        super().__init__(message)

        self.code = code
        self.path = path


@dataclass(frozen=True, slots=True)
class LoadedAssetCatalog:
    """Validated catalog and deterministic source metadata."""

    catalog: AssetCatalog
    source_path: Path
    sha256_hex: str
    byte_size: int

    @property
    def asset_count(self) -> int:
        """Return the number of declared assets."""

        return len(self.catalog.assets)


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""

class _DisposableYamlLoader(Protocol):
    """Typed boundary for PyYAML's untyped dispose method."""

    def dispose(self) -> None:
        """Release parser and composer state."""

def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[object, object]:
    """Construct a mapping while rejecting duplicate keys."""

    if not isinstance(node, MappingNode):
        raise ConstructorError(
            "while constructing a mapping",
            node.start_mark,
            "expected a mapping node",
            node.start_mark,
        )

    mapping: dict[object, object] = {}

    for key_node, value_node in node.value:
        key: object = loader.construct_object(
            key_node,
            deep=deep,
        )

        if not isinstance(key, Hashable):
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            )

        if key in mapping:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )

        value: object = loader.construct_object(
            value_node,
            deep=deep,
        )

        mapping[key] = value

    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _resolve_catalog_path(
    path: str | Path,
) -> Path:
    requested_path = Path(path).expanduser()

    if requested_path.suffix.lower() not in _ALLOWED_CATALOG_SUFFIXES:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.UNSUPPORTED_EXTENSION,
            path=requested_path,
            message=(
                "The data asset catalog must use a "
                ".yaml or .yml extension."
            ),
        )

    try:
        resolved_path = requested_path.resolve(strict=True)
    except FileNotFoundError as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.PATH_NOT_FOUND,
            path=requested_path,
            message="The data asset catalog file does not exist.",
        ) from error
    except (OSError, RuntimeError) as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.READ_FAILED,
            path=requested_path,
            message=(
                "The data asset catalog path could not be resolved."
            ),
        ) from error

    if not resolved_path.is_file():
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.PATH_NOT_FILE,
            path=resolved_path,
            message=(
                "The data asset catalog path is not a regular file."
            ),
        )

    return resolved_path


def _read_catalog_bytes(
    path: Path,
    *,
    max_bytes: int,
) -> bytes:
    try:
        declared_size = path.stat().st_size
    except OSError as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.READ_FAILED,
            path=path,
            message=(
                "The data asset catalog metadata could not be read."
            ),
        ) from error

    if declared_size > max_bytes:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.FILE_TOO_LARGE,
            path=path,
            message=(
                "The data asset catalog exceeds the configured "
                "maximum size."
            ),
        )

    try:
        content = path.read_bytes()
    except OSError as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.READ_FAILED,
            path=path,
            message="The data asset catalog could not be read.",
        ) from error

    if len(content) > max_bytes:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.FILE_TOO_LARGE,
            path=path,
            message=(
                "The data asset catalog exceeds the configured "
                "maximum size."
            ),
        )

    return content


def _decode_catalog(
    content: bytes,
    *,
    path: Path,
) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.INVALID_ENCODING,
            path=path,
            message=(
                "The data asset catalog must be valid UTF-8."
            ),
        ) from error


def _parse_yaml_document(
    content: str,
    *,
    path: Path,
) -> Mapping[object, object]:
    loader = _UniqueKeySafeLoader(content)

    try:
        document: object = loader.get_single_data()
    except yaml.YAMLError as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.INVALID_YAML,
            path=path,
            message=(
                "The data asset catalog contains invalid YAML."
            ),
        ) from error
    finally:
        cast(_DisposableYamlLoader, loader).dispose()

    if document is None:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.EMPTY_DOCUMENT,
            path=path,
            message=(
                "The data asset catalog document must not be empty."
            ),
        )

    if not isinstance(document, Mapping):
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.INVALID_ROOT,
            path=path,
            message=(
                "The data asset catalog root must be a mapping."
            ),
        )

    return document


def _validate_catalog_document(
    document: Mapping[object, object],
    *,
    path: Path,
) -> AssetCatalog:
    try:
        return AssetCatalog.model_validate(document)
    except ValidationError as error:
        raise CatalogLoadError(
            code=CatalogLoadErrorCode.VALIDATION_FAILED,
            path=path,
            message=(
                "The data asset catalog failed governance validation."
            ),
        ) from error


def load_asset_catalog(
    path: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_CATALOG_BYTES,
) -> LoadedAssetCatalog:
    """
    Load and validate one version-controlled data asset catalog.

    The loader:

    - Requires a YAML file.
    - Enforces a bounded file size.
    - Requires valid UTF-8.
    - Uses a SafeLoader-derived YAML loader.
    - Rejects duplicate YAML mapping keys.
    - Requires a mapping at the document root.
    - Validates the result using AssetCatalog.
    - Returns a SHA-256 digest for reconstruction evidence.
    """

    if max_bytes <= 0:
        raise ValueError("max_bytes must be greater than zero")

    resolved_path = _resolve_catalog_path(path)

    content = _read_catalog_bytes(
        resolved_path,
        max_bytes=max_bytes,
    )

    decoded_content = _decode_catalog(
        content,
        path=resolved_path,
    )

    document = _parse_yaml_document(
        decoded_content,
        path=resolved_path,
    )

    catalog = _validate_catalog_document(
        document,
        path=resolved_path,
    )

    return LoadedAssetCatalog(
        catalog=catalog,
        source_path=resolved_path,
        sha256_hex=sha256(content).hexdigest(),
        byte_size=len(content),
    )
