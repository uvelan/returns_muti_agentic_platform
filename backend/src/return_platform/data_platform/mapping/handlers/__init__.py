"""Public code-owned mapping handler contracts and Customer handlers."""

from return_platform.data_platform.mapping.handlers.contracts import (
    HandlerErrorCode,
    HandlerExecutionContext,
    HandlerInvocationError,
    HandlerOutputType,
    HandlerPurpose,
    HandlerRegistrationError,
    HandlerResult,
    MappingHandler,
    SingleStringHandler,
)
from return_platform.data_platform.mapping.handlers.customer import (
    build_customer_account_handler_registry,
)
from return_platform.data_platform.mapping.handlers.registry import HandlerRegistry

__all__ = [
    "HandlerErrorCode",
    "HandlerExecutionContext",
    "HandlerInvocationError",
    "HandlerOutputType",
    "HandlerPurpose",
    "HandlerRegistrationError",
    "HandlerRegistry",
    "HandlerResult",
    "MappingHandler",
    "SingleStringHandler",
    "build_customer_account_handler_registry",
]
