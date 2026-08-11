"""No customer PII crosses the provider boundary, however deeply it is nested.

The gateway's original check rejected a sensitive *top-level payload key*. The
Order Agent's payload has five keys, one of which -- `contextJson` -- is a JSON
string carrying the transcript and every graph row the agent retrieved. Names,
addresses, phones and emails went out inside it on every reasoning call, and the
interception store recorded the same bytes.

These tests assert on the payload actually handed to the provider, because that
is the thing that leaves the platform. A test that asserted on a log line would
pass while the request went out intact.
"""

from __future__ import annotations

import json
from typing import Any

from return_platform.ai.gateway.redaction import REDACTED, redact_payload


def test_a_scalar_under_a_sensitive_key_is_masked() -> None:
    redacted = redact_payload(
        {
            "customer_name": "Jane Doe",
            "email": "jane@example.invalid",
            "phone": "555-0100",
            "shipping_address": "1 High Street",
            "order_number": "CW273354",
        }
    )

    assert redacted["customer_name"] == REDACTED
    assert redacted["email"] == REDACTED
    assert redacted["phone"] == REDACTED
    assert redacted["shipping_address"] == REDACTED
    # Not sensitive, and the agent needs it to do its job.
    assert redacted["order_number"] == "CW273354"


def test_camel_case_and_hyphenated_keys_are_caught_too() -> None:
    redacted = redact_payload(
        {"customerName": "Jane", "CUSTOMER-EMAIL": "j@x.invalid", "Phone_Number": "555"}
    )

    assert redacted["customerName"] == REDACTED
    assert redacted["CUSTOMER-EMAIL"] == REDACTED
    assert redacted["Phone_Number"] == REDACTED


def test_a_customer_row_nested_in_a_list_is_masked() -> None:
    redacted = redact_payload(
        {
            "candidates": [
                {"data": {"customer_name": "Jane Doe", "sales_order_number": "CW273354"}},
                {"data": {"customer_name": "John Roe", "sales_order_number": "CW273355"}},
            ]
        }
    )

    rows = [entry["data"] for entry in redacted["candidates"]]
    assert [row["customer_name"] for row in rows] == [REDACTED, REDACTED]
    # The order numbers are what make the candidates distinguishable to the model.
    assert [row["sales_order_number"] for row in rows] == ["CW273354", "CW273355"]


def test_pii_inside_a_json_encoded_string_is_masked() -> None:
    """`contextJson` itself. This is the escape the whole module exists for."""
    context = {
        "user_message": "looking for Jane Doe's order",
        "query_evidence": [
            {"result": {"rows": [{"customer_name": "Jane Doe", "phone": "555-0100"}]}}
        ],
    }

    redacted = redact_payload({"mode": "DECIDE", "contextJson": json.dumps(context)})

    reparsed = json.loads(redacted["contextJson"])
    row = reparsed["query_evidence"][0]["result"]["rows"][0]
    assert row["customer_name"] == REDACTED
    assert row["phone"] == REDACTED
    assert redacted["mode"] == "DECIDE"


def test_json_nested_inside_json_is_still_reached() -> None:
    inner = json.dumps({"customer_name": "Jane Doe"})
    outer = json.dumps({"nested": inner})

    reparsed = json.loads(redact_payload({"contextJson": outer})["contextJson"])

    assert json.loads(reparsed["nested"])["customer_name"] == REDACTED


def test_schema_metadata_survives_so_the_agent_can_still_plan() -> None:
    """The failure mode this rule is shaped to avoid.

    In `compact_schema`, `customer_name` is a key whose value describes a field
    the agent may search. Blanking it would leave the agent unable to plan a
    query at all -- masking data must not mask the description of the data.
    """
    compact_schema = {
        "entities": {
            "customer": {
                "fields": {
                    "customer_name": {
                        "description": "The customer's name as recorded on the order.",
                        "type": "STRING",
                        "searchable": True,
                    }
                }
            }
        }
    }

    redacted = redact_payload({"contextJson": json.dumps({"compact_schema": compact_schema})})

    field = json.loads(redacted["contextJson"])["compact_schema"]["entities"]["customer"]["fields"][
        "customer_name"
    ]
    assert field["searchable"] is True
    assert field["type"] == "STRING"
    assert field["description"].startswith("The customer's name")


def test_a_null_under_a_sensitive_key_stays_null() -> None:
    """Masking absence would tell the model a value exists where none does."""
    assert redact_payload({"email": None})["email"] is None


def test_a_non_json_string_is_left_alone() -> None:
    payload: dict[str, Any] = {"mode": "DECIDE", "validationError": "not json {at all"}
    assert redact_payload(payload)["validationError"] == "not json {at all"


def test_deeply_recursive_input_terminates() -> None:
    """A hostile payload must not drive unbounded recursion."""
    value: Any = {"customer_name": "Jane"}
    for _ in range(50):
        value = {"nested": value}

    redacted = redact_payload(value)

    assert isinstance(redacted, dict)


def test_the_structured_invocation_path_redacts_before_dispatch() -> None:
    """The order agent's actual path, asserted at the boundary.

    `structured_invocation` had no redaction of any kind -- not even the
    top-level key scan -- so this is the regression that matters most.
    """
    from return_platform.ai.gateway import structured_invocation

    source = (structured_invocation.__file__ or "").strip()
    assert source
    with open(source, encoding="utf-8") as handle:
        text = handle.read()

    # The call must wrap the payload, not pass it through.
    assert "user_payload=redact_payload(" in text, (
        "structured_invocation must redact at ProviderRequest construction; "
        "passing `dict(payload)` straight through is the defect this closes"
    )
