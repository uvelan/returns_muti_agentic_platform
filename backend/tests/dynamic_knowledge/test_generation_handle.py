"""Resolution and leasing are one step, and nothing bypasses it.

Phase 12: "No code below handle acquisition resolves 'current generation'
independently." Slice 1 built the drain that retirement waits on; that drain is
only meaningful if the readers it protects are actually counted, so a caller
that resolves a generation without leasing it holds a pin the retirement path
cannot see.

The last test here is the one that keeps this true over time. The others prove
the mechanism works today; the architecture test is what stops the next caller
from reaching past it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from return_platform.dynamic_knowledge.lifecycle.handle import GenerationHandleProvider
from return_platform.dynamic_knowledge.lifecycle.lease_store import LeaseClass

_SRC = Path(__file__).resolve().parents[2] / "src" / "return_platform"


class _Resolver:
    def __init__(self, generation_id: str = "gen-1") -> None:
        self.generation_id = generation_id
        self.calls = 0

    async def active_generation(self, schema: object) -> str:
        self.calls += 1
        return self.generation_id


class _LeaseStore:
    def __init__(self, *, refuse: bool = False, raise_on_acquire: bool = False) -> None:
        self.refuse = refuse
        self.raise_on_acquire = raise_on_acquire
        self.held: dict[str, str] = {}
        self.released: list[str] = []
        self.raise_on_release = False

    async def acquire_read_lease(
        self,
        *,
        graph_generation_id: str,
        snapshot_activation_version: int,
        owner_instance_id: str,
        ttl_seconds: int,
    ) -> object | None:
        if self.raise_on_acquire:
            raise RuntimeError("mongo unreachable")
        if self.refuse:
            return None
        lease_id = f"lease-{len(self.held) + 1}"
        self.held[lease_id] = graph_generation_id
        return type("_Lease", (), {"lease_id": lease_id})()

    async def acquire_write_reservation(self, **kwargs: object) -> object | None:
        raise NotImplementedError

    async def release(self, *, graph_generation_id: str, lease_id: str) -> None:
        if self.raise_on_release:
            raise RuntimeError("mongo unreachable")
        self.released.append(lease_id)
        self.held.pop(lease_id, None)

    async def begin_drain(self, *, graph_generation_id: str) -> None:
        self.refuse = True

    async def outstanding(self, *, graph_generation_id: str) -> dict[LeaseClass, int]:
        return {
            LeaseClass.READ: sum(1 for g in self.held.values() if g == graph_generation_id),
            LeaseClass.WRITE: 0,
        }


@pytest.mark.asyncio
async def test_the_generation_is_leased_for_the_block_and_released_after() -> None:
    store = _LeaseStore()
    provider = GenerationHandleProvider(_Resolver(), lease_store=store)

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.graph_generation_id == "gen-1"
        assert handle.leased is True
        assert (await store.outstanding(graph_generation_id="gen-1"))[LeaseClass.READ] == 1

    assert store.released == ["lease-1"]
    assert (await store.outstanding(graph_generation_id="gen-1"))[LeaseClass.READ] == 0


@pytest.mark.asyncio
async def test_a_failing_turn_still_gives_the_lease_back() -> None:
    """A leaked lease turns a transient request failure into an operational one:
    the next retirement blocks for the full TTL waiting on a reader that died."""
    store = _LeaseStore()
    provider = GenerationHandleProvider(_Resolver(), lease_store=store)

    with pytest.raises(RuntimeError, match="turn exploded"):
        async with provider.acquire_read(object()):  # type: ignore[arg-type]
            raise RuntimeError("turn exploded")

    assert store.released == ["lease-1"]


@pytest.mark.asyncio
async def test_the_generation_is_resolved_exactly_once_per_acquisition() -> None:
    """Re-resolving mid-request would let one request read half its data from
    one generation and half from its successor, making the cutover visible as
    inconsistency instead of an atomic swap."""
    resolver = _Resolver()
    provider = GenerationHandleProvider(resolver, lease_store=_LeaseStore())

    async with provider.acquire_read(object()):  # type: ignore[arg-type]
        pass

    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_a_draining_generation_yields_an_unleased_handle_rather_than_failing() -> None:
    """Refusal means the cutover happened between resolution and acquisition.
    Until REBIND_ON_RESUME exists, degrading to the unleased behaviour that
    shipped before this module beats failing a request that would have worked --
    but `leased` has to say so, or nothing can tell the difference."""
    provider = GenerationHandleProvider(_Resolver(), lease_store=_LeaseStore(refuse=True))

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.leased is False
        assert handle.lease_id is None
        assert handle.graph_generation_id == "gen-1"


@pytest.mark.asyncio
async def test_a_lease_store_outage_does_not_fail_the_request() -> None:
    provider = GenerationHandleProvider(_Resolver(), lease_store=_LeaseStore(raise_on_acquire=True))

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.leased is False


@pytest.mark.asyncio
async def test_a_release_failure_does_not_fail_the_request() -> None:
    """The TTL is the backstop. Converting a cleanup error into a user-visible
    failure buys nothing -- the lease expires either way."""
    store = _LeaseStore()
    store.raise_on_release = True
    provider = GenerationHandleProvider(_Resolver(), lease_store=store)

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.leased is True


@pytest.mark.asyncio
async def test_without_a_lease_store_the_handle_is_honest_about_being_unleased() -> None:
    provider = GenerationHandleProvider(_Resolver(), lease_store=None)

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.graph_generation_id == "gen-1"
        assert handle.leased is False


# --- the rule itself --------------------------------------------------------


def test_only_the_handle_provider_resolves_the_current_generation() -> None:
    """Phase 12's "no code below handle acquisition resolves 'current
    generation' independently", enforced rather than documented.

    Matches `.active_generation(...)` call sites by AST rather than by grep so a
    method *definition* (the protocol declaration, the Mongo implementation) is
    not mistaken for a call. `handle.py` is the sanctioned caller; `coordinator.py`
    still declares the `GraphStateProvider` protocol and passes the resolver in,
    which is why the check is on calls and not on imports.
    """
    offenders: list[str] = []
    for path in _SRC.rglob("*.py"):
        if path.name == "handle.py" and path.parent.name == "lifecycle":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "active_generation"
            ):
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")

    assert offenders == [], (
        "these call active_generation() directly instead of going through "
        "GenerationHandleProvider.acquire_read(), so the generation they pin is "
        "invisible to retirement's drain: " + ", ".join(offenders)
    )


# --- which "current generation" wins ----------------------------------------


class _SnapshotStore:
    def __init__(self, snapshot: object | None = None, *, raises: bool = False) -> None:
        self.snapshot = snapshot
        self.raises = raises

    async def read(self, *, snapshot_name: str) -> object | None:
        if self.raises:
            raise RuntimeError("mongo unreachable")
        return self.snapshot

    async def compare_and_swap(self, **kwargs: object) -> bool:  # pragma: no cover
        raise NotImplementedError


def _snapshot(graph_generation_id: str, activation_version: int) -> object:
    return type(
        "_Snapshot",
        (),
        {
            "graph_generation_id": graph_generation_id,
            "activation_version": activation_version,
        },
    )()


@pytest.mark.asyncio
async def test_the_active_runtime_snapshot_wins_over_the_legacy_resolver() -> None:
    """Two notions of "current" exist: the older `dynamic_graph_generations`
    lookup, and ActiveRuntimeSnapshot -- the pointer the activation
    compare-and-swap actually moves. Resolving the older one would let a request
    read a generation the cutover has already replaced."""
    resolver = _Resolver("gen-legacy")
    store = _LeaseStore()
    provider = GenerationHandleProvider(
        resolver,
        lease_store=store,
        snapshot_store=_SnapshotStore(_snapshot("gen-snapshot", 7)),  # type: ignore[arg-type]
    )

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.graph_generation_id == "gen-snapshot"
    # The legacy resolver was not consulted at all.
    assert resolver.calls == 0


@pytest.mark.asyncio
async def test_the_real_activation_version_reaches_the_lease() -> None:
    """Carried through instead of the 0 placeholder earlier slices recorded, so
    a lease says which activation it belongs to."""
    recorded: list[int] = []
    store = _LeaseStore()
    original = store.acquire_read_lease

    async def _recording(**kwargs: object) -> object | None:
        recorded.append(int(kwargs["snapshot_activation_version"]))  # type: ignore[arg-type]
        return await original(**kwargs)  # type: ignore[arg-type]

    store.acquire_read_lease = _recording  # type: ignore[assignment]
    provider = GenerationHandleProvider(
        _Resolver(),
        lease_store=store,
        snapshot_store=_SnapshotStore(_snapshot("gen-snapshot", 7)),  # type: ignore[arg-type]
    )

    async with provider.acquire_read(object()):  # type: ignore[arg-type]
        pass

    assert recorded == [7]


@pytest.mark.asyncio
async def test_no_snapshot_yet_falls_back_to_the_legacy_resolver() -> None:
    """Production has never run a rebuild, so there is no snapshot and
    LEGACY_GENERATION_ID is still what serves. Removing the fallback would break
    every request until the first rebuild completed."""
    resolver = _Resolver("gen-legacy")
    provider = GenerationHandleProvider(
        resolver, lease_store=_LeaseStore(), snapshot_store=_SnapshotStore(None)
    )

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.graph_generation_id == "gen-legacy"
    assert resolver.calls == 1


@pytest.mark.asyncio
async def test_a_snapshot_read_failure_falls_back_rather_than_failing_the_request() -> None:
    resolver = _Resolver("gen-legacy")
    provider = GenerationHandleProvider(
        resolver,
        lease_store=_LeaseStore(),
        snapshot_store=_SnapshotStore(raises=True),
    )

    async with provider.acquire_read(object()) as handle:  # type: ignore[arg-type]
        assert handle.graph_generation_id == "gen-legacy"
