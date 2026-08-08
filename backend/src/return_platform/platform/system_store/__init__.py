"""Configuration-driven system store bootstrap (design doc §13.6, §13.7).

Application-owned structures (Mongo collections, for the canonical provider)
create themselves safely at startup: fenced-lease-guarded creation, index
provisioning, and forward-only schema migration, resolved from the
`platform.system_store` manifest module rather than hardcoded names.
"""

from __future__ import annotations

from return_platform.platform.system_store.bootstrap import (
    BootstrapStateStore,
    FenceVerifier,
    IndexDriftDetected,
    MissingSystemStoreStructure,
    StructureVanishedDuringBootstrap,
    SystemStoreBootstrapper,
    SystemStoreBootstrapTimeout,
)
from return_platform.platform.system_store.contracts import (
    BootstrapState,
    BootstrapStatus,
    CompatibilityStatus,
    IndexDefinition,
    IndexDriftReport,
    IndexEnsureResult,
    StructureDefinition,
    StructureIdentity,
    StructureInspection,
    SystemStoreAdapter,
    SystemStoreBootstrapReport,
    compute_manifest_fingerprint,
    compute_structure_fingerprint,
)
from return_platform.platform.system_store.encryption import EncryptionGuard, PlaintextWriteRejected
from return_platform.platform.system_store.locking import (
    FencedLease,
    FencedLeaseManager,
    LeaseLost,
    LeaseStore,
    LeaseUnavailable,
    bounded_retry_with_jitter,
)
from return_platform.platform.system_store.migrations import (
    Migration,
    MigrationDowngradeUnsupported,
    MigrationPathInvalid,
    MigrationPathValidator,
    MigrationRunner,
    VersionLedger,
)
from return_platform.platform.system_store.mongo import (
    FencedMongoTransactionGuard,
    MongoBootstrapStateStore,
    MongoLeaseStore,
    MongoStructureGateway,
    MongoSystemStoreAdapter,
    MongoVersionLedger,
    PymongoStructureGateway,
    StructurePhysicalIdentityUnavailable,
)
from return_platform.platform.system_store.repository import (
    EncryptedStructureRequiresGuardedAccess,
    ReadOnlyCollection,
    SystemStore,
    UnknownStructure,
)

__all__ = [
    "BootstrapState",
    "BootstrapStateStore",
    "BootstrapStatus",
    "CompatibilityStatus",
    "EncryptedStructureRequiresGuardedAccess",
    "EncryptionGuard",
    "FenceVerifier",
    "FencedLease",
    "FencedLeaseManager",
    "FencedMongoTransactionGuard",
    "IndexDefinition",
    "IndexDriftDetected",
    "IndexDriftReport",
    "IndexEnsureResult",
    "LeaseLost",
    "LeaseStore",
    "LeaseUnavailable",
    "Migration",
    "MigrationDowngradeUnsupported",
    "MigrationPathInvalid",
    "MigrationPathValidator",
    "MigrationRunner",
    "MissingSystemStoreStructure",
    "MongoBootstrapStateStore",
    "MongoLeaseStore",
    "MongoStructureGateway",
    "MongoSystemStoreAdapter",
    "MongoVersionLedger",
    "PlaintextWriteRejected",
    "PymongoStructureGateway",
    "ReadOnlyCollection",
    "StructureDefinition",
    "StructureIdentity",
    "StructureInspection",
    "StructurePhysicalIdentityUnavailable",
    "StructureVanishedDuringBootstrap",
    "SystemStore",
    "SystemStoreAdapter",
    "SystemStoreBootstrapReport",
    "SystemStoreBootstrapTimeout",
    "SystemStoreBootstrapper",
    "UnknownStructure",
    "VersionLedger",
    "bounded_retry_with_jitter",
    "compute_manifest_fingerprint",
    "compute_structure_fingerprint",
]
