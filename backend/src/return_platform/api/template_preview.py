"""Preview one support-template draft against the built-in sample case.

`POST /api/v1/config/support-template/preview` exists for exactly one screen:
the Configuration page's template editor. An operator editing a variant needs
to see what it renders *before* proposing the release -- the alternative is
publishing to find out, which is how a template with a broken selector or an
always-gapping required field reaches a real handoff.

Deliberately narrow:

- **The body carries the draft template, not a case id.** The render runs
  against a fixed sample case shipped here, so previewing can never read a
  real customer's data and needs no case access check -- `RETURNS_SESSION_READ`
  is the whole gate. The sample mirrors the representative fixture the
  equivalence test pins, so "preview of the default variant" and "what
  `compose_support_handoff` says today" are the same text.
- **No graph port.** A preview must not spend on-demand syncs; a `graph:`
  binding previews as its fallback or as a gap, labelled, which is itself
  useful information about the draft.
- **Validation is the model's.** An invalid draft is a 422 with pydantic's
  field-level detail -- the same refusal release validation would give.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.support_template_configuration import (
    SupportTemplateConfiguration,
)
from return_platform.operations.support_template_renderer import (
    RenderedTemplate,
    TemplateDraftInput,
    TemplateNotConfiguredError,
    TemplateRenderContext,
    render_support_template,
)
from return_platform.security.authorization import require_capability
from return_platform.security.capabilities import RETURNS_SESSION_READ
from return_platform.shared.contracts import APIResponse, ResponseMeta

router = APIRouter(prefix="/api/v1/config/support-template", tags=["Support Template"])


def _meta(request: Request) -> ResponseMeta:
    request_id = getattr(request.state, "correlation_id", "unknown")
    return ResponseMeta(request_id=request_id if isinstance(request_id, str) else "unknown")


#: The sample case a preview renders against. Fabricated by construction --
#: every value announces itself as a sample -- and shaped exactly like the
#: draft input the workflow assembler produces: `{"value": ...}` entries,
#: case-level partition only.
_SAMPLE_FACTS: dict[str, Any] = {
    "case_id": "sample-case",
    "work_item_id": "sample-work-item",
    "created_at": datetime(2026, 1, 15, 9, 30, tzinfo=UTC),
    "workflow_status_at_handoff": "AWAITING_SUPPORT_HANDOFF",
    "customer_name": "Sample Customer Ltd",
    "customer_id": "SAMPLE-CUST-1",
    "customer_account": "SAMPLE-ACCT-1",
    "branch_associate_name": "Sample Associate",
    "branch_associate_email": "associate@example.com",
    "branch_associate_phone": "555-0100",
    "confirmed_order_reference": "SAMPLE-ORDER-1",
    "selected_items": [
        {
            "lineReference": "10",
            "productName": "Sample Water Filter Housing",
            "colour": "Blue",
            "sku": "SAMPLE-SKU-1",
            "quantity": 2,
            "reason": "SHIPPING_DAMAGE",
            "condition": "NEW_IN_ORIGINAL_PACKAGING",
        }
    ],
    "return_method": "PREPAID_PARCEL",
    "requested_resolution": "REFUND",
    "product_presence": "AT_BRANCH",
    "associate_notes": "Sample note from the branch associate.",
    "bay_assignment_status": "RECOMMENDED",
    "bay_reference": "SAMPLE-BAY-1",
    "bay_warehouse_reference": "SAMPLE-WH-1",
    "bay_return_location": "Sample Dock",
    "bay_handling_instructions": "Keep upright.",
    "order_confirmation": "Confirmed",
    "required_return_information": "Complete",
    "policy_evaluation_rendered": "APPROVE on the AUTO route",
}

_SAMPLE_RECORDS: tuple[dict[str, Any], ...] = (
    {
        "returnRecordId": "sample-record-1",
        "returnReference": "SAMPLE-RMA-1",
        "status": "OPEN",
        "returnMethod": "PREPAID_PARCEL",
        "returnLocation": "Sample Dock",
        "approvedItems": ({"returnItemId": "sample-item-1"},),
    },
)


class TemplatePreviewContext(BaseModel):
    """What the draft's selectors are judged against -- operator-chosen, so a
    variant can be previewed as the case class that would earn it."""

    model_config = ConfigDict(extra="forbid")

    shipping_modes: tuple[str, ...] = ()
    return_reason_classes: tuple[str, ...] = ()
    order_sources: tuple[str, ...] = ()
    item_count: int = Field(default=1, ge=0)


class SupportTemplatePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: SupportTemplateConfiguration
    context: TemplatePreviewContext = Field(default_factory=TemplatePreviewContext)


class PreviewedField(BaseModel):
    field_id: str
    label: str | None
    value: str
    source: str
    source_path: str
    fact_id: str | None
    applied_fallback: bool


class PreviewedSection(BaseModel):
    section_id: str
    title: str | None
    return_record_id: str | None
    fields: list[PreviewedField]


class PreviewedGap(BaseModel):
    field_id: str
    reason: str


class SupportTemplatePreviewResponse(BaseModel):
    template_id: str
    variant_id: str
    subject: str
    text: str
    sections: list[PreviewedSection]
    gaps: list[PreviewedGap]
    review_blocked: bool


def _response(rendered: RenderedTemplate) -> SupportTemplatePreviewResponse:
    return SupportTemplatePreviewResponse(
        template_id=rendered.template_id,
        variant_id=rendered.variant_id,
        subject=rendered.subject,
        text=rendered.text,
        sections=[
            PreviewedSection(
                section_id=section.section_id,
                title=section.title,
                return_record_id=section.return_record_id,
                fields=[
                    PreviewedField(
                        field_id=field.field_id,
                        label=field.label,
                        value=field.value,
                        source=field.source,
                        source_path=field.source_path,
                        fact_id=field.fact_id,
                        applied_fallback=field.applied_fallback,
                    )
                    for field in section.fields
                ],
            )
            for section in rendered.sections
        ],
        gaps=[PreviewedGap(field_id=gap.field_id, reason=gap.reason) for gap in rendered.gaps],
        review_blocked=rendered.review_blocked,
    )


@router.post(
    "/preview",
    response_model=APIResponse[SupportTemplatePreviewResponse],
    summary="Render a support-template draft against the built-in sample case",
)
async def preview_support_template(
    body: SupportTemplatePreviewRequest,
    request: Request,
    _actor: str = Depends(require_capability(RETURNS_SESSION_READ)),
) -> APIResponse[SupportTemplatePreviewResponse]:
    draft = TemplateDraftInput(
        case_id="sample-case",
        context=TemplateRenderContext(
            shipping_modes=body.context.shipping_modes,
            return_reason_classes=body.context.return_reason_classes,
            order_sources=body.context.order_sources,
            item_count=body.context.item_count,
        ),
        facts={(None, name): {"value": value} for name, value in _SAMPLE_FACTS.items()},
        return_records=_SAMPLE_RECORDS,
        graph=None,
    )
    try:
        rendered = await render_support_template(body.template, draft)
    except TemplateNotConfiguredError as empty:
        # An empty draft is a draft state, not a server fault: answered as a
        # 422-shaped refusal the editor can show inline.
        raise HTTPException(status_code=422, detail=str(empty)) from empty
    # Enveloped like every other route this console reads: `apiClient` refuses a
    # bare body, so a preview that answered outside the envelope would be
    # unreachable from the one screen it exists for.
    return APIResponse(data=_response(rendered), meta=_meta(request))
