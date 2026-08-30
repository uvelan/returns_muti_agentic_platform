"""The review gate's released settings (contracts.md sect. 6 and sect. 10).

Two questions, and they are deliberately separate blocks:

* **`request_grouping`** decides how many support *requests* one case produces,
  and therefore how many reviews. It is a declarative enum, never an
  expression, for the same reason sect. 8's selectors are: a grouping rule that
  could compute would be scripting from configuration, and this one decides how
  many humans get asked how many questions.
* **`template_review`** decides what happens to each of those requests while a
  person is looking at it -- whether the gate runs at all, how long it waits,
  how often it reminds, and what the deadline does.

`on_timeout` is the field with teeth. `hold` is the default because it is the
only value that cannot send a message nobody read; `auto_send` exists because a
deployment may reasonably decide that a well-formed draft with no gaps is
better sent than parked, and `escalate` is for one that wants neither. **An
unresolved required gap forces hold or escalate regardless of this setting**
(contracts.md sect. 6) -- that rule is in the workflow, not here, because a
configuration value that could be overridden by another configuration value is
not a setting, it is a suggestion.

Nothing in this module is read at *render* time. The case's pinned
`configurationReleaseId` is the version pin for the template (sect. 8) and the
timings are pinned at workflow start the way `ReturnCaseTimingConfiguration` is
-- a release that moves `review_wait_seconds` applies to cases started after
it, never to a countdown an associate is already watching.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MAX_REVIEW_REMINDERS",
    "RequestGrouping",
    "SupportGateConfiguration",
    "TemplateReviewConfiguration",
    "TemplateReviewTimeoutPolicy",
]

#: The ceiling on `max_reminders`. Per *case*, not per review (DR-7): one
#: cadence, one deadline, and the reminder text names every pending review. A
#: cap at all because the reminder is a message to a human queue, and a
#: misconfigured release must not be able to turn the gate into a mailer.
MAX_REVIEW_REMINDERS: Final[int] = 20


class RequestGrouping(StrEnum):
    """How a case's return records are split into support requests.

    A closed enum, matching contracts.md sect. 6 exactly. `by_shipping_mode`
    and `by_ship_from` are declared here because the contract declares them and
    the grouping key is read from this value; a release selecting one that the
    grouping code does not implement is refused at validation rather than
    silently collapsing to `one_per_case`, which would send one message where
    the operator asked for two.
    """

    ONE_PER_CASE = "one_per_case"
    BY_SHIPPING_MODE = "by_shipping_mode"
    BY_SHIP_FROM = "by_ship_from"


class TemplateReviewTimeoutPolicy(StrEnum):
    """What the deadline does when nobody has answered.

    `HOLD` parks the review at `HELD_FOR_OPERATIONS` and the case at its park
    reason. `ESCALATE` does the same and raises the operations alert. `AUTO_SEND`
    approves as the reserved `SYSTEM` actor -- through the *same* transition,
    with the same refusals, so an unresolved conflict or a pending revision
    holds the system exactly as it holds a person.
    """

    AUTO_SEND = "auto_send"
    HOLD = "hold"
    ESCALATE = "escalate"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TemplateReviewConfiguration(_Strict):
    """The gate's cadence for one case.

    Every duration is in *working* seconds and is resolved through
    `resolve_business_deadline`, never by local arithmetic: the deadline an
    associate sees has to survive a weekend the same way the support-response
    deadline does (non-negotiable #9, DR-1's exception is the physical bay wait
    and nothing else).
    """

    #: DR-4: the gate is on by default in `production.yaml`. `False` is the
    #: pre-gate behaviour exactly -- `_open_support` takes its straight-through
    #: path and composes the handoff, byte-identically.
    enabled: bool = True
    #: How long the case waits for a reviewer, in working seconds. The default
    #: mirrors `support_response_wait_seconds`: the same working day the
    #: platform already gives Support to answer.
    review_wait_seconds: int = Field(default=28_800, ge=60, le=30 * 24 * 60 * 60)
    #: How often to remind, in working seconds.
    reminder_interval_seconds: int = Field(default=7_200, ge=60, le=7 * 24 * 60 * 60)
    #: A **case** total, not a per-review one (DR-7).
    max_reminders: int = Field(default=3, ge=0, le=MAX_REVIEW_REMINDERS)
    on_timeout: TemplateReviewTimeoutPolicy = TemplateReviewTimeoutPolicy.HOLD


class SupportGateConfiguration(_Strict):
    """`support_gate` on `ReturnPlatformConfiguration`.

    Defaulted whole, so a release cut before this block existed still loads.
    The default is the *gate on*, which is DR-4 and is the deliberate choice:
    an older release must not silently become one that sends unreviewed
    messages to Support. The un-patched-history path is what preserves
    old *executions*, and that is a workflow-versioning question rather than a
    configuration one.
    """

    request_grouping: RequestGrouping = RequestGrouping.ONE_PER_CASE
    template_review: TemplateReviewConfiguration = Field(
        default_factory=TemplateReviewConfiguration
    )
