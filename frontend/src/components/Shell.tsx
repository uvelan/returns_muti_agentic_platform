import { useState, type ReactNode } from "react";
import {
  Activity,
  ChevronLeft,
  ChevronRight,
  Menu,
  X,
} from "lucide-react";
import { Link, useLocation } from "wouter";

import { routes } from "../routes";

const DESKTOP_SIDEBAR_STORAGE_KEY =
  "return-platform.desktop-sidebar-collapsed";

type ShellProps = {
  readonly children: ReactNode;
};

function readDesktopSidebarPreference(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    return (
      window.localStorage.getItem(
        DESKTOP_SIDEBAR_STORAGE_KEY,
      ) === "true"
    );
  } catch {
    return false;
  }
}

function persistDesktopSidebarPreference(collapsed: boolean): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    window.localStorage.setItem(
      DESKTOP_SIDEBAR_STORAGE_KEY,
      String(collapsed),
    );
  } catch {
    // Storage can be unavailable in privacy-restricted browser contexts.
  }
}

export function Shell({ children }: ShellProps) {
  const [location] = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [desktopSidebarCollapsed, setDesktopSidebarCollapsed] =
    useState(readDesktopSidebarPreference);

  const isMockMode =
    import.meta.env.MODE === "mock" ||
    import.meta.env.VITE_MOCK_MODE === "true";

  const navigation = routes.filter((route) => route.navigable);
  const groups = [
    "Associate",
    "Customer",
    "Support",
    "AI Gateway",
    "Explore",
    "Data Operations",
    "Sandbox & AI",
    "Governance",
    "System",
  ] as const;

  const toggleDesktopSidebar = () => {
    setDesktopSidebarCollapsed((current) => {
      const next = !current;
      persistDesktopSidebarPreference(next);
      return next;
    });
  };

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 md:flex-row">
      <a
        href="#main-content"
        className="sr-only z-50 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2"
      >
        Skip to main content
      </a>

      {/* Mobile header */}
      <header className="sticky top-0 z-40 flex items-center justify-between border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur md:hidden">
        <Link href="/" className="flex items-center gap-2">
          <span
            className="flex size-8 items-center justify-center rounded-lg bg-slate-900 text-white shadow-sm"
            aria-hidden="true"
          >
            <Activity size={16} />
          </span>
          <span className="font-semibold tracking-tight text-slate-900">
            Data Console
          </span>
        </Link>

        <button
          type="button"
          className="rounded-md p-1 text-slate-500 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-slate-500"
          onClick={() => {
            setMobileMenuOpen(!mobileMenuOpen);
          }}
          aria-expanded={mobileMenuOpen}
        >
          <span className="sr-only">
            {mobileMenuOpen ? "Close menu" : "Open menu"}
          </span>
          {mobileMenuOpen ? (
            <X size={24} aria-hidden="true" />
          ) : (
            <Menu size={24} aria-hidden="true" />
          )}
        </button>
      </header>

      {/* Mobile navigation menu */}
      {mobileMenuOpen && (
        <div
          className="fixed inset-0 z-30 bg-slate-900/50 backdrop-blur-sm md:hidden"
          onClick={() => {
            setMobileMenuOpen(false);
          }}
        >
          <div
            className="fixed inset-y-0 left-0 flex w-64 flex-col bg-white shadow-xl"
            onClick={(event) => {
              event.stopPropagation();
            }}
          >
            <nav
              aria-label="Mobile Navigation"
              className="mt-14 flex-1 space-y-1 overflow-y-auto px-4 py-6"
            >
              {groups.map((group) => {
                const groupItems = navigation.filter(
                  (item) => item.group === group,
                );

                if (groupItems.length === 0) {
                  return null;
                }

                return (
                  <div key={group} className="mb-4">
                    <h3 className="mb-1 px-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
                      {group}
                    </h3>

                    {groupItems.map((item) => {
                      const isActive =
                        location === item.path ||
                        (item.path === "/overview" &&
                          location === "/");

                      return (
                        <Link
                          key={item.name}
                          href={item.path}
                          className={`group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                            isActive
                              ? "bg-slate-100 text-slate-900"
                              : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                          }`}
                          onClick={() => {
                            setMobileMenuOpen(false);
                          }}
                          aria-current={
                            isActive ? "page" : undefined
                          }
                        >
                          {item.icon && (
                            <item.icon
                              className="h-5 w-5 shrink-0"
                              aria-hidden="true"
                            />
                          )}
                          {item.name}
                        </Link>
                      );
                    })}
                  </div>
                );
              })}
            </nav>
          </div>
        </div>
      )}

      {/* Desktop sidebar */}
      <aside
        aria-label="Primary navigation"
        data-collapsed={desktopSidebarCollapsed}
        className={`sticky top-0 hidden h-screen shrink-0 flex-col border-r border-slate-200 bg-white transition-[width] duration-200 ease-out md:flex ${
          desktopSidebarCollapsed ? "w-20" : "w-64"
        }`}
      >
        <div
          className={`flex h-16 shrink-0 items-center border-b border-slate-200 ${
            desktopSidebarCollapsed
              ? "justify-center gap-1 px-1"
              : "justify-between gap-2 px-4"
          }`}
        >
          <Link
            href="/"
            className="flex min-w-0 items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2"
            aria-label="Return Platform overview"
            title={
              desktopSidebarCollapsed
                ? "Return Platform"
                : undefined
            }
          >
            <span
              className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-slate-900 text-white shadow-sm"
              aria-hidden="true"
            >
              <Activity size={16} />
            </span>

            {!desktopSidebarCollapsed && (
              <span className="min-w-0">
                <span className="block truncate text-base font-semibold tracking-tight text-slate-900">
                  Return Platform
                </span>
                <span className="block text-xs text-slate-500">
                  Data Console
                </span>
              </span>
            )}
          </Link>

          <button
            type="button"
            onClick={toggleDesktopSidebar}
            aria-label={
              desktopSidebarCollapsed
                ? "Expand sidebar"
                : "Collapse sidebar"
            }
            aria-expanded={!desktopSidebarCollapsed}
            title={
              desktopSidebarCollapsed
                ? "Expand sidebar"
                : "Collapse sidebar"
            }
            className="flex size-8 shrink-0 items-center justify-center rounded-md text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2"
          >
            {desktopSidebarCollapsed ? (
              <ChevronRight size={18} aria-hidden="true" />
            ) : (
              <ChevronLeft size={18} aria-hidden="true" />
            )}
          </button>
        </div>

        <nav
          className={`flex-1 space-y-1 overflow-y-auto py-4 ${
            desktopSidebarCollapsed ? "px-2" : "px-4"
          }`}
          aria-label="Sidebar"
        >
          {groups.map((group) => {
            const groupItems = navigation.filter(
              (item) => item.group === group,
            );

            if (groupItems.length === 0) {
              return null;
            }

            return (
              <div
                key={group}
                className={
                  desktopSidebarCollapsed ? "mb-3" : "mb-6"
                }
              >
                {!desktopSidebarCollapsed && (
                  <h3 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-slate-500">
                    {group}
                  </h3>
                )}

                {groupItems.map((item) => {
                  const isActive =
                    location === item.path ||
                    (item.path === "/overview" &&
                      location === "/");

                  return (
                    <Link
                      key={item.name}
                      href={item.path}
                      title={
                        desktopSidebarCollapsed
                          ? item.name
                          : undefined
                      }
                      aria-label={
                        desktopSidebarCollapsed
                          ? item.name
                          : undefined
                      }
                      className={`group flex items-center rounded-md py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 ${
                        desktopSidebarCollapsed
                          ? "justify-center px-2"
                          : "gap-3 px-3"
                      } ${
                        isActive
                          ? "bg-slate-100 text-slate-900"
                          : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                      }`}
                      aria-current={
                        isActive ? "page" : undefined
                      }
                    >
                      {item.icon && (
                        <item.icon
                          className="h-5 w-5 shrink-0"
                          aria-hidden="true"
                        />
                      )}

                      {!desktopSidebarCollapsed && (
                        <span className="min-w-0 truncate">
                          {item.name}
                        </span>
                      )}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>
      </aside>

      {/* Main content area */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {isMockMode && (
          <div
            role="region"
            aria-label="Mock Mode Indicator"
            className="shrink-0 border-b border-amber-200 bg-amber-100 px-4 py-2 text-center text-sm font-medium text-amber-900"
          >
            FIXTURE MODE — NON-DURABLE
          </div>
        )}

        <main
          id="main-content"
          className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8"
          tabIndex={-1}
        >
          <div className="mx-auto w-full max-w-6xl">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
