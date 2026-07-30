import { describe, expect, it } from "vitest";

import { routes } from "./routes";
import {
  COPILOT_V2_PATH,
  legacyRouteDestination,
  normalizeBrowserPath,
  VERSION_ONE_PREFIX,
} from "./versioning";

describe("frontend route versioning", () => {
  it("keeps every existing root route reachable under v1", () => {
    for (const route of routes) {
      expect(legacyRouteDestination(route.path)).toBe(`${VERSION_ONE_PREFIX}${route.path}`);
    }
  });

  it("redirects the old root to the v1 returns assistant and preserves URL state", () => {
    expect(legacyRouteDestination("/", "?source=legacy", "#message")).toBe(
      "/v1/associate/returns?source=legacy#message",
    );
    expect(normalizeBrowserPath("/support/returns/")).toBe("/support/returns");
  });

  it("reserves the v2 UI surface for Copilot", () => {
    expect(COPILOT_V2_PATH).toBe("/v2/copilot");
  });
});
