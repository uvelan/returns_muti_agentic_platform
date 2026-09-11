import { describe, expect, it } from "vitest";

import { mergePatchOf } from "./mergePatch";

describe("mergePatchOf", () => {
  it("is empty when nothing changed", () => {
    const document = { enabled: true, queues: ["A", "B"], nested: { a: 1 } };
    expect(mergePatchOf(document, structuredClone(document))).toEqual({});
  });

  it("names only the leaves that moved", () => {
    expect(
      mergePatchOf(
        { enabled: false, disabled_reason: "paused", limits: { max: 5, min: 1 } },
        { enabled: true, disabled_reason: "paused", limits: { max: 9, min: 1 } },
      ),
    ).toEqual({ enabled: true, limits: { max: 9 } });
  });

  it("deletes with null, which sending the document whole never could", () => {
    // The defect: a variant removed in the editor stayed in the release,
    // because `{ support_template: document }` merges and never removes.
    expect(
      mergePatchOf(
        { ship_via_methods: { CPU: "COUNTER", XPW: "PARCEL" } },
        { ship_via_methods: { CPU: "COUNTER" } },
      ),
    ).toEqual({ ship_via_methods: { XPW: null } });
  });

  it("sends an edited list whole, as the RFC does", () => {
    expect(mergePatchOf({ codes: ["A", "B"] }, { codes: ["A"] })).toEqual({ codes: ["A"] });
  });

  it("adds a new key with its whole value", () => {
    expect(mergePatchOf({ a: 1 }, { a: 1, b: { c: 2 } })).toEqual({ b: { c: 2 } });
  });

  it("replaces whole when either side is not an object", () => {
    expect(mergePatchOf({ a: 1 }, "text")).toBe("text");
    expect(mergePatchOf(null, { a: 1 })).toEqual({ a: 1 });
  });
});
