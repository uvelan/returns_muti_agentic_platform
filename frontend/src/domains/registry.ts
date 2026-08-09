import { Bot, Network, RotateCcw, Settings } from "lucide-react";

import type { Capability } from "../api/principal";

/**
 * The four canonical domains (Phase 17).
 *
 * These four and nothing else -- no V1/V2 selector, no legacy groups. The
 * legacy shell in `routes.ts` keeps its own navigation until Wave F removes it.
 *
 * `requires` is the capability that makes a domain *visible*. It is
 * deliberately the domain's cheapest read: a principal who cannot read
 * anything in a domain has no use for its entry, and one who can read is
 * shown the domain and then finds individual actions enabled or disabled by
 * their own capabilities. Hiding is presentation only -- the backend refuses
 * regardless.
 */
export type DomainDefinition = {
  readonly path: `/${string}`;
  readonly name: string;
  readonly description: string;
  readonly icon: React.ComponentType<{ size?: number; className?: string }>;
  readonly requires: Capability;
  /** The phase that builds this domain's screen. Shell-only until then. */
  readonly screenPhase: 18 | 19 | 20 | 21;
};

export const DOMAINS: readonly DomainDefinition[] = [
  {
    path: "/returns",
    name: "Return Business Copilot",
    description: "Discovery through resolution, one operational screen.",
    icon: RotateCcw,
    requires: "returns.session.read",
    screenPhase: 18,
  },
  {
    path: "/config",
    name: "Configuration",
    description: "Sources, integrations, business rules, runtime, and releases.",
    icon: Settings,
    requires: "config.runtime.read",
    screenPhase: 19,
  },
  {
    path: "/graph-schema",
    name: "Graph Schema Analyzer",
    description: "Source-driven schema proposal, validation, and activation.",
    icon: Network,
    requires: "graph_schema.draft.read",
    screenPhase: 20,
  },
  {
    path: "/ai",
    name: "AI Control Center",
    description: "Requests, interceptions, metrics, routes, and safety.",
    icon: Bot,
    requires: "ai.request.read",
    screenPhase: 21,
  },
];

export const DOMAIN_PATHS: readonly string[] = DOMAINS.map((domain) => domain.path);

export function isDomainPath(pathname: string): boolean {
  return DOMAINS.some(
    (domain) => pathname === domain.path || pathname.startsWith(`${domain.path}/`),
  );
}
