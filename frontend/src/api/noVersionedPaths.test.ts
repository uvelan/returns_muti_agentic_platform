import { expect, it } from "vitest";

/**
 * The shell talks to the canonical versionless surface and nothing else.
 *
 * This became true only when the runtime-config route moved off `/api/v1`.
 * Until then the four-domain shell could not boot without a versioned legacy
 * route -- a dependency that survived Waves F1, F2 and F5 precisely because
 * nobody was asserting its absence, only describing it in a README as a "known
 * leftover".
 *
 * Generated types are excluded: `return-platform.d.ts` mirrors the whole
 * backend contract, which legitimately still serves 63 versioned paths to
 * clients that are not this shell.
 *
 * Sources arrive through `import.meta.glob` rather than `node:fs` so the test
 * runs under the same browser-targeted tsconfig as the code it inspects --
 * reading the tree from disk would mean adding `@types/node` to the app project
 * for one test.
 */
const sources: Record<string, string> = import.meta.glob(
  ["../**/*.ts", "../**/*.tsx", "!../api/generated/**"],
  { query: "?raw", import: "default", eager: true },
);

/**
 * Blanks comments while preserving line numbering, so a comment that discusses
 * a versioned path -- including the one above -- is not a violation.
 * Comment-aware rather than quote-aware: the first attempt at this test looked
 * for quoted paths and tripped on its own backticked prose.
 */
function withoutComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "))
    .replace(/(^|[^:])\/\/.*$/gm, (_line, prefix: string) => prefix);
}

/**
 * The one versioned path the shell may still call, and why.
 *
 * Order Discovery has no canonical `/api` route -- the agent is only exposed at
 * `/api/v2/order-agent/.../turns`. Listed as a named exception rather than
 * relaxing the rule, so this stays a one-line record of a real gap and any
 * *other* versioned call still fails.
 */
const ALLOWED = ["/api/v2/order-agent/"];

it("never requests a versioned API path, except the agent turn endpoint", () => {
  const offenders = Object.entries(sources).flatMap(([path, source]) =>
    withoutComments(source)
      .split("\n")
      .flatMap((line, index) =>
        /\/api\/v\d/.test(line) && !ALLOWED.some((allowed) => line.includes(allowed))
          ? [`${path}:${String(index + 1)}`]
          : [],
      ),
  );

  expect(offenders).toEqual([]);
});
