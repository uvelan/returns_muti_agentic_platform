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
 * The versioned paths the shell may still call, and why. Each is a record of a
 * real gap, not a relaxation: any *other* versioned call still fails.
 *
 * Order Discovery has no canonical `/api` route -- the agent is only exposed at
 * `/api/v2/order-agent/.../turns`.
 *
 * Returns Support is the same shape. Channel B's backend is mounted only at
 * `/api/v1/return-support/*`, and the Support console is a screen over that
 * existing service -- standing up a canonical router beside it would be a
 * second HTTP surface for one backend, which is the fragmentation this
 * programme is removing. The rename belongs with the consolidation wave that
 * retires the versioned prefixes, not with the screen that first needed one.
 *
 * The support-template preview is the third, and it is a gap of the same kind.
 * The versionless `/api/config` surface holds one invariant worth keeping --
 * promotion is its only mutation, because configuration changes by a release
 * moving along its lifecycle -- and a `POST` mounted there would blur that,
 * even one that renders a draft and writes nothing. So the preview sits with
 * the return-platform routers it renders for, and joins the same consolidation
 * wave as the two above rather than getting a canonical alias of its own.
 */
const ALLOWED = [
  "/api/v2/order-agent/",
  "/api/v1/return-support/",
  "/api/v1/config/support-template/",
];

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
