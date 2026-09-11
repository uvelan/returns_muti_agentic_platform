/**
 * `countPatchLeaves` -- RV CFG-4 round 2, F6. `TypedSectionScreen`'s
 * `dirtyCount` used to be `Object.keys(patch).length`, so two edits nested
 * under the same top-level key both read as "1 change staged". These pin
 * the walk directly, independent of any one screen.
 */

import { describe, expect, it } from "vitest";

import { countPatchLeaves } from "./jsonPath";

describe("countPatchLeaves", () => {
  it("counts a flat patch's own keys", () => {
    expect(countPatchLeaves({ a: 1, b: 2 })).toBe(2);
  });

  it("counts an empty patch as zero", () => {
    expect(countPatchLeaves({})).toBe(0);
  });

  it("walks into a nested object patch rather than counting the parent key once", () => {
    // The motivating case: two edits under one RETURN_PLATFORM section.
    expect(countPatchLeaves({ discovery: { ambiguity_gap_millionths: 900_000, strong_anchors: [] } })).toBe(2);
  });

  it("sums leaves across sibling sections", () => {
    expect(countPatchLeaves({ discovery: { a: 1 }, workflow: { b: 2, c: 3 } })).toBe(3);
  });

  it("walks arbitrarily deep", () => {
    expect(countPatchLeaves({ a: { b: { c: 1, d: 2 } } })).toBe(2);
  });

  it("counts a null (RFC 7396 deletion) as one leaf, not zero", () => {
    expect(countPatchLeaves({ a: null })).toBe(1);
  });

  it("counts an array as one leaf -- a merge patch replaces it wholesale, so there is nothing inside it to walk", () => {
    expect(countPatchLeaves({ tags: ["a", "b", "c"] })).toBe(1);
  });

  it("mixes scalars, deletions, arrays and nested objects in one patch", () => {
    expect(
      countPatchLeaves({
        enabled: false,
        disabled_reason: "paused",
        removed_field: null,
        tags: ["x", "y"],
        nested: { a: 1, b: { c: 2 } },
      }),
    ).toBe(6);
  });
});
