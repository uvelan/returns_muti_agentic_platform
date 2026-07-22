import asyncio
import logging
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Request

from return_platform.data_console.infrastructure.probes import (
    probe_mongodb,
    probe_neo4j,
    probe_sqlserver,
    probe_temporal,
    probe_valkey,
)
from return_platform.shared.contracts import (
    APIResponse,
    DependencyErrorCode,
    DependencyProbeResult,
    DependencyStatus,
    ResponseMeta,
    WarningMeta,
)

logger = logging.getLogger("return_platform.api.router")


router = APIRouter(
    prefix="/data-console/v1",
    tags=["Infrastructure"],
)


_DEPENDENCY_KEYS = (
    "mongodb",
    "neo4j",
    "sqlserver",
    "temporal",
    "valkey",
)


def _unexpected_probe_result(
    dependency_name: str,
    error: BaseException,
) -> DependencyProbeResult:
    logger.error(
        "probe_escaped_exception",
        extra={
            "dependency": dependency_name,
            "error_type": type(error).__name__,
        },
        exc_info=error,
    )

    return DependencyProbeResult(
        status=DependencyStatus.UNAVAILABLE,
        latency_ms=None,
        checked_at=datetime.now(UTC),
        error_code=DependencyErrorCode.UNKNOWN_ERROR,
        safe_message="Probe failed unexpectedly.",
    )


def _warning_for_result(
    dependency_name: str,
    result: DependencyProbeResult,
) -> WarningMeta:
    error_code = result.error_code

    return WarningMeta(
        source=dependency_name.upper(),
        code=(
            error_code.value
            if error_code is not None
            else DependencyErrorCode.UNKNOWN_ERROR.value
        ),
        message=(
            result.safe_message
            or "Dependency is degraded or unavailable."
        ),
    )


@router.get(
    "/overview",
    response_model=APIResponse[dict[str, DependencyProbeResult]],
)
async def get_infrastructure_overview(
    request: Request,
) -> APIResponse[dict[str, DependencyProbeResult]]:
    """Return health and latency details for core infrastructure."""
    gathered_results = await asyncio.gather(
        probe_mongodb(request),
        probe_neo4j(request),
        probe_sqlserver(request),
        probe_temporal(request),
        probe_valkey(request),
        return_exceptions=True,
    )

    data: dict[str, DependencyProbeResult] = {}
    warnings: list[WarningMeta] = []

    for dependency_name, gathered_result in zip(
        _DEPENDENCY_KEYS,
        gathered_results,
        strict=True,
    ):
        if isinstance(gathered_result, asyncio.CancelledError):
            raise gathered_result

        if isinstance(gathered_result, BaseException):
            probe_result = _unexpected_probe_result(
                dependency_name,
                gathered_result,
            )
        else:
            probe_result = gathered_result

        data[dependency_name] = probe_result

        if probe_result.status is not DependencyStatus.HEALTHY:
            warnings.append(
                _warning_for_result(
                    dependency_name,
                    probe_result,
                )
            )

    correlation_id = cast(
        str,
        getattr(
            request.state,
            "correlation_id",
            "unknown",
        ),
    )

    return APIResponse[dict[str, DependencyProbeResult]](
        data=data,
        meta=ResponseMeta(
            request_id=correlation_id,
            partial=bool(warnings),
            warnings=tuple(warnings),
        ),
    )
