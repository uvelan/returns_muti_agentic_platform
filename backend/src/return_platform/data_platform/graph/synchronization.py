"""
AIG9 Graph Synchronization.

Make generated source data available to the production Copilot through the existing synchronization path.
"""

from return_platform.data_platform.operational_generation.graph_sync import request_graph_sync


class SynchronizationManager:
    """Manages the invocation of graph synchronization jobs after operational generation."""

    @staticmethod
    def enqueue_sync(run_id: str, assets: list[str]) -> bool:
        """Enqueue graph sync after source writes."""
        return request_graph_sync(run_id, assets)
