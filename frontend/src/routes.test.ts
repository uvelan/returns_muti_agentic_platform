import { describe, expect, it } from "vitest";

import { routes } from "./routes";

const mandatoryRoutes = [
  "/associate/returns",
  "/operations/returns/:sessionId",
  "/operations/return-agents",
  "/return-support/workbench",
  "/logistics/returns",
  "/warehouse/returns",
  "/tracking/returns",
  "/system/integration-outbox",
  "/system/dependencies",
  "/system/dependency-simulator",
  "/system/dependency-simulator/omc",
  "/system/dependency-simulator/parcel",
  "/system/dependency-simulator/freight",
  "/system/dependency-simulator/lsi",
  "/system/dependency-simulator/ai-metrics",
  "/system/dependency-simulator/operations/:operationId",
  "/ai-gateway/requests",
  "/ai-gateway/routes",
  "/ai-gateway/tasks",
  "/ai-gateway/metrics",
  "/ai-gateway/safety",
  "/ai-gateway/simulator",
  "/ai-gateway/interceptions",
] as const;

describe("mandatory Stage 4O routes", () => {
  it("registers every required screen exactly once", () => {
    const paths = routes.map((route) => route.path);

    for (const path of mandatoryRoutes) {
      expect(paths.filter((candidate) => candidate === path)).toHaveLength(1);
    }
  });

  it("makes every mandatory top-level work queue navigable", () => {
    const navigableRoutes = [
      "/associate/returns",
      "/operations/return-agents",
      "/return-support/workbench",
      "/logistics/returns",
      "/warehouse/returns",
      "/tracking/returns",
      "/system/integration-outbox",
      "/system/dependencies",
      "/system/dependency-simulator",
      "/ai-gateway/requests",
      "/ai-gateway/routes",
      "/ai-gateway/tasks",
      "/ai-gateway/metrics",
      "/ai-gateway/safety",
      "/ai-gateway/simulator",
      "/ai-gateway/interceptions",
    ];

    for (const path of navigableRoutes) {
      expect(routes.find((route) => route.path === path)?.navigable).toBe(true);
    }
  });
});
