import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";

import { App } from "./App";
import "./env";
import "./index.css";


import { APIError } from "./api/client";
import { registerClarificationsSection } from "./domains/returns/panes/casePanel/sections/registerClarificationsSection";

/**
 * Reads recover from an outage; turns do not replay themselves.
 *
 * `refetchOnReconnect` is on and `refetchOnWindowFocus` stays off, and the
 * asymmetry is deliberate. The Copilot is a screen an associate leaves open on
 * a counter for an hour while they walk to the back of the shop; refetching
 * every time the window regains focus would re-read every case on the platform
 * all day for nothing. Losing the network is different -- it means the client
 * has definitely missed whatever happened in the gap, which for a return
 * mid-flight is exactly the RMA or the label it was waiting for.
 *
 * **Only reads refetch.** Mutations retry `false` here and are never refetched
 * by either trigger, so a browser reconnecting cannot re-issue an agent turn:
 * it would spend a model call and append a turn to the transcript that nobody
 * typed, and on a confirmation turn it would be a second attempt at a write.
 *
 * The retry stays bounded, and 4xx is not retried at all: a refusal the backend
 * has already explained does not become a different refusal on a second ask.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      retry: (failureCount, error) => {
        if (error instanceof APIError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 1;
      },
    },
    mutations: {
      retry: false,
    },
  },
});

const rootElement = document.getElementById("root");

if (rootElement === null) {
  throw new Error(
    'Application root element "#root" was not found.',
  );
}

async function enableMocking() {
  if (import.meta.env.MODE !== "mock" && import.meta.env.VITE_MOCK_MODE !== "true") {
    return;
  }
  const { worker } = await import("./mocks/browser");
  // RV CFG-1 F5: this used to print only for `/data-console/v1/` requests --
  // the legacy Data Console surface Wave F4 deleted along with the frontend
  // that called it. That request is never made any more, so the branch was
  // dead code with no live effect either way -- deleted rather than kept as
  // a check against a path nothing can hit.
  //
  // **Tried `onUnhandledRequest: "error"` here first, to turn the dead
  // branch into the loud check its own comment described.** It works for
  // this lease's own routes, but MSW's `"error"` does not merely warn: an
  // unhandled request is answered with a mocked 500 rather than bypassed,
  // which `tests/canonical-routes.spec.ts` (outside CFG-5's Owns) treats as
  // the route itself failing. Against the mock stack today that broke
  // `/support/work-queue` and `/support/rma-tickets` -- `GET /api/rma-tickets`
  // and `GET /api/v1/return-support/work-items` have no handler in
  // `supportHandlers.ts`, a real gap, but one outside `frontend/src/domains/
  // config/**` and this lease's mandate.
  //
  // RV round 1, F2: settled on `"bypass"` next, on the mistaken belief that
  // omitting the option entirely -- MSW's own default -- "leaves every
  // unhandled request to fall through to Vite's dev server unremarked".
  // MSW v2's actual default is `"warn"`
  // (`node_modules/msw/lib/core/utils/request/onUnhandledRequest.js`), and
  // `"warn"` both bypasses the request *and* reports it, through
  // `console.warn` rather than `console.error`. That is the option that
  // answers the original objection with nothing given up:
  // `tests/canonical-routes.spec.ts` only fails a route on `console.error`
  // or a 4xx/5xx response, so `"warn"` fails no route (`"error"`'s own
  // problem) while still surfacing exactly the kind of gap the `/support`
  // routes above turned out to have -- which `"bypass"`, by construction,
  // can never report at all.
  return worker.start({ onUnhandledRequest: "warn" });
}

/**
 * The panel sections each slice contributes, named here rather than imported
 * for their side effects.
 *
 * V1 ships the section registry and never touches it again (contracts.md §9);
 * V2 and V3 contribute from their own modules. Calling the registrations from
 * the composition root is what keeps that true without making the layout depend
 * on import order -- a bare `import "./…/ClarificationsSection"` would put a
 * section on the screen or not depending on which other module happened to pull
 * it in first, which is the failure `order` and the registry's duplicate-id
 * refusal exist to prevent.
 *
 * `StrictMode` double-invokes render, never module scope, so this runs once.
 */
registerClarificationsSection();

void enableMocking().then(() => {
  createRoot(rootElement).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  );
});
