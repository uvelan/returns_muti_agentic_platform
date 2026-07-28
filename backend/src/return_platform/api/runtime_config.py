from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse

from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1", tags=["runtime-config"])


@router.get(
    "/runtime-config",
    response_model=APIResponse[dict[str, object]],
    status_code=status.HTTP_200_OK,
)
async def get_runtime_config(request: Request) -> JSONResponse:
    """Retrieve safe runtime configuration for the frontend."""
    settings = request.app.state.settings
    snapshot = getattr(request.app.state, "return_configuration_snapshot", None)

    release_id = getattr(snapshot, "release_id", "unknown") if snapshot else "unknown"

    data = {
        "releaseId": release_id,
        "environment": settings.environment,
        "apiBasePath": "/api/v1",
        "features": {"orderDiscoveryCopilot": True, "aiStudioOperationalGeneration": True},
        "capabilities": {
            "availableSourceTypes": ["MONGODB", "SQLSERVER", "NEO4J"],
            "availableModelProviders": ["GOOGLE", "NVIDIA", "OPENAI", "ANTHROPIC"],
        },
    }

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=APIResponse[dict[str, object]](
            data=data,
            meta=ResponseMeta(request_id=getattr(request.state, "correlation_id", "unknown")),
        ).model_dump(mode="json"),
    )
