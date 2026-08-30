import type { ReactNode } from "react";

import type { CasePanelView, PanelSectionView } from "../../../../api/casePanel";

/**
 * The console half of the section seam (contracts.md sect. 9).
 *
 * The backend registry decides what is *in* `CasePanelView.sections[]`; this
 * one decides how each is drawn. They are deliberately two registries and not
 * one contract: a contributed section's payload is an opaque JSON object on the
 * wire precisely so V2's and V3's shapes never enter V1's DTO, and the same
 * reasoning applies here -- a renderer typed over every possible payload would
 * put those shapes into this file instead.
 *
 * **V1 registers the two built-ins and then never touches this file again.**
 * V2 and V3 call `registerPanelSectionRenderer` from their own modules.
 */

export type PanelSectionRendererProps = {
  /** The contributed section, or `undefined` for a built-in that has none. */
  readonly section: PanelSectionView | undefined;
  /** The whole panel, because a built-in reads fields rather than a payload. */
  readonly panel: CasePanelView;
  readonly caseId: string;
};

export type PanelSectionRenderer = {
  /** Matches `PanelSectionView.section_id`, or names a built-in. */
  readonly sectionId: string;
  /**
   * Where this section sits. Explicit rather than registration-ordered for the
   * backend registry's reason: registration order is import order, and a
   * section that moved when a module moved would be a layout nobody could
   * explain. Ties break on `sectionId`, so the order is total.
   */
  readonly order: number;
  readonly render: (props: PanelSectionRendererProps) => ReactNode;
};

const renderers = new Map<string, PanelSectionRenderer>();

export function registerPanelSectionRenderer(renderer: PanelSectionRenderer): void {
  if (renderers.has(renderer.sectionId)) {
    // Two renderers for one id would race, and which won would depend on
    // import order -- the same failure the backend registry refuses.
    throw new Error(`panel section renderer ${renderer.sectionId} is already registered`);
  }
  renderers.set(renderer.sectionId, renderer);
}

export function panelSectionRenderers(): readonly PanelSectionRenderer[] {
  return [...renderers.values()].sort(
    (left, right) => left.order - right.order || left.sectionId.localeCompare(right.sectionId),
  );
}

/** For tests, and named so its purpose is not mistaken for a reset-on-mount. */
export function clearPanelSectionRenderers(): void {
  renderers.clear();
}

/**
 * A contributed section with no renderer.
 *
 * Rendered as a labelled placeholder rather than dropped, because a section the
 * backend went to the trouble of composing and the console silently discards is
 * a deployment skew nobody can see: the server is newer than the bundle. The
 * placeholder says so.
 */
export function unrenderedSectionLabel(section: PanelSectionView): string {
  return section.status === "degraded"
    ? `${section.section_id}: temporarily unavailable`
    : `${section.section_id}: this console build cannot display this section yet`;
}
