import os

import pytest

from return_platform.data_platform.operational_generation.adapter_factory import create_adapter
from return_platform.data_platform.operational_generation.adapters.registry import _ADAPTERS
from return_platform.data_platform.operational_generation.connection_validation import (
    validate_connection,
)
from return_platform.data_platform.operational_generation.write_models import (
    Operation,
    OperationType,
)


def test_unknown_adapter_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown adapter"):
        create_adapter("unknown_fake_adapter")


def test_asset_outside_allowlist_rejected() -> None:
    adapter = create_adapter("source_admin")
    op = Operation(
        operation_id="1",
        type=OperationType.INSERT,
        asset_id="platform.mongodb.some_protected_asset",
        payload={},
        target_channel="source_admin",
        dependencies=(),
    )
    with pytest.raises(ValueError, match="not supported by adapter"):
        validate_connection(adapter, [op])


def test_operation_outside_capability_rejected() -> None:
    adapter = create_adapter("source_admin")
    op = Operation(
        operation_id="1",
        type=OperationType.DOMAIN_COMMAND,
        asset_id="source.mongodb.sales_inv",
        payload={},
        target_channel="source_admin",
        dependencies=(),
    )
    with pytest.raises(ValueError, match="not supported"):
        validate_connection(adapter, [op])


def test_source_writer_cannot_access_platform_database() -> None:
    adapter = create_adapter("source_admin")
    op = Operation(
        operation_id="1",
        type=OperationType.INSERT,
        asset_id="platform.mongodb.operational_returns",
        payload={},
        target_channel="source_admin",
        dependencies=(),
    )
    with pytest.raises(ValueError, match="not supported by adapter"):
        validate_connection(adapter, [op])


def test_platform_adapter_cannot_perform_direct_insert() -> None:
    adapter = create_adapter("domain_api")
    op = Operation(
        operation_id="1",
        type=OperationType.INSERT,
        asset_id="platform.mongodb.support_cases",
        payload={},
        target_channel="domain_api",
        dependencies=(),
    )
    with pytest.raises(ValueError, match="not supported"):
        validate_connection(adapter, [op])


def test_graph_adapter_cannot_execute_cypher() -> None:
    adapter = create_adapter("GRAPH_SYNC_ADAPTER")
    op = Operation(
        operation_id="1",
        type=OperationType.DOMAIN_COMMAND,
        asset_id="source.mongodb.sales_inv",
        payload={"query": "MATCH (n) DETACH DELETE n"},
        target_channel="GRAPH_SYNC_ADAPTER",
        dependencies=(),
    )
    with pytest.raises(ValueError, match="not supported"):
        validate_connection(adapter, [op])


@pytest.mark.asyncio
async def test_missing_credential_profile_rejected() -> None:
    adapter = create_adapter("source_admin")
    original_val = os.environ.get("VAULT_SOURCE_ADMIN_CREDENTIALS")
    if "VAULT_SOURCE_ADMIN_CREDENTIALS" in os.environ:
        del os.environ["VAULT_SOURCE_ADMIN_CREDENTIALS"]

    try:
        assert not await adapter.is_ready()
    finally:
        if original_val is not None:
            os.environ["VAULT_SOURCE_ADMIN_CREDENTIALS"] = original_val


def test_adapter_registry_deterministic() -> None:
    assert "source_admin" in _ADAPTERS
    assert "domain_api" in _ADAPTERS
    assert "direct_operational" in _ADAPTERS
    assert "GRAPH_SYNC_ADAPTER" in _ADAPTERS


@pytest.mark.asyncio
async def test_source_execution_requires_runtime_configuration() -> None:
    adapter = create_adapter("source_admin")
    with pytest.raises(RuntimeError, match="not configured"):
        await adapter.execute([])

    with pytest.raises(RuntimeError, match="not configured"):
        await adapter.compensate([])
