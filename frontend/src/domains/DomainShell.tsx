import { Link, Route, Router, Switch, useLocation } from "wouter";
import { Suspense, useState, type ReactNode } from "react";
import { ArrowLeft, PanelLeftClose, PanelLeftOpen } from "lucide-react";

import { useCapabilities } from "../hooks/capabilityContext";
import { DOMAINS, domainForPath, LANDING_PATH, type DomainDefinition } from "./registry";
import { DomainLanding } from "./DomainLanding";
import { DOMAIN_SCREENS } from "./domainScreens";
import { RailSlotProvider } from "./railSlot";
import { PlatformLanding } from "./PlatformLanding";
import { useRailCollapsed } from "./useRailCollapsed";


/**
 * The platform shell.
 *
 * **The sidebar belongs to the domain you are in, not to the platform.** It
 * used to list all five domains on every screen, which cost a fixed 64 rows of
 * chrome to answer a question -- "where else could I go?" -- that an operator
 * asks a few times a day, while the navigation they use constantly (the
 * domain's own sections) was squeezed into a horizontal tab strip that wrapped
 * at nine entries. The rail now shows one domain's sections and a single way
 * back to the launcher, which is where cross-domain navigation lives.
 *
 * RBAC hides unauthorized sections. That is presentation: the backend refuses
 * the request regardless, and this shell never decides anything on its own.
 */

function RailHeader({ collapsed, onToggle }: { collapsed: boolean; onToggle: () => void }) {
  return (
    <div className={`flex items-center ${collapsed ? "flex-col gap-1 py-2" : "justify-between pr-2"}`}>
      <Link
        href={LANDING_PATH}
        title="All domains"
        aria-label="All domains"
        className={`flex items-center gap-2 text-sm font-medium text-rail-on-surface/70 transition hover:text-rail-on-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-inverse-primary ${collapsed ? "p-2" : "px-4 py-3"}`}
      >
        <ArrowLeft size={16} />
        {collapsed ? null : "All domains"}
      </Link>
      <button
        type="button"
        onClick={onToggle}
        // `aria-expanded` on the control, describing the region it governs --
        // a screen reader user needs to know the rail collapsed, not just that
        // a button was pressed.
        aria-expanded={!collapsed}
        aria-controls="domain-rail"
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="rounded p-2 text-rail-on-surface/60 transition hover:bg-white/10 hover:text-rail-on-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-inverse-primary"
      >
        {collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />}
      </button>
    </div>
  );
}

function DomainSectionNav({ domain }: { domain: DomainDefinition }) {
  const [location] = useLocation();

  if (domain.sections.length === 0) {
    return null;
  }

  // The domain root and its first section are the same screen, so the first
  // entry has to look active at both URLs or landing on the domain shows a
  // rail with nothing selected.
  const rest = location.startsWith(`${domain.path}/`)
    ? location.slice(domain.path.length + 1).split("/")[0]
    : "";
  const activeSlug =
    domain.sections.find((section) => section.slug === rest)?.slug ?? domain.sections[0].slug;

  return (
    <nav aria-label={`${domain.name} sections`} className="flex flex-col gap-0.5 p-3">
      {domain.sections.map((section) => (
        <Link
          key={section.slug}
          href={`${domain.path}/${section.slug}`}
          aria-current={section.slug === activeSlug ? "page" : undefined}
          className={[
            "rounded-md px-3 py-2 text-sm font-medium transition",
            section.slug === activeSlug
              ? "bg-primary text-on-primary"
              : "text-rail-on-surface/70 hover:bg-white/10 hover:text-rail-on-surface",
          ].join(" ")}
        >
          {section.label}
        </Link>
      ))}
    </nav>
  );
}

function NoDomainsAvailable() {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-on-surface">No domains available</h1>
      <p className="max-w-md text-sm text-on-surface-variant">
        Your account is signed in but has not been granted access to any platform domain.
        Ask an administrator to review your roles.
      </p>
    </section>
  );
}

function SignInRequired() {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-on-surface">Sign in required</h1>
      <p className="max-w-md text-sm text-on-surface-variant">
        The platform could not identify you. Sign in and try again.
      </p>
    </section>
  );
}

function Forbidden({ domain }: { domain: DomainDefinition }) {
  return (
    <section className="flex min-h-[60vh] flex-col items-center justify-center gap-3 text-center">
      <h1 className="text-2xl font-semibold text-on-surface">Not available</h1>
      <p className="max-w-md text-sm text-on-surface-variant">
        You do not have access to {domain.name}.
      </p>
    </section>
  );
}

/**
 * The launcher has no rail. There is nothing for one to contain -- the cards
 * are the navigation -- and a rail beside them would repeat itself.
 */
function LandingFrame({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen bg-surface">
      <main className="px-6 py-10">{children}</main>
    </div>
  );
}

function DomainFrame({ domain, children }: { domain: DomainDefinition; children: ReactNode }) {
  const { principal } = useCapabilities();
  const [collapsed, toggle] = useRailCollapsed();
  // Captured through state rather than a ref so the portal re-renders once the
  // element exists: a ref set during commit would leave the first paint empty
  // and never invalidate.
  const [railSlot, setRailSlot] = useState<HTMLElement | null>(null);
  const Icon = domain.icon;

  return (
    <div className="flex min-h-screen bg-surface">
      <aside
        id="domain-rail"
        className={`flex shrink-0 flex-col overflow-y-auto bg-rail-surface transition-[width] duration-150 ${collapsed ? "w-14" : "w-64"}`}
      >
        <RailHeader collapsed={collapsed} onToggle={toggle} />
        <div
          className={`flex items-start gap-3 border-y border-white/10 py-4 ${collapsed ? "justify-center px-0" : "px-4"}`}
        >
          <span className="mt-0.5 text-inverse-primary" title={collapsed ? domain.name : undefined}>
            <Icon size={20} />
          </span>
          {collapsed ? null : (
            <span className="min-w-0">
              <span className="block text-sm font-semibold leading-tight text-rail-on-surface">
                {domain.name}
              </span>
              {principal ? (
                <span
                  className="mt-1 block truncate text-xs text-rail-on-surface/60"
                  title={principal.subject}
                >
                  {principal.subject}
                </span>
              ) : null}
            </span>
          )}
        </div>
        {/*
          Sections are hidden when collapsed rather than reduced to icons: they
          have no icons, and inventing one per section would be six ambiguous
          glyphs where there are currently six unambiguous words. Collapsing is
          for reclaiming width on the copilot, which has no sections at all.
        */}
        {collapsed ? null : <DomainSectionNav domain={domain} />}
        {/*
          The contextual slot. Filled by the screen through `DomainRail`, which
          is why it carries no fallback: a rail block describing what is on
          screen can only come from the screen, and a shell-authored default
          would be the shared navigation panel this replaced.
        */}
        {collapsed ? null : <div ref={setRailSlot} className="flex flex-col" />}
      </aside>
      <main className="flex-1 overflow-x-auto p-6">
        <RailSlotProvider value={collapsed ? null : railSlot}>{children}</RailSlotProvider>
      </main>
    </div>
  );
}

/** Chooses the frame from the URL, so the launcher and a domain differ in chrome. */
function Frame({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const domain = domainForPath(location);
  return domain === null ? (
    <LandingFrame>{children}</LandingFrame>
  ) : (
    <DomainFrame domain={domain}>{children}</DomainFrame>
  );
}

export function DomainApp() {
  const { can, isLoading, isUnauthenticated } = useCapabilities();

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-on-surface-variant">
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
      <Frame>
        <Switch>
          <Route path={LANDING_PATH}>
            <PlatformLanding />
          </Route>
          {DOMAINS.flatMap((domain) => {
            const Screen = DOMAIN_SCREENS[domain.path];
            const body = !can(domain.requires) ? (
              <Forbidden domain={domain} />
            ) : Screen ? (
              <Suspense fallback={<p className="text-sm text-on-surface-variant">Loading...</p>}>
                <Screen />
              </Suspense>
            ) : (
              <DomainLanding domain={domain} />
            );
            // Two patterns, not one. `/config/:rest*` does not match a bare
            // `/config` in wouter, and with only the wildcard registered the
            // rail correctly showed Configuration while the body fell through
            // to the launcher -- a split screen that looked like a routing
            // race. The screen reads its section from the path via
            // `useDomainSection`, so both patterns render the same thing.
            return [
              <Route key={domain.path} path={domain.path}>
                {body}
              </Route>,
              <Route key={`${domain.path}/*`} path={`${domain.path}/:rest*`}>
                {body}
              </Route>,
            ];
          })}
          <Route>{visible.length === 0 ? <NoDomainsAvailable /> : <PlatformLanding />}</Route>
        </Switch>
      </Frame>
    </Router>
  );
}
