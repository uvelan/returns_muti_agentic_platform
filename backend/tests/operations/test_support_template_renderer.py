"""The renderer holds contracts.md sect. 8: selection, binding, batching, gaps.

The tests that matter most are the last: `TestComposedEquivalenceMatrix`
renders the production release's default variant against
`compose_support_handoff` **on every branch that composition's conditionals
take**, character for character -- which is what lets the template path replace
the composed path without anybody noticing the seam.

The previous version of that claim compared one fixture, and the fixture took
the one branch of each conditional the template could express. It was green,
and eleven of the seventeen scenarios below diverged.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

from return_platform.configuration.return_configuration import load_return_configuration
from return_platform.configuration.support_template_configuration import (
    SupportTemplateConfiguration,
)
from return_platform.operations.support_handoff import (
    SupportHandoffBay,
    SupportHandoffCustomer,
    SupportHandoffItem,
    SupportHandoffOrder,
    SupportHandoffPolicy,
    SupportHandoffReturn,
    compose_support_handoff,
)
from return_platform.operations.support_template_draft import (
    SNAPSHOT_KEYS,
    fact_log_projection,
    snapshot_as_facts,
    support_template_snapshot,
)
from return_platform.operations.support_template_renderer import (
    TemplateDraftInput,
    TemplateNotConfiguredError,
    TemplateRenderCache,
    TemplateRenderContext,
    render_support_template,
    select_variant,
)

_PRODUCTION_YAML = Path(__file__).resolve().parents[2] / "config" / "returns" / "production.yaml"


def _template(payload: dict) -> SupportTemplateConfiguration:
    return SupportTemplateConfiguration.model_validate(payload)


def _facts(**values: object) -> dict[tuple[str | None, str], dict[str, object]]:
    return {(None, name): {"value": value} for name, value in values.items()}


def _minimal_template(**field_overrides: object) -> SupportTemplateConfiguration:
    field = {
        "field_id": "order_number",
        "label": "Order Number",
        "source_binding": "case_fact:confirmed_order_reference",
    }
    field.update(field_overrides)
    return _template(
        {
            "template_id": "t",
            "default_variant_id": "default",
            "variants": [
                {
                    "variant_id": "default",
                    "subject_template": "Return {order_number}",
                    "sections": [{"section_id": "order", "title": "Order:", "fields": [field]}],
                }
            ],
        }
    )


class _GraphStub:
    """A graph port that counts its traffic, per the batching acceptance."""

    def __init__(
        self, values: dict[str, object] | None = None, after_sync: dict[str, object] | None = None
    ):
        self.values = dict(values or {})
        self.after_sync = dict(after_sync or {})
        self.reads: list[str] = []
        self.sync_calls: list[tuple[str, tuple[str, ...]]] = []

    async def read(self, path: str) -> object:
        self.reads.append(path)
        return self.values.get(path)

    async def synchronize(self, source: str, paths) -> None:
        self.sync_calls.append((source, tuple(paths)))
        self.values.update(self.after_sync)


@pytest.fixture(scope="module")
def production_template() -> SupportTemplateConfiguration:
    return load_return_configuration(_PRODUCTION_YAML).configuration.support_template


class TestVariantSelection:
    def test_parcel_modes_select_the_parcel_variant(self, production_template) -> None:
        context = TemplateRenderContext(shipping_modes=("PREPAID_PARCEL",), item_count=1)
        assert select_variant(production_template, context).variant_id == "parcel"

    def test_ltl_modes_select_the_ltl_variant(self, production_template) -> None:
        context = TemplateRenderContext(shipping_modes=("BRANCH_LTL", "OFFSITE_LTL"), item_count=2)
        assert select_variant(production_template, context).variant_id == "ltl"

    def test_mixed_parcel_and_ltl_falls_to_the_default(self, production_template) -> None:
        context = TemplateRenderContext(
            shipping_modes=("PREPAID_PARCEL", "BRANCH_LTL"), item_count=2
        )
        assert select_variant(production_template, context).variant_id == "default"

    def test_no_modes_falls_to_the_default(self, production_template) -> None:
        assert select_variant(production_template, TemplateRenderContext()).variant_id == "default"

    def test_first_matching_selector_wins(self) -> None:
        template = _template(
            {
                "template_id": "t",
                "default_variant_id": "default",
                "variants": [
                    {
                        "variant_id": "small",
                        "selector": {"max_item_count": 3},
                        "subject_template": "s",
                        "sections": [{"section_id": "s"}],
                    },
                    {
                        "variant_id": "also_small",
                        "selector": {"max_item_count": 5},
                        "subject_template": "s",
                        "sections": [{"section_id": "s"}],
                    },
                    {
                        "variant_id": "default",
                        "subject_template": "s",
                        "sections": [{"section_id": "s"}],
                    },
                ],
            }
        )
        assert select_variant(template, TemplateRenderContext(item_count=2)).variant_id == "small"
        assert (
            select_variant(template, TemplateRenderContext(item_count=4)).variant_id == "also_small"
        )

    def test_a_template_with_no_variants_refuses_loudly(self) -> None:
        with pytest.raises(TemplateNotConfiguredError):
            select_variant(SupportTemplateConfiguration(), TemplateRenderContext())


class TestBinding:
    pytestmark = pytest.mark.asyncio

    async def test_case_fact_binding_carries_provenance(self) -> None:
        template = _minimal_template()
        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="case-1",
                context=TemplateRenderContext(),
                facts={(None, "confirmed_order_reference"): {"value": "CQ800002", "factId": "f-1"}},
            ),
        )
        (field,) = rendered.sections[0].fields
        assert field.value == "CQ800002"
        assert (field.source, field.source_path, field.fact_id) == (
            "case_fact",
            "confirmed_order_reference",
            "f-1",
        )
        assert rendered.subject == "Return CQ800002"
        assert rendered.gaps == ()

    async def test_fallback_applies_only_after_the_binding_fails(self) -> None:
        template = _minimal_template(fallback="Not available")
        rendered = await render_support_template(
            template,
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}),
        )
        (field,) = rendered.sections[0].fields
        assert field.value == "Not available"
        assert field.applied_fallback is True
        assert rendered.gaps == ()

    async def test_missing_required_field_is_a_gap_not_a_blank(self) -> None:
        template = _minimal_template(required=True)
        rendered = await render_support_template(
            template,
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}),
        )
        assert rendered.sections[0].fields == ()
        (gap,) = rendered.gaps
        assert gap.field_id == "order_number"
        assert "confirmed_order_reference" in gap.reason
        assert rendered.review_blocked is True

    async def test_formatter_refusal_is_a_binding_failure(self) -> None:
        template = _minimal_template(formatter="date", required=True)
        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts=_facts(confirmed_order_reference="not-a-date"),
            ),
        )
        (gap,) = rendered.gaps
        assert "not a date" in gap.reason

    async def test_missing_optional_field_without_fallback_does_not_render(self) -> None:
        template = _minimal_template()
        rendered = await render_support_template(
            template,
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}),
        )
        assert rendered.sections[0].fields == ()
        assert rendered.gaps == ()

    async def test_literal_binding_renders_verbatim_and_cannot_gap(self) -> None:
        template = _minimal_template(
            source_binding="literal:- Review the complete return request.",
            label=None,
            required=True,
        )
        rendered = await render_support_template(
            template,
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}),
        )
        (field,) = rendered.sections[0].fields
        assert field.value == "- Review the complete return request."
        assert rendered.gaps == ()
        assert "- Review the complete return request." in rendered.text


class TestPerRecordSections:
    pytestmark = pytest.mark.asyncio

    def _template(self) -> SupportTemplateConfiguration:
        return _template(
            {
                "template_id": "t",
                "default_variant_id": "default",
                "variants": [
                    {
                        "variant_id": "default",
                        "subject_template": "s",
                        "sections": [
                            {
                                "section_id": "record",
                                "title": "Record:",
                                "fields": [
                                    {
                                        "field_id": "rma",
                                        "label": "RMA",
                                        "source_binding": "return_record:returnReference",
                                    },
                                    {
                                        "field_id": "tracking",
                                        "label": "Tracking",
                                        "source_binding": "case_fact:tracking_number",
                                        "fallback": "Not available",
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        )

    async def test_each_record_gets_its_own_stamped_group(self) -> None:
        rendered = await render_support_template(
            self._template(),
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts={
                    ("rec-1", "tracking_number"): {"value": "1Z-ONE", "factId": "f-1"},
                    ("rec-2", "tracking_number"): {"value": "LTL-TWO", "factId": "f-2"},
                },
                return_records=(
                    {"returnRecordId": "rec-1", "returnReference": "RMA-1"},
                    {"returnRecordId": "rec-2", "returnReference": "RMA-2"},
                ),
            ),
        )
        assert [section.return_record_id for section in rendered.sections] == ["rec-1", "rec-2"]
        first, second = rendered.sections
        # Each record's scoped fact stays its own: RMA-1 never renders RMA-2's
        # tracking number (multi-RMA integrity).
        assert [field.value for field in first.fields] == ["RMA-1", "1Z-ONE"]
        assert [field.value for field in second.fields] == ["RMA-2", "LTL-TWO"]

    async def test_a_case_level_fact_never_leaks_into_a_record_group(self) -> None:
        rendered = await render_support_template(
            self._template(),
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts={(None, "tracking_number"): {"value": "CASE-LEVEL"}},
                return_records=({"returnRecordId": "rec-1", "returnReference": "RMA-1"},),
            ),
        )
        (section,) = rendered.sections
        assert [field.value for field in section.fields] == ["RMA-1", "Not available"]

    async def test_a_per_record_value_never_reaches_the_subject(self) -> None:
        """RV advisory A1, from the render side.

        Release validation refuses a subject naming a per-record field, so this
        template is built past it. What must not happen is the render quietly
        resolving one: two RMAs, and the subject would have stated the second
        one's reference as if it were the request's.
        """
        template = self._template()
        variant = template.variants[0].model_copy(update={"subject_template": "Return {rma}"})
        template = template.model_copy(update={"variants": (variant,)})

        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts={},
                return_records=(
                    {"returnRecordId": "rec-1", "returnReference": "RMA-1"},
                    {"returnRecordId": "rec-2", "returnReference": "RMA-2"},
                ),
            ),
        )
        assert "RMA-2" not in rendered.subject
        assert rendered.subject == "Return Not available"

    async def test_an_undeclared_attribute_degrades_rather_than_reaching(self) -> None:
        """AMENDMENT-2, from the render side.

        Release validation already refuses `return_record:__class__`, so this
        template is built past it. The point is what the *resolver* does if it
        is ever handed such a name anyway: it must treat it as an absent value
        -- a gap or a fallback -- and never resolve it, so `<class '...'>`
        cannot reach the message a person on the Support desk reads, and no
        exception escapes to the caller either.
        """
        template = self._template()
        reaching = (
            template.variants[0]
            .sections[0]
            .fields[0]
            .model_copy(update={"source_binding": "return_record:__class__", "required": True})
        )
        section = template.variants[0].sections[0].model_copy(update={"fields": (reaching,)})
        variant = template.variants[0].model_copy(update={"sections": (section,)})
        template = template.model_copy(update={"variants": (variant,)})

        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts={},
                return_records=({"returnRecordId": "rec-1", "returnReference": "RMA-1"},),
            ),
        )
        assert rendered.text.find("class") == -1
        assert [gap.field_id for gap in rendered.gaps] == ["rma"]


class TestGraphBatching:
    pytestmark = pytest.mark.asyncio

    def _template(self, *paths: str) -> SupportTemplateConfiguration:
        return _template(
            {
                "template_id": "t",
                "default_variant_id": "default",
                "variants": [
                    {
                        "variant_id": "default",
                        "subject_template": "s",
                        "sections": [
                            {
                                "section_id": "graph",
                                "title": "Graph:",
                                "fields": [
                                    {
                                        "field_id": f"g{index}",
                                        "label": f"G{index}",
                                        "source_binding": f"graph:{path}",
                                        "required": True,
                                    }
                                    for index, path in enumerate(paths)
                                ],
                            }
                        ],
                    }
                ],
            }
        )

    async def test_n_missing_bindings_cost_one_sync_per_source(self) -> None:
        graph = _GraphStub(
            after_sync={
                "erp/order/total": "12.00",
                "erp/order/carrier": "UPS",
                "wms/bay/zone": "Z-4",
            }
        )
        template = self._template("erp/order/total", "erp/order/carrier", "wms/bay/zone")
        rendered = await render_support_template(
            template,
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}, graph=graph),
        )
        # Three missing bindings across two sources: exactly two synchronize
        # calls, each carrying every missing path of its source.
        assert sorted(source for source, _ in graph.sync_calls) == ["erp", "wms"]
        erp_paths = dict(graph.sync_calls)["erp"]
        assert set(erp_paths) == {"erp/order/total", "erp/order/carrier"}
        assert rendered.gaps == ()
        assert [field.value for field in rendered.sections[0].fields] == [
            "12.00",
            "UPS",
            "Z-4",
        ]

    async def test_still_missing_after_sync_is_a_gap(self) -> None:
        graph = _GraphStub()
        rendered = await render_support_template(
            self._template("erp/order/total"),
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}, graph=graph),
        )
        assert len(graph.sync_calls) == 1
        (gap,) = rendered.gaps
        assert "erp/order/total" in gap.reason

    async def test_a_re_render_of_the_same_draft_never_syncs_again(self) -> None:
        graph = _GraphStub()
        template = self._template("erp/order/total")
        cache = TemplateRenderCache()
        draft = TemplateDraftInput(
            case_id="c", context=TemplateRenderContext(), facts={}, graph=graph
        )
        await render_support_template(template, draft, cache=cache)
        assert len(graph.sync_calls) == 1
        reads_after_first = len(graph.reads)
        rendered_again = await render_support_template(template, draft, cache=cache)
        # Same draft, same cache: the sync budget is spent and the cached
        # resolution answers -- no second synchronize, no repeated read.
        assert len(graph.sync_calls) == 1
        assert len(graph.reads) == reads_after_first
        (gap,) = rendered_again.gaps
        assert "after sync" in gap.reason

    async def test_a_resolved_graph_value_is_cached_within_the_draft(self) -> None:
        graph = _GraphStub(values={"erp/order/total": "12.00"})
        template = self._template("erp/order/total")
        cache = TemplateRenderCache()
        draft = TemplateDraftInput(
            case_id="c", context=TemplateRenderContext(), facts={}, graph=graph
        )
        await render_support_template(template, draft, cache=cache)
        await render_support_template(template, draft, cache=cache)
        assert graph.reads == ["erp/order/total"]
        assert graph.sync_calls == []


class TestSubjectEscaping:
    pytestmark = pytest.mark.asyncio

    async def test_double_braces_render_as_literal_braces(self) -> None:
        template = _template(
            {
                "template_id": "t",
                "default_variant_id": "default",
                "variants": [
                    {
                        "variant_id": "default",
                        "subject_template": "{{not_a_placeholder}} {order_number} {{}}",
                        "sections": [
                            {
                                "section_id": "order",
                                "fields": [
                                    {
                                        "field_id": "order_number",
                                        "label": "Order Number",
                                        "source_binding": "case_fact:confirmed_order_reference",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )
        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts=_facts(confirmed_order_reference="CQ800002"),
            ),
        )
        assert rendered.subject == "{not_a_placeholder} CQ800002 {}"

    async def test_a_value_containing_braces_is_never_reinterpolated(self) -> None:
        template = _minimal_template()
        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="c",
                context=TemplateRenderContext(),
                facts=_facts(confirmed_order_reference="{order_number}"),
            ),
        )
        # The value is inserted as text; it does not become a placeholder.
        assert rendered.subject == "Return {order_number}"
        assert rendered.sections[0].fields[0].value == "{order_number}"


class TestComposedEquivalenceMatrix:
    """The seam test: the production default variant against
    `compose_support_handoff`, **on every branch its conditionals take**.

    The previous version of this compared one fixture, and the fixture happened
    to take the one branch of each conditional that the template could express
    -- a named associate, a recommended bay, nothing outstanding, no additional
    details. It was green and blind. Rendered against a case with no associate
    and no bay recommendation, the two paths diverged on five lines and the
    renderer reported no gaps at all, because every one of those lines was
    masked by a `fallback`.

    So this is a matrix, and it is the matrix of the conditionals rather than
    of the data: each case below flips one branch that the composed path
    decides for itself. Both paths are driven from **one** set of inputs, so a
    scenario cannot accidentally feed them different cases -- the snapshot half
    of the template's input comes from `support_template_snapshot`, which takes
    `compose_support_handoff`'s own argument list for exactly this reason.
    """

    pytestmark = pytest.mark.asyncio

    _CREATED_AT = datetime(2026, 8, 30, 9, 15, tzinfo=UTC)

    @staticmethod
    def _case(**overrides: object) -> dict:
        """One straight-through case, before a scenario flips a branch."""
        case: dict = {
            "case_id": "case-7",
            "work_item_id": "wi-9",
            "created_at": TestComposedEquivalenceMatrix._CREATED_AT,
            "workflow_status": "AWAITING_SUPPORT_HANDOFF",
            "customer": SupportHandoffCustomer(
                name="Rivera Plumbing",
                reference="CUST-55",
                account="ACCT-9",
                contact_name="Dana Reyes",
                contact_email="dana@example.com",
                contact_phone="555-0100",
                customer_phone="555-0199",
                customer_email="buyer@example.com",
            ),
            "order": SupportHandoffOrder(
                reference="CQ800002",
                items=(
                    SupportHandoffItem(
                        line_reference="10",
                        product_name="Water Filter Housing",
                        colour="Blue",
                        sku="WFH-100",
                        quantity=4,
                        reason="SHIPPING_DAMAGE",
                        condition="NEW_IN_ORIGINAL_PACKAGING",
                    ),
                ),
            ),
            "return_details": SupportHandoffReturn(
                method="PREPAID_PARCEL",
                requested_resolution="REFUND",
                product_presence="AT_BRANCH",
                associate_notes="Customer dropped the unit at the branch.",
            ),
            "bay": SupportHandoffBay(
                status="RECOMMENDED",
                bay_reference="BAY-12",
                warehouse_reference="WH-3",
                return_location="Dock B",
                handling_instructions="Keep upright.",
            ),
            "policy": SupportHandoffPolicy(state="EVALUATED", route="AUTO", decision="APPROVE"),
            "order_confirmed": True,
            "required_details_complete": True,
            "outstanding_support_dimensions": (),
            "support_state_known": True,
        }
        case.update(overrides)
        return case

    @staticmethod
    def _fact_log(case: dict) -> dict:
        """The fact-log half of the render input, from the same case.

        Which fact feeds which binding is `fact_log_projection`'s to say, not
        this test's -- a second mapping here is exactly the drift F2 is about.
        The ids are the test's own, because production's come off the fact log
        and a fabricated case has none; carrying them anyway is what keeps the
        matrix honest about provenance.
        """
        return {
            (None, name): {"value": value, "factId": f"fact-{name}"}
            for name, value in fact_log_projection(**case).items()
        }

    #: One entry per conditional branch the composed path can take. The name is
    #: what the scenario flips, not what the data says.
    _SCENARIOS: ClassVar[dict[str, dict]] = {
        "straight_through": {},
        "no_branch_associate": {
            "customer": SupportHandoffCustomer(
                name="Rivera Plumbing",
                reference="CUST-55",
                account="ACCT-9",
                customer_phone="555-0199",
                customer_email="buyer@example.com",
            )
        },
        "no_associate_and_no_customer_contact": {
            "customer": SupportHandoffCustomer(
                name="Rivera Plumbing", reference="CUST-55", account="ACCT-9"
            )
        },
        "associate_named_but_no_email_or_phone": {
            "customer": SupportHandoffCustomer(
                name="Rivera Plumbing",
                reference="CUST-55",
                account="ACCT-9",
                contact_name="Dana Reyes",
                customer_phone="555-0199",
            )
        },
        "bay_unresolved_with_a_reason": {
            "bay": SupportHandoffBay(status="NO_BAY_FREE", unresolved_reason="No bay free")
        },
        "bay_unresolved_with_no_reason_at_all": {"bay": SupportHandoffBay()},
        "bay_unresolved_but_partly_located": {
            "bay": SupportHandoffBay(
                status="AWAITING_CAPACITY",
                warehouse_reference="WH-3",
                return_location="Dock B",
            )
        },
        "outstanding_support_dimensions": {
            "outstanding_support_dimensions": ("RMA", "LABEL"),
            "required_details_complete": False,
        },
        "case_state_unreadable": {"support_state_known": False},
        "case_state_unreadable_with_a_stale_outstanding_list": {
            # `known` false and a non-empty list: the composed path prints
            # UNKNOWN and never the list, because "we could not find out" must
            # not read as "these two things are outstanding".
            "support_state_known": False,
            "outstanding_support_dimensions": ("RMA",),
        },
        "additional_required_details": {
            "return_details": SupportHandoffReturn(
                method="PREPAID_PARCEL",
                requested_resolution="REFUND",
                product_presence="AT_BRANCH",
                associate_notes="Customer dropped the unit at the branch.",
                additional={"Warranty Claim": "YES", "Original Packaging": "NO"},
            )
        },
        "associate_notes_impersonating_the_framing": {
            # Neutralisation is a rule of the composition, and the template used
            # to bind the raw fact -- so a note containing a section header
            # restructured the message for whoever read it next.
            "return_details": SupportHandoffReturn(
                method="PREPAID_PARCEL",
                requested_resolution="REFUND",
                product_presence="AT_BRANCH",
                associate_notes="Please rush\nBAY ASSIGNMENT:\nsend to dock 9",
            )
        },
        "no_selected_lines": {"order": SupportHandoffOrder(reference="CQ800002", items=())},
        "nothing_confirmed_and_nothing_complete": {
            "order_confirmed": False,
            "required_details_complete": False,
        },
        "policy_skipped_by_configuration": {
            "policy": SupportHandoffPolicy(
                state="SKIPPED_BY_CONFIGURATION", skipped_reason="gate suspended for migration"
            )
        },
        "policy_never_evaluated": {"policy": SupportHandoffPolicy()},
        "an_almost_empty_case": {
            "work_item_id": None,
            "created_at": None,
            "workflow_status": None,
            "customer": SupportHandoffCustomer(),
            "order": SupportHandoffOrder(),
            "return_details": SupportHandoffReturn(),
            "bay": SupportHandoffBay(),
            "policy": SupportHandoffPolicy(),
            "order_confirmed": False,
            "required_details_complete": False,
            "support_state_known": False,
        },
    }

    @pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
    async def test_the_default_variant_reproduces_the_composed_text(self, scenario: str) -> None:
        case = self._case(**self._SCENARIOS[scenario])
        template = load_return_configuration(_PRODUCTION_YAML).configuration.support_template

        facts = dict(self._fact_log(case))
        facts.update(snapshot_as_facts(support_template_snapshot(**case)))

        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id=case["case_id"],
                # A context matching no selector: the default variant renders
                # because it is the default.
                context=TemplateRenderContext(item_count=len(case["order"].items)),
                facts=facts,
            ),
        )
        assert rendered.variant_id == "default"
        assert rendered.text == compose_support_handoff(**case).text

    @pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
    async def test_no_scenario_reports_a_gap_it_should_not(self, scenario: str) -> None:
        """A gap is review-blocking, so a *normal* case must not raise one.

        None of these scenarios is broken -- a case with no branch associate is
        ordinary -- so the only required field, the case id, is always filled
        and nothing else may block a review. This is the other half of the F1
        failure: the first version reported no gaps *and* silently dropped
        lines. Both halves have to hold at once.
        """
        case = self._case(**self._SCENARIOS[scenario])
        template = load_return_configuration(_PRODUCTION_YAML).configuration.support_template
        facts = dict(self._fact_log(case))
        facts.update(snapshot_as_facts(support_template_snapshot(**case)))

        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id=case["case_id"],
                context=TemplateRenderContext(item_count=len(case["order"].items)),
                facts=facts,
            ),
        )
        assert rendered.gaps == ()

    async def test_a_missing_case_id_is_still_a_gap(self) -> None:
        """The one required field, so the matrix above cannot be vacuous."""
        template = load_return_configuration(_PRODUCTION_YAML).configuration.support_template
        rendered = await render_support_template(
            template,
            TemplateDraftInput(case_id="c", context=TemplateRenderContext(), facts={}),
        )
        assert [gap.field_id for gap in rendered.gaps] == ["case_id"]


class TestTheMatrixItself:
    """Not a rendering test: a test that the matrix above is not vacuous."""

    def test_the_matrix_exercises_every_snapshot_key(self) -> None:
        """Coverage of the vocabulary, not of the code.

        Every declared snapshot key must be produced by at least one scenario;
        a key no scenario produces is a key the equivalence claim never
        tested. (The two contact arms are mutually exclusive, so no single
        scenario yields them all -- hence the union.)
        """
        produced: set[str] = set()
        for overrides in TestComposedEquivalenceMatrix._SCENARIOS.values():
            produced.update(
                support_template_snapshot(**TestComposedEquivalenceMatrix._case(**overrides))
            )
        assert produced == set(SNAPSHOT_KEYS)

    def test_every_scenario_flips_something(self) -> None:
        # A scenario whose overrides change nothing would pad the count without
        # testing a branch. `straight_through` is the one deliberate baseline.
        baseline = TestComposedEquivalenceMatrix._case()
        for name, overrides in TestComposedEquivalenceMatrix._SCENARIOS.items():
            if name == "straight_through":
                continue
            assert TestComposedEquivalenceMatrix._case(**overrides) != baseline, name
