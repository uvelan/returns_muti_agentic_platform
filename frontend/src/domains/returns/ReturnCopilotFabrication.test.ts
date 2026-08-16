/**
 * The fabrication guard.
 *
 * The audit's central finding was a Copilot "wired to real endpoints and driven
 * by invented data": five of eight lifecycle modes rendered hardcoded literals
 * -- an RMA number, a tracking number, a policy code, a bay, a credit memo and
 * three currency amounts -- behind `??` fallbacks, so a screen with no data
 * looked exactly like a screen with data.
 *
 * Every one of them is deleted. This reads the source back and fails if one
 * returns, because the next person to reach for a placeholder will reach for it
 * while making something else work, and a review is a worse place to catch that
 * than a red build.
 *
 * **Two scans, deliberately different in reach.**
 *
 * The *named* literals -- the exact strings the audit found -- are banned
 * everywhere under `domains/returns`, tests and fixtures included. There is no
 * legitimate reason for `TRK-98421049281` to exist in this codebase again, and
 * a test asserting it would be a test pinning the defect.
 *
 * The *shaped* literals -- anything that looks like an RMA, a tracking number,
 * a policy code, a bay, a memo or a currency amount -- are banned in
 * non-test source only. Tests must name concrete values to assert anything at
 * all; components must not, because a component's literal is what ends up in
 * front of an associate holding a box.
 */

import { describe, expect, it } from "vitest";

/** This file. It quotes every banned literal, which is the one place that is allowed. */
const GUARD = "./ReturnCopilotFabrication.test.ts";

/**
 * Every `.ts` and `.tsx` file in this domain, as text.
 *
 * `import.meta.glob` rather than `node:fs`: the app's TypeScript project is
 * typed for the browser and has no Node types, and a guard that needed the
 * build loosened to run would be a guard nobody keeps.
 */
const RAW: Record<string, string> = import.meta.glob("./**/*.{ts,tsx}", {
  query: "?raw",
  import: "default",
  eager: true,
});

/**
 * The file with its comment lines removed.
 *
 * A comment cannot be rendered, and the deleted literals are named in the
 * prose above several of these panes on purpose -- "this used to say X" is how
 * the next reader learns why the binding looks the way it does. Scanning the
 * code and not the commentary is what lets both rules hold at once.
 */
function code(text: string): string {
  return text
    .split("\n")
    .filter((line) => !/^\s*(\{?\s*\/\*|\*\/?|\/\/)/.test(line))
    .join("\n");
}

function sources(): readonly { path: string; text: string }[] {
  return Object.entries(RAW)
    .filter(([path]) => path !== GUARD)
    .map(([path, text]) => ({ path: path.replace(/^\.\//, ""), text: code(text) }));
}

/** Everything the copilot renders, minus the tests and the fixtures they share. */
function productionSources(): readonly { path: string; text: string }[] {
  return sources().filter(
    (file) => !/\.test\.tsx?$/.test(file.path) && !file.path.startsWith("fixtures/"),
  );
}

/** The literals the audit found, verbatim. Banned everywhere in this domain. */
const AUDITED: readonly string[] = [
  "RMA-2026-78901",
  "TRK-98421049281",
  "FedEx Freight",
  "Prepaid Ground Dropoff",
  "Facility East Bay Dock",
  "Bay 14-B",
  "Tier 2 Technical Inspection",
  "POL-STD-30D",
  "CM-2026-88192",
  "149.99",
  "249.99",
  "18.75",
  "CW273354",
  "EM-9821",
  "Emerson 1.5HP Motor",
  "Central Distribution Center",
  "DEFAULT_MILESTONES",
  "DEFAULT_EVALUATION",
  "DEFAULT_SAMPLE_ITEMS",
  "DEFAULT_ITEMS",
];

/**
 * The shapes those literals had. Banned in anything that renders.
 *
 * Deliberately shape-based rather than value-based: `RMA-2026-78901` is gone,
 * and the finding is not that particular string but that a component was
 * willing to name an RMA it had not been given.
 */
const SHAPES: readonly (readonly [RegExp, string])[] = [
  [/\bRMA-[A-Z0-9]/i, "an RMA number"],
  [/\bTRK-?\d/i, "a tracking number"],
  [/\bLBL-[A-Z0-9]/i, "a label reference"],
  [/\bBOL-\d/i, "a bill-of-lading reference"],
  [/\bPOL-[A-Z0-9]/i, "a policy code"],
  [/\bCM-\d{4}-\d/i, "a credit memo reference"],
  [/\bbay\s*-?\s*\d/i, "a bay name"],
  [/\bDC-\d/i, "a facility code"],
  [/\$\s*\d/, "a currency amount"],
  [/\btoFixed\s*\(/, "a formatted currency amount"],
  [/\bC[WQ]\d{5,}\b/, "an order reference"],
  [/\bFedEx\b/i, "a carrier name"],
  [/\bUPS Ground\b/i, "a carrier service"],
];

describe("no fabricated business value survives under domains/returns", () => {
  it("reads a source tree to scan", () => {
    // A guard that silently scanned nothing would pass forever.
    const scanned = productionSources();
    expect(scanned.length).toBeGreaterThan(10);
    expect(scanned.map((file) => file.path)).toContain("ReturnCopilotPage.tsx");
    expect(scanned.map((file) => file.path)).toContain("modes/AuthorizedRmaMode.tsx");
  });

  for (const literal of AUDITED) {
    it(`has deleted ${literal}, everywhere`, () => {
      const offenders = sources()
        .filter((file) => file.text.includes(literal))
        .map((file) => file.path);
      expect(offenders).toEqual([]);
    });
  }

  for (const [shape, description] of SHAPES) {
    it(`names ${description} nowhere a pane could render it`, () => {
      const offenders = productionSources().flatMap((file) =>
        file.text
          .split("\n")
          .flatMap((line, index) =>
            shape.test(line) ? [`${file.path}:${String(index + 1)}: ${line.trim()}`] : [],
          ),
      );
      expect(offenders).toEqual([]);
    });
  }

  it("keeps no `??` literal fallback on a business value", () => {
    // The exact construction the audit named: a real value on the left and an
    // invented one on the right, so an empty case renders as a full one.
    const offenders = productionSources().flatMap((file) =>
      file.text
        .split("\n")
        .flatMap((line, index) =>
          // A quoted fallback is only permitted when it is one of the words
          // this domain uses for "the platform has not said", or the empty
          // string, which asserts nothing at all.
          /\?\?\s*"(?!Pending"|Unavailable"|Support"|No package"|RMA pending"|")/.test(line)
            ? [`${file.path}:${String(index + 1)}: ${line.trim()}`]
            : [],
        ),
    );
    expect(offenders).toEqual([]);
  });
});
