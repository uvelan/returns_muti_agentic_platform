import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { requireDomain, toSlug } from "./registry";
import { useDomainSection } from "./useDomainSection";

const CONFIG = requireDomain("/config");
const RETURNS = requireDomain("/returns");

function at(path: string) {
  window.history.pushState(null, "", path);
  return renderHook(() => useDomainSection(CONFIG)).result.current;
}

describe("the section a domain screen renders", () => {
  beforeEach(() => {
    window.history.pushState(null, "", "/");
  });

  it("comes from the URL", () => {
    expect(at("/config/releases")).toBe("Releases");
    expect(at("/config/data-sources")).toBe("Data Sources");
  });

  it("falls back to the first section at the bare domain path", () => {
    // `/config` and `/config/overview` are the same screen. Without this the
    // sidebar would show nothing selected on arrival.
    expect(at("/config")).toBe("Overview");
  });

  it("falls back rather than blanking on a stale or hand-edited slug", () => {
    expect(at("/config/a-section-that-was-renamed")).toBe("Overview");
  });

  it("ignores anything below the section segment", () => {
    // Screens are free to add their own nested routes later; the section is
    // the first segment and nothing deeper changes it.
    expect(at("/config/releases/rel-1")).toBe("Releases");
  });

  it("is empty for a domain that declares no sections", () => {
    window.history.pushState(null, "", "/returns");
    const { result } = renderHook(() => useDomainSection(RETURNS));
    expect(result.current).toBe("");
  });
});

describe("section slugs", () => {
  it("survives punctuation and spacing", () => {
    expect(toSlug("Providers & Models")).toBe("providers-models");
    expect(toSlug("Routes & Tasks")).toBe("routes-tasks");
    expect(toSlug("Data Sources")).toBe("data-sources");
  });

  it("is unique within a domain, so no two entries claim one URL", () => {
    for (const domain of [CONFIG, requireDomain("/ai")]) {
      const slugs = domain.sections.map((section) => section.slug);
      expect(new Set(slugs).size).toBe(slugs.length);
    }
  });
});
