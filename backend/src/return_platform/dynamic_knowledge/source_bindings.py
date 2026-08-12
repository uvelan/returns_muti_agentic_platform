"""Where a dataset the schema names actually lives.

**Shape and binding are different decisions and change on different clocks.**
The graph's shape -- what an Order is, which properties it carries, how it
relates to a line -- is designed once and edited rarely. Where `salesInv` lives,
which connection reaches it, and which column advances the cursor changes when
infrastructure changes, which is often and for reasons that have nothing to do
with the graph.

They were the same thing until now. The release compiler resolved a draft's
dataset name by matching it against the shipped schema's source asset ids and
then against their object references -- a guess that worked for the datasets
already in the file and failed for anything else. Rebinding meant editing the
schema, and an entity could not be pointed at a source the baseline had never
heard of at all.

A binding is therefore a first-class, separately editable record: the draft
names a dataset, this says how to reach it, and a release captures the answer at
the moment it was compiled. The catalogue below is the resolution -- overrides
first, then whatever the configured baseline already knows -- and it is pure, so
"which source would this draft compile against" is answerable without a
database.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydantic import BaseModel, ConfigDict

from return_platform.dynamic_knowledge.schema import ActiveSchema, SourceAssetDefinition

__all__ = ["SourceBinding", "SourceBindingCatalogue", "catalogue_from"]


class SourceBinding(BaseModel):
    """One dataset name, resolved to a reachable source asset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # What the draft calls it. Looser than an identifier because source-side
    # names carry dots and hyphens (`sales.salesInv`, `order-lines`), which is
    # the same vocabulary the analyzer's mutations accept.
    dataset: str
    # What the compiled release will call it. Separate from `dataset` because
    # the runtime's source ids are identifiers and a dataset name need not be.
    asset: SourceAssetDefinition

    def rebound_to(self, asset: SourceAssetDefinition) -> SourceBinding:
        return SourceBinding(dataset=self.dataset, asset=asset)


class SourceBindingCatalogue:
    """Dataset name -> source asset, with overrides layered over configuration.

    Pure and constructed from data, not from a client, so the same resolution
    runs in a test, in the compiler and in a console preview. An unknown dataset
    resolves to nothing rather than to a plausible neighbour: compiling against
    the wrong source produces a release that syncs real data from the wrong
    place, which is worse than one that will not compile.
    """

    def __init__(self, bindings: Mapping[str, SourceBinding]) -> None:
        self._bindings = dict(bindings)

    def resolve(self, dataset: str) -> SourceAssetDefinition | None:
        binding = self._bindings.get(dataset)
        return None if binding is None else binding.asset

    def datasets(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def all(self) -> tuple[SourceBinding, ...]:
        return tuple(self._bindings[name] for name in sorted(self._bindings))


def catalogue_from(
    baseline: ActiveSchema, overrides: Iterable[SourceBinding] = ()
) -> SourceBindingCatalogue:
    """Everything the configured schema already reaches, plus what was rebound.

    The baseline contributes each source under two names -- its asset id and its
    object reference -- because both are things an analyst reasonably types, and
    a draft that names the collection rather than the asset is not making a
    mistake. Overrides are applied last and win outright: a rebinding exists
    precisely to disagree with the file.
    """
    bindings: dict[str, SourceBinding] = {}
    for asset in baseline.sources.values():
        for name in _names_of(asset):
            bindings[name] = SourceBinding(dataset=name, asset=asset)
    for override in overrides:
        bindings[override.dataset] = override
    return SourceBindingCatalogue(bindings)


def _names_of(asset: SourceAssetDefinition) -> tuple[str, ...]:
    """The names this asset answers to.

    Its id, and the object-reference values that identify the object itself --
    a collection or table name. Deliberately not every value: a database name
    is shared by every asset in it, and binding a draft to "return_source"
    would resolve to whichever asset happened to be last.
    """
    names = [asset.source_asset_id]
    for key, value in asset.object_ref.items():
        if key in {"name", "collection", "table", "view"} and value:
            names.append(value)
    return tuple(names)
