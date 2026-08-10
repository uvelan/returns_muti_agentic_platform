import { Link, Route, Router, Switch, useLocation } from "wouter";
import { lazy, Suspense, type ComponentType, type ReactNode } from "react";

import { useCapabilities } from "../hooks/capabilityContext";
import { DOMAINS, type DomainDefinition } from "./registry";
import { DomainLanding } from "./DomainLanding";

/**
 * Domains whose screen is built. The rest fall back to `DomainLanding` until
 * their phase lands, so adding a screen is one entry here rather than an edit
 * to the routing below.
 */
const DOMAIN_SCREENS: Partial<Record<string, ComponentType>> = {
  "/graph-schema": lazy(() =>
    import("./graph-schema/GraphSchemaPage").then((m) => ({ default: m.GraphSchemaPage })),
  ),
  "/ai": lazy(() =>
    import("./ai/AiControlCenterPage").then((m) => ({ default: m.AiControlCenterPage })),
  ),
  "/returns": lazy(() =>
    import("./returns/ReturnCopilotPage").then((m) => ({ default: m.ReturnCopilotPage })),
  ),
  "/config": lazy(() =>
    import("./config/ConfigurationPage").then((m) => ({ default: m.ConfigurationPage })),
  ),
};

/**
 * The unified four-domain shell (Phase 17).
 *
 * RBAC hides unauthorized sections. That is presentation: the backend refuses
 * the request regardless, and this shell never decides anything on its own.
 */

function DomainNav() {
  const { can } = useCapabilities();
  const [location] = useLocation();

  const visible = DOMAINS.filter((domain) => can(domain.requires));

  return (
    <nav aria-label="Domains" className="flex flex-col gap-1 p-3">
      {visible.map((domain) => {
        const Icon = domain.icon;
        const active = location === domain.path || location.startsWith(`${domain.path}/`);
        return (
          <Link
            key={domain.path}
            href={domain.path}
            aria-current={active ? "page" : undefined}
            className={[
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition",
              active
                ? "bg-slate-900 text-white"
                : "text-slate-700 hover:bg-slate-100 hover:text-slate-900",
            ].join(" ")}
          >
            <Icon size={18} />
            {domain.name}
          </Link>
        );
      })}
    </nav>
  );
}

function NoDomainsAvailable() {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-slate-900">No domains available</h1>
      <p className="max-w-md text-sm text-slate-600">
        Your account is signed in but has not been granted access to any platform domain.
        Ask an administrator to review your roles.
      </p>
    </section>
  );
}

function SignInRequired() {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-slate-900">Sign in required</h1>
      <p className="max-w-md text-sm text-slate-600">
        The platform could not identify you. Sign in and try again.
      </p>
    </section>
  );
}

function Forbidden({ domain }: { domain: DomainDefinition }) {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-slate-900">Not available</h1>
      <p className="max-w-md text-sm text-slate-600">
        You do not have access to {domain.name}.
      </p>
    </section>
  );
}

function DomainFrame({ children }: { children: ReactNode }) {
  const { principal } = useCapabilities();

  return (
    <div className="flex min-h-screen bg-slate-50">
      <aside className="w-64 shrink-0 border-r border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-4">
          <p className="text-sm font-semibold text-slate-900">Returns Platform</p>
          {principal ? (
            <p className="mt-1 truncate text-xs text-slate-500" title={principal.subject}>
              {principal.subject}
            </p>
          ) : null}
        </div>
        <DomainNav />
      </aside>
      <main className="flex-1 overflow-x-auto p-6">{children}</main>
    </div>
  );
}

export function DomainApp() {
  const { can, isLoading, isUnauthenticated } = useCapabilities();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-600">
        Loading your access...
      </div>
    );
  }

  if (isUnauthenticated) {
    return <SignInRequired />;
  }

  const visible = DOMAINS.filter((domain) => can(domain.requires));

  return (
    <Router>
      <DomainFrame>
        <Switch>
          {DOMAINS.map((domain) => {
            const Screen = DOMAIN_SCREENS[domain.path];
            return (
              <Route key={domain.path} path={domain.path}>
                {!can(domain.requires) ? (
                  <Forbidden domain={domain} />
                ) : Screen ? (
                  <Suspense fallback={<p className="text-sm text-slate-600">Loading...</p>}>
                    <Screen />
                  </Suspense>
                ) : (
                  <DomainLanding domain={domain} />
                )}
              </Route>
            );
          })}
          <Route>
            {visible.length === 0 ? <NoDomainsAvailable /> : <DomainLanding domain={visible[0]} />}
          </Route>
        </Switch>
      </DomainFrame>
    </Router>
  );
}
