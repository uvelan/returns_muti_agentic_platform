import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useRailCollapsed } from "./useRailCollapsed";

const KEY = "platform.rail.collapsed";

describe("the rail's collapsed state", () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  it("starts expanded", () => {
    expect(renderHook(() => useRailCollapsed()).result.current[0]).toBe(false);
  });

  it("survives the remount that every domain change causes", () => {
    // The rail unmounts and remounts when the domain changes. Held in plain
    // component state this would silently re-expand each time, which reads as
    // the control not working rather than as a reset.
    const first = renderHook(() => useRailCollapsed());
    act(() => { first.result.current[1](); });
    expect(first.result.current[0]).toBe(true);

    const remounted = renderHook(() => useRailCollapsed());
    expect(remounted.result.current[0]).toBe(true);
  });

  it("toggles back", () => {
    const { result } = renderHook(() => useRailCollapsed());
    act(() => { result.current[1](); });
    act(() => { result.current[1](); });
    expect(result.current[0]).toBe(false);
    expect(window.localStorage.getItem(KEY)).toBe("false");
  });

  it("still toggles when storage is unavailable", () => {
    // A browser with site data blocked throws on access. Losing a layout
    // preference must not take the shell down with it.
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    const { result } = renderHook(() => useRailCollapsed());
    expect(result.current[0]).toBe(false);
    act(() => { result.current[1](); });
    expect(result.current[0]).toBe(true);
  });
});
