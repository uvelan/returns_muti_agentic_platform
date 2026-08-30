import { registerPanelSectionRenderer } from "../panelSectionRegistry";
import { ClarificationsSection } from "./ClarificationsSection";
import { CLARIFICATIONS_SECTION_ID } from "./clarificationModel";

/**
 * Contribute V3's section to the panel.
 *
 * ---
 *
 * **Its own file** for two reasons. The mechanical one is that a `.tsx` module
 * exporting both a component and a plain function loses fast refresh, which the
 * lint rule enforces. The one that matters is that this is the *only* thing on
 * the seam: everything else V3 owns is a renderer and a reader, and the single
 * line that puts them on somebody's screen deserves to be findable.
 *
 * **A function, not a module side effect.** A bare
 * `import "./ClarificationsSection"` would make the section appear or not
 * depending on which other module happened to import it first -- import-order
 * dependence, which is precisely what `order` and the registry's duplicate-id
 * refusal exist to keep out of the layout. The composition root calls this.
 *
 * `order: 30` puts the section after V1's built-ins and after V2's ingress
 * contribution, which is the right way round: the reviews are what an associate
 * is blocked on, and a question from Support is what they do next.
 *
 * **The first draft of this section registered nothing at all** -- it exported a
 * component, and no module imported it. It could not have drawn on any panel,
 * and no test that rendered the component by hand would ever have said so.
 */
export function registerClarificationsSection(): void {
  registerPanelSectionRenderer({
    sectionId: CLARIFICATIONS_SECTION_ID,
    order: 30,
    render: ClarificationsSection,
  });
}
