"""The renderer holds contracts.md sect. 8: selection, binding, batching, gaps.

The one test that matters most is the last: the production release's default
variant renders, character for character, the text `compose_support_handoff`
composes today for a representative case -- which is what lets the template
path replace the composed path without anybody noticing the seam.
"""

from datetime import datetime, timezone
from pathlib import Path

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
                    "sections": [
                        {"section_id": "order", "title": "Order:", "fields": [field]}
                    ],
                }
            ],
        }
    )


class _GraphStub:
    """A graph port that counts its traffic, per the batching acceptance."""

    def __init__(self, values: dict[str, object] | None = None, after_sync: dict[str, object] | None = None):
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
        context = TemplateRenderContext(
            shipping_modes=("BRANCH_LTL", "OFFSITE_LTL"), item_count=2
        )
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
            select_variant(template, TemplateRenderContext(item_count=4)).variant_id
            == "also_small"
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
                facts={
                    (None, "confirmed_order_reference"): {"value": "CQ800002", "factId": "f-1"}
                },
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
            TemplateDraftInput(
                case_id="c", context=TemplateRenderContext(), facts={}, graph=graph
            ),
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
            TemplateDraftInput(
                case_id="c", context=TemplateRenderContext(), facts={}, graph=graph
            ),
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


class TestDefaultVariantEquivalence:
    pytestmark = pytest.mark.asyncio
    """The seam test: production default variant vs `compose_support_handoff`."""

    _CREATED_AT = datetime(2026, 8, 30, 9, 15, tzinfo=timezone.utc)

    _ITEMS = (
        {
            "lineReference": "10",
            "productName": "Water Filter Housing",
            "colour": "Blue",
            "sku": "WFH-100",
            "quantity": 4,
            "reason": "SHIPPING_DAMAGE",
            "condition": "NEW_IN_ORIGINAL_PACKAGING",
        },
    )

    def _composed(self) -> str:
        return compose_support_handoff(
            case_id="case-7",
            work_item_id="wi-9",
            created_at=self._CREATED_AT,
            workflow_status="AWAITING_SUPPORT_HANDOFF",
            customer=SupportHandoffCustomer(
                name="Rivera Plumbing",
                reference="CUST-55",
                account="ACCT-9",
                contact_name="Dana Reyes",
                contact_email="dana@example.com",
                contact_phone="555-0100",
            ),
            order=SupportHandoffOrder(
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
            return_details=SupportHandoffReturn(
                method="PREPAID_PARCEL",
                requested_resolution="REFUND",
                product_presence="AT_BRANCH",
                associate_notes="Customer dropped the unit at the branch.",
            ),
            bay=SupportHandoffBay(
                status="RECOMMENDED",
                bay_reference="BAY-12",
                warehouse_reference="WH-3",
                return_location="Dock B",
                handling_instructions="Keep upright.",
            ),
            policy=SupportHandoffPolicy(state="EVALUATED", route="AUTO", decision="APPROVE"),
            order_confirmed=True,
            required_details_complete=True,
            outstanding_support_dimensions=(),
            support_state_known=True,
        ).text

    async def test_default_variant_reproduces_the_composed_text(self) -> None:
        template = load_return_configuration(_PRODUCTION_YAML).configuration.support_template
        facts = _facts(
            case_id="case-7",
            work_item_id="wi-9",
            created_at=self._CREATED_AT,
            workflow_status_at_handoff="AWAITING_SUPPORT_HANDOFF",
            customer_name="Rivera Plumbing",
            customer_id="CUST-55",
            customer_account="ACCT-9",
            branch_associate_name="Dana Reyes",
            branch_associate_email="dana@example.com",
            branch_associate_phone="555-0100",
            confirmed_order_reference="CQ800002",
            selected_items=list(self._ITEMS),
            return_method="PREPAID_PARCEL",
            requested_resolution="REFUND",
            product_presence="AT_BRANCH",
            associate_notes="Customer dropped the unit at the branch.",
            bay_assignment_status="RECOMMENDED",
            bay_reference="BAY-12",
            bay_warehouse_reference="WH-3",
            bay_return_location="Dock B",
            bay_handling_instructions="Keep upright.",
            order_confirmation="Confirmed",
            required_return_information="Complete",
            policy_evaluation_rendered="APPROVE on the AUTO route",
        )
        # A context matching no selector: the default variant renders because
        # it is the default, which is the representative straight-through case.
        rendered = await render_support_template(
            template,
            TemplateDraftInput(
                case_id="case-7", context=TemplateRenderContext(item_count=1), facts=facts
            ),
        )
        assert rendered.variant_id == "default"
        assert rendered.gaps == ()
        assert rendered.text == self._composed()
