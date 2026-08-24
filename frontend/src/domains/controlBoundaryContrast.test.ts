/**
 * The boundary of a form control, at WCAG 1.4.11's 3:1.
 *
 * An input's fill is `surface` on a page whose background is also `surface`,
 * and `.premium-field` is `surface-container-lowest` on a white panel. Both are
 * literally 1.00:1, so the 1px border is the only thing that says "this is a
 * field". `outline-control` (#828d8a) exists for exactly that job and clears
 * the bar at 3.43:1 on white and 3.26:1 on `surface`.
 *
 * The token landed and the migration did not. Seventeen of forty-nine controls
 * across six files kept `outline-variant` (1.70:1) or `slate-300` (1.48:1) --
 * borders that are present in the markup and absent to anyone who needs them --
 * while thirteen used the new token. Nothing failed, because nothing was
 * looking.
 *
 * So this looks. It reads the source back the way
 * `ReturnCopilotFabrication.test.ts` does, and for the same reason: the next
 * person to hand-roll an input will do it while making something else work, and
 * a red build is a better place to catch that than a contrast audit six months
 * later.
 *
 * **Scoped to controls, not to panels.** `outline-variant` at 1.62:1 is correct
 * on a divider between two regions -- 1.4.11 governs the boundary of a
 * component, and darkening all 183 usages to satisfy 49 inputs would repaint
 * every panel in the product. This bans it on `<input>`, `<select>` and
 * `<textarea>` and nowhere else.
 */

import { describe, expect, it } from "vitest";

const RAW: Record<string, string> = import.meta.glob("./**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
});

/** Borders that cannot reach 3:1 against this product's surfaces. */
const TOO_FAINT = /\bborder-(?:outline-variant|slate-300|gray-300|zinc-300|neutral-300)(?:\/\d{1,3})?\b/;

const CONTROL = /<(input|select|textarea)\b/g;
const OPENS_ELEMENT = /<[A-Za-z]/;
const CLASS_NAME = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{([^}]{0,400}?)\})/s;

type Offender = { readonly path: string; readonly line: string; readonly token: string };

/**
 * The class string belonging to a control, not to whatever follows it.
 *
 * A control's attributes routinely span twenty lines and its handlers contain
 * `=>`, so anything that stops at the first `>` reads the wrong element -- which
 * is how the first pass over this codebase reported zero offenders. The window
 * is abandoned if another element opens before `className` does.
 */
function controlClassName(source: string, from: number): string | null {
  const window = source.slice(from, from + 2000);
  const match = CLASS_NAME.exec(window);
  if (match === null) return null;
  if (OPENS_ELEMENT.test(window.slice(0, match.index))) return null;
  // Exactly one of the three alternations matched; the other two are
  // `undefined` at runtime. The standard library types every group as `string`,
  // so without this the linter reads the check as comparing types that cannot
  // overlap and the `??` as a dead branch. The cast states the runtime truth
  // rather than working around the rule.
  const groups = match.slice(1) as readonly (string | undefined)[];
  return groups.find((group) => group !== undefined) ?? "";
}

function offenders(): readonly Offender[] {
  const found: Offender[] = [];
  for (const [rawPath, source] of Object.entries(RAW)) {
    const path = rawPath.replace(/^\.\//, "");
    if (path.includes(".test.") || path.includes(".a11y.")) continue;

    for (const match of source.matchAll(CONTROL)) {
      const className = controlClassName(source, match.index + match[0].length);
      if (className === null) continue;
      const faint = TOO_FAINT.exec(className);
      if (faint === null) continue;
      found.push({
        path,
        line: String(source.slice(0, match.index).split("\n").length),
        token: faint[0],
      });
    }
  }
  return found;
}

describe("every form control has a boundary someone can see", () => {
  it("uses no border below 3:1 on an input, select or textarea", () => {
    const failing = offenders().map(
      (row) => `${row.path}:${row.line} uses ${row.token}; use border-outline-control`,
    );
    expect(failing).toEqual([]);
  });

  it("is actually looking at controls, and finds the ones that exist", () => {
    // Without this the rule above passes on a scanner that matched nothing,
    // which is the failure mode that let seventeen of them ship.
    let controls = 0;
    for (const [rawPath, source] of Object.entries(RAW)) {
      const path = rawPath.replace(/^\.\//, "");
      if (path.includes(".test.") || path.includes(".a11y.")) continue;
      for (const match of source.matchAll(CONTROL)) {
        if (controlClassName(source, match.index + match[0].length) !== null) controls += 1;
      }
    }
    expect(controls).toBeGreaterThan(20);
  });

  it("would catch a faint border if one were introduced", () => {
    const planted = '<input className="rounded border border-outline-variant px-2" />';
    const className = controlClassName(planted, planted.indexOf("<input") + "<input".length);
    expect(className).not.toBeNull();
    expect(TOO_FAINT.test(className ?? "")).toBe(true);
  });
});
