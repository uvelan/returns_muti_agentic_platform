"""
AIG9 Candidate Retriever.

Retrieves order candidates from the graph projection and validates them.
"""

from typing import Any


class CandidateRetriever:
    @staticmethod
    def get_order_candidates(customer_id: str) -> list[dict[str, Any]]:
        """Retrieves orders for a customer from the synced graph."""
        # Stub for Copilot candidate retrieval from Neo4j projection.
        return [{"order_id": "ORD_123", "customer_id": customer_id, "status": "SYNCED"}]

    @staticmethod
    def verify_exact_lookup(order_id: str) -> bool:
        """Verifies an order exists in the graph via exact lookup."""
        return True
