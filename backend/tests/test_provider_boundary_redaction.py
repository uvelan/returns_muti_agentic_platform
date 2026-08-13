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

import ast
import json
from pathlib import Path
from typing import Any

import return_platform
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


# ---------------------------------------------------------------------------
# AI-03: the provider boundary, asserted structurally
# ---------------------------------------------------------------------------
#
# What used to be here was a grep of `structured_invocation.py` for the literal
# `user_payload=redact_payload(`. It was the right instinct and the wrong
# anchor: it pinned a security property to a FILE. AI-02 moved redaction to the
# single dispatch boundary and AI-01 moved it earlier still -- ahead of the
# interception decision, so a held request cannot be persisted richer than what a
# provider would have received -- and the grep began failing while the property
# it named was strictly stronger than before. A test that fails when the code
# gets safer is guarding nothing; it is the false-confidence class the audit
# catalogues as TEST-02, inverted.
#
# These replace it with the invariants themselves. They are AST-based, so they
# survive the boundary being moved again, and they fail only when a payload can
# actually escape unmasked.

SOURCE_ROOT = Path(return_platform.__file__).resolve().parent

#: Provider adapters build and consume `ProviderRequest` because they *are* the
#: providers. Everything else is a caller, and a caller assembling its own
#: outbound request is a caller that has started to grow a second boundary.
PROVIDER_PACKAGE = "ai/providers/"
DISPATCH_MODULE = "ai/gateway/final_dispatch.py"

#: Packages that hold business logic. None of them may know a provider exists:
#: an agent holding a client can send whatever it likes, and no amount of
#: redaction at a boundary it bypasses will help.
BUSINESS_PACKAGES = (
    "agents/",
    "dynamic_knowledge/",
    "operations/",
    "workflows/",
    "conversation/",
    "graph_schema_analyzer/",
    "data_platform/",
    "canonical/",
)

#: Vendor SDKs. Importing one outside an adapter is the direct route around
#: every control the boundary applies.
PROVIDER_SDKS = frozenset(
    {
        "openai",
        "anthropic",
        "ollama",
        "cohere",
        "mistralai",
        "litellm",
        "groq",
        "together",
        "google.generativeai",
        "google.genai",
        "vertexai",
    }
)


def _sources() -> list[Path]:
    return sorted(SOURCE_ROOT.rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(SOURCE_ROOT).as_posix()


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module)
    return roots


def _first_line(tree: ast.AST, predicate: Any) -> int | None:
    return min(
        (node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and predicate(node)),
        default=None,
    )


def test_the_payload_is_masked_before_it_can_be_dispatched_or_persisted() -> None:
    """The ordering AI-01 made load-bearing.

    Interception persists the held request so a human can read it. Masking at
    `ProviderRequest` construction -- where it was -- would seal customer data
    into a store the provider itself never received. Both sinks are downstream of
    one `redact_payload` call, and this asserts that ordering over the parse tree
    rather than over the text.
    """
    tree = ast.parse((SOURCE_ROOT / DISPATCH_MODULE).read_text(encoding="utf-8"))

    redaction = _first_line(
        tree,
        lambda call: isinstance(call.func, ast.Name) and call.func.id == "redact_payload",
    )
    interception = _first_line(
        tree,
        lambda call: isinstance(call.func, ast.Attribute) and call.func.attr == "decide",
    )
    dispatch = _first_line(
        tree,
        lambda call: isinstance(call.func, ast.Name) and call.func.id == "ProviderRequest",
    )

    assert redaction is not None, "the dispatch boundary does not redact at all"
    assert interception is not None and dispatch is not None
    assert redaction < interception, (
        "redaction must precede the interception decision, or a held request is "
        "persisted with more than the provider would have seen"
    )
    assert redaction < dispatch, "redaction must precede provider dispatch"


def test_exactly_one_place_builds_an_outbound_provider_request() -> None:
    """The claim above is only worth anything if there is one place to make it.

    Before AI-02 there were three, each with its own copy of the machinery, and
    one of them -- the dependency simulator's -- had no redaction of any kind.
    """
    offenders: dict[str, list[int]] = {}
    for path in _sources():
        module = _relative(path)
        if module == DISPATCH_MODULE or module.startswith(PROVIDER_PACKAGE):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ProviderRequest"
        ]
        if lines:
            offenders[module] = lines

    assert offenders == {}, (
        f"outbound provider requests are built outside the boundary: {offenders}"
    )


def _sdk_imports(source: str) -> list[str]:
    """The detector, separated from the corpus so it can be tested on its own.

    This platform reaches providers over plain HTTP and imports no vendor SDK
    anywhere -- which is the correct state and also means a scan of the real tree
    finds nothing whether the detector works or not. A guard that cannot be shown
    to fire is not a guard, so `test_the_sdk_detector_actually_fires` runs it
    against a module that violates the rule.
    """
    tree = ast.parse(source)
    return sorted(
        name
        for name in _imported_roots(tree)
        if name in PROVIDER_SDKS or name.split(".")[0] in PROVIDER_SDKS
    )


def test_the_sdk_detector_actually_fires() -> None:
    """Negative control. Without this the test below is decoration."""
    assert _sdk_imports("import openai\n") == ["openai"]
    assert _sdk_imports("from anthropic import Anthropic\n") == ["anthropic"]
    assert _sdk_imports("import google.generativeai as genai\n") == ["google.generativeai"]
    assert _sdk_imports("from return_platform.ai.gateway import service\n") == []


def test_no_business_package_imports_a_provider_sdk() -> None:
    """A business agent holding a vendor client can send whatever it likes.

    Redaction, safety inspection, interception, pricing and telemetry all live at
    a boundary; an import that reaches past it makes every one of them optional.
    """
    scanned = 0
    offenders: dict[str, list[str]] = {}
    for path in _sources():
        module = _relative(path)
        if not module.startswith(BUSINESS_PACKAGES):
            continue
        scanned += 1
        found = _sdk_imports(path.read_text(encoding="utf-8"))
        if found:
            offenders[module] = found

    # A path filter that silently matched nothing would make this pass forever.
    assert scanned > 100, f"the business-package scan only reached {scanned} modules"
    assert offenders == {}, f"business packages import provider SDKs: {offenders}"


#: Vendor endpoints. Present in `settings.py` as configuration defaults, which is
#: what makes this scan demonstrably able to see them.
PROVIDER_HOSTS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "integrate.api.nvidia.com",
    "api.mistral.ai",
    "api.cohere.ai",
    "api.groq.com",
)


def _endpoint_hits() -> dict[str, list[str]]:
    return {
        _relative(path): found
        for path in _sources()
        if (found := [host for host in PROVIDER_HOSTS if host in path.read_text(encoding="utf-8")])
    }


def test_no_provider_endpoint_is_named_outside_the_adapters_and_settings() -> None:
    """A raw HTTP client pointed at a vendor is an adapter someone did not call
    an adapter. Base URLs are configuration; they belong in `settings.py` and in
    the adapters that consume them, and nowhere a business package can reach.

    Unlike the SDK rule, this one is self-evidently live: the endpoints really do
    appear in the tree, so the assertion is about *where*, and a broken scan
    would fail rather than pass.
    """
    hits = _endpoint_hits()
    assert hits, "the endpoint scan found no provider hosts at all -- it is broken"

    allowed = (PROVIDER_PACKAGE, "configuration/settings.py")
    offenders = {module: found for module, found in hits.items() if not module.startswith(allowed)}

    assert offenders == {}, f"provider endpoints named outside the adapter layer: {offenders}"
