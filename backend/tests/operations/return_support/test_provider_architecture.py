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
from return_platform.operations.return_support.providers.sandbox import SandboxReturnSupportProvider


def test_clean_import_no_db_dependencies():
    cmd = [
        sys.executable,
        "-c",
        "import sys\n"
        "from return_platform.operations.return_support.providers import contracts, factory, sandbox, external\n"  # noqa: E501
        "if 'pymssql' in sys.modules or 'pymongo' in sys.modules or 'return_platform.operations.models' in sys.modules:\n"  # noqa: E501
        "    sys.exit(1)\n",
    ]
    res = subprocess.run(cmd)
    assert res.returncode == 0


def test_factory_sandbox_auto():
    settings = Settings(support_ticket_mode="SANDBOX_AUTO")
    repo = unittest.mock.AsyncMock(spec=ReturnSupportRepository)
    client = httpx.AsyncClient()
    provider = build_return_support_provider(settings, repo, client)
    assert isinstance(provider, SandboxReturnSupportProvider)


def test_factory_external():
    settings = Settings(support_ticket_mode="EXTERNAL", support_ticket_base_url="http://test")
    repo = unittest.mock.AsyncMock(spec=ReturnSupportRepository)
    client = httpx.AsyncClient()
    provider = build_return_support_provider(settings, repo, client)
    assert isinstance(provider, ExternalReturnSupportProvider)


@pytest.mark.asyncio
async def test_orchestrator_closes_http_client():
    settings = Settings(support_ticket_mode="SANDBOX_AUTO")
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
