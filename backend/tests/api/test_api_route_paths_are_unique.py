"""No two routers in this application declare the same endpoint (AMENDMENT-3).

This is the check T0 did not run, written down so it cannot be skipped again.

The V2 ingress endpoint was frozen at
`POST /api/v1/return-support/work-items/{work_item_id}/messages`. That path was
already `return_support.add_message`, a live associate-facing endpoint, and had
been since long before this programme started. Nothing failed. FastAPI does not
refuse a duplicate path: it matches the first router mounted and leaves the
second unreachable, and it keys the OpenAPI document by path, so the regenerated
document described whichever handler `include_router` reached last. A suite can
go green on a document describing neither surface.

Two properties, and the second is the one with teeth:

* the ingress route is at the amended path, exactly, spelled out rather than
  matched by prefix -- a prefix assertion passes for `/inbound-messages-v2`;
* **no `(method, path)` pair is declared twice across every router in
  `return_platform.api`**, with path parameters normalised to a placeholder
  first. Normalising is not tidiness: FastAPI matches on shape, so
  `/work-items/{work_item_id}/messages` and `/work-items/{id}/messages` are the
  same endpoint and a string comparison would call them different.

Every router is collected, not only the mounted ones. Mounting is the
integration agent's step; a collision that only appears once someone mounts the
router is a collision found too late, which is the entire history of this
amendment.
"""

from __future__ import annotations

import collections
import importlib
import pkgutil
import re

from fastapi import APIRouter

import return_platform.api as api_package

#: The amended path, written once. AMENDMENT-3 in `.plan/contracts.md` sect. 1a.
INGRESS_PATH = "/api/v1/return-support/work-items/{work_item_id}/inbound-messages"

#: The path AMENDMENT-3 moved off, and who owns it.
ASSOCIATE_MESSAGES_PATH = "/api/v1/return-support/work-items/{work_item_id}/messages"


def _normalised(path: str) -> str:
    """Path with every parameter reduced to `{}` -- FastAPI's own matching shape."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _declared_endpoints() -> dict[tuple[str, str], list[str]]:
    """`(method, normalised path)` -> the handlers that claim it.

    Routers are de-duplicated by object identity, so a module that imports
    another module's router (several do) does not report its routes twice and
    then fail this test for a collision with itself.
    """
    claims: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    seen_routers: set[int] = set()
    for module_info in pkgutil.iter_modules(api_package.__path__):
        module = importlib.import_module(f"return_platform.api.{module_info.name}")
        for attribute in dir(module):
            candidate = getattr(module, attribute)
            if not isinstance(candidate, APIRouter) or id(candidate) in seen_routers:
                continue
            seen_routers.add(id(candidate))
            for route in candidate.routes:
                for method in getattr(route, "methods", None) or ():
                    key = (method, _normalised(getattr(route, "path", "")))
                    claims[key].append(
                        f"{module_info.name}.{attribute}::{getattr(route, 'name', '?')}"
                    )
    return claims


def test_no_two_routers_in_this_application_declare_the_same_endpoint() -> None:
    """The property, over every router, mounted or not.

    Reported as the full list of offenders rather than a bare count, because a
    count tells whoever hits this nothing about which two things collided.
    """
    claims = _declared_endpoints()
    assert claims, "no routers were collected -- the walk itself is broken"
    collisions = {key: sorted(handlers) for key, handlers in claims.items() if len(handlers) > 1}
    assert collisions == {}, f"two handlers claim one endpoint: {collisions}"


def test_the_ingress_route_is_at_the_amended_path_and_owns_it_alone() -> None:
    """Exact path, exact handler. AMENDMENT-3's resolution, pinned."""
    claims = _declared_endpoints()
    assert claims[("POST", _normalised(INGRESS_PATH))] == [
        "support_ingress.router::receive_support_message"
    ]


def test_the_path_the_ingress_moved_off_still_belongs_to_the_associate_endpoint() -> None:
    """The half AMENDMENT-3 exists to protect.

    Moving the ingress is only correct if the endpoint it was about to displace
    is still there and still answering. sect. 10 puts retirement of any
    superseded surface post-gate and RV-gated; nothing here retires anything.
    A `not in` assertion would pass just as well if `return_support` had been
    deleted, so this asserts the owner by name.
    """
    claims = _declared_endpoints()
    assert claims[("POST", _normalised(ASSOCIATE_MESSAGES_PATH))] == [
        "return_support.router::add_message"
    ]
    assert claims[("GET", _normalised(ASSOCIATE_MESSAGES_PATH))] == [
        "return_support.router::list_messages"
    ]
