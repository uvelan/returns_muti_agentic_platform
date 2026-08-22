/**
 * A turn must be abandonable.
 *
 * There was no `AbortController`, no `signal` and no timeout anywhere in
 * `orderAgent.ts` or `client.ts`. A turn held the connection until the server
 * answered, and the server tried its reasoning routes in series with a
 * per-attempt timeout -- so a run where every route timed out kept an associate
 * waiting roughly fourteen minutes on a screen that said only "Searching order
 * graph...", with no estimate and no way out.
 *
 * Aborting is a client-side withdrawal. The server keeps working on the turn it
 * accepted, which is why the control is worded "Stop waiting" rather than
 * "Cancel" -- claiming to have cancelled work this cannot reach would be the
 * same fabrication as a spinner describing a search that never started.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { orderAgentApi } from "./orderAgent";

/** A valid envelope. `client.ts` rejects anything without a complete `meta`. */
function envelope(data: unknown): Response {
  return new Response(
    JSON.stringify({
      data,
      meta: {
        schema_version: "1",
        request_id: "req-1",
        generated_at: "2026-08-22T00:00:00Z",
        freshness: "LIVE",
        partial: false,
        warnings: [],
      },
    }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("sending a turn", () => {
  it("passes the caller's signal down to fetch", async () => {
    // The whole defect was that nothing reached `fetch`. Asserting the wiring
    // rather than the abort behaviour, because a signal that never arrives is
    // exactly what a control that appears to work but does nothing looks like.
    const controller = new AbortController();
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(envelope({ conversation_version: 1 }));

    await orderAgentApi.sendTurn({
      conversationId: "conv-1",
      expectedConversationVersion: 1,
      message: "Order CA273603",
      agentId: "agent-1",
      signal: controller.signal,
    });

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const init = fetchSpy.mock.calls[0]?.[1];
    expect(init?.signal).toBe(controller.signal);
  });

  it("rejects once the caller aborts", async () => {
    const controller = new AbortController();
    vi.spyOn(globalThis, "fetch").mockImplementation(
      (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => {
            reject(
              new DOMException("The operation was aborted.", "AbortError"),
            );
          });
        }),
    );

    const inFlight = orderAgentApi.sendTurn({
      conversationId: "conv-1",
      expectedConversationVersion: 1,
      message: "Order CA273603",
      agentId: "agent-1",
      signal: controller.signal,
    });
    controller.abort();

    await expect(inFlight).rejects.toThrow();
  });

  it("still works when no signal is given", async () => {
    // Every other caller passes nothing, and none of them moved.
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(envelope({ conversation_version: 2 }));

    await orderAgentApi.sendTurn({
      conversationId: "conv-1",
      expectedConversationVersion: 1,
      message: "Order CA273603",
      agentId: "agent-1",
    });

    const init = fetchSpy.mock.calls[0]?.[1];
    expect(init?.signal).toBeUndefined();
  });
});
