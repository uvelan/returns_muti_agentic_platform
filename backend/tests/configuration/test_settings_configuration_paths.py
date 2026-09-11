"""`return_configuration_path` / `ai_gateway_configuration_path` accept a directory.

CFG-2 moves these two fields out of `validate_catalog_path` (absolute-only,
`.yaml`/`.yml`-suffix-only) into `validate_packaged_configuration_path`, shaped
like `resolve_configuration_directory`: relative resolves against
`REPOSITORY_ROOT`, no suffix rule, no existence check. This is the blocker the
CFG-2 design note names -- without it, every container that mounts a packaged
configuration *directory* fails at settings construction, before any loader
runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from return_platform.configuration.settings import REPOSITORY_ROOT, Settings


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_a_directory_with_no_suffix_is_accepted(tmp_path: Path) -> None:
    directory = tmp_path / "returns"
    settings = _settings(return_configuration_path=directory)
    assert settings.return_configuration_path == directory.resolve()


def test_a_relative_directory_resolves_against_the_repository_root() -> None:
    settings = _settings(return_configuration_path=Path("config/returns"))
    assert settings.return_configuration_path == (REPOSITORY_ROOT / "config/returns").resolve()


def test_a_single_file_path_still_resolves_as_before(tmp_path: Path) -> None:
    """The old single-file shape still works -- this is a widening, not a break."""
    file_path = tmp_path / "production.yaml"
    settings = _settings(return_configuration_path=file_path)
    assert settings.return_configuration_path == file_path.resolve()


def test_no_existence_check_synthetic_fixture_directory_need_not_exist(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "does_not_exist_yet"
    assert not directory.exists()
    settings = _settings(ai_gateway_configuration_path=directory)
    assert settings.ai_gateway_configuration_path == directory.resolve()


def test_the_ai_gateway_path_accepts_a_directory_too(tmp_path: Path) -> None:
    directory = tmp_path / "ai_gateway"
    directory.mkdir()
    (directory / "index.yaml").write_text("schemaVersion: '1.0'\n", encoding="utf-8")
    settings = _settings(ai_gateway_configuration_path=directory)
    assert settings.ai_gateway_configuration_path == directory.resolve()


def test_unrelated_catalog_paths_still_enforce_the_old_absolute_yaml_rule(
    tmp_path: Path,
) -> None:
    """`catalog_path` was not part of the CFG-2 blocker -- its rule is unchanged."""
    with pytest.raises(ValidationError, match="must reference YAML files"):
        _settings(catalog_path=tmp_path / "not_yaml")


def test_unrelated_catalog_paths_still_refuse_a_relative_value(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="must be absolute"):
        _settings(catalog_path=Path("relative/catalog.yaml"))
