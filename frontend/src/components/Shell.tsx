import type { ReactNode } from "react";
import {
  Activity,
  LayoutDashboard,
} from "lucide-react";
import {
  Link,
  useRoute,
} from "wouter";


type ShellProps = {
  readonly children: ReactNode;
};


export function Shell({
  children,
}: ShellProps) {
  const [isOverviewRoute] = useRoute("/overview");
  const [isRootRoute] = useRoute("/");

  const isOverviewActive =
    isOverviewRoute || isRootRoute;

  return (
    <div className="min-h-screen bg-slate-50">
      <a
        href="#main-content"
        className="
          sr-only z-50 rounded-md bg-slate-900 px-4 py-2
          text-sm font-medium text-white
          focus:not-sr-only focus:fixed focus:left-4 focus:top-4
          focus:outline-none focus:ring-2
          focus:ring-slate-500 focus:ring-offset-2
        "
      >
        Skip to main content
      </a>

      <header
        className="
          sticky top-0 z-40 border-b border-slate-200
          bg-white/95 shadow-sm backdrop-blur
        "
      >
        <div
          className="
            mx-auto flex h-16 max-w-7xl items-center
            justify-between gap-4 px-4 sm:px-6 lg:px-8
          "
        >
          <Link
            href="/overview"
            className="
              flex min-w-0 items-center gap-3 rounded-md
              focus-visible:outline-none focus-visible:ring-2
              focus-visible:ring-slate-500
              focus-visible:ring-offset-2
            "
            aria-label="Return Platform overview"
          >
            <span
              className="
                flex size-9 shrink-0 items-center justify-center
                rounded-lg bg-slate-900 text-white
                shadow-sm
              "
              aria-hidden="true"
            >
              <Activity size={18} />
            </span>

            <span className="min-w-0">
              <span
                className="
                  block truncate text-base font-semibold
                  tracking-tight text-slate-900 sm:text-lg
                "
              >
                Return Platform
              </span>

              <span
                className="
                  hidden text-xs text-slate-500 sm:block
                "
              >
                Data Console
              </span>
            </span>
          </Link>

          <nav
            className="flex items-center"
            aria-label="Primary navigation"
          >
            <Link
              href="/overview"
              className={`
                inline-flex items-center gap-2 rounded-md
                px-3 py-2 text-sm font-medium transition-colors
                focus-visible:outline-none focus-visible:ring-2
                focus-visible:ring-slate-500
                focus-visible:ring-offset-2
                ${
                  isOverviewActive
                    ? "bg-slate-100 text-slate-950"
                    : `
                      text-slate-600 hover:bg-slate-100
                      hover:text-slate-950
                    `
                }
              `}
              aria-current={
                isOverviewActive
                  ? "page"
                  : undefined
              }
            >
              <LayoutDashboard
                size={16}
                aria-hidden="true"
              />

              <span className="hidden sm:inline">
                Overview
              </span>
            </Link>
          </nav>
        </div>
      </header>

      <main
        id="main-content"
        className="
          mx-auto w-full max-w-7xl
          px-4 py-6 sm:px-6 sm:py-8 lg:px-8
        "
        tabIndex={-1}
      >
        {children}
      </main>
    </div>
  );
}