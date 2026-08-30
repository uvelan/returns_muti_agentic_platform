"""How the platform answers a question Support asked (contracts.md sect. 9, 10).

The released policy for the *outbound* half of the bridge: how sure the resolver
must be before it answers from facts or from the graph, which tools a validated
intent makes eligible, whether an answer goes out on its own or waits for an
associate's approval, and how much model spend one case may consume before the
whole thing escalates instead.

Four things here are configuration rather than code, each for a stated reason.

**The two confidence thresholds** are millionths, matching the gateway's
`confidenceMillionths` envelope so that the released number and the measured
number are the same unit and no conversion sits between them. They are released
because "sure enough to tell Support" is a business tolerance, not an
engineering constant -- and they default *high* (900,000), because the failure
this slice must not have is a confident-sounding wrong answer sent to Support
under the platform's own name.

**`tool_bindings`** maps a validated intent to the capabilities it makes
eligible. It defaults **empty**, which means no tool is eligible for any intent
and the ladder's tool rung refuses every time. That is the closed default the
contract's boundary implies: a tool a deployment has not bound is a tool that
cannot run, and "config-only tool addition" (sect. 9) is precisely the act of
binding an implementation the build already has.

**`reply_gate`** defaults to `review_required` for every intent, per sect. 9.
`auto_reply` is a per-intent opt-in, never a default and never a global.

**`per_case_llm_budget`** is a count of model invocations one case may spend on
resolution. Exhaustion is not a silent stop: it writes
`support_resolver_budget_exhausted` and escalates to an associate, so a case
that outruns its budget becomes visible work rather than an unanswered message.

This module owns its own `StrictConfigModel` for the reason
`support_ingress_configuration` documents: `return_configuration` imports this
module for its field, so importing back would be a cycle.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from return_platform.configuration.support_ingress_configuration import FALLBACK_INTENT
from return_platform.platform.capabilities.tool_schemas import (
    UnknownInputSchemaError,
    known_input_schema_refs,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]

MILLIONTHS = 1_000_000

__all__ = [
    "AUTO_REPLY",
    "MILLIONTHS",
    "REVIEW_REQUIRED",
    "NonBlank",
    "ReplyGateConfiguration",
    "StrictConfigModel",
    "SupportResolverConfiguration",
    "ToolBindingConfiguration",
]


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


#: The two gate modes. A constrained string on the fields below rather than a
#: `StrEnum`, so that a release naming a third mode fails to *parse* -- with the
#: two legal spellings in the error -- instead of falling through to a branch
#: that treats "anything not auto_reply" as review and quietly works.
REVIEW_REQUIRED = "review_required"
AUTO_REPLY = "auto_reply"

GateMode = Annotated[str, Field(pattern=r"^(review_required|auto_reply)$")]


class ToolBindingConfiguration(StrictConfigModel):
    """One validated intent's eligibility for one capability (contracts.md sect. 9).

    Read it as a sentence: *when classification returns `intent`, the tool
    `tool_id` becomes eligible; it is implemented by whatever has published
    `contract` for `capability` in the `CapabilityRegistry`; its arguments must
    satisfy `input_schema_ref`; and it authenticates through
    `credential_binding_id`.*

    Every one of those five is a **reference**. None of them is a value, an
    expression, or a template. That is what keeps a released binding from being
    a program: the strongest thing a deployment can say here is "this existing
    implementation is now reachable for this intent", which is exactly what
    sect. 9 calls a config-only tool addition.

    `credential_binding_id` names a `CredentialBindingConfiguration.profile_key`.
    The credential's *value* is resolved platform-side at invocation and is
    never placed in agent state, a prompt, a checkpoint or a log -- see
    `tool_router.py`, where the executor receives the id and the resolution
    happens behind it.
    """

    #: The tool's stable name. Distinct from `capability` because one capability
    #: can back several differently-shaped tools, exactly as the registry is
    #: keyed by `(capability, contract)` rather than by capability alone.
    tool_id: NonBlank
    #: Which validated intents make this tool eligible. Non-empty: a binding
    #: eligible for no intent is a binding that can never be selected, and
    #: shipping one would make the eligibility map look larger than it is.
    intents: tuple[NonBlank, ...] = Field(min_length=1)
    #: A `CapabilityName` value. Held as a string rather than the enum so that a
    #: release naming a capability this build does not have is a *routing*
    #: refusal with a clear message, not a parse failure that blocks the whole
    #: release from loading.
    capability: NonBlank
    #: The contract class name published for that capability.
    contract: NonBlank
    description: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)
    ]
    #: An entry in `platform/capabilities/tool_schemas.py`. Validated below.
    input_schema_ref: NonBlank
    credential_binding_id: NonBlank | None = None

    @model_validator(mode="after")
    def input_schema_must_be_implemented(self) -> ToolBindingConfiguration:
        """Refuse at parse time, not at the first support question.

        A binding naming a schema this build does not implement is a binding
        that can only ever refuse. Discovering that when a real question arrives
        would spend a case's time on an answer that was never possible.
        """
        if self.input_schema_ref not in known_input_schema_refs():
            raise UnknownInputSchemaError(self.input_schema_ref)
        return self


class ReplyGateConfiguration(StrictConfigModel):
    """Whether an answer goes out on its own, per intent (contracts.md sect. 9).

    `default` is the floor and `per_intent` is the exception list. Stated that
    way round because the safe value is the one that applies to intents nobody
    thought about -- including the ones a later release adds to the taxonomy.
    """

    default: GateMode = REVIEW_REQUIRED
    per_intent: dict[NonBlank, GateMode] = Field(default_factory=dict)

    def mode_for(self, intent: str) -> str:
        """The gate for one intent. Unknown intents get `default`, by design."""
        return self.per_intent.get(intent, self.default)

    def requires_review(self, intent: str) -> bool:
        return self.mode_for(intent) != AUTO_REPLY


class SupportResolverConfiguration(StrictConfigModel):
    """The released resolution policy (contracts.md sect. 9, sect. 10).

    Defaulted throughout so a release cut before this block still loads, and
    every default is the conservative one: high thresholds, no eligible tools,
    review required, a finite budget.
    """

    #: How sure a fact-derived answer must be before it may be given. Below
    #: this, the ladder does not "try harder with the same evidence" -- it
    #: descends a rung, and if it runs out of rungs it escalates.
    fact_confidence_millionths: int = Field(default=900_000, ge=0, le=MILLIONTHS)
    #: The same tolerance for a graph-derived answer.
    graph_confidence_millionths: int = Field(default=900_000, ge=0, le=MILLIONTHS)
    tool_bindings: tuple[ToolBindingConfiguration, ...] = ()
    reply_gate: ReplyGateConfiguration = Field(default_factory=ReplyGateConfiguration)
    #: Whether an answered clarification pushes the review deadline out again.
    #: True by default: the associate answered promptly and the clock they were
    #: racing was started by a question the platform asked *them*.
    clarification_resets_deadline: bool = True
    #: Model invocations one case may spend on resolution, across all its
    #: support questions. Exhaustion writes a fact and escalates.
    per_case_llm_budget: int = Field(default=12, gt=0)
    #: Which classified intents reach the resolution ladder at all.
    #:
    #: **`info_request` alone by default**, and that is a substantive choice
    #: rather than a conservative one. Of sect. 5's closed taxonomy --
    #: `info_request, rma_issued, label_issued, shipping_instruction,
    #: tracking_provided, partial_fulfillment, rejection, acknowledgement,
    #: other` -- `info_request` is the only member in which Support is *asking*
    #: rather than *telling*. The other seven are Support informing the platform,
    #: and V2's extraction already commits what they say; running a ladder over
    #: one of them would compose a reply to a statement.
    #:
    #: Released rather than hardcoded because "which questions the platform tries
    #: to answer by itself" is exactly the kind of reach a deployment should be
    #: able to narrow to nothing (`()` disables resolution entirely) or widen
    #: deliberately -- not a constant in a dispatcher.
    trigger_intents: tuple[NonBlank, ...] = ("info_request",)

    @model_validator(mode="after")
    def the_fallback_intent_cannot_trigger(self) -> SupportResolverConfiguration:
        """`other` may not be a trigger intent, and this is refused at parse time.

        `other` is not a classification; it is `coerce_intent`'s sink for
        *everything the classifier did not recognise*. Triggering on it would
        run the ladder -- and spend the model budget -- on every unclassifiable
        message on every case, which is the opposite of the closed set sect. 5
        exists to keep closed. A release that names it is refused with this
        sentence rather than discovered as a bill.
        """
        if FALLBACK_INTENT in self.trigger_intents:
            raise ValueError(
                f"{FALLBACK_INTENT!r} cannot be a trigger intent: it is the sink for every "
                "classification the released taxonomy does not recognise, so triggering on "
                "it triggers on everything"
            )
        return self

    def bindings_for_intent(self, intent: str) -> tuple[ToolBindingConfiguration, ...]:
        """Eligible bindings for a validated intent, in declaration order.

        Declaration order, not sorted -- the order a release lists bindings in
        is the preference an operator expressed, and an alphabet is not that.
        """
        return tuple(binding for binding in self.tool_bindings if intent in binding.intents)
