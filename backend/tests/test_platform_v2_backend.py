from pathlib import Path

import pytest
from fastapi.routing import APIRoute

from return_platform.api.platform_v2 import router
from return_platform.v2.models import (
    AuthorizationScope,
    DraftCreate,
    FieldPatch,
    FullSyncRequest,
    ModuleCreate,
    ModuleStatus,
    OrderAnchor,
    PartialSyncRequest,
    ReleaseCreate,
    ReleaseModuleRef,
    ReleaseStatus,
    SchemaAnswer,
    SchemaDesignCreate,
    SourceField,
    SourceOrderRecord,
    SourceStructure,
    SyncStatus,
)
from return_platform.v2.services import (
    InMemoryOrderProjectionStore,
    InMemoryOrderSourceGateway,
    ModularConfigurationService,
    OrderSyncService,
    SchemaDesignService,
    V2ConflictError,
)


def _module(version: str = "1.0.0") -> ModuleCreate:
    return ModuleCreate(
        module_id="agent.test",
        module_type="AGENT",
        schema_version="1.0",
        configuration_version=version,
        owner="TEST_OWNER",
        payload={
            "enabled": True,
            "thresholds": {"confidence": 0.8},
            "direct_agent_calls_allowed": False,
            "idempotency_required": True,
        },
    )


@pytest.mark.asyncio
async def test_bootstrap_loads_each_agent_as_an_independent_module() -> None:
    service = ModularConfigurationService()
    config_root = Path(__file__).resolve().parents[1] / "config"  # noqa: ASYNC240

    await service.bootstrap(config_root)
    modules = await service.list_modules(module_type="AGENT")

    assert len(modules) == 8
    assert {module.module_id for module in modules} == {
        "agent.return_session_orchestrator",
        "agent.order_discovery",
        "agent.order_analysis",
        "agent.return_workflow",
        "agent.return_fulfillment",
        "agent.bay_allocation",
        "agent.learning",
        "agent.graph_schema_design",
    }
    assert all(module.payload["direct_agent_calls_allowed"] is False for module in modules)


@pytest.mark.asyncio
async def test_nested_field_patch_is_revision_checked_and_does_not_mutate_source() -> None:
    service = ModularConfigurationService()
    original = await service.create_module(_module(), "admin")
    draft = await service.create_draft(
        original.module_id,
        DraftCreate(configuration_version="1.1.0", from_version="1.0.0"),
        "admin",
    )

    updated = await service.patch_fields(
        draft.module_id,
        draft.configuration_version,
        FieldPatch(path=("thresholds", "confidence"), value=0.91, expected_revision=1),
        "admin",
    )

    assert updated.payload["thresholds"] == {"confidence": 0.91}
    assert updated.revision == 2
    assert (await service.get_module("agent.test", "1.0.0")).payload == original.payload
    with pytest.raises(V2ConflictError, match="Revision conflict"):
        await service.patch_fields(
            draft.module_id,
            draft.configuration_version,
            FieldPatch(path=("enabled",), value=False, expected_revision=1),
            "admin",
        )


@pytest.mark.asyncio
async def test_release_activation_is_atomic_and_supersedes_previous_release() -> None:
    service = ModularConfigurationService()
    module_one = await service.create_module(_module("1.0.0"), "admin")
    await service.transition_module("agent.test", "1.0.0", ModuleStatus.VALIDATED)
    module_one = await service.transition_module("agent.test", "1.0.0", ModuleStatus.APPROVED)
    first = await service.create_release(
        ReleaseCreate(
            release_id="release-1",
            modules=(
                ReleaseModuleRef(
                    module_id=module_one.module_id,
                    version=module_one.configuration_version,
                    checksum=module_one.checksum,
                ),
            ),
        ),
        "admin",
    )
    for target in (
        ReleaseStatus.DEPENDENCIES_RESOLVED,
        ReleaseStatus.VALIDATED,
        ReleaseStatus.APPROVED,
        ReleaseStatus.MIGRATION_READY,
        ReleaseStatus.ACTIVE,
    ):
        first = await service.transition_release(first.release_id, target)
    assert first.status is ReleaseStatus.ACTIVE

    module_two = await service.create_module(_module("2.0.0"), "admin")
    await service.transition_module("agent.test", "2.0.0", ModuleStatus.VALIDATED)
    module_two = await service.transition_module("agent.test", "2.0.0", ModuleStatus.APPROVED)
    second = await service.create_release(
        ReleaseCreate(
            release_id="release-2",
            modules=(
                ReleaseModuleRef(
                    module_id=module_two.module_id,
                    version=module_two.configuration_version,
                    checksum=module_two.checksum,
                ),
            ),
        ),
        "admin",
    )
    for target in (
        ReleaseStatus.DEPENDENCIES_RESOLVED,
        ReleaseStatus.VALIDATED,
        ReleaseStatus.APPROVED,
        ReleaseStatus.MIGRATION_READY,
        ReleaseStatus.ACTIVE,
    ):
        second = await service.transition_release(second.release_id, target)

    assert (await service.active_release()).release_id == "release-2"  # type: ignore[union-attr]
    assert (await service.get_release("release-1")).status is ReleaseStatus.SUPERSEDED


@pytest.mark.asyncio
async def test_schema_design_asks_only_unresolved_metadata_question() -> None:
    service = SchemaDesignService()
    context = await service.create(
        SchemaDesignCreate(
            selected_modules=("source.sales_inv",),
            requested_capabilities=("ORDER_LOOKUP",),
            source_structures=(
                SourceStructure(
                    source_id="sales-inv",
                    dataset="salesInv",
                    fields=(
                        SourceField(path="_id", data_type="string", key=True),
                        SourceField(path="salesHdr.orderId", data_type="string"),
                    ),
                    fingerprint="sha256:sales-v1",
                ),
            ),
        ),
        "architect",
    )

    assert context.status == "WAITING_FOR_ANSWER"
    assert context.current_question is not None
    assert context.current_question.field_path.endswith("identityPaths")

    answered = await service.answer(
        context.request_id,
        SchemaAnswer(question_id=context.current_question.question_id, value=["_id"]),
    )

    assert answered.status == "REVIEW_READY"
    assert answered.current_question is None
    assert len(answered.commands) == 1
    assert (await service.simulate(answered.request_id))["productionWrites"] == 0


def _orders() -> tuple[SourceOrderRecord, ...]:
    return (
        SourceOrderRecord(
            account="ACCOUNT1",
            order_number="ORDER100",
            customer_id="C1",
            customer_name="One Supply",
            customer_po="PO-7",
            delivery_ticket="DT-11",
            invoice_numbers=("INV-9",),
            tracking_numbers=("TRACK-SHARED",),
            source_revision="rev-1",
            lines=(
                {
                    "lineNumber": "1",
                    "itemNumber": "SKU-1",
                    "description": "Valve",
                    "quantityOrdered": 2,
                    "quantityReturned": 0,
                },
                {
                    "lineNumber": "2",
                    "itemNumber": "SKU-2",
                    "description": "Pipe",
                    "quantityOrdered": 4,
                    "quantityReturned": 1,
                },
            ),
        ),
        SourceOrderRecord(
            account="ACCOUNT2",
            order_number="ORDER100",
            customer_id="C2",
            tracking_numbers=("TRACK-SHARED",),
            source_revision="rev-2",
            lines=({"lineNumber": "1", "itemNumber": "SKU-9", "quantityOrdered": 1},),
        ),
    )


@pytest.mark.asyncio
async def test_partial_anchor_can_resolve_multiple_isolated_full_order_ids() -> None:
    service = OrderSyncService(
        InMemoryOrderSourceGateway(_orders()), InMemoryOrderProjectionStore()
    )

    result = await service.partial(
        PartialSyncRequest(
            anchor=OrderAnchor(type="TRACKING_NUMBER", value="track-shared"),
            release_id="release-1",
            authorization_scope=AuthorizationScope(max_candidates=10),
            idempotency_key="partial-shared-001",
        )
    )

    assert result.status is SyncStatus.RESOLVED
    assert result.full_order_ids == ("ACCOUNT1*ORDER100", "ACCOUNT2*ORDER100")
    assert all(not order.lines for order in result.orders)


@pytest.mark.asyncio
async def test_full_sync_uses_one_order_id_and_hydrates_all_lines_idempotently() -> None:
    service = OrderSyncService(
        InMemoryOrderSourceGateway(_orders()), InMemoryOrderProjectionStore()
    )
    request = FullSyncRequest(
        full_order_id="account1*order100",
        release_id="release-1",
        authorization_scope=AuthorizationScope(accounts=("ACCOUNT1",)),
        idempotency_key="full-account1-order100",
    )

    first = await service.full(request)
    second = await service.full(request)

    assert first.status is SyncStatus.COMPLETED
    assert first.request_id == second.request_id
    assert first.full_order_ids == ("ACCOUNT1*ORDER100",)
    assert len(first.orders) == 1
    assert [line.full_order_line_id for line in first.orders[0].lines] == [
        "ACCOUNT1*ORDER100*1",
        "ACCOUNT1*ORDER100*2",
    ]
    assert first.records_read == 3


def test_v2_router_exposes_governing_api_surface() -> None:
    routes = {
        (route.path, frozenset(route.methods or set()))
        for route in router.routes
        if isinstance(route, APIRoute)
    }

    required = {
        ("/api/v2/configuration/module-schemas", frozenset({"GET"})),
        ("/api/v2/configuration/modules", frozenset({"POST"})),
        ("/api/v2/configuration/releases", frozenset({"POST"})),
        ("/api/v2/configuration/imports", frozenset({"POST"})),
        ("/api/v2/schema-design/requests", frozenset({"POST"})),
        ("/api/v2/order-sync/partial", frozenset({"POST"})),
        ("/api/v2/order-sync/full", frozenset({"POST"})),
        ("/api/v2/order-sync/requests/{request_id}", frozenset({"GET"})),
    }
    assert required <= routes
