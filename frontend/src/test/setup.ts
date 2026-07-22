import { afterEach, vi } from "vitest";
import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";


class ResizeObserverMock implements ResizeObserver {
  private readonly callback: ResizeObserverCallback;

  public readonly observe =
    vi.fn<ResizeObserver["observe"]>();

  public readonly unobserve =
    vi.fn<ResizeObserver["unobserve"]>();

  public readonly disconnect =
    vi.fn<ResizeObserver["disconnect"]>();

  public constructor(
    callback: ResizeObserverCallback,
  ) {
    this.callback = callback;
  }

  public trigger(
    entries: ResizeObserverEntry[] = [],
  ): void {
    this.callback(entries, this);
  }
}

function createMediaQueryList(
  query: string,
): MediaQueryList {
  const eventTarget = new EventTarget();

  return {
    matches: false,
    media: query,
    onchange: null,
    addEventListener:
      eventTarget.addEventListener.bind(eventTarget),
    removeEventListener:
      eventTarget.removeEventListener.bind(eventTarget),
    dispatchEvent:
      eventTarget.dispatchEvent.bind(eventTarget),
  } as MediaQueryList;
}


vi.stubGlobal(
  "ResizeObserver",
  ResizeObserverMock,
);

vi.stubGlobal(
  "matchMedia",
  vi.fn(createMediaQueryList),
);


afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
