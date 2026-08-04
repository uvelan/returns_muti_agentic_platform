import { Link, Redirect, Route, Router, Switch, useLocation } from "wouter";
import { lazy, Suspense } from "react";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { Shell } from "./components/Shell";
import { ToastProvider } from "./components/ToastProvider";
import { RuntimeConfigProvider } from "./components/RuntimeConfigProvider";
import { LoadingState } from "./components/LoadingState";
import { routes } from "./routes";
import {
  COPILOT_V2_PATH,
  legacyRouteDestination,
  normalizeBrowserPath,
  VERSION_ONE_PREFIX,
} from "./versioning";

const CopilotV2Page = lazy(() => import("./features/copilot-v2/CopilotV2Page").then(
  (module) => ({ default: module.CopilotV2Page }),
));
const DataSourceConfigApp = lazy(() => import("./features/data-source-config/DataSourceConfigApp").then(
  (module) => ({ default: module.DataSourceConfigApp }),
));

function NotFoundPage() {
  return (
    <section
      className="flex min-h-[60vh] flex-col items-center justify-center gap-4 text-center"
      aria-labelledby="not-found-heading"
    >
      <p className="text-sm font-medium uppercase tracking-wider text-slate-500">Page not found</p>
      <h1 id="not-found-heading" className="text-4xl font-semibold text-slate-900">404</h1>
      <p className="max-w-md text-sm text-slate-600">The requested console module could not be found.</p>
      <Link
        href="/overview"
        className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white transition hover:bg-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2"
      >
        Return to overview
      </Link>
    </section>
  );
}

function VersionOneApp() {
  return (
    <Router base="/v1">
      <Shell>
        <Suspense fallback={<LoadingState message="Loading module..." />}>
          <Switch>
            {routes.map((route) => (
              <Route key={route.path} path={route.path} component={route.component} />
            ))}
            <Route path="/" component={routes.find(r => r.path === "/associate/returns")?.component} />
            <Route>
              <NotFoundPage />
            </Route>
          </Switch>
        </Suspense>
      </Shell>
    </Router>
  );
}

export function App() {
  const [location] = useLocation();
  const pathname = normalizeBrowserPath(location);
  const legacyDestination = legacyRouteDestination(
    pathname,
    window.location.search,
    window.location.hash,
  );

  return (
    <ErrorBoundary>
      <ToastProvider>
        <RuntimeConfigProvider>
          {pathname.startsWith("/v2/config") ? (
            <Suspense fallback={<LoadingState message="Loading datasource configuration..." />}>
              <DataSourceConfigApp />
            </Suspense>
          ) : pathname === COPILOT_V2_PATH ? (
            <Suspense fallback={<LoadingState message="Loading Order Discovery Copilot..." />}>
              <CopilotV2Page />
            </Suspense>
          ) : pathname === VERSION_ONE_PREFIX || pathname.startsWith(`${VERSION_ONE_PREFIX}/`) ? (
            <VersionOneApp />
          ) : (
            <Redirect to={legacyDestination} replace />
          )}
        </RuntimeConfigProvider>
      </ToastProvider>
    </ErrorBoundary>
  );
}
