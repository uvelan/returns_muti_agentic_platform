import { expect, it, vi } from "vitest";

import { fetchRuntimeConfig } from "./runtimeConfig";

it("loads runtime configuration from the canonical versionless path", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(
      JSON.stringify({
        data: {
          releaseId: "release-test",
          environment: "development",
          apiBasePath: "/api",
          features: {
            orderDiscoveryCopilot: true,
          },
          capabilities: {
            availableSourceTypes: ["MONGODB"],
            availableModelProviders: [],
          },
          agents: {
            orderDiscovery: "order-discovery-agent",
          },
        },
        page: null,
        meta: {
          schema_version: "1.0",
          request_id: "11111111-1111-4111-8111-111111111111",
          generated_at: "2026-07-28T00:00:00Z",
          freshness: "LIVE",
          partial: false,
          warnings: [],
        },
      }),
      {
        status: 200,
        headers: {
          "Content-Type": "application/json",
        },
      },
    ),
  );
  vi.stubGlobal("fetch", fetchMock);

  await expect(fetchRuntimeConfig()).resolves.toMatchObject({
    releaseId: "release-test",
    // The copilot's agent id travels on this payload rather than being compiled
    // into the shell -- the literal that did was `"order_discovery"`, which the
    // active schema has never known by that name.
    agents: { orderDiscovery: "order-discovery-agent" },
  });
  // The point of the assertion: no `/v1`. This was the shell's last hard
  // dependency on a versioned route.
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/runtime-config");
});
