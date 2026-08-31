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
  return worker.start({
    onUnhandledRequest(request, print) {
      if (request.url.includes("/data-console/v1/")) {
        print.error();
      }
    }
  });
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
