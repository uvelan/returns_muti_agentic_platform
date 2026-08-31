# Acceptance item 10 — the deferral, asserted

AMENDMENT-8 defers item 10 ("Support asks a question requiring a tool → agent
resolves via the registry, credentials never surfaced") and rules that **the
deferral must itself be checkable**. This is that check.

**Test:** `backend/tests/acceptance/test_item_10_the_tool_rung_is_unreachable.py`
— 4 tests, normal suite (no live infra), **4 passed in 1.4s**.

## The three places, read separately

| # | place | read | result |
| --- | --- | --- | --- |
| 1 | released config | `production.yaml` parsed **from disk as YAML**, plus the loaded `SupportResolverConfiguration` | `tool_bindings: []`, key present, loaded value `()` |
| 2 | compiled graph | node set of `build_resolution_ladder(deps)` where `deps` come from the **production factory** `build_support_resolution_ladder` | no `route_tool`, no `sync_graph`, no `resolve_from_graph`; `resolve_from_facts` / `finalize` / `escalate` present |
| 3 | target map | `ends` of every conditional branch in that graph | no branch names a tool or graph node |

Plus a fourth test asserting the **agreement** of six reads as one identity
(document, loaded object, `deps.tool_rung_available`, `compiled_rungs`, node
set, target map) — because each of the three above can be green while another
disagrees, and "unreachable, and visibly so" is a claim about them agreeing.

**Why the target map is not a formality.** LangGraph raises at compile time for
a map naming an absent node, and at run time for a router returning a name
absent from the map. Neither fires for the thing item 10 turns on: a branch that
*can route to* a rung. The node set says what exists; the target map says what
is reachable. Nothing else in the suite reads it.

**What is deliberately not duplicated.** V3's own
`tests/operations/test_support_resolver_composition.py` already asserts places 1
and 2. This file builds through **the same production factory** (importing that
module's `_built` rather than copying its doubles, so a second definition of
"how this deployment is assembled" cannot appear), adds place 3, and adds the
agreement.

## Fault injection — three, each verified to have landed

| # | injected fault | anchor verified | result |
| --- | --- | --- | --- |
| INJ-10a | `LadderDependencies(… trusted_facts=…, tools=object(), principal_id=…)` in `build_support_resolution_ladder` | read-back of lines 305-316 confirms it landed in the ladder factory's dependency construction, not the dispatcher's | **3 failed, 1 passed** — topology, target map and agreement red; **the config test stays green**, correctly, because no config was touched |
| INJ-10b | `production.yaml` `tool_bindings: []` → one valid released binding | `count(old) == 1`; the parsed block read back | **2 failed, 2 passed** — config and agreement red; **the topology tests stay green** |
| INJ-10c | `tool_bindings` key deleted from `production.yaml` outright | `grep -c` → 0 occurrences | **1 failed, 3 passed** — the presence assertion fires with its own message |

**The two asymmetries are the verification.** INJ-10a and INJ-10b are mirror
images: each reds exactly the reads it should and leaves the others green. A
generic breakage — a bad import, a broken factory — would have taken all four
down together, which is `merge.md`'s newest shape (*an injection red for the
wrong reason*) and is what this pattern rules out.

**INJ-10b's first attempt was an invalid injection, and was discarded rather
than recorded.** Its `input_schema_ref: shipment_status.v1` is not in this
build's schema allowlist, so `ReturnPlatformConfiguration` raised a
`ValidationError` and all four tests **errored** rather than failed. That is a
red for the wrong reason exactly: the release never loaded, so no assertion ran.
Re-injected with `graph.shipment_status.v1` (a real entry), and only then did
the red mean anything.

A second, smaller correction came out of INJ-10c: the agreement test originally
indexed the document with `[...]`, so a deleted key raised a bare `KeyError` out
of the middle of it. Changed to `.get(...)`, so its red always arrives carrying
its own explanation. The key's *absence* is the first test's finding; the
agreement test is about the six reads agreeing.

Every injection reverted with `git checkout`; `git status` clean after each. No
production file is modified by this branch.

## Standing consequence

If a future release wires `TrustedEntityPort` / `ToolExecutor` / `principal_id`,
or publishes a `tool_bindings` entry, **this file fails** and item 10 returns to
scope with its full obligation (registry resolution *and* credentials never
surfaced). That is the intent, not a fragility.
