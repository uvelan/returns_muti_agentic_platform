import { describe, expect, it } from "vitest";

import { COPILOT_TOKENS } from "./copilotTokens";

/**
 * WCAG 1.4.3 for the `review` token group, and the palette-level rule that
 * `review.conflict` broke.
 *
 * ## Why this file exists at all
 *
 * `supportContrast.test.ts` already measures contrast off token strings against
 * the real palette, and it is the reason V2's `attentionNotice` never shipped at
 * 1.3:1. It measures `COPILOT_TOKENS.support` **only**, by an explicit and
 * correct scoping decision recorded in its own header: *"Widening it to the
 * whole object would make this V2's test of V1's tokens, and a failure in it
 * would land on whoever touched `review` next."*
 *
 * That decision was right for V2 and is exactly why `review.conflict` shipped at
 * **1.29:1** and was found by an audit. The instrument existed, was correct, and
 * did not reach the component. Its scope was a boundary between slices, and the
 * defect walked through the gap -- so the fix is not to widen V2's file but to
 * give `review` the same measurement under its own ownership.
 *
 * **And axe, which "already catches contrast failures", catches nothing here.**
 * The only axe in this repository is in `tests/canonical-routes.spec.ts`, a
 * Playwright spec. No CI job runs Playwright -- `npm test` is `vitest run`,
 * whose `include` is `src/**` and does not match `tests/*.spec.ts`. And even run
 * by hand it sweeps route paths in their *default loaded state*, while
 * `review.conflict` renders only when a case panel carries
 * `conflict_present: true`. No navigation produces that state, so axe never has
 * the element in the page. A detector must reach as far as the thing it
 * protects; that one reaches a route and this token lives three states inside
 * it.
 *
 * ## The rule, stated once
 *
 * **An opacity modifier is never applied to a `*-container` ground whose
 * foreground is the paired `on-*-container` role.** The pair is contrast-tested
 * *as a pair*; tinting one side invalidates the test that licensed it. Use the
 * container at full strength, or tint it and choose a foreground tested against
 * the tint.
 *
 * It is a palette-level rule, not a token-level one, because this palette's
 * `tertiary` pair is **inverted**: `secondary-container` and `error-container`
 * are light grounds with dark `on-` roles, so a tint moves the ground *away*
 * from the foreground and the pairing survives -- `review.gap` at `/30` improves
 * to 8.68:1. `tertiary-container` is a dark brown with a *light* `on-` role, so
 * the same tint moves the ground **towards** the foreground and they meet. The
 * two cases are indistinguishable by reading class names.
 *
 * The palette is read out of `tailwind.config.js` as text rather than mirrored:
 * a second copy of the hexes would agree with itself forever.
 */

const REVIEW = COPILOT_TOKENS.review;

const PALETTE: Readonly<Record<string, string>> = Object.fromEntries(
  [
    ...Object.values(
      import.meta.glob("../../../tailwind.config.js", {
        query: "?raw",
        import: "default",
        eager: true,
      }),
    )
      .join(" ")
      .matchAll(/"?([a-z0-9-]+)"?\s*:\s*"(#[0-9a-f]{6})"/gi),
  ].map((match) => [match[1], match[2]]),
);

/**
 * The grounds a tinted container in this product can be composited over.
 *
 * Both, and the **worst** result is the one asserted. The alternative is a
 * per-call-site table of which surface each element sits on, which is a second
 * copy of the layout that would go stale silently -- and a stale ground makes a
 * contrast test report a number about a screen that does not exist. Taking the
 * minimum cannot be wrong in the unsafe direction.
 */
const GROUNDS = ["surface-container-lowest", "surface"] as const;

function channel(value: number): number {
  const ratio = value / 255;
  return ratio <= 0.04045 ? ratio / 12.92 : Math.pow((ratio + 0.055) / 1.055, 2.4);
}

function rgb(hex: string): readonly [number, number, number] {
  const parsed = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex);
  if (parsed === null) throw new Error(`not a hex colour: ${hex}`);
  const [red, green, blue] = parsed.slice(1).map((part) => parseInt(part, 16));
  return [red, green, blue];
}

function luminance(hex: string): number {
  const [red, green, blue] = rgb(hex).map(channel);
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

/** A tint over a ground, the way `bg-x/40` actually composites. */
function blend(over: string, under: string, alpha: number): string {
  const [ar, ag, ab] = rgb(over);
  const [br, bg, bb] = rgb(under);
  return `#${[
    Math.round(ar * alpha + br * (1 - alpha)),
    Math.round(ag * alpha + bg * (1 - alpha)),
    Math.round(ab * alpha + bb * (1 - alpha)),
  ]
    .map((component) => component.toString(16).padStart(2, "0"))
    .join("")}`;
}

function contrast(foreground: string, background: string): number {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
}

/** Every string in a group, with the path it was reached by. */
function tokenEntries(
  node: unknown,
  path: readonly string[] = [],
): readonly { path: string; value: string }[] {
  if (typeof node === "string") return [{ path: path.join("."), value: node }];
  if (typeof node !== "object" || node === null) return [];
  return Object.entries(node).flatMap(([key, child]) => tokenEntries(child, [...path, key]));
}

/**
 * The tokens that paint text on a ground of their own.
 *
 * **One definition, used by the measurement and by the guard that proves the
 * measurement is not empty.** Written twice, the guard passes while the
 * measurement measures nothing -- the fault `supportContrast.test.ts` records
 * getting past an earlier version of itself.
 */
function measurableTokens(): readonly { path: string; value: string }[] {
  return tokenEntries(REVIEW).filter(
    (entry) => /\bbg-/.test(entry.value) && /\btext-/.test(entry.value),
  );
}

/**
 * The foreground and ground a token actually asks for, read off the token.
 *
 * A token naming a role the palette does not define is a **failure** rather than
 * a skip: a silently unresolved role is how a whole token drops out of the audit
 * while the audit reports success.
 */
function measured(token: string): number {
  const foreground = /\btext-((?:on-|outline|primary|secondary|tertiary|error)[a-z-]*)\b/.exec(
    token,
  );
  const ground = /\bbg-([a-z-]+)(?:\/(\d+))?\b/.exec(token);
  if (foreground === null) throw new Error(`token names no foreground: ${token}`);
  if (ground === null) throw new Error(`token names no ground: ${token}`);
  if (!(foreground[1] in PALETTE)) throw new Error(`unknown role ${foreground[1]}`);
  if (!(ground[1] in PALETTE)) throw new Error(`unknown role ${ground[1]}`);
  const alpha = ground[2] ? Number(ground[2]) / 100 : 1;
  return Math.min(
    ...GROUNDS.map((base) =>
      contrast(PALETTE[foreground[1]], blend(PALETTE[ground[1]], PALETTE[base], alpha)),
    ),
  );
}

/**
 * Every `bg-<family>-container/<tint>` in the frontend source, with whatever
 * `text-on-<family>-container` sits on the same line.
 *
 * The `-container/` grep, alongside the hardcoded-literal check in
 * `supportTokens.test.ts`. It runs over source rather than over the token object
 * because the shape is not confined to tokens: `ConversationPane`,
 * `ShipmentConsolePage` and `AiControlCenterPage` all spell tinted containers
 * inline.
 *
 * **It strips comments, then scans string literals.** Both halves were earned.
 *
 * The first version scanned raw lines and reported `copilotTokens.ts:98`
 * failing at 1.29:1 -- which is a *comment* describing the defect this file
 * exists to prevent, quoting the broken pairing verbatim. A red that was real,
 * precise, correctly computed, and about prose.
 *
 * The second version read only quoted strings, on the reasoning that a Tailwind
 * class can only reach the DOM from inside one. It reported the same line
 * again: JSDoc writes code spans in **backticks**, and a backtick span is
 * indistinguishable from a template literal to a regex. Two different fixes,
 * one surviving false positive, because prose about a bug looks exactly like
 * the bug.
 *
 * So comments go first, and the literal scan runs on what is left. Line
 * comments are stripped too, which would truncate a line containing `//` inside
 * a string -- a URL. No Tailwind class list contains one, and the failure mode
 * is a missed match on that line rather than a wrong measurement.
 *
 * **Its reach, stated rather than assumed.** It pairs a ground and a foreground
 * within ONE string literal, which is how every same-element instance is
 * written. It does not follow a ground on a parent element to a foreground on a
 * child -- `AiControlCenterPage.tsx:546/551` is that shape, and is measured by
 * hand in the ledger at 8.46:1. Widening this to a DOM-aware check means
 * rendering, and the thing that renders these is the a11y sweep that cannot
 * reach them.
 */
function containerTintUsages(): readonly { where: string; ground: string; tint: number; foreground: string }[] {
  const sources: Record<string, string> = import.meta.glob("./**/*.{ts,tsx}", {
    query: "?raw",
    import: "default",
    eager: true,
  });

  const found: { where: string; ground: string; tint: number; foreground: string }[] = [];
  for (const [file, raw] of Object.entries(sources)) {
    if (/\.test\.tsx?$/.test(file)) continue;
    // Comments out first, newlines preserved so reported line numbers still
    // point at the real source.
    const text = raw
      .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
      .replace(/\/\/[^\n]*/g, "");
    // Then every double-quoted, single-quoted or backtick string, with where it
    // starts. Deliberately not a parser: a Tailwind class list is always a flat
    // literal, and the failure mode of an over-broad match here is measuring
    // something harmless, not missing something dangerous.
    for (const literal of text.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g)) {
      // Truthiness, not `??`. An unmatched alternation group is `undefined` at
      // runtime but typed `string` by `RegExpExecArray`, so `??` is a check the
      // type checker calls impossible and the linter refuses -- the same trap
      // `supportContrast.test.ts` records hitting on its alpha group. `||` is
      // honest here anyway: an empty literal has no classes in it either.
      const body = literal[1] || literal[2] || literal[3] || "";
      for (const hit of body.matchAll(/\bbg-([a-z]+)-container\/(\d+)\b/g)) {
        const family = hit[1];
        if (!new RegExp(`\\btext-on-${family}-container\\b`).test(body)) continue;
        const line = text.slice(0, literal.index).split("\n").length;
        found.push({
          where: `${file.replace(/^\.\//, "")}:${String(line)}`,
          ground: `${family}-container`,
          tint: Number(hit[2]),
          foreground: `on-${family}-container`,
        });
      }
    }
  }
  return found;
}

describe("what the review tokens actually read at", () => {
  it("has a palette to measure against", () => {
    // A regex that matched nothing would make every measurement below throw --
    // or worse, quietly compare undefined roles.
    expect(Object.keys(PALETTE).length).toBeGreaterThan(20);
    expect(PALETTE["tertiary-container"]).toMatch(/^#[0-9a-f]{6}$/i);
    for (const ground of GROUNDS) expect(PALETTE[ground]).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("measures every review token that paints text on a ground, and names them", () => {
    // The guard on the measurement below, running the *same* `measurableTokens()`
    // -- so a filter that stops matching fails here too. Pinned as a list rather
    // than a count, so a token added later is measured without anybody
    // remembering to come back to this file.
    expect(measurableTokens().map((entry) => entry.path).sort()).toEqual([
      // The three actions carry their ground on `hover:`, so what is measured
      // for them is the hovered state -- which is the only state they have a
      // ground in, and the one nobody looks at.
      "action.danger",
      "action.primary",
      "action.secondary",
      "clarification",
      "conflict",
      "field.input",
      "gap",
      "provenance",
      "state.ABANDONED",
      "state.APPROVING",
      "state.CANCELLED",
      "state.DELIVERY_FAILED",
      "state.HELD_FOR_OPERATIONS",
      "state.OPEN",
      "state.SENT",
    ]);
  });

  it("clears 4.5:1 on every one of them, at the opacity it is drawn with", () => {
    const failures = measurableTokens().flatMap((entry) => {
      const ratio = measured(entry.value);
      return ratio >= 4.5 ? [] : [`${entry.path}: ${ratio.toFixed(2)}:1`];
    });
    expect(failures).toEqual([]);
  });

  it("holds the conflict notice well clear of the line, not on it", () => {
    // The token this file was added for. 4.5 is the threshold; the pairing that
    // shipped read 1.29:1, and the obvious repair -- dropping the `/40` and
    // keeping `on-tertiary-container` -- reads 4.54:1, which passes by 0.04.
    //
    // This asserts the AAA threshold instead, because of what the notice says:
    // somebody else is editing the review this associate is about to approve.
    // A margin of 0.04 on that is one palette tweak from a silent regression,
    // and the palette has been tweaked twice this run.
    expect(measured(REVIEW.conflict)).toBeGreaterThanOrEqual(7);
    // And the repair that was NOT taken still fails this bar, so the assertion
    // above is a statement about the fix rather than about the threshold.
    expect(measured("bg-tertiary-container text-on-tertiary-container")).toBeLessThan(7);
  });

  it("computes real ratios -- the helper is not agreeing with itself", () => {
    // Two known answers either side of the line...
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 1);
    // ...and the exact pairing this file was added for, still failing when it is
    // measured. Without this, a `measured()` that returned 21 for everything
    // would make the audit above green and meaningless.
    expect(measured("bg-tertiary-container/40 text-on-tertiary-container")).toBeLessThan(2);
  });
});

describe("the -container/ tint rule, across the frontend", () => {
  it("finds the tinted-container usages, and names every one", () => {
    // The guard on the grep. A regex that stopped matching would make the
    // measurement below pass over an empty list -- green, and about nothing.
    // Pinned as a full list rather than a count so that a NEW tinted container
    // paired with its `on-` role fails here first and gets measured before it
    // ships, which is the whole mechanism `review.conflict` did not have.
    expect(
      containerTintUsages()
        .map((usage) => `${usage.where}  bg-${usage.ground}/${String(usage.tint)} text-${usage.foreground}`)
        .sort(),
    //
    // `review.conflict` is deliberately ABSENT: its foreground is now
    // `on-surface`, so it is no longer the paired shape this list tracks. Its
    // disappearance from here is part of the fix.
    // Sorted as strings, so `:182` precedes `:93`.
    ).toEqual([
      // review.clarification -- 5.44:1
      "copilotTokens.ts:182  bg-secondary-container/40 text-on-secondary-container",
      // support.notice -- 5.44:1
      "copilotTokens.ts:262  bg-secondary-container/40 text-on-secondary-container",
      // support.warning -- 8.68:1
      "copilotTokens.ts:302  bg-error-container/30 text-on-error-container",
      // review.gap -- 8.68:1. The error pair IMPROVES under tint.
      "copilotTokens.ts:93  bg-error-container/30 text-on-error-container",
      // The one instance outside the token registry. 8.45:1.
      "panes/ConversationPane.tsx:240  bg-error-container/40 text-on-error-container",
    ]);
  });

  it("clears 4.5:1 at every one of them", () => {
    const failures = containerTintUsages().flatMap((usage) => {
      const ratio = Math.min(
        ...GROUNDS.map((base) =>
          contrast(PALETTE[usage.foreground], blend(PALETTE[usage.ground], PALETTE[base], usage.tint / 100)),
        ),
      );
      return ratio >= 4.5 ? [] : [`${usage.where}: ${ratio.toFixed(2)}:1`];
    });
    expect(failures).toEqual([]);
  });

  it("never tints tertiary-container under its own on- role, anywhere", () => {
    // The rule, narrowed to where it actually bites, and enforced as a SHAPE
    // rather than as a number.
    //
    // Every other family survives its own tint because the ground moves away
    // from the foreground (asserted below). `tertiary` is the inverted pair --
    // dark ground, light `on-` role -- so tinting moves the ground *towards*
    // the foreground. There is no tint of it under `on-tertiary-container` that
    // is worth having: `/40` reads 1.29:1 and even full strength is 4.54:1.
    //
    // A shape ban rather than a threshold, because the threshold test two cases
    // up would let somebody land `/12` at some passing number and reintroduce
    // the pattern the palette cannot support. This is the copy that
    // `support.attentionNotice`'s comment records nearly shipping twice.
    const offenders = tokenEntries(COPILOT_TOKENS)
      .filter(
        (entry) =>
          /\bbg-tertiary-container\/\d+\b/.test(entry.value) &&
          /\btext-on-tertiary-container\b/.test(entry.value),
      )
      .map((entry) => `${entry.path}: ${entry.value}`);
    expect(offenders).toEqual([]);
  });

  it("shows why the rule is palette-level: only tertiary degrades under tint", () => {
    // The finding this whole file encodes, asserted rather than asserted-about.
    // If a future palette change flips one of these, the reasoning in
    // `copilotTokens.ts` stops being true and somebody needs to know before
    // they copy a token on the strength of it.
    const atTint = (family: string) =>
      measured(`bg-${family}-container/40 text-on-${family}-container`);
    const solid = (family: string) => measured(`bg-${family}-container text-on-${family}-container`);

    // Light ground, dark `on-` role: a tint lightens the ground away from the
    // foreground, so the pairing gets BETTER.
    expect(atTint("error")).toBeGreaterThan(solid("error"));
    expect(atTint("secondary")).toBeGreaterThan(solid("secondary"));
    // Dark ground, light `on-` role: the same tint moves the ground TOWARDS the
    // foreground, and they meet.
    expect(atTint("tertiary")).toBeLessThan(solid("tertiary"));
    expect(atTint("tertiary")).toBeLessThan(2);
  });
});
