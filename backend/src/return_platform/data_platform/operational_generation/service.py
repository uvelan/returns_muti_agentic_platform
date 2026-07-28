import typing
from uuid import UUID

from return_platform.data_platform.operational_generation.apply_models import ExecutionRun
from return_platform.data_platform.operational_generation.apply_service import ApplyService
from return_platform.data_platform.operational_generation.approval import (
    ApprovalRecord,
    create_approval,
    verify_approval,
)
from return_platform.data_platform.operational_generation.authorization import (
    OperationalGenerationPermission,
    require_permission,
)
from return_platform.data_platform.operational_generation.generator import OperationalGenerator
from return_platform.data_platform.operational_generation.models import (
    CollisionPolicy,
    GenerationMode,
    GenerationRequest,
    OperationalGenerationProposal,
    OperationProposal,
    ScenarioType,
)
from return_platform.data_platform.operational_generation.planner import OperationalPlanner
from return_platform.data_platform.operational_generation.rollback_service import RollbackService
from return_platform.data_platform.operational_generation.validator import validate_proposal
from return_platform.data_platform.operational_generation.write_models import OperationalWritePlan
from return_platform.data_platform.schema_registry import SchemaRegistry


class OperationalGenerationService:
    def __init__(
        self,
        generator: OperationalGenerator,
        planner: OperationalPlanner,
        apply_service: ApplyService,
        rollback_service: RollbackService,
        registry: SchemaRegistry,
    ) -> None:
        self.generator = generator
        self.planner = planner
        self.apply_service = apply_service
        self.rollback_service = rollback_service
        self.registry = registry

        self.proposals: dict[str, OperationalGenerationProposal] = {}
        self.plans: dict[UUID, OperationalWritePlan] = {}
        self.approvals: dict[UUID, ApprovalRecord] = {}

    async def generate_proposal(
        self, config: dict[str, typing.Any], actor_permissions: list[str]
    ) -> OperationalGenerationProposal:
        require_permission(actor_permissions, OperationalGenerationPermission.GENERATE)

        import datetime

        req = GenerationRequest(
            asset_ids=tuple(config.get("asset_ids", [])),
            record_count=config.get("record_count", 1),
            deterministic_seed=config.get("deterministic_seed", 42),
            tenant_id=config.get("tenant_id", "default"),
            branch_id=config.get("branch_id"),
            region_id=config.get("region_id"),
            date_from=datetime.datetime.now(datetime.UTC),
            date_to=datetime.datetime.now(datetime.UTC),
            generation_mode=GenerationMode.DETERMINISTIC,
            collision_policy=CollisionPolicy.REJECT,
            scenario_distribution={ScenarioType.POSITIVE: 1},
        )

        proposal = await self.generator.generate_proposal(req)
        self.proposals[proposal.proposal_checksum] = proposal
        return proposal

    def validate_proposal(
        self, proposal_checksum: str, actor_permissions: list[str]
    ) -> dict[str, typing.Any]:
        require_permission(actor_permissions, OperationalGenerationPermission.VALIDATE)
        proposal = self.proposals.get(proposal_checksum)
        if not proposal:
            raise ValueError("Proposal not found")

        from collections import defaultdict

        records_by_asset: dict[str, list[dict[str, typing.Any]]] = defaultdict(list)
        for rec in proposal.records:
            records_by_asset[rec.asset_id].append(rec.values)  # type: ignore

        all_findings = []
        is_denied = False
        is_invalid = False

        for asset_id, recs in records_by_asset.items():
            op_prop = OperationProposal(asset_id=asset_id, records=recs)
            result = validate_proposal(self.registry, op_prop)
            all_findings.extend(result.findings)
            if result.state == "POLICY_DENIED":
                is_denied = True
            elif result.state == "INVALID_RECORD" or result.state == "INVALID_PROPOSAL":
                is_invalid = True

        state = "VALID"
        if is_denied:
            state = "POLICY_DENIED"
        elif is_invalid:
            state = "INVALID_RECORD"

        return {"state": state, "findings": [f.model_dump() for f in all_findings]}

    def plan_proposal(
        self, proposal_checksum: str, plan_salt: str, actor_permissions: list[str]
    ) -> OperationalWritePlan:
        require_permission(actor_permissions, OperationalGenerationPermission.PLAN)
        proposal = self.proposals.get(proposal_checksum)
        if not proposal:
            raise ValueError("Proposal not found")

        plan = self.planner.build_plan(proposal, plan_salt)
        self.plans[plan.plan_id] = plan
        return plan

    def approve_plan(
        self,
        plan_id: UUID,
        proposal_checksum: str,
        target_environment: str,
        actor_id: str,
        actor_permissions: list[str],
    ) -> ApprovalRecord:
        require_permission(actor_permissions, OperationalGenerationPermission.APPROVE)
        plan = self.plans.get(plan_id)
        if not plan:
            raise ValueError("Plan not found")

        proposal = self.proposals.get(proposal_checksum)
        if not proposal:
            raise ValueError("Proposal not found")

        approval = create_approval(
            plan, proposal, actor_id, target_environment, enforce_separation=True
        )
        self.approvals[approval.approval_id] = approval
        return approval

    async def apply_plan(
        self,
        plan_id: UUID,
        approval_id: UUID,
        target_environment: str,
        actor_permissions: list[str],
    ) -> ExecutionRun:
        require_permission(actor_permissions, OperationalGenerationPermission.APPLY_OPERATIONAL)
        plan = self.plans.get(plan_id)
        if not plan:
            raise ValueError("Plan not found")

        approval = self.approvals.get(approval_id)
        if not approval:
            raise ValueError("Approval not found")

        proposal = self.proposals.get(approval.proposal_checksum)
        if not proposal:
            raise ValueError("Proposal not found")

        verify_approval(approval, plan, proposal, target_environment)

        run = await self.apply_service.apply_plan(plan)
        return run

    async def rollback_run(
        self, run_id: UUID, plan_id: UUID, actor_permissions: list[str]
    ) -> ExecutionRun:
        require_permission(actor_permissions, OperationalGenerationPermission.ROLLBACK_OPERATIONAL)
        plan = self.plans.get(plan_id)
        if not plan:
            raise ValueError("Plan not found")

        run = await self.rollback_service.rollback(run_id, plan)
        return run
