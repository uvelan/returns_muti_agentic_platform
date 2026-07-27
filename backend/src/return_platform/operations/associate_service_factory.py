"""Build the shared production Associate Conversation service for API surfaces."""

from __future__ import annotations

from fastapi import HTTPException, Request

from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.configuration.snapshot import PinnedConfigurationSnapshot
from return_platform.operations.associate_flow import AssociateConversationService
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import RuntimeResources


def build_associate_conversation_service(request: Request) -> AssociateConversationService:
    """Return the production service or fail when required dependencies are unavailable."""

    resources = getattr(request.app.state, "resources", None)
    if (
        not isinstance(resources, RuntimeResources)
        or resources.mongo is None
        or resources.source_mongo is None
        or resources.neo4j is None
    ):
        raise HTTPException(
            status_code=503,
            detail="Associate discovery dependencies are unavailable.",
        )
    repository = OperationalRepository(
        resources.mongo,
        resources.settings,
        resources.source_mongo,
    )
    loaded = getattr(request.app.state, "return_configuration", None)
    snapshot = getattr(request.app.state, "return_configuration_snapshot", None)
    return AssociateConversationService(
        platform_client=resources.mongo,
        source_client=resources.source_mongo,
        graph=resources.neo4j,
        settings=resources.settings,
        repository=repository,
        return_configuration=(
            loaded.configuration if isinstance(loaded, LoadedReturnConfiguration) else None
        ),
        configuration_release_id=(
            snapshot.release_id if isinstance(snapshot, PinnedConfigurationSnapshot) else None
        ),
        configuration_checksum=(
            snapshot.checksum_sha256 if isinstance(snapshot, PinnedConfigurationSnapshot) else None
        ),
        configuration_source=(
            snapshot.source
            if isinstance(snapshot, PinnedConfigurationSnapshot)
            else "VERSION_CONTROLLED_BASELINE"
        ),
    )
