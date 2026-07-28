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
