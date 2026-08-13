"""The shared proposal kernel: one inbox, one lifecycle, three kinds of change."""

from return_platform.platform.governance.errors import (
    ForbiddenProposalKey,
    GovernanceError,
    InvalidProposalTransition,
    ProposalConcurrentModification,
    ProposalIntegrityError,
    UnknownProposal,
)
from return_platform.platform.governance.kernel import NoActivatorRegistered, ProposalKernel
from return_platform.platform.governance.key_policy import (
    KeyPolicy,
    KeyPolicyDecision,
    PermittedKey,
    evaluate_keys,
    policy_for,
    resolve_improvement_key,
)
from return_platform.platform.governance.ports import (
    ActivationReceipt,
    GovernanceAuditPort,
    ProposalActivationPort,
    ProposalStorePort,
)
from return_platform.platform.governance.proposal import (
    Proposal,
    ProposalDiffEntry,
    ProposalStatus,
    ProposalType,
    RiskLevel,
    diff_documents,
)
from return_platform.platform.governance.store import (
    GOVERNANCE_PROPOSALS,
    SystemStoreProposalStore,
)

__all__ = [
    "GOVERNANCE_PROPOSALS",
    "ActivationReceipt",
    "ForbiddenProposalKey",
    "GovernanceAuditPort",
    "GovernanceError",
    "InvalidProposalTransition",
    "KeyPolicy",
    "KeyPolicyDecision",
    "NoActivatorRegistered",
    "PermittedKey",
    "Proposal",
    "ProposalActivationPort",
    "ProposalConcurrentModification",
    "ProposalDiffEntry",
    "ProposalIntegrityError",
    "ProposalKernel",
    "ProposalStatus",
    "ProposalStorePort",
    "ProposalType",
    "RiskLevel",
    "SystemStoreProposalStore",
    "UnknownProposal",
    "diff_documents",
    "evaluate_keys",
    "policy_for",
    "resolve_improvement_key",
]
