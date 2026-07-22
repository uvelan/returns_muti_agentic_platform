"""Adversarial tests for deterministic Customer CDM mapping handlers."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from return_platform.canonical.base import SourceProvenance
from return_platform.data_platform.mapping.handlers import (
    HandlerErrorCode,
    HandlerExecutionContext,
    HandlerInvocationError,
    HandlerPurpose,
    build_customer_account_handler_registry,
)

_CONFIG_PATH = Path(__file__).parents[1] / "config" / "data_platform" / "canonical_mappings.yaml"


def _context(
    *,
    source_system: str = "CUSTOMER_CDM",
    source_document_id: str = "PARTY-123",
) -> HandlerExecutionContext:
    """Create fixed source and configuration evidence."""
    return HandlerExecutionContext(
        source_id="source.customer_cdm.v1",
        catalog_asset_id="source.mongodb.customer_outbound_cdm",
        source_system=source_system,
        source_database="eventMessages",
        source_asset="customerOutboundCDM",
        source_document_id=source_document_id,
        source_updated_at=datetime(2026, 7, 20, 1, 2, tzinfo=UTC),
        source_version="42",
        source_event_id="event-77",
        source_hash="b" * 64,
        observed_at=datetime(2026, 7, 21, 3, 4, tzinfo=UTC),
        mapping_version="1.0",
        configuration_version="1.0",
        configuration_digest="a" * 64,
    )


def _configured_handler_names() -> set[str]:
    """Extract handler references from the trusted profile fixture."""
    document = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(document, Mapping)
    mappings = document["canonical_mappings"]
    assert isinstance(mappings, list)

    result: set[str] = set()
    for mapping in mappings:
        assert isinstance(mapping, Mapping)
        identity = mapping["identity"]
        assert isinstance(identity, Mapping)
        identity_handler = identity["handler"]
        assert isinstance(identity_handler, str)
        result.add(identity_handler)

        fields = mapping["fields"]
        assert isinstance(fields, list)
        for field in fields:
            assert isinstance(field, Mapping)
            handler = field.get("handler")
            if handler is not None:
                assert isinstance(handler, str)
                result.add(handler)
    return result


def _invoke(name: str, value: object) -> str | SourceProvenance:
    """Invoke one built-in handler using deterministic context."""
    registry = build_customer_account_handler_registry()
    return registry.invoke(name, (value,), _context())


def test_registry_covers_every_handler_referenced_by_profile() -> None:
    """Fail when YAML references a handler absent from the code-owned registry."""
    registry = build_customer_account_handler_registry()

    assert set(registry.registered_names) == _configured_handler_names()


def test_handler_purposes_match_field_and_identity_usage() -> None:
    """Classify key builders separately from normal field handlers."""
    registry = build_customer_account_handler_registry()

    assert registry.resolve("customer_key_v1").purpose is HandlerPurpose.IDENTITY
    assert registry.resolve("customer_reference_key_v1").purpose is HandlerPurpose.FIELD
    assert registry.resolve("customer_account_key_v1").purpose is HandlerPurpose.IDENTITY
    assert registry.resolve("account_number_customer_id_v1").purpose is HandlerPurpose.FIELD


def test_customer_handlers_build_confirmed_identity_values() -> None:
    """Build only the locked Customer CDM identities."""
    registry = build_customer_account_handler_registry()
    context = _context()

    assert registry.invoke("customer_cdm_source_system_v1", ("PARTY-123",), context) == (
        "CUSTOMER_CDM"
    )
    assert (
        registry.invoke(
            "source_record_id_from_party_id_v1",
            ("PARTY-123",),
            context,
        )
        == "PARTY-123"
    )
    assert registry.invoke("customer_key_v1", ("PARTY-123",), context) == ("CUSTOMER_CDM:PARTY-123")
    assert (
        registry.invoke(
            "customer_reference_key_v1",
            ("PARTY-123",),
            context,
        )
        == "CUSTOMER_CDM:PARTY-123"
    )


def test_customer_account_handlers_parse_confirmed_composite_format() -> None:
    """Parse LOGON*customerId and build the locked account identity."""
    registry = build_customer_account_handler_registry()
    context = _context()

    assert (
        registry.invoke(
            "account_number_customer_id_v1",
            ("101*CUST-9",),
            context,
        )
        == "CUST-9"
    )
    assert (
        registry.invoke(
            "customer_account_key_v1",
            ("101*CUST-9",),
            context,
        )
        == "CUSTOMER_CDM:101*CUST-9"
    )


@pytest.mark.parametrize(
    "value",
    [
        "PARTY:123",
        "PARTY*123",
        "PARTY 123",
        "PARTY\n123",
    ],
)
def test_customer_identity_rejects_ambiguous_components(value: str) -> None:
    """Reject delimiters and whitespace that corrupt composite identity."""
    with pytest.raises(HandlerInvocationError) as exc_info:
        build_customer_account_handler_registry().invoke(
            "customer_key_v1",
            (value,),
            _context(),
        )

    assert exc_info.value.code is HandlerErrorCode.INVALID_INPUT_VALUE


def test_party_identity_must_match_source_document_context() -> None:
    """Reject Customer identity detached from physical document evidence."""
    with pytest.raises(HandlerInvocationError) as exc_info:
        build_customer_account_handler_registry().invoke(
            "customer_key_v1",
            ("PARTY-OTHER",),
            _context(),
        )

    assert exc_info.value.code is HandlerErrorCode.SOURCE_CONTEXT_MISMATCH


@pytest.mark.parametrize(
    "value",
    [
        "101",
        "101*CUST*9",
        "*CUST-9",
        "101*",
        "10:1*CUST-9",
        "101*CUST 9",
        "101*CUS\nT-9",
    ],
)
def test_account_handlers_reject_ambiguous_account_number(value: str) -> None:
    """Reject malformed or delimiter-ambiguous account identities."""
    with pytest.raises(HandlerInvocationError) as exc_info:
        build_customer_account_handler_registry().invoke(
            "customer_account_key_v1",
            (value,),
            _context(),
        )

    assert exc_info.value.code is HandlerErrorCode.INVALID_INPUT_VALUE


def test_customer_handlers_require_customer_cdm_context() -> None:
    """Prevent accidental reuse against a different source system."""
    with pytest.raises(HandlerInvocationError) as exc_info:
        build_customer_account_handler_registry().invoke(
            "customer_account_key_v1",
            ("101*CUST-9",),
            _context(source_system="TDS"),
        )

    assert exc_info.value.code is HandlerErrorCode.SOURCE_CONTEXT_MISMATCH


def test_customer_document_provenance_uses_supplied_fixed_evidence() -> None:
    """Build Customer provenance without environment, clock, or random access."""
    registry = build_customer_account_handler_registry()
    context = _context()
    result = registry.invoke(
        "customer_cdm_document_provenance_v1",
        ("PARTY-123",),
        context,
    )

    assert isinstance(result, SourceProvenance)
    assert result.source_system == "CUSTOMER_CDM"
    assert result.source_database == "eventMessages"
    assert result.source_asset == "customerOutboundCDM"
    assert result.source_record_id == "PARTY-123"
    assert result.source_updated_at == context.source_updated_at
    assert result.source_version == "42"
    assert result.source_event_id == "event-77"
    assert result.source_hash == "b" * 64
    assert result.observed_at == context.observed_at
    assert result.mapping_version == "1.0"
    assert result.configuration_version == "1.0"
    assert result.configuration_digest == "a" * 64


def test_customer_account_provenance_uses_account_number_record_identity() -> None:
    """Trace the selected nested account record using confirmed accountNumber."""
    result = _invoke("customer_account_provenance_v1", "101*CUST-9")

    assert isinstance(result, SourceProvenance)
    assert result.source_record_id == "101*CUST-9"


def test_handlers_are_deterministic_for_identical_input_and_context() -> None:
    """Return equal values without reading clocks, environment, or randomness."""
    registry = build_customer_account_handler_registry()
    context = _context()

    for name, value in (
        ("customer_key_v1", "PARTY-123"),
        ("customer_reference_key_v1", "PARTY-123"),
        ("customer_account_key_v1", "101*CUST-9"),
        ("customer_cdm_document_provenance_v1", "PARTY-123"),
        ("customer_account_provenance_v1", "101*CUST-9"),
    ):
        first = registry.invoke(name, (value,), context)
        second = registry.invoke(name, (value,), context)
        assert first == second
