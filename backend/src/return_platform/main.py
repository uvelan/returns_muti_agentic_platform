import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

import redis.asyncio as redis
from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j import AsyncGraphDatabase
from pymongo import AsyncMongoClient
from temporalio.client import Client

from return_platform.api.ai_gateway import router as ai_gateway_router
from return_platform.api.dependencies import router as dependencies_router
from return_platform.api.returns import router as returns_router
from return_platform.api.seed import router as seed_router
from return_platform.api.support import router as support_router
from return_platform.configuration.settings import Settings
from return_platform.data_console.api.audit import router as audit_router
from return_platform.data_console.api.browser import router as browser_router
from return_platform.data_console.api.graph import router as graph_router
from return_platform.data_console.api.graph_evidence import router as graph_evidence_router
from return_platform.data_console.api.inventory import router as inventory_router
from return_platform.data_console.api.jobs import router as jobs_router
from return_platform.data_console.api.router import router as console_router
from return_platform.data_console.api.scenarios import router as scenarios_router
from return_platform.data_console.api.sources import router as sources_router
from return_platform.data_console.api.workspaces import router as workspaces_router
from return_platform.data_console.infrastructure.probes import (
    probe_mongodb,
    probe_neo4j,
    probe_source_mongodb,
    probe_sqlserver,
    probe_temporal,
    probe_valkey,
)
from return_platform.data_governance import load_asset_catalog
from return_platform.operations.repository import OperationalRepository
from return_platform.resources import (
    AsyncValkeyClient,
    RuntimeResources,
    close_resources,
)
from return_platform.security.principal import (
    AuthorizationError,
    PrincipalProvider,
    get_development_principal,
    validate_correlation_id,
)
from return_platform.shared.contracts import (
    APIResponse,
    DependencyStatus,
    ResponseMeta,
    WarningMeta,
)

logger = logging.getLogger("return_platform.main")


_HEALTH_PATHS = frozenset(
    {
        "/health/live",
        "/health/ready",
    }
)

_DEVELOPMENT_ENVIRONMENTS = frozenset(
    {
        "development",
        "test",
    }
)


def _get_settings(
    app: FastAPI,
) -> Settings:
    """Return the validated application settings."""

    return cast(Settings, app.state.settings)


def _log_initialization_failure(
    dependency: str,
    exc: Exception,
) -> None:
    """Record a dependency initialization failure without exposing secrets."""

    logger.exception(
        "dependency_initialization_failed",
        extra={
            "dependency": dependency,
            "error_type": type(exc).__name__,
        },
    )


def _create_error_response(
    *,
    status_code: int,
    correlation_id: str,
    source: str,
    code: str,
    message: str,
) -> JSONResponse:
    """Create the standard API error envelope."""

    response = JSONResponse(
        status_code=status_code,
        content=APIResponse[None](
            data=None,
            meta=ResponseMeta(
                request_id=correlation_id,
                partial=True,
                warnings=(
                    WarningMeta(
                        source=source,
                        code=code,
                        message=message,
                    ),
                ),
            ),
        ).model_dump(mode="json"),
    )

    response.headers["X-Correlation-ID"] = correlation_id

    return response


def _initialize_mongodb(
    settings: Settings,
    resources: RuntimeResources,
) -> None:
    """Create the asynchronous MongoDB client."""

    try:
        mongo_client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            settings.mongo_dsn.get_secret_value()
        )

        resources.mongo = mongo_client
        source_dsn = (
            settings.source_mongo_dsn.get_secret_value()
            if settings.source_mongo_dsn is not None
            else settings.mongo_dsn.get_secret_value()
        )
        resources.source_mongo = (
            mongo_client
            if source_dsn == settings.mongo_dsn.get_secret_value()
            else AsyncMongoClient[dict[str, object]](source_dsn)
        )
    except Exception as exc:
        _log_initialization_failure(
            "mongodb",
            exc,
        )


def _initialize_neo4j(
    settings: Settings,
    resources: RuntimeResources,
) -> None:
    """Create the asynchronous Neo4j driver."""

    try:
        resources.neo4j = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(
                settings.neo4j_user,
                settings.neo4j_password.get_secret_value(),
            ),
        )
    except Exception as exc:
        _log_initialization_failure(
            "neo4j",
            exc,
        )


def _initialize_valkey(
    settings: Settings,
    resources: RuntimeResources,
) -> None:
    """Create the asynchronous Valkey client."""

    try:
        valkey_client: redis.Redis = redis.Redis(
            host=settings.valkey_host,
            port=settings.valkey_port,
            password=(settings.valkey_password.get_secret_value()),
            socket_connect_timeout=(settings.probe_timeout_seconds),
            socket_timeout=settings.probe_timeout_seconds,
            decode_responses=True,
        )

        resources.valkey = cast(
            AsyncValkeyClient,
            valkey_client,
        )
    except Exception as exc:
        _log_initialization_failure(
            "valkey",
            exc,
        )


async def _initialize_temporal(
    settings: Settings,
    resources: RuntimeResources,
) -> None:
    """Connect to Temporal within the configured timeout."""

    try:
        async with asyncio.timeout(settings.dependency_connect_timeout_seconds):
            resources.temporal = await Client.connect(settings.temporal_target)
    except Exception as exc:
        _log_initialization_failure(
            "temporal",
            exc,
        )


@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # noqa: UP043
    """
    Create and close application-wide dependency resources.

    The governance catalog is loaded before any external dependency client
    is created. A missing, malformed, oversized, or governance-invalid
    catalog therefore prevents the application from starting.
    """

    settings = _get_settings(app)

    loaded_catalog = load_asset_catalog(settings.catalog_path)

    resources = RuntimeResources(
        settings=settings,
        catalog=loaded_catalog,
    )

    try:
        _initialize_mongodb(
            settings,
            resources,
        )

        _initialize_neo4j(
            settings,
            resources,
        )

        _initialize_valkey(
            settings,
            resources,
        )

        await _initialize_temporal(
            settings,
            resources,
        )

        app.state.resources = resources
        if resources.mongo is not None:
            await OperationalRepository(resources.mongo, settings).ensure_indexes()

        logger.info(
            "application_resources_initialized",
            extra={
                "catalog_version": (loaded_catalog.catalog.version),
                "catalog_asset_count": (loaded_catalog.asset_count),
                "catalog_sha256": (loaded_catalog.sha256_hex),
            },
        )

        yield
    finally:
        if (
            getattr(
                app.state,
                "resources",
                None,
            )
            is resources
        ):
            del app.state.resources

        await close_resources(resources)


def _resolve_principal_provider(
    settings: Settings,
    principal_provider: PrincipalProvider | None,
) -> PrincipalProvider:
    """Resolve the configured authentication principal provider."""

    if principal_provider is not None:
        return principal_provider

    if settings.environment in _DEVELOPMENT_ENVIRONMENTS:
        return get_development_principal

    raise RuntimeError("A production principal provider is required.")


def create_app(
    custom_settings: Settings | None = None,
    principal_provider: PrincipalProvider | None = None,
) -> FastAPI:
    """Construct and configure the Return Platform API."""

    app_settings = custom_settings or Settings()

    provider = _resolve_principal_provider(
        app_settings,
        principal_provider,
    )

    fastapi_app = FastAPI(
        title="Return Platform API",
        lifespan=lifespan,
    )

    fastapi_app.state.settings = app_settings

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(app_settings.frontend_cors_origin).rstrip("/")],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    @fastapi_app.middleware("http")
    async def correlation_middleware(
        request: Request,
        call_next: Callable[
            [Request],
            Awaitable[Response],
        ],
    ) -> Response:
        correlation_id = validate_correlation_id(request.headers.get("X-Correlation-ID"))

        request.state.correlation_id = correlation_id

        normalized_path = request.url.path.rstrip("/") or "/"

        if normalized_path not in _HEALTH_PATHS:
            try:
                request.state.principal = await provider(request)
            except AuthorizationError:
                return _create_error_response(
                    status_code=(status.HTTP_403_FORBIDDEN),
                    correlation_id=correlation_id,
                    source="AUTH",
                    code="FORBIDDEN",
                    message="Access is forbidden.",
                )
            except Exception as exc:
                logger.exception(
                    "principal_provider_failed",
                    extra={
                        "error_type": (type(exc).__name__),
                        "correlation_id": (correlation_id),
                    },
                )

                return _create_error_response(
                    status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
                    correlation_id=correlation_id,
                    source="AUTH",
                    code="PROVIDER_FAILURE",
                    message=("Principal resolution failed."),
                )

        response = await call_next(request)

        response.headers["X-Correlation-ID"] = correlation_id

        return response

    from fastapi.exceptions import HTTPException

    @fastapi_app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        correlation_id = cast(
            str,
            getattr(
                request.state,
                "correlation_id",
                "unknown",
            ),
        )
        return _create_error_response(
            status_code=exc.status_code,
            correlation_id=correlation_id,
            source="API",
            code="CLIENT_ERROR" if exc.status_code < 500 else "INTERNAL_ERROR",
            message=exc.detail if isinstance(exc.detail, str) else "Request failed.",
        )

    @fastapi_app.exception_handler(Exception)
    async def global_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        correlation_id = cast(
            str,
            getattr(
                request.state,
                "correlation_id",
                "unknown",
            ),
        )

        logger.exception(
            "unhandled_api_boundary_error",
            extra={
                "error_type": type(exc).__name__,
                "correlation_id": correlation_id,
            },
        )

        return _create_error_response(
            status_code=(status.HTTP_500_INTERNAL_SERVER_ERROR),
            correlation_id=correlation_id,
            source="SYSTEM",
            code="INTERNAL_ERROR",
            message=("An unexpected system error occurred."),
        )

    @fastapi_app.get(
        "/health/live",
        status_code=status.HTTP_200_OK,
    )
    async def liveness() -> dict[str, str]:
        """Confirm that the API process is running."""

        return {
            "status": "alive",
        }

    @fastapi_app.get(
        "/health/ready",
        status_code=status.HTTP_200_OK,
    )
    async def readiness(
        request: Request,
    ) -> JSONResponse:
        """Confirm that application resources were initialized."""

        resources = getattr(
            request.app.state,
            "resources",
            None,
        )

        if not isinstance(
            resources,
            RuntimeResources,
        ):
            return JSONResponse(
                status_code=(status.HTTP_503_SERVICE_UNAVAILABLE),
                content={
                    "status": "not initialized",
                },
            )

        probe_names = ("mongodb", "source_mongodb", "sqlserver", "neo4j", "valkey", "temporal")
        probe_results = await asyncio.gather(
            probe_mongodb(request),
            probe_source_mongodb(request),
            probe_sqlserver(request),
            probe_neo4j(request),
            probe_valkey(request),
            probe_temporal(request),
        )
        dependencies = {
            name: result.model_dump(mode="json")
            for name, result in zip(probe_names, probe_results, strict=True)
        }
        ready = all(result.status is DependencyStatus.HEALTHY for result in probe_results)
        return JSONResponse(
            status_code=(status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE),
            content={
                "status": "ready" if ready else "not ready",
                "catalog": {
                    "version": resources.catalog.catalog.version,
                    "asset_count": resources.catalog.asset_count,
                },
                "dependencies": dependencies,
            },
        )

    fastapi_app.include_router(console_router)
    fastapi_app.include_router(graph_router)
    fastapi_app.include_router(graph_evidence_router)
    fastapi_app.include_router(inventory_router)
    fastapi_app.include_router(sources_router)
    fastapi_app.include_router(browser_router)
    fastapi_app.include_router(workspaces_router)
    fastapi_app.include_router(jobs_router)
    fastapi_app.include_router(scenarios_router)
    fastapi_app.include_router(audit_router)
    fastapi_app.include_router(returns_router)
    fastapi_app.include_router(support_router)
    fastapi_app.include_router(ai_gateway_router)
    fastapi_app.include_router(seed_router)
    fastapi_app.include_router(dependencies_router)

    return fastapi_app
