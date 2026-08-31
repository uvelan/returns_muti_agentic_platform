"""AMENDMENT-3: all three Support message surfaces coexist, in the document.

The amendment moved NL ingress from
`POST .../work-items/{id}/messages` to `.../inbound-messages` because the frozen
path was **already served** by `return_support.add_message`, a live
associate-facing endpoint. Its sharpest consequence was not the routing
collision but what the collision did to the published contract:

> FastAPI keys the OpenAPI document by path, so the regenerated document
> advertised the ingress handler while **dropping the associate endpoint that
> actually answers there** -- the suite would have gone green on a contract
> describing neither surface.

**What already covers this, and what does not.**
`tests/api/test_api_route_paths_are_unique.py` asserts the declaration side and
does it well: the ingress path spelled out exactly (a prefix match would accept
`/inbound-messages-v2`), no `(method, path)` pair declared twice across every
router in `return_platform.api` with parameters normalised to FastAPI's own
matching shape, and -- the half with teeth -- the associate path still claimed
**by name** by `add_message` and `list_messages`, because a `not in` assertion
would pass just as well if the whole router had been deleted.

`tests/test_openapi_contract_drift.py` asserts that the four committed documents
match what the code generates.

Neither asserts **the document contains all three operations**. Together they
come close -- code declares them, document matches code -- but that is a
transitive argument across two suites, and the failure AMENDMENT-3 actually
produced was a *document* that described neither surface. The integration agent
checked this by hand, in all four snapshots, and recorded the result in
`.plan/merge.md`. This file is that check made permanent, and it is the seam
assertion: `merge.md`'s "nobody stands at the seam" is how the approval signal
failed to decode silently between two suites that each tested their own side.

It reads the **committed** documents rather than regenerating, deliberately. The
committed copies are what the frontend generator, the MSW handlers and any
third-party client consume; regenerating here would assert the code against
itself and say nothing about the artifact anybody reads.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

#: Every committed copy, exactly the list `test_openapi_contract_drift.py` and
#: `scripts/check_openapi_drift.py` use. Four, not one -- the drift receipt's own
#: history is that regeneration covers six files across two commands, and a
#: surface present in one snapshot and absent from another is the same
#: invisibility in a smaller costume.
_SNAPSHOTS = (
    _REPOSITORY_ROOT / "openapi" / "return-platform.openapi.json",
    _REPOSITORY_ROOT / "backend" / "openapi" / "return-platform.openapi.json",
    _REPOSITORY_ROOT / "frontend" / "openapi" / "return-platform.openapi.json",
    _REPOSITORY_ROOT / "openapi.json",
)

_INGRESS = "/api/v1/return-support/work-items/{work_item_id}/inbound-messages"
_ASSOCIATE = "/api/v1/return-support/work-items/{work_item_id}/messages"

#: `(path, method, the operationId's leading handler name)`. The handler name is
#: asserted, not merely the presence of *an* operation: a document that answered
#: POST on the associate path with the ingress handler is precisely the state
#: the amendment was raised about, and "the path is present" cannot tell them
#: apart.
_REQUIRED = (
    (_INGRESS, "post", "receive_support_message"),
    (_ASSOCIATE, "post", "add_message"),
    (_ASSOCIATE, "get", "list_messages"),
)


#: Parametrised by the **relative path**, not the basename. Three of the four
#: snapshots are called `return-platform.openapi.json`, so a `path.name` id
#: collapsed them into one id and a lookup by name resolved every one of them to
#: the first file -- three parameter sets reading the same document while the
#: report showed four. Found by injection: a fault written into one file made
#: three parameter sets fail. `merge.md`'s "green because the inputs can't
#: exercise the property", in the instrument rather than in the subject.
_SNAPSHOT_IDS = {
    str(path.relative_to(_REPOSITORY_ROOT)).replace("\\", "/"): path for path in _SNAPSHOTS
}


@pytest.fixture(scope="module", params=sorted(_SNAPSHOT_IDS))
def document(request: pytest.FixtureRequest) -> dict:
    """Each committed snapshot in turn, named so a failure says which one."""
    return json.loads(_SNAPSHOT_IDS[request.param].read_text(encoding="utf-8"))


@pytest.mark.parametrize(("path", "method", "handler"), _REQUIRED)
def test_the_published_document_carries_all_three_surfaces(
    document: dict, path: str, method: str, handler: str
) -> None:
    paths = document["paths"]
    assert path in paths, (
        f"{path} is absent from the published contract. Every generated client -- "
        "the frontend types, the MSW handlers, any third party -- is built from "
        "this document, so a surface missing here is a surface that does not exist "
        "as far as anything downstream is concerned."
    )
    operations = paths[path]
    assert method in operations, (
        f"{method.upper()} {path} is absent while the path itself is present. "
        "That is the exact shape AMENDMENT-3 was raised about: two handlers shared "
        "a path, the document kept the path and one operation, and the other "
        "endpoint vanished from the contract while still answering in production."
    )
    operation_id = str(operations[method]["operationId"])
    assert operation_id.startswith(handler), (
        f"{method.upper()} {path} is documented as {operation_id!r}, which is not "
        f"{handler!r}. The path is present and answers with somebody else's "
        "handler -- a client generated from this document would call the wrong "
        "endpoint and get a plausible response."
    )


def test_the_snapshot_list_is_the_one_the_drift_check_uses() -> None:
    """So this file cannot go on checking three documents while a fourth drifts.

    The list above is a copy, and a copy of a list is a thing that goes stale
    silently. `test_openapi_contract_drift.py` already pins its own list against
    `scripts/check_openapi_drift.py`; this pins ours against that one, so the
    chain has no free end.
    """
    from tests.test_openapi_contract_drift import JSON_SNAPSHOTS

    assert set(_SNAPSHOTS) == set(JSON_SNAPSHOTS)


def test_the_three_surfaces_are_three_distinct_operations() -> None:
    """Not three names for one handler.

    Read across the whole document rather than at the three paths: an
    `operationId` reused elsewhere would mean the generator had collapsed two
    endpoints into one client method, which is the collision one layer along.
    """
    document = json.loads(_SNAPSHOTS[0].read_text(encoding="utf-8"))
    # `.get` chains rather than indexing: a document missing one of the three is
    # the *previous* test's finding, and this one must still arrive carrying its
    # own message rather than a bare `KeyError` from the middle of a
    # comprehension. Measured -- the first form did exactly that under injection.
    identifiers = [
        str(document["paths"].get(path, {}).get(method, {}).get("operationId", f"<absent {path}>"))
        for path, method, _handler in _REQUIRED
    ]
    assert len(set(identifiers)) == 3, f"the three surfaces share operation ids: {identifiers}"

    everything = [
        str(operation["operationId"])
        for operations in document["paths"].values()
        for operation in operations.values()
        if isinstance(operation, dict) and "operationId" in operation
    ]
    for identifier in identifiers:
        assert everything.count(identifier) == 1, (
            f"{identifier!r} is declared {everything.count(identifier)} times in the "
            "document -- a generated client would have one method where the platform "
            "has two endpoints"
        )
