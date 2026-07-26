from types import SimpleNamespace
from typing import Any, cast

import pytest

from return_platform.data_platform.ai_studio import AIStudioService


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
