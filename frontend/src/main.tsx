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

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
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

void enableMocking().then(() => {
  createRoot(rootElement).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </StrictMode>,
  );
});
