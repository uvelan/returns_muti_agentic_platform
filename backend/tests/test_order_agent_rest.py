"""Forty discovery scenarios through the real REST surface.

Two requirements, and this module used to declare neither.

`TestClient(create_app(...))` is entered as a context manager, so the
application lifespan runs and opens MongoDB, Neo4j, Valkey and Temporal for
real; and every scenario needs an actual model response. Unmarked, it was
collected into the default run alongside 2,700 tests that need nothing --
`test_order_agent_scenarios` blocked on `client.post(...)` waiting for
infrastructure, the run was killed at a per-test timeout, and the entire suite
produced no pass/fail signal at all. That is the whole of Y-10: one test's
dependency erasing every other test's result.

`live_infra` answers the first requirement -- it moves this module into the
suite that has the datastores. `live_ai_credentials` answers the second, and is
the mechanism `tests/conftest.py` already defines for it: with real provider
keys these scenarios run, and without them they say so and skip. The live-suite
runner used to `--ignore` this file by name to get the same effect, which meant
the exclusion was permanent and invisible from here. A requirement a test states
itself is one that stops applying the moment it is satisfied.
"""

import logging
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from return_platform.main import create_app

logger = logging.getLogger(__name__)

SCENARIOS = [
    # Exact identifiers
    (1, "Find order id ORD-10001"),
    (2, "Exact order number 10001"),
    (3, "Reformatted order number: ORD 100 01"),
    (4, "Exact SKU 12345"),
    # Customer search
    (5, "Order for John Smith"),
    (6, "Order for John"),
    (7, "Order for Smith"),
    (8, "Order for Jhon Smith"),
    (9, "Customer Smith in 37211"),
    (10, "Customer Smith in Nashville"),
    (11, "Customer Smith on 18 Main Street"),
    # Product search
    (12, "Moen chrome kitchen faucet"),
    (13, "Moen faucet"),
    (14, "SKU ending in 345"),
    (15, "blue faucet"),
    (16, "faucet bought by John"),
    (17, "faucet bought around July 25"),
    # Date search
    (18, "Order on July 24 2026"),
    (19, "Orders between July 20 and July 28"),
    (20, "Order from last Friday"),
    (21, "Order from around July 25"),
    (22, "John Smith orders between July 20 and July 28"),
    # Combined search
    (23, "John Smith bought a blue faucet"),
    (24, "John Smith in 37211 bought a faucet"),
    (25, "Address 18 Main Street bought a faucet"),
    (26, "ZIP 37211 on July 24 bought a faucet"),
    (27, "Jhon with SKU ending in 345"),
    (28, "I think John bought a blue sink last week in Nashville"),
    # Ambiguity and failure
    (29, "Find order for Smith in Nashville"),  # Multiple candidates
    (30, "blue"),  # Weak clue
    (31, "Order for XYZ123 not exist"),  # No match
    (32, "John Smith bought red drill in 99999"),  # Contradictory clues
    (33, "Jhon Smuth bought red dril"),  # Misspelled customer and product
    (34, "It was a blue one"),  # Follow-up adding one clue
    (35, "New search"),  # New idempotency key
    (36, "New search"),  # Replayed idempotency key (tested manually below)
    (37, "Invalid output trigger failover"),  # Invalid output, then model failover
    (38, "Temporary fail trigger failover"),  # Temporary route failure
    (39, "Order exactly ORD-10001"),  # Lightweight model selected
    (40, "I do not know the order number, customer is David and product was black"),
]


@pytest.mark.live_infra
@pytest.mark.parametrize("scenario_id, message", SCENARIOS)
def test_order_agent_scenarios(scenario_id: int, message: str, test_settings, live_ai_credentials):
    logger.debug(
        "order_agent_scenario_test_started",
        extra={
            "scenario_id": scenario_id,
            "provider_order": test_settings.ai_provider_order,
            "nvidia_lightweight_models": test_settings.nvidia_lightweight_models,
        },
    )
    app = create_app(test_settings)

    conv_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    idem_key = str(uuid.uuid4())

    with TestClient(app, base_url="http://test") as client:
        if scenario_id == 36:
            # Replay same idempotency key twice
            payload = {
                "conversation_id": conv_id,
                "expected_conversation_version": 0,
                "client_turn_id": turn_id,
                "idempotency_key": idem_key,
                "message_id": msg_id,
                "message": message,
                "agent_id": "order-discovery-agent",
            }
            res1 = client.post(
                f"/api/v2/order-agent/conversations/{conv_id}/turns",
                json=payload,
                headers={"X-Correlation-ID": turn_id},
            )
            assert res1.status_code in (200, 201)

            res2 = client.post(
                f"/api/v2/order-agent/conversations/{conv_id}/turns",
                json=payload,
                headers={"X-Correlation-ID": turn_id},
            )
            assert res2.status_code in (200, 201)
            assert res1.json() == res2.json()
            return

        payload = {
            "conversation_id": conv_id,
            "expected_conversation_version": 0,
            "client_turn_id": turn_id,
            "idempotency_key": idem_key,
            "message_id": msg_id,
            "message": message,
            "agent_id": "order-discovery-agent",
        }

        response = client.post(
            f"/api/v2/order-agent/conversations/{conv_id}/turns",
            json=payload,
            headers={"X-Correlation-ID": turn_id},
        )

        # We expect 200 or 201 for a valid processing, even if agent responds with "no match"
        if response.status_code not in (200, 201):
            logger.error(
                "order_agent_scenario_test_failed",
                extra={"scenario_id": scenario_id, "status_code": response.status_code},
            )
        assert response.status_code in (200, 201)
        # The route returns the platform `{data, meta}` envelope, like every
        # other route. Reading `response.json()` directly used to work only
        # because this one was the exception.
        data = response.json()["data"]
        assert "response" in data
        # At least some model execution happened
        assert data["model_provider"] is not None

        time.sleep(3)
