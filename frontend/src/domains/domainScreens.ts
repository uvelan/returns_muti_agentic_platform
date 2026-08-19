import { lazy, type ComponentType } from "react";

/**
 * Domains whose screen is built. The rest fall back to `DomainLanding` until
 * their phase lands, so adding a screen is one entry here rather than an edit
 * to the routing below.
 */
export const DOMAIN_SCREENS: Partial<Record<string, ComponentType>> = {
  "/graph-schema": lazy(() =>
    import("./graph-schema/GraphAnalyzerWorkspace").then((m) => ({
      default: m.GraphAnalyzerWorkspace,
    })),
  ),
  "/ai": lazy(() =>
    import("./ai/AiControlCenterPage").then((m) => ({ default: m.AiControlCenterPage })),
  ),
  "/returns": lazy(() =>
    import("./returns/ReturnCopilotPage").then((m) => ({ default: m.ReturnCopilotPage })),
  ),
  "/support": lazy(() =>
    import("./support/SupportConsolePage").then((m) => ({ default: m.SupportConsolePage })),
  ),
  "/config": lazy(() =>
    import("./config/ConfigurationPage").then((m) => ({ default: m.ConfigurationPage })),
  ),
  "/operations": lazy(() =>
    import("./operations/OperationsPage").then((m) => ({ default: m.OperationsPage })),
  ),
  "/sync": lazy(() =>
    import("./sync/SyncControlPage").then((m) => ({ default: m.SyncControlPage })),
  ),
  "/approvals": lazy(() =>
    import("./approvals/ApprovalsPage").then((m) => ({ default: m.ApprovalsPage })),
  ),
};
