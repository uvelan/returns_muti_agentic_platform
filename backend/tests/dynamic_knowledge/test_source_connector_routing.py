"""A connector type is not a store.

`scan_connector_registry` resolved a source to a connector by connector type
alone, which was correct while every MongoDB source lived in one database. The
return side does not: cases, RMAs, items and handling units are in the
platform's own store, and a connector is bound to one database for its lifetime.

Resolved by type, those sources reached the upstream connector and scanned
collections that were not there -- a run that completes, writes nothing, and
reports success. These pin the per-source override that closes it, and pin that
it does not change how anything else resolves.
"""

from __future__ import annotations

import pytest

from return_platform.dynamic_knowledge.schema import ActiveSchema
from return_platform.dynamic_knowledge.sync.adapters import scan_connector_registry
from return_platform.source_connectors.registry import UnreachableSource


class _Connector:
    """A named stand-in for a connector, so `resolve` can be asserted on identity.

    Not a mock of the connector protocol: nothing here is called. The registry's
    entire job is to hand back an object, and what it hands back is the assertion.
    """

    def __init__(self, name: str) -> None:
        self.name = name


def test_a_source_resolves_by_connector_type_when_nothing_overrides_it(
    active_schema: ActiveSchema,
) -> None:
    mongo = _Connector("mongo")

    registry = scan_connector_registry(schema=active_schema, mongo_connector=mongo)  # type: ignore[arg-type]

    assert registry.resolve("source_a") is mongo


def test_an_override_wins_over_the_type_default(active_schema: ActiveSchema) -> None:
    """The whole point: two MongoDB sources, two stores, two connectors."""
    upstream = _Connector("upstream")
    platform = _Connector("platform")

    registry = scan_connector_registry(
        schema=active_schema,
        mongo_connector=upstream,  # type: ignore[arg-type]
        overrides={"source_a": platform},  # type: ignore[dict-item]
    )

    assert registry.resolve("source_a") is platform


def test_an_unoverridden_source_of_the_same_type_still_uses_the_default(
    active_schema: ActiveSchema,
) -> None:
    """An override must not become a whole-type replacement.

    If it did, adding one platform-store source would silently redirect the four
    Ferguson collections into the platform database -- the same silent-empty
    failure, in the other direction.
    """
    upstream = _Connector("upstream")
    platform = _Connector("platform")
    raw = active_schema.model_dump(mode="json")
    raw["sources"]["source_c"] = {
        "source_asset_id": "source_c",
        "connector_type": "MONGODB",
        "connection_ref": "vault://source/c",
        "object_ref": {"database": "elsewhere", "name": "other_objects"},
    }
    schema = ActiveSchema.model_validate(raw)

    registry = scan_connector_registry(
        schema=schema,
        mongo_connector=upstream,  # type: ignore[arg-type]
        overrides={"source_c": platform},  # type: ignore[dict-item]
    )

    assert registry.resolve("source_c") is platform
    assert registry.resolve("source_a") is upstream


def test_a_type_with_no_connector_is_refused_rather_than_defaulted(
    active_schema: ActiveSchema,
) -> None:
    """The fixture's `source_b` is POSTGRESQL and no such connector exists.

    Refusing loudly is the behaviour that already existed and must survive the
    override path: falling back to whichever connector is registered would read
    a Postgres table's worth of nothing out of MongoDB.

    Asserted on `UnreachableSource` rather than on a bare `ValueError`, because
    the two registries this consolidated onto one raised different types for the
    same condition and callers could only catch it by catching everything. The
    message names the type and what *is* configured: "no connector registered"
    left an operator to guess which of the two answers was missing.
    """
    registry = scan_connector_registry(
        schema=active_schema,
        mongo_connector=_Connector("mongo"),  # type: ignore[arg-type]
    )

    with pytest.raises(UnreachableSource, match="no connector of that type is configured"):
        registry.resolve("source_b")
