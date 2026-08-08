import hashlib
import hmac
import json

from return_platform.configuration.domain.agents import AgentsConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.errors import ConfigurationIntegrityError
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.modules import ModulesConfig
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourcesConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.workflow import WorkflowConfig


def compute_checksum(snapshot: RuntimeSnapshot) -> str:
    """Deterministic SHA-256 checksum of the canonical snapshot.

    Uses the same serialisation for release creation, validation
    verification, activation pointer, and runtime view comparison.
    """
    raw_json = json.dumps(snapshot.model_dump(), sort_keys=True)
    return hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


def verify_snapshot_integrity(snapshot: RuntimeSnapshot, expected_checksum: str) -> None:
    """Recompute the snapshot checksum and compare it to the persisted value.

    The single integrity gate for every transition that trusts a persisted snapshot:
    DRAFT -> VALIDATED, VALIDATED -> APPROVED, APPROVED -> ACTIVE, and pinned historical
    resolution. A checksum mismatch is an integrity violation, not an ordinary transition
    conflict -- it always raises ConfigurationIntegrityError, never a transition-specific
    error, so tampering is never mistaken for a concurrent-write conflict.
    """
    recomputed = compute_checksum(snapshot)
    if not hmac.compare_digest(recomputed, expected_checksum):
        raise ConfigurationIntegrityError(
            f"Snapshot failed integrity verification. "
            f"Expected checksum: {expected_checksum}, recomputed: {recomputed}"
        )


class SnapshotBuilder:
    def create_snapshot(
        self,
        platform: PlatformConfig,
        system_store: SystemStoreConfig,
        modules: ModulesConfig,
        agents: AgentsConfig,
        workflow: WorkflowConfig,
        sources: SourcesConfig,
        integrations: IntegrationsConfig,
        graph: GraphConfig,
        ai: AiConfig,
        features: FeaturesConfig,
    ) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            platform=platform,
            system_store=system_store,
            modules=modules,
            agents=agents,
            workflow=workflow,
            sources=sources,
            integrations=integrations,
            graph=graph,
            ai=ai,
            features=features,
        )
