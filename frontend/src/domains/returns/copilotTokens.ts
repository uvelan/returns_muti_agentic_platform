/**
 * Design tokens and layout constants for the Return Copilot 3-pane shell.
 *
 * Modeled strictly on the Stitch "Deep Forest Enterprise" visual design contract:
 * - 40% Conversation / 24% Progress & Truth / 36% Authoritative Business Object
 * - Strict minimum readable text size (>= 12px / 0.75rem)
 * - Uniform 52px header heights to guarantee zero pixel-shift during mode transitions
 * - Seamless glassmorphic cards and crisp borders
 */

export const COPILOT_TOKENS = {
  layout: {
    // Post-sidebar 40fr / 24fr / 36fr desktop grid
    grid: "grid h-full grid-cols-1 gap-3 lg:grid-cols-[minmax(0,40fr)_minmax(0,24fr)_minmax(0,36fr)]",
    shell: "grid h-full grid-cols-1 gap-3 lg:grid-cols-[minmax(0,40fr)_minmax(0,24fr)_minmax(0,36fr)]",
    pane: "flex min-h-0 flex-col overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container-lowest shadow-[0_4px_24px_rgba(0,0,0,0.03)]",
    // A pane body scrolls and often holds nothing focusable, so each one
    // carries `tabIndex={0}` and a name at its call site; the ring is here
    // so they cannot disagree about how focus looks.
    paneBody:
      "flex-1 overflow-y-auto p-5 space-y-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary",
    paneBodyNoPadding: "flex-1 overflow-y-auto",
    messageStream: "flex-1 overflow-y-auto p-5 flex flex-col justify-between",
  },
  header: {
    container: "flex h-[56px] shrink-0 items-center justify-between border-b border-outline-variant/20 px-5 bg-surface-container-lowest/80 backdrop-blur-sm",
    title: "text-sm font-semibold text-on-surface truncate",
    subtitle: "text-xs text-outline truncate",
  },
  chatDock: {
    container: "shrink-0 border-t border-outline-variant/20 p-4 bg-surface-container-lowest",
    input: "w-full rounded-xl border border-outline-control bg-surface py-3 pl-4 pr-12 text-sm text-on-surface placeholder:text-outline outline-none transition focus:border-primary focus:ring-1 focus:ring-primary shadow-sm",
    hint: "mt-2 text-center text-xs text-outline",
  },
  section: {
    kicker: "text-xs font-semibold uppercase tracking-wider text-outline",
    divider: "border-t border-outline-variant/30",
  },
  typography: {
    caption: "text-xs text-outline",
    body: "text-sm text-on-surface leading-relaxed",
    subheading: "text-sm font-semibold text-on-surface",
    heading: "text-base font-semibold text-on-surface",
    badge: "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium",
  },
} as const;
