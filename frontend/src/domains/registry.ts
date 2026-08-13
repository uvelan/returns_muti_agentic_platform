import {
  Activity,
  Bot,
  ClipboardCheck,
  Database,
  Headset,
  Network,
  RefreshCw,
  RotateCcw,
  Settings,
} from "lucide-react";

import type { Capability } from "../api/principal";

/**
 * The canonical domains (Phase 17).
 *
 * These and nothing else -- no V1/V2 selector, no legacy groups. The
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
  readonly screenPhase: 18 | 19 | 20 | 21 | 22;
  /**
   * One line for the landing card about what this domain is *for*, as opposed
   * to `description`, which says what it contains. A card is read by someone
   * deciding where to go, not by someone already there.
   */
  readonly purpose: string;
  /** Present when no backend surface exists yet. Rendered as a card badge. */
  readonly status?: "NO BACKEND YET";
  /**
   * The domain's own navigation, rendered in its sidebar and addressable as
   * `/{domain}/{slug}`.
   *
   * Empty for a domain whose screen has no sections. Left empty rather than
   * filled with the flow its Stitch design implies -- a rail entry that routes
   * nowhere is worse than an absent one, and inventing navigation ahead of the
   * screens is how a shell starts lying about what exists.
   */
  readonly sections: readonly DomainSection[];
};

export type DomainSection = {
  /** URL segment. Lower-case, hyphenated. */
  readonly slug: string;
  /** Sidebar label, and the value the screen switches on. */
  readonly label: string;
};

/** `"Providers & Models"` -> `"providers-models"`. */
export function toSlug(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-|-$)/g, "");
}

function sections(labels: readonly string[]): readonly DomainSection[] {
  return labels.map((label) => ({ slug: toSlug(label), label }));
}

/**
 * Section labels, declared once and used twice: here to build the sidebar, and
 * by the screen to derive the union its body switches on. Kept as `as const`
 * tuples so the screen gets literal types -- a label renamed here is a compile
 * error in the screen's switch rather than a section that silently renders
 * nothing.
 */
/**
 * `"Data Sources"` is deliberately absent.
 *
 * It was a tab here, which made the platform's whole source surface -- health,
 * the collections and tables each source exposes, and where every dataset is
 * bound -- a nested selection inside Configuration. Sources are not a
 * configuration *field*; they are what the platform reads from, and the
 * question "can we still reach the warehouse database" is asked by people who
 * are not editing configuration at all. It is now the `/data-sources` domain.
 */
export const CONFIG_SECTIONS = [
  "Overview",
  "Agents",
  "Runtime",
  "Releases",
  "Integrations",
  "Business",
  "Modules",
  "Security",
  "Audit",
] as const;

export const AI_SECTIONS = [
  "Overview",
  "Requests",
  "Interceptions",
  "Metrics",
  "Providers & Models",
  "Routes & Tasks",
  "Safety",
  "Configuration",
  "Audit",
] as const;

export const DOMAINS: readonly DomainDefinition[] = [
  {
    path: "/returns",
    name: "Return Business Copilot",
    description: "Discovery through resolution, one operational screen.",
    purpose: "Take a customer from a partial description to a completed return.",
    icon: RotateCcw,
    requires: "returns.session.read",
    screenPhase: 18,
    // The copilot is one workspace with queues, not a set of sections. Its
    // queue picker stays on the screen, where it belongs -- it filters a list
    // rather than switching what the screen is.
    sections: [],
  },
  {
    path: "/support",
    name: "Returns Support",
    description: "The Channel B conversation: return requests, and the replies to them.",
    purpose: "Answer the agent's return requests and issue the RMA, label and pickup.",
    icon: Headset,
    // Same capability as the copilot for now. Support is a distinct *role*, and
    // it should get a distinct capability -- but inventing `support.*` before
    // the backend grants it would hide this screen from everybody, which reads
    // as a bug rather than as work in progress.
    requires: "returns.session.read",
    screenPhase: 18,
    // One workspace: a queue and the conversation it opens. The queue picker
    // filters a list rather than switching what the screen is.
    sections: [],
  },
  {
    path: "/config",
    name: "Configuration",
    description: "Sources, integrations, business rules, runtime, and releases.",
    purpose: "Change how the platform behaves, and release the change safely.",
    icon: Settings,
    requires: "config.runtime.read",
    screenPhase: 19,
    sections: sections(CONFIG_SECTIONS),
  },
  {
    path: "/approvals",
    name: "Approvals",
    description: "Every change waiting on a human decision, in one queue.",
    purpose: "Decide what is waiting on you, with the whole basis for the decision in front of you.",
    icon: ClipboardCheck,
    // The governance read, which is exactly the question this domain asks.
    // `ProposalKernel` is one inbox on purpose -- a schema draft, an agent
    // configuration edit and a feedback improvement are one decision queue --
    // so it needs no per-type capability and this domain has none.
    requires: "governance.proposal.read",
    screenPhase: 22,
    // One queue and the proposal it opens. The status filter narrows a list
    // rather than switching what the screen is.
    sections: [],
  },
  {
    path: "/data-sources",
    name: "Data Sources",
    description: "Configured sources, their health, what they expose, and where each dataset is bound.",
    purpose: "Check the platform can still reach its data, and point a dataset somewhere else.",
    icon: Database,
    requires: "config.source.read",
    screenPhase: 22,
    // One workspace: the source list, the source it opens, and the dataset
    // bindings underneath. Nothing here switches what the screen is.
    sections: [],
  },
  {
    path: "/graph-schema",
    name: "Graph Schema Analyzer",
    description: "Source-driven schema proposal, validation, and activation.",
    purpose: "Turn source collections into the graph the copilot searches.",
    icon: Network,
    requires: "graph_schema.draft.read",
    screenPhase: 20,
    // The analyzer's tabs (Validation, Versions, Mapping, ...) belong to a
    // selected draft, not to the domain, so they are not sections. The Stitch
    // kit's own rail -- Sources, Context, Schema, Validation, Indexes, Sync --
    // is the flow those screens will introduce; it goes here when they land.
    sections: [],
  },
  {
    path: "/ai",
    name: "AI Control Center",
    description: "Requests, interceptions, metrics, routes, and safety.",
    purpose: "See what the models were asked, and answer anything held for review.",
    icon: Bot,
    requires: "ai.request.read",
    screenPhase: 21,
    sections: sections(AI_SECTIONS),
  },
  {
    path: "/sync",
    name: "Source Sync",
    description: "Sync runs, what each one read, and what it wrote to the graph.",
    purpose: "Check the graph is current, and rebuild it from the sources when it is not.",
    icon: RefreshCw,
    // Its own capability, and it already existed. `config.source.read` is the
    // right question -- "may this person see how the platform reads its
    // sources" -- so this domain does not join the two that had to borrow one.
    requires: "config.source.read",
    screenPhase: 22,
    // One workspace: a run history and the run it opens. The mode filter
    // narrows a list rather than switching what the screen is.
    sections: [],
  },
  {
    path: "/operations",
    name: "Operations",
    description: "Return queues and timelines; generations and workers to come.",
    purpose: "Work the return queues, and see whether the platform is healthy.",
    icon: Activity,
    // No `operations.*` capability exists yet. Gated on the runtime read
    // rather than on an invented one: a capability the backend never grants
    // would hide this domain from everybody, which reads as a bug rather than
    // as work-in-progress. It gets its own capability when the API lands.
    requires: "config.runtime.read",
    screenPhase: 22,
    // The returns-operations half is backed and built. What is left of the
    // platform-health half -- graph generations, workers, outbox -- has no API,
    // so the badge stays until it does. Sync runs left this list when they got
    // one; they are the `/sync` domain above.
    status: "NO BACKEND YET",
    sections: [],
  },
];

export const DOMAIN_PATHS: readonly string[] = DOMAINS.map((domain) => domain.path);

/** The launcher. Not a domain, so it is not in `DOMAINS`. */
export const LANDING_PATH = "/";

/**
 * The domain at `path`. Throws rather than returning null: a screen asking for
 * its own domain is asking about a constant in this file, so a miss is a typo
 * at build time, not a condition to handle at runtime.
 */
export function requireDomain(path: string): DomainDefinition {
  const domain = DOMAINS.find((candidate) => candidate.path === path);
  if (domain === undefined) {
    throw new Error(`no domain registered at ${path}`);
  }
  return domain;
}

/** The domain a path belongs to, or null for the launcher / an unknown path. */
export function domainForPath(pathname: string): DomainDefinition | null {
  return (
    DOMAINS.find(
      (domain) => pathname === domain.path || pathname.startsWith(`${domain.path}/`),
    ) ?? null
  );
}

export function isDomainPath(pathname: string): boolean {
  return (
    pathname === LANDING_PATH ||
    DOMAINS.some((domain) => pathname === domain.path || pathname.startsWith(`${domain.path}/`))
  );
}
