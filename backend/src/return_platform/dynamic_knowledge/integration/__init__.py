"""Concrete adapters for the feat/v2-order-discovery-integration branch."""

from return_platform.dynamic_knowledge.integration.runtime_factory import (
    build_dynamic_order_agent_runtime,
    dynamic_order_agent_enabled,
)

__all__ = ["build_dynamic_order_agent_runtime", "dynamic_order_agent_enabled"]
