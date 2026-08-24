import { Link, Redirect, Route, Router, Switch, useLocation } from "wouter";
import { Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ChevronRight,
  Command,
  Menu,
  PanelLeftClose,
  PanelLeftOpen,
  ShieldCheck,
  X,
} from "lucide-react";

import { useCapabilities } from "../hooks/capabilityContext";
import { useRuntimeConfig } from "../hooks/useRuntimeConfig";
import {
  DOMAINS,
  domainForPath,
  LANDING_PATH,
  ROOT_PATH,
  ROOT_REDIRECT_PATH,
  type DomainDefinition,
} from "./registry";
import { DomainLanding } from "./DomainLanding";
import { DOMAIN_SCREENS } from "./domainScreens";
import { RailSlotProvider } from "./railSlot";
import { PlatformLanding } from "./PlatformLanding";
import { useRailCollapsed } from "./useRailCollapsed";
import { useDomainSection } from "./useDomainSection";
import { SIGN_IN_TITLE, useDocumentTitle, useRouteDocumentTitle } from "./useDocumentTitle";


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
        // Desktop only, for two reasons. Collapsing a *drawer* to an icon strip
        // is meaningless -- the drawer is already off screen when you are not
        // using it. And below `lg` this button and the drawer trigger would both
        // claim `aria-controls="domain-rail"` with opposite `aria-expanded`
        // meanings, leaving a screen reader with two contradictory answers about
        // one region.
        className="hidden rounded p-2 text-rail-on-surface/60 transition hover:bg-white/10 hover:text-rail-on-surface focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-inverse-primary lg:block"
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
  // Rendered above `Frame`, so the route title never reaches it. Someone who
  // lands here with several tabs open should be able to tell which one is
  // asking them to sign in.
  useDocumentTitle(SIGN_IN_TITLE);
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
      <main id={MAIN_CONTENT_ID} tabIndex={-1} className="px-10 py-10 outline-none">
        {children}
      </main>
    </div>
  );
}

/**
 * Below `lg` the rail is a drawer; at `lg` and above it is the rail it has
 * always been and this state is inert.
 *
 * The shell used to pin `min-width: 1280px` to `html` and `body`, so every
 * width under 1280 scrolled sideways -- the exact two-dimensional scrolling
 * WCAG 1.4.10 exists to prevent, and at 320 the 288px rail left 32px for the
 * screen itself. Removing the floor is what makes a drawer necessary: without
 * one the rail simply eats the viewport.
 */
function useNavigationDrawer(): {
  open: boolean;
  setOpen: (open: boolean) => void;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
} {
  const [location] = useLocation();
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  // The route the drawer was opened on is stored with it, so "you have since
  // navigated" is derived rather than corrected by an effect. An effect that
  // closed it on `location` change worked, but it rendered the drawer open over
  // the new route for one frame first -- and the state it wrote was a function
  // of a value already in scope, which is the definition of derivable.
  const [opened, setOpened] = useState<string | null>(null);
  const open = opened === location;

  function setOpen(next: boolean) {
    setOpened(next ? location : null);
  }

  // Escape closes it, and focus goes back to the control that opened it --
  // otherwise focus is left on a now-hidden element and the next Tab starts
  // over at the top of the document.
  useEffect(() => {
    if (!open) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      // `setOpened`, not the `setOpen` wrapper: the wrapper is redeclared each
      // render, so depending on it would re-bind this listener every time.
      setOpened(null);
      triggerRef.current?.focus();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return { open, setOpen, triggerRef };
}

/** Opens the rail below `lg`. Absent above it, where the rail is always there. */
function DrawerTrigger({
  open,
  onOpen,
  triggerRef,
}: {
  open: boolean;
  onOpen: () => void;
  triggerRef: React.RefObject<HTMLButtonElement | null>;
}) {
  return (
    <button
      ref={triggerRef}
      type="button"
      onClick={onOpen}
      aria-expanded={open}
      aria-controls="domain-rail"
      className="flex size-10 shrink-0 items-center justify-center rounded-lg border border-outline-control text-on-surface-variant transition hover:bg-surface-container focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 lg:hidden"
    >
      <Menu size={18} aria-hidden="true" />
      <span className="sr-only">Open navigation</span>
    </button>
  );
}

function DomainFrame({ domain, children }: { domain: DomainDefinition; children: ReactNode }) {
  const { principal } = useCapabilities();
  const runtimeConfig = useRuntimeConfig();
  const activeSectionLabel = useDomainSection(domain);
  const [collapsed, toggle] = useRailCollapsed();
  // Captured through state rather than a ref so the portal re-renders once the
  // element exists: a ref set during commit would leave the first paint empty
  // and never invalidate.
  const [railSlot, setRailSlot] = useState<HTMLElement | null>(null);
  const drawer = useNavigationDrawer();
  // The desktop collapse preference must not blank the drawer: a collapsed rail
  // hides the section links, which are the only reason to open a drawer.
  const railCollapsed = collapsed && !drawer.open;
  const Icon = domain.icon;

  return (
    <div className="flex min-h-screen bg-surface">
      {/*
        Closes the drawer by clicking away from it. `aria-hidden` because the
        Escape key and the close button are the accessible affordances; a
        screen-reader user should not meet an unlabelled full-screen div.
      */}
      {drawer.open ? (
        <div
          aria-hidden="true"
          onClick={() => { drawer.setOpen(false); }}
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
        />
      ) : null}
      <aside
        id="domain-rail"
        // `invisible` and not merely translated: an off-canvas element that is
        // still visible stays in the tab order, so Tab would walk nineteen
        // links the reader cannot see. `lg:visible` puts it back on desktop,
        // where it is not a drawer at all.
        className={`fixed inset-y-0 left-0 z-40 flex h-screen shrink-0 flex-col overflow-y-auto border-r border-white/10 bg-rail-surface shadow-2xl shadow-black/10 transition-[width,transform] duration-200 lg:sticky lg:top-0 lg:visible lg:translate-x-0 ${
          drawer.open ? "w-72 translate-x-0" : `invisible -translate-x-full ${collapsed ? "lg:w-16" : "lg:w-72"}`
        }`}
      >
        <button
          type="button"
          onClick={() => {
            drawer.setOpen(false);
            drawer.triggerRef.current?.focus();
          }}
          className="absolute right-3 top-3 flex size-9 items-center justify-center rounded-lg text-rail-on-surface/70 transition hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inverse-primary lg:hidden"
        >
          <X size={18} aria-hidden="true" />
          <span className="sr-only">Close navigation</span>
        </button>
        <RailHeader collapsed={railCollapsed} onToggle={toggle} />
        <div
          className={`flex items-start gap-3 border-y border-white/10 py-5 ${railCollapsed ? "justify-center px-0" : "px-4"}`}
        >
          <span
            className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-white/10 text-inverse-primary ring-1 ring-inset ring-white/10"
            title={railCollapsed ? domain.name : undefined}
          >
            <Icon size={19} />
          </span>
          {railCollapsed ? null : (
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
        {railCollapsed ? null : <DomainSectionNav domain={domain} />}
        {/*
          The contextual slot. Filled by the screen through `DomainRail`, which
          is why it carries no fallback: a rail block describing what is on
          screen can only come from the screen, and a shell-authored default
          would be the shared navigation panel this replaced.
        */}
        {railCollapsed ? null : <div ref={setRailSlot} className="flex flex-col" />}
        {railCollapsed ? null : (
          <div className="mt-auto border-t border-white/10 px-4 py-4">
            <div className="flex items-center gap-2 text-[11px] leading-relaxed text-rail-on-surface/50">
              <ShieldCheck size={14} className="shrink-0 text-inverse-primary/70" />
              <span>Actions remain capability-gated and audited.</span>
            </div>
          </div>
        )}
      </aside>
      <div className="min-w-0 flex-1 flex flex-col h-screen overflow-hidden">
        {domain.path === "/returns" ? (
          // The copilot renders no header -- it needs the height -- so below
          // `lg` it gets the one control it cannot do without. Absent entirely
          // on desktop, where the rail is always on screen.
          // Visible at every width, not just below `lg`. It began as a place to
          // hang the drawer trigger, but the copilot is also one of the nine
          // routes that never said what it was, and it has no header to put
          // that in. Thirty-six pixels is what page identity costs here.
          <div className="flex shrink-0 items-center gap-3 border-b border-outline-variant/70 bg-surface/95 px-3 py-2">
            <DrawerTrigger
              open={drawer.open}
              onOpen={() => { drawer.setOpen(true); }}
              triggerRef={drawer.triggerRef}
            />
            <h1 className="truncate text-sm font-semibold text-on-surface">{domain.name}</h1>
          </div>
        ) : (
          <header className="sticky top-0 z-20 flex h-[4.5rem] shrink-0 items-center justify-between gap-3 border-b border-outline-variant/70 bg-surface/95 px-4 backdrop-blur sm:gap-6 sm:px-7">
            <DrawerTrigger
              open={drawer.open}
              onOpen={() => { drawer.setOpen(true); }}
              triggerRef={drawer.triggerRef}
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5 text-xs font-medium text-outline">
                {/* The platform name is the one crumb a 320px reader can spare. */}
                <span className="hidden sm:inline">Returns Intelligence Platform</span>
                <ChevronRight size={13} aria-hidden="true" className="hidden sm:inline" />
                {/*
                  The page's identity, and the shell's job rather than each
                  screen's. Nine canonical routes rendered no `<h1>` at all and
                  two more rendered one that disagreed with the name the rail
                  used for them -- both because identity was left to eight
                  separate screens to remember. Here it is derived from the
                  registry, so it cannot drift from the navigation and a new
                  domain gets it for free.

                  Small type, but a heading is a structural claim rather than a
                  visual one: the breadcrumb is genuinely where this page says
                  what it is.
                */}
                <h1 className="truncate text-xs font-medium text-on-surface-variant">
                  {domain.name}
                </h1>
                {activeSectionLabel === "" ? null : (
                  <>
                    <ChevronRight size={13} aria-hidden="true" />
                    <span className="truncate text-primary">{activeSectionLabel}</span>
                  </>
                )}
              </div>
              <p className="mt-1 hidden truncate text-sm text-on-surface-variant sm:block">
                {domain.description}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-3">
              <span className="hidden items-center gap-1.5 rounded-lg border border-outline-variant bg-surface-container-lowest px-2.5 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-on-surface-variant xl:flex" title={runtimeConfig?.releaseId}>
                <Command size={13} aria-hidden="true" />
                {runtimeConfig?.environment ?? "Environment unknown"}
              </span>
              {principal === undefined ? null : (
                <span className="hidden max-w-64 truncate rounded-full bg-secondary-container px-3 py-1.5 text-xs font-medium text-on-secondary-container sm:block" title={principal.subject}>
                  {principal.subject}
                </span>
              )}
            </div>
          </header>
        )}
        <main
          id={MAIN_CONTENT_ID}
          tabIndex={-1}
          className={`min-w-0 flex-1 outline-none ${domain.path === "/returns" ? "p-3 h-full overflow-hidden" : "overflow-x-auto p-4 sm:p-7"}`}
        >
          <RailSlotProvider value={collapsed ? null : railSlot}>{children}</RailSlotProvider>
        </main>
      </div>
    </div>
  );
}

/**
 * Where `Skip to main content` lands. Both frames use it, because a keyboard
 * user on the launcher has the same reason to skip the chrome as one inside a
 * domain, and a link pointing at an id that only half the routes render is
 * worse than no link.
 */
const MAIN_CONTENT_ID = "main-content";

/**
 * The first thing Tab reaches, on every route.
 *
 * A domain frame puts a rail and a header ahead of the content -- on `/config`
 * that is nineteen links before the first thing the page is about, and they are
 * the same nineteen on every route. Off-screen until focused, so it costs
 * pointer users nothing.
 *
 * `tabIndex={-1}` on the target is what makes the jump actually move focus:
 * without it the browser scrolls to the element and leaves focus on the link,
 * so the next Tab returns to the second rail item.
 */
function SkipToContent() {
  return (
    <a
      href={`#${MAIN_CONTENT_ID}`}
      className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2.5 focus:text-sm focus:font-semibold focus:text-on-primary focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-primary focus:ring-offset-2"
    >
      Skip to main content
    </a>
  );
}

/** Chooses the frame from the URL, so the launcher and a domain differ in chrome. */
function Frame({ children }: { children: ReactNode }) {
  const [location] = useLocation();
  const domain = domainForPath(location);
  // Every route passes through here, including the launcher and the forbidden
  // screen, so this is the one place a title cannot be forgotten.
  useRouteDocumentTitle(domain);
  return (
    <>
      <SkipToContent />
      {domain === null ? (
        <LandingFrame>{children}</LandingFrame>
      ) : (
        <DomainFrame domain={domain}>{children}</DomainFrame>
      )}
    </>
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
          <Route path={ROOT_PATH}>
            {/* The root opens Returns. Redirected rather than rendered so the
                rail highlight and the section tabs, which both read the
                pathname, agree with what is on screen. */}
            <Redirect to={ROOT_REDIRECT_PATH} replace />
          </Route>
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
