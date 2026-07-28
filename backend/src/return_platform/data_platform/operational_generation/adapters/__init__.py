# Import adapters to register them
from . import (  # noqa: F401
    direct_mongodb,
    graph_sync,
    platform_domain_api,
    source_mongodb,
    test_doubles,
)
from .protocol import ExecutionAdapter
from .registry import get_adapter_class, register_adapter

__all__ = [
    "ExecutionAdapter",
    "get_adapter_class",
    "register_adapter",
]
