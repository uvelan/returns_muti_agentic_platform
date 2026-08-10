"""One way to create a return, and one way to hold a discovery conversation.

Wave D4, slice 4 -- the "associate flow drives the same session by another
route" item.

**It is already resolved.** Like the artifact pair before it, this was recorded
as an open duplicate and is not one:

* `OperationalRepository.create_return` is the *only* writer to the returns
  collection, and it has exactly two callers.
* `POST /api/v1/returns` refuses anything but `channel="SYSTEM"` with a 409 that
  names the associate flow; `submit_details` creates with `channel="ASSOCIATE"`.
  The two entry points are partitioned, not competing.
* `start_chat`/`continue_chat` do not reimplement `start`/`continue_discovery` --
  they resolve free text into anchors and delegate. A natural-language adapter
  over the structured API, one implementation underneath.

What was actually missing is anything holding that true. The partition rested on
a single untested `if` in a router: delete that line and both endpoints silently
become general-purpose return creators, with nothing failing. These tests are the
difference between "resolved" and "resolved and staying that way".
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from return_platform.api.returns import router as returns_router
from return_platform.security import roles as r
from return_platform.security.principal import Principal

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"

_VALID_RETURN = {
    "customerReference": "CUST-1",
    "orderReference": "ORD-1",
    "itemReferences": ["LINE-1"],
    "reasonCode": "DAMAGED",
}


def _client_for(*role_names: str) -> Iterator[TestClient]:
    app = FastAPI()

    @app.middleware("http")
    async def _attach(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.principal = Principal(subject="test-actor", roles=frozenset(role_names))
        request.state.correlation_id = "test-correlation-id"
        return await call_next(request)

    app.include_router(returns_router)
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# The channel partition, over HTTP
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("channel", ["ASSOCIATE", "CUSTOMER"])
def test_an_interactive_channel_cannot_create_a_return_directly(channel: str) -> None:
    """Refused before any datastore is resolved, so the 409 is the guard and not
    an accident of infrastructure being down."""
    for client in _client_for(r.RETURN_ASSOCIATE):
        response = client.post("/api/v1/returns", json={**_VALID_RETURN, "channel": channel})

    assert response.status_code == 409, response.text
    assert "associate-returns" in response.json()["detail"], (
        "a 409 that does not name the endpoint to use instead leaves the caller "
        "to guess which of nine return routers is the right one"
    )


def test_a_system_channel_request_gets_past_the_guard() -> None:
    """503, not 409: the caller cleared the channel check and the handler went on
    to look for a datastore this app does not provide. Asserting only "not 409"
    would pass if the guard were deleted."""
    for client in _client_for(r.RETURN_ASSOCIATE):
        response = client.post("/api/v1/returns", json={**_VALID_RETURN, "channel": "SYSTEM"})

    assert response.status_code == 503, response.text


def test_the_default_channel_is_system() -> None:
    """Omitting `channel` must not be a way past the guard. It defaults to
    SYSTEM, which is the honest default for a direct API call."""
    for client in _client_for(r.RETURN_ASSOCIATE):
        response = client.post("/api/v1/returns", json=_VALID_RETURN)

    assert response.status_code == 503, response.text


# ---------------------------------------------------------------------------
# Structural: one creation implementation, one discovery implementation
# ---------------------------------------------------------------------------


def _calls_to(path: Path, attribute: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [
        f"{path.relative_to(_SRC)}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    ]


def test_create_return_has_exactly_the_two_known_callers() -> None:
    """A third caller is a third way to create a return, which is the thing this
    phase exists to prevent. Adding one should require justifying it here."""
    callers = sorted(
        call for path in sorted(_SRC.rglob("*.py")) for call in _calls_to(path, "create_return")
    )
    modules = sorted({caller.rsplit(":", 1)[0] for caller in callers})

    # `repository.py` defines `create_return` but never calls it, so it is
    # correctly absent here -- the AST walk finds call sites, not definitions.
    assert modules == [
        str(Path("api/returns.py")),
        str(Path("operations/associate_flow.py")),
    ], f"unexpected create_return callers: {callers}"


def test_nothing_writes_the_returns_collection_outside_create_return() -> None:
    """The guard above is only worth anything if `create_return` is the sole
    writer. A module inserting into `returns` directly would bypass the channel
    partition, the idempotency key and the correlation id in one step."""
    repository = _SRC / "operations" / "repository.py"
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path == repository:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr not in {"insert_one", "insert_many"}:
                continue
            target = node.func.value
            if isinstance(target, ast.Attribute) and target.attr == "returns":
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == [], f"direct writes to the returns collection: {offenders}"


@pytest.mark.parametrize(
    ("chat_method", "structured_method"),
    [("start_chat", "start"), ("continue_chat", "continue_discovery")],
)
def test_the_chat_entry_points_delegate_rather_than_reimplement(
    chat_method: str, structured_method: str
) -> None:
    """`/chat` and `/messages` are not a duplicate pair.

    The chat methods resolve free text into anchors and then call the structured
    method -- a natural-language adapter over one implementation. If a future
    change forks the discovery logic instead of delegating, this fails, which is
    the moment to notice rather than after the two have drifted.
    """
    source = (_SRC / "operations" / "associate_flow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == chat_method
    )
    delegated = {
        node.func.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    }

    assert structured_method in delegated, (
        f"{chat_method} no longer delegates to {structured_method}; the chat "
        "surface has forked from the structured one"
    )
