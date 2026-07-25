"""Return Support provider factory."""

import httpx

from return_platform.configuration.settings import Settings
from return_platform.operations.return_support.providers.contracts import (
    ReturnSupportProvider,
    ReturnSupportRepository,
)
from return_platform.operations.return_support.providers.external import (
    ExternalReturnSupportProvider,
)
from return_platform.operations.return_support.providers.sandbox import SandboxReturnSupportProvider


def build_return_support_provider(
    settings: Settings,
    repository: ReturnSupportRepository,
    http_client: httpx.AsyncClient,
) -> ReturnSupportProvider:
    if settings.support_ticket_mode == "SANDBOX_AUTO":
        return SandboxReturnSupportProvider(repository)
    return ExternalReturnSupportProvider(
        repository=repository, http_client=http_client, settings=settings
    )
