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
  /**
   * The review panel's own vocabulary (V1 phase 2).
   *
   * Added here rather than inside the components for the reason the file
   * exists: a component that invented its own colours would be the second place
   * the visual language lives, and the first divergence nobody notices is a
   * "sending" badge that is a different amber from every other one.
   *
   * Every value is an M3 role, never a hex. The audit before this was written
   * found `COPILOT_TOKENS` covering layout, header, chat dock, section and
   * typography, and carrying **no** state scale and **no** form controls --
   * which is exactly what a review section needs for six review states and an
   * editable field.
   */
  review: {
    /**
     * One review state, as a badge.
     *
     * Six entries because the aggregate has six non-initial states and the
     * panel shows every one of them: a review an associate can no longer edit
     * is precisely the one they most need to see. Colour is never the only
     * signal -- each badge also carries its word and an icon at the call site
     * -- because a state distinguished only by hue is unreadable to a
     * colour-blind associate and invisible to a screen reader.
     */
    state: {
      OPEN: "bg-secondary-container text-on-secondary-container",
      APPROVING: "bg-tertiary-container text-on-tertiary-container",
      SENT: "bg-primary-container text-on-primary-container",
      DELIVERY_FAILED: "bg-error-container text-on-error-container",
      HELD_FOR_OPERATIONS: "bg-error-container text-on-error-container",
      CANCELLED: "bg-surface-container-high text-outline",
      ABANDONED: "bg-surface-container-high text-outline",
    },
    /**
     * Where a field's value came from. Provenance, not decoration -- sect. 8.
     *
     * `text-xs` (0.75rem), not smaller. The first draft of this used
     * `text-[0.6875rem]` to keep the chips out of the way, which breaks this
     * file's own stated rule at the top -- *strict minimum readable text size
     * (>= 12px / 0.75rem)*. Provenance is precisely the thing an associate
     * squints at when deciding whether to trust a value, so it is the last
     * place to shave a pixel off.
     */
    provenance:
      "inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium bg-surface-container-high text-outline",
    /** A field the release marked required and the case cannot answer. */
    gap: "rounded-lg border border-error/40 bg-error-container/30 px-3 py-2 text-xs text-on-error-container",
    /** Somebody else is editing this. Case-level, never one actor's contents. */
    conflict:
      "rounded-lg border border-tertiary/50 bg-tertiary-container/40 px-3 py-2 text-xs text-on-tertiary-container",
    field: {
      row: "grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-3 py-1.5 items-start",
      label: "text-xs font-medium text-outline pt-1.5",
      value: "text-sm text-on-surface break-words",
      /**
       * `min-h` rather than `rows`, and `field-sizing-content` where the
       * browser has it: a draft field that scrolls inside three lines hides
       * the thing the associate is checking.
       */
      input:
        "w-full rounded-lg border border-outline-control bg-surface px-2.5 py-1.5 text-sm text-on-surface outline-none transition focus:border-primary focus:ring-1 focus:ring-primary field-sizing-content min-h-[2.25rem]",
      edited: "border-tertiary bg-tertiary-container/20",
    },
    action: {
      primary:
        "inline-flex items-center gap-1.5 rounded-lg bg-primary px-3.5 py-2 text-sm font-medium text-on-primary transition hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 aria-disabled:opacity-50 aria-disabled:pointer-events-none",
      secondary:
        "inline-flex items-center gap-1.5 rounded-lg border border-outline-control px-3 py-2 text-sm font-medium text-on-surface transition hover:bg-surface-container-high focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 aria-disabled:opacity-50 aria-disabled:pointer-events-none",
      danger:
        "inline-flex items-center gap-1.5 rounded-lg border border-error/50 px-3 py-2 text-sm font-medium text-error transition hover:bg-error-container/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-error focus-visible:ring-offset-2 aria-disabled:opacity-50 aria-disabled:pointer-events-none",
      /** A row of actions. Wraps, because a narrow pane must not clip Send. */
      bar: "flex flex-wrap items-center gap-2 pt-3",
    },
    /**
     * Where an autosave, a re-render or an arriving message announces itself.
     *
     * `polite`, never `assertive`, and never focused: an associate mid-sentence
     * must not be interrupted, and a support artifact arriving while they type
     * must not take the caret out of the field they are in.
     */
    liveRegion: "text-xs text-outline min-h-[1rem]",
    /**
     * A question Support is asking this associate (V3, contracts.md sect. 9).
     *
     * Its own entry rather than a borrowed one. The first draft of V3's
     * clarification card reused `review.conflict`, whose documented meaning is
     * "another actor editing; a superseded draft; a confirmation" -- a question
     * from a supplier is none of those, and reusing the nearest container is how
     * a token's meaning erodes until it means "boxed".
     *
     * `secondary-container` because it is neither a failure (`gap`, error) nor
     * somebody else's half-finished work (`conflict`, tertiary): it is an open
     * item addressed to the person reading it, which is what `state.OPEN`
     * already uses the secondary pair for. Every value is an M3 role, so a theme
     * change reaches it with the rest of the console.
     */
    clarification:
      "rounded-lg border border-secondary/50 bg-secondary-container/40 px-3 py-2 text-on-secondary-container",
  },
} as const;
