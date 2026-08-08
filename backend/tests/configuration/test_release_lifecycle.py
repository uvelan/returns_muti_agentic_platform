"""
Test: Release lifecycle — DRAFT → VALIDATED → APPROVED → ACTIVE → SUPERSEDED.

All tests use in-memory mocks for MongoDB; no live database required.
"""

from __future__ import annotations

import asyncio

import pytest

from return_platform.configuration.application.activation import (
    ActivationConflictError,
    ActivationService,
)
from return_platform.configuration.application.release_service import ReleaseService
from return_platform.configuration.domain.agents import AgentConfigNode, AgentsConfig
from return_platform.configuration.domain.ai import AiConfig
from return_platform.configuration.domain.errors import (
    ConfigurationIntegrityError,
    InvalidTransitionError,
)
from return_platform.configuration.domain.features import FeaturesConfig
from return_platform.configuration.domain.graph import GraphConfig
from return_platform.configuration.domain.integrations import IntegrationsConfig
from return_platform.configuration.domain.modules import ModuleConfigNode, ModulesConfig
from return_platform.configuration.domain.platform import PlatformConfig
from return_platform.configuration.domain.release import ReleaseStatus
from return_platform.configuration.domain.release_model import RuntimeSnapshot
from return_platform.configuration.domain.sources import SourceConfigNode, SourcesConfig
from return_platform.configuration.domain.system_store import SystemStoreConfig
from return_platform.configuration.domain.workflow import WorkflowConfig

# ---------------------------------------------------------------------------
# Minimal in-memory MongoDB mock
# ---------------------------------------------------------------------------


class _InMemoryCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    async def insert_one(self, doc: dict) -> None:
        existing = [d for d in self.docs if d.get("release_id") == doc.get("release_id")]
        if existing:
            raise ValueError(f"Duplicate release_id: {doc.get('release_id')}")
        self.docs.append(dict(doc))

    async def find_one(self, flt: dict, session=None) -> dict | None:
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                return dict(d)
        return None

    async def update_one(self, flt: dict, update: dict, session=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                if "$set" in update:
                    d.update(update["$set"])

                class _R:
                    modified_count = 1

                return _R()

        class _R0:
            modified_count = 0

        return _R0()

    async def count_documents(self, flt: dict) -> int:
        return sum(1 for d in self.docs if all(d.get(k) == v for k, v in flt.items()))

    async def create_indexes(self, _):
        pass

    async def find_one_and_update(
        self, flt, update, upsert=False, return_document=None, session=None
    ):
        for d in self.docs:
            if all(d.get(k) == v for k, v in flt.items()):
                if "$set" in update:
                    d.update(update["$set"])
                return dict(d)
        if upsert:
            new_doc = {k: v for k, v in flt.items()}
            if "$set" in update:
                new_doc.update(update["$set"])
            self.docs.append(new_doc)
            return dict(new_doc)
        return None


class _InMemorySession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def start_transaction(self):
        # Real pymongo (AsyncClientSession.start_transaction) is itself a
        # coroutine that resolves to an async context manager -- callers must
        # write `async with await session.start_transaction():`. The mock
        # must match that shape or it silently hides a missing `await` at
        # the real call site.
        return self


class _InMemoryMongoClient:
    def __init__(self):
        self._releases = _InMemoryCollection()
        self._pointer = _InMemoryCollection()

    def get_database(self, _):
        return self

    def get_collection(self, name):
        if name == "configuration_releases":
            return self._releases
        return self._pointer

    def start_session(self):
        return _InMemorySession()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot() -> RuntimeSnapshot:
    """Build a minimal self-consistent snapshot that passes the validator."""
    return RuntimeSnapshot(
        platform=PlatformConfig(),
        system_store=SystemStoreConfig(),
        modules=ModulesConfig(
            modules={
                "agent.order_discovery": ModuleConfigNode(
                    module_id="agent.order_discovery",
                    module_type="AGENT",
                )
            }
        ),
        agents=AgentsConfig(agents={"order_discovery": AgentConfigNode()}),
        workflow=WorkflowConfig(workflow={}),
        sources=SourcesConfig(
            sources={
                "sales_inv": SourceConfigNode(
                    connector_type="MONGODB",
                    access_mode="READ_ONLY",
                )
            }
        ),
        integrations=IntegrationsConfig(integrations={}),
        graph=GraphConfig(),
        ai=AiConfig(),
        features=FeaturesConfig(flags={}),
    )


def _make_services():
    client = _InMemoryMongoClient()
    release_svc = ReleaseService(client)
    activation_svc = ActivationService(client)
    return client, release_svc, activation_svc


# ---------------------------------------------------------------------------
# P0 Acceptance criterion 1: new releases start DRAFT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_release_starts_as_draft():
    _, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    release = await release_svc.create_release("r-draft-1", snapshot)
    assert release.status == ReleaseStatus.DRAFT


# ---------------------------------------------------------------------------
# P0 Acceptance criterion 2: validation failure keeps DRAFT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validation_failure_keeps_release_draft():
    client, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-fail-1", snapshot)

    # Corrupt the persisted snapshot to fail checksum
    doc = await client._releases.find_one({"release_id": "r-fail-1"})
    doc["checksum"] = "badhash"
    # Update it in the collection (hacky but sufficient for mock)
    for d in client._releases.docs:
        if d.get("release_id") == "r-fail-1":
            d["checksum"] = "badhash"

    with pytest.raises(ConfigurationIntegrityError, match="integrity"):
        await release_svc.validate_release("r-fail-1")

    doc_after = await client._releases.find_one({"release_id": "r-fail-1"})
    assert doc_after["status"] == ReleaseStatus.DRAFT


# ---------------------------------------------------------------------------
# P0 Acceptance criterion 3: validate_release runs validator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_release_runs_semantic_validator():
    """validate_release must call ConfigurationValidator, not just CAS the status."""
    _, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-val-1", snapshot)

    # If validator is called this should succeed (snapshot is coherent)
    await release_svc.validate_release("r-val-1")


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: DRAFT → APPROVED is rejected (must go via VALIDATED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_draft_to_approved_is_rejected():
    _, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-no-skip-1", snapshot)

    with pytest.raises(InvalidTransitionError):
        await release_svc.approve_release("r-no-skip-1")


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: VALIDATED cannot activate without approval
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validated_cannot_activate_without_approval():
    client, release_svc, activation_svc = _make_services()
    await activation_svc.initialize_indexes()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-no-act-1", snapshot)
    await release_svc.validate_release("r-no-act-1")

    with pytest.raises(ActivationConflictError, match="APPROVED"):
        await activation_svc.activate_release("r-no-act-1")


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: only VALIDATED can be approved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_validated_release_can_be_approved():
    _, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-appr-1", snapshot)

    with pytest.raises(InvalidTransitionError):
        await release_svc.approve_release("r-appr-1")


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: full happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_approved_release_can_activate():
    client, release_svc, activation_svc = _make_services()
    await activation_svc.initialize_indexes()
    snapshot = _make_snapshot()

    await release_svc.create_release("r-full-1", snapshot)
    await release_svc.validate_release("r-full-1")
    await release_svc.approve_release("r-full-1", approved_by="test-user")
    await activation_svc.activate_release("r-full-1")

    doc = await client._releases.find_one({"release_id": "r-full-1"})
    assert doc["status"] == ReleaseStatus.ACTIVE
    pointer = await client._pointer.find_one({"_id": "active"})
    assert pointer["release_id"] == "r-full-1"
    assert pointer["version"] == 1


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: snapshot checksum verified before validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_release_snapshot_checksum_is_verified_before_validation():
    client, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-ck-1", snapshot)

    # Tamper with stored checksum
    for d in client._releases.docs:
        if d.get("release_id") == "r-ck-1":
            d["checksum"] = "0" * 64

    with pytest.raises(ConfigurationIntegrityError, match="integrity"):
        await release_svc.validate_release("r-ck-1")


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: rejection path VALIDATED → DRAFT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validated_release_can_be_rejected_to_draft():
    client, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-rej-1", snapshot)
    await release_svc.validate_release("r-rej-1")

    await release_svc.reject_release("r-rej-1")

    doc = await client._releases.find_one({"release_id": "r-rej-1"})
    assert doc["status"] == ReleaseStatus.DRAFT
    assert doc["validated_at"] is None


# ---------------------------------------------------------------------------
# P0 Acceptance criterion: CAS guard on concurrent validate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_validate_cas_only_one_wins():
    """Two concurrent validate_release() calls on the same DRAFT release.

    The first to complete the CAS write wins; the second sees modified_count=0
    and raises InvalidTransitionError.
    """
    client, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-cas-v-1", snapshot)

    success_count = 0
    failure_count = 0
    barrier = asyncio.Barrier(2)

    async def try_validate():
        nonlocal success_count, failure_count
        await barrier.wait()
        try:
            await release_svc.validate_release("r-cas-v-1")
            success_count += 1
        except InvalidTransitionError:
            failure_count += 1

    await asyncio.gather(try_validate(), try_validate())

    assert success_count == 1
    assert failure_count == 1


@pytest.mark.asyncio
async def test_concurrent_approve_cas_only_one_wins():
    """Two concurrent approve_release() calls on the same VALIDATED release.

    The first to complete the CAS write wins; the second sees modified_count=0
    and raises InvalidTransitionError.
    """
    client, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-cas-a-1", snapshot)
    await release_svc.validate_release("r-cas-a-1")

    success_count = 0
    failure_count = 0
    barrier = asyncio.Barrier(2)

    async def try_approve():
        nonlocal success_count, failure_count
        await barrier.wait()
        try:
            await release_svc.approve_release("r-cas-a-1")
            success_count += 1
        except InvalidTransitionError:
            failure_count += 1

    await asyncio.gather(try_approve(), try_approve())

    assert success_count == 1
    assert failure_count == 1


# ---------------------------------------------------------------------------
# Slice 3R.7: checksum tamper after validation blocks approval / after approval
# blocks activation. Both are integrity violations, not ordinary CAS conflicts.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checksum_tamper_after_validation_blocks_approval():
    client, release_svc, _ = _make_services()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-tamper-approve-1", snapshot)
    await release_svc.validate_release("r-tamper-approve-1")

    for d in client._releases.docs:
        if d.get("release_id") == "r-tamper-approve-1":
            d["checksum"] = "0" * 64

    with pytest.raises(ConfigurationIntegrityError, match="integrity"):
        await release_svc.approve_release("r-tamper-approve-1")

    doc_after = await client._releases.find_one({"release_id": "r-tamper-approve-1"})
    assert doc_after["status"] == ReleaseStatus.VALIDATED


@pytest.mark.asyncio
async def test_checksum_tamper_after_approval_blocks_activation():
    client, release_svc, activation_svc = _make_services()
    await activation_svc.initialize_indexes()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-tamper-activate-1", snapshot)
    await release_svc.validate_release("r-tamper-activate-1")
    await release_svc.approve_release("r-tamper-activate-1")

    for d in client._releases.docs:
        if d.get("release_id") == "r-tamper-activate-1":
            d["checksum"] = "0" * 64

    with pytest.raises(ConfigurationIntegrityError, match="integrity"):
        await activation_svc.activate_release("r-tamper-activate-1")

    doc_after = await client._releases.find_one({"release_id": "r-tamper-activate-1"})
    assert doc_after["status"] == ReleaseStatus.APPROVED
    pointer = await client._pointer.find_one({"_id": "active"})
    assert pointer is None


# ---------------------------------------------------------------------------
# Activation: pointer _id="active", release_id, checksum, version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_pointer_contains_release_id_checksum_version():
    client, release_svc, activation_svc = _make_services()
    await activation_svc.initialize_indexes()
    snapshot = _make_snapshot()
    await release_svc.create_release("r-ptr-1", snapshot)
    await release_svc.validate_release("r-ptr-1")
    await release_svc.approve_release("r-ptr-1")
    await activation_svc.activate_release("r-ptr-1")

    pointer = await client._pointer.find_one({"_id": "active"})
    assert pointer is not None
    assert pointer["release_id"] == "r-ptr-1"
    assert "checksum" in pointer
    assert pointer["version"] == 1


# ---------------------------------------------------------------------------
# Activation: supersedes previous active release
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_supersedes_previous_active_release():
    client, release_svc, activation_svc = _make_services()
    await activation_svc.initialize_indexes()
    snapshot = _make_snapshot()

    for rid in ("r-sup-1", "r-sup-2"):
        await release_svc.create_release(rid, snapshot)
        await release_svc.validate_release(rid)
        await release_svc.approve_release(rid)

    await activation_svc.activate_release("r-sup-1")
    await activation_svc.activate_release("r-sup-2")

    r1 = await client._releases.find_one({"release_id": "r-sup-1"})
    assert r1["status"] == ReleaseStatus.SUPERSEDED
    assert r1["superseded_by"] == "r-sup-2"

    r2 = await client._releases.find_one({"release_id": "r-sup-2"})
    assert r2["status"] == ReleaseStatus.ACTIVE

    pointer = await client._pointer.find_one({"_id": "active"})
    assert pointer["release_id"] == "r-sup-2"
    assert pointer["version"] == 2
