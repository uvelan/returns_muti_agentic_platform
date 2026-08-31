"""Acceptance item 10 -- DEFERRED per AMENDMENT-8, and the deferral asserted.

Item 10 reads "Support asks a question requiring a tool -> agent resolves via
the registry, credentials never surfaced". §9's ladder is implemented, but only
its first rung (case facts) is reachable in this deployment: `GraphSyncPort`,
`GraphReadPort`, `TrustedEntityPort`, `ToolExecutor.contracts`,
`AuthorizationPort` and `principal_id` were deliberately left unwired, each with
a reason recorded in `resolver_composition.py`'s module docstring. Wiring any of
them would have required inventing a read plan, a fact-name -> entity mapping
that decides what fills a tool argument, a tool allowlist, or a service-principal
identity.

**So this module does not exercise the tool rung. It asserts that the rung is
not there** -- because AMENDMENT-8 rules the deferral must be *checkable*, not
silent. A deferred gate item becomes a verified fact, and **if a future release
wires those ports this file fails**, which is precisely the intent: item 10
returns to scope the moment it becomes reachable, rather than staying deferred
because nobody re-read a decision made in August.

**Three places must agree, and this file reads all three separately.**

1. the **released configuration** -- `support_resolver.tool_bindings` in
   `backend/config/returns/production.yaml`, read from disk;
2. the **compiled graph** -- the node set `build_resolution_ladder` produces
   from the dependencies the production factory assembles;
3. the **target map** -- the `ends` mapping on every conditional branch in that
   graph.

The third is the one nothing else checks, and it is not a formality. LangGraph
raises at compile time for a map naming a node that was never added, and at run
time for a router returning a name absent from the map -- but neither of those
fires for the failure that matters here: a branch that *can* route to a rung.
`compiled_rungs` and the node set are both statements about what exists;
the target map is the statement about what is **reachable**, and reachability is
what item 10 turns on. A node set alone would go green on a graph whose routers
could still name `route_tool`.

`tests/operations/test_support_resolver_composition.py` already asserts (1) and
(2) as part of V3's own slice, and this file deliberately does not duplicate
that work by re-deriving it -- it builds through **the same production factory**
so the two cannot drift, adds (3), and states the agreement between all three as
one assertion so a partial re-wiring cannot leave a half-truth standing.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from return_platform.configuration.settings import DEFAULT_RETURN_CONFIGURATION_PATH
from return_platform.operations.return_support.resolution_ladder import (
    RUNG_FACTS,
    build_resolution_ladder,
    compiled_rungs,
)
from return_platform.operations.return_support.resolution_state import RUNG_GRAPH, RUNG_TOOL

# The production factory, built through the slice's own harness rather than a
# copy of it. Copying the Mongo/gateway doubles here would create a second
# definition of "how this deployment is assembled", and the whole point of this
# file is that there is exactly one and everything agrees with it.
from tests.operations.test_support_resolver_composition import _built

#: The nodes a wired tool rung would add, from `build_resolution_ladder`.
_TOOL_NODES = ("route_tool",)
#: And a graph rung's, kept beside it: the released config binds neither, and a
#: deployment that quietly gained one would be the same class of surprise.
_GRAPH_NODES = ("sync_graph", "resolve_from_graph")


def _released_document() -> dict[str, Any]:
    """`production.yaml` as text, parsed -- not as a configuration object.

    Read from disk on purpose. `SupportResolverConfiguration.tool_bindings`
    defaults to `()`, so a loaded object reports "no bindings" identically
    whether the release **declares** an empty list or the key vanished from the
    document entirely. Those are different facts: the first is a deliberate
    closed default with a comment above it, the second is a deleted key nobody
    noticed. The loaded object is asserted too, below; this read is what tells
    them apart.
    """
    with DEFAULT_RETURN_CONFIGURATION_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _branch_targets(compiled: Any) -> dict[str, dict[str, str]]:
    """Every conditional branch's target map, keyed `source -> {key: node}`.

    `builder.branches` is the pre-compilation record of the `path_map` each
    `add_conditional_edges` call was given -- which is exactly the artefact
    `build_resolution_ladder` builds from `graph_rung_available` /
    `tool_rung_available`, and therefore the artefact that would carry a
    re-wiring.
    """
    return {
        source: dict(branch.ends or {})
        for source, branches in compiled.builder.branches.items()
        for branch in branches.values()
    }


@pytest.fixture(scope="module")
def deployment(monkeypatch_module: pytest.MonkeyPatch) -> Any:
    """The dependencies this deployment's factory actually assembles."""
    return _built(monkeypatch_module)._deps  # noqa: SLF001 - the inventory is the assertion


@pytest.fixture(scope="module")
def monkeypatch_module() -> Any:
    with pytest.MonkeyPatch.context() as patch:
        yield patch


class TestItem10IsUnreachableAndSaysSo:
    def test_the_released_config_binds_no_tool_at_all(self, deployment: Any) -> None:
        """Place 1: the document, and the object loaded from it.

        Both, because they can disagree in one direction: a key deleted from the
        release reads as an empty tuple on the object.
        """
        document = _released_document()
        resolver_block = document["support_resolver"]
        assert "tool_bindings" in resolver_block, (
            "`support_resolver.tool_bindings` has disappeared from production.yaml. "
            "The loaded configuration still reports no bindings, because the field "
            "defaults to (), so nothing else in the suite would notice -- but a "
            "deliberate closed default and a deleted key are not the same release."
        )
        assert resolver_block["tool_bindings"] == [], (
            "the released configuration now binds a tool. Acceptance item 10 is no "
            "longer deferred: it must be exercised, credentials-never-surfaced "
            "included (contracts.md AMENDMENT-8)."
        )
        assert deployment.configuration.tool_bindings == ()

    def test_the_compiled_graph_has_no_tool_rung(self, deployment: Any) -> None:
        """Place 2: the topology."""
        nodes = set(build_resolution_ladder(deployment).nodes)
        assert {"resolve_from_facts", "finalize", "escalate"} <= nodes, (
            "the fact rung is the one rung this deployment does serve; if it is "
            "missing the resolver is not deferred, it is broken"
        )
        for node in (*_TOOL_NODES, *_GRAPH_NODES):
            assert node not in nodes, f"{node!r} was compiled into the ladder"

    def test_no_branch_can_route_to_a_rung_this_build_does_not_serve(self, deployment: Any) -> None:
        """Place 3 -- the one nothing else in the suite reads.

        A node set says what exists. A target map says what a router is *allowed
        to name*, and that is what reachability means. These are separable: a
        map is built from the same availability flags the nodes are, so a
        re-wiring that added the map entries without the nodes would be a
        compile error -- but the reverse, and any future refactor that builds the
        map from a different source, lands here and nowhere else.
        """
        compiled = build_resolution_ladder(deployment)
        targets = _branch_targets(compiled)
        assert targets, (
            "no conditional branches at all -- the ladder is meant to have at "
            "least the post-facts descent, so this read is looking at the wrong "
            "attribute rather than at an unreachable rung"
        )
        for source, mapping in targets.items():
            for key, node in mapping.items():
                assert node not in (*_TOOL_NODES, *_GRAPH_NODES), (
                    f"branch {source!r} can route to {node!r} under key {key!r} -- "
                    "a rung this deployment cannot serve is reachable from the graph"
                )

    def test_the_three_places_agree(self, deployment: Any) -> None:
        """The agreement itself, so a partial re-wiring cannot pass.

        Stated as one identity across four reads rather than four independent
        assertions: each of the tests above can go green while another place
        disagrees, and "unreachable, and visibly so" is a claim about the
        places *agreeing*, not about any one of them.
        """
        compiled = build_resolution_ladder(deployment)
        nodes = set(compiled.nodes)
        reachable = {
            node for mapping in _branch_targets(compiled).values() for node in mapping.values()
        }

        says_tool = {
            # `.get` rather than `[...]`, so a key deleted from the release
            # reaches this assertion's own message instead of raising a KeyError
            # out of the middle of it. Its *absence* is the previous test's
            # finding; this one is about the six reads agreeing.
            "released config": bool(
                _released_document()["support_resolver"].get("tool_bindings") or ()
            ),
            "loaded config": bool(deployment.configuration.tool_bindings),
            "dependencies": deployment.tool_rung_available,
            "rung inventory": RUNG_TOOL in compiled_rungs(deployment),
            "compiled graph": any(node in nodes for node in _TOOL_NODES),
            "target map": any(node in reachable for node in _TOOL_NODES),
        }
        assert set(says_tool.values()) == {False}, (
            "the places that must agree about the tool rung do not: "
            f"{says_tool}. Either the rung has been wired (acceptance item 10 comes "
            "back into scope, per AMENDMENT-8) or one of these reads has drifted "
            "from the others, which is the half-truth the amendment exists to stop."
        )

        assert compiled_rungs(deployment) == (RUNG_FACTS,)
        assert RUNG_GRAPH not in compiled_rungs(deployment)
