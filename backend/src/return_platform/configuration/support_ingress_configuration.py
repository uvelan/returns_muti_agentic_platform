"""What the platform will accept from Support, and how it answers back.

Contracts.md sect. 5 and sect. 10. The released policy for the inbound half of
the bridge: whether the natural-language door is open at all, which intents the
classifier is allowed to return, what happens to messages that arrive while the
door is shut, and the size/rate ceilings the endpoint enforces before anything
durable happens.

Three things here are deliberately configuration rather than code constants.

**`nl_enabled`** is a runtime switch, not a deploy. The contract's parked
lifecycle only means anything if the flip is cheap: a message that arrives while
NL is disabled is *persisted* as `PARKED` and reprocessed in stream order when
the switch goes on, so the operator's decision is reversible and no message is
lost to it. A code constant would make "turn it on" a release.

**`intents`** is the closed set the classifier is scored against. Closed because
an open one is a taxonomy nobody can write a downstream branch for, and released
because the taxonomy is a business vocabulary -- but the closure is enforced in
*code*: anything the model returns outside this set becomes `other`
(contracts.md sect. 5), so a released list can widen what is recognised and can
never widen what is executed.

**`agent_disclosure`** rides the release because sect. 9 requires every
agent-authored Channel B message to carry it. A disclosure line whose text lived
in code would be a legal-ish statement that only engineering can change.

This module owns its own `StrictConfigModel` for the reason
`context_assembly_configuration` documents: `return_configuration` imports this
module for its field, so importing back would be a cycle.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]

#: The contract's intent taxonomy (contracts.md sect. 5), seeded here so a
#: deployment that publishes no `intents` list still classifies against the set
#: the contract froze rather than against nothing.
DEFAULT_INTENTS: tuple[str, ...] = (
    "info_request",
    "rma_issued",
    "label_issued",
    "shipping_instruction",
    "tracking_provided",
    "partial_fulfillment",
    "rejection",
    "acknowledgement",
    "other",
)

#: The member every out-of-set classification collapses to. Named rather than
#: spelled at each use: it is the taxonomy's floor, and a second spelling of it
#: is a branch that silently never runs.
FALLBACK_INTENT: str = "other"

__all__ = [
    "DEFAULT_INTENTS",
    "FALLBACK_INTENT",
    "AgentDisclosureConfiguration",
    "NonBlank",
    "StrictConfigModel",
    "SupportIngressConfiguration",
    "SupportIngressLimits",
    "SupportParkingConfiguration",
]


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SupportParkingConfiguration(StrictConfigModel):
    """What happens to messages that arrive while the NL door is shut.

    Parking is not rejection and never a 409 (contracts.md sect. 5). The
    message is on file, the operator is told once per window rather than once
    per message, and a case that parks more than it should escalates instead of
    accumulating quietly.
    """

    #: How long a parked message is kept before it is a housekeeping concern.
    #: Seconds, like every other duration in this configuration tree.
    retention_seconds: int = Field(default=30 * 24 * 3600, gt=0)
    #: How many parked messages one case may hold before the parking itself is
    #: escalated. A case at quota is not a case with a backlog; it is a case
    #: nobody is reading, and that is an operations fact rather than a counter.
    per_case_quota: int = Field(default=50, gt=0)
    #: One alert per case per window, not one per message. A disabled switch
    #: with live traffic behind it would otherwise page proportionally to the
    #: traffic, which is exactly the signal that gets muted.
    alert_dedupe_window_seconds: int = Field(default=900, gt=0)


class AgentDisclosureConfiguration(StrictConfigModel):
    """Who the agent says it is, on every message it writes to Support.

    Contracts.md sect. 9: the disclosure is attached to templated sends and is
    carried as a shared prompt-section anchor on every `support.*` task, so an
    agent-authored message cannot leave the platform reading as a person's.
    """

    display_name: NonBlank = "Returns Assistant"
    disclosure_line: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=512)
    ] = (
        "This message was written by the Returns Assistant, an automated agent "
        "working on this return case. A branch associate reviews and can answer "
        "anything it cannot."
    )


class SupportIngressLimits(StrictConfigModel):
    """The ceilings the ingress endpoint applies before anything is persisted.

    Checked in the handler, ahead of the store, because the point of a size
    limit is to refuse work rather than to record having done it.
    """

    #: Longest inbound message body accepted, in characters. Refused with 413
    #: rather than truncated: a truncated support message is a message whose
    #: tracking number may be the part that was cut.
    max_body_characters: int = Field(default=16_000, gt=0)
    #: Inbound messages accepted per case per window. A per-case rather than a
    #: per-principal budget, because the resource being protected is the case's
    #: analysis pipeline, and one transport speaking for many principals is the
    #: ordinary shape.
    max_messages_per_case_per_window: int = Field(default=60, gt=0)
    rate_window_seconds: int = Field(default=60, gt=0)
    #: Longest `external_message_id` / `transport_id` accepted. Half of the
    #: dedupe identity; an unbounded one is an unbounded index key.
    max_identifier_characters: int = Field(default=256, gt=0)


class SupportIngressConfiguration(StrictConfigModel):
    """The released ingress policy (contracts.md sect. 5, sect. 10).

    Defaulted throughout so a release cut before this block still loads, and
    the defaults are the closed ones: the NL door starts **shut**. That is not
    caution for its own sake -- an ingress path that opens itself on deploy
    would begin classifying live support traffic through a model before any
    operator decided it should, and the parked lifecycle exists precisely so
    that starting shut costs nothing.
    """

    #: Whether the natural-language ingress endpoint processes or parks.
    #: The structured `.../return-outcome` path is unaffected by this and is
    #: always on (contracts.md sect. 5, DR-2).
    nl_enabled: bool = False
    #: The closed classification taxonomy. Out-of-set answers become
    #: `FALLBACK_INTENT` in code, whatever this list says.
    intents: tuple[NonBlank, ...] = DEFAULT_INTENTS
    parking: SupportParkingConfiguration = Field(default_factory=SupportParkingConfiguration)
    #: The prompt-section key carrying the do-not-mix framing used when one
    #: inbound message fans out to several return records. A key rather than
    #: the text, so the wording lives with the other prompt text and this block
    #: names which of it applies.
    multi_record_framing_prompt_key: NonBlank = "support-multi-record-do-not-mix"
    agent_disclosure: AgentDisclosureConfiguration = Field(
        default_factory=AgentDisclosureConfiguration
    )
    #: Outbound text by template id, under sect. 8's interpolation-only
    #: grammar. Config-templated is the *default* composition path for
    #: Channel B text (contracts.md sect. 9); a model composes only where a
    #: deployment has said so.
    outbound_templates: dict[NonBlank, str] = Field(default_factory=dict)
    limits: SupportIngressLimits = Field(default_factory=SupportIngressLimits)

    def normalized_intents(self) -> frozenset[str]:
        """The taxonomy as the classifier's guard reads it.

        `FALLBACK_INTENT` is unioned in rather than assumed present: a release
        that omits `other` from its list must still be able to express "this
        message is none of the above", and the alternative -- refusing the
        release -- would make the floor of the taxonomy a thing an operator can
        delete.
        """
        return frozenset(self.intents) | {FALLBACK_INTENT}
