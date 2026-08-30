"""Preview one support-template draft against the built-in sample case.

`POST /api/v1/config/support-template/preview` exists for exactly one screen:
the Configuration page's template editor. An operator editing a variant needs
to see what it renders *before* proposing the release -- the alternative is
publishing to find out, which is how a template with a broken selector or an
always-gapping required field reaches a real handoff.

Deliberately narrow:

- **The body carries the draft template, not a case id.** The render runs
  against `support_template_draft.SAMPLE_CASE`, so previewing can never read a
  real customer's data and needs no case access check -- `RETURNS_SESSION_READ`
  is the whole gate.
- **The sample is not defined here.** It used to be: a second, hand-written
  copy of the whole binding vocabulary, with nothing keeping it in step with
  `production.yaml`, under a docstring claiming a preview showed "what
  `compose_support_handoff` says today" that no test checked. A rename in the
  yaml would have made the preview quietly show fallbacks for values a real
  case fills, which is the one thing this screen exists to be trusted about.
  The sample and its facts now come from the same producer the workflow will
  use, and the claim is a test (`test_the_preview_is_the_composed_text`).
- **No graph port.** A preview must not spend on-demand syncs; a `graph:`
  binding previews as its fallback or as a gap, labelled, which is itself
  useful information about the draft.
- **Validation is the model's.** An invalid draft is a 422 with pydantic's
  field-level detail -- the same refusal release validation would give.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from return_platform.configuration.support_template_configuration import (
    SupportTemplateConfiguration,
)
from return_platform.operations.support_template_draft import SAMPLE_CASE, draft_facts
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


#: One return record, so a per-record section group has something to render.
#: Not part of `SAMPLE_CASE`: `compose_support_handoff` predates return records
#: and knows nothing about them, so this belongs to the preview rather than to
#: the equivalence seam.
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
        case_id=str(SAMPLE_CASE["case_id"]),
        context=TemplateRenderContext(
            shipping_modes=body.context.shipping_modes,
            return_reason_classes=body.context.return_reason_classes,
            order_sources=body.context.order_sources,
            # The operator's own count, not the sample's: they are describing
            # the case shape a variant should be judged against, and the sample
            # is only what fills the fields once one is chosen.
            item_count=body.context.item_count,
        ),
        facts=draft_facts(**SAMPLE_CASE),
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
