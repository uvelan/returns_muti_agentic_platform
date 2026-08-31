# AMENDMENT-3 — all three Support message surfaces coexist, in the document

**Test:** `backend/tests/acceptance/test_amendment_3_three_support_surfaces_coexist.py`
— 14 tests (12 parametrised × 4 documents × 3 operations, plus 2), **all green**.

## What already covers this, verified rather than duplicated

Per the brief, in-slice coverage was read before anything was written:

* `tests/api/test_api_route_paths_are_unique.py` asserts the **declaration**
  side, and asserts it well — the ingress path spelled out exactly (a prefix
  match would accept `/inbound-messages-v2`); no `(method, path)` declared twice
  across every router in `return_platform.api`, parameters normalised to
  FastAPI's own matching shape; and the associate path still claimed **by name**
  by `add_message` and `list_messages`, because a `not in` assertion would pass
  just as well if the router had been deleted. Nothing here duplicates it.
* `tests/test_openapi_contract_drift.py` asserts the four committed documents
  match what the code generates.

**Neither asserts that the published document contains all three operations.**
Together they come close — code declares them, document matches code — but that
is a transitive argument across two suites, and the failure AMENDMENT-3 actually
produced was a *document* that described neither surface. That is `merge.md`'s
"nobody stands at the seam" exactly. The integration agent checked it by hand in
all four snapshots; this file makes the check permanent.

It reads the **committed** documents rather than regenerating: the committed
copies are what the frontend generator, the MSW handlers and any third-party
client consume, and regenerating here would assert the code against itself.

It asserts the **handler behind each operation**, not merely that the path is
present: a document answering POST on the associate path with the ingress
handler is precisely the amendment's state, and presence cannot tell the two
apart. A fourth test pins the snapshot list against
`test_openapi_contract_drift.JSON_SNAPSHOTS`, so this file cannot go on checking
a stale set while a fifth document appears.

## Fault injection — two, plus one that found a defect in the test itself

| # | injected fault | result |
| --- | --- | --- |
| INJ-A3a | the amendment's own failure reproduced in **all four** documents: the ingress operation takes the associate path and the associate POST vanishes | **9 failed, 5 passed** — both affected operations in every document, plus the distinctness test |
| INJ-A3b | **one** document only (`frontend/openapi/…`), everything present, only `add_message`'s `operationId` swapped for an impostor | **1 failed, 13 passed** — exactly the injected document's parameter set, named in the failure id |

### INJ-A3b found that the test was reading two documents while reporting four

The first form parametrised the document fixture by `path.name` and resolved
each id with `next(path for path in _SNAPSHOTS if path.name == request.param)`.
**Three of the four snapshots are named `return-platform.openapi.json`**, so the
three ids collapsed to one and every lookup resolved to the first file. A fault
written into `openapi/…` alone made **three** parameter sets fail — which is how
it was caught, because one injected file cannot legitimately red three
documents.

Fixed by parametrising on the repository-relative path. Re-run of INJ-A3b then
failed exactly one parameter set, naming `frontend/openapi/…`, and INJ-A3a was
re-run afterwards so the numbers above are the corrected instrument's.

`merge.md`'s "green because the inputs can't exercise the property" — in the
instrument rather than in the subject, and found only because the injection was
made narrow enough to distinguish.

A second, smaller correction, the same lesson as INJ-10c: the distinctness test
originally indexed the document directly and raised a bare `KeyError` under
INJ-A3a. It now uses `.get` chains so its red always carries its own message.

All injections were made in the working tree and reverted with `git checkout`;
`git status` clean after each. No production file is modified.
