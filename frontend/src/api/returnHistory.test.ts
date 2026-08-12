/**
 * What the return-history client puts on the wire.
 *
 * The one thing worth pinning here is the customer key. An ERP customer number
 * is unique within a branch account and not across them, so a request that sent
 * only `customerId` would be answered for a different customer with the same
 * number in another branch -- and the response would look entirely plausible.
 * The backend refuses a half key; this asserts the client never builds one.
 */

import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { fixtureServer } from "../test/server";
import { returnHistoryApi, type ReturnHistory } from "./returnHistory";

function capture(): { readonly urls: string[] } {
  const urls: string[] = [];
  fixtureServer.use(
    http.get("/api/return-history", ({ request }) => {
      urls.push(new URL(request.url).search);
      return HttpResponse.json({
        data: {
          orderReference: null,
          accountId: null,
          customerId: null,
          cases: [],
        } satisfies ReturnHistory,
        meta: {
          schema_version: "1.0",
          request_id: "test",
          generated_at: new Date().toISOString(),
          freshness: "LIVE",
          partial: false,
          warnings: [],
        },
      });
    }),
  );
  return { urls };
}

describe("the return history client", () => {
  it("sends both halves of the customer key", async () => {
    const captured = capture();

    await returnHistoryApi.byCustomer("CHARLOTTE", "9911");

    const query = new URLSearchParams(captured.urls[0]);
    expect(query.get("accountId")).toBe("CHARLOTTE");
    expect(query.get("customerId")).toBe("9911");
    expect(query.get("orderReference")).toBeNull();
  });

  it("sends the order anchor alone", async () => {
    const captured = capture();

    await returnHistoryApi.byOrder("CQ363350");

    const query = new URLSearchParams(captured.urls[0]);
    expect(query.get("orderReference")).toBe("CQ363350");
    expect(query.get("accountId")).toBeNull();
  });

  it("escapes an anchor that would otherwise change the query", async () => {
    // Order references are ERP-issued and have no guaranteed character set. An
    // unescaped `&` would silently split into a second parameter and the
    // backend would answer about a different order.
    const captured = capture();

    await returnHistoryApi.byOrder("CW&customerId=9911");

    const query = new URLSearchParams(captured.urls[0]);
    expect(query.get("orderReference")).toBe("CW&customerId=9911");
    expect(query.get("customerId")).toBeNull();
  });

  it("treats a response with no body as no history rather than as a crash", async () => {
    // `apiClient` returns an envelope whose `data` is optional, and a screen
    // that had to null-check it would end up rendering "unknown" where "none"
    // is the truth.
    fixtureServer.use(
      http.get("/api/return-history", () =>
        HttpResponse.json({
          meta: {
            schema_version: "1.0",
            request_id: "test",
            generated_at: new Date().toISOString(),
            freshness: "LIVE",
            partial: false,
            warnings: [],
          },
        }),
      ),
    );

    await expect(returnHistoryApi.byOrder("CQ363350")).resolves.toEqual({
      orderReference: null,
      accountId: null,
      customerId: null,
      cases: [],
    });
  });
});
