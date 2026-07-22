"""Deterministic Customer CDM handlers required by the first mapping profile."""

from return_platform.canonical.base import SourceProvenance
from return_platform.data_platform.mapping.handlers.contracts import (
    HandlerErrorCode,
    HandlerExecutionContext,
    HandlerInvocationError,
    HandlerOutputType,
    HandlerPurpose,
    SingleStringHandler,
)
from return_platform.data_platform.mapping.handlers.registry import HandlerRegistry

__all__ = ["build_customer_account_handler_registry"]

_CUSTOMER_SOURCE_SYSTEM = "CUSTOMER_CDM"
_ACCOUNT_SEPARATOR = "*"
_CONTROL_CHARACTER_LIMIT = 32
_DELETE_CHARACTER_CODE = 127


def _require_customer_context(context: HandlerExecutionContext) -> None:
    """Require the Customer CDM source-system boundary."""
    if context.source_system != _CUSTOMER_SOURCE_SYSTEM:
        raise HandlerInvocationError(
            HandlerErrorCode.SOURCE_CONTEXT_MISMATCH,
            "handler source context does not match Customer CDM",
        )


def _require_safe_component(value: str, *, allow_account_separator: bool) -> str:
    """Reject ambiguous identity delimiters, whitespace, and controls."""
    forbidden = {":"}
    if not allow_account_separator:
        forbidden.add(_ACCOUNT_SEPARATOR)

    if any(character in forbidden for character in value):
        raise HandlerInvocationError(
            HandlerErrorCode.INVALID_INPUT_VALUE,
            "handler input contains a reserved identity delimiter",
        )
    if any(
        character.isspace()
        or ord(character) < _CONTROL_CHARACTER_LIMIT
        or ord(character) == _DELETE_CHARACTER_CODE
        for character in value
    ):
        raise HandlerInvocationError(
            HandlerErrorCode.INVALID_INPUT_VALUE,
            "handler input contains whitespace or control characters",
        )
    return value


def _require_party_id(value: str, context: HandlerExecutionContext) -> str:
    """Validate one Customer party identity against document evidence."""
    _require_customer_context(context)
    party_id = _require_safe_component(value, allow_account_separator=False)
    if party_id != context.source_document_id:
        raise HandlerInvocationError(
            HandlerErrorCode.SOURCE_CONTEXT_MISMATCH,
            "party identity does not match the source document",
        )
    return party_id


def _parse_account_number(
    value: str,
    context: HandlerExecutionContext,
) -> tuple[str, str, str]:
    """Parse LOGON*customerId without accepting ambiguous delimiters."""
    _require_customer_context(context)
    account_number = _require_safe_component(value, allow_account_separator=True)
    if account_number.count(_ACCOUNT_SEPARATOR) != 1:
        raise HandlerInvocationError(
            HandlerErrorCode.INVALID_INPUT_VALUE,
            "account number must contain exactly one separator",
        )

    logon, customer_id = account_number.split(_ACCOUNT_SEPARATOR, maxsplit=1)
    if not logon or not customer_id:
        raise HandlerInvocationError(
            HandlerErrorCode.INVALID_INPUT_VALUE,
            "account number components must not be blank",
        )

    _require_safe_component(logon, allow_account_separator=False)
    _require_safe_component(customer_id, allow_account_separator=False)
    return account_number, logon, customer_id


def _customer_cdm_source_system_v1(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Return the code-owned Customer CDM source-system token."""
    _require_party_id(value, context)
    return _CUSTOMER_SOURCE_SYSTEM


def _source_record_id_from_party_id_v1(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Return the confirmed Customer document identity."""
    return _require_party_id(value, context)


def _customer_key_v1(value: str, context: HandlerExecutionContext) -> str:
    """Build the canonical Customer identity."""
    party_id = _require_party_id(value, context)
    return f"CUSTOMER_CDM:{party_id}"


def _customer_reference_key_v1(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Build a Customer reference for a dependent canonical record."""
    return _customer_key_v1(value, context)


def _account_number_customer_id_v1(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Extract customerId from the confirmed LOGON*customerId format."""
    _, _, customer_id = _parse_account_number(value, context)
    return customer_id


def _customer_account_key_v1(
    value: str,
    context: HandlerExecutionContext,
) -> str:
    """Build the canonical CustomerAccount identity."""
    account_number, _, _ = _parse_account_number(value, context)
    return f"CUSTOMER_CDM:{account_number}"


def _build_provenance(
    source_record_id: str,
    context: HandlerExecutionContext,
) -> SourceProvenance:
    """Build immutable provenance only from supplied deterministic evidence."""
    _require_customer_context(context)
    return SourceProvenance(
        source_system=_CUSTOMER_SOURCE_SYSTEM,
        source_database=context.source_database,
        source_asset=context.source_asset,
        source_record_id=source_record_id,
        source_updated_at=context.source_updated_at,
        source_version=context.source_version,
        source_event_id=context.source_event_id,
        source_hash=context.source_hash,
        observed_at=context.observed_at,
        mapping_version=context.mapping_version,
        configuration_version=context.configuration_version,
        configuration_digest=context.configuration_digest,
    )


def _customer_cdm_document_provenance_v1(
    value: str,
    context: HandlerExecutionContext,
) -> SourceProvenance:
    """Create Customer document provenance from confirmed party identity."""
    return _build_provenance(_require_party_id(value, context), context)


def _customer_account_provenance_v1(
    value: str,
    context: HandlerExecutionContext,
) -> SourceProvenance:
    """Create nested CustomerAccount provenance from accountNumber."""
    account_number, _, _ = _parse_account_number(value, context)
    return _build_provenance(account_number, context)


_CUSTOMER_ACCOUNT_HANDLERS = (
    SingleStringHandler(
        name="customer_cdm_source_system_v1",
        purpose=HandlerPurpose.FIELD,
        function=_customer_cdm_source_system_v1,
    ),
    SingleStringHandler(
        name="source_record_id_from_party_id_v1",
        purpose=HandlerPurpose.FIELD,
        function=_source_record_id_from_party_id_v1,
    ),
    SingleStringHandler(
        name="customer_cdm_document_provenance_v1",
        purpose=HandlerPurpose.FIELD,
        function=_customer_cdm_document_provenance_v1,
        output_type=HandlerOutputType.SOURCE_PROVENANCE,
    ),
    SingleStringHandler(
        name="customer_key_v1",
        purpose=HandlerPurpose.IDENTITY,
        function=_customer_key_v1,
    ),
    SingleStringHandler(
        name="customer_reference_key_v1",
        purpose=HandlerPurpose.FIELD,
        function=_customer_reference_key_v1,
    ),
    SingleStringHandler(
        name="account_number_customer_id_v1",
        purpose=HandlerPurpose.FIELD,
        function=_account_number_customer_id_v1,
    ),
    SingleStringHandler(
        name="customer_account_provenance_v1",
        purpose=HandlerPurpose.FIELD,
        function=_customer_account_provenance_v1,
        output_type=HandlerOutputType.SOURCE_PROVENANCE,
    ),
    SingleStringHandler(
        name="customer_account_key_v1",
        purpose=HandlerPurpose.IDENTITY,
        function=_customer_account_key_v1,
    ),
)


def build_customer_account_handler_registry() -> HandlerRegistry:
    """Build a new immutable registry containing the approved first handlers."""
    return HandlerRegistry(_CUSTOMER_ACCOUNT_HANDLERS)
