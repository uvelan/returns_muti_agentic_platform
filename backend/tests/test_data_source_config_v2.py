from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from return_platform.api.data_source_config_v2 import (
    DataSourceWrite,
    _builtins,
    _generated_vault_reference,
    _slug,
    router,
)
from return_platform.resources import RuntimeResources


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        source_mongo_database="source",
        mongo_database="platform",
        sqlserver_host="sql.internal",
        sqlserver_port=1433,
        sqlserver_database="returns",
        sqlserver_user="reader",
        neo4j_uri="neo4j://graph.internal:7687",
        neo4j_database="neo4j",
        neo4j_user="neo4j",
    )


def test_v2_router_exposes_full_datasource_flow() -> None:
    routes = {
        (route.path, frozenset(route.methods or set()))
        for item in router.routes
        if isinstance(item, APIRoute)
        for route in (item,)
    }

    assert ("/api/v2/config/data-sources", frozenset({"GET"})) in routes
    assert ("/api/v2/config/data-sources", frozenset({"POST"})) in routes
    assert ("/api/v2/config/data-sources/{source_id}", frozenset({"PUT"})) in routes
    assert ("/api/v2/config/data-sources/{source_id}", frozenset({"DELETE"})) in routes
    assert (
        "/api/v2/config/data-sources/{source_id}/validate",
        frozenset({"POST"}),
    ) in routes
    assert (
        "/api/v2/config/data-sources/{source_id}/schema",
        frozenset({"GET"}),
    ) in routes
    assert (
        "/api/v2/config/data-sources/{source_id}/data",
        frozenset({"GET"}),
    ) in routes


def test_managed_sources_have_valid_connection_shapes() -> None:
    resources = cast(RuntimeResources, SimpleNamespace(settings=_settings()))
    sources = _builtins(resources)

    assert set(sources) == {"source-mongodb", "platform-mongodb", "sqlserver", "neo4j"}
    assert sources["source-mongodb"].accessMode == "READ_ONLY"
    assert sources["sqlserver"].credentialKind == "PASSWORD"
    assert sources["neo4j"].uri == "neo4j://graph.internal:7687"


def test_mongodb_requires_dsn_credential_kind() -> None:
    with pytest.raises(ValidationError, match="credentialKind=DSN"):
        DataSourceWrite(
            name="Orders",
            sourceType="MONGODB",
            database="orders",
            credentialVaultReference="vault://secret/production/data-sources/orders#dsn",
            credentialKind="PASSWORD",
        )


def test_vault_reference_is_restricted_to_datasource_prefix() -> None:
    with pytest.raises(ValidationError, match="production data-sources Vault path"):
        DataSourceWrite(
            name="Orders",
            sourceType="MONGODB",
            database="orders",
            credentialVaultReference="vault://secret/production/ai/orders#dsn",
            credentialKind="DSN",
        )


def test_generated_source_id_is_url_safe() -> None:
    source_id = _slug("Warehouse Returns / India")

    assert source_id.startswith("warehouse-returns-india-")
    assert source_id.replace("-", "").isalnum()


def test_raw_credential_is_excluded_from_persisted_payload() -> None:
    source = DataSourceWrite(
        name="Orders",
        sourceType="MONGODB",
        database="orders",
        credentialVaultReference="vault://secret/production/data-sources/orders#dsn",
        credentialKind="DSN",
        credential="mongodb://user:password@mongo:27017/orders",
    )

    assert "credential" not in source.model_dump(mode="python")
    assert (
        _generated_vault_reference("orders-primary", "DSN")
        == "vault://secret/production/data-sources/orders-primary#dsn"
    )
