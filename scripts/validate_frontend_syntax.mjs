import fs from "node:fs";
import path from "node:path";
import ts from "/opt/nvm/versions/node/v22.16.0/lib/node_modules/typescript/lib/typescript.js";

const root = path.resolve("frontend/src");
const failures = [];
let files = 0;

function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const candidate = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      walk(candidate);
      continue;
    }
    if (!entry.isFile() || !/\.(ts|tsx)$/.test(entry.name)) continue;
    files += 1;
    const source = fs.readFileSync(candidate, "utf8");
    const kind = entry.name.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
    const sourceFile = ts.createSourceFile(candidate, source, ts.ScriptTarget.ES2022, true, kind);
    for (const diagnostic of sourceFile.parseDiagnostics) {
      const position = diagnostic.start === undefined
        ? undefined
        : ts.getLineAndCharacterOfPosition(sourceFile, diagnostic.start);
      failures.push({
        file: path.relative(process.cwd(), candidate),
        line: position === undefined ? null : position.line + 1,
        column: position === undefined ? null : position.character + 1,
        code: diagnostic.code,
        message: ts.flattenDiagnosticMessageText(diagnostic.messageText, " "),
      });
    }
  }
}

walk(root);
const evidence = {
  stage: "Stage 4 — Frontend Syntax",
  validationLevel: "SOURCE_VALIDATED",
  command: "node scripts/validate_frontend_syntax.mjs",
  nodeVersion: process.version,
  typescriptVersion: ts.version,
  filesParsed: files,
  exitCode: failures.length === 0 ? 0 : 1,
  status: failures.length === 0 ? "PASSED" : "FAILED",
  failures,
  limitation: "This parses every TypeScript file but does not replace dependency-backed typecheck, lint, unit, build, E2E, or accessibility gates.",
};
const output = path.resolve("docs/evidence/stage4_e2e_completion/frontend_syntax_validation.json");
fs.mkdirSync(path.dirname(output), { recursive: true });
fs.writeFileSync(output, `${JSON.stringify(evidence, null, 2)}\n`);
console.log(JSON.stringify(evidence, null, 2));
process.exitCode = evidence.exitCode;
