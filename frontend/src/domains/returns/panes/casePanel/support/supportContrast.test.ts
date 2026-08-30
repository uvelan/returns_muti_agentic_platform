import { describe, expect, it } from "vitest";

import { COPILOT_TOKENS } from "../../../copilotTokens";

/**
 * WCAG 1.4.3, measured off the token strings and the real palette.
 *
 * Added by the accessibility review, which found the `attentionNotice` token --
 * added by the *design critique* one gate earlier -- reading at roughly 1.3:1.
 * The cause is a trap the whole tokens file sits over: this palette's `tertiary`
 * pair is **inverted** relative to its siblings. `secondary-container` and
 * `error-container` are light grounds with dark `on-` foregrounds, so a `/40`
 * tint lightens the ground and the pairing still reads. `tertiary-container` is
 * a dark brown with a *light* `on-` role, so the same tint moves the ground
 * **towards** the foreground and the two meet in the middle. A reviewer cannot
 * see that by reading class names -- the token looks exactly like its siblings.
 *
 * ## Two things this file learned the hard way, both by injection
 *
 * **The roles are parsed out of the token, not restated here.** The first
 * version carried a table of `(token, foreground, ground, alpha)` and computed
 * from the table -- so it measured the table. Reverting `attentionNotice` to the
 * failing foreground, and giving the parking notice a foreground that fails on
 * its tint, both left every test green. A contrast test that does not read the
 * value it is testing is a contrast test of nothing.
 *
 * **There is one `measurableTokens()`, shared.** The second version had the
 * filter written twice -- once in the measurement and once in the guard that
 * proves the measurement is not empty. Injecting `() => false` into the
 * measurement left the guard's own copy intact, so the suite stayed green with
 * nothing measured. A guard that does not run the thing it is guarding is the
 * same defect one level up.
 *
 * The palette is read out of `tailwind.config.js` as text rather than mirrored:
 * a second copy of the hexes would agree with itself forever.
 */

const SUPPORT = COPILOT_TOKENS.support;

const PALETTE: Readonly<Record<string, string>> = Object.fromEntries(
  [
    ...Object.values(
      import.meta.glob("../../../../../../tailwind.config.js", {
        query: "?raw",
        import: "default",
        eager: true,
      }),
    )
      .join(" ")
      .matchAll(/"?([a-z0-9-]+)"?\s*:\s*"(#[0-9a-f]{6})"/gi),
  ].map((match) => [match[1], match[2]]),
);

/** The pane's own ground. Everything in the panel is drawn over it. */
const PANE_GROUND = "surface-container-lowest";

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

/** Every string in the group, with the path it was reached by. */
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
 * **One definition, used by the measurement and by the guard below.** Written
 * twice, the guard passes while the measurement measures nothing -- which is the
 * exact fault that got past the previous version of this file.
 */
function measurableTokens(): readonly { path: string; value: string }[] {
  return tokenEntries(SUPPORT).filter(
    (entry) => /\bbg-/.test(entry.value) && /\btext-/.test(entry.value),
  );
}

/**
 * The foreground and ground a token actually asks for, read off the token.
 *
 * A token naming a role this palette does not define is a **failure** rather
 * than a skip: a silently unresolved role is how a whole token drops out of the
 * audit while the audit reports success.
 */
function measured(token: string): number {
  const foreground = /\btext-((?:on-|outline|primary|secondary|tertiary|error)[a-z-]*)\b/.exec(
    token,
  );
  const ground = /\bbg-([a-z-]+)(?:\/(\d+))?\b/.exec(token);
  if (foreground === null) throw new Error(`token names no foreground: ${token}`);
  if (ground === null) throw new Error(`token names no ground: ${token}`);
  // `in`, not a truthiness check: the index signature types every lookup as a
  // `string`, so `!hex` never fires and an unresolved role would silently
  // become `undefined` inside the hex parser instead of naming itself here.
  if (!(foreground[1] in PALETTE)) throw new Error(`unknown role ${foreground[1]}`);
  if (!(ground[1] in PALETTE)) throw new Error(`unknown role ${ground[1]}`);
  const foregroundHex = PALETTE[foreground[1]];
  const groundHex = PALETTE[ground[1]];
  // Truthiness, not `=== undefined`: an unmatched optional group is `undefined`
  // at runtime but typed `string` by `RegExpExecArray`, so the explicit
  // comparison is one the type checker calls impossible and the linter refuses.
  const alpha = ground[2] ? Number(ground[2]) / 100 : 1;
  return contrast(foregroundHex, blend(groundHex, PALETTE[PANE_GROUND], alpha));
}

describe("what the support tokens actually read at", () => {
  it("has a palette to measure against", () => {
    // A regex that matched nothing would make every measurement below throw --
    // or worse, quietly compare undefined roles.
    expect(Object.keys(PALETTE).length).toBeGreaterThan(20);
    expect(PALETTE["tertiary-container"]).toMatch(/^#[0-9a-f]{6}$/i);
    expect(PALETTE[PANE_GROUND]).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("measures every token that paints text on a ground, and names them", () => {
    // The guard on the measurement below, and it runs the *same*
    // `measurableTokens()` -- so a filter that stops matching fails here too.
    // Pinned as a list rather than a count, so a token added later is measured
    // without anybody remembering to come back to this file.
    expect(measurableTokens().map((entry) => entry.path).sort()).toEqual([
      "attentionNotice",
      "chipTone.attention",
      "chipTone.neutral",
      "chipTone.parked",
      "notice",
      "warning",
    ]);
  });

  it("clears 4.5:1 on every one of them, at the opacity it is drawn with", () => {
    const failures = measurableTokens().flatMap((entry) => {
      const ratio = measured(entry.value);
      return ratio >= 4.5 ? [] : [`${entry.path}: ${ratio.toFixed(2)}:1`];
    });
    expect(failures).toEqual([]);
  });

  it("computes real ratios -- the helper is not agreeing with itself", () => {
    // Two known answers either side of the line...
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 1);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 1);
    // ...and the pairing this whole file was added for, still failing when it is
    // measured. Without this, a `measured()` that returned 21 for everything
    // would make the audit above green and meaningless.
    expect(measured("bg-tertiary-container/40 text-on-tertiary-container")).toBeLessThan(2);
  });
});
