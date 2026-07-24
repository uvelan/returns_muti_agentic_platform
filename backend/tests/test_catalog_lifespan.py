"""Tests for governance catalog registration during application lifespan."""

from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from return_platform.configuration.settings import Settings
from return_platform.data_governance import (
    CatalogLoadError,
)
from return_platform.main import create_app
from return_platform.resources import RuntimeResources


def test_valid_catalog_is_registered_in_runtime_resources(
    test_settings: Settings,
    empty_catalog_path: Path,
    isolated_lifespan_dependencies: None,
) -> None:
    settings = test_settings.model_copy(
        update={
            "catalog_path": empty_catalog_path,
        }
    )

    app = create_app(
        custom_settings=settings,
    )

    with TestClient(app):
        resources = cast(
            RuntimeResources,
            app.state.resources,
        )

        assert resources.settings is settings
        assert resources.catalog.asset_count == 0
        assert resources.catalog.catalog.version == "1.0"
        assert resources.catalog.source_path == empty_catalog_path


def test_missing_catalog_prevents_startup(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    settings = test_settings.model_copy(
        update={
            "catalog_path": (tmp_path / "missing.yaml").resolve(),
        }
    )

    app = create_app(
        custom_settings=settings,
    )

    with pytest.raises(
        CatalogLoadError,
        match="catalog file does not exist",
    ):
        with TestClient(app):
            pytest.fail("Application startup unexpectedly succeeded.")


def test_invalid_catalog_prevents_startup(
    test_settings: Settings,
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "invalid.yaml"

    catalog_path.write_text(
        """
version: "1.0"
assets:
  invalid: true
""".lstrip(),
        encoding="utf-8",
    )

    settings = test_settings.model_copy(
        update={
            "catalog_path": catalog_path.resolve(),
        }
    )

    app = create_app(
        custom_settings=settings,
    )

    with pytest.raises(
        CatalogLoadError,
        match="failed governance validation",
    ):
        with TestClient(app):
            pytest.fail("Application startup unexpectedly succeeded.")
