"""Which configuration keys a proposal of each type may change (plan section 7).

Three policies, chosen by proposal *type* and never read off the record:

``ALLOWLIST``       an improvement proposal may only touch a key named here, and
                    every numeric one is additionally bounded. This is the set
                    the plan resolves under "Permitted proposal keys (audit Q2)".
``DENYLIST``        an operator's own configuration edit is not restricted to a
                    curated list -- an operator may legitimately edit any field
                    of a module they own -- but the forbidden set still applies.
                    Security, credentials and access changes go through platform
                    administration, not through a configuration screen.
``NOT_KEY_SCOPED``  a graph-schema proposal's "keys" are entity and property
                    paths, not configuration keys. Its soundness is established
                    by validation against the source snapshot and by compiling
                    against the graph target, which is a stronger check than any
                    key list; applying a configuration allowlist to it would
                    refuse every schema change and prove nothing.

**Forbidden beats permitted, and a key that is neither is refused under
ALLOWLIST.** Fail-closed is the only safe default for a list whose job is to stop
a model from editing something nobody thought to forbid.

**Matched at every offset, not only as a prefix.** `secrets.*` has to catch
`payload.secrets.api_key` as surely as `secrets.api_key`; a prefix-only matcher
would be defeated by one level of nesting, which is exactly the shape a document
editor produces.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from return_platform.platform.governance.proposal import ProposalType

__all__ = [
    "ALLOWED_PROPOSAL_KEY_PREFIXES",
    "FORBIDDEN_PROPOSAL_KEY_PATTERNS",
    "PERMITTED_IMPROVEMENT_KEYS",
    "KeyPolicy",
    "KeyPolicyDecision",
    "PermittedKey",
    "evaluate_keys",
    "policy_for",
    "resolve_improvement_key",
]


class KeyPolicy(StrEnum):
    ALLOWLIST = "ALLOWLIST"
    DENYLIST = "DENYLIST"
    NOT_KEY_SCOPED = "NOT_KEY_SCOPED"


_POLICY_BY_TYPE: Mapping[ProposalType, KeyPolicy] = {
    ProposalType.IMPROVEMENT: KeyPolicy.ALLOWLIST,
    ProposalType.CONFIGURATION: KeyPolicy.DENYLIST,
    ProposalType.GRAPH_SCHEMA: KeyPolicy.NOT_KEY_SCOPED,
}


def policy_for(proposal_type: ProposalType) -> KeyPolicy:
    """Derived from the type, so a stored field cannot widen it."""
    return _POLICY_BY_TYPE[proposal_type]


#: Plan section 7, "Forbidden", verbatim. `*` matches exactly one segment.
FORBIDDEN_PROPOSAL_KEY_PATTERNS: tuple[str, ...] = (
    "secrets.*",
    "credentials.*",
    "vault.*",
    "auth.*",
    "principal.*",
    "capabilities.*",
    "permissions.*",
    "sources.*.credentials",
    "sources.*.connection",
    "graph.schema.*",
    "graph.identities.*",
    "graph.source_bindings.*",
    "agent.failure_policy.*",
    "ai.provider_credentials.*",
    "ai.allowed_hosts.*",
    "ai.safety.*",
    "ai.guardrails.*",
    "workflow.idempotency.*",
    "workflow.authorization.*",
)

#: Plan section 7, "Allowed". A prefix here makes a key *eligible*; it still has
#: to appear in `PERMITTED_IMPROVEMENT_KEYS` to be applicable, because a key with
#: no configuration path bound to it cannot be activated by anything.
ALLOWED_PROPOSAL_KEY_PREFIXES: tuple[str, ...] = (
    "returns.conversation.tone",
    "returns.reminders",
    "returns.elicitation.field_priority",
    "returns.discovery.scoring",
    "returns.discovery.clarification",
    "returns.support.template",
    "returns.fulfillment.polling",
)


@dataclass(frozen=True, slots=True)
class PermittedKey:
    """One improvement key, and where in `ReturnPlatformConfiguration` it lands.

    `document_path` is a dotted path into the RETURN_PLATFORM release domain.
    `collection_key` is set for the two families whose target is an entry inside
    a keyed collection rather than a fixed field -- a smart question addressed by
    its `field`, an anchor weight addressed by its anchor name.

    `minimum`/`maximum` are the plan's "every numeric field additionally carries
    server-side min/max validation". They repeat the Pydantic bounds on purpose:
    the model refuses an invalid document at release time, and this refuses an
    invalid *proposal* at submission, which is where the person who has to fix it
    is still looking.
    """

    key: str
    document_path: str
    collection_key: str | None = None
    minimum: int | None = None
    maximum: int | None = None

    @property
    def is_numeric(self) -> bool:
        return self.minimum is not None or self.maximum is not None


def _entry(
    key: str,
    document_path: str,
    *,
    collection_key: str | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> tuple[str, PermittedKey]:
    return key, PermittedKey(
        key=key,
        document_path=document_path,
        collection_key=collection_key,
        minimum=minimum,
        maximum=maximum,
    )


#: Every improvement key the platform can actually apply, with the configuration
#: field it resolves to and its server-side bounds.
#:
#: The two prefixes plan section 7 permits that are **absent** here --
#: `returns.support.template.*` and `returns.fulfillment.polling.*` -- have no
#: field in `ReturnPlatformConfiguration` today: support carries queues and an
#: outbox topic but no message templates, and fulfillment polling is not
#: configuration at all. A key under either prefix is refused with that reason
#: rather than accepted into a proposal nothing could ever activate.
PERMITTED_IMPROVEMENT_KEYS: Mapping[str, PermittedKey] = dict(
    (
        _entry(
            "returns.reminders.reminder_interval_seconds",
            "return_case.reminder_interval_seconds",
            minimum=60,
            maximum=604_800,
        ),
        _entry(
            "returns.reminders.max_reminders", "return_case.max_reminders", minimum=0, maximum=50
        ),
        _entry(
            "returns.reminders.support_response_wait_seconds",
            "return_case.support_response_wait_seconds",
            minimum=60,
            maximum=604_800,
        ),
        _entry(
            "returns.discovery.scoring.ambiguity_gap_millionths",
            "discovery.ambiguity_gap_millionths",
            minimum=0,
            maximum=1_000_000,
        ),
        _entry(
            "returns.discovery.scoring.conflict_penalty_millionths",
            "discovery.conflict_penalty_millionths",
            minimum=0,
            maximum=1_000_000,
        ),
        _entry(
            "returns.discovery.clarification.max_clarification_options",
            "discovery.progressive.max_clarification_options",
            minimum=2,
            maximum=12,
        ),
        _entry(
            "returns.discovery.clarification.candidate_limit",
            "discovery.progressive.candidate_limit",
            minimum=1,
            maximum=20,
        ),
        _entry(
            "returns.discovery.clarification.max_prompts_per_turn",
            "clarification_policy.max_prompts_per_turn",
            minimum=1,
            maximum=5,
        ),
        _entry(
            "returns.discovery.clarification.max_fields_per_turn",
            "clarification_policy.max_fields_per_turn",
            minimum=1,
            maximum=5,
        ),
        _entry(
            "returns.conversation.tone.greeting_response",
            "discovery.conversation.greeting_response",
        ),
        _entry("returns.conversation.tone.greeting_title", "discovery.conversation.greeting_title"),
        _entry(
            "returns.conversation.tone.greeting_status", "discovery.conversation.greeting_status"
        ),
    )
)

#: Families addressed by a trailing name rather than a fixed key.
_KEYED_FAMILIES: tuple[PermittedKey, ...] = (
    PermittedKey(
        key="returns.elicitation.field_priority",
        document_path="clarification_policy.fields",
        collection_key="field",
        minimum=0,
        maximum=10_000,
    ),
    PermittedKey(
        key="returns.discovery.scoring.anchor_weight",
        document_path="discovery.anchor_weights",
        collection_key="",
        minimum=0,
        maximum=1_000,
    ),
)


def resolve_improvement_key(key: str) -> tuple[PermittedKey, str | None] | None:
    """The configuration target for an improvement key, or None if unbound.

    Returns the entry and, for a keyed family, the trailing name -- the smart
    question's `field`, the anchor's own name.
    """
    exact = PERMITTED_IMPROVEMENT_KEYS.get(key)
    if exact is not None:
        return exact, None
    for family in _KEYED_FAMILIES:
        prefix = f"{family.key}."
        if key.startswith(prefix):
            remainder = key[len(prefix) :]
            if remainder and "." not in remainder:
                return family, remainder
    return None


def _pattern_matches(pattern_segments: Sequence[str], key_segments: Sequence[str]) -> bool:
    """Does the pattern match anywhere inside the key, segment-aligned?"""
    span = len(pattern_segments)
    for offset in range(len(key_segments) - span + 1):
        window = key_segments[offset : offset + span]
        if all(
            expected == "*" or expected == actual
            for expected, actual in zip(pattern_segments, window, strict=True)
        ):
            return True
    return False


def is_forbidden(key: str) -> bool:
    key_segments = key.split(".")
    return any(
        _pattern_matches(pattern.split("."), key_segments)
        for pattern in FORBIDDEN_PROPOSAL_KEY_PATTERNS
    )


@dataclass(frozen=True, slots=True)
class KeyPolicyDecision:
    """Why a key set was accepted or refused, key by key."""

    policy: KeyPolicy
    forbidden: tuple[str, ...] = ()
    #: Under ALLOWLIST: named no permitted prefix at all.
    not_permitted: tuple[str, ...] = ()
    #: Under ALLOWLIST: permitted by prefix, but no configuration field is bound.
    unresolvable: tuple[str, ...] = ()
    out_of_bounds: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not (self.forbidden or self.not_permitted or self.unresolvable or self.out_of_bounds)

    def reason(self) -> str:
        parts: list[str] = []
        if self.forbidden:
            parts.append("forbidden by the permitted-key policy: " + ", ".join(self.forbidden))
        if self.not_permitted:
            parts.append("outside every permitted key prefix: " + ", ".join(self.not_permitted))
        if self.unresolvable:
            parts.append(
                "permitted by prefix but bound to no configuration field: "
                + ", ".join(self.unresolvable)
            )
        if self.out_of_bounds:
            parts.append("outside the server-side bounds: " + ", ".join(self.out_of_bounds))
        return "; ".join(parts)


def evaluate_keys(
    policy: KeyPolicy,
    keys: Iterable[str],
    values: Mapping[str, object] | None = None,
) -> KeyPolicyDecision:
    """Apply `policy` to a key set, and to the proposed values where numeric.

    `values` maps key to the *proposed* value. Omitted for the plain key check
    the review screen runs; supplied at submission and again at activation, so a
    ranking weight of one billion is refused by the same code both times.
    """
    if policy is KeyPolicy.NOT_KEY_SCOPED:
        return KeyPolicyDecision(policy=policy)

    forbidden: list[str] = []
    not_permitted: list[str] = []
    unresolvable: list[str] = []
    out_of_bounds: list[str] = []

    for key in keys:
        if is_forbidden(key):
            forbidden.append(key)
            continue
        if policy is KeyPolicy.DENYLIST:
            continue
        if not any(
            key == prefix or key.startswith(f"{prefix}.")
            for prefix in ALLOWED_PROPOSAL_KEY_PREFIXES
        ):
            not_permitted.append(key)
            continue
        resolved = resolve_improvement_key(key)
        if resolved is None:
            unresolvable.append(key)
            continue
        permitted, _ = resolved
        if values is not None and permitted.is_numeric and key in values:
            value = values[key]
            if not isinstance(value, int) or isinstance(value, bool):
                out_of_bounds.append(key)
            elif permitted.minimum is not None and value < permitted.minimum:
                out_of_bounds.append(key)
            elif permitted.maximum is not None and value > permitted.maximum:
                out_of_bounds.append(key)

    return KeyPolicyDecision(
        policy=policy,
        forbidden=tuple(forbidden),
        not_permitted=tuple(not_permitted),
        unresolvable=tuple(unresolvable),
        out_of_bounds=tuple(out_of_bounds),
    )
