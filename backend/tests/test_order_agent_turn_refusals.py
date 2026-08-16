"""A misconfigured deployment and an out-of-scope agent are different answers.

`order_discovery_activities` looks the requested agent up in
`runtime.schema.agent_policies`, gets `None` and fails the turn with
`ORDER_AGENT_OUT_OF_SCOPE`. It does that for two unrelated reasons:

* the caller named an agent that is genuinely not in scope -- a client error,
  and the caller's to fix; and
* `copilot.order_discovery_agent_id` is unset, or names a policy the active
  schema does not publish -- an operator error, which no caller can fix and no
  retry can survive.

The turn route used to answer 422 to both, and that conflation is what made the
original P0 read as a client bug for as long as it did: every single turn came
back "the agent you asked for is out of scope" while the actual fault was that
the shipped mapping named `order_discovery` and the schema keyed the policy
`order-discovery-agent`. Plan sect. 5.4 asks for `503
COPILOT_AGENT_CONFIGURATION_INVALID` in the second case;
`api/return_history.py` already answered it and this route did not.

No HTTP and no Temporal server. `process_turn` is awaited directly with a
stubbed workflow handle, because what is under test is the decision the route
makes about an outcome it has already received -- standing up a `TestClient`
would add a lifespan, a Mongo client and a task queue without exercising one
more line of it. The configuration and the schema are the real shipped files.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request

from return_platform.configuration.return_configuration import (
    LoadedReturnConfiguration,
    load_return_configuration,
)
from return_platform.configuration.settings import (
    DEFAULT_RETURN_CONFIGURATION_PATH,
    Settings,
)
from return_platform.data_governance import LoadedAssetCatalog
from return_platform.dynamic_knowledge.api.order_agent import (
    DynamicOrderAgentRuntime,
    process_turn,
)
from return_platform.dynamic_knowledge.order_agent.contracts import AgentTurnRequest
from return_platform.resources import RuntimeResources
from return_platform.security.principal import Principal
from return_platform.workflows.order_discovery_workflow import (
    OrderDiscoveryTurnError,
    OrderDiscoveryTurnOutcome,
)

CONVERSATION_ID = "11111111-2222-3333-4444-555555555555"

#: The activity's own refusal, reproduced exactly. Both scenarios below arrive
#: at the route as this one value; everything that distinguishes them is
#: configuration, which is the whole point.
OUT_OF_SCOPE = OrderDiscoveryTurnOutcome(
    result=None,
    error=OrderDiscoveryTurnError(
        code="ORDER_AGENT_OUT_OF_SCOPE",
        message="Agent policy is not in scope for this request.",
        retryable=False,
    ),
)


class _StubHandle:
    def __init__(self, outcome: OrderDiscoveryTurnOutcome) -> None:
        self._outcome = outcome

    async def execute_update(self, _update: Any, _argument: Any) -> OrderDiscoveryTurnOutcome:
        return self._outcome


class _StubTemporalClient:
    def __init__(self, outcome: OrderDiscoveryTurnOutcome) -> None:
        self._outcome = outcome

    async def start_workflow(self, *_args: Any, **_kwargs: Any) -> _StubHandle:
        return _StubHandle(self._outcome)

    def get_workflow_handle(self, _workflow_id: str) -> _StubHandle:  # pragma: no cover
        return _StubHandle(self._outcome)


def _runtime(outcome: OrderDiscoveryTurnOutcome) -> DynamicOrderAgentRuntime:
    return DynamicOrderAgentRuntime(
        temporal_client=cast(Any, _StubTemporalClient(outcome)),
        task_queue="order-discovery-test",
    )


def _configuration(*, agent_id: str | None, replace: bool) -> LoadedReturnConfiguration:
    """The shipped configuration, optionally with its Copilot mapping moved."""
    shipped = load_return_configuration(DEFAULT_RETURN_CONFIGURATION_PATH)
    if not replace:
        return shipped
    return shipped.model_copy(
        update={
            "configuration": shipped.configuration.model_copy(
                update={
                    "copilot": shipped.configuration.copilot.model_copy(
                        update={"order_discovery_agent_id": agent_id}
                    )
                }
            )
        }
    )


def _request(
    configuration: LoadedReturnConfiguration,
    settings: Settings,
    catalog: LoadedAssetCatalog,
) -> Request:
    """Enough of a request to reach application state and the caller's identity.

    `resources.mongo` is `None` deliberately: with no release store,
    `resolve_known_agent_policy_ids` resolves the active schema from
    `settings.dynamic_knowledge_schema_path` -- the shipped file -- so the
    binding is checked against the real published policy ids without a server.
    """
    app_state = SimpleNamespace(
        return_configuration=configuration,
        resources=RuntimeResources(settings=settings, catalog=catalog, mongo=None),
        settings=settings,
    )
    request_state = SimpleNamespace(
        principal=Principal(subject="associate-1", roles=frozenset({"returns.read"})),
        tenant_id="default",
        branch_ids=("branch-1",),
        correlation_id="corr-1",
    )
    return cast(
        Request,
        SimpleNamespace(app=SimpleNamespace(state=app_state), state=request_state),
    )


def _payload(agent_id: str) -> AgentTurnRequest:
    return AgentTurnRequest(
        conversation_id=CONVERSATION_ID,
        expected_conversation_version=0,
        client_turn_id="turn-1",
        idempotency_key="idem-1",
        message_id="msg-1",
        message="Find order ORD-10001",
        agent_id=agent_id,
    )


async def _refusal(
    configuration: LoadedReturnConfiguration,
    settings: Settings,
    catalog: LoadedAssetCatalog,
    *,
    agent_id: str,
) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await process_turn(
            CONVERSATION_ID,
            _payload(agent_id),
            _request(configuration, settings, catalog),
            _runtime(OUT_OF_SCOPE),
        )
    return refused.value


@pytest.mark.asyncio
async def test_a_dangling_agent_mapping_is_a_503_not_a_client_error(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    """The original P0's exact shape: the mapping names a policy nothing publishes.

    `order_discovery` is the literal the frontend used to send while the schema
    keyed the policy `order-discovery-agent`. Answering 422 to it told the
    associate they had asked for the wrong thing.
    """
    refusal = await _refusal(
        _configuration(agent_id="order_discovery", replace=True),
        test_settings,
        loaded_empty_catalog,
        agent_id="order_discovery",
    )

    assert refusal.status_code == 503
    detail = cast(dict[str, Any], refusal.detail)
    assert detail["code"] == "COPILOT_AGENT_CONFIGURATION_INVALID"
    # The guard's own sentence, not a restatement -- it names the configured id
    # and the policies the active schema does publish, which is what an operator
    # needs in order to fix it.
    assert "order_discovery" in detail["message"]
    assert detail["retryable"] is False


@pytest.mark.asyncio
async def test_an_unset_agent_mapping_is_a_503_not_a_client_error(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    """No mapping at all is the same operator fault, not a different one.

    A deployment that cannot say which agent the Copilot answers to has no turn
    it could route, whatever the caller asked for.
    """
    refusal = await _refusal(
        _configuration(agent_id=None, replace=True),
        test_settings,
        loaded_empty_catalog,
        agent_id="order-discovery-agent",
    )

    assert refusal.status_code == 503
    detail = cast(dict[str, Any], refusal.detail)
    assert detail["code"] == "COPILOT_AGENT_CONFIGURATION_INVALID"
    assert detail["retryable"] is False


@pytest.mark.asyncio
async def test_a_genuinely_out_of_scope_agent_is_still_a_422(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    """The shipped configuration is sound, so the fault is the caller's.

    This is the assertion that keeps the fix from being "503 for everything":
    the deployment resolves, the caller named something else, and the code says
    so.
    """
    refusal = await _refusal(
        _configuration(agent_id=None, replace=False),
        test_settings,
        loaded_empty_catalog,
        agent_id="some-other-agent",
    )

    assert refusal.status_code == 422
    detail = cast(dict[str, Any], refusal.detail)
    assert detail["code"] == "ORDER_AGENT_OUT_OF_SCOPE"


@pytest.mark.asyncio
async def test_other_turn_failures_keep_the_status_they_had(
    test_settings: Settings,
    loaded_empty_catalog: LoadedAssetCatalog,
) -> None:
    """Only the ambiguous code is reinterpreted.

    A version conflict is still a 409 whatever the Copilot mapping says --
    a configuration check that widened to every failure would hide real client
    errors behind an operator one.
    """
    conflict = OrderDiscoveryTurnOutcome(
        result=None,
        error=OrderDiscoveryTurnError(
            code="CONVERSATION_VERSION_CONFLICT",
            message="This conversation moved on.",
            retryable=False,
        ),
    )

    with pytest.raises(HTTPException) as refused:
        await process_turn(
            CONVERSATION_ID,
            _payload("order-discovery-agent"),
            _request(
                _configuration(agent_id="order_discovery", replace=True),
                test_settings,
                loaded_empty_catalog,
            ),
            _runtime(conflict),
        )

    assert refused.value.status_code == 409
    detail = cast(dict[str, Any], refused.value.detail)
    assert detail["code"] == "CONVERSATION_VERSION_CONFLICT"
