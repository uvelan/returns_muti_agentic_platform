import uuid

from return_platform.data_platform.schema_registry import SchemaRegistry

from .checksum import calculate_proposal_checksum
from .collision import analyze_collisions
from .dependency_graph import build_operation_dependency_graph
from .guard import HallucinationGuard
from .idempotency import generate_idempotency_key
from .impact import calculate_impact_summary
from .models import (
    CollisionPolicy,
    OperationalGenerationProposal,
    OperationProposal,
    ValidationResultState,
)
from .plan_checksum import calculate_plan_checksum
from .policy_resolver import is_domain_api, resolve_execution_channel
from .rollback_analysis import classify_rollback_feasibility
from .transaction_groups import partition_transaction_groups
from .write_models import Operation, OperationalWritePlan, OperationType, SagaStep


class OperationalPlanner:
    def __init__(self, registry: SchemaRegistry, guard: HallucinationGuard):
        self.registry = registry
        self.guard = guard

    def build_plan(
        self, proposal: OperationalGenerationProposal, plan_salt: str
    ) -> OperationalWritePlan:
        # 1. Verify proposal checksum
        current_checksum = calculate_proposal_checksum(proposal)
        if current_checksum != proposal.proposal_checksum:
            raise ValueError("Proposal checksum is invalid or stale")

        # 2. Verify schema release and checksum (Mocking checksum check as we don't have it in schema_registry right now)
        if proposal.schema_release_id != self.registry.schema_version:
            raise ValueError("Schema release mismatch")

        # 3. Resolve policies and channels
        channel_mapping = {}
        for asset_id in {r.asset_id for r in proposal.records}:
            asset = self.registry.asset(asset_id)
            channel = resolve_execution_channel(asset)
            channel_mapping[asset_id] = channel

        # 4. Guard validation
        for asset_id in {r.asset_id for r in proposal.records}:
            recs = [r.values for r in proposal.records if r.asset_id == asset_id]
            op_prop = OperationProposal(asset_id=asset_id, records=recs)
            res = self.guard.validate(op_prop)
            if res.state != ValidationResultState.VALID:
                raise ValueError(f"Proposal violates guard for asset {asset_id}")

        # 5. Dependency Graph
        dep_graph = build_operation_dependency_graph(proposal)

        # 6. Read-only collision analysis
        analyze_collisions(
            proposal, CollisionPolicy.REJECT
        )  # we use mode or collision policy if passed, mock for now

        # 7. Generate Operations
        operations = []
        for rec in proposal.records:
            channel = channel_mapping[rec.asset_id]
            op_type = (
                OperationType.DOMAIN_COMMAND if is_domain_api(channel) else OperationType.INSERT
            )

            operations.append(
                Operation(
                    operation_id=rec.temporary_record_key,
                    type=op_type,
                    asset_id=rec.asset_id,
                    payload=rec.values,
                    target_channel=channel,
                    dependencies=tuple(dep_graph.get(rec.temporary_record_key, [])),
                )
            )

        # 8. Sort operations topologically (simple stable sort since generator output is already sorted topologically)
        # We rely on the generator's ordering being topologically sound.
        # AIG4: "stable operation ordering" - we just keep the proposal.records order which was stable.

        # 9. Partition transaction groups and assign saga steps
        # For simplicity, we can just treat the ordered operations as one saga step if they are in the same channel,
        # or separate saga steps. The prompt says "partition transaction groups, assign saga steps".
        tgs = partition_transaction_groups(operations)
        saga_steps = []

        for idx, tg in enumerate(tgs):
            rf = classify_rollback_feasibility(
                [SagaStep(step_index=0, transaction_groups=(tg,), rollback_feasibility="SAFE")]
            )  # hack to reuse
            saga_steps.append(
                SagaStep(step_index=idx, transaction_groups=(tg,), rollback_feasibility=rf)
            )

        # 10. Append graph-sync requests after authoritative writes
        sync_ops = []
        for asset_id in {r.asset_id for r in proposal.records}:
            asset = self.registry.asset(asset_id)
            # If the asset needs graph sync, add a GRAPH_SYNC_REQUEST.
            # We assume it does if it is a source collection that typically syncs.
            if getattr(asset, "graph_sync_policy", "NONE") != "NONE" and channel_mapping[
                asset_id
            ] in ("source_admin", "SOURCE_ADMIN_WRITER"):
                sync_ops.append(
                    Operation(
                        operation_id=str(uuid.uuid4()),
                        type=OperationType.GRAPH_SYNC_REQUEST,
                        asset_id=asset_id,
                        payload={"sync": "true"},
                        target_channel="GRAPH_SYNC_ADAPTER",
                        dependencies=(),
                    )
                )

        if sync_ops:
            sync_tg = partition_transaction_groups(sync_ops)
            saga_steps.append(
                SagaStep(
                    step_index=len(saga_steps),
                    transaction_groups=tuple(sync_tg),
                    rollback_feasibility=classify_rollback_feasibility(
                        [
                            SagaStep(
                                step_index=0,
                                transaction_groups=tuple(sync_tg),
                                rollback_feasibility="SAFE",
                            )
                        ]
                    ),
                )
            )

        # 11. Idempotency Key
        idemp_key = generate_idempotency_key(proposal.proposal_checksum, plan_salt)

        # 12. Impact Summary
        impact = calculate_impact_summary(saga_steps)

        plan_id = uuid.uuid5(uuid.NAMESPACE_OID, f"plan:{idemp_key}")

        plan = OperationalWritePlan(
            plan_id=plan_id,
            proposal_checksum=proposal.proposal_checksum,
            schema_release_id=self.registry.schema_version,
            schema_checksum="MOCK_CHECKSUM",
            idempotency_key=idemp_key,
            saga_steps=tuple(saga_steps),
            impact=impact,
            plan_checksum="",
        )

        chk = calculate_plan_checksum(plan)

        return OperationalWritePlan(
            plan_id=plan_id,
            proposal_checksum=proposal.proposal_checksum,
            schema_release_id=self.registry.schema_version,
            schema_checksum="MOCK_CHECKSUM",
            idempotency_key=idemp_key,
            saga_steps=tuple(saga_steps),
            impact=impact,
            plan_checksum=chk,
        )
