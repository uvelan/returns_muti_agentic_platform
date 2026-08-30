import { apiClient } from "./client";
import type { components } from "./generated/return-platform";

/**
 * `POST /api/v1/config/support-template/preview` -- render a draft template
 * against the platform's built-in sample case.
 *
 * The route exists for one screen: an operator editing the template needs to
 * see what it renders *before* the release is published, because the
 * alternative is publishing to find out, which is how a variant with a broken
 * selector or an always-gapping required field reaches a real handoff.
 *
 * **The request carries the draft, never a case id.** The backend renders
 * against a fabricated sample, so a preview cannot read a customer's data and
 * cannot spend an on-demand graph sync -- a `graph:` binding previews as its
 * fallback or as a gap, which is itself worth knowing about the draft.
 */

export type SupportTemplatePreviewResponse =
  components["schemas"]["SupportTemplatePreviewResponse"];
export type PreviewedSection = components["schemas"]["PreviewedSection"];
export type PreviewedField = components["schemas"]["PreviewedField"];
export type PreviewedGap = components["schemas"]["PreviewedGap"];
export type TemplatePreviewContext = components["schemas"]["TemplatePreviewContext"];

/**
 * The draft is typed loosely on purpose.
 *
 * `SupportTemplateConfiguration` describes a *valid* template, and the whole
 * point of a preview is to send one that may not be: a misspelled formatter or
 * an unresolvable subject placeholder must reach the backend and come back as
 * that backend's own 422. Typing the field as the valid model here would make
 * the editor structurally unable to submit the drafts this endpoint exists to
 * refuse -- a second, weaker definition of valid, in the client.
 */
export type SupportTemplateDraft = Readonly<Record<string, unknown>>;

export const supportTemplateApi = {
  async preview(
    template: SupportTemplateDraft,
    context: TemplatePreviewContext,
  ): Promise<SupportTemplatePreviewResponse> {
    const response = await apiClient<SupportTemplatePreviewResponse>(
      "/api/v1/config/support-template/preview",
      {
        method: "POST",
        // `createHeaders` sets Accept but not Content-Type.
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template, context }),
      },
    );
    if (!response.data) throw new Error("The preview returned no rendered template.");
    return response.data;
  },
};
