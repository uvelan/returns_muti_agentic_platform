"""`GET /api/runtime-config` -- the shell's bootstrap payload.

Design doc section 9.5 places this on the versionless canonical surface, and
section 12's migration table maps `api/runtime_config.py` here. It served
`/api/v1/runtime-config` until then, which left the four-domain shell unable to
boot without a `/api/v1` route -- the last such dependency, and the reason the
README carried a "known leftover" note.

The two vocabulary lists are derived, not retyped. Both existed as literals in
this module *and* at the place that actually enforces them, which is a drift the
type checker cannot see: a client is validated against the enforcing definition,
never against the advertisement.
"""

from __future__ import annotations

from typing import Literal, get_args

from fastapi import APIRouter, Request, status
from pydantic import BaseModel

from return_platform.configuration.bootstrap_runtime_integrations import HOSTED_AI_PROVIDERS
from return_platform.configuration.return_configuration import LoadedReturnConfiguration
from return_platform.configuration.runtime_validation import DataSourceValidateAndStageRequest
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api", tags=["runtime-config"])

#: The source types a client may actually submit, read off the request model
#: that rejects everything else.
_AVAILABLE_SOURCE_TYPES: tuple[str, ...] = tuple(
    get_args(DataSourceValidateAndStageRequest.model_fields["sourceType"].annotation)
)


class RuntimeConfigFeatures(BaseModel):
    orderDiscoveryCopilot: bool


class RuntimeConfigCapabilities(BaseModel):
    availableSourceTypes: tuple[str, ...]
    availableModelProviders: tuple[str, ...]


class RuntimeConfigAgents(BaseModel):
    """Which agent the shell addresses, served rather than compiled into it.

    The Copilot sent the literal `"order_discovery"` while the active schema
    keys the policy `order-discovery-agent`, so every turn 422'd. `None` is the
    honest answer when no configuration is loaded or the deployment has not
    stated the mapping -- the shell fails closed on it, and there is deliberately
    no server-side default to fall back to.
    """

    orderDiscovery: str | None


class RuntimeConfigSelectionVocabulary(BaseModel):
    """The reason and condition catalogues an associate may pick a line from.

    Served rather than compiled into the console for the same reason
    `agents.orderDiscovery` is: `selection_vocabulary` is operator-owned and
    changes in a release, and `POST /api/cases/{id}/selected-items` refuses
    anything the active release does not publish with
    `422 SELECTION_TERM_NOT_PUBLISHED`. A picker built from a list in the
    browser would therefore offer terms the writer rejects the moment an
    operator edits the catalogue -- which is the hardcoded catalogue plan
    sect. 12.4 removed from the item-selection pane.

    **Empty means "no catalogue is published", not "nothing is allowed".** That
    is the release's own reading -- `unknown_reasons` refuses nothing for an
    unpublished catalogue -- and a client seeing empty lists must fall back to
    the free-text behaviour a pre-12.4 release still has, not to a list of its
    own.

    Non-secret: two lists of enum tokens an associate reads off a dropdown.
    """

    reasons: tuple[str, ...]
    conditions: tuple[str, ...]


class RuntimeConfigFactCatalogue(BaseModel):
    """The order the operator ranked the conversation's facts in.

    `clarification_policy.fields[].priority` is the operator's own statement of
    how badly each fact is wanted, and `build_fact_catalogue` binds that very
    list into the catalogue that decides which fact names a turn may capture at
    all -- a name it does not define is reported and discarded rather than
    stored. So this is not a new configuration surface: it is the ranking of
    the exact key space `captured_facts` is already written in.

    Served rather than compiled into the console for the reason
    `selectionVocabulary` is. The facts panel listed its rows in an order
    written into a TypeScript array, which meant an operator who re-ranked
    `clarification_policy` changed what the agent asks for next and changed
    nothing about the panel that reports what it got -- and could not correct
    the panel at all without a frontend release.

    **Why the priority and not `discovery.anchor_weights` or
    `identification_fields[].clarification_priority`.** Neither is keyed by a
    fact name. `anchor_weights` is per anchor *type* -- eight entries scoring
    candidate matches, with no entry for a return reason, which is not an
    anchor and never will be. `identification_fields[].clarification_priority`
    is keyed by the discovery catalogue's own `field_id` (`sku`, `postal_code`,
    `free_text`), a namespace that overlaps this one only by coincidence and
    that omits `return_reason`, `product_sku`, `customer_id` and
    `tracking_number` outright. Ordering by either would leave most of what the
    panel renders unranked.

    `orderedFields` is descending priority with ties broken by field name, so
    the sequence is total and every client agrees on it without re-implementing
    the comparison. It is the configured ranking verbatim, including names a
    given screen chooses to withhold: what a panel suppresses is that panel's
    decision, not the contract's.

    **Empty means no configuration is loaded**, the same as every other block
    here, and carries the same instruction: a client must fall back to an order
    it can defend rather than substitute a ranking of its own.

    Non-secret: a list of configured field names, no values.
    """

    orderedFields: tuple[str, ...]


class RuntimeConfigCandidateColumn(BaseModel):
    """One column of the Copilot's candidate table, verbatim from the release.

    `copilot.candidate_columns`, served rather than compiled into the console
    for the reason every block here is: which fields identify an order to an
    associate is an operator decision, and it changes in a release, not in a
    frontend deploy. `fields` is an alias chain -- the first name the row
    carries supplies the value -- because order, line and customer searches
    return differently shaped rows that one column must read across.

    **An empty list means the deployment has not said**, and the client falls
    back to the identity columns it can defend rather than to rendering every
    field the query selected.

    Non-secret: column labels and field names, no values.
    """

    label: str
    fields: tuple[str, ...]


class RuntimeConfig(BaseModel):
    releaseId: str
    environment: str
    apiBasePath: Literal["/api"]
    features: RuntimeConfigFeatures
    capabilities: RuntimeConfigCapabilities
    agents: RuntimeConfigAgents
    selectionVocabulary: RuntimeConfigSelectionVocabulary
    factCatalogue: RuntimeConfigFactCatalogue
    candidateColumns: tuple[RuntimeConfigCandidateColumn, ...]


def _ordered_fact_fields(loaded: object) -> tuple[str, ...]:
    """`clarification_policy.fields`, most wanted first.

    Sorted here rather than at the client so the tie-break is decided once. The
    field names are unique -- `SmartQuestionConfiguration` validates that -- so
    `(-priority, field)` is a total order and the same release always produces
    the same sequence.
    """
    if not isinstance(loaded, LoadedReturnConfiguration):
        return ()
    return tuple(
        item.field
        for item in sorted(
            loaded.configuration.clarification_policy.fields,
            key=lambda item: (-item.priority, item.field),
        )
    )


@router.get(
    "/runtime-config",
    response_model=APIResponse[RuntimeConfig],
    status_code=status.HTTP_200_OK,
)
async def get_runtime_config(request: Request) -> APIResponse[RuntimeConfig]:
    """Safe, non-secret configuration the shell can read before authenticating."""
    settings = request.app.state.settings
    snapshot = getattr(request.app.state, "return_configuration_snapshot", None)
    # The same source `apply_active_return_policy` reads, and for the same
    # reason: the correlation middleware refreshes it before every handler, so
    # this is the mapping the process is serving right now rather than one
    # captured at import.
    loaded = getattr(request.app.state, "return_configuration", None)

    return APIResponse[RuntimeConfig](
        data=RuntimeConfig(
            releaseId=getattr(snapshot, "release_id", "unknown") if snapshot else "unknown",
            environment=settings.environment,
            # Was "/api/v1", which stopped being true when Wave F made the
            # versionless surface canonical. A literal type, so it cannot drift
            # back without the contract changing.
            apiBasePath="/api",
            features=RuntimeConfigFeatures(
                # Was hardcoded `True` next to an `aiStudioOperationalGeneration`
                # flag for a feature Wave F deleted. This one is a real setting.
                orderDiscoveryCopilot=settings.dynamic_order_agent_enabled,
            ),
            capabilities=RuntimeConfigCapabilities(
                availableSourceTypes=_AVAILABLE_SOURCE_TYPES,
                availableModelProviders=HOSTED_AI_PROVIDERS,
            ),
            agents=RuntimeConfigAgents(
                orderDiscovery=(
                    loaded.configuration.copilot.order_discovery_agent_id
                    if isinstance(loaded, LoadedReturnConfiguration)
                    else None
                ),
            ),
            # Empty when nothing is loaded, which is the same answer an
            # unpublished catalogue gives and carries the same instruction to
            # the client: this deployment has published no catalogue. There is
            # deliberately no default list here -- a server-side fallback would
            # advertise terms `_reject_unpublished_terms` has never heard of.
            selectionVocabulary=RuntimeConfigSelectionVocabulary(
                reasons=(
                    loaded.configuration.selection_vocabulary.reasons
                    if isinstance(loaded, LoadedReturnConfiguration)
                    else ()
                ),
                conditions=(
                    loaded.configuration.selection_vocabulary.conditions
                    if isinstance(loaded, LoadedReturnConfiguration)
                    else ()
                ),
            ),
            # Empty when nothing is loaded, for the same reason and with the
            # same meaning as the block above: there is no server-side default
            # ranking, because a default here would be the hardcoded order
            # moved one tier down rather than removed.
            factCatalogue=RuntimeConfigFactCatalogue(
                orderedFields=_ordered_fact_fields(loaded),
            ),
            # Empty when nothing is loaded or the release states no columns,
            # with the same meaning as every block above: the deployment has
            # not said, and the client falls back to columns it can defend.
            candidateColumns=(
                tuple(
                    RuntimeConfigCandidateColumn(label=item.label, fields=item.fields)
                    for item in loaded.configuration.copilot.candidate_columns
                )
                if isinstance(loaded, LoadedReturnConfiguration)
                else ()
            ),
        ),
        meta=ResponseMeta(request_id=getattr(request.state, "correlation_id", "unknown")),
    )
