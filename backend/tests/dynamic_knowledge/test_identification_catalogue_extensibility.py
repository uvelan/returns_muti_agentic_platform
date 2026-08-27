"""Adversarial scenario #37: the tenth identification field, added by configuration.

The requirement is exact and the audit is blunt about how it had been met so
far: *adding the tenth identification field must require zero Python edits*.
Seven places in the backend had to agree about which fields exist -- the intent
model with seventeen names under `extra="forbid"`, a signature tuple, a numbered
branch per field in the plan builder, an address pair list, a date-field
constant, an unsupported-signal tuple, and a scoring block per field.

So the test is written to fail if any of them comes back. It takes the *shipped*
production configuration, appends one field to `discovery.identification_fields`
as an operator would, and drives the real planner, the real schema guard, the
real Cypher compiler and the real ranker with it. Nothing here imports a
registry to register into, and nothing patches a module: if a field name is
needed anywhere in Python for a field to work, this cannot pass.

The new field is a real one -- `sales_order.order_status`, a property the active
schema carries and that no configured field searched on before. Inventing a
field the schema does not have would prove only that the catalogue accepts text.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
import yaml

from return_platform.configuration.return_configuration import (
    DiscoveryConfiguration,
    load_return_configuration,
)
from return_platform.dynamic_knowledge.config_loader import load_active_schema
from return_platform.dynamic_knowledge.knowledge.cypher_compiler import CypherCompiler
from return_platform.dynamic_knowledge.knowledge.guards import (
    GuardContext,
    PrincipalContext,
    QuerySafetyGuard,
    QuerySafetyPolicy,
    SchemaQueryGuard,
)
from return_platform.dynamic_knowledge.order_agent.contracts import OrderSearchIntent
from return_platform.dynamic_knowledge.order_agent.identification import (
    IdentificationCatalogue,
    build_identification_catalogue,
)
from return_platform.dynamic_knowledge.order_agent.search_strategy import (
    build_search_program,
    rank_search_results,
    search_intent_signature,
)
from return_platform.dynamic_knowledge.schema import ActiveSchema

REPOSITORY_BACKEND = Path(__file__).parents[2]
PRODUCTION_CONFIGURATION = REPOSITORY_BACKEND / "config/returns/production.yaml"
ACTIVE_SCHEMA = REPOSITORY_BACKEND / "config/dynamic_knowledge/active-schema.return-order.yaml"

#: The operator's edit, in the form they would actually make it: one YAML entry.
#: Deliberately exercises the parts a new field is most likely to need -- an
#: alias so the model can recognise it, a validation pattern, a normalization,
#: a ranking weight and two searches with different operators.
NEW_FIELD_YAML = """
field_id: order_status
intent_key: orderStatuses
label: "order status"
description: "Whether the order is open, shipped, invoiced or cancelled."
aliases: [status, order status, state of the order]
normalization: LOWER_ALPHANUMERIC
validation_pattern: "^[A-Za-z ]{3,32}$"
sensitivity: NONE
ranking_weight_millionths: 120000
exact_match_bonus_millionths: 80000
clarification_priority: 48
searches:
  - entity: sales_order
    field: order_status
    strategy: EXACT
    limit: 10
    result_fields: [sales_order_number, customer_id, order_status]
"""


@pytest.fixture(scope="module")
def production_schema() -> ActiveSchema:
    return load_active_schema(ACTIVE_SCHEMA)


@pytest.fixture(scope="module")
def shipped_discovery() -> DiscoveryConfiguration:
    return load_return_configuration(PRODUCTION_CONFIGURATION).configuration.discovery


@pytest.fixture(scope="module")
def extended_discovery(shipped_discovery: DiscoveryConfiguration) -> DiscoveryConfiguration:
    """The shipped configuration plus one field, validated the ordinary way.

    Re-validated through `DiscoveryConfiguration` rather than mutated in place,
    so the new entry passes exactly the checks an operator's edit would --
    unique ids, unique intent keys, resolvable `narrow_with` references.
    """
    payload = shipped_discovery.model_dump(mode="json")
    payload["identification_fields"] = [
        *payload["identification_fields"],
        yaml.safe_load(NEW_FIELD_YAML),
    ]
    return DiscoveryConfiguration.model_validate(payload)


def _catalogue(discovery: DiscoveryConfiguration, schema: ActiveSchema) -> IdentificationCatalogue:
    return build_identification_catalogue(
        discovery.identification_fields,
        schema,
        default_fulltext_index=discovery.progressive.customer_fulltext_index,
    )


@pytest.fixture(scope="module")
def extended_catalogue(
    extended_discovery: DiscoveryConfiguration, production_schema: ActiveSchema
) -> IdentificationCatalogue:
    return _catalogue(extended_discovery, production_schema)


def _intent(**signals: Any) -> OrderSearchIntent:
    return OrderSearchIntent.model_validate(signals)


# --- the closure test ---------------------------------------------------------


def test_the_new_field_resolves_against_the_active_schema(
    extended_catalogue: IdentificationCatalogue,
) -> None:
    """Step one: the catalogue binds it to a real property with a real operator."""
    assert extended_catalogue.unresolved == ()
    field = extended_catalogue.field_for("orderStatuses")
    assert field is not None
    assert field.is_usable
    assert [(search.entity_id, search.field_id) for search in field.searches] == [
        ("sales_order", "order_status")
    ]


def test_the_model_is_told_about_the_new_field_without_a_prompt_edit(
    extended_catalogue: IdentificationCatalogue,
) -> None:
    """Step two: the reasoning model can learn the key exists.

    A field the model is never told about is a field the model never populates,
    so this is not decoration -- it is the difference between a configured field
    and a dead one. The packaged system prompt is one immutable string per
    configuration release; this description is read from the catalogue on every
    turn and travels in `AgentTurnContext.identification_fields`.
    """
    described = {item["intentKey"]: item for item in extended_catalogue.describe()}

    assert described["orderStatuses"]["label"] == "order status"
    assert "status" in described["orderStatuses"]["aliases"]
    assert described["orderStatuses"]["searchable"] is True


def test_the_new_field_is_accepted_on_an_intent_the_model_sends(
    extended_catalogue: IdentificationCatalogue,
) -> None:
    """Step three: the request layer does not reject it.

    This is the one the old contract failed. `OrderSearchIntent` declared its
    seventeen signals with `extra="forbid"`, so a model populating an
    eighteenth had its whole action rejected -- and the associate saw a
    validation failure rather than a search.
    """
    parsed = extended_catalogue.parse(_intent(orderStatuses=["SHIPPED"]).signal_values)

    assert parsed.unknown_keys == ()
    signal = parsed.by_key("orderStatuses")
    assert signal is not None and signal.values == ("SHIPPED",)


def test_the_new_field_produces_a_plan_that_passes_the_guard_and_compiles(
    extended_catalogue: IdentificationCatalogue, production_schema: ActiveSchema
) -> None:
    """Step four: it reaches the graph, through the real guard and compiler.

    Everything short of this could be satisfied by configuration that parses.
    A plan that the schema guard refuses or the compiler cannot build is a
    field that looks configured and searches nothing.
    """
    program = build_search_program(_intent(orderStatuses=["SHIPPED"]), extended_catalogue)
    assert len(program.primary) == 1
    plan = program.primary[0].plan

    guard_context = GuardContext(
        schema=production_schema,
        agent_policy=production_schema.agent_policies["order-discovery-agent"],
        principal=PrincipalContext(
            principal_id="assoc-1", tenant_id="tenant-1", roles=frozenset({"associate"})
        ),
    )
    SchemaQueryGuard().validate(guard_context, plan)
    QuerySafetyGuard(QuerySafetyPolicy()).validate(plan)
    compiled = CypherCompiler().compile_read(production_schema, plan)

    assert compiled.read_only is True
    assert "order_status" in compiled.cypher
    assert compiled.parameters["p0"] == "SHIPPED"


def test_the_new_field_ranks_candidates_by_its_configured_weight(
    extended_catalogue: IdentificationCatalogue,
) -> None:
    """Step five: a match on it counts, and counts by the configured amount.

    A field that searches but never scores lets the row it found sit below rows
    that matched nothing, which is indistinguishable from not having the field.
    """
    intent = _intent(orderStatuses=["SHIPPED"], cities=["Dallas"])
    program = build_search_program(intent, extended_catalogue)
    result = rank_search_results(
        intent,
        [
            {
                "rows": [
                    {"sales_order_number": "SO-2", "ship_to_city": "Dallas"},
                    {"sales_order_number": "SO-1", "order_status": "SHIPPED"},
                ]
            }
        ],
        program=program,
    )
    ranked = result["candidates"]

    assert [candidate["candidate_id"] for candidate in ranked] == ["SO-1", "SO-2"]
    assert "order_status_exact" in ranked[0]["matches"]


def test_the_new_field_participates_in_the_pagination_signature(
    extended_catalogue: IdentificationCatalogue,
) -> None:
    """Step six: "show next" cannot page a set gathered under different signals."""
    assert search_intent_signature(
        _intent(customerNames=["Maya"]), extended_catalogue
    ) != search_intent_signature(
        _intent(customerNames=["Maya"], orderStatuses=["SHIPPED"]), extended_catalogue
    )


def test_the_new_field_validates_its_values(
    extended_catalogue: IdentificationCatalogue,
) -> None:
    """Configured validation is real, and a rejected value is reported not dropped.

    A value that cannot be a status returns nothing whatever we do with it. The
    difference that matters is whether the associate is told why.
    """
    parsed = extended_catalogue.parse(_intent(orderStatuses=["!!!"]).signal_values)

    assert parsed.invalid_signals == ("orderStatuses",)
    assert build_search_program(_intent(orderStatuses=["!!!"]), extended_catalogue).primary == ()


def _executable_strings_and_names(source: Path) -> set[str]:
    """Every string literal and identifier a module actually executes.

    Docstrings are excluded and comments never reach the AST, so prose may go on
    explaining what `postalCodes` is for -- the point is not that the word never
    appears in the file, it is that no code branches on it.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = next(iter(node.body), None)
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.add(node.value)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.keyword) and node.arg:
            found.add(node.arg)
    return found


def test_no_identification_field_is_named_anywhere_in_the_discovery_code(
    extended_discovery: DiscoveryConfiguration,
    shipped_discovery: DiscoveryConfiguration,
) -> None:
    """The invariant behind the scenario, checked against the source itself.

    Every step above ran on a catalogue built by appending one YAML entry, which
    shows the mechanism works *today*. This is what keeps it working: if any
    module on the discovery path starts branching on a field name again -- an
    intent key back on the model, a signature tuple, a per-field scoring block --
    it fails here, naming the file and the field.

    Checked on `intent_key` rather than on `field_id`. The intent keys are the
    model's vocabulary -- `customerNames`, `postalCodes`, `approximateDate` --
    and a Python file containing one is doing something with that specific
    signal, which is the thing being forbidden. Several `field_id` values are
    ordinary English (`state`, `city`, `sku`) that legitimately appear as graph
    property names and local variables, so matching on them would report noise
    and train a future reader to ignore this test.
    """
    # Relative to what shipped, not a literal. This is a guard that the fixture
    # really appended its field before the sweep below reads the intent keys --
    # and pinning the total to `18` made it fail the moment an operator added
    # `contact_name` by editing YAML, which is the exact change the whole module
    # exists to prove needs no code edit. A hardcoded count is the same defect
    # one level up.
    assert (
        len(extended_discovery.identification_fields)
        == len(shipped_discovery.identification_fields) + 1
    )

    forbidden = {item.intent_key for item in extended_discovery.identification_fields}
    discovery_package = REPOSITORY_BACKEND / "src/return_platform/dynamic_knowledge/order_agent"

    offences: list[str] = []
    for source in sorted(discovery_package.glob("*.py")):
        for name in sorted(_executable_strings_and_names(source) & forbidden):
            offences.append(f"{source.name} names the identification field {name!r}")

    assert offences == []


# --- DISC-02: colour, and ZIP, through the same catalogue ---------------------


def test_colour_needs_only_a_search_entry_to_become_searchable(
    shipped_discovery: DiscoveryConfiguration, production_schema: ActiveSchema
) -> None:
    """DISC-02, dissolved rather than special-cased.

    Colour is already an ordinary catalogue entry -- same shape, same
    normalization, same ranking weight, same clarification priority as every
    other field. It is unsearchable today for one reason and the catalogue says
    which: no property in the active knowledge graph records a colour. Nothing
    about colour is hardcoded anywhere, so the day one exists an operator adds a
    `searches` entry and colour works.

    Proven by doing exactly that, against a property the schema really has.
    """
    shipped = _catalogue(shipped_discovery, production_schema)
    colour = shipped.field_for("colors")
    assert colour is not None
    assert not colour.is_usable

    payload = shipped_discovery.model_dump(mode="json")
    for entry in payload["identification_fields"]:
        if entry["field_id"] == "product_colour":
            entry["searches"] = [
                {
                    "entity": "order_line",
                    "field": "product_description",
                    "strategy": "CONTAINS",
                    "limit": 5,
                    "result_fields": [
                        "sales_order_number",
                        "product_description",
                        "ordered_quantity",
                    ],
                }
            ]
    enabled = _catalogue(DiscoveryConfiguration.model_validate(payload), production_schema)

    field = enabled.field_for("colors")
    assert field is not None and field.is_usable
    program = build_search_program(_intent(colors=["chrome"]), enabled)
    assert len(program.primary) == 1
    assert program.primary[0].plan.filters[0].field_id == "product_description"
    assert program.parsed.unusable_signals == ()


def test_a_zip_code_goes_through_the_same_generic_catalogue(
    shipped_discovery: DiscoveryConfiguration, production_schema: ActiveSchema
) -> None:
    """The other half of DISC-02: no field has a private code path any more.

    ZIP was one of six signals declared and then silently dropped, long after
    the schema grew real properties for all of them. It is now described by the
    same six configuration keys as everything else, searched by the same loop,
    and scored by the same weight lookup -- and it is EXACT on both sides
    because that is the only operator the schema enables for it.
    """
    catalogue = _catalogue(shipped_discovery, production_schema)
    intent = _intent(postalCodes=["75201"])
    program = build_search_program(intent, catalogue)

    assert {
        (item.plan.start_entity_id, item.plan.filters[0].field_id, item.plan.filters[0].operator)
        for item in program.primary
    } == {
        ("contact_point", "postal_code", "EXACT"),
        ("sales_order", "ship_to_postal_code", "EXACT"),
    }

    result = rank_search_results(
        intent,
        [{"rows": [{"customer_id": "C1", "postal_code": "75201"}]}],
        program=program,
    )
    assert "postal_code_exact" in result["candidates"][0]["matches"]


# --- what an associate actually types -----------------------------------------


def test_a_pasted_identifier_keeps_its_meaning_when_it_keeps_its_whitespace(
    shipped_discovery: DiscoveryConfiguration, production_schema: ActiveSchema
) -> None:
    """Surrounding whitespace is typing, not content.

    Measured against the live system before the fix: `"  CQ800002  "` was
    searched verbatim and found **zero** orders, while `"CQ800002"` found exactly
    one. An associate pasting an order number out of an email was told there was
    no such order.
    """
    catalogue = _catalogue(shipped_discovery, production_schema)
    parsed = catalogue.parse({"orderNumbers": ["  CQ800002  "]})

    assert [value for signal in parsed.signals for value in signal.values] == ["CQ800002"]


def test_an_identifier_that_is_only_whitespace_is_not_a_signal(
    shipped_discovery: DiscoveryConfiguration, production_schema: ActiveSchema
) -> None:
    """The rule the empty string already had. A search on `""` matches nothing
    and spends a query against the turn's budget to find that out."""
    catalogue = _catalogue(shipped_discovery, production_schema)
    parsed = catalogue.parse({"orderNumbers": ["   "]})

    assert [value for signal in parsed.signals for value in signal.values] == []


def test_trimming_reaches_only_text(
    shipped_discovery: DiscoveryConfiguration, production_schema: ActiveSchema
) -> None:
    """Coercing a non-string to text in order to strip it would change what the
    model supplied. A date bound and a quantity pass through untouched."""
    catalogue = _catalogue(shipped_discovery, production_schema)
    parsed = catalogue.parse({"orderNumbers": ["CQ800002", 4000096]})

    assert [value for signal in parsed.signals for value in signal.values] == [
        "CQ800002",
        4000096,
    ]
