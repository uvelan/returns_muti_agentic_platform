"""The Copilot read contract, and the completion and stage semantics over it.

This package is the contract freeze of plan sect. 6.2-6.4 and 6.6. It is **pure**
-- no IO, no database, no workflow client, no model call -- so the rules can be
tested on their own, which the audit found nothing had been.

```text
CaseProjectionState        what the platform has computed about one case
  -> derive_copilot_stage    which mode the Copilot renders          (sect. 6.6)
  -> resolve_completion      awaiting / businessComplete / isTerminal (sect. 6.4)
  -> project_case            both, assembled into the wire contract   (sect. 6.3)
```

Deliberately **not** in scope here, and deliberately not imported: persistence,
the HTTP route, and the migration. What assembles a `CaseProjectionState` from
Mongo is the repository's problem; a projection that knew how to load itself
would be a repository nobody could test without one.
"""

from return_platform.operations.case_projection.completion import (
    DEFAULT_RETURN_METHOD_REQUIREMENTS,
    POLICY_GATE_STATE_FACT,
    POLICY_GATE_SUSPENDED,
    REQUIREMENT_DIMENSIONS,
    UNRESOLVED_DIMENSIONS,
    CompletionAssessment,
    ReturnMethodRequirement,
    ReturnMethodRequirementTable,
    effective_decision,
    is_terminal_status,
    policy_gate_suspended,
    resolve_completion,
    resolve_method_requirements,
)
from return_platform.operations.case_projection.contract import (
    ApprovedItemProjection,
    CaseFactProjection,
    CaseProjection,
    CaseProjectionState,
    ConfirmedOrderProjection,
    CustomerProjection,
    PickupProjection,
    PolicyEvaluationProjection,
    PolicyOverrideProjection,
    ReturnArtifactProjection,
    ReturnRecordProjection,
    SelectedItemProjection,
    SettlementProjection,
    ShipmentProjection,
    SupportProjection,
    WarehouseProjection,
)
from return_platform.operations.case_projection.projection import project_case
from return_platform.operations.case_projection.stage import (
    DEFAULT_STAGE_DERIVATION_POLICY,
    StageDerivationPolicy,
    StageTransition,
    classify_stage_transition,
    derive_copilot_stage,
    stage_regressed,
)
from return_platform.operations.case_projection.vocabulary import (
    AWAITING_DIMENSION_ORDER,
    COMPLETION_FORBIDDING_STATUSES,
    STAGE_LIFECYCLE_ORDER,
    STAGE_PRECEDENCE,
    STAGE_PRECEDENCE_LEVELS,
    SUCCESSFUL_TERMINAL_STATUSES,
    TERMINAL_RETURN_CASE_STATUSES,
    UNKNOWN_RETURN_METHOD,
    UNSUCCESSFUL_TERMINAL_STATUSES,
    AwaitingDimension,
    CopilotStage,
    NormalizedReturnMethod,
    ReturnArtifactType,
    ReturnCaseStatus,
    SettlementStatus,
    ShipmentStatus,
    StageRegressionReason,
    SupportOutcome,
    awaiting_dimension_rank,
    stage_rank,
)

__all__ = [
    "AWAITING_DIMENSION_ORDER",
    "COMPLETION_FORBIDDING_STATUSES",
    "DEFAULT_RETURN_METHOD_REQUIREMENTS",
    "DEFAULT_STAGE_DERIVATION_POLICY",
    "POLICY_GATE_STATE_FACT",
    "POLICY_GATE_SUSPENDED",
    "REQUIREMENT_DIMENSIONS",
    "STAGE_LIFECYCLE_ORDER",
    "STAGE_PRECEDENCE",
    "STAGE_PRECEDENCE_LEVELS",
    "SUCCESSFUL_TERMINAL_STATUSES",
    "TERMINAL_RETURN_CASE_STATUSES",
    "UNKNOWN_RETURN_METHOD",
    "UNRESOLVED_DIMENSIONS",
    "UNSUCCESSFUL_TERMINAL_STATUSES",
    "ApprovedItemProjection",
    "AwaitingDimension",
    "CaseFactProjection",
    "CaseProjection",
    "CaseProjectionState",
    "CompletionAssessment",
    "ConfirmedOrderProjection",
    "CopilotStage",
    "CustomerProjection",
    "NormalizedReturnMethod",
    "PickupProjection",
    "PolicyEvaluationProjection",
    "PolicyOverrideProjection",
    "ReturnArtifactProjection",
    "ReturnArtifactType",
    "ReturnCaseStatus",
    "ReturnMethodRequirement",
    "ReturnMethodRequirementTable",
    "ReturnRecordProjection",
    "SelectedItemProjection",
    "SettlementProjection",
    "SettlementStatus",
    "ShipmentProjection",
    "ShipmentStatus",
    "StageDerivationPolicy",
    "StageRegressionReason",
    "StageTransition",
    "SupportOutcome",
    "SupportProjection",
    "WarehouseProjection",
    "awaiting_dimension_rank",
    "classify_stage_transition",
    "derive_copilot_stage",
    "effective_decision",
    "is_terminal_status",
    "policy_gate_suspended",
    "project_case",
    "resolve_completion",
    "resolve_method_requirements",
    "stage_rank",
    "stage_regressed",
]
