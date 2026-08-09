"""Order Agent failure type -- its own module so graph_nodes.py and
coordinator.py can both import it without a circular dependency
(coordinator.py builds/wraps the compiled graph that graph_nodes.py defines)."""

from __future__ import annotations


class OrderAgentFailure(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
