import asyncio
import subprocess
import sys
import unittest.mock

import httpx
import pytest

from return_platform.configuration.settings import Settings
from return_platform.operations.orchestrator import ReturnOrchestrator
from return_platform.operations.return_support.providers.contracts import ReturnSupportRepository
from return_platform.operations.return_support.providers.external import (
    ExternalReturnSupportProvider,
)
from return_platform.operations.return_support.providers.factory import (
    build_return_support_provider,
)


def test_clean_import_no_db_dependencies() -> None:
    """The provider package must not drag a database driver in on import.

    `sandbox` was in this list until `SandboxReturnSupportProvider` was deleted --
    it had no caller anywhere in `backend/src`, `scripts`, `compose.yaml` or any
    configuration, and no `support_ticket_mode` value could select it. This import
    was the last reference to it in the repository, and it is why "nothing imports
    it" is necessary and not sufficient: a grep over production code would have
    reported the module clean to delete and this test would have broken the suite.
    """
    cmd = [
        sys.executable,
        "-c",
        "import sys\n"
        "from return_platform.operations.return_support.providers import "
        "contracts, factory, external\n"
        "if 'pymssql' in sys.modules or 'pymongo' in sys.modules or 'return_platform.operations.models' in sys.modules:\n"
        "    sys.exit(1)\n",
    ]
    res = subprocess.run(cmd)
    assert res.returncode == 0


@pytest.mark.asyncio
async def test_factory_internal_uses_platform_service() -> None:
    settings = Settings(support_ticket_mode="INTERNAL")
    repo = unittest.mock.AsyncMock(spec=ReturnSupportRepository)
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="platform-owned"):
            build_return_support_provider(settings, repo, client)


@pytest.mark.asyncio
async def test_factory_external() -> None:
    settings = Settings(
        support_ticket_mode="EXTERNAL_AUTHORITY", support_ticket_base_url="http://test"
    )
    repo = unittest.mock.AsyncMock(spec=ReturnSupportRepository)
    async with httpx.AsyncClient() as client:
        provider = build_return_support_provider(settings, repo, client)
        assert isinstance(provider, ExternalReturnSupportProvider)


@pytest.mark.asyncio
async def test_orchestrator_closes_http_client() -> None:
    settings = Settings(support_ticket_mode="INTERNAL")
    repo = unittest.mock.AsyncMock()
    repo.claim_next_return.return_value = None
    temporal = unittest.mock.AsyncMock()
    orchestrator = ReturnOrchestrator(
        repository=repo, temporal=temporal, settings=settings, worker_id="test"
    )

    task = asyncio.create_task(orchestrator.run_forever())
    await asyncio.sleep(0.01)

    assert not orchestrator._http_client.is_closed
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert orchestrator._http_client.is_closed
