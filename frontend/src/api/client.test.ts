import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { apiClient } from "./client";
import { fixtureServer } from "../test/server";

describe("apiClient error details", () => {
  it("surfaces FastAPI string details instead of only the HTTP status", async () => {
    fixtureServer.use(
      http.get("/api/test/string-detail", () => (
        HttpResponse.json({ detail: "Return details are incomplete: branch_id" }, { status: 422 })
      )),
    );

    await expect(apiClient("/api/test/string-detail")).rejects.toMatchObject({
      message: "Return details are incomplete: branch_id",
      status: 422,
    });
  });

  it("surfaces FastAPI validation locations and messages", async () => {
    fixtureServer.use(
      http.get("/api/test/validation-detail", () => (
        HttpResponse.json({
          detail: [{
            loc: ["body", "shippingPathExpectation"],
            msg: "Input should be a normalized return method",
            type: "enum",
          }],
        }, { status: 422 })
      )),
    );

    await expect(apiClient("/api/test/validation-detail")).rejects.toMatchObject({
      message: "body.shippingPathExpectation: Input should be a normalized return method",
      status: 422,
    });
  });
});

describe("the apiClient response envelope", () => {
  const meta = {
    schema_version: "1.0",
    request_id: "test",
    generated_at: new Date().toISOString(),
    freshness: "LIVE",
    partial: false,
    warnings: [],
  };

  it("accepts an envelope carrying no data, and settles the absence to null", async () => {
    // `data` and `page` are `T | None = None` on the backend models, so the
    // generated schema marks neither required. An envelope that omits `data` is
    // the contract's "nothing to report", not a malformed response.
    fixtureServer.use(
      http.get("/api/test/no-data", () => HttpResponse.json({ meta })),
    );

    await expect(apiClient("/api/test/no-data")).resolves.toMatchObject({
      data: null,
      page: null,
    });
  });

  it("rejects a body that is not an envelope at all", async () => {
    // The case this check exists for: a route answering with its bare result
    // instead of the platform envelope. `meta` is what catches it.
    fixtureServer.use(
      http.get("/api/test/bare-body", () => (
        HttpResponse.json({ answer: "reasoned correctly, shaped wrongly" })
      )),
    );

    await expect(apiClient("/api/test/bare-body")).rejects.toMatchObject({
      message: "The server returned an invalid API response envelope.",
      status: 200,
    });
  });

  it("rejects a 200 that carries no body at all", async () => {
    fixtureServer.use(
      http.get("/api/test/empty-body", () => new HttpResponse(null, { status: 200 })),
    );

    await expect(apiClient("/api/test/empty-body")).rejects.toMatchObject({
      message: "The server returned an invalid API response envelope.",
      status: 200,
    });
  });
});
