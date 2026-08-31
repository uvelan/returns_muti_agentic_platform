/**
 * The published document must declare every always-serialised field required.
 *
 * `CaseFactProjection`'s writer guarantees eleven fields on every response:
 * `case_repository.append_scoped_case_fact` writes every key with `None`
 * included, and `case_projection/assembly.py`'s `project_facts` binds all
 * eleven on every construction. The document said otherwise -- two required,
 * nine optional -- because in Pydantic *any* default, `= None` included, emits
 * a field as non-required. A third-party client reading that document types
 * nine fields optional and writes defensive code for an absence that cannot
 * occur, and `value !== null` stops meaning "the platform said something".
 *
 * Why this is a **data** assertion over the generated JSON, and not a type test:
 *
 *   - `frontend/src/api/cases.ts`'s `Served<T>` strips `?` regardless of what
 *     the document declares. An assertion written against the alias -- or
 *     against anything derived from it -- passes whether the schema is honest
 *     or not. It is not a weak instrument; it is a vacuous one.
 *   - `tsc` is provably invariant to this change: the only consumers go through
 *     `Served<T>`, so the generated `?` never reaches a typechecked position.
 *     A typecheck injection here would be unpassable -- any red would be red
 *     for an unrelated reason.
 *
 * So the only instrument that can distinguish the two states is the `required`
 * array in the emitted document, read as data. It runs from `contracts:check`,
 * immediately after `contracts:generate` writes the file it reads, and CI's
 * `contract drift` job runs `contracts:check` -- so this guard has a gate.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const frontendRoot = fileURLToPath(new URL("../", import.meta.url));
const documentPath = path.join(frontendRoot, "openapi", "return-platform.openapi.json");

/**
 * Schemas whose every declared property must also be required.
 *
 * Deliberately an allowlist rather than "every schema": most projections still
 * under-declare (measured: fourteen of `cases.ts`'s fifteen `Served<T>`
 * consumers), and asserting the property globally today would fail on work
 * nobody has authorised. Each entry is a schema whose Python model has had its
 * defaults removed on purpose. Add a schema here when you do the same to it;
 * `Served<T>` goes away when this list covers all fifteen.
 */
const FULLY_REQUIRED_SCHEMAS = [
  {
    name: "CaseFactProjection",
    // Named in full, not counted. A count passes if a field is renamed and a
    // second one dropped, and the point of this file is that the document says
    // what the writer actually does.
    expected: [
      "acquisitionMethod",
      "actorId",
      "agentId",
      "channel",
      "factId",
      "factName",
      "observedAt",
      "recordedAt",
      "sourceSystem",
      "supersedesFactId",
      "value",
    ],
  },
];

const document = JSON.parse(readFileSync(documentPath, "utf8"));
const schemas = document?.components?.schemas ?? {};
const failures = [];

for (const { name, expected } of FULLY_REQUIRED_SCHEMAS) {
  const schema = schemas[name];
  if (!schema) {
    failures.push(`${name}: absent from the published document`);
    continue;
  }

  const properties = Object.keys(schema.properties ?? {}).sort();
  const required = [...(schema.required ?? [])].sort();
  const sortedExpected = [...expected].sort();

  // Three separate claims, reported separately: the pin still describes the
  // model, the model still declares what the pin lists, and nothing is
  // declared-but-not-required. Collapsing them into one set comparison would
  // report a renamed field and a lost `required` entry as the same failure.
  const missingFromProperties = sortedExpected.filter((f) => !properties.includes(f));
  const undeclaredInPin = properties.filter((f) => !sortedExpected.includes(f));
  const notRequired = properties.filter((f) => !required.includes(f));

  if (missingFromProperties.length > 0) {
    failures.push(
      `${name}: pinned field(s) no longer on the schema: ${missingFromProperties.join(", ")}. ` +
        `If the rename is intended, update the pin in this file.`,
    );
  }
  if (undeclaredInPin.length > 0) {
    failures.push(
      `${name}: new propert(ies) not in the pin: ${undeclaredInPin.join(", ")}. ` +
        `Add them here and confirm the writer populates them on every response.`,
    );
  }
  if (notRequired.length > 0) {
    failures.push(
      `${name}: declared but not required: ${notRequired.join(", ")}. ` +
        `In Pydantic any default -- '= None' included -- emits a field as ` +
        `non-required. Declare it 'X | None' with NO default to get ` +
        `required-and-nullable, which is what the writer guarantees.`,
    );
  }
}

if (failures.length > 0) {
  console.error("The published document under-declares always-serialised fields:\n");
  for (const failure of failures) {
    console.error(`  - ${failure}`);
  }
  console.error(`\nDocument: ${documentPath}`);
  process.exit(1);
}

const pinned = FULLY_REQUIRED_SCHEMAS.map((s) => `${s.name} (${s.expected.length})`).join(", ");
console.log(`Fully-required schemas verified against the published document: ${pinned}`);
