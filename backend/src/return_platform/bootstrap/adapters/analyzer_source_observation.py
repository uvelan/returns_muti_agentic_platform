"""What the analyzer saw in a source, and the runtime facets that follow from it.

The analyzer's authoring form -- `GraphSchemaShape` -- carries a property's name,
type and source field and nothing else. `ActiveSchema` needs considerably more
before an entity is usable: whether a field is nullable, whether it may be
searched or filtered, which operators apply, which combinations of fields
identify one row, and whether the declared paths have been confirmed against the
source at all. W2.4's descriptor work stalled on exactly that gap -- the analyzer
could produce an entity the sync would run and no agent could read.

**Those facets are not preferences; the source already states them.** A column's
nullability is in the catalogue. A unique index is the source saying "this
identifies one row". A non-unique index is the source saying "this is cheap to
look up by, and will return several". Deriving the facets from the indexes and
declared types is therefore reading the source, not deciding for it -- and it is
reproducible, which a hand-authored capability block is not.

Two rules here are policy rather than observation, and are named as such:

* **Permissions mirror capabilities and grant every role.** A source cannot say
  who may read a column. This matches what all eleven shipped entities already
  do, and per-role narrowing is W4.6's masking work.
* **An entity compiled against an observation is `VERIFIED`.** The claim is
  precisely "every declared path was checked against what the source says it
  holds" -- which for a SQL table is the whole catalogue, not a sample. It is not
  a claim about data quality, and it is why an entity compiled with no
  observation stays `UNVERIFIED`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from return_platform.dynamic_knowledge.schema import (
    AnchorFieldDefinition,
    FieldCapabilities,
    FieldPermissions,
    FieldSelectivity,
    FieldType,
    IdentifierLikelihood,
    StrongAnchorDefinition,
)
from return_platform.graph_schema_analyzer.domain.source_snapshot import (
    DatasetMetadata,
    FieldMetadata,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldProfile,
    IndexDescription,
    ObjectDescription,
    ObjectProfile,
)

__all__ = [
    "SourceObservation",
    "dataset_metadata_of",
    "observed_capabilities",
    "observed_permissions",
    "observed_selectivity",
    "observed_strong_anchors",
]

#: Equality operators. Everything an identifier or a coded value needs, and
#: nothing that scans: `PREFIX`/`CONTAINS` compile to lowercased substring
#: matches and no declared index makes those cheap, so nothing here grants them.
_EQUALITY = frozenset({"EXACT", "EQUALS"})

#: Ordering operators, for the temporal types where a range is the natural
#: question. A range predicate on a string column is legal Cypher and almost
#: never what anyone meant.
_RANGE = frozenset({"GT", "GTE", "LT", "LTE", "BETWEEN"})

_TEMPORAL = frozenset({FieldType.DATE, FieldType.DATETIME})

#: Every role. The descriptor's existing convention, spelled once.
_ALL_ROLES = frozenset({"*"})


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One object as the analyzer's inspection port described it.

    `indexes` and `profile` are optional because not every backend has them --
    MongoDB reports indexes, Neo4j reports none -- and an absent index list means
    "nothing is declared cheap to look up by", which yields a displayable-only
    entity rather than a guess.
    """

    description: ObjectDescription
    indexes: tuple[IndexDescription, ...] = ()
    profile: ObjectProfile | None = None

    @property
    def object_name(self) -> str:
        return self.description.object_name

    def declared_type(self, column: str) -> str | None:
        for item in self.description.fields:
            if item.field_name == column:
                return item.declared_type
        return None

    def nullable(self, column: str) -> bool | None:
        """`None` when the source never mentioned the column.

        Distinguished from `True` deliberately: a column the description does not
        carry is one this observation cannot speak for, and defaulting it to
        nullable would state something the source did not.
        """
        for item in self.description.fields:
            if item.field_name == column:
                return item.nullable
        return None


def dataset_metadata_of(observation: SourceObservation) -> DatasetMetadata:
    """Bridge the inspection vocabulary to the discovery vocabulary.

    `SourceInspectionPort` answers about one object; `SourceSchemaSnapshot` --
    which validation and re-analysis both read -- is expressed in
    `DatasetMetadata`. They describe the same thing in two shapes because they
    were built for different callers, and this is the one translation between
    them, so a snapshot assembled object-by-object is indistinguishable from one
    `SourceDiscoveryPort.discover` produced in a single call.
    """
    profiled = observation.profile.fields if observation.profile is not None else ()
    distinct_by_name = {item.field_name: item.approximate_distinct for item in profiled}
    return DatasetMetadata(
        source_id=observation.description.source_id,
        dataset_name=observation.description.object_name,
        fields=tuple(
            FieldMetadata(
                field_name=item.field_name,
                declared_type=item.declared_type,
                nullable=item.nullable,
                distinct_count=distinct_by_name.get(item.field_name),
            )
            for item in observation.description.fields
        ),
        approximate_row_count=observation.description.approximate_row_count,
    )


def observed_capabilities(
    observation: SourceObservation, *, column: str, data_type: FieldType
) -> FieldCapabilities:
    """What the source's own access paths say may be asked of this column.

    The whole rule, and it is short on purpose:

    * **A unique index on it alone** -- the source identifies a row by it.
      Searchable, filterable, and an on-demand sync anchor: a targeted read on it
      is bounded to one row by the source's own constraint.
    * **Leading column of any index** -- cheap to look up by, and bounded to
      whatever that key prefix selects. Searchable, filterable, distinct and an
      anchor, because a bounded read is the thing an anchor exists to permit.
    * **A later column of a composite index** -- only cheap when the leading
      columns are constrained too, which nothing here can promise. Filterable,
      not searchable, not an anchor.
    * **Temporal and unindexed** -- filterable on a range, because that is the
      question a timestamp answers.
    * **Everything else** -- displayable and nothing more.

    `displayable` is unconditional. A field the descriptor declares and the graph
    holds but no reader may see is a projection nobody can account for.
    """
    unique_alone = any(index.unique and index.fields == (column,) for index in observation.indexes)
    unique_composite = any(index.unique and column in index.fields for index in observation.indexes)
    leading = any(index.fields[:1] == (column,) for index in observation.indexes if index.fields)
    later = any(column in index.fields[1:] for index in observation.indexes)

    if unique_alone or leading:
        return FieldCapabilities(
            searchable=True,
            filterable=True,
            # A unique column has one value per row, so DISTINCT over it returns
            # the row count and answers nothing. An indexed non-unique one
            # repeats, which is exactly when the question is worth asking.
            distinct=not unique_alone,
            displayable=True,
            on_demand_sync_anchor=True,
            operators=_EQUALITY,
        )
    if unique_composite or later:
        return FieldCapabilities(filterable=True, displayable=True, operators=_EQUALITY)
    if data_type in _TEMPORAL:
        return FieldCapabilities(filterable=True, displayable=True, operators=_RANGE)
    return FieldCapabilities(displayable=True)


def observed_selectivity(observation: SourceObservation, *, column: str) -> FieldSelectivity | None:
    """How well this column narrows a search, as the profile measured it (W4.8).

    `None` when nothing profiled the column -- no profile was taken, or the
    profile does not mention it. That is a different statement from "measured and
    found unselective", and the model is entitled to tell them apart: ranking a
    never-measured field level with a status flag would push a perfectly good
    identifier to the bottom of the questions worth asking.

    The row count comes from the *description*, not the profile, when the profile
    does not carry one. The description is what the source's catalogue reports
    for the object as a whole, and it is the denominator that turns "48 distinct"
    into a coverage claim.
    """
    profile = observation.profile
    if profile is None:
        return None
    field = next((item for item in profile.fields if item.field_name == column), None)
    if field is None:
        return None
    return FieldSelectivity(
        sampled_rows=profile.sampled_rows,
        approximate_row_count=(
            profile.approximate_row_count
            if profile.approximate_row_count is not None
            else observation.description.approximate_row_count
        ),
        null_rate=field.null_rate,
        approximate_distinct=field.approximate_distinct,
        identifier_likelihood=_identifier_likelihood(observation, column=column, field=field),
    )


def _identifier_likelihood(
    observation: SourceObservation, *, column: str, field: FieldProfile
) -> IdentifierLikelihood:
    """Grade the two kinds of evidence separately, strongest first.

    A unique index on the column alone outranks anything counting can establish:
    it is the source promising one row per value, over every row, forever. Only
    when there is no such index does the sample get a say -- and then what it can
    say depends on how much of the object it saw. Distinct in every row of a
    sample that covered the whole object is as good as counted; distinct in every
    row of fifty out of a million is a hypothesis worth ranking above a column
    that already repeated, and no more than that.

    `identifier_candidate` is deliberately not passed through as-is. It is a
    boolean about a sample, and the whole point of W4.8 is that a consumer
    comparing fields across sources cannot rank honestly on a boolean whose basis
    it cannot see.
    """
    if any(index.unique and index.fields == (column,) for index in observation.indexes):
        return IdentifierLikelihood.DECLARED_UNIQUE
    profile = observation.profile
    if profile is None or profile.sampled_rows == 0 or field.approximate_distinct is None:
        # A profile over no rows, or one that could not count distinctness,
        # leaves `identifier_candidate` at False for want of evidence rather
        # than because of it. Reporting that as UNLIKELY would turn "we did not
        # look" into "we looked and it is poor".
        return IdentifierLikelihood.UNKNOWN
    if not field.identifier_candidate:
        return IdentifierLikelihood.UNLIKELY
    if _sample_covered_everything(observation):
        return IdentifierLikelihood.LIKELY
    return IdentifierLikelihood.POSSIBLE


def _sample_covered_everything(observation: SourceObservation) -> bool:
    """Whether the profile read as many rows as the object is reported to hold.

    Requires a reported count: without one there is no denominator, and
    assuming the sample was exhaustive is precisely the over-claim `sampled_rows`
    was added to prevent.
    """
    profile = observation.profile
    if profile is None:
        return False
    total = (
        profile.approximate_row_count
        if profile.approximate_row_count is not None
        else observation.description.approximate_row_count
    )
    return total is not None and profile.sampled_rows >= total


def observed_permissions(capabilities: FieldCapabilities) -> FieldPermissions:
    """Mirror the capabilities, granted to every role.

    **Policy, not observation** -- see the module docstring. Expressed as a
    function of the capabilities rather than as a constant so the two cannot
    drift: a field that is searchable and that no role may search is a capability
    the guard refuses on every request, which reads as a broken query rather than
    as a permission decision.
    """
    return FieldPermissions(
        displayable_by=_ALL_ROLES if capabilities.displayable else frozenset(),
        searchable_by=_ALL_ROLES if capabilities.searchable else frozenset(),
        on_demand_sync_by=_ALL_ROLES if capabilities.on_demand_sync_anchor else frozenset(),
    )


def observed_strong_anchors(
    observation: SourceObservation,
    *,
    columns_by_field: Mapping[str, str],
    natural_key: tuple[str, ...],
    capabilities: Mapping[str, FieldCapabilities],
) -> dict[str, StrongAnchorDefinition]:
    """Anchors for the field sets that are provably bounded to one record.

    Exactly two things qualify, and nothing else does:

    * **A unique index**, which is the source promising one row per value.
    * **The entity's natural key**, which is the *graph* promising one node per
      value -- the node key makes it so regardless of how many source rows
      carried it. This is what gives a dimension projected out of a fact table an
      anchor at all: `warehouse` is one node per `warehouse_id` even though the
      bay table repeats that column on every row.

    Nothing else gets one. A non-unique index bounds a read to some number, and
    `maximum_expected_matches` would have to state which -- a number no
    observation supplies and that would be invented if it appeared here. Such a
    column is still `on_demand_sync_anchor` in its own right, so it can *appear*
    in an anchor; it just cannot define one.

    Every field of a candidate set must already carry `on_demand_sync_anchor`,
    so the anchor and the capability come from one reading of the source. A set
    that fails that is dropped rather than granted the capability here, which
    would be this function promoting a field on its own authority.
    """
    field_by_column = {column: field_id for field_id, column in columns_by_field.items()}
    candidates: list[tuple[str, ...]] = []
    for index in observation.indexes:
        if not index.unique:
            continue
        mapped = tuple(
            field_by_column[column] for column in index.fields if column in field_by_column
        )
        if len(mapped) == len(index.fields) and mapped:
            candidates.append(mapped)
    if natural_key:
        candidates.append(natural_key)

    anchors: dict[str, StrongAnchorDefinition] = {}
    for fields in candidates:
        if not all(
            capabilities.get(field_id, FieldCapabilities()).on_demand_sync_anchor
            for field_id in fields
        ):
            continue
        anchor_id = "exact_" + "_".join(fields)
        if anchor_id in anchors:
            continue
        anchors[anchor_id] = StrongAnchorDefinition(
            anchor_id=anchor_id,
            fields=tuple(
                AnchorFieldDefinition(field_id=field_id, allowed_operators=_EQUALITY, required=True)
                for field_id in fields
            ),
            minimum_fields_present=len(fields),
            maximum_expected_matches=1,
            on_demand_sync_allowed=True,
        )
    return anchors
