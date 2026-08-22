/**
 * The two ways an elapsed counter goes wrong, held shut once for every screen
 * that shows one.
 */

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useElapsedSeconds } from "./useElapsedSeconds";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useElapsedSeconds", () => {
  it("counts while the operation is running", () => {
    const { result } = renderHook(() => useElapsedSeconds(true));
    expect(result.current).toBe(0);

    act(() => {
      vi.advanceTimersByTime(3_000);
    });
    expect(result.current).toBe(3);
  });

  it("reads zero when nothing is running", () => {
    const { result } = renderHook(() => useElapsedSeconds(false));
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(result.current).toBe(0);
  });

  it("restarts for the next operation rather than carrying the last one forward", () => {
    // The question is "how long has this one taken", not "how long since the
    // screen loaded". Without the reset the second send opens at the first
    // send's duration and looks stuck.
    const { result, rerender } = renderHook(({ active }) => useElapsedSeconds(active), {
      initialProps: { active: true },
    });
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    expect(result.current).toBe(4);

    rerender({ active: false });
    expect(result.current).toBe(0);

    rerender({ active: true });
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(result.current).toBe(1);
  });

  it("stops counting when the operation ends", () => {
    const { result, rerender } = renderHook(({ active }) => useElapsedSeconds(active), {
      initialProps: { active: true },
    });
    act(() => {
      vi.advanceTimersByTime(2_000);
    });
    rerender({ active: false });

    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(result.current).toBe(0);
  });
});
