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
import logging
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import return_platform
from return_platform.ai.gateway.interception_policy import build_interception_policy
from return_platform.ai.gateway.redaction import REDACTED, redact_payload
from return_platform.ai.gateway.structured_invocation import (
    StructuredInvocationUnavailable,
    StructuredOutputInvoker,
)
from return_platform.ai.interception.records import (
    Interception,
    InterceptionStatus,
    ResumeCommand,
)
from return_platform.ai.providers import ProviderRequest, ProviderResponse
from return_platform.ai.routing.routes import AIRoute
from return_platform.ai.routing.selection import AIRoutePool
from return_platform.ai.routing.tasks import ModelTier, load_ai_gateway_configuration
from return_platform.configuration.settings import Settings


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


# ---------------------------------------------------------------------------
# AI-03: no raw provider HTTP client outside the adapters
# ---------------------------------------------------------------------------
#
# "Raw provider HTTP client" cannot be scanned for as "an HTTP client", because
# this platform legitimately makes HTTP calls from a dozen places -- Vault,
# Temporal validation probes, the integration outbox, the external support
# provider. A scan that flagged `httpx` would fire on all of them and be turned
# off within a week.
#
# What a raw *provider* client actually needs is three things, and only two of
# them are specific enough to be a signal:
#
#   1. a provider endpoint   -- scanned above
#   2. a provider credential -- scanned here
#   3. an HTTP client        -- ubiquitous, and no signal on its own
#
# Denying 1 and 2 outside the adapter layer is what makes 3 harmless: a caller
# holding an `httpx.AsyncClient` and neither a vendor URL nor a vendor key cannot
# reach a provider with it. The third check below then pins where the AI
# package's own transport lives, so a second one inside `ai/` -- which would have
# both -- cannot appear quietly.

#: The attribute names by which a provider credential is read off `Settings`.
#: A raw client needs one of these; nothing else in the process authenticates to
#: a model vendor.
PROVIDER_CREDENTIAL_NAMES = frozenset(
    {
        "google_api_key",
        "nvidia_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "resolved_google_api_keys",
        "resolved_nvidia_api_keys",
        "resolved_openai_api_keys",
        "resolved_anthropic_api_keys",
        "google_api_key_references",
        "nvidia_api_key_references",
        "openai_api_key_references",
        "anthropic_api_key_references",
    }
)


def _credential_reads(source: str) -> list[str]:
    """Attribute *and* string forms, because only one of them is obvious.

    `settings.google_api_key` is the readable way and the way an attribute scan
    catches. `getattr(settings, "google_api_key")` and
    `settings.model_dump()["google_api_key"]` are the ways that get past one, and
    they are exactly what someone writes when they already know a direct read
    would be noticed.
    """
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in PROVIDER_CREDENTIAL_NAMES:
            found.add(node.attr)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in PROVIDER_CREDENTIAL_NAMES
        ):
            found.add(node.value)
    return sorted(found)


def test_the_credential_detector_actually_fires() -> None:
    """Negative control, and the shape of the evasion it is written against."""
    assert _credential_reads("key = settings.google_api_key\n") == ["google_api_key"]
    assert _credential_reads('key = getattr(settings, "nvidia_api_key")\n') == ["nvidia_api_key"]
    assert _credential_reads('key = dumped["anthropic_api_key"]\n') == ["anthropic_api_key"]
    # The discriminating case: a non-provider secret must not fire, or the rule
    # would forbid ordinary configuration reads and be deleted as noise.
    assert _credential_reads("password = settings.graph_password\n") == []


def test_a_provider_credential_is_read_only_where_a_provider_is_built() -> None:
    """A business package that can read a vendor key can build a client with it.

    Unlike the SDK rule this one is demonstrably live on this tree: the names
    really are read, by the adapters and by the route builder that hands them
    over. The first assertion is what proves the name list still matches
    `Settings` -- if a rename made every name stale, this scan would find nothing
    and would otherwise pass forever.
    """
    hits = {
        module: found
        for path in _sources()
        if (found := _credential_reads(path.read_text(encoding="utf-8")))
        and (module := _relative(path))
    }
    assert f"{PROVIDER_PACKAGE}google.py" in hits, (
        "the credential scan cannot see the adapter that definitely reads a key "
        f"-- the names are stale. Found: {sorted(hits)}"
    )

    scanned = 0
    offenders: dict[str, list[str]] = {}
    for path in _sources():
        module = _relative(path)
        if not module.startswith(BUSINESS_PACKAGES):
            continue
        scanned += 1
        if found := _credential_reads(path.read_text(encoding="utf-8")):
            offenders[module] = found

    assert scanned > 100, f"the business-package scan only reached {scanned} modules"
    assert offenders == {}, f"business packages read provider credentials: {offenders}"


#: How an HTTP client is *constructed*. Holding one someone else built is a
#: dependency; building one is the act of choosing where to send bytes.
_HTTP_CLIENT_CONSTRUCTORS = frozenset(
    {"httpx.AsyncClient", "httpx.Client", "aiohttp.ClientSession", "requests.Session"}
)


def _http_client_constructions(source: str) -> list[str]:
    tree = ast.parse(source)
    return sorted(
        {
            unparsed
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (unparsed := ast.unparse(node.func))
            if unparsed in _HTTP_CLIENT_CONSTRUCTORS
        }
    )


def test_the_http_client_detector_actually_fires() -> None:
    assert _http_client_constructions("c = httpx.AsyncClient(timeout=1)\n") == ["httpx.AsyncClient"]
    assert _http_client_constructions("s = aiohttp.ClientSession()\n") == ["aiohttp.ClientSession"]
    # A response object is not a client. Flagging one would make the rule fire on
    # the error handling that lives beside every real client.
    assert _http_client_constructions("raise_for_status(httpx.Response(200))\n") == []


def test_the_ai_package_has_exactly_one_http_transport() -> None:
    """`HTTPProvider._post` is it, and it is the reason the rule above is enough.

    Every adapter goes through it, so bounded timeouts, the status-to-error-code
    mapping and the JSON-shape check are properties of *the* AI transport rather
    than of whichever adapter remembered them. A second client inside `ai/` would
    have both a vendor URL and a vendor key in scope and would therefore be a
    provider call that no other rule here can see.
    """
    builders = {
        module: found
        for path in _sources()
        if (module := _relative(path)).startswith("ai/")
        and (found := _http_client_constructions(path.read_text(encoding="utf-8")))
    }
    assert builders == {f"{PROVIDER_PACKAGE}http.py": ["httpx.AsyncClient"]}, (
        f"the AI package builds HTTP clients outside its one transport: {builders}"
    )


# ---------------------------------------------------------------------------
# AI-03: nothing unredacted reaches interception *persistence*
# ---------------------------------------------------------------------------
#
# A distinct sink from provider dispatch, and the one with the worse failure
# mode. Redaction used to run at `ProviderRequest` construction -- i.e. *after*
# the interception verdict -- so a held request sealed customer data into a store
# whose entire purpose is to be opened and read by an operator, and which the
# provider never received. The store is encrypted at rest, which protects it from
# an attacker and not at all from the person it is designed for.
#
# The test below is a canary sweep rather than a single sample: PII is planted in
# every shape the redactor has to follow, and a non-sensitive control is planted
# beside each one. The controls are what make a pass meaningful -- an assertion
# that a string is absent from a payload also passes when the payload is empty,
# and "the store persisted nothing" is not the property being claimed.

CONFIG = Path(__file__).resolve().parents[1] / "config" / "ai_gateway.yaml"


class _Verdict(BaseModel):
    verdict: str


class _CapturingProvider:
    configured = True
    name = "GOOGLE"
    model = "model-standard"

    def __init__(self) -> None:
        self.requests: list[ProviderRequest] = []

    async def generate(self, request: ProviderRequest) -> ProviderResponse:
        self.requests.append(request)
        return ProviderResponse(self.name, self.model, '{"verdict":"ok"}', 10, None, 5, 15)


class _CapturingInterceptionStore:
    """Only the four methods `DurableInterceptionPolicy` calls.

    Deliberately keeps `request_payload` exactly as handed over, unsealed: what
    is being asserted is what the policy *gave* the store, so encrypting it here
    would hide the very bytes in question. The real
    `SystemStoreInterceptionStore` and its sealing are exercised against Mongo in
    `test_durable_interception_real_infra.py`.
    """

    def __init__(self) -> None:
        self.records: dict[str, Interception] = {}
        self.payloads: dict[str, dict[str, Any]] = {}

    async def open(
        self,
        *,
        interception_id: str,
        task_id: str,
        request_payload: Any,
        resume: ResumeCommand,
        expires_at: Any,
    ) -> Interception:
        record = Interception(
            interception_id=interception_id,
            task_id=task_id,
            status=InterceptionStatus.PENDING,
            resume=resume,
            created_at=expires_at,
            expires_at=expires_at,
        )
        self.records[interception_id] = record
        self.payloads[interception_id] = dict(request_payload)
        return record

    async def get(self, interception_id: str) -> Interception | None:
        return self.records.get(interception_id)

    async def request_payload(self, interception_id: str) -> dict[str, Any] | None:
        return self.payloads.get(interception_id)

    # A held request is all this file needs; the decided transitions have
    # compare-and-set semantics only the real store honours, and they are
    # exercised in `test_ai_interception_covers_every_path.py`.
    async def answer(
        self, *, interception_id: str, response_text: str, answered_by: str
    ) -> Interception:
        raise NotImplementedError

    async def allow(self, *, interception_id: str, allowed_by: str) -> Interception:
        raise NotImplementedError

    async def cancel(self, *, interception_id: str, status: InterceptionStatus) -> None:
        raise NotImplementedError

    async def list_pending(self, *, limit: int = 100) -> list[Interception]:
        return list(self.records.values())[:limit]


class _AlwaysOn:
    async def get_ai_settings(self) -> Any:
        class _View:
            interceptMode = True

        return _View()


def _interception_harness() -> tuple[
    StructuredOutputInvoker[_Verdict], _CapturingProvider, _CapturingInterceptionStore
]:
    loaded = load_ai_gateway_configuration(CONFIG)
    provider = _CapturingProvider()
    task_id = next(
        task_id
        for task_id, task in sorted(loaded.configuration.tasks.items())
        if task.tier is ModelTier.STANDARD and "SIMULATOR" not in task.allowedProviders
    )
    pool = AIRoutePool(
        (
            AIRoute(
                route_id="google/model-standard/key-1",
                provider_name="GOOGLE",
                model=provider.model,
                credential_id="key-1",
                credential_fingerprint="test",
                tier=ModelTier.STANDARD,
                provider=provider,
                provider_priority=0,
                model_priority=0,
                credential_priority=0,
            ),
        ),
        loaded.configuration,
    )
    store = _CapturingInterceptionStore()
    invoker: StructuredOutputInvoker[_Verdict] = StructuredOutputInvoker(
        settings=Settings.model_construct(
            environment="test",
            ai_gateway_configuration_path=CONFIG,
            ai_timeout_seconds=2.0,
            ai_global_timeout_seconds=10.0,
            ai_max_payload_bytes=8_192,
            ai_provider_order="GOOGLE,NVIDIA,SIMULATOR",
            ai_requests_per_minute=120,
        ),
        configuration=loaded.configuration,
        route_pool=pool,
        task_id=task_id,
        response_model=_Verdict,
        logger=logging.getLogger("test"),
        event_prefix="test",
        subject="test invocation",
        interception=build_interception_policy(
            store=store, settings_source=_AlwaysOn(), subject="test"
        ),
    )
    return invoker, provider, store


@pytest.mark.asyncio
async def test_no_shape_of_pii_survives_into_the_interception_store() -> None:
    """Every position the recursive redactor has to reach, at the persistence
    sink rather than at the dispatch sink.

    The paired controls are the point. `customer_email` and `orderId` sit in the
    same object, at the same depth, reached by the same recursion -- so if the
    canary is gone and the control is present, the redactor followed the data and
    masked leaves, which is the behaviour that keeps the agent able to reason. If
    both were gone the payload would simply not have been persisted and every
    "not in" assertion would be vacuously true.
    """
    invoker, provider, store = _interception_harness()

    with pytest.raises(StructuredInvocationUnavailable, match="HUMAN_RESPONSE"):
        await invoker.invoke(
            payload={
                # 1. a flat scalar under a sensitive key
                "customer_name": "Jane Doe",
                # 2. inside a JSON-encoded string -- `contextJson` itself
                "contextJson": json.dumps(
                    {
                        "query_evidence": [
                            {"rows": [{"customer_email": "jane@example.test", "orderId": "SO-9"}]}
                        ],
                        # 3. JSON nested inside that JSON
                        "conversationState": json.dumps({"phone": "555-0100", "turn": "T-4"}),
                        # 4. schema metadata, which must survive
                        "compact_schema": {
                            "customer_email": {"searchable": True, "type": "STRING"}
                        },
                    }
                ),
                "mode": "DECIDE",
            },
            size_probe="small",
            log_context={},
        )

    assert provider.requests == [], "a held request reached the provider"
    assert len(store.payloads) == 1, "interception ON must persist exactly one held request"
    sealed = json.dumps(next(iter(store.payloads.values())))

    for leaked in ("Jane Doe", "jane@example.test", "555-0100"):
        assert leaked not in sealed, f"interception persisted unredacted {leaked!r}"
    # The controls: same objects, same depths, not sensitive.
    for kept in ("SO-9", "T-4", "DECIDE", "searchable"):
        assert kept in sealed, f"redaction blanked the non-sensitive {kept!r}"
    assert REDACTED in sealed
