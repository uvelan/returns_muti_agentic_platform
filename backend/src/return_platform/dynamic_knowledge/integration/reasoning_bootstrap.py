"""Bootstraps the real Mongo-backed SystemStore + EnvelopeEncryptor the
reasoning subsystem's encrypted checkpoint/evidence structures need.

Shared by every process that needs a `DynamicOrderAgentCoordinator`: the
FastAPI process (`main.py`) and the dedicated `order-discovery-worker`
Temporal worker process (`scripts/run_order_discovery_worker.py`) both need
their own `SystemStore`/`EnvelopeEncryptor`, but the underlying Mongo
structures/migrations are shared infrastructure that must not be bootstrapped
by two independently-written copies of the same logic -- mirrors
`configuration/runtime_integrations.py`'s `verify_runtime_validation_receipts`,
which `run_return_workflow_worker.py` already imports the same way.
"""

from __future__ import annotations

import base64
from uuid import uuid4

from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.secrets.envelope import AesGcmEnvelopeEncryptor
from return_platform.platform.system_store.bootstrap import SystemStoreBootstrapper
from return_platform.platform.system_store.manifest_loader import (
    load_system_store_config,
    structure_definitions,
)
from return_platform.platform.system_store.migrations import MigrationRunner
from return_platform.platform.system_store.mongo import (
    FencedMongoTransactionGuard,
    MongoBootstrapStateStore,
    MongoLeaseStore,
    MongoSystemStoreAdapter,
    MongoVersionLedger,
    PymongoStructureGateway,
)
from return_platform.platform.system_store.repository import SystemStore


async def bootstrap_reasoning_system_store(
    settings: Settings,
    platform_mongo: AsyncMongoClient[dict[str, object]],
) -> tuple[SystemStore, AesGcmEnvelopeEncryptor]:
    """Bootstrap the real Mongo-backed SystemStore the reasoning subsystem's
    encrypted checkpoint/evidence structures need. Nothing in `src` did this before --
    only test fixtures constructed a SystemStore/EnvelopeEncryptor directly."""

    config = load_system_store_config(settings.system_store_manifest_path)
    structures = structure_definitions(config)
    bootstrapper = SystemStoreBootstrapper(
        lease_store=MongoLeaseStore(platform_mongo, database="platform"),
        adapter=MongoSystemStoreAdapter(
            PymongoStructureGateway(platform_mongo, database="platform")
        ),
        migration_runner=MigrationRunner(MongoVersionLedger(platform_mongo, database="platform")),
        bootstrap_state=MongoBootstrapStateStore(platform_mongo, database="platform"),
        guard=FencedMongoTransactionGuard(platform_mongo, database="platform"),
        owner_instance_id=str(uuid4()),
        fail_closed_on_drift=config.fail_closed_on_drift,
    )
    await bootstrapper.bootstrap(
        structures, auto_bootstrap_missing=config.auto_bootstrap_missing_structures
    )
    system_store = SystemStore(
        platform_mongo,
        {definition.logical_name: definition for definition in structures},
        database="platform",
    )
    encryptor = AesGcmEnvelopeEncryptor(
        key=base64.b64decode(settings.reasoning_encryption_key.get_secret_value()),
        key_ref=settings.reasoning_encryption_key_secret_reference or "reasoning-dev-key",
    )
    return system_store, encryptor
