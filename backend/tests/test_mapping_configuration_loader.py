"""Tests for bounded multi-file data-platform configuration loading."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Protocol, cast

import pytest

import return_platform.data_platform.mapping.loader as loader_module
from return_platform.data_platform.mapping import (
    CONFIGURATION_DIGEST_ALGORITHM,
    CONFIGURATION_FILE_NAMES,
    LoadedDataPlatformConfiguration,
    MappingConfigurationErrorCode,
    MappingConfigurationLoadError,
    load_data_platform_mapping_configuration,
)

_CONFIG_SOURCE = Path(__file__).parents[1] / "config" / "data_platform"
_SHA256_HEX_LENGTH = hashlib.sha256().digest_size * 2


class _MutableLoadedConfiguration(Protocol):
    configuration_digest: str


def _copy_valid_configuration(tmp_path: Path) -> Path:
    target = tmp_path / "data_platform"
    shutil.copytree(_CONFIG_SOURCE, target)
    return target


def _assert_load_error(
    directory: Path,
    expected_code: MappingConfigurationErrorCode,
) -> MappingConfigurationLoadError:
    with pytest.raises(MappingConfigurationLoadError) as exc_info:
        load_data_platform_mapping_configuration(directory)
    assert exc_info.value.code is expected_code
    assert str(exc_info.value) == exc_info.value.safe_message
    return exc_info.value


def _calculate_expected_digest(directory: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"return-platform:data-platform-configuration:v1\x00")
    for filename in CONFIGURATION_FILE_NAMES:
        content = (directory / filename).read_bytes()
        encoded_filename = filename.encode("utf-8")
        digest.update(len(encoded_filename).to_bytes(4, "big"))
        digest.update(encoded_filename)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def test_loads_valid_configuration_with_deterministic_evidence(
    tmp_path: Path,
) -> None:
    """Verify loads valid configuration with deterministic evidence."""
    directory = _copy_valid_configuration(tmp_path)

    loaded = load_data_platform_mapping_configuration(directory)

    assert loaded.bundle.schema_version == "1.0"
    assert loaded.resolved_directory == directory.resolve()
    assert tuple(file.filename for file in loaded.files) == CONFIGURATION_FILE_NAMES
    assert loaded.total_byte_size == sum(file.byte_size for file in loaded.files)
    assert loaded.configuration_digest == _calculate_expected_digest(directory)
    assert len(loaded.configuration_digest) == _SHA256_HEX_LENGTH
    assert CONFIGURATION_DIGEST_ALGORITHM == "sha256"
    assert all(len(file.sha256_digest) == _SHA256_HEX_LENGTH for file in loaded.files)
    assert all(file.resolved_path.is_absolute() for file in loaded.files)

    assert tuple(source.source_id for source in loaded.bundle.source_assets) == (
        "source.customer_cdm.v1",
    )
    assert tuple(mapping.mapping_id for mapping in loaded.bundle.canonical_mappings) == (
        "canonical.customer.v1",
        "canonical.customer_account.v1",
    )


def test_loaded_result_is_immutable(tmp_path: Path) -> None:
    """Verify loaded result is immutable."""
    loaded = load_data_platform_mapping_configuration(_copy_valid_configuration(tmp_path))
    mutable_loaded = cast("_MutableLoadedConfiguration", loaded)

    with pytest.raises(AttributeError):
        mutable_loaded.configuration_digest = "0" * 64


def test_repeated_loads_produce_identical_evidence(tmp_path: Path) -> None:
    """Verify repeated loads produce identical evidence."""
    directory = _copy_valid_configuration(tmp_path)

    first = load_data_platform_mapping_configuration(directory)
    second = load_data_platform_mapping_configuration(directory)

    assert first == second


def test_exact_byte_change_changes_configuration_digest(tmp_path: Path) -> None:
    """Verify exact byte change changes configuration digest."""
    directory = _copy_valid_configuration(tmp_path)
    before = load_data_platform_mapping_configuration(directory)
    sources_path = directory / "sources.yaml"
    sources_path.write_text(
        sources_path.read_text(encoding="utf-8") + "\n# evidence-only change\n",
        encoding="utf-8",
    )

    after = load_data_platform_mapping_configuration(directory)

    assert after.bundle == before.bundle
    assert after.configuration_digest != before.configuration_digest


def test_utf8_bom_is_accepted_and_bound_to_digest(tmp_path: Path) -> None:
    """Verify utf8 bom is accepted and bound to digest."""
    directory = _copy_valid_configuration(tmp_path)
    sources_path = directory / "sources.yaml"
    original = sources_path.read_bytes()
    sources_path.write_bytes(b"\xef\xbb\xbf" + original)

    loaded = load_data_platform_mapping_configuration(directory)

    assert loaded.bundle.schema_version == "1.0"
    assert loaded.configuration_digest == _calculate_expected_digest(directory)


def test_missing_directory_fails_closed(tmp_path: Path) -> None:
    """Verify missing directory fails closed."""
    _assert_load_error(
        tmp_path / "missing",
        MappingConfigurationErrorCode.DIRECTORY_NOT_FOUND,
    )


def test_non_directory_path_fails_closed(tmp_path: Path) -> None:
    """Verify non directory path fails closed."""
    path = tmp_path / "not-a-directory"
    path.write_text("data", encoding="utf-8")

    _assert_load_error(
        path,
        MappingConfigurationErrorCode.DIRECTORY_NOT_DIRECTORY,
    )


def test_non_path_argument_is_rejected(tmp_path: Path) -> None:
    """Verify non path argument is rejected."""
    with pytest.raises(TypeError, match=r"pathlib\.Path"):
        load_data_platform_mapping_configuration(cast("Path", str(tmp_path)))


@pytest.mark.parametrize("filename", CONFIGURATION_FILE_NAMES)
def test_each_required_file_is_mandatory(tmp_path: Path, filename: str) -> None:
    """Verify each required file is mandatory."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / filename).unlink()

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.FILE_NOT_FOUND,
    )


def test_symbolic_link_configuration_file_is_rejected(tmp_path: Path) -> None:
    """Verify symbolic link configuration file is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    source_path = directory / "sources.yaml"
    external = tmp_path / "external.yaml"
    external.write_bytes(source_path.read_bytes())
    source_path.unlink()
    try:
        source_path.symlink_to(external)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.FILE_SYMLINK,
    )


def test_non_regular_configuration_path_is_rejected(tmp_path: Path) -> None:
    """Verify non regular configuration path is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    source_path = directory / "sources.yaml"
    source_path.unlink()
    source_path.mkdir()

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.FILE_NOT_REGULAR,
    )


def test_file_size_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify file size limit is enforced."""
    directory = _copy_valid_configuration(tmp_path)
    monkeypatch.setattr(loader_module, "MAX_CONFIGURATION_FILE_BYTES", 16)

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.FILE_TOO_LARGE,
    )


def test_total_size_limit_is_enforced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify total size limit is enforced."""
    directory = _copy_valid_configuration(tmp_path)
    monkeypatch.setattr(loader_module, "MAX_CONFIGURATION_TOTAL_BYTES", 1)

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.TOTAL_SIZE_EXCEEDED,
    )


def test_invalid_utf8_is_rejected(tmp_path: Path) -> None:
    """Verify invalid utf8 is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_bytes(b"\xff\xfe\x00")

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_ENCODING,
    )


def test_empty_document_is_rejected(tmp_path: Path) -> None:
    """Verify empty document is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_text("# only a comment\n", encoding="utf-8")

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.EMPTY_DOCUMENT,
    )


def test_non_mapping_root_is_rejected(tmp_path: Path) -> None:
    """Verify non mapping root is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_text("- invalid\n", encoding="utf-8")

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_ROOT,
    )


def test_non_string_root_key_is_rejected(tmp_path: Path) -> None:
    """Verify non string root key is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_text(
        'schema_version: "1.0"\n1: []\n',
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_ROOT_KEYS,
    )


def test_duplicate_yaml_key_is_rejected_at_any_depth(tmp_path: Path) -> None:
    """Verify duplicate yaml key is rejected at any depth."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_text(
        """schema_version: "1.0"
source_assets:
  - source_id: source.customer_cdm.v1
    source_id: source.duplicate.v1
    catalog_asset_id: source.mongodb.customer_outbound_cdm
    source_system: CUSTOMER_CDM
    lifecycle: ACTIVE
    required_for_sync: true
""",
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.DUPLICATE_KEY,
    )


def test_yaml_alias_is_rejected(tmp_path: Path) -> None:
    """Verify yaml alias is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_text(
        """schema_version: "1.0"
source_assets:
  - &customer_source
    source_id: source.customer_cdm.v1
    catalog_asset_id: source.mongodb.customer_outbound_cdm
    source_system: CUSTOMER_CDM
    lifecycle: ACTIVE
    required_for_sync: true
  - *customer_source
""",
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.YAML_ALIAS_NOT_ALLOWED,
    )


def test_malformed_yaml_is_rejected_without_content_leak(tmp_path: Path) -> None:
    """Verify malformed yaml is rejected without content leak."""
    directory = _copy_valid_configuration(tmp_path)
    sensitive_value = "do-not-leak-this-value"
    (directory / "sources.yaml").write_text(
        f'schema_version: "1.0"\nsource_assets: [{{secret: {sensitive_value}}}\n',
        encoding="utf-8",
    )

    error = _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_YAML,
    )

    assert sensitive_value not in str(error)


def test_missing_or_extra_root_keys_are_rejected(tmp_path: Path) -> None:
    """Verify missing or extra root keys are rejected."""
    directory = _copy_valid_configuration(tmp_path)
    (directory / "sources.yaml").write_text(
        'schema_version: "1.0"\nsource_assets: []\nunexpected: true\n',
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_ROOT_KEYS,
    )


def test_graph_file_requires_relationship_root_even_when_empty(
    tmp_path: Path,
) -> None:
    """Verify graph file requires relationship root even when empty."""
    directory = _copy_valid_configuration(tmp_path)
    graph_path = directory / "graph_projection.yaml"
    graph_path.write_text(
        'schema_version: "1.0"\ngraph_nodes: []\n',
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_ROOT_KEYS,
    )


@pytest.mark.parametrize("invalid_version", [1.0, "v 1", "", True])
def test_invalid_schema_version_is_rejected(
    tmp_path: Path,
    invalid_version: object,
) -> None:
    """Verify invalid schema version is rejected."""
    directory = _copy_valid_configuration(tmp_path)
    source_path = directory / "sources.yaml"
    original = source_path.read_text(encoding="utf-8")
    rendered = (
        repr(invalid_version).lower() if isinstance(invalid_version, bool) else str(invalid_version)
    )
    source_path.write_text(
        original.replace('schema_version: "1.0"', f"schema_version: {rendered}"),
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.INVALID_SCHEMA_VERSION,
    )


def test_schema_versions_must_match_across_files(tmp_path: Path) -> None:
    """Verify schema versions must match across files."""
    directory = _copy_valid_configuration(tmp_path)
    pipeline_path = directory / "sync_pipelines.yaml"
    pipeline_path.write_text(
        pipeline_path.read_text(encoding="utf-8").replace(
            'schema_version: "1.0"',
            'schema_version: "1.1"',
            1,
        ),
        encoding="utf-8",
    )

    _assert_load_error(
        directory,
        MappingConfigurationErrorCode.SCHEMA_VERSION_MISMATCH,
    )


def test_invalid_merged_bundle_is_reported_without_pydantic_details(
    tmp_path: Path,
) -> None:
    """Verify invalid merged bundle is reported without pydantic details."""
    directory = _copy_valid_configuration(tmp_path)
    canonical_path = directory / "canonical_mappings.yaml"
    sensitive_value = "sensitive-source-id"
    canonical_path.write_text(
        canonical_path.read_text(encoding="utf-8").replace(
            "source.customer_cdm.v1",
            sensitive_value,
            1,
        ),
        encoding="utf-8",
    )

    error = _assert_load_error(
        directory,
        MappingConfigurationErrorCode.VALIDATION_FAILED,
    )

    assert sensitive_value not in str(error)


def test_loader_does_not_modify_input_files(tmp_path: Path) -> None:
    """Verify loader does not modify input files."""
    directory = _copy_valid_configuration(tmp_path)
    before = {
        filename: (directory / filename).read_bytes() for filename in CONFIGURATION_FILE_NAMES
    }

    loaded = load_data_platform_mapping_configuration(directory)

    after = {filename: (directory / filename).read_bytes() for filename in CONFIGURATION_FILE_NAMES}
    assert after == before
    assert isinstance(loaded, LoadedDataPlatformConfiguration)
