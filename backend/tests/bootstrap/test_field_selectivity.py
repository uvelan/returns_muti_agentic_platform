"""W4.8: selectivity travels from the analyzer's profile to the model's catalogue.

The claim under test is narrow and specific. It is not "the numbers arrive" --
it is that the numbers arrive *with the basis that makes them mean anything*.
`approximate_distinct=40` is a near-key over a 50-row table and nearly worthless
over a million rows sampled 50 deep, and a catalogue that cannot express the
difference has handed the model a number to guess with rather than evidence to
rank on.

Nothing here asserts an ordering. There is no expected question order to assert,
because fixing one would be the same guess this step exists to replace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from return_platform.bootstrap.adapters.analyzer_release_compiler import compile_active_schema
from return_platform.bootstrap.adapters.analyzer_source_observation import (
    SourceObservation,
    observed_selectivity,
)
from return_platform.configuration.settings import DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.integration.neo4j_gateway import Neo4jKnowledgeGateway
from return_platform.dynamic_knowledge.schema import (
    ActiveSchema,
    FieldSelectivity,
    IdentifierLikelihood,
)
from return_platform.graph_schema_analyzer.ports.source_port import (
    FieldDescription,
    FieldProfile,
    IndexDescription,
    ObjectDescription,
    ObjectKind,
    ObjectProfile,
)

COLUMNS = (
    FieldDescription(field_name="order_id", declared_type="varchar", nullable=False),
    FieldDescription(field_name="status", declared_type="varchar", nullable=True),
)


def _observation(
    *,
    profile_fields: tuple[FieldProfile, ...] = (),
    sampled_rows: int = 50,
    profile_row_count: int | None = None,
    described_row_count: int | None = 1_000_000,
    indexes: tuple[IndexDescription, ...] = (),
    with_profile: bool = True,
) -> SourceObservation:
    description = ObjectDescription(
        source_id="warehouse",
        object_name="dbo.orders",
        object_kind=ObjectKind.TABLE,
        fields=COLUMNS,
        approximate_row_count=described_row_count,
    )
    profile = (
        ObjectProfile(
            source_id="warehouse",
            object_name="dbo.orders",
            approximate_row_count=profile_row_count,
            sampled_rows=sampled_rows,
            fields=profile_fields,
        )
        if with_profile
        else None
    )
    return SourceObservation(description=description, indexes=indexes, profile=profile)


def _field(
    name: str = "order_id",
    *,
    distinct: int | None = 50,
    null_rate: float = 0.0,
    identifier_candidate: bool = True,
) -> FieldProfile:
    return FieldProfile(
        field_name=name,
        declared_type="varchar",
        null_rate=null_rate,
        approximate_distinct=distinct,
        identifier_candidate=identifier_candidate,
    )


# ---------------------------------------------------------------------------
# Nothing measured is a different answer from measured and poor
# ---------------------------------------------------------------------------


def test_no_profile_yields_no_selectivity_at_all() -> None:
    """`None`, not a zeroed record. An all-zero `FieldSelectivity` would state
    that the field was measured and found useless at narrowing, which would
    demote exactly the fields nobody has profiled yet."""
    assert observed_selectivity(_observation(with_profile=False), column="order_id") is None


def test_a_column_the_profile_does_not_mention_yields_none() -> None:
    """A profile taken over other columns says nothing about this one."""
    observation = _observation(profile_fields=(_field("status"),))
    assert observed_selectivity(observation, column="order_id") is None


# ---------------------------------------------------------------------------
# The basis travels with the numbers -- the point of the whole step
# ---------------------------------------------------------------------------


def test_the_numbers_arrive_with_the_sample_they_were_computed_from() -> None:
    observation = _observation(profile_fields=(_field(distinct=48, null_rate=0.02),))
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.approximate_distinct == 48
    assert selectivity.null_rate == 0.02
    assert selectivity.sampled_rows == 50
    assert selectivity.approximate_row_count == 1_000_000


def test_the_same_distinct_count_reads_differently_against_a_different_basis() -> None:
    """The headline claim. Forty distinct values is a near-key over fifty rows
    of a fifty-row table and a weak signal over fifty rows of a million, and the
    only thing separating them is the basis carried alongside."""
    small = observed_selectivity(
        _observation(
            profile_fields=(_field(distinct=40),), sampled_rows=50, described_row_count=50
        ),
        column="order_id",
    )
    large = observed_selectivity(
        _observation(
            profile_fields=(_field(distinct=40),), sampled_rows=50, described_row_count=1_000_000
        ),
        column="order_id",
    )
    assert small is not None and large is not None
    assert small.approximate_distinct == large.approximate_distinct
    assert small.distinct_ratio == large.distinct_ratio
    # ... and yet they are distinguishable, which is the whole requirement.
    assert small.sample_coverage == 1.0
    assert large.sample_coverage is not None and large.sample_coverage < 0.001


def test_coverage_is_undefined_rather_than_assumed_when_the_size_is_unknown() -> None:
    """Assuming an unreported count means the sample was exhaustive is exactly
    the over-claim `sampled_rows` exists to prevent."""
    observation = _observation(profile_fields=(_field(),), described_row_count=None)
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.approximate_row_count is None
    assert selectivity.sample_coverage is None


def test_coverage_never_exceeds_one_even_when_the_estimate_is_low() -> None:
    """`estimated_document_count` can come back under the rows actually read.
    A coverage above 1.0 would read as a bug rather than as an estimate being
    approximate."""
    observation = _observation(profile_fields=(_field(),), sampled_rows=50, described_row_count=10)
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.sample_coverage == 1.0


def test_distinct_ratio_is_derived_so_it_cannot_disagree_with_its_inputs() -> None:
    ratio = FieldSelectivity(sampled_rows=50, null_rate=0.0, approximate_distinct=5).distinct_ratio
    assert ratio == 0.1


# ---------------------------------------------------------------------------
# Identifier likelihood grades the evidence rather than flattening it
# ---------------------------------------------------------------------------


def test_a_unique_index_outranks_anything_counting_could_establish() -> None:
    """The source promising one row per value, over every row that exists --
    not a claim about fifty sampled ones."""
    observation = _observation(
        profile_fields=(_field(distinct=50),),
        indexes=(
            IndexDescription(index_name="pk", fields=("order_id",), unique=True, primary=True),
        ),
    )
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.identifier_likelihood is IdentifierLikelihood.DECLARED_UNIQUE


def test_a_composite_unique_index_does_not_make_one_column_unique() -> None:
    """`(warehouse_id, bay_id)` unique says nothing about `warehouse_id` alone,
    and reporting it as declared-unique would promise one row per warehouse."""
    observation = _observation(
        profile_fields=(_field(distinct=50),),
        indexes=(
            IndexDescription(index_name="ux", fields=("warehouse_id", "order_id"), unique=True),
        ),
    )
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.identifier_likelihood is not IdentifierLikelihood.DECLARED_UNIQUE


def test_distinct_across_a_sample_that_saw_everything_is_likely() -> None:
    observation = _observation(
        profile_fields=(_field(distinct=50),), sampled_rows=50, described_row_count=50
    )
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.identifier_likelihood is IdentifierLikelihood.LIKELY


def test_distinct_across_a_fraction_of_the_object_is_only_possible() -> None:
    """Fifty of a million rows being distinct is a hypothesis, and grading it
    the same as fifty of fifty is the confusion this step exists to remove."""
    observation = _observation(
        profile_fields=(_field(distinct=50),), sampled_rows=50, described_row_count=1_000_000
    )
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.identifier_likelihood is IdentifierLikelihood.POSSIBLE


def test_a_column_that_already_repeated_is_unlikely() -> None:
    observation = _observation(
        profile_fields=(_field(distinct=3, identifier_candidate=False),),
    )
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.identifier_likelihood is IdentifierLikelihood.UNLIKELY


@pytest.mark.parametrize(
    ("sampled_rows", "distinct"),
    [(0, None), (0, 0), (50, None)],
    ids=["nothing_sampled", "empty_object", "distinctness_uncountable"],
)
def test_absent_evidence_is_unknown_rather_than_unlikely(
    sampled_rows: int, distinct: int | None
) -> None:
    """`identifier_candidate` sits at False for want of evidence in all three
    cases. Reporting that as UNLIKELY turns "we did not look" into "we looked
    and it is poor", and a ranker cannot tell the difference afterwards."""
    observation = _observation(
        profile_fields=(_field(distinct=distinct, identifier_candidate=False),),
        sampled_rows=sampled_rows,
    )
    selectivity = observed_selectivity(observation, column="order_id")
    assert selectivity is not None
    assert selectivity.identifier_likelihood is IdentifierLikelihood.UNKNOWN


# ---------------------------------------------------------------------------
# The compiler carries it from the analyzer's observation onto the release
# ---------------------------------------------------------------------------


def _draft(dataset: str, cursor: str | None) -> dict[str, Any]:
    """One entity over `dataset`, mapping the source's cursor if it has one --
    a release whose entity cannot supply the cursor is refused, so a realistic
    draft maps it."""
    extra = {} if cursor is None else {cursor: {"type": "STRING", "source_field": cursor}}
    return {
        "entities": {
            "Order": {
                "source_dataset": dataset,
                "properties": {
                    "order_id": {"type": "STRING", "source_field": "order_id"},
                    "status": {"type": "STRING", "source_field": "status"},
                    **extra,
                },
                "identifier_properties": ["order_id"],
            }
        }
    }


def _compiled(baseline: ActiveSchema, observation: SourceObservation | None) -> ActiveSchema:
    dataset = baseline.entities[next(iter(baseline.entities))].source_asset_id
    cursor = baseline.sources[dataset].incremental_cursor_field
    columns = COLUMNS if cursor is None else (*COLUMNS, _cursor_column(cursor))
    supplied = (
        None
        if observation is None
        else SourceObservation(
            description=observation.description.model_copy(update={"fields": columns}),
            indexes=observation.indexes,
            profile=observation.profile,
        )
    )
    return compile_active_schema(
        _draft(dataset, cursor),
        baseline=baseline,
        observations=None if supplied is None else {dataset: supplied},
        configuration_release_id="release-selectivity",
        schema_version="v-selectivity",
        approved_by="analyst-1",
        approved_at=datetime(2026, 8, 13, tzinfo=UTC),
    )


def _cursor_column(cursor: str) -> FieldDescription:
    return FieldDescription(field_name=cursor, declared_type="datetime", nullable=True)


def test_the_compiler_puts_the_profiled_statistics_on_the_release(
    descriptor: ActiveSchema,
) -> None:
    """The production route. Without this the numbers exist in the analyzer and
    reach nothing -- which is the state W4.5 left `profile` in."""
    observation = _observation(
        profile_fields=(_field(distinct=48, null_rate=0.02),), described_row_count=1_000
    )
    release = _compiled(descriptor, observation)
    selectivity = release.entities["Order"].fields["order_id"].selectivity
    assert selectivity is not None
    assert selectivity.approximate_distinct == 48
    assert selectivity.null_rate == 0.02
    assert selectivity.sampled_rows == 50
    assert selectivity.identifier_likelihood is IdentifierLikelihood.POSSIBLE


def test_a_field_the_profile_missed_compiles_without_selectivity(
    descriptor: ActiveSchema,
) -> None:
    """`status` was not profiled, so the release says nothing about it rather
    than inheriting the object's numbers."""
    observation = _observation(profile_fields=(_field("order_id"),))
    release = _compiled(descriptor, observation)
    assert release.entities["Order"].fields["order_id"].selectivity is not None
    assert release.entities["Order"].fields["status"].selectivity is None


def test_compiling_without_an_observation_leaves_every_field_unmeasured(
    descriptor: ActiveSchema,
) -> None:
    """Nothing read the source, so nothing may claim to have measured it -- the
    same discipline that already marks such an entity UNVERIFIED."""
    release = _compiled(descriptor, None)
    assert all(field.selectivity is None for field in release.entities["Order"].fields.values())


# ---------------------------------------------------------------------------
# ... and it reaches the catalogue the model actually reads
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def descriptor() -> ActiveSchema:
    """The real shipped release, not a fixture schema.

    `compact_schema` is only interesting over a release the runtime would
    actually load: a hand-built one could agree with an emitter that is wrong
    about every entity nobody wrote into the fixture.
    """
    return load_active_schema(DEFAULT_DYNAMIC_KNOWLEDGE_SCHEMA_PATH)


def _agent_of(schema: ActiveSchema) -> str:
    return next(iter(schema.agent_policies))


def _target_entity(schema: ActiveSchema) -> str:
    """An entity the agent is actually permitted, so it survives into the
    compact form at all."""
    return sorted(schema.agent_policies[_agent_of(schema)].allowed_entity_ids)[0]


async def _compact(schema: ActiveSchema) -> dict[str, Any]:
    # No driver is touched: `compact_schema` reads the schema and nothing else,
    # and constructing a real gateway would demand a live Neo4j connection to
    # test a pure projection.
    gateway = Neo4jKnowledgeGateway.__new__(Neo4jKnowledgeGateway)
    return await gateway.compact_schema(schema, _agent_of(schema))


def _with_selectivity(schema: ActiveSchema, selectivity: FieldSelectivity | None) -> ActiveSchema:
    """Put `selectivity` on one field of the shipped schema, leaving the rest."""
    entity_id = _target_entity(schema)
    entity = schema.entities[entity_id]
    field_id = sorted(entity.fields)[0]
    fields = dict(entity.fields)
    fields[field_id] = fields[field_id].model_copy(update={"selectivity": selectivity})
    entities = dict(schema.entities)
    entities[entity_id] = entity.model_copy(update={"fields": fields})
    return schema.model_copy(update={"entities": entities})


def _first_field(compact: dict[str, Any], schema: ActiveSchema) -> dict[str, Any]:
    entity_id = _target_entity(schema)
    field_id = sorted(schema.entities[entity_id].fields)[0]
    fields: dict[str, Any] = compact["entities"][entity_id]["fields"]
    return fields[field_id]


@pytest.mark.asyncio
async def test_compact_schema_carries_the_statistics_and_their_basis(
    descriptor: ActiveSchema,
) -> None:
    """The deliverable: `approximate_distinct`, `null_rate` and identifier
    likelihood in front of the model, each with the sample it came from."""
    schema = _with_selectivity(
        descriptor,
        FieldSelectivity(
            sampled_rows=50,
            approximate_row_count=1_000_000,
            null_rate=0.02,
            approximate_distinct=48,
            identifier_likelihood=IdentifierLikelihood.POSSIBLE,
        ),
    )
    field = _first_field(await _compact(schema), schema)
    assert field["approximateDistinct"] == 48
    assert field["nullRate"] == 0.02
    assert field["identifierLikelihood"] == "POSSIBLE"
    assert field["sampledRows"] == 50
    assert field["approximateRowCount"] == 1_000_000
    assert field["distinctRatio"] == 0.96
    # The capability block is untouched: what may be asked and what asking is
    # worth are different questions, and both still travel.
    assert "searchable" in field
    assert "operators" in field


@pytest.mark.asyncio
async def test_an_unprofiled_field_says_nothing_rather_than_unknown(
    descriptor: ActiveSchema,
) -> None:
    """Absence is the honest encoding of "not measured". A stated UNKNOWN on
    every field of a hand-authored descriptor spends context to say nothing, and
    a model reading it tends to treat it as a finding."""
    schema = _with_selectivity(descriptor, None)
    field = _first_field(await _compact(schema), schema)
    assert "identifierLikelihood" not in field
    assert "sampledRows" not in field
    assert "approximateDistinct" not in field


@pytest.mark.asyncio
async def test_an_uncountable_distinct_is_omitted_rather_than_reported_as_zero(
    descriptor: ActiveSchema,
) -> None:
    """A wrong selectivity estimate is worse than a missing one, because the
    ranker would use it confidently."""
    schema = _with_selectivity(
        descriptor,
        FieldSelectivity(sampled_rows=50, null_rate=0.0, approximate_distinct=None),
    )
    field = _first_field(await _compact(schema), schema)
    assert "approximateDistinct" not in field
    assert "distinctRatio" not in field
    assert field["sampledRows"] == 50
