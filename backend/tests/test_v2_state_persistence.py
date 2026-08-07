from pathlib import Path

import pytest

from return_platform.v2.models import (
    AuthorizationScope,
    ModuleCreate,
    OrderAnchor,
    PartialSyncRequest,
    SchemaDesignCreate,
    SourceField,
    SourceOrderRecord,
    SourceStructure,
)
from return_platform.v2.services import V2PlatformServices
from return_platform.v2.state_store import InMemoryV2StateStore, StateRevisionConflict

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"


@pytest.mark.asyncio
async def test_service_state_survives_restart_and_preserves_idempotency() -> None:
    store = InMemoryV2StateStore()
    first = V2PlatformServices()
    await first.bootstrap(CONFIG_ROOT)
    await first.bind_state_store(store)
    await first.configuration.create_module(
        ModuleCreate(
            module_id="policy.restart_test",
            module_type="POLICY",
            schema_version="1.0",
            configuration_version="1.0.0",
            owner="TEST_OWNER",
            payload={"enabled": True},
        ),
        "admin",
    )
    schema_context = await first.schema_design.create(
        SchemaDesignCreate(
            selected_modules=("source.sales_inv",),
            requested_capabilities=("ORDER_LOOKUP",),
            source_structures=(
                SourceStructure(
                    source_id="sales",
                    dataset="salesInv",
                    fields=(SourceField(path="_id", data_type="string", key=True),),
                    identity_paths=("_id",),
                    fingerprint="sales-v1",
                ),
            ),
        ),
        "architect",
    )
    await first.order_source.replace_records(
        (
            SourceOrderRecord(
                account="A1",
                order_number="O1",
                tracking_numbers=("TRACK-1",),
                source_revision="rev-1",
                lines=({"lineNumber": "1", "itemNumber": "SKU-1"},),
            ),
        )
    )
    sync = await first.order_sync.partial(
        PartialSyncRequest(
            anchor=OrderAnchor(type="TRACKING_NUMBER", value="TRACK-1"),
            release_id="release-test",
            authorization_scope=AuthorizationScope(accounts=("A1",)),
            idempotency_key="restart-partial-1",
        )
    )
    await first.persist_all()

    restarted = V2PlatformServices()
    await restarted.bootstrap(CONFIG_ROOT)
    await restarted.bind_state_store(store)

    restored_module = await restarted.configuration.get_module(
        "policy.restart_test", "1.0.0"
    )
    restored_context = await restarted.schema_design.get(schema_context.request_id)
    restored_sync = await restarted.order_sync.get(sync.request_id)
    replay = await restarted.order_sync.partial(
        PartialSyncRequest(
            anchor=OrderAnchor(type="TRACKING_NUMBER", value="does-not-matter"),
            release_id="release-test",
            authorization_scope=AuthorizationScope(accounts=("A1",)),
            idempotency_key="restart-partial-1",
        )
    )

    assert restored_module.payload == {"enabled": True}
    assert restored_context.context_version == schema_context.context_version
    assert restored_sync.request_id == sync.request_id
    assert replay.request_id == sync.request_id


@pytest.mark.asyncio
async def test_state_store_rejects_stale_process_revision() -> None:
    store = InMemoryV2StateStore()
    first = V2PlatformServices()
    second = V2PlatformServices()
    await first.bind_state_store(store)
    await second.bind_state_store(store)

    await first.persist_all()

    with pytest.raises(StateRevisionConflict, match="expected revision 0, current 1"):
        await second.persist_all()
