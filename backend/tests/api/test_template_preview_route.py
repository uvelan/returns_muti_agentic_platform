"""`POST /api/v1/config/support-template/preview` -- the editor's dry run.

The properties that matter: the gate is `RETURNS_SESSION_READ` and nothing
weaker; the render happens against the built-in sample case (the body carries
no case id, so no real case is reachable); an invalid draft is a 422 with the
model's own refusal; the operator-chosen context drives variant selection.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api.template_preview import router
from return_platform.security import roles as r
from return_platform.security.principal import Principal


def _client(
    *,
    roles: frozenset[str] = frozenset({r.RETURN_ASSOCIATE}),
    authenticated: bool = True,
) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        if authenticated:
            request.state.principal = Principal(subject="operator-1", roles=roles)
        return await call_next(request)

    app.include_router(router)
    with TestClient(app) as client:
        yield client


def _template(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "template_id": "support-handoff",
        "default_variant_id": "default",
        "variants": [
            {
                "variant_id": "default",
                "subject_template": "Return {order_number}",
                "sections": [
                    {
                        "section_id": "order",
                        "title": "Order:",
                        "fields": [
                            {
                                "field_id": "order_number",
                                "label": "Order Number",
                                "source_binding": "case_fact:confirmed_order_reference",
                                "required": True,
                            }
                        ],
                    }
                ],
            },
            {
                "variant_id": "ltl",
                "selector": {"shipping_modes": ["BRANCH_LTL", "OFFSITE_LTL"]},
                "subject_template": "Freight return {order_number}",
                "sections": [
                    {
                        "section_id": "order",
                        "title": "Order:",
                        "fields": [
                            {
                                "field_id": "order_number",
                                "label": "Order Number",
                                "source_binding": "case_fact:confirmed_order_reference",
                            }
                        ],
                    }
                ],
            },
        ],
    }
    payload.update(overrides)
    return payload


def test_a_valid_draft_previews_against_the_sample_case() -> None:
    for client in _client():
        response = client.post(
            "/api/v1/config/support-template/preview", json={"template": _template()}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["variant_id"] == "default"
    assert body["subject"] == "Return SAMPLE-ORDER-1"
    assert "- Order Number: SAMPLE-ORDER-1" in body["text"]
    assert body["gaps"] == []
    assert body["review_blocked"] is False
    (section,) = body["sections"]
    (field,) = section["fields"]
    assert field["source"] == "case_fact"
    assert field["source_path"] == "confirmed_order_reference"


def test_the_context_selects_the_variant() -> None:
    for client in _client():
        response = client.post(
            "/api/v1/config/support-template/preview",
            json={
                "template": _template(),
                "context": {"shipping_modes": ["BRANCH_LTL"], "item_count": 2},
            },
        )
    assert response.status_code == 200, response.text
    assert response.json()["variant_id"] == "ltl"


def test_an_invalid_draft_is_a_422_not_a_render() -> None:
    template = _template()
    template["variants"][0]["sections"][0]["fields"][0]["formatter"] = "jinja2"
    for client in _client():
        response = client.post(
            "/api/v1/config/support-template/preview", json={"template": template}
        )
    assert response.status_code == 422
    assert "unknown formatter" in response.text


def test_an_empty_draft_is_a_422_not_a_500() -> None:
    for client in _client():
        response = client.post(
            "/api/v1/config/support-template/preview",
            json={"template": {"template_id": "t", "default_variant_id": "default"}},
        )
    assert response.status_code == 422
    assert "no variants" in response.text


def test_a_gapping_required_field_previews_as_a_gap() -> None:
    template = _template()
    template["variants"][0]["sections"][0]["fields"][0]["source_binding"] = (
        "case_fact:no_such_fact"
    )
    for client in _client():
        response = client.post(
            "/api/v1/config/support-template/preview", json={"template": template}
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["review_blocked"] is True
    (gap,) = body["gaps"]
    assert gap["field_id"] == "order_number"


def test_without_the_capability_the_answer_is_403() -> None:
    # An authenticated principal whose role grants nothing: 403, not 401.
    for client in _client(roles=frozenset({"unmapped_role"})):
        response = client.post(
            "/api/v1/config/support-template/preview", json={"template": _template()}
        )
    assert response.status_code == 403


def test_without_a_principal_the_answer_is_401() -> None:
    for client in _client(authenticated=False):
        response = client.post(
            "/api/v1/config/support-template/preview", json={"template": _template()}
        )
    assert response.status_code == 401
