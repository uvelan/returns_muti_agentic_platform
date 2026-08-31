"""The tool boundary (contracts.md sect. 9).

> *Raw support text can never directly name a tool or supply an argument;
> classification produces validated intent + required-entity schema; config maps
> intent -> eligible capabilities; arguments only from trusted case facts/graph;
> execution requires schema validation + authorization; missing required
> entities -> refuse.*

The reason that wording is implementable is that it separates **selection** from
**argument supply**, and this module makes both structural rather than
disciplinary.

**Selection.** `plan_tool_invocation` takes a `ValidatedIntent` -- a value that
can only be constructed by `validate_intent`, which checks membership in the
*released closed taxonomy* and refuses anything else. A string of support prose
cannot become a `ValidatedIntent`; the widest thing any text can do is land on
one of the nine taxonomy members, each of which selects from a list a release
wrote. There is no path from text to a tool id.

**Argument supply.** `plan_tool_invocation` has **no parameter through which
raw text could arrive**. Its inputs are the intent, the released bindings, and a
`TrustedEntities` bag. That is the whole signature. A future edit that wanted to
take an argument from the support message would have to add a parameter, which
is a visible change to a reviewed signature rather than a line buried in a
branch -- and `test_support_tool_router.py` asserts the signature.

`TrustedEntities` can itself only be built by `trusted_entities_from` , whose
inputs are a case-fact projection and graph results. It has no constructor that
accepts free text, and every value it holds carries the provenance of the fact
or graph path it came from, which the plan then reports.

**Credentials.** A plan carries `credential_binding_id` -- an id, never a value.
Resolution happens inside `ToolExecutor.execute`, behind the plan, from a
resolver the platform supplies. Nothing in this module ever holds a secret, so
nothing it returns can put one into agent state, a prompt, a checkpoint or a
log.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from return_platform.platform.capabilities.contracts import CapabilityName
from return_platform.platform.capabilities.tool_schemas import (
    EntityField,
    ToolInputSchema,
    UnknownInputSchemaError,
    resolve_input_schema,
)

__all__ = [
    "AuthorizationPort",
    "EntitySource",
    "RefusalReason",
    "ToolBindingLike",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolInvocationPlan",
    "ToolPortLike",
    "ToolRefusal",
    "ToolRouteOutcome",
    "TrustedEntities",
    "TrustedEntity",
    "UnvalidatedIntentError",
    "ValidatedIntent",
    "plan_tool_invocation",
    "trusted_entities_from",
    "validate_intent",
]


class RefusalReason(StrEnum):
    """Why no tool ran. Every one of these is a *refusal*, not an error.

    The distinction matters to the ladder: a refusal means "descend a rung or
    escalate", and an error means "something is broken". Collapsing them would
    let a misconfigured binding read as an unanswerable question.
    """

    #: No released binding lists this intent. The ordinary case on a default
    #: release, where `tool_bindings` is empty.
    NO_ELIGIBLE_BINDING = "NO_ELIGIBLE_BINDING"
    #: A binding exists but the trusted bag does not carry every required
    #: entity. Sect. 9's "missing required entities -> refuse".
    MISSING_REQUIRED_ENTITY = "MISSING_REQUIRED_ENTITY"
    #: The binding names a capability/contract nothing has published, or a
    #: schema this build does not implement.
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    #: The principal may not use this capability on this case.
    NOT_AUTHORIZED = "NOT_AUTHORIZED"


class EntitySource(StrEnum):
    """Where a trusted entity's value came from. Recorded on every argument."""

    CASE_FACT = "case_fact"
    GRAPH = "graph"


class UnvalidatedIntentError(TypeError):
    """Something that is not a `ValidatedIntent` reached the router.

    A `TypeError` rather than a refusal, because it is a programming error:
    every legitimate caller has been through `validate_intent`. Refusing
    quietly would let the one path that skipped validation look like the one
    path with no eligible tool.
    """


@dataclass(frozen=True, slots=True)
class ValidatedIntent:
    """A classification that is a member of the released closed taxonomy.

    Constructible only through `validate_intent`. The guard is the private
    `_validated` field: a caller writing `ValidatedIntent("info_request")`
    positionally would still have to pass the sentinel, and one written by hand
    is a line a reviewer sees.
    """

    value: str
    _validated: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._validated is not _VALIDATION_SENTINEL:
            raise UnvalidatedIntentError(
                "ValidatedIntent must be built by validate_intent(); a classification "
                "that has not been checked against the released taxonomy is raw text"
            )


_VALIDATION_SENTINEL = object()


def validate_intent(candidate: str, allowed: Iterable[str]) -> ValidatedIntent | None:
    """`candidate` as a validated intent, or `None` when it is not in the set.

    `None` rather than a fallback, deliberately. V2's `coerce_intent` already
    collapses out-of-set answers to `other` *at classification time*, which is
    where the taxonomy floor belongs. By the time a classification reaches the
    router it has been through that; a value still outside the set here means
    the caller skipped the pipeline, and quietly rewriting it to `other` would
    make a skipped stage look like an ordinary message.
    """
    normalized = candidate.strip().lower() if candidate else ""
    if not normalized or normalized not in {str(item).strip().lower() for item in allowed}:
        return None
    return ValidatedIntent(normalized, _VALIDATION_SENTINEL)


@dataclass(frozen=True, slots=True)
class TrustedEntity:
    """One argument value, with the provenance of where it was trusted from."""

    name: str
    value: Any
    source: EntitySource
    #: The fact name or graph path. Carried into the plan so an executed tool's
    #: arguments can be audited back to the record they came from.
    source_path: str
    fact_id: str | None = None


@dataclass(frozen=True, slots=True)
class TrustedEntities:
    """The only bag `plan_tool_invocation` will take arguments from.

    Built by `trusted_entities_from`. There is deliberately no constructor
    taking text: the type *is* the trust boundary, so a value that is in here
    has already been through a case fact or a graph read.
    """

    entities: Mapping[str, TrustedEntity]

    def get(self, name: str) -> TrustedEntity | None:
        return self.entities.get(name)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self.entities)


def trusted_entities_from(
    *,
    case_facts: Mapping[str, Any],
    fact_ids: Mapping[str, str] | None = None,
    graph_results: Mapping[str, Any] | None = None,
) -> TrustedEntities:
    """Build the trusted bag from the two sources sect. 9 permits.

    `case_facts` is a scoped-latest fact projection keyed by entity name;
    `graph_results` is what a graph read returned. Nothing else is accepted, and
    a graph result never silently replaces a case fact -- the fact wins, because
    the case's own record is the more authoritative of the two and a graph that
    disagreed with it is a *conflict*, which the ladder escalates rather than
    resolves by precedence.
    """
    resolved: dict[str, TrustedEntity] = {}
    for name, value in (graph_results or {}).items():
        if value is not None:
            resolved[name] = TrustedEntity(
                name=name, value=value, source=EntitySource.GRAPH, source_path=f"graph:{name}"
            )
    identifiers = fact_ids or {}
    for name, value in case_facts.items():
        if value is not None:
            resolved[name] = TrustedEntity(
                name=name,
                value=value,
                source=EntitySource.CASE_FACT,
                source_path=f"case_fact:{name}",
                fact_id=identifiers.get(name),
            )
    return TrustedEntities(entities=resolved)


class ToolBindingLike(Protocol):
    """The shape `plan_tool_invocation` needs from a released binding.

    A Protocol rather than an import of `ToolBindingConfiguration`, for the
    reason `case_context.ContextPolicy` states: `platform/` must not depend on
    `configuration/`, and structural typing is what keeps the dependency
    pointing one way.
    """

    @property
    def tool_id(self) -> str: ...
    @property
    def intents(self) -> tuple[str, ...]: ...
    @property
    def capability(self) -> str: ...
    @property
    def contract(self) -> str: ...
    @property
    def input_schema_ref(self) -> str: ...
    @property
    def credential_binding_id(self) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ToolRefusal:
    """No tool ran, and precisely why."""

    reason: RefusalReason
    intent: str
    detail: str
    tool_id: str | None = None
    missing_entities: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolInvocationPlan:
    """A tool that may run, with arguments that came only from trusted sources."""

    tool_id: str
    intent: str
    capability: str
    contract: str
    input_schema_ref: str
    arguments: Mapping[str, Any]
    #: One entry per argument: `{argument_name: (source, source_path, fact_id)}`.
    #: Sect. 8's provenance rule applied to tool arguments -- an executed tool's
    #: inputs are auditable back to the fact or graph path they came from.
    argument_provenance: Mapping[str, tuple[str, str, str | None]]
    credential_binding_id: str | None


#: A plan, or a refusal. Never both, and never neither.
ToolRouteOutcome = ToolInvocationPlan | ToolRefusal


def _arguments_for(
    schema: ToolInputSchema, trusted: TrustedEntities
) -> tuple[dict[str, Any], dict[str, tuple[str, str, str | None]], tuple[str, ...]]:
    """Fill a schema from the trusted bag. Returns (arguments, provenance, missing).

    Only fields the schema declares are read, and each is type-checked by the
    field itself. A value present in the bag under a name the schema does not
    declare is *not* passed through: the schema is the argument list, so an
    extra trusted fact cannot widen a tool's input.
    """
    arguments: dict[str, Any] = {}
    provenance: dict[str, tuple[str, str, str | None]] = {}
    missing: list[str] = []
    for declared in schema.fields:
        entity = trusted.get(declared.name)
        coerced = declared.coerced(entity.value) if entity is not None else None
        if coerced is None:
            if _is_required(schema, declared):
                missing.append(declared.name)
            continue
        assert entity is not None
        arguments[declared.name] = coerced
        provenance[declared.name] = (entity.source.value, entity.source_path, entity.fact_id)
    return arguments, provenance, tuple(missing)


def _is_required(schema: ToolInputSchema, declared: EntityField) -> bool:
    return declared.name in schema.required_entity_names


def plan_tool_invocation(
    intent: ValidatedIntent,
    bindings: Sequence[ToolBindingLike],
    trusted: TrustedEntities,
) -> ToolRouteOutcome:
    """Select a tool for a validated intent and fill its arguments. Or refuse.

    **Three parameters. None of them is text.** That is the boundary: this
    function cannot take an argument from a support message because it has
    nowhere to receive one, and it cannot select a tool from a support message
    because `ValidatedIntent` is a closed-set membership rather than a string.

    Bindings are tried in the order the release declares them, and the first
    whose required entities are all present wins. A binding that is missing an
    entity does not abort the search -- a release may list a precise tool ahead
    of a general one, and the general one is the answer when the precise one's
    entity is not on file. If none can be filled, the refusal names the
    entities the *first* eligible binding wanted, because that is the one the
    operator ranked highest and therefore the one whose gap is worth reporting.
    """
    if not isinstance(intent, ValidatedIntent):
        raise UnvalidatedIntentError(
            f"plan_tool_invocation requires a ValidatedIntent, got {type(intent).__name__}"
        )
    eligible = [binding for binding in bindings if intent.value in binding.intents]
    if not eligible:
        return ToolRefusal(
            reason=RefusalReason.NO_ELIGIBLE_BINDING,
            intent=intent.value,
            detail=f"no released tool binding lists intent {intent.value!r}",
        )

    first_gap: ToolRefusal | None = None
    for binding in eligible:
        try:
            schema = resolve_input_schema(binding.input_schema_ref)
        except UnknownInputSchemaError as error:
            refusal = ToolRefusal(
                reason=RefusalReason.CAPABILITY_UNAVAILABLE,
                intent=intent.value,
                detail=str(error),
                tool_id=binding.tool_id,
            )
            first_gap = first_gap or refusal
            continue
        arguments, provenance, missing = _arguments_for(schema, trusted)
        if missing:
            refusal = ToolRefusal(
                reason=RefusalReason.MISSING_REQUIRED_ENTITY,
                intent=intent.value,
                detail=(
                    f"tool {binding.tool_id!r} requires {', '.join(missing)}, and no trusted "
                    "case fact or graph result supplies them"
                ),
                tool_id=binding.tool_id,
                missing_entities=missing,
            )
            first_gap = first_gap or refusal
            continue
        return ToolInvocationPlan(
            tool_id=binding.tool_id,
            intent=intent.value,
            capability=binding.capability,
            contract=binding.contract,
            input_schema_ref=binding.input_schema_ref,
            arguments=arguments,
            argument_provenance=provenance,
            credential_binding_id=binding.credential_binding_id,
        )
    assert first_gap is not None  # `eligible` is non-empty and every branch sets it
    return first_gap


class AuthorizationPort(Protocol):
    """Whether this principal may use this capability on this case."""

    async def authorize(self, *, principal_id: str, capability: str, case_id: str) -> bool: ...


class ToolPortLike(Protocol):
    """What a published tool implementation must offer.

    `credential_binding_id` rather than a credential: the implementation
    resolves it platform-side. A port taking a secret would make every caller a
    place a secret can leak from.
    """

    async def invoke(
        self,
        *,
        arguments: Mapping[str, Any],
        credential_binding_id: str | None,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    tool_id: str
    result: Mapping[str, Any]
    argument_provenance: Mapping[str, tuple[str, str, str | None]]


class ToolExecutor:
    """Runs a plan, after re-checking the schema and checking authorization.

    The schema is validated **again** here rather than trusted from the plan.
    That is not belt-and-braces for its own sake: a plan is a value that can be
    carried across a checkpoint boundary and resumed later, possibly on a build
    where the schema has changed, and executing a stale plan against a moved
    schema is exactly the shape sect. 9's "execution requires schema
    validation" exists to refuse.
    """

    def __init__(
        self,
        *,
        registry: Any,
        authorization: AuthorizationPort,
        contracts: Mapping[str, type],
    ) -> None:
        self._registry = registry
        self._authorization = authorization
        #: Contract *name* -> contract class. A code-side allowlist, for the
        #: same reason the schemas are: a released string must never be able to
        #: name an arbitrary type for the registry to resolve.
        self._contracts = dict(contracts)

    async def execute(
        self,
        plan: ToolInvocationPlan,
        *,
        principal_id: str,
        case_id: str,
    ) -> ToolExecutionResult | ToolRefusal:
        try:
            schema = resolve_input_schema(plan.input_schema_ref)
        except UnknownInputSchemaError as error:
            return ToolRefusal(
                reason=RefusalReason.CAPABILITY_UNAVAILABLE,
                intent=plan.intent,
                detail=str(error),
                tool_id=plan.tool_id,
            )
        missing = tuple(
            name for name in schema.required_entity_names if plan.arguments.get(name) is None
        )
        if missing:
            return ToolRefusal(
                reason=RefusalReason.MISSING_REQUIRED_ENTITY,
                intent=plan.intent,
                detail=f"plan for {plan.tool_id!r} no longer satisfies {plan.input_schema_ref!r}",
                tool_id=plan.tool_id,
                missing_entities=missing,
            )
        declared = {field_.name for field_ in schema.fields}
        undeclared = tuple(sorted(set(plan.arguments) - declared))
        if undeclared:
            # An argument the schema does not declare cannot have come from
            # `plan_tool_invocation`, which only ever fills declared fields. Its
            # presence means the plan was built or edited somewhere else.
            return ToolRefusal(
                reason=RefusalReason.CAPABILITY_UNAVAILABLE,
                intent=plan.intent,
                detail=(
                    f"plan for {plan.tool_id!r} carries arguments {', '.join(undeclared)} that "
                    f"{plan.input_schema_ref!r} does not declare"
                ),
                tool_id=plan.tool_id,
            )

        if not await self._authorization.authorize(
            principal_id=principal_id, capability=plan.capability, case_id=case_id
        ):
            return ToolRefusal(
                reason=RefusalReason.NOT_AUTHORIZED,
                intent=plan.intent,
                detail=f"principal is not authorized for {plan.capability!r} on this case",
                tool_id=plan.tool_id,
            )

        contract = self._contracts.get(plan.contract)
        capability = _capability_or_none(plan.capability)
        if contract is None or capability is None:
            return ToolRefusal(
                reason=RefusalReason.CAPABILITY_UNAVAILABLE,
                intent=plan.intent,
                detail=(
                    f"binding names capability {plan.capability!r} / contract {plan.contract!r}, "
                    "which this build does not implement"
                ),
                tool_id=plan.tool_id,
            )
        port = self._registry.resolve_optional(capability, contract)
        if port is None:
            return ToolRefusal(
                reason=RefusalReason.CAPABILITY_UNAVAILABLE,
                intent=plan.intent,
                detail=f"nothing has published {plan.contract!r} for {plan.capability!r}",
                tool_id=plan.tool_id,
            )
        result = await port.invoke(
            arguments=dict(plan.arguments),
            credential_binding_id=plan.credential_binding_id,
        )
        return ToolExecutionResult(
            tool_id=plan.tool_id,
            result=dict(result),
            argument_provenance=dict(plan.argument_provenance),
        )


def _capability_or_none(value: str) -> CapabilityName | None:
    try:
        return CapabilityName(value)
    except ValueError:
        return None
