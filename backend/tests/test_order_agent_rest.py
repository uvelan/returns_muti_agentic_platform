import uuid
import pytest
from fastapi.testclient import TestClient

import logging
logging.basicConfig(level=logging.DEBUG)
from return_platform.main import create_app


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
    (29, "Find order for Smith in Nashville"), # Multiple candidates
    (30, "blue"), # Weak clue
    (31, "Order for XYZ123 not exist"), # No match
    (32, "John Smith bought red drill in 99999"), # Contradictory clues
    (33, "Jhon Smuth bought red dril"), # Misspelled customer and product
    (34, "It was a blue one"), # Follow-up adding one clue
    (35, "New search"), # New idempotency key
    (36, "New search"), # Replayed idempotency key (will test manually logic below)
    (37, "Invalid output trigger failover"), # Invalid output followed by model failover
    (38, "Temporary fail trigger failover"), # Temporary route failure
    (39, "Order exactly ORD-10001"), # Lightweight model selected
    (40, "I do not know the order number, customer is David and product was black"), # Standard model selected
]


@pytest.mark.parametrize("scenario_id, message", SCENARIOS)
def test_order_agent_scenarios(scenario_id: int, message: str, test_settings):
    print("DEBUG IN TEST:", test_settings.ai_provider_order, test_settings.nvidia_lightweight_models)
    app = create_app(test_settings)
    
    conv_id = str(uuid.uuid4())
    turn_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    idem_key = str(uuid.uuid4())
    
    with TestClient(app, base_url="http://test") as client:
        print("DEBUG STATE IN TEST:", getattr(app.state, "dynamic_order_agent_status", None))
        print("DEBUG ROUTES IN TEST:", [r.model for r in getattr(app.state, "ai_gateway_route_pool", None).routes] if getattr(app.state, "ai_gateway_route_pool", None) else None)
        if scenario_id == 36:
            # Replay same idempotency key twice
            payload = {
                "conversation_id": conv_id,
                "expected_conversation_version": 0,
                "client_turn_id": turn_id,
                "idempotency_key": idem_key,
                "message_id": msg_id,
                "message": message,
                "agent_id": "order-discovery-agent"
            }
            res1 = client.post(
                f"/api/v2/order-agent/conversations/{conv_id}/turns",
                json=payload,
                headers={"X-Correlation-ID": turn_id}
            )
            assert res1.status_code in (200, 201)
            
            res2 = client.post(
                f"/api/v2/order-agent/conversations/{conv_id}/turns",
                json=payload,
                headers={"X-Correlation-ID": turn_id}
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
            "agent_id": "order-discovery-agent"
        }
        
        response = client.post(
            f"/api/v2/order-agent/conversations/{conv_id}/turns",
            json=payload,
            headers={"X-Correlation-ID": turn_id}
        )
        
        # We expect 200 or 201 for a valid processing, even if agent responds with "no match"
        if response.status_code not in (200, 201):
            print(f"Error {response.status_code}: {response.text}")
        assert response.status_code in (200, 201)
        data = response.json()
        assert "response" in data
        # At least some model execution happened
        assert data["model_provider"] is not None

        import time
        time.sleep(3)
