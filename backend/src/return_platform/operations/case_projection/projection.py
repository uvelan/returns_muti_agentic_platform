"""`project_case` -- the state plus the four values derived from it.

The one sanctioned way to build a `CaseProjection`. Everything it adds is a
function of what it is given, computed here once, so that a handler cannot
assemble a case whose `stage` and `returnRecords` disagree.

Causal order, and never the reverse:

```text
requirements satisfied -> businessComplete -> workflow transitions COMPLETED
                       -> isTerminal
```

An earlier draft made completion require a terminal workflow while keeping the
workflow alive until completion, which deadlocks. Nothing here reads the
workflow: `isTerminal` is membership of a set of persisted statuses, and
`businessComplete` is computed from the case's own contents, so the projection
can say "this return is done" before anything has been told.

`requirements` is passed in and has no default, for the reason
`resolve_completion` states: the table is the operator's, released in
`return_policy.return_method_requirements`, and a default here would let a
caller that never reached the configuration close cases from a code constant
instead. It had one until the workflow's `_assess_completion` was given the
released table, and while it did, the workflow and the API were computing "is
this return finished" from two different tables that only happened to agree.
"""

from __future__ import annotations

from return_platform.operations.case_projection.completion import (
    ReturnMethodRequirementTable,
    resolve_completion,
)
from return_platform.operations.case_projection.contract import (
    CaseProjection,
    CaseProjectionState,
)
from return_platform.operations.case_projection.stage import (
    DEFAULT_STAGE_DERIVATION_POLICY,
    StageDerivationPolicy,
    derive_copilot_stage,
)

__all__ = ["project_case"]


def project_case(
    state: CaseProjectionState,
    *,
    requirements: ReturnMethodRequirementTable,
    stage_policy: StageDerivationPolicy = DEFAULT_STAGE_DERIVATION_POLICY,
) -> CaseProjection:
    """One case, with `stage`, `awaiting`, `businessComplete` and `isTerminal` derived.

    `requirements` is the operator's released table and there is no default.
    Build it with `build_return_method_requirement_table(configuration)`; a
    caller that cannot reach a configuration has to say so rather than project.
    """
    completion = resolve_completion(state, requirements=requirements)
    return CaseProjection(
        **state.model_dump(),
        stage=derive_copilot_stage(state, policy=stage_policy),
        awaiting=completion.awaiting,
        businessComplete=completion.business_complete,
        isTerminal=completion.is_terminal,
    )
