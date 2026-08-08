"""Shared fixtures for reasoning platform tests: a real Mongo-backed SystemStore with
uniquely-named structures per test, matching tests/platform/test_index_drift.py's
established pattern (no shared/global state to leak between tests)."""

from __future__ import annotations

import uuid

import pytest
from pymongo import AsyncMongoClient

from return_platform.configuration.settings import Settings
from return_platform.platform.secrets.envelope import AesGcmEnvelopeEncryptor
from return_platform.platform.system_store.contracts import StructureDefinition
from return_platform.platform.system_store.repository import SystemStore

REASONING_STRUCTURE_NAMES = (
    "reasoning_runs",
    "reasoning_action_receipts",
    "ai_interceptions",
    "reasoning_resume_commands",
    "reasoning_checkpoints",
    "reasoning_checkpoint_writes",
    "order_discovery_query_evidence",
)


class ReasoningTestFixture:
    def __init__(
        self, client: AsyncMongoClient[dict[str, object]], store: SystemStore, suffix: str
    ) -> None:
        self.client = client
        self.store = store
        self.suffix = suffix
        self.encryptor = AesGcmEnvelopeEncryptor(key=b"0" * 32, key_ref=f"test-key-{suffix}")

    def physical_name(self, logical_name: str) -> str:
        return f"{logical_name}_{self.suffix}"


@pytest.fixture
def reasoning_store(test_settings: Settings) -> ReasoningTestFixture:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        test_settings.mongo_dsn.get_secret_value()
    )
    suffix = uuid.uuid4().hex[:8]
    structures = {
        name: StructureDefinition(
            logical_name=name,
            physical_name=f"{name}_{suffix}",
            schema_version=1,
            encrypted=name.startswith("reasoning_checkpoint")
            or name == "order_discovery_query_evidence",
        )
        for name in REASONING_STRUCTURE_NAMES
    }
    store = SystemStore(client, structures, database="platform")
    return ReasoningTestFixture(client, store, suffix)


@pytest.fixture
def temporal_target(test_settings: Settings) -> str:
    return test_settings.temporal_target
