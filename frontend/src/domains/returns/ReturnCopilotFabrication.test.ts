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
 *
 * **A third scan: the fallback position itself.**
 *
 * Neither scan above catches a *plausible* invention. `"Delivered on time"`
 * matches no shape and is on no list, and it is the more dangerous literal
 * precisely because it reads like something the platform said. What gives it
 * away is not the text but the *position*: it stands where a value goes when
 * the value is missing.
 *
 * That scan used to read `??` out of the raw text with a regex, and a reviewer
 * proved the cost of that in one line. The same fabricated string, written two
 * ways:
 *
 * ```
 * value ?? "Delivered on time"                    // 1 failed
 * value === null ? "Delivered on time" : value    // 35 passed
 * ```
 *
 * The idiom the guard did not know about was the more common of the two -- the
 * clarifications card alone has four ternaries. A regex over source text can
 * only ever recognise the spellings someone thought to write down, so the
 * fallback scan is now a **parse**: the file is handed to the TypeScript
 * parser and the tree is walked for the *position*, which both spellings share.
 * Comments come for free -- prose about a deleted literal is not a node.
 * (`fallbackFindings` is exercised below against a source that does carry each
 * form, because a scanner that finds nothing is indistinguishable from a
 * scanner that looks for nothing.)
 *
 * **What the walk covers, and what it does not.** Stated rather than implied,
 * so the next reader does not mistake it for total:
 *
 * - `x ?? "…"` and `x || "…"`.
 * - A ternary on an explicit absence test, either way round: `=== null`,
 *   `== null`, `!== null`, `=== undefined`, `typeof x === "undefined"`,
 *   `x === ""`, `x.length === 0`, and `!x`.
 * - `if (x === null) { y = "…" }`, with or without an `else`.
 * - `if (x === null) { return "…" }`, but only where the other path returns
 *   something computed -- see `statementAfter`.
 * - `function f(x = "…")` and `const { x = "…" } = obj`.
 * - A sentence split by `+` across two lines, folded before it is judged.
 *
 * Known-uncovered, each for a reason:
 *
 * - **`useState("…")` / `useRef("…")` seeds.** This domain legitimately seeds
 *   local UI state with typed tokens -- `useState<SaveStatus>("idle")`,
 *   `useState<PaneId>("conversation")`. Telling a state token from a seeded
 *   business value needs types, and this walk is syntax only; adding the two
 *   tokens to a vocabulary of *absence words* would corrupt the vocabulary to
 *   buy coverage of a weak vector (a seed is overwritten before it renders, or
 *   it reaches the screen through one of the positions above).
 * - **A ternary on bare truthiness** -- `flag ? … : "…"`. Syntactically
 *   identical to `booleanProp ? "Hide" : "More"`, which is a copy choice and
 *   not a fallback. Only explicit absence tests are read as absence.
 * - **A JSX element in the absent branch** -- `x === null ? <p>…</p> : x`. The
 *   text lives in a `JsxText` node, not a literal.
 * - **A template literal with substitutions** in the absent branch. It renders
 *   something, so it is not a bare invention; a template with *no*
 *   substitutions is treated as the string literal it is.
 * - **A fabricated value imported from outside this domain**, or assembled
 *   from parts (`"Delivered " + when`). The walk reads one file's literals.
 * - **Compound conditions** -- `x === null && y === null ? "…" : …`.
 */

import ts from "typescript";
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

/**
 * The same files, un-stripped, for the scan that parses rather than reads.
 *
 * `code()` exists to keep a regex off the commentary. A parser has no such
 * problem -- a comment produces no node -- and stripping lines first would only
 * shift every reported line number away from the one in the editor.
 */
function productionRaw(): readonly { path: string; text: string }[] {
  return Object.entries(RAW)
    .filter(([path]) => path !== GUARD)
    .map(([path, text]) => ({ path: path.replace(/^\.\//, ""), text }))
    .filter(
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

/**
 * The words this domain is allowed to put where a value should have been.
 *
 * Every entry says *the platform has not said* -- it reports an absence rather
 * than filling it. That is the whole test for membership, and it is why
 * `"Delivered on time"` can never join: it reports a delivery.
 *
 * An exact-match list is deliberate. It means a new sentence in a fallback
 * position turns this red and somebody has to look at it and decide, which is
 * the cost being bought. The first five were already sanctioned by the `??`
 * scan this replaces; the seven below were already merged, surfaced when the
 * scan learned to read ternaries and `if`s, and each was read against that test
 * and kept rather than waved through.
 */
const ABSENCE_VOCABULARY: ReadonlySet<string> = new Set([
  "", // Asserts nothing at all.
  "Pending",
  "Unavailable",
  "Support",
  "No package",
  "RMA pending",
  "-", // CandidateOrderMode's absence marker for a column with no value.
  "Support has been asked to verify this claim.", // ReturnEvaluationMode
  "This reply was empty.", // CasePanel -- names an empty reply, invents no reply
  "This reply is empty. Rebuild it before sending — Support would receive nothing.", // SupportReplyBody
  "source not recorded", // SupportReplyBody -- provenance the platform does not have
  "That could not be recorded. Nothing was sent to Support.", // ClarificationsSection
  "Ordered quantity not on the source line", // ItemSelectionMode
]);

/** One place a literal stands in for a value, whether or not it is sanctioned. */
type Fallback = {
  readonly path: string;
  readonly line: number;
  readonly idiom: string;
  readonly literal: string;
};

function unwrap(node: ts.Expression): ts.Expression {
  return ts.isParenthesizedExpression(node) ? unwrap(node.expression) : node;
}

/**
 * The text of a constant string, or `null` for anything that renders.
 *
 * `+` of two constants is folded, because a sentence split over two lines to
 * fit the formatter is still one sentence -- both for catching
 * `"Delivered " + "on time"` and for recognising that a *pair* of constant
 * branches is a choice of wording rather than a fallback.
 */
function literalOf(node: ts.Expression): string | null {
  const inner = unwrap(node);
  if (ts.isStringLiteral(inner) || ts.isNoSubstitutionTemplateLiteral(inner)) {
    return inner.text;
  }
  if (ts.isBinaryExpression(inner) && inner.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    const left = literalOf(inner.left);
    const right = literalOf(inner.right);
    return left === null || right === null ? null : left + right;
  }
  return null;
}

function isReference(node: ts.Expression): boolean {
  return (
    ts.isIdentifier(node) ||
    ts.isPropertyAccessExpression(node) ||
    ts.isElementAccessExpression(node)
  );
}

function isAbsentValue(node: ts.Expression): boolean {
  const inner = unwrap(node);
  return (
    inner.kind === ts.SyntaxKind.NullKeyword ||
    (ts.isIdentifier(inner) && inner.text === "undefined") ||
    literalOf(inner) === ""
  );
}

function isEmptyLength(node: ts.Expression, against: ts.Expression): boolean {
  const inner = unwrap(node);
  const other = unwrap(against);
  return (
    ts.isPropertyAccessExpression(inner) &&
    inner.name.text === "length" &&
    ts.isNumericLiteral(other) &&
    other.text === "0"
  );
}

/**
 * Whether a condition asks "is this missing", and which way round the answer is.
 *
 * Only *explicit* absence is read as absence. `flag ? a : b` is left alone --
 * it is indistinguishable from `isOpen ? "Hide" : "More"`, which is a choice of
 * wording, and a guard that flagged those would be a guard someone suppresses.
 */
function absenceTest(condition: ts.Expression): { absentWhenTrue: boolean } | null {
  const node = unwrap(condition);

  if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.ExclamationToken) {
    const operand = unwrap(node.operand);
    const nested = absenceTest(operand);
    if (nested !== null) {
      return { absentWhenTrue: !nested.absentWhenTrue };
    }
    return isReference(operand) ? { absentWhenTrue: true } : null;
  }

  if (!ts.isBinaryExpression(node)) {
    return null;
  }

  const operator = node.operatorToken.kind;
  const affirmed =
    operator === ts.SyntaxKind.EqualsEqualsEqualsToken ||
    operator === ts.SyntaxKind.EqualsEqualsToken;
  const negated =
    operator === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
    operator === ts.SyntaxKind.ExclamationEqualsToken;
  if (!affirmed && !negated) {
    return null;
  }

  const left = unwrap(node.left);
  const right = unwrap(node.right);
  const typeofUndefined =
    (ts.isTypeOfExpression(left) && literalOf(right) === "undefined") ||
    (ts.isTypeOfExpression(right) && literalOf(left) === "undefined");

  if (
    !typeofUndefined &&
    !isAbsentValue(left) &&
    !isAbsentValue(right) &&
    !isEmptyLength(left, right) &&
    !isEmptyLength(right, left)
  ) {
    return null;
  }

  return { absentWhenTrue: affirmed };
}

/** What a branch hands back directly, split by whether it is a constant. */
type Yielded = {
  readonly assigned: readonly { node: ts.Node; text: string }[];
  readonly returned: readonly { node: ts.Node; text: string }[];
  readonly renders: boolean;
};

const YIELDS_NOTHING: Yielded = { assigned: [], returned: [], renders: false };

function yieldedBy(statement: ts.Statement | undefined): Yielded {
  if (statement === undefined) {
    return YIELDS_NOTHING;
  }
  const body = ts.isBlock(statement) ? statement.statements : [statement];
  const assigned: { node: ts.Node; text: string }[] = [];
  const returned: { node: ts.Node; text: string }[] = [];
  let renders = false;

  for (const child of body) {
    let produced: ts.Expression | undefined;
    let into = returned;
    if (ts.isReturnStatement(child)) {
      produced = child.expression;
    } else if (
      ts.isExpressionStatement(child) &&
      ts.isBinaryExpression(child.expression) &&
      child.expression.operatorToken.kind === ts.SyntaxKind.EqualsToken
    ) {
      produced = child.expression.right;
      into = assigned;
    }
    if (produced === undefined) {
      continue;
    }
    const literal = literalOf(produced);
    if (literal === null) {
      renders = true;
    } else {
      into.push({ node: produced, text: literal });
    }
  }

  return { assigned, returned, renders };
}

/**
 * What runs when the value is *present*, for an `if` with no `else`.
 *
 * An early `return` under an absence test only stands in for a value if the
 * path that skips it goes on to return the value -- which, with no `else`, is
 * the statement sitting after the `if`. Without that, the shape is a
 * validation ladder (`if (empty) return "Write your answer first."`, three
 * more refusals underneath), and a guard that could not tell those apart is a
 * guard the next person adds a suppression to.
 */
function statementAfter(node: ts.IfStatement): ts.Statement | undefined {
  const parent: ts.Node = node.parent;
  if (!ts.isBlock(parent) && !ts.isSourceFile(parent)) {
    return undefined;
  }
  const siblings = parent.statements;
  const index = siblings.indexOf(node);
  return index === -1 ? undefined : siblings[index + 1];
}

/**
 * Every position in one file where a literal stands in for a value.
 *
 * Sanctioned or not -- the vocabulary is applied by the caller, so that a test
 * can assert this walk still *sees* the domain's real fallbacks. A walk that
 * had silently stopped parsing would otherwise report a clean tree.
 */
function fallbackPositions(path: string, text: string): Fallback[] {
  const source = ts.createSourceFile(
    path,
    text,
    ts.ScriptTarget.Latest,
    true,
    path.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS,
  );
  const found: Fallback[] = [];

  const note = (node: ts.Node, idiom: string, literal: string): void => {
    found.push({
      path,
      line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
      idiom,
      literal,
    });
  };

  const visit = (node: ts.Node): void => {
    if (ts.isBinaryExpression(node)) {
      const operator = node.operatorToken.kind;
      if (
        operator === ts.SyntaxKind.QuestionQuestionToken ||
        operator === ts.SyntaxKind.BarBarToken
      ) {
        const literal = literalOf(node.right);
        if (literal !== null) {
          note(node.right, operator === ts.SyntaxKind.QuestionQuestionToken ? "??" : "||", literal);
        }
      }
    }

    if (ts.isConditionalExpression(node)) {
      const test = absenceTest(node.condition);
      if (test !== null) {
        const absent = test.absentWhenTrue ? node.whenTrue : node.whenFalse;
        const present = test.absentWhenTrue ? node.whenFalse : node.whenTrue;
        const literal = literalOf(absent);
        // Both branches literal is a choice of wording, not a fallback: no
        // value is rendered either way, so nothing is standing in for one.
        if (literal !== null && literalOf(present) === null) {
          note(absent, "ternary", literal);
        }
      }
    }

    if (ts.isIfStatement(node)) {
      const test = absenceTest(node.expression);
      if (test !== null) {
        const absent = test.absentWhenTrue ? node.thenStatement : node.elseStatement;
        const present = test.absentWhenTrue ? node.elseStatement : node.thenStatement;
        const yielded = yieldedBy(absent);
        // An assignment under an absence test is the fallback shape whether or
        // not there is an `else`: the variable already holds the real value,
        // and this is the line that overwrites it with a constant.
        for (const literal of yielded.assigned) {
          note(literal.node, "if/else assignment", literal.text);
        }
        // A `return` is only a fallback if the other path returns the value.
        const otherPath = present ?? statementAfter(node);
        if (yieldedBy(otherPath).renders) {
          for (const literal of yielded.returned) {
            note(literal.node, "if/else return", literal.text);
          }
        }
      }
    }

    if ((ts.isParameter(node) || ts.isBindingElement(node)) && node.initializer !== undefined) {
      const literal = literalOf(node.initializer);
      if (literal !== null) {
        note(
          node.initializer,
          ts.isParameter(node) ? "default parameter" : "destructuring default",
          literal,
        );
      }
    }

    ts.forEachChild(node, visit);
  };

  ts.forEachChild(source, visit);
  return found;
}

/** The positions the vocabulary does not sanction. */
function fallbackFindings(path: string, text: string): Fallback[] {
  return fallbackPositions(path, text).filter(
    (fallback) => !ABSENCE_VOCABULARY.has(fallback.literal),
  );
}

/**
 * The reviewer's probe, in every idiom the walk claims. One `it` each, so a
 * form quietly dropped from the walk fails under its own name.
 */
const PROBE = "Delivered on time";

const COVERED: readonly (readonly [string, string])[] = [
  ["a ?? fallback", `const shown = value ?? "${PROBE}";`],
  ["a || fallback", `const shown = value || "${PROBE}";`],
  ["=== null ternary", `const shown = value === null ? "${PROBE}" : value;`],
  ["!== null ternary", `const shown = value !== null ? value : "${PROBE}";`],
  ["== null ternary", `const shown = value == null ? "${PROBE}" : value;`],
  ["negation ternary", `const shown = !value ? "${PROBE}" : value;`],
  ["empty-string ternary", `const shown = value === "" ? "${PROBE}" : String(value);`],
  ["empty-length ternary", `const shown = value.length === 0 ? "${PROBE}" : value[0];`],
  [
    "typeof-undefined ternary",
    `const shown = typeof value === "undefined" ? "${PROBE}" : value;`,
  ],
  [
    "a split sentence in a ternary",
    `const shown = value === null ? "Delivered " + "on time" : value;`,
  ],
  ["if/else assignment", `let shown = value;\nif (value === null) {\n  shown = "${PROBE}";\n}`],
  [
    "if/else return",
    `function read(value) {\n  if (value === null) {\n    return "${PROBE}";\n  }\n  return value;\n}`,
  ],
  [
    "if/else return with an else",
    `function read(value) {\n  if (value !== null) {\n    return value;\n  } else {\n    return "${PROBE}";\n  }\n}`,
  ],
  ["default parameter", `function read(status = "${PROBE}") {\n  return status;\n}`],
  ["destructuring default", `const { status = "${PROBE}" } = record;`],
];

/** Shapes that must stay legal, or the guard becomes something to suppress. */
const FORGIVEN: readonly (readonly [string, string])[] = [
  ["a sanctioned absence word", `const shown = value ?? "Unavailable";`],
  ["a sanctioned word in a ternary", `const shown = value === null ? "Unavailable" : value;`],
  ["a wording choice on a flag", `const label = isOpen ? "Hide" : "More";`],
  [
    "a wording choice on an absence test",
    `const label = reply === null ? "Message to Support" : "Reply to Support";`,
  ],
  ["an empty fallback", `const shown = value === null ? "" : String(value);`],
  [
    "a wording choice split over two lines",
    `const label = value === null\n  ? "nothing has loaded "  + "yet."\n  : "this deployment has none " + "configured.";`,
  ],
  [
    "a validation ladder",
    `function check(text) {\n  if (text.length === 0) {\n    return "Write your answer first.";\n  }\n  if (text.length > 900) {\n    return "That is too long.";\n  }\n  return null;\n}`,
  ],
  ["prose in a comment", `// The pane used to say "${PROBE}" here.\nconst shown = value;`],
  ["prose in a block comment", `/* It said "${PROBE}". */\nconst shown = value;`],
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

  for (const [idiom, snippet] of COVERED) {
    it(`sees a fabricated fallback written as ${idiom}`, () => {
      expect(fallbackFindings("probe.tsx", snippet).map((found) => found.literal)).toEqual([
        PROBE,
      ]);
    });
  }

  for (const [shape, snippet] of FORGIVEN) {
    it(`leaves ${shape} alone`, () => {
      expect(fallbackFindings("probe.tsx", snippet)).toEqual([]);
    });
  }

  it("still finds this domain's real fallback positions", () => {
    // The vocabulary is not applied here. A walk that had quietly stopped
    // parsing -- a wrong script kind, an import that resolved to nothing --
    // would report a clean tree, which is what the assertion below reports on
    // a genuinely clean tree. This separates the two.
    const file = productionRaw().find((each) => each.path === "modes/ItemSelectionMode.tsx");
    expect(file).toBeDefined();

    const positions = fallbackPositions("modes/ItemSelectionMode.tsx", file?.text ?? "");
    expect(positions.length).toBeGreaterThan(3);
    expect(positions.map((found) => found.idiom)).toContain("ternary");
    expect(positions.map((found) => found.idiom)).toContain("??");
  });

  it("keeps no literal fallback on a business value", () => {
    // The construction the audit named -- a real value on one side and an
    // invented one on the other, so an empty case renders as a full one --
    // in every idiom the walk above claims to read.
    const offenders = productionRaw().flatMap((file) =>
      fallbackFindings(file.path, file.text).map(
        (found) =>
          `${found.path}:${String(found.line)}: ${found.idiom} falls back to ${JSON.stringify(found.literal)}`,
      ),
    );
    expect(offenders).toEqual([]);
  });
});
