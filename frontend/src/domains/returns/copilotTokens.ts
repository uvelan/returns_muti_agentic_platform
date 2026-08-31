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
    /**
     * Somebody else is editing this. Case-level, never one actor's contents.
     *
     * **The foreground is `on-surface`, not `on-tertiary-container`.** This
     * token shipped as the paired role at a `/40` tint and read at **1.29:1** --
     * unreadable, on the one notice that tells an associate somebody else is
     * editing the review they are about to approve.
     *
     * The rule it broke, which now holds across this file: *an opacity modifier
     * is never applied to a `*-container` ground whose foreground is the paired
     * `on-*-container` role.* The pair is contrast-tested **as a pair**, so
     * tinting one side invalidates the test that licensed it. Use the container
     * at full strength, or tint it and pick a foreground tested against the
     * tint.
     *
     * Why that rule bites here and nowhere else: this palette's `tertiary` pair
     * is **inverted** relative to its siblings. `secondary-container` and
     * `error-container` are light grounds with dark `on-` roles, so a tint moves
     * the ground *away* from the foreground and the pairing survives --
     * `review.gap` at `/30` actually improves, to 8.68:1. `tertiary-container`
     * is a dark brown with a *light* `on-` role, so the same tint moves the
     * ground **towards** the foreground and the two meet in the middle. A
     * reviewer cannot see that by reading class names: the token looks exactly
     * like its siblings.
     *
     * Why not simply drop the tint: at full strength the pair reads 4.54:1,
     * which passes a 4.5 threshold by 0.04 and is one palette tweak from
     * failing. On this notice that is not a margin worth having.
     *
     * `on-surface` on the tint reads **9.08:1**, and this is the second time
     * that answer has been reached in this file rather than a new idea --
     * `support.attentionNotice` is the same ground, the same tint and the same
     * foreground, for the same diagnosed reason. Two tokens, one rule. The
     * `tertiary` family is kept because its documented meaning, "this is in
     * somebody else's hands", is exactly what an edit conflict is.
     *
     * Measured by `reviewContrast.test.ts`, off the token string and the real
     * palette -- the gate that did not exist when this shipped.
     */
    conflict:
      "rounded-lg border border-tertiary/50 bg-tertiary-container/40 px-3 py-2 text-xs text-on-surface",
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
  /**
   * What Support said, and what the platform made of it (V2 phase 2).
   *
   * Its own group rather than more entries under `review`, for the reason the
   * two registries are two registries: `review.*` is V1's vocabulary for a
   * draft an associate is about to send, and this is V2's for a message that
   * has already arrived. Folding one into the other would mean every future
   * change to a review badge had to be checked against a support card.
   *
   * Where a value here is identical to a `review` one it says so, and the
   * duplication is deliberate: two tokens naming one M3 role pair is how a
   * design system lets the two diverge later without a rename. Every value is
   * an M3 role. No hex, and nothing smaller than `text-xs`.
   */
  support: {
    /**
     * One return record's artifact card.
     *
     * `ProgressTruthPane`'s record card is the pattern -- same radius, same
     * `surface-container-low` on `outline-variant`, same shadow -- so the two
     * places a return record is drawn look like the same object. Extracted to
     * a token here because that pane spells it inline, and a second inline
     * copy is the first divergence.
     */
    card: "rounded-lg border border-outline-variant/40 bg-surface-container-low p-3 shadow-sm",
    cardHeader: "flex items-baseline justify-between gap-2",
    /** The reference Support issued. Never invented -- see `PENDING_LABEL`. */
    reference: "truncate text-sm font-semibold text-on-surface",
    /** A `<dl>` row. `grid` rather than `flex`, so long values wrap under. */
    row: "grid grid-cols-[minmax(0,7rem)_minmax(0,1fr)] gap-2 py-0.5",
    term: "text-xs text-outline",
    /**
     * `break-words`, not `truncate`.
     *
     * A truncated tracking number is a different tracking number, and this is
     * the value an associate reads aloud down a phone. The record card in
     * `ProgressTruthPane` truncates because it is a glance surface with a
     * `title`; the panel is the surface someone opens to get the value right.
     */
    value: "text-sm text-on-surface break-words",
    /**
     * A small labelled chip: an intent, a disposition, a binding status.
     *
     * Tone is never the only signal -- every call site puts the word inside
     * the chip, so the meaning survives a monochrome screen and a screen
     * reader.
     */
    chip: "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
    chipTone: {
      /** The ordinary case: a message that was classified and applied. */
      neutral: "bg-surface-container-high text-on-surface-variant",
      /**
       * Something is waiting on a person.
       *
       * The same **role pair** as `review.state.APPROVING`
       * (`tertiary-container` on `on-tertiary-container`, 4.54:1), which is the
       * badge V1 already uses for "in somebody else's hands". Deliberately not
       * `review.conflict`'s roles: that one is a *notice box* at
       * `bg-tertiary-container/40` with a `tertiary` border, and a chip drawn
       * at 40% would land near 2:1 against `on-tertiary-container`. An earlier
       * draft of this comment named `review.conflict` as the twin, which was
       * wrong in the one way a token comment can be dangerous -- it points the
       * next reader at the wrong value to copy.
       */
      attention: "bg-tertiary-container text-on-tertiary-container",
      /** On file, not acted on. Deliberately not an error tone -- see below. */
      parked: "bg-secondary-container text-on-secondary-container",
    },
    /**
     * Messages are being kept rather than processed.
     *
     * `secondary`, not `error`, and this is a judgement rather than a palette
     * choice: `nl_enabled: false` parks a message on purpose (contracts.md
     * sect. 5 -- "never 409"), the message is on file, counted, and replayed in
     * stream order when the switch flips. Painting a deliberate configuration
     * in the error colour teaches an associate to ignore the error colour.
     */
    notice:
      "rounded-lg border border-outline-variant/40 bg-secondary-container/40 px-3 py-2 text-xs text-on-secondary-container",
    /**
     * Something is on file and waiting on a person.
     *
     * Added by the design critique, which found the unbound-artifact block
     * drawn in `notice` -- the *parking* tone. Those two states are opposites
     * from where an associate stands: a parked message is on file and needs
     * nobody, and an unfiled artifact cannot be used by anybody until somebody
     * says which return it belongs to. Drawing them in one colour told a reader
     * that both were equally finished.
     *
     * The `tertiary` family -- V1's roles for "this is in somebody's hands" --
     * rather than `error`, which is reserved here for the do-not-mix warning,
     * the one case that is unrecoverable by re-reading.
     *
     * **The foreground is `on-surface`, not `on-tertiary-container`, and the
     * accessibility review is why.** This palette's tertiary pair is *inverted*
     * relative to its siblings: `secondary-container` and `error-container` are
     * light with dark `on-` roles, but `tertiary-container` is a dark brown with
     * a *light* `on-` role. That pair reads 4.54:1 as a solid chip -- and at the
     * `/40` tint a notice needs, the ground lightens to roughly `#d0b7ad` while
     * the foreground stays light, landing near **1.3:1**. The `on-` role is only
     * the right foreground for the *solid* container, which is why the chip uses
     * it and this does not. On the tint, `on-surface` reads about 9.6:1.
     *
     * `supportTokens.test.ts` now computes this from the palette rather than
     * trusting the pairing, because the first draft of this token copied
     * `review.conflict`'s foreground and would have shipped the 1.3:1.
     */
    attentionNotice:
      "rounded-lg border border-tertiary/50 bg-tertiary-container/40 px-3 py-2 text-xs text-on-surface",
    /**
     * Do not mix these records up.
     *
     * The one place in this group that borrows `review.gap`'s roles, because
     * it is the one place with the same weight: a label filed against the
     * wrong RMA sends a customer's freight to the wrong dock, and it is not
     * recoverable by reading the screen again afterwards.
     */
    warning:
      "rounded-lg border border-error/40 bg-error-container/30 px-3 py-2 text-xs text-on-error-container",
    /** One inbound message in the thread digest. */
    digestRow: "border-t border-outline-variant/30 pt-2 first:border-t-0 first:pt-0",
    /**
     * A typed system entry in the Order Discovery transcript (DR-3).
     *
     * Full width and centred rather than a left or right bubble, because it is
     * neither party speaking: the associate's messages sit right, the agent's
     * sit left, and an entry that borrowed either shape would put the
     * platform's words in somebody's mouth on a screen somebody screenshots.
     */
    systemEntry:
      "rounded-xl border border-dashed border-outline-variant/60 bg-surface-container-high/60 px-4 py-2.5",
    systemEntryKicker:
      "mb-1 block text-xs font-bold uppercase tracking-wider text-on-surface-variant",
    /** Same value and same rules as `review.liveRegion`: polite, never focused. */
    liveRegion: "text-xs text-outline min-h-[1rem]",
    /**
     * The panel's announcement, with no visible counterpart.
     *
     * `sr-only`, not `hidden` and not `text-transparent`: a `display:none`
     * region is not announced at all, which is the failure this exists to
     * avoid. It carries no `min-h` because it occupies no space either way, so
     * there is no layout to reserve.
     *
     * Why an invisible one rather than reusing `liveRegion`: what arrives is
     * *already on the screen* -- a new artifact card, a rising parked count --
     * so a visible line repeating it would be the same fact twice for a sighted
     * associate, and its absence would be the fact only once for everyone else.
     */
    announcer: "sr-only",
  },
} as const;

/**
 * The one word this domain uses for "the platform has not said".
 *
 * `ProgressTruthPane` declares its own `PENDING` and `ReturnCopilotFabrication`
 * allowlists exactly this spelling in its `??`-fallback rule, so a second
 * vocabulary here would either be banned by that test or -- worse -- slip
 * through as a new invented word. Exported so the panel sections and the record
 * card cannot drift apart on what an unknown value is called.
 */
export const PENDING_LABEL = "Pending";
