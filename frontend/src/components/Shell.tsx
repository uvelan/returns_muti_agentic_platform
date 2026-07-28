import { useState, type ReactNode } from "react";
import { Link, useLocation } from "wouter";
import { Menu, X, Activity } from "lucide-react";
import { routes } from "../routes";

type ShellProps = {
  readonly children: ReactNode;
};

export function Shell({ children }: ShellProps) {
  const [location] = useLocation();
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const isMockMode = import.meta.env.MODE === "mock" || import.meta.env.VITE_MOCK_MODE === "true";

  const navigation = routes.filter(r => r.navigable);
  const groups = ["Associate", "Customer", "Support", "AI Gateway", "Explore", "Data & AI Validation", "Data Operations", "Isolated Data & AI", "Governance", "System"] as const;

  return (
    <div className="min-h-screen bg-slate-50 flex flex-col md:flex-row">
      <a
        href="#main-content"
        className="sr-only z-50 rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:outline-none focus:ring-2 focus:ring-slate-500 focus:ring-offset-2"
      >
        Skip to main content
      </a>

      {/* Mobile header */}
      <header className="md:hidden sticky top-0 z-40 flex items-center justify-between border-b border-slate-200 bg-white/95 px-4 py-3 backdrop-blur">
        <Link href="/" className="flex items-center gap-2">
          <span className="flex size-8 items-center justify-center rounded-lg bg-slate-900 text-white shadow-sm" aria-hidden="true">
            <Activity size={16} />
          </span>
          <span className="font-semibold text-slate-900 tracking-tight">Data Console</span>
        </Link>
        <button
          type="button"
          className="text-slate-500 hover:text-slate-600 focus:outline-none focus:ring-2 focus:ring-slate-500 rounded-md p-1"
          onClick={() => { setMobileMenuOpen(!mobileMenuOpen); }}
          aria-expanded={mobileMenuOpen}
        >
          <span className="sr-only">{mobileMenuOpen ? "Close menu" : "Open menu"}</span>
          {mobileMenuOpen ? <X size={24} aria-hidden="true" /> : <Menu size={24} aria-hidden="true" />}
        </button>
      </header>

      {/* Mobile navigation menu */}
      {mobileMenuOpen && (
        <div className="md:hidden fixed inset-0 z-30 bg-slate-900/50 backdrop-blur-sm" onClick={() => { setMobileMenuOpen(false); }}>
          <div className="fixed inset-y-0 left-0 w-64 bg-white shadow-xl flex flex-col" onClick={e => { e.stopPropagation(); }}>
            <nav aria-label="Mobile Navigation" className="flex-1 px-4 py-6 space-y-1 overflow-y-auto mt-14">
              {groups.map(group => {
                const groupItems = navigation.filter(item => item.group === group);
                if (groupItems.length === 0) return null;
                return (
                  <div key={group} className="mb-4">
                    <h3 className="px-3 text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">{group}</h3>
                    {groupItems.map((item) => {
                      const isActive = location === item.path || (item.path === '/overview' && location === '/');
                      return (
                        <Link
                          key={item.name}
                          href={item.path}
                          className={`group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors ${isActive ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'}`}
                          onClick={() => { setMobileMenuOpen(false); }}
                          aria-current={isActive ? 'page' : undefined}
                        >
                          {item.icon && <item.icon className="h-5 w-5 shrink-0" aria-hidden="true" />}
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
      <aside aria-label="Primary navigation" className="hidden md:flex flex-col w-64 shrink-0 border-r border-slate-200 bg-white sticky top-0 h-screen">
        <div className="flex h-16 shrink-0 items-center px-6 border-b border-slate-200">
          <Link href="/" className="flex min-w-0 items-center gap-3 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2" aria-label="Return Platform overview">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-slate-900 text-white shadow-sm" aria-hidden="true">
              <Activity size={16} />
            </span>
            <span className="min-w-0">
              <span className="block truncate text-base font-semibold tracking-tight text-slate-900">Return Platform</span>
              <span className="block text-xs text-slate-500">Data Console</span>
            </span>
          </Link>
        </div>
        <nav className="flex-1 overflow-y-auto px-4 py-4 space-y-1" aria-label="Sidebar">
          {groups.map(group => {
            const groupItems = navigation.filter(item => item.group === group);
            if (groupItems.length === 0) return null;
            return (
              <div key={group} className="mb-6">
                <h3 className="px-3 text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">{group}</h3>
                {groupItems.map((item) => {
                  const isActive = location === item.path || (item.path === '/overview' && location === '/');
                  return (
                    <Link
                      key={item.name}
                      href={item.path}
                      className={`group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2 ${isActive ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900'}`}
                      aria-current={isActive ? 'page' : undefined}
                    >
                      {item.icon && <item.icon className="h-5 w-5 shrink-0" aria-hidden="true" />}
                      {item.name}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>
      </aside>

      {/* Main content area */}
      <div className="flex flex-1 flex-col overflow-hidden min-w-0">
        {isMockMode && (
          <div role="region" aria-label="Mock Mode Indicator" className="bg-amber-100 px-4 py-2 text-center text-sm font-medium text-amber-900 border-b border-amber-200 shrink-0">
            FIXTURE MODE — NON-DURABLE
          </div>
        )}
        <main
          id="main-content"
          className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8"
          tabIndex={-1}
        >
          <div className="mx-auto max-w-6xl w-full">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
