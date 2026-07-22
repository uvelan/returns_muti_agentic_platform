"""Immutable contracts for deterministic code-owned mapping handlers."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from return_platform.canonical.base import (
    CanonicalBaseModel,
    CanonicalIdentifier,
    NonBlankText,
    Sha256Digest,
    SourceProvenance,
    UtcDateTime,
    VersionReference,
)
from return_platform.data_platform.mapping.contracts import (
    HandlerName,
    MappingIdentifier,
    SourceSystemName,
)

__all__ = [
    "HandlerErrorCode",
    "HandlerExecutionContext",
    "HandlerInvocationError",
    "HandlerOutputType",
    "HandlerPurpose",
    "HandlerRegistrationError",
    "HandlerResult",
    "MappingHandler",
    "SingleStringHandler",
]

type HandlerResult = str | SourceProvenance
"""Outputs currently required by the Customer foundation profile."""

type SingleStringHandlerFunction = Callable[[str, "HandlerExecutionContext"], HandlerResult]


class HandlerOutputType(StrEnum):
    """Declared output category used by the mapping compiler."""

    STRING = "STRING"
    SOURCE_PROVENANCE = "SOURCE_PROVENANCE"


class HandlerPurpose(StrEnum):
    """Code-owned purpose of a mapping handler."""

    FIELD = "FIELD"
    IDENTITY = "IDENTITY"


class HandlerErrorCode(StrEnum):
    """Stable safe error codes for registration and invocation failures."""

    DUPLICATE_HANDLER = "DUPLICATE_HANDLER"
    HANDLER_NOT_REGISTERED = "HANDLER_NOT_REGISTERED"
    INVALID_HANDLER_NAME = "INVALID_HANDLER_NAME"
    INVALID_HANDLER_SEQUENCE = "INVALID_HANDLER_SEQUENCE"
    INVALID_ARITY = "INVALID_ARITY"
    INVALID_INPUT_TYPE = "INVALID_INPUT_TYPE"
    INVALID_INPUT_VALUE = "INVALID_INPUT_VALUE"
    SOURCE_CONTEXT_MISMATCH = "SOURCE_CONTEXT_MISMATCH"


class HandlerRegistrationError(ValueError):
    """Safe error raised while constructing an immutable handler registry."""

    def __init__(self, code: HandlerErrorCode, safe_message: str) -> None:
        """Initialize one safe registration error."""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class HandlerInvocationError(ValueError):
    """Safe error raised for invalid deterministic handler invocation."""

    def __init__(self, code: HandlerErrorCode, safe_message: str) -> None:
        """Initialize one safe invocation error."""
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class HandlerExecutionContext(CanonicalBaseModel):
    """Immutable source and configuration evidence supplied by the compiler."""

    source_id: MappingIdentifier
    catalog_asset_id: MappingIdentifier
    source_system: SourceSystemName
    source_database: NonBlankText
    source_asset: NonBlankText
    source_document_id: CanonicalIdentifier
    source_updated_at: UtcDateTime | None = None
    source_version: VersionReference | None = None
    source_event_id: NonBlankText | None = None
    source_hash: Sha256Digest | None = None
    observed_at: UtcDateTime
    mapping_version: VersionReference
    configuration_version: VersionReference
    configuration_digest: Sha256Digest


class MappingHandler(Protocol):
    """Runtime protocol implemented by every code-owned mapping handler."""

    @property
    def name(self) -> str:
        """Return the stable registry name."""

    @property
    def purpose(self) -> HandlerPurpose:
        """Return whether the handler creates a field or identity value."""

    @property
    def input_arity(self) -> int:
        """Return the exact number of ordered input values required."""

    @property
    def output_type(self) -> HandlerOutputType:
        """Return the statically declared handler output category."""

    @property
    def contract_version(self) -> str:
        """Return the handler contract version."""

    @property
    def deterministic(self) -> bool:
        """Return whether the handler promises deterministic execution."""

    def invoke(
        self,
        values: tuple[object, ...],
        context: HandlerExecutionContext,
    ) -> HandlerResult:
        """Execute deterministically using immutable input and context."""


@dataclass(frozen=True, slots=True)
class SingleStringHandler:
    """Reusable exact-arity handler for one strict non-blank string input."""

    name: HandlerName
    purpose: HandlerPurpose
    function: SingleStringHandlerFunction
    output_type: HandlerOutputType = HandlerOutputType.STRING
    contract_version: VersionReference = "1.0"

    @property
    def input_arity(self) -> int:
        """Return the exact supported input arity."""
        return 1

    @property
    def deterministic(self) -> bool:
        """Declare stateless deterministic execution."""
        return True

    def invoke(
        self,
        values: tuple[object, ...],
        context: HandlerExecutionContext,
    ) -> HandlerResult:
        """Validate exact arity and strict input type before execution."""
        if len(values) != self.input_arity:
            raise HandlerInvocationError(
                HandlerErrorCode.INVALID_ARITY,
                "handler invocation received an invalid number of values",
            )

        raw_value = values[0]
        if not isinstance(raw_value, str):
            raise HandlerInvocationError(
                HandlerErrorCode.INVALID_INPUT_TYPE,
                "handler input must be a string",
            )

        value = raw_value.strip()
        if not value:
            raise HandlerInvocationError(
                HandlerErrorCode.INVALID_INPUT_VALUE,
                "handler input must not be blank",
            )

        return self.function(value, context)
