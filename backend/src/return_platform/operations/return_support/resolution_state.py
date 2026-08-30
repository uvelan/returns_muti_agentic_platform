"""The resolution ladder's checkpointed state, and what may be in it.

Contracts.md sect. 9. One LangGraph checkpoint's state for one attempt at
answering one inbound support question.

## Why this file is separate from `resolution_ladder.py`

The same reason `order_agent/state.py` is separate from `graph.py`: the
allowlist and the schema must be provably the same set, and
`test_support_resolver_state.py` proves it with a pure, fixture-free equality.
A schema that lived next to the nodes would be edited in the same breath as a
node that needed "just one more field", which is exactly the edit the
allowlist exists to make visible.

## The rule every field here obeys

Each key is one of three things and never a fourth:

* **a pinned identifier** the platform issued (`case_id`, `support_event_id`,
  `configuration_release_id`, `run_id`);
* **a bounded, schema-constrained value the ladder itself produced** -- a
  resolution attempt is `{answer_text, confidence_millionths, ...}` under a
  declared response schema, never a raw provider payload;
* **a counter or a reference** (`llm_invocations_used`, `consumed_fact_ids`,
  `tool_result_ref`).

Two things are deliberately **absent**, and their absence is asserted:

* **No credential, and no shape that could carry one.** The tool rung
  checkpoints `tool_plan`, which carries `credential_binding_id` -- an *id*
  that names a `CredentialBindingConfiguration.profile_key`. The value behind
  it is resolved inside `ToolExecutor.execute`, after the checkpoint is
  written, and is never returned to the graph. There is no state key a secret
  could be assigned to, which is the only version of this guarantee that
  survives a later edit.
* **No raw tool result.** A tool read returns whatever the graph holds about a
  record; `tool_result_ref` names it and `tool_answer` is the bounded thing the
  ladder derived, so a checkpoint never becomes a second copy of the graph.

`question_text` **is** checkpointed, and on purpose. It is Support's own
sentence -- the same trust plane as the `support_message_received` fact that
already stores it and as `OrderAgentGraphState.user_message`, which has been
checkpointed since Order Discovery shipped -- and a resume that had forgotten
the question would have to re-read it to mean anything. `SystemStoreCheckpointSaver`
envelope-encrypts every checkpoint it writes, so this is not the same act as
putting it in a log.

## `attempt` is not here, and must never be

Acceptance 23 requires a retried resolution to resume at the last completed
node. That works only if a retry addresses the *same* thread, so the thread id
is `support-resolver:{case_id}:{support_event_id}` and the attempt number
travels as LangGraph checkpoint **metadata**. Putting `attempt` in the state --
or in the key -- would give every retry a fresh, empty thread and turn "resume"
into "start again", while still passing any test that only checked a retry
completes.
"""

from __future__ import annotations

from typing import Any, Final, TypedDict

__all__ = [
    "SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST",
    "SUPPORT_RESOLVER_THREAD_PREFIX",
    "LadderRung",
    "SupportResolverState",
    "support_resolver_thread_id",
]

#: The ladder's rungs, in the order sect. 9 descends them. Recorded on the
#: state as `rungs_attempted` so an escalation can say what was tried -- which
#: is one of the fields `support_clarification_requested` carries.
LadderRung = str

RUNG_FACTS: Final[LadderRung] = "case_facts"
RUNG_GRAPH: Final[LadderRung] = "graph"
RUNG_TOOL: Final[LadderRung] = "registered_tool"

SUPPORT_RESOLVER_THREAD_PREFIX: Final = "support-resolver"

#: The one character that separates a thread id's components, and therefore the
#: one character a component may not contain. Named rather than inlined so the
#: constructor's refusal and the format string cannot drift apart.
_SEPARATOR: Final = ":"


def support_resolver_thread_id(*, case_id: str, support_event_id: str) -> str:
    """`support-resolver:{case_id}:{support_event_id}` (contracts.md sect. 9).

    **No attempt component.** See the module docstring: the attempt is
    checkpoint metadata, and a thread id that carried it would silently defeat
    resume-at-last-completed-node.

    Both components are refused when blank rather than formatted into a thread
    id with an empty segment: `support-resolver::evt-1` and
    `support-resolver:case-1:` are both ids that look valid, address the wrong
    thread, and would collide with every other blank-component run.

    **A component may not contain the separator**, and that refusal is load-
    bearing rather than tidy. Without it, `(case_id="case-1:evt",
    support_event_id="9")` and `(case_id="case-1", support_event_id="evt:9")`
    mint the *same* thread id -- two different support events resuming into one
    another's checkpoint. This is not hypothetical in this codebase:
    `auto_responder.support_event_id_for` already mints support event ids of the
    form `support-response-agent:{work_item_id}`, which carry a colon. A
    delimiter-joined key is only unique when the delimiter cannot appear in what
    it joins, so the constructor enforces that instead of assuming it.
    """
    for label, component in (("case_id", case_id), ("support_event_id", support_event_id)):
        if not component or not component.strip():
            raise ValueError(f"{label} is required to address a resolver thread")
        if _SEPARATOR in component:
            raise ValueError(
                f"{label} may not contain {_SEPARATOR!r}: a resolver thread id is a "
                f"{_SEPARATOR!r}-joined key, and a component carrying the separator makes "
                "two different support events address one thread"
            )
    return f"{SUPPORT_RESOLVER_THREAD_PREFIX}{_SEPARATOR}{case_id}{_SEPARATOR}{support_event_id}"


class SupportResolverState(TypedDict, total=False):
    """One resolution attempt's durable reasoning position.

    Every key must also appear in `SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST`; a
    field added here without adding it there fails closed at the next
    checkpoint write via `CheckpointRedactor.enforce()`.
    """

    # -- Pinned identity, set once at graph input, never mutated. -------------
    case_id: str
    support_event_id: str
    #: The classified intent, already through V2's closed taxonomy. A string
    #: here rather than a `ValidatedIntent`, because a checkpoint holds data and
    #: the router re-validates on the way out -- a trust object that survived
    #: serialization would be trust the router never checked.
    intent: str
    #: Support's own sentence. See the module docstring.
    question_text: str
    configuration_release_id: str
    prompt_version: str
    agent_id: str
    run_id: str
    #: When this attempt believes "now" is, ISO-8601 UTC. Pinned for the reason
    #: `OrderAgentGraphState.as_of` documents: a clock read per node entry would
    #: let one answer reason about two different days.
    as_of: str

    # -- What the ladder has done so far. -------------------------------------
    #: Rung ids in descent order. The `resolution_attempts[]` a clarification
    #: reports to the associate is rendered from this.
    rungs_attempted: tuple[str, ...]
    #: `assemble_case_context`'s own record of what the prompt was built from
    #: (contracts.md sect. 10, "consumed_fact_ids recorded per invocation").
    consumed_fact_ids: tuple[str, ...]
    #: `AssembledContext.content_hash` -- the claim that two runs saw the same
    #: context, in a form anyone can check.
    context_hash: str

    # -- One entry per rung: a bounded, schema-constrained resolution. ---------
    fact_answer: dict[str, Any] | None
    #: True once the graph rung's own on-demand sync has been requested. A
    #: separate node from the read so a resume after a completed sync does not
    #: sync twice -- the thing acceptance 23 is actually about.
    graph_synced: bool
    graph_sync_receipt_id: str | None
    graph_answer: dict[str, Any] | None
    #: A `ToolInvocationPlan` as a mapping. Carries `credential_binding_id` --
    #: an id, never a value.
    tool_plan: dict[str, Any] | None
    #: Names the tool read; never the read's contents.
    tool_result_ref: str | None
    tool_answer: dict[str, Any] | None
    #: A `ToolRefusal` as a mapping, when no tool ran and why.
    tool_refusal: dict[str, Any] | None

    # -- Budget (contracts.md sect. 9, `per_case_llm_budget`). ----------------
    llm_invocations_used: int
    budget_exhausted: bool

    # -- Terminal. Exactly one of these three is set when the graph ends. ------
    #: The answer, with the gate decision that says whether it may be sent.
    resolution: dict[str, Any] | None
    #: Why no answer was given, in the form the clarification fact needs.
    escalation: dict[str, Any] | None


SUPPORT_RESOLVER_CHECKPOINT_ALLOWLIST: frozenset[str] = frozenset(
    {
        "case_id",
        "support_event_id",
        "intent",
        # Support's own sentence, on the same trust plane as the
        # `support_message_received` fact that already holds it, and written
        # into an envelope-encrypted checkpoint. See the module docstring.
        "question_text",
        "configuration_release_id",
        "prompt_version",
        "agent_id",
        "run_id",
        # A platform-generated timestamp. Says nothing about a customer, and
        # the attempt is unreplayable without it.
        "as_of",
        "rungs_attempted",
        "consumed_fact_ids",
        "context_hash",
        # Schema-constrained resolutions, not raw provider payloads.
        "fact_answer",
        "graph_synced",
        "graph_sync_receipt_id",
        "graph_answer",
        # An id-only plan: `credential_binding_id` names a profile key, and the
        # secret behind it is resolved after this is written and never returned.
        "tool_plan",
        "tool_result_ref",
        "tool_answer",
        "tool_refusal",
        "llm_invocations_used",
        "budget_exhausted",
        "resolution",
        "escalation",
    }
)
