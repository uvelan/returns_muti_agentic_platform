from types import SimpleNamespace
from typing import Any, cast

import pytest

from return_platform.data_platform.ai_studio import (
    AIStudioService,
    _bulk_order_context,
    _parse_customer_order_prompt,
)


def test_customer_order_prompt_builds_relationship_aware_plan() -> None:
    assert _parse_customer_order_prompt(
        "Create 500 customers and 100 orders for each customer"
    ) == (500, 100)
    assert _parse_customer_order_prompt("create 500 customes and 100 orders for each cusotmer") == (
        500,
        100,
    )


def test_bulk_order_context_keeps_customer_identity_across_orders() -> None:
    first = _bulk_order_context(7, 0, seed=20260727)
    second = _bulk_order_context(7, 99, seed=20260727)
    assert first.customer_reference == second.customer_reference
    assert first.customer_name == second.customer_name
    assert first.order_reference != second.order_reference
    assert first.product_reference != second.product_reference

    same_product_for_another_customer = _bulk_order_context(8, 99, seed=20260727)
    assert second.product_reference == same_product_for_another_customer.product_reference
    assert second.sku == same_product_for_another_customer.sku
    assert second.warehouse_reference in {
        "WH-CHENNAI-01",
        "WH-ATLANTA-01",
        "WH-DALLAS-01",
    }
    assert second.bay_reference.startswith(
        f"BAY-{second.warehouse_reference.removeprefix('WH-').removesuffix('-01')}-"
    )


@pytest.mark.parametrize(
    "prompt",
    (
        "Create customers and orders",
        "Create 501 customers and 1 order for each customer",
        "Create 1 customer and 101 orders for each customer",
    ),
)
def test_customer_order_prompt_rejects_unsafe_or_ambiguous_scale(prompt: str) -> None:
    with pytest.raises(ValueError):
        _parse_customer_order_prompt(prompt)


@pytest.mark.asyncio
async def test_mongo_apply_is_disabled_without_dedicated_sandbox_database() -> None:
    service = cast(Any, object.__new__(AIStudioService))
    service._settings = SimpleNamespace(
        ai_studio_mongo_database=None,
        mongo_database="return_platform",
        source_mongo_database="return_source",
    )

    with pytest.raises(PermissionError, match="dedicated sandbox database"):
        await service._apply_mongodb(SimpleNamespace(), [])


@pytest.mark.asyncio
async def test_mongo_apply_rejects_operational_or_source_database() -> None:
    service = cast(Any, object.__new__(AIStudioService))
    service._settings = SimpleNamespace(
        ai_studio_mongo_database="return_source",
        mongo_database="return_platform",
        source_mongo_database="return_source",
    )

    with pytest.raises(PermissionError, match="operational or source"):
        await service._apply_mongodb(SimpleNamespace(), [])


@pytest.mark.asyncio
async def test_sql_apply_is_disabled_without_dedicated_connection() -> None:
    service = cast(Any, object.__new__(AIStudioService))
    service._settings = SimpleNamespace(
        ai_studio_sqlserver_host=None,
        ai_studio_sqlserver_user=None,
        ai_studio_sqlserver_password=None,
        ai_studio_sqlserver_database=None,
    )

    with pytest.raises(PermissionError, match="dedicated sandbox connection"):
        await service._apply_sql(SimpleNamespace(), [])


@pytest.mark.asyncio
async def test_sql_apply_rejects_reused_operational_boundary() -> None:
    service = cast(Any, object.__new__(AIStudioService))
    service._settings = SimpleNamespace(
        ai_studio_sqlserver_host="sqlserver",
        ai_studio_sqlserver_user="sandbox-user",
        ai_studio_sqlserver_password=SimpleNamespace(get_secret_value=lambda: "secret"),
        ai_studio_sqlserver_database="ai_studio_sandbox",
        sqlserver_host="sqlserver",
        sqlserver_user="platform-user",
        sqlserver_database="return_platform",
    )

    with pytest.raises(PermissionError, match="separate host, credential, and database"):
        await service._apply_sql(SimpleNamespace(), [])
