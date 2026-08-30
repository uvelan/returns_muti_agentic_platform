"""What a case's assembled reasoning context is allowed to contain.

Contracts.md sect. 10. The policy half of `assemble_case_context`: which fact
names are always present whatever the budget says, how large the budget is,
which tokenizer measures it, and when the budget's pressure should trigger a
compaction summary.

The tokenizer version is configuration rather than a code constant for the
reason the contract gives -- it is pinned *with* `promptVersion`. A context
measured by one tokenizer and consumed by a model that counts with another is
a budget that is not a budget, and the failure is silent right up to the
truncation. Pinning it in the release means the pair moves together or not at
all.

This module owns its own `StrictConfigModel` rather than importing the one in
`return_configuration`. That is not a preference: `return_configuration`
imports *this* module for its field, so importing back would be a cycle. The
definition is two lines and identical, and the alternative -- a shared base
module -- is a refactor of every config model in the tree, which is not this
slice's to make.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: One million is 1.0. Fractions are integers throughout this codebase's
#: configuration so that a released value is exact rather than a float whose
#: stored form depends on who serialized it. Imported from the consumer rather
#: than restated here: the assembler divides by it, and two definitions of one
#: constant is how a released 800_000 quietly becomes 0.8 of the wrong thing.
from return_platform.platform.reasoning.case_context import MILLIONTHS

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]

__all__ = [
    "MILLIONTHS",
    "ContextAssemblyConfiguration",
    "ContextCompactionConfiguration",
    "NonBlank",
    "StrictConfigModel",
]


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextCompactionConfiguration(StrictConfigModel):
    """When to summarise, and which pinned task does it.

    Compaction is a **separate write-once step** (contracts.md sect. 10): this
    block says when it should be asked for, never how to produce it, and the
    assembler consumes whatever summary is already persisted rather than
    generating one. An assembler that could generate would make the context a
    function of when it was assembled.
    """

    #: Fraction of the token budget at which a compaction summary should be
    #: requested. The default leaves a fifth of the budget as headroom, which
    #: is the room the next few facts need before anything has to be omitted.
    trigger_fraction_millionths: int = Field(default=800_000, ge=0, le=MILLIONTHS)
    #: The ai_gateway task that writes the summary. Pinned so that the summary
    #: on a case names the release that produced it.
    summary_task_id: NonBlank = "support.context.summarize.v1"


class ContextAssemblyConfiguration(StrictConfigModel):
    """The assembly policy, released with everything else.

    Defaulted throughout so a release cut before this block still loads, and
    the defaults are the conservative ones: no pinned names beyond what a
    caller adds, a budget large enough for an ordinary case, and the tokenizer
    the estimator actually implements.
    """

    #: Fact names that are always in the assembled context, whatever the
    #: budget. A pinned name that gets trimmed is the failure this list exists
    #: to prevent: the model reasons without the one fact the operator was
    #: certain it had seen.
    pinned_fact_names: tuple[NonBlank, ...] = ()
    #: The ceiling, measured by `tokenizer_version`.
    token_budget: int = Field(default=8_000, gt=0)
    #: Pinned with `promptVersion`. `assemble_case_context` refuses a version
    #: it cannot measure rather than falling back to a different estimator --
    #: a silent fallback is the one outcome a pin exists to rule out.
    tokenizer_version: NonBlank = "wordpiece-approx.v1"
    compaction: ContextCompactionConfiguration = Field(
        default_factory=ContextCompactionConfiguration
    )
