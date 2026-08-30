import { registerPanelSectionRenderer } from "../panelSectionRegistry";
import { SUPPORT_SECTION_IDS, SUPPORT_SECTION_ORDER } from "./supportPanelPayloads";
import {
  SupportAnnouncerSection,
  SupportDigestSection,
  SupportParkedSection,
  SupportRecordsSection,
} from "./supportSections";

/**
 * V2's four renderers into V1's registry, and the one import V2 asks of
 * anybody else's file.
 *
 * A renderer registers at import time, so a registration module nothing imports
 * is a section that never draws -- and the failure is silent, because
 * `CasePanel` renders a contributed section with no renderer as a labelled
 * placeholder that reads exactly like a deployment skew. So the two screens that
 * mount `CasePanel` -- `ReturnCopilotPage` and `CaseOperationsPage` -- each carry
 * one side-effect import of this file, and nothing else about V2 appears in
 * either.
 *
 * Separate from `supportSections.tsx` because that file exports components and
 * this one exports functions; keeping them apart is what lets fast refresh work
 * on the sections while somebody is looking at them.
 */
let installed = false;

/**
 * Guarded, and not for tidiness: the registry throws on a duplicate id, both
 * screens can be in one bundle, and a module is evaluated once per module graph
 * -- but a test that resets modules, or a lazily split chunk, can evaluate it
 * twice. Throwing there would take a screen down over a bookkeeping detail.
 *
 * The guard is **this module's**, not the registry's. Registering the same id
 * from two genuinely different modules still throws, which is the race the
 * registry exists to refuse.
 */
export function installSupportPanelSections(): void {
  if (installed) return;
  installed = true;
  registerSupportPanelSections();
}

/** The registrations themselves, unguarded, so a test can assert the refusal. */
export function registerSupportPanelSections(): void {
  registerPanelSectionRenderer({
    sectionId: SUPPORT_SECTION_IDS.announcer,
    order: SUPPORT_SECTION_ORDER[SUPPORT_SECTION_IDS.announcer],
    render: (props) => <SupportAnnouncerSection {...props} />,
  });
  registerPanelSectionRenderer({
    sectionId: SUPPORT_SECTION_IDS.parked,
    order: SUPPORT_SECTION_ORDER[SUPPORT_SECTION_IDS.parked],
    render: (props) => <SupportParkedSection {...props} />,
  });
  registerPanelSectionRenderer({
    sectionId: SUPPORT_SECTION_IDS.records,
    order: SUPPORT_SECTION_ORDER[SUPPORT_SECTION_IDS.records],
    render: (props) => <SupportRecordsSection {...props} />,
  });
  registerPanelSectionRenderer({
    sectionId: SUPPORT_SECTION_IDS.digest,
    order: SUPPORT_SECTION_ORDER[SUPPORT_SECTION_IDS.digest],
    render: (props) => <SupportDigestSection {...props} />,
  });
}

/** Test seam. Lets a spec clear the registry and re-install deliberately. */
export function resetSupportPanelSectionInstall(): void {
  installed = false;
}

installSupportPanelSections();
