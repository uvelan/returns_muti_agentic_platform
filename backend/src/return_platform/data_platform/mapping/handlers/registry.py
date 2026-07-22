"""Immutable deterministic registry for code-owned mapping handlers."""

from types import MappingProxyType

from pydantic import TypeAdapter, ValidationError

from return_platform.data_platform.mapping.contracts import HandlerName
from return_platform.data_platform.mapping.handlers.contracts import (
    HandlerErrorCode,
    HandlerExecutionContext,
    HandlerInvocationError,
    HandlerRegistrationError,
    HandlerResult,
    MappingHandler,
)

__all__ = ["HandlerRegistry"]

_HANDLER_NAME_ADAPTER = TypeAdapter(HandlerName)


class HandlerRegistry:
    """Resolve and invoke an immutable allow-list of mapping handlers."""

    __slots__ = ("_handlers", "_registered_names")

    def __init__(self, handlers: tuple[MappingHandler, ...]) -> None:
        """Index one ordered immutable handler collection."""
        if not isinstance(handlers, tuple):
            raise HandlerRegistrationError(
                HandlerErrorCode.INVALID_HANDLER_SEQUENCE,
                "handler registration must use an ordered tuple",
            )

        indexed: dict[str, MappingHandler] = {}
        for handler in handlers:
            try:
                handler_name = _HANDLER_NAME_ADAPTER.validate_python(
                    handler.name,
                    strict=True,
                )
            except ValidationError as error:
                raise HandlerRegistrationError(
                    HandlerErrorCode.INVALID_HANDLER_NAME,
                    "handler name is invalid",
                ) from error

            if handler_name in indexed:
                raise HandlerRegistrationError(
                    HandlerErrorCode.DUPLICATE_HANDLER,
                    "handler names must be unique",
                )
            indexed[handler_name] = handler

        self._handlers = MappingProxyType(indexed)
        self._registered_names = tuple(sorted(indexed))

    @property
    def registered_names(self) -> tuple[str, ...]:
        """Return the deterministic sorted registry inventory."""
        return self._registered_names

    def resolve(self, name: HandlerName) -> MappingHandler:
        """Resolve one registered handler or raise a safe bounded error."""
        try:
            validated_name = _HANDLER_NAME_ADAPTER.validate_python(name, strict=True)
        except ValidationError as error:
            raise HandlerInvocationError(
                HandlerErrorCode.INVALID_HANDLER_NAME,
                "mapping handler name is invalid",
            ) from error

        handler = self._handlers.get(validated_name)
        if handler is None:
            raise HandlerInvocationError(
                HandlerErrorCode.HANDLER_NOT_REGISTERED,
                "mapping handler is not registered",
            )
        return handler

    def invoke(
        self,
        name: HandlerName,
        values: tuple[object, ...],
        context: HandlerExecutionContext,
    ) -> HandlerResult:
        """Resolve and execute one deterministic handler."""
        return self.resolve(name).invoke(values, context)
