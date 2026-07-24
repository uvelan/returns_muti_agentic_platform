"""Tests for secure data asset catalog loading."""

from hashlib import sha256
from pathlib import Path

import pytest

from return_platform.data_governance.catalog_loader import (
    CatalogLoadError,
    CatalogLoadErrorCode,
    load_asset_catalog,
)


def write_catalog(
    path: Path,
    content: str,
) -> Path:
    """Write one UTF-8 YAML catalog fixture."""

    path.write_text(
        content,
        encoding="utf-8",
    )

    return path


def test_loads_valid_empty_catalog(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets: []
""".lstrip(),
    )

    loaded_catalog = load_asset_catalog(catalog_path)

    expected_bytes = catalog_path.read_bytes()

    assert loaded_catalog.catalog.version == "1.0"
    assert loaded_catalog.catalog.assets == ()
    assert loaded_catalog.asset_count == 0
    assert loaded_catalog.source_path == catalog_path.resolve()
    assert loaded_catalog.byte_size == len(expected_bytes)
    assert loaded_catalog.sha256_hex == sha256(expected_bytes).hexdigest()


def test_loads_valid_catalog_entry(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets:
  - asset_id: "platform.mongodb.return_sessions"
    store: "MONGODB"
    database: "return_platform"
    object_name: "return_sessions"
    object_kind: "COLLECTION"
    ownership: "PLATFORM_OWNED"
    authoritative: true
    allowed_operations:
      - "READ"
      - "WRITE"
    sampling:
      enabled: false
      max_rows: 0
      redact_fields: []
""".lstrip(),
    )

    loaded_catalog = load_asset_catalog(catalog_path)

    assert loaded_catalog.asset_count == 1

    asset = loaded_catalog.catalog.assets[0]

    assert asset.asset_id == ("platform.mongodb.return_sessions")
    assert asset.database == "return_platform"
    assert asset.object_name == "return_sessions"


def test_missing_catalog_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "missing.yaml"

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.PATH_NOT_FOUND)


def test_directory_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.yaml"
    catalog_path.mkdir()

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.PATH_NOT_FILE)


def test_non_yaml_extension_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.json",
        '{"version": "1.0", "assets": []}',
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.UNSUPPORTED_EXTENSION)


def test_empty_document_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        "",
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.EMPTY_DOCUMENT)


def test_invalid_utf8_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "assets.yaml"

    catalog_path.write_bytes(
        b"\xff\xfe\x00\x00",
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.INVALID_ENCODING)


def test_malformed_yaml_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets:
  - asset_id: [
""".lstrip(),
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.INVALID_YAML)


def test_duplicate_yaml_keys_are_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
version: "1.0"
assets: []
""".lstrip(),
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.INVALID_YAML)

    assert isinstance(
        error_info.value.__cause__,
        Exception,
    )


def test_non_mapping_root_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
- version: "1.0"
- assets: []
""".lstrip(),
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.INVALID_ROOT)


def test_unknown_catalog_field_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets: []
unexpected: true
""".lstrip(),
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.VALIDATION_FAILED)


def test_invalid_governance_entry_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets:
  - asset_id: "source.sqlserver.sales_orders"
    store: "SQLSERVER"
    database: "return_platform"
    namespace: "dbo"
    object_name: "sales_orders"
    object_kind: "TABLE"
    ownership: "SOURCE_SYSTEM"
    authoritative: true
    allowed_operations:
      - "READ"
      - "WRITE"
""".lstrip(),
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(catalog_path)

    assert error_info.value.code is (CatalogLoadErrorCode.VALIDATION_FAILED)


def test_file_size_limit_is_enforced(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets: []
""".lstrip(),
    )

    with pytest.raises(CatalogLoadError) as error_info:
        load_asset_catalog(
            catalog_path,
            max_bytes=4,
        )

    assert error_info.value.code is (CatalogLoadErrorCode.FILE_TOO_LARGE)


def test_non_positive_size_limit_is_rejected(
    tmp_path: Path,
) -> None:
    catalog_path = write_catalog(
        tmp_path / "assets.yaml",
        """
version: "1.0"
assets: []
""".lstrip(),
    )

    with pytest.raises(
        ValueError,
        match="max_bytes must be greater than zero",
    ):
        load_asset_catalog(
            catalog_path,
            max_bytes=0,
        )
