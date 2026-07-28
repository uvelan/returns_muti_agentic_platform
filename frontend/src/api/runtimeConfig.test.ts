import { expect, it, vi } from "vitest";

import { fetchRuntimeConfig } from "./runtimeConfig";

it("loads runtime configuration through the browser API proxy", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(
      JSON.stringify({
        data: {
          releaseId: "release-test",
          environment: "development",
          apiBasePath: "/api/v1",
          features: {
            orderDiscoveryCopilot: true,
            aiStudioOperationalGeneration: true,
          },
          capabilities: {
            availableSourceTypes: ["MONGODB"],
            availableModelProviders: [],
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
  });
  expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/runtime-config");
});
