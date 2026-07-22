"""Deterministic tests for the immutable mapping handler registry."""

from datetime import UTC, datetime
from typing import cast

import pytest

from return_platform.data_platform.mapping import HandlerName
from return_platform.data_platform.mapping.handlers import (
    HandlerErrorCode,
    HandlerExecutionContext,
    HandlerInvocationError,
    HandlerPurpose,
    HandlerRegistrationError,
    HandlerRegistry,
    MappingHandler,
    SingleStringHandler,
    build_customer_account_handler_registry,
)

_EXPECTED_HANDLER_NAMES = (
    "account_number_customer_id_v1",
    "customer_account_key_v1",
    "customer_account_provenance_v1",
    "customer_cdm_document_provenance_v1",
    "customer_cdm_source_system_v1",
    "customer_key_v1",
    "customer_reference_key_v1",
    "source_record_id_from_party_id_v1",
)


def _context() -> HandlerExecutionContext:
    """Create deterministic Customer CDM execution evidence."""
    return HandlerExecutionContext(
        source_id="source.customer_cdm.v1",
        catalog_asset_id="source.mongodb.customer_outbound_cdm",
        source_system="CUSTOMER_CDM",
        source_database="eventMessages",
        source_asset="customerOutboundCDM",
        source_document_id="PARTY-123",
        source_updated_at=datetime(2026, 7, 20, 1, 2, tzinfo=UTC),
        source_version="42",
        source_event_id="event-77",
        source_hash="b" * 64,
        observed_at=datetime(2026, 7, 21, 3, 4, tzinfo=UTC),
        mapping_version="1.0",
        configuration_version="1.0",
        configuration_digest="a" * 64,
    )


def _echo(value: str, context: HandlerExecutionContext) -> str:
    """Return one deterministic test value."""
    del context
    return value


def _handler(name: HandlerName = "test_echo_v1") -> SingleStringHandler:
    """Create one reusable test handler."""
    return SingleStringHandler(
        name=name,
        purpose=HandlerPurpose.FIELD,
        function=_echo,
    )


def test_builtin_registry_contains_exact_profile_handlers() -> None:
    """Expose only the handlers required by the approved first profile."""
    registry = build_customer_account_handler_registry()

    assert registry.registered_names == _EXPECTED_HANDLER_NAMES


def test_registry_instances_do_not_share_mutable_state() -> None:
    """Build independent immutable registry objects for dependency injection."""
    first = build_customer_account_handler_registry()
    second = build_customer_account_handler_registry()

    assert first is not second
    assert first.registered_names == second.registered_names
    assert first.resolve("customer_key_v1") is second.resolve("customer_key_v1")


def test_registry_rejects_invalid_handler_name() -> None:
    """Reject executable or non-token handler names at registration."""
    handler = _handler("package.module:function")

    with pytest.raises(HandlerRegistrationError) as exc_info:
        HandlerRegistry((handler,))

    assert exc_info.value.code is HandlerErrorCode.INVALID_HANDLER_NAME


def test_registry_rejects_invalid_handler_name_at_resolution() -> None:
    """Reject malformed caller names before registry lookup."""
    registry = build_customer_account_handler_registry()

    with pytest.raises(HandlerInvocationError) as exc_info:
        registry.resolve("customer_key_v1()")

    assert exc_info.value.code is HandlerErrorCode.INVALID_HANDLER_NAME


def test_registry_rejects_duplicate_handler_names() -> None:
    """Reject duplicate names before compiler startup."""
    handler = _handler()

    with pytest.raises(HandlerRegistrationError) as exc_info:
        HandlerRegistry((handler, handler))

    assert exc_info.value.code is HandlerErrorCode.DUPLICATE_HANDLER
    assert str(exc_info.value) == "handler names must be unique"


def test_registry_rejects_unordered_or_mutable_registration_input() -> None:
    """Require an ordered tuple as code-owned registration evidence."""
    handlers = cast("tuple[MappingHandler, ...]", [_handler()])

    with pytest.raises(HandlerRegistrationError) as exc_info:
        HandlerRegistry(handlers)

    assert exc_info.value.code is HandlerErrorCode.INVALID_HANDLER_SEQUENCE


def test_registry_unknown_handler_error_does_not_echo_name() -> None:
    """Return a stable safe error without reflecting caller input."""
    registry = build_customer_account_handler_registry()

    with pytest.raises(HandlerInvocationError) as exc_info:
        registry.resolve("missing_handler_v1")

    assert exc_info.value.code is HandlerErrorCode.HANDLER_NOT_REGISTERED
    assert "missing_handler_v1" not in str(exc_info.value)


def test_single_string_handler_requires_exact_arity() -> None:
    """Reject missing and excess ordered inputs."""
    handler = _handler()

    for values in ((), ("one", "two")):
        with pytest.raises(HandlerInvocationError) as exc_info:
            handler.invoke(values, _context())
        assert exc_info.value.code is HandlerErrorCode.INVALID_ARITY


def test_single_string_handler_rejects_non_string_and_blank_input() -> None:
    """Reject permissive scalar coercion and blank strings."""
    handler = _handler()

    for value, expected_code in (
        (123, HandlerErrorCode.INVALID_INPUT_TYPE),
        (True, HandlerErrorCode.INVALID_INPUT_TYPE),
        (b"value", HandlerErrorCode.INVALID_INPUT_TYPE),
        ("   ", HandlerErrorCode.INVALID_INPUT_VALUE),
    ):
        with pytest.raises(HandlerInvocationError) as exc_info:
            handler.invoke((value,), _context())
        assert exc_info.value.code is expected_code


def test_single_string_handler_normalizes_outer_whitespace_only() -> None:
    """Strip bounded outer whitespace before deterministic execution."""
    assert _handler().invoke(("  value  ",), _context()) == "value"
