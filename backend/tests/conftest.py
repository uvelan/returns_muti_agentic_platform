"""Shared fixtures and environment configuration for backend tests."""

import os
from pathlib import Path
from urllib.parse import quote

import pytest
from dotenv import load_dotenv
from pydantic import AnyHttpUrl, SecretStr

from return_platform.configuration.settings import Settings
from return_platform.data_governance import (
    LoadedAssetCatalog,
    load_asset_catalog,
)
from return_platform.resources import RuntimeResources

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_FILE = REPOSITORY_ROOT / ".env"


def pytest_configure(
    config: pytest.Config,
) -> None:
    """Load the repository environment before tests execute."""

    del config

    if not ROOT_ENV_FILE.is_file():
        raise RuntimeError(f"Required repository environment file was not found: {ROOT_ENV_FILE}")

    loaded = load_dotenv(
        dotenv_path=ROOT_ENV_FILE,
        override=False,
    )

    if not loaded:
        raise RuntimeError("The repository environment file could not be loaded.")


def _required_environment_variable(
    name: str,
) -> str:
    """Return a required environment variable without exposing its value."""

    value = os.getenv(name)

    if value is None or not value.strip():
        raise RuntimeError(f"Required test environment variable is not set: {name}")

    return value


#: Stands in for a model-provider key when none is configured.
#:
#: `Settings` requires the AI-gateway fields to be populated for a route pool to
#: exist at all, so `test_settings` used to demand NVIDIA_API_KEY and
#: GOOGLE_API_KEY through `_required_environment_variable`. That made index
#: drift, catalog loading, health probes and the system-store bootstrap -- none
#: of which reach a provider -- fail at setup on a machine with no model
#: credentials, and three modules had already responded by copying the fixture
#: rather than using it, which is a second implementation of settings
#: construction growing inside the test tree.
#:
#: Deliberately not a real-looking value: a test that genuinely dispatches to a
#: provider must take `live_ai_credentials`, which skips when the real keys are
#: absent instead of letting this one reach an endpoint and fail as an
#: authentication error somewhere unrelated.
_ABSENT_PROVIDER_KEY = "test-placeholder-no-provider-call-expected"


def _provider_key(name: str) -> str:
    return os.getenv(name, "").strip() or _ABSENT_PROVIDER_KEY


@pytest.fixture
def live_ai_credentials() -> None:
    """Opt in to a test that actually dispatches to a model provider.

    The explicit half of the rule the placeholder above implements: a test that
    needs a real key says so and is skipped without one, rather than every test
    in the suite carrying the requirement so that a handful can be satisfied.
    """

    missing = [
        name for name in ("NVIDIA_API_KEY", "GOOGLE_API_KEY") if not os.getenv(name, "").strip()
    ]
    if missing:
        pytest.skip(f"no model-provider credentials configured: {', '.join(missing)}")


@pytest.fixture
def empty_catalog_path(
    tmp_path: Path,
) -> Path:
    """Create a valid empty governance catalog."""

    catalog_path = tmp_path / "data_assets.yaml"

    catalog_path.write_text(
        'version: "1.0"\nassets: []\n',
        encoding="utf-8",
    )

    return catalog_path.resolve()


@pytest.fixture
def loaded_empty_catalog(
    empty_catalog_path: Path,
) -> LoadedAssetCatalog:
    """Load an isolated empty governance catalog."""

    return load_asset_catalog(empty_catalog_path)


@pytest.fixture
def test_settings(
    empty_catalog_path: Path,
) -> Settings:
    """Provide valid test settings using root environment secrets."""

    mongo_username = quote(
        _required_environment_variable("MONGO_ROOT_USERNAME"),
        safe="",
    )

    mongo_password = quote(
        _required_environment_variable("MONGO_ROOT_PASSWORD"),
        safe="",
    )

    # Defaults to "localhost" for host-side test runs against the compose port
    # mapping. A test runner attached to the compose network directly (so that
    # Mongo-dependent tests can exercise the real replica set without rewriting its
    # advertised host) sets this to the compose service name, e.g. "mongodb".
    mongo_host = os.getenv("PLATFORM_TEST_MONGO_HOST", "localhost")
    temporal_target = os.getenv("PLATFORM_TEST_TEMPORAL_TARGET", "localhost:7233")
    sqlserver_host = os.getenv("PLATFORM_TEST_SQLSERVER_HOST", "localhost")
    # The remaining three dependency addresses, overridable for the same reason
    # and previously not. They were passed to `Settings` as literal keyword
    # arguments, and an init keyword outranks both the process environment and
    # the dotenv file -- so a container run could not move them at all, and
    # `docker exec -e PLATFORM_NEO4J_URI=...` reached a value nothing consulted.
    # That is what made every test in `test_order_agent_rest.py` fail on a Neo4j
    # connection to IPv6 loopback inside the compose network.
    neo4j_uri = os.getenv("PLATFORM_TEST_NEO4J_URI", "bolt://localhost:7687")
    valkey_host = os.getenv("PLATFORM_TEST_VALKEY_HOST", "localhost")
    # Published port on the host, container port in-network. The default reads
    # the repository `.env` that `pytest_configure` loaded, so the host case is
    # unchanged whatever that file says.
    sqlserver_port = int(
        os.getenv("PLATFORM_TEST_SQLSERVER_PORT") or os.getenv("PLATFORM_SQLSERVER_PORT", "1433")
    )

    return Settings(
        _env_file=str(REPOSITORY_ROOT / ".env"),
        catalog_path=empty_catalog_path,
        environment="test",
        frontend_cors_origin=AnyHttpUrl("http://localhost:5173"),
        # `directConnection=true` deliberately. The deployment runs a single-node
        # replica set that advertises its *container* hostname, so ordinary
        # topology discovery from the host resolves a name that does not exist
        # there and every operation times out. A direct connection skips
        # discovery and works identically from inside the compose network, so
        # one DSN serves both hosts of `mongo_host` above. Transactions are
        # unaffected -- the driver allows them against a replica-set primary
        # whatever the topology type, and `OperationalRepository` and
        # `ReturnSupportService` both open one.
        mongo_dsn=SecretStr(
            f"mongodb://{mongo_username}:{mongo_password}"
            f"@{mongo_host}:27017/return_platform"
            "?authSource=admin&directConnection=true"
        ),
        neo4j_uri=neo4j_uri,
        neo4j_user="neo4j",
        neo4j_password=SecretStr(_required_environment_variable("GRAPH_PASSWORD")),
        valkey_host=valkey_host,
        valkey_password=SecretStr(_required_environment_variable("VALKEY_PASSWORD")),
        ai_provider_order="NVIDIA,GOOGLE",
        nvidia_lightweight_models=["meta/llama-3.1-8b-instruct"],
        nvidia_standard_models=["nvidia/llama-3.3-nemotron-super-49b-v1"],
        google_lightweight_models=["models/gemini-3.5-flash-lite"],
        google_standard_models=["models/gemini-3.6-flash"],
        nvidia_api_keys=[SecretStr(_provider_key("NVIDIA_API_KEY"))],
        google_api_keys=[SecretStr(_provider_key("GOOGLE_API_KEY"))],
        temporal_target=temporal_target,
        ai_timeout_seconds=30.0,
        ai_global_timeout_seconds=120.0,
        sqlserver_host=sqlserver_host,
        sqlserver_port=sqlserver_port,
        sqlserver_user="sa",
        sqlserver_password=SecretStr(_required_environment_variable("MSSQL_SA_PASSWORD")),
        sqlserver_database="test_db",
        vault_token_file=REPOSITORY_ROOT / ".vault-local/return-platform.token",
    )


@pytest.fixture
def isolated_lifespan_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prevent lifespan unit tests from opening external connections."""

    async def initialize_mongodb(
        settings: Settings,
        resources: RuntimeResources,
    ) -> None:
        del settings
        del resources

    async def initialize_neo4j(
        settings: Settings,
        resources: RuntimeResources,
    ) -> None:
        del settings
        del resources

    async def initialize_valkey(
        settings: Settings,
        resources: RuntimeResources,
    ) -> None:
        del settings
        del resources

    async def initialize_temporal(
        settings: Settings,
        resources: RuntimeResources,
    ) -> None:
        del settings
        del resources

    async def close_test_resources(
        resources: RuntimeResources,
    ) -> None:
        resources.sql_manager.executor.shutdown(
            wait=False,
            cancel_futures=True,
        )

    monkeypatch.setattr(
        "return_platform.main._initialize_mongodb",
        initialize_mongodb,
    )

    monkeypatch.setattr(
        "return_platform.main._initialize_neo4j",
        initialize_neo4j,
    )

    monkeypatch.setattr(
        "return_platform.main._initialize_valkey",
        initialize_valkey,
    )

    monkeypatch.setattr(
        "return_platform.main._initialize_temporal",
        initialize_temporal,
    )

    monkeypatch.setattr(
        "return_platform.main.close_resources",
        close_test_resources,
    )
