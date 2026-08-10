import { Suspense } from "react";
import { Redirect, useLocation } from "wouter";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { LoadingState } from "./components/LoadingState";
import { RuntimeConfigProvider } from "./components/RuntimeConfigProvider";
import { ToastProvider } from "./components/ToastProvider";
import { DomainApp } from "./domains/DomainShell";
import { isDomainPath } from "./domains/registry";
import { CapabilityProvider } from "./hooks/CapabilityProvider";
import { normalizeBrowserPath } from "./versioning";

/**
 * Wave F4: four user routes, and nothing else.
 *
 * This file used to branch four ways -- the canonical domains, the `/v2/config`
 * datasource app, the Order Discovery Copilot at `/v2/copilot`, and a `/v1`
 * fallback that swallowed every other path into the legacy Data Console. All of
 * that is gone, along with the 76 legacy routes behind it.
 *
 * **What that deleted, stated plainly.** The four canonical domains replace the
 * return-domain screens. They do not replace the Data Console: the data
 * browser, graph explorer, inventory, workspaces, scenarios, jobs, imports and
 * exports, AI studio and the system tooling had no canonical equivalent and are
 * now absent rather than superseded. That was the owner's decision, and F4's
 * stated end state ("exactly four user routes") is what it means.
 *
 * **Anything unrecognised goes to `/returns`**, not to a 404. Every legacy
 * bookmark is now an unrecognised path, and the honest thing to do with someone
 * arriving from one is to put them on the platform's front door rather than a
 * dead end that says nothing about where the screens went.
 *
 * `DomainApp` is no longer lazy. It was split out when it was one of four
 * possible applications; it is now the only one, so the extra chunk bought a
 * round trip on first paint and nothing else.
 */
export function App() {
  const [location] = useLocation();
  const pathname = normalizeBrowserPath(location);

  return (
    <ErrorBoundary>
      <ToastProvider>
        <RuntimeConfigProvider>
          {isDomainPath(pathname) ? (
            <Suspense fallback={<LoadingState message="Loading platform..." />}>
              <CapabilityProvider>
                <DomainApp />
              </CapabilityProvider>
            </Suspense>
          ) : (
            <Redirect to="/returns" replace />
          )}
        </RuntimeConfigProvider>
      </ToastProvider>
    </ErrorBoundary>
  );
}
