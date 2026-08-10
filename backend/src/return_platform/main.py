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

from return_platform.ai.interception.store import SystemStoreInterceptionStore
from return_platform.ai_gateway.configuration import (
    build_loaded_ai_gateway_configuration,
    load_ai_gateway_configuration,
)
from return_platform.ai_gateway.routing import AIRoutePool, build_routes
from return_platform.api.ai_gateway import router as ai_gateway_router
from return_platform.api.associate_returns import router as associate_returns_router
from return_platform.api.canonical_ai import router as canonical_ai_router
from return_platform.api.canonical_principal import router as canonical_principal_router
from return_platform.api.canonical_returns import router as canonical_returns_router
from return_platform.api.canonical_session import router as canonical_session_router
from return_platform.api.dependencies import router as dependencies_router
from return_platform.api.dependency_probes import (
    probe_mongodb,
    probe_neo4j,
    probe_source_mongodb,
    probe_sqlserver,
    probe_temporal,
    probe_valkey,
)
from return_platform.api.dependency_simulator import router as dependency_simulator_router
from return_platform.api.integration_outbox import router as integration_outbox_router
from return_platform.api.physical_operations import router as physical_operations_router
from return_platform.api.production_workflow import router as production_workflow_router
from return_platform.api.return_agents import router as return_agents_router
from return_platform.api.return_artifacts import router as return_artifacts_router
from return_platform.api.return_support import router as return_support_router
from return_platform.api.returns import router as returns_router
from return_platform.api.runtime_config import router as runtime_config_router
from return_platform.api.seed import router as seed_router
from return_platform.api.support import router as support_router
from return_platform.api.warehouse_placement import router as warehouse_placement_router
from return_platform.bootstrap.adapters.analyzer_ai_adapter import (
    build_analyzer_ai_adapter,
)
from return_platform.bootstrap.adapters.analyzer_graph_target_adapter import (
    build_neo4j_graph_target_adapter,
)
from return_platform.bootstrap.adapters.analyzer_source_adapter import (
    build_mongo_source_discovery_adapter,
)
from return_platform.bootstrap.context import (
    RuntimeContext,
    StaticCorrelationContext,
    SystemClock,
)
from return_platform.bootstrap.lifespan import module_lifespan
from return_platform.bootstrap.system_store import bootstrap_system_store
from return_platform.configuration.api.router import router as canonical_configuration_router
from return_platform.configuration.graph_repository import (
    ConfigurationGraphRepository,
    InMemoryConfigurationGraphRepository,
    Neo4jConfigurationGraphRepository,
)
from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.configuration.runtime_activation import RuntimeConfigurationActivator
from return_platform.configuration.runtime_integrations import (
    apply_graph_runtime_configuration,
    verify_runtime_validation_receipts,
)
from return_platform.configuration.settings import Settings
from return_platform.configuration.snapshot import ConfigurationSnapshotBuilder
from return_platform.data_governance import load_asset_catalog
from return_platform.data_platform.graph.sync_service import GraphSyncService
from return_platform.data_platform.operational_generation.adapters.graph_sync import (
    configure_graph_sync,
)
from return_platform.data_platform.operational_generation.adapters.source_mongodb import (
    configure_source_mongodb,
)
from return_platform.data_platform.schema_registry import load_schema_registry
from return_platform.dependency_simulation.configuration import (
    build_loaded_dependency_simulation_configuration,
    load_dependency_simulation_configuration,
)
from return_platform.dependency_simulation.repository import MongoSimulationRepository
from return_platform.dynamic_knowledge.api.order_agent import DynamicOrderAgentRuntime
from return_platform.dynamic_knowledge.api.order_agent import (
    router as dynamic_order_agent_router,
)
from return_platform.dynamic_knowledge.integration.runtime_factory import (
    dynamic_order_agent_enabled,
)
from return_platform.graph_schema_analyzer.api import router as graph_schema_analyzer_router
from return_platform.graph_schema_analyzer.persistence import build_system_store_persistence
from return_platform.operations.repository import OperationalRepository
from return_platform.operations.return_support.service import ReturnSupportService
from return_platform.platform.capabilities.registry import InMemoryCapabilityRegistry
from return_platform.platform.contracts.epoch import RuntimeEpoch
from return_platform.platform.contracts.runtime_configuration import RuntimeConfigurationView
from return_platform.platform.modules.registry import ModuleRegistry
from return_platform.resources import (
    AsyncValkeyClient,
    RuntimeResources,
    close_resources,
)
from return_platform.secrets.runtime import resolve_runtime_settings_from_vault
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


class _NoModulesYetConfigurationHandle:
    """Placeholder RuntimeConfigurationHandle for the zero-module kernel proof below.

    No module is registered yet (see `_kernel_module_registry`), so nothing ever calls
    this -- it exists only to satisfy RuntimeContext's required field. Deleted once
    Phase 2's real ConfigurationHandle exists to wire in its place.
    """

    def current(self, epoch: RuntimeEpoch) -> RuntimeConfigurationView:  # pragma: no cover
        raise NotImplementedError("no modules are registered yet; nothing calls this")

    async def pinned(self, release_id: str) -> RuntimeConfigurationView:  # pragma: no cover
        raise NotImplementedError("no modules are registered yet; nothing calls this")

    @property
    def adopted_release_id(self) -> str:
        return "none"

    @property
    def pending_release_id(self) -> str | None:
        return None

    @property
    def requires_restart(self) -> bool:
        return False


_kernel_module_registry = ModuleRegistry()
"""Empty on purpose (plan Phase 1B / design doc section 2.1): proves the module
kernel's construct -> publish -> resolve -> initialize -> shutdown sequence runs
end to end alongside the existing boot process, with zero modules and therefore zero
effect on it. The first real module is registered here starting in a later phase."""


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


async def _initialize_mongodb(
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
        async with asyncio.timeout(settings.dependency_connect_timeout_seconds):
            await mongo_client.admin.command("ping")
            if resources.source_mongo is not mongo_client:
                await resources.source_mongo.admin.command("ping")
    except Exception as exc:
        _log_initialization_failure(
            "mongodb",
            exc,
        )
        resources.mongo = None
        resources.source_mongo = None
        if settings.environment == "production":
            raise RuntimeError("Required MongoDB dependencies are unavailable") from exc


async def _initialize_neo4j(
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
        async with asyncio.timeout(settings.dependency_connect_timeout_seconds):
            await resources.neo4j.verify_connectivity()
    except Exception as exc:
        _log_initialization_failure(
            "neo4j",
            exc,
        )
        resources.neo4j = None
        if settings.environment == "production":
            raise RuntimeError("Required Neo4j dependency is unavailable") from exc


async def _initialize_valkey(
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
        async with asyncio.timeout(settings.dependency_connect_timeout_seconds):
            if not await resources.valkey.ping():
                raise RuntimeError("Valkey ping returned an unhealthy result")
    except Exception as exc:
        _log_initialization_failure(
            "valkey",
            exc,
        )
        resources.valkey = None
        if settings.environment == "production":
            raise RuntimeError("Required Valkey dependency is unavailable") from exc


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
        resources.temporal = None
        if settings.environment == "production":
            raise RuntimeError("Required Temporal dependency is unavailable") from exc


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

    configured_settings = _get_settings(app)
    try:
        bootstrap_settings, secret_resolver = await resolve_runtime_settings_from_vault(
            configured_settings,
            resolve_ai_credentials=False,
        )
    except Exception as exc:
        _log_initialization_failure("vault", exc)
        if configured_settings.environment == "production":
            raise RuntimeError("Required Vault dependency is unavailable") from exc
        bootstrap_settings = configured_settings
        secret_resolver = None
    app.state.settings = bootstrap_settings
    app.state.secret_resolver = secret_resolver

    loaded_catalog = load_asset_catalog(bootstrap_settings.catalog_path)
    schema_registry = load_schema_registry(bootstrap_settings.schema_registry_path)
    baseline_return_configuration = load_return_configuration(
        bootstrap_settings.return_configuration_path
    )
    baseline_dependency_simulation_configuration = load_dependency_simulation_configuration(
        bootstrap_settings.dependency_simulation_configuration_path
    )
    baseline_ai_gateway_configuration = load_ai_gateway_configuration(
        bootstrap_settings.ai_gateway_configuration_path
    )

    resources = RuntimeResources(
        settings=bootstrap_settings,
        catalog=loaded_catalog,
        schema_registry=schema_registry,
    )
    try:
        await _initialize_neo4j(
            bootstrap_settings,
            resources,
        )
        graph_configuration_repository: ConfigurationGraphRepository
        if resources.neo4j is not None:
            graph_configuration_repository = Neo4jConfigurationGraphRepository(resources.neo4j)
        else:
            graph_configuration_repository = InMemoryConfigurationGraphRepository()
        app.state.graph_configuration_repository = graph_configuration_repository

        configuration_snapshot = await ConfigurationSnapshotBuilder(
            graph_configuration_repository
        ).build_snapshot(
            baseline_return_configuration.configuration,
            allow_baseline_fallback=(bootstrap_settings.environment in _DEVELOPMENT_ENVIRONMENTS),
            default_ai_gateway_configuration=(baseline_ai_gateway_configuration.configuration),
            default_dependency_simulation_configuration=(
                baseline_dependency_simulation_configuration.configuration
            ),
            require_all_behavior_domains=(
                bootstrap_settings.environment not in _DEVELOPMENT_ENVIRONMENTS
            ),
        )
        return_configuration = LoadedReturnConfiguration(
            configuration=configuration_snapshot.configuration,
            path=baseline_return_configuration.path,
            sha256=configuration_snapshot.checksum_sha256,
        )
        if configuration_snapshot.ai_gateway_configuration is None:
            raise RuntimeError("Runtime snapshot has no AI gateway configuration")
        if configuration_snapshot.dependency_simulation_configuration is None:
            raise RuntimeError("Runtime snapshot has no dependency simulation configuration")
        ai_gateway_configuration = build_loaded_ai_gateway_configuration(
            configuration_snapshot.ai_gateway_configuration,
            path=baseline_ai_gateway_configuration.path,
        )
        dependency_simulation_configuration = build_loaded_dependency_simulation_configuration(
            configuration_snapshot.dependency_simulation_configuration,
            path=baseline_dependency_simulation_configuration.path,
        )
        graph_settings = apply_graph_runtime_configuration(
            bootstrap_settings,
            return_configuration.configuration,
        )
        try:
            settings, resolved_secret_resolver = await resolve_runtime_settings_from_vault(
                graph_settings
            )
        except Exception as exc:
            _log_initialization_failure("vault", exc)
            if graph_settings.environment == "production":
                raise RuntimeError("Required Vault dependency is unavailable") from exc
            settings = graph_settings
            resolved_secret_resolver = None
        if resolved_secret_resolver is not None:
            secret_resolver = resolved_secret_resolver
            app.state.secret_resolver = secret_resolver
        resources.settings = settings
        app.state.settings = settings

        await _initialize_mongodb(
            settings,
            resources,
        )
        if resources.source_mongo is not None:
            configure_source_mongodb(
                resources.source_mongo,
                settings.source_mongo_database,
                schema_registry,
            )
        if (
            resources.mongo is not None
            and resources.source_mongo is not None
            and resources.neo4j is not None
        ):
            configure_graph_sync(
                GraphSyncService(
                    platform_client=resources.mongo,
                    source_client=resources.source_mongo,
                    driver=resources.neo4j,
                    settings=settings,
                    registry=schema_registry,
                )
            )
        await _initialize_valkey(
            settings,
            resources,
        )
        await _initialize_temporal(
            settings,
            resources,
        )

        app.state.resources = resources
        app.state.dependency_simulation_configuration = dependency_simulation_configuration
        app.state.ai_gateway_configuration = ai_gateway_configuration
        app.state.return_configuration = return_configuration
        app.state.return_configuration_snapshot = configuration_snapshot
        app.state.runtime_configuration_activator = RuntimeConfigurationActivator(
            app_state=app.state,
            repository=graph_configuration_repository,
            baseline_path=baseline_return_configuration.path,
            ai_gateway_baseline_path=baseline_ai_gateway_configuration.path,
            dependency_simulation_baseline_path=(baseline_dependency_simulation_configuration.path),
            resources=resources,
        )
        if resources.mongo is not None:
            await verify_runtime_validation_receipts(
                resources.mongo,
                settings.mongo_database,
                return_configuration.configuration,
            )
            operational_repository = OperationalRepository(
                resources.mongo,
                settings,
                resources.source_mongo,
            )
            await operational_repository.ensure_indexes()
            await operational_repository.persist_return_configuration_snapshot(
                path=str(return_configuration.path),
                sha256=return_configuration.sha256,
                schema_version=return_configuration.configuration.schema_version,
                assumption_set_version=(return_configuration.configuration.assumption_set_version),
                configuration=return_configuration.configuration.model_dump(mode="json"),
                behavior_domains=configuration_snapshot.domain_payloads,
            )
            await MongoSimulationRepository(resources.mongo, settings).ensure_indexes()
            await ReturnSupportService(
                client=resources.mongo,
                settings=settings,
                configuration=return_configuration.configuration,
                operational_repository=operational_repository,
            ).ensure_indexes()
        # The SystemStore is bootstrapped here, before the route pool, because
        # the MANUAL provider's durable implementation needs an interception
        # store *at route construction time* -- and until Wave D2 closed this,
        # the API process built routes first and silently resolved MANUAL to the
        # filesystem `ManualFileProvider` while the order-discovery worker, which
        # bootstraps first, got the durable one. Two processes, two different
        # MANUAL providers, one of them writing prompts to disk.
        #
        # Degrade-safety is preserved exactly: a bootstrap failure sets the
        # analyzer status, leaves `analyzer_system_store` None, and the route
        # pool is still built -- with the filesystem provider, which is what this
        # process had anyway.
        analyzer_system_store = None
        analyzer_encryptor = None
        app.state.graph_schema_analyzer_status = {"state": "UNAVAILABLE"}
        if resources.mongo is None:
            logger.warning(
                "graph_schema_analyzer_dependencies_unavailable",
                extra={"missing_dependencies": ("mongodb",)},
            )
        else:
            try:
                analyzer_system_store, analyzer_encryptor = await bootstrap_system_store(
                    settings, resources.mongo
                )
            except Exception as exc:  # noqa: BLE001 - degrade, never block startup
                app.state.graph_schema_analyzer_status = {
                    "state": "INITIALIZATION_FAILED",
                    "detail": str(exc),
                }
                logger.warning("graph_schema_analyzer_initialization_failed", exc_info=exc)

        interception_store = (
            SystemStoreInterceptionStore(analyzer_system_store, analyzer_encryptor)
            if analyzer_system_store is not None and analyzer_encryptor is not None
            else None
        )
        app.state.ai_interception_store = interception_store
        app.state.ai_gateway_route_pool = AIRoutePool(
            build_routes(settings, interception_store=interception_store),
            ai_gateway_configuration.configuration,
        )
        dynamic_agent_enabled = dynamic_order_agent_enabled(settings)
        app.state.dynamic_order_agent_status = {
            "enabled": dynamic_agent_enabled,
            "state": "DISABLED",
        }
        if dynamic_agent_enabled:
            # Turn processing itself now runs entirely inside the dedicated
            # order-discovery-worker process's Activity (see
            # workflows/order_discovery_activities.py) -- the FastAPI process
            # only needs a Temporal client to route a turn to that worker's
            # workflow, not a DynamicOrderAgentCoordinator of its own.
            temporal_client = resources.temporal
            if temporal_client is None:
                app.state.dynamic_order_agent_status = {
                    "enabled": True,
                    "state": "DEPENDENCIES_UNAVAILABLE",
                    "missing_dependencies": ("temporal",),
                }
                logger.warning(
                    "dynamic_order_agent_dependencies_unavailable",
                    extra={"missing_dependencies": ("temporal",)},
                )
            else:
                app.state.dynamic_order_agent_runtime = DynamicOrderAgentRuntime(
                    temporal_client=temporal_client,
                    task_queue=settings.order_discovery_workflow_task_queue,
                )
                app.state.dynamic_order_agent_status = {
                    "enabled": True,
                    "state": "READY",
                }

        # Graph Schema Analyzer (Wave C3). Unlike the order agent it does its own
        # durable work in-process, so this process needs a real SystemStore --
        # re-introduced here after Commit 3 removed it, since the order agent no
        # longer needed one. Degrades to an explicit UNAVAILABLE state rather than
        # failing startup: the analyzer is an operator tool, and the return flow
        # must not stop serving because schema analysis cannot persist.
        # The SystemStore was bootstrapped above, before the route pool, so the
        # MANUAL provider could be the durable one. What is left here is binding
        # the analyzer's own surfaces onto it.
        if analyzer_system_store is not None:
            app.state.graph_schema_analyzer_persistence = build_system_store_persistence(
                analyzer_system_store
            )
            # Bind the analyzer's outward ports. Each is independently
            # optional: discovery needs a source client, validation needs
            # Neo4j, and an operator with one but not the other should still
            # get the half that works rather than a wholly dead module. The
            # routes that need a missing port 503 individually.
            if resources.source_mongo is not None:
                app.state.graph_schema_analyzer_source_discovery = (
                    build_mongo_source_discovery_adapter(
                        resources.source_mongo,
                        database_name=settings.source_mongo_database,
                        source_id=settings.source_mongo_database,
                    )
                )
            if resources.neo4j is not None:
                app.state.graph_schema_analyzer_graph_target = build_neo4j_graph_target_adapter(
                    resources.neo4j
                )
            # Reasoning needs no dependency of its own beyond the shared
            # route pool, but an empty pool means no provider credential is
            # configured, and binding the port then would advertise a
            # capability every call would fail. A misconfigured task raises
            # from the invoker's constructor; degrade like the rest of this
            # block rather than failing startup over an operator tool.
            analyzer_reasoning_bound = False
            if app.state.ai_gateway_route_pool.routes:
                try:
                    app.state.graph_schema_analyzer_reasoning = build_analyzer_ai_adapter(
                        settings=settings,
                        configuration=ai_gateway_configuration.configuration,
                        route_pool=app.state.ai_gateway_route_pool,
                    )
                except Exception as exc:  # noqa: BLE001 - degrade, never block startup
                    logger.warning(
                        "graph_schema_analyzer_reasoning_unavailable",
                        exc_info=exc,
                    )
                else:
                    analyzer_reasoning_bound = True
            else:
                logger.warning(
                    "graph_schema_analyzer_reasoning_unavailable",
                    extra={"missing_dependencies": ("ai_gateway_routes",)},
                )
            app.state.graph_schema_analyzer_status = {
                "state": "READY",
                "source_discovery": resources.source_mongo is not None,
                "graph_target": resources.neo4j is not None,
                "reasoning": analyzer_reasoning_bound,
            }

        logger.info(
            "application_resources_initialized",
            extra={
                "catalog_version": (loaded_catalog.catalog.version),
                "catalog_asset_count": (loaded_catalog.asset_count),
                "catalog_sha256": (loaded_catalog.sha256_hex),
                "schema_registry_asset_count": len(schema_registry.assets),
                "graph_node_count": len(schema_registry.graph.nodes),
                "return_configuration_sha256": return_configuration.sha256,
                "return_configuration_release_id": configuration_snapshot.release_id,
                "return_configuration_source": configuration_snapshot.source,
                "return_assumption_set": (
                    return_configuration.configuration.assumption_set_version
                ),
                "dependency_simulation_sha256": dependency_simulation_configuration.sha256,
                "ai_gateway_configuration_sha256": ai_gateway_configuration.sha256,
                "ai_gateway_route_count": len(app.state.ai_gateway_route_pool.routes),
                "dynamic_order_agent_state": app.state.dynamic_order_agent_status["state"],
            },
        )

        kernel_capabilities = InMemoryCapabilityRegistry()
        kernel_context = RuntimeContext(
            configuration=_NoModulesYetConfigurationHandle(),
            capabilities=kernel_capabilities,
            clock=SystemClock(),
            correlation=StaticCorrelationContext(),
        )
        async with module_lifespan(
            registry=_kernel_module_registry,
            module_ids=(),
            context=kernel_context,
            configs={},
            capabilities=kernel_capabilities,
            adapter_publishers=(),
        ) as kernel_modules:
            app.state.kernel_modules = kernel_modules
            yield
    finally:
        if hasattr(app.state, "dynamic_order_agent_runtime"):
            del app.state.dynamic_order_agent_runtime
        for analyzer_attribute in (
            "graph_schema_analyzer_source_discovery",
            "graph_schema_analyzer_graph_target",
            "graph_schema_analyzer_reasoning",
        ):
            if hasattr(app.state, analyzer_attribute):
                delattr(app.state, analyzer_attribute)
        if hasattr(app.state, "graph_schema_analyzer_persistence"):
            # Holds no connection of its own -- it borrows resources.mongo, which
            # close_resources() below owns -- so dropping the reference is the whole
            # of teardown. Removed anyway so a torn-down app cannot serve analyzer
            # requests against a closed client.
            del app.state.graph_schema_analyzer_persistence
        configure_graph_sync(None)
        configure_source_mongodb(None)
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

            activator = getattr(
                request.app.state,
                "runtime_configuration_activator",
                None,
            )
            if isinstance(activator, RuntimeConfigurationActivator):
                try:
                    await activator.refresh()
                except Exception as exc:
                    logger.exception(
                        "runtime_configuration_refresh_failed_using_last_good_snapshot",
                        extra={
                            "error_type": type(exc).__name__,
                            "correlation_id": correlation_id,
                        },
                    )

        response = await call_next(request)

        # The `/api/v2/` write hook that used to call
        # `V2PlatformServices.persist_all()` here went with the V2 shell in Wave
        # F2. `/api/v2/order-agent` still exists and is unrelated -- it is Wave
        # C's Order Discovery surface, which persists through its own workflow.

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
        error_code = "CLIENT_ERROR" if exc.status_code < 500 else "INTERNAL_ERROR"
        error_message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        if isinstance(exc.detail, dict):
            candidate_code = exc.detail.get("code")
            candidate_message = exc.detail.get("message")
            if (
                isinstance(candidate_code, str)
                and candidate_code
                and candidate_code.replace("_", "").isalnum()
                and candidate_code.upper() == candidate_code
            ):
                error_code = candidate_code[:100]
            if isinstance(candidate_message, str) and candidate_message.strip():
                error_message = candidate_message.strip()[:500]
        return _create_error_response(
            status_code=exc.status_code,
            correlation_id=correlation_id,
            source="API",
            code=error_code,
            message=error_message,
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
                "configuration": {
                    "release_id": getattr(
                        getattr(request.app.state, "return_configuration_snapshot", None),
                        "release_id",
                        "unknown",
                    ),
                    "source": getattr(
                        getattr(request.app.state, "return_configuration_snapshot", None),
                        "source",
                        "unknown",
                    ),
                },
            },
        )

    fastapi_app.include_router(runtime_config_router)
    # --- Wave F1: the Data Console routers are no longer mounted --------------
    #
    # Eighteen routers served `/data-console/v1/*` and one `/api/v1/data-console`
    # prefix. Their only consumer was the legacy frontend, which Wave F4 deleted;
    # a scan of scripts, workers and compose found nothing else calling them.
    #
    # Unregistered, not deleted. `configuration.py`, `sources.py`, `audit.py` and
    # `auth.py` still export handler functions that the canonical `/api/config`
    # router imports directly -- a Python import, not an HTTP call -- so the
    # modules stay until Wave F5 moves those bodies across.
    fastapi_app.include_router(returns_router)
    fastapi_app.include_router(return_agents_router)
    fastapi_app.include_router(return_support_router)
    fastapi_app.include_router(production_workflow_router)
    fastapi_app.include_router(physical_operations_router)
    fastapi_app.include_router(return_artifacts_router)
    fastapi_app.include_router(warehouse_placement_router)
    fastapi_app.include_router(integration_outbox_router)
    fastapi_app.include_router(associate_returns_router)
    fastapi_app.include_router(dynamic_order_agent_router)
    fastapi_app.include_router(graph_schema_analyzer_router)
    fastapi_app.include_router(canonical_configuration_router)
    fastapi_app.include_router(canonical_returns_router)
    fastapi_app.include_router(canonical_ai_router)
    fastapi_app.include_router(canonical_session_router)
    fastapi_app.include_router(canonical_principal_router)
    fastapi_app.include_router(support_router)
    fastapi_app.include_router(ai_gateway_router)
    fastapi_app.include_router(seed_router)
    fastapi_app.include_router(dependencies_router)
    fastapi_app.include_router(dependency_simulator_router)
    return fastapi_app
