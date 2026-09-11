"""The canonical `/api/config` surface, and the rule it has to enforce.

Phase 15: "Secrets are stored in Vault and APIs return references only." A
configuration document legitimately carries references -- an operator must be
able to see *which* secret a source binds to -- and must never carry a resolved
value.

The scrub runs on the whole response rather than on hand-picked fields, and that
is the point: hand-picking is what eventually misses one. These tests pin the
distinction between a reference (passes through) and a value (masked), because
getting it backwards would either leak credentials or blind every operator
screen.
"""

from __future__ import annotations

from typing import Any

import pytest

from return_platform.configuration.api.secrets import MASKED, redact_secret_values

REFERENCE = "vault://secret/production/ai/google/credentials/key-0#api_key"


# --- references survive ------------------------------------------------------


def test_a_vault_reference_is_not_masked() -> None:
    """Masking references would make every binding screen useless while
    protecting nothing -- the reference is a pointer, not a credential."""
    payload = {"apiKey": REFERENCE}
    assert redact_secret_values(payload) == {"apiKey": REFERENCE}


def test_a_resolved_value_under_a_secret_key_is_masked() -> None:
    assert redact_secret_values({"apiKey": "AIzaSyREAL-KEY"}) == {"apiKey": MASKED}


def test_masking_is_case_and_convention_insensitive() -> None:
    """`apiKey`, `api_key`, `API_KEY` and `googleApiKey` all have to be caught.
    Enumerating exact spellings across four naming conventions is how one gets
    missed."""
    payload = {
        "apiKey": "a",
        "api_key": "b",
        "API_KEY": "c",
        "googleApiKey": "d",
        "password": "e",
        "connectionString": "f",
        "privateKey": "g",
        "refreshToken": "h",
    }
    assert set(redact_secret_values(payload).values()) == {MASKED}


def test_non_secret_fields_are_untouched() -> None:
    payload = {"provider": "GOOGLE", "model": "gemini-2.5-pro", "enabled": True, "retries": 3}
    assert redact_secret_values(payload) == payload


# --- shape is preserved ------------------------------------------------------


def test_nested_structures_are_scrubbed_all_the_way_down() -> None:
    """A release document nests domain payloads several levels deep; a scrub
    that only looked at the top level would be decorative."""
    payload: dict[str, Any] = {
        "releaseId": "r-1",
        "domains": {
            "ai_gateway": {
                "providers": [
                    {"name": "GOOGLE", "credentials": "SECRET-VALUE"},
                    {"name": "NVIDIA", "credentials": REFERENCE},
                ]
            }
        },
    }

    scrubbed = redact_secret_values(payload)

    providers = scrubbed["domains"]["ai_gateway"]["providers"]
    assert providers[0]["credentials"] == MASKED
    assert providers[1]["credentials"] == REFERENCE
    # Structure and non-secret values survive intact.
    assert providers[0]["name"] == "GOOGLE"
    assert scrubbed["releaseId"] == "r-1"


def test_non_string_values_under_secret_keys_are_left_alone() -> None:
    """A null, a boolean flag, or a nested object under a secret-ish key is
    structure rather than credential. Masking those would destroy information
    without protecting anything."""
    payload = {
        "password": None,
        "secretRequired": True,
        "credentials": {"reference": REFERENCE, "rotationDays": 30},
    }

    scrubbed = redact_secret_values(payload)

    assert scrubbed["password"] is None
    assert scrubbed["secretRequired"] is True
    assert scrubbed["credentials"] == {"reference": REFERENCE, "rotationDays": 30}


def test_lists_of_scalars_survive() -> None:
    payload = {"allowedProviders": ["GOOGLE", "NVIDIA"], "tokenBudgets": [1024, 2048]}
    assert redact_secret_values(payload) == payload


def test_an_empty_string_is_not_treated_as_a_secret() -> None:
    """Masking an empty value would tell an operator a credential is configured
    when none is -- worse than showing the empty field."""
    assert redact_secret_values({"apiKey": ""}) == {"apiKey": ""}


# --- the route surface -------------------------------------------------------


def test_the_canonical_router_is_versionless() -> None:
    """Versionless matches `/api/graph-schema`: a release, not a URL, pins
    configuration."""
    from return_platform.configuration.api.router import router

    assert router.prefix == "/api/config"


def test_the_release_lifecycle_is_the_only_mutation_surface_here() -> None:
    """Replaces `..._is_read_only`, which held while two release lifecycles
    existed and either could be blessed by a button.

    D3 settled that in favour of the graph, so a mutation surface became
    buildable -- but only *one*, and this pins its exact shape. Configuration
    changes by a release being drafted, edited and moved along its lifecycle;
    any route here outside that set and the one deliberate exception below is
    a second way to change what the platform is running.

    Promotion alone used to be the whole set, which is how the surface shipped
    able to publish a release but unable to create or edit one -- every prompt
    or policy change needed a source edit and an image rebuild. The full-document
    `PUT .../domains/{key}` the console router also declares stays off: a merge
    patch reaches the same outcome without letting a caller overwrite fields it
    never read.

    `POST /publish` and `POST /adopt-packaged` (CFG-3a) are two more genuine
    mutations, not exceptions to this rule -- each publishes a release
    exactly the way `/releases` + PATCH + `/promote` x2 would, collapsed
    into one call: `/publish` from a caller-supplied patch,
    `/adopt-packaged` from the units `adopt_packaged_configuration` decides.

    **`POST /validate/{domain_key}` is not a mutation.** It is POST-shaped
    because a payload or a patch does not fit a GET's query string, not
    because it writes -- `test_validate_a_patch_against_the_active_release`
    in `test_configuration_api.py` proves no write happens by reading the
    release back unchanged after both a valid and an invalid call. Filtering
    routes by HTTP method alone can no longer say "no write" the way it used
    to when every non-GET route here really was one, so it is named here by
    exception rather than silently widening what this assertion means.
    """
    from return_platform.configuration.api.router import router

    mutations = {
        (route.path, method)
        for route in router.routes
        for method in getattr(route, "methods", set())
        if method not in {"GET", "HEAD"}
    }
    assert mutations == {
        ("/api/config/releases", "POST"),
        ("/api/config/releases/{release_id}/domains/{domain_key}", "PATCH"),
        ("/api/config/releases/{release_id}/promote", "POST"),
        ("/api/config/validate/{domain_key}", "POST"),
        ("/api/config/publish", "POST"),
        ("/api/config/adopt-packaged", "POST"),
    }, mutations


def test_the_canonical_promotion_delegates_rather_than_reimplementing() -> None:
    """The console handler validates three behaviour domains, verifies unexpired
    runtime-validation receipts, requires `expected_head_revision`, and refreshes
    the process's active configuration. A canonical copy that did four of those
    five would be a second lifecycle wearing the same name -- which is what D3
    spent its effort deleting."""
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / ".."
        / "src"
        / "return_platform"
        / "configuration"
        / "api"
        / "router.py"
    ).resolve()
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "promote_release"
    )
    called = {
        node.func.id
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "console_promote_release_status" in called
    # And the response is re-scrubbed: `GET /releases/{id}` redacts the same
    # domain payloads, so a promote answering with them unredacted would make
    # that redaction pointless to the same caller.
    assert "_ok" in called


def test_every_canonical_response_goes_through_the_scrub() -> None:
    """The handlers build responses through one helper precisely so a new
    endpoint cannot forget the scrub. If someone adds a handler that constructs
    `APIResponse` directly, this catches it."""
    import ast
    from pathlib import Path

    source = Path(
        Path(__file__).resolve().parents[2]
        / "src"
        / "return_platform"
        / "configuration"
        / "api"
        / "router.py"
    ).read_text(encoding="utf-8")

    direct = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "APIResponse"
    ]

    # Exactly one: the `_ok` helper itself.
    assert len(direct) == 1, (
        "a handler constructs APIResponse directly instead of going through _ok(), "
        f"bypassing the secret scrub (lines {direct})"
    )


def test_no_handler_returns_a_delegate_response_unscrubbed() -> None:
    """RV CFG-1 F4, carried into CFG-3a: `/sources`, `/sources/{id}`,
    `/sources/{id}/assets/{id}`, `/audit` and `/audit/{id}` used to
    `return await console_X(...)` straight through -- the delegate's OWN
    `APIResponse`, built in `sources.py`/`audit.py`, never touched this
    router's `_ok`.

    `test_every_canonical_response_goes_through_the_scrub` above cannot see
    this: it only catches an `APIResponse(...)` CONSTRUCTED in router.py, and
    these five constructed none here at all -- they returned one built
    elsewhere. This walks every `return` statement instead and flags any
    that directly awaits a `console_*` delegate rather than passing its
    `.data` through `_ok`.
    """
    import ast
    from pathlib import Path

    source_path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "return_platform"
        / "configuration"
        / "api"
        / "router.py"
    )
    source = source_path.read_text(encoding="utf-8")

    def _is_delegate_await(node: ast.expr | None) -> bool:
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            return False
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        return name.startswith("console_")

    unscrubbed = [
        node.lineno
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Return) and _is_delegate_await(node.value)
    ]

    assert not unscrubbed, (
        "these lines return a console_* delegate's response directly, "
        f"bypassing redact_secret_values: {unscrubbed}"
    )


def test_the_audit_route_threads_actions_and_target_to_the_delegate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CFG-3a scope item 6: `?actions=...&target=...` on `GET /api/config/audit`
    reaches `AuditService.list_logs`, not just the route's own signature --
    `test_audit_filter.py` proves the service builds the right Mongo query
    from those two; this proves the query string actually gets there."""
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    import return_platform.configuration.api.router as router_module
    from return_platform.security.principal import Principal
    from return_platform.shared.contracts import APIResponse, ResponseMeta

    captured: dict[str, Any] = {}

    async def fake_list(_request: Any, _user_id: Any, **kwargs: Any) -> APIResponse[list[Any]]:
        captured.update(kwargs)
        return APIResponse(data=[], meta=ResponseMeta(request_id="test"))

    monkeypatch.setattr(router_module, "console_list_audit_logs", fake_list)

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({"console_admin"}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router_module.router)
    client = TestClient(app)

    response = client.get(
        "/api/config/audit",
        params=[
            ("actions", "CONFIGURATION_*"),
            ("actions", "AI_ROUTE_REFRESHED"),
            ("target", "release-1"),
        ],
    )

    assert response.status_code == 200, response.text
    assert captured["actions"] == ["CONFIGURATION_*", "AI_ROUTE_REFRESHED"]
    assert captured["target"] == "release-1"


def test_the_audit_route_defaults_to_unfiltered(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    import return_platform.configuration.api.router as router_module
    from return_platform.security.principal import Principal
    from return_platform.shared.contracts import APIResponse, ResponseMeta

    captured: dict[str, Any] = {}

    async def fake_list(_request: Any, _user_id: Any, **kwargs: Any) -> APIResponse[list[Any]]:
        captured.update(kwargs)
        return APIResponse(data=[], meta=ResponseMeta(request_id="test"))

    monkeypatch.setattr(router_module, "console_list_audit_logs", fake_list)

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({"console_admin"}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router_module.router)
    client = TestClient(app)

    response = client.get("/api/config/audit")

    assert response.status_code == 200, response.text
    assert captured["actions"] is None
    assert captured["target"] is None


def test_audit_reads_now_mask_a_secret_carried_in_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The behavioural proof behind the two structural tests above, on
    `/audit` and `/audit/{id}`: an audit record's `details` is a free-form
    `dict[str, Any]` -- exactly the shape a writer could accidentally stamp
    a resolved credential into -- forced here to carry one, with the live
    HTTP response read back masked.

    (`/sources`, `/sources/{id}` and `/sources/{id}/assets/{id}` answer with
    strictly-typed, `extra="forbid"` models that decline a synthetic secret
    field outright -- which is itself a second line of defence against
    exactly this leak, just not one a test can exercise by injecting a
    field the schema does not declare. Those three routes are covered by
    the structural checks above instead.)
    """
    from datetime import UTC, datetime

    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    import return_platform.configuration.api.router as router_module
    from return_platform.configuration.api.audit import AuditLog
    from return_platform.security.principal import Principal
    from return_platform.shared.contracts import APIResponse, ResponseMeta

    secret = "AIzaSyREAL-RESOLVED-KEY"
    record = AuditLog(
        id="a1",
        action="CONFIGURATION_RELEASE_PROMOTED",
        actor="operator",
        target="release-1",
        timestamp=datetime.now(UTC),
        details={"apiKey": secret},
    )

    async def fake_list(*_args: object, **_kwargs: object) -> APIResponse[list[AuditLog]]:
        return APIResponse(data=[record], meta=ResponseMeta(request_id="test"))

    async def fake_get(*_args: object, **_kwargs: object) -> APIResponse[AuditLog]:
        return APIResponse(data=record, meta=ResponseMeta(request_id="test"))

    monkeypatch.setattr(router_module, "console_list_audit_logs", fake_list)
    monkeypatch.setattr(router_module, "console_get_audit_log", fake_get)

    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="reader", roles=frozenset({"console_admin"}))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(router_module.router)
    client = TestClient(app)

    listed = client.get("/api/config/audit")
    assert listed.status_code == 200, listed.text
    assert listed.json()["data"][0]["details"]["apiKey"] == MASKED

    single = client.get("/api/config/audit/a1")
    assert single.status_code == 200, single.text
    assert single.json()["data"]["details"]["apiKey"] == MASKED
