"""Configuration-driven system store bootstrap (design doc §13.6, §13.7).

Application-owned structures (Mongo collections, for the canonical provider)
create themselves safely at startup: fenced-lease-guarded creation, index
provisioning, and forward-only schema migration, resolved from the
`platform.system_store` manifest module rather than hardcoded names.
"""

from __future__ import annotations

from return_platform.platform.system_store.bootstrap import (
    MissingSystemStoreStructure,
    SystemStoreBootstrapper,
)
from return_platform.platform.system_store.contracts import (
    CompatibilityStatus,
    StructureDefinition,
    StructureInspection,
    SystemStoreAdapter,
    SystemStoreBootstrapReport,
)
from return_platform.platform.system_store.encryption import EncryptionGuard, PlaintextWriteRejected
from return_platform.platform.system_store.locking import (
    FencedLease,
    FencedLeaseManager,
    LeaseLost,
    LeaseStore,
    LeaseUnavailable,
)
from return_platform.platform.system_store.migrations import (
    Migration,
    MigrationRunner,
    VersionLedger,
)
from return_platform.platform.system_store.mongo import (
    FencedMongoWriter,
    MongoLeaseStore,
    MongoStructureGateway,
    MongoSystemStoreAdapter,
    MongoVersionLedger,
    PymongoStructureGateway,
)
from return_platform.platform.system_store.repository import SystemStore, UnknownStructure

__all__ = [
    "CompatibilityStatus",
    "EncryptionGuard",
    "FencedLease",
    "FencedLeaseManager",
    "FencedMongoWriter",
    "LeaseLost",
    "LeaseStore",
    "LeaseUnavailable",
    "Migration",
    "MigrationRunner",
    "MissingSystemStoreStructure",
    "MongoLeaseStore",
    "MongoStructureGateway",
    "MongoSystemStoreAdapter",
    "MongoVersionLedger",
    "PlaintextWriteRejected",
    "PymongoStructureGateway",
    "StructureDefinition",
    "StructureInspection",
    "SystemStore",
    "SystemStoreAdapter",
    "SystemStoreBootstrapReport",
    "SystemStoreBootstrapper",
    "UnknownStructure",
    "VersionLedger",
]
