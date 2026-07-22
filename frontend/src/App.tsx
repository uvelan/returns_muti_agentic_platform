import { Link, Route, Switch } from "wouter";

import { ErrorBoundary } from "./components/ErrorBoundary";
import { Shell } from "./components/Shell";
import { OverviewPage } from "./features/data-console/pages/OverviewPage";


function NotFoundPage() {
  return (
    <section
      className="
        flex min-h-[60vh] flex-col items-center justify-center
        gap-4 text-center
      "
      aria-labelledby="not-found-heading"
    >
      <p className="text-sm font-medium uppercase tracking-wider text-slate-500">
        Page not found
      </p>

      <h1
        id="not-found-heading"
        className="text-4xl font-semibold text-slate-900"
      >
        404
      </h1>

      <p className="max-w-md text-sm text-slate-600">
        The requested console module could not be found.
      </p>

      <Link
        href="/"
        className="
          rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white
          transition hover:bg-slate-700
          focus-visible:outline-none focus-visible:ring-2
          focus-visible:ring-slate-500 focus-visible:ring-offset-2
        "
      >
        Return to overview
      </Link>
    </section>
  );
}


export function App() {
  return (
    <ErrorBoundary>
      <Shell>
        <Switch>
          <Route
            path="/"
            component={OverviewPage}
          />

          <Route
            path="/overview"
            component={OverviewPage}
          />

          <Route>
            <NotFoundPage />
          </Route>
        </Switch>
      </Shell>
    </ErrorBoundary>
  );
}