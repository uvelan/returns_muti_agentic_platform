/**
 * Validate a JSON value against the committed OpenAPI document.
 *
 * **Why this exists.** `npm run contracts:check` proves the *document* and the
 * generated `.d.ts` still match the backend. Neither proves that anything we
 * hand the console at runtime -- above all an MSW handler body -- conforms to
 * the schema it claims to be serving. That gap is audit finding #14: the mock
 * handlers were shaped to satisfy the frontend rather than to mirror the
 * server, 51 tests went green, and the build could not complete a single turn
 * against the real backend.
 *
 * Types cannot close it. A handler is an object literal returned from a
 * closure; TypeScript will happily accept one that omits a required field
 * unless every handler is annotated, and an annotation is exactly the thing a
 * person editing a mock in a hurry removes. So the check is at runtime, over
 * the bytes the handler actually produces, against the schema the server
 * actually publishes.
 *
 * WHAT IT CATCHES
 * ---------------
 * - a property the schema does not declare (`additionalProperties: false`,
 *   which Pydantic's `extra="forbid"` emits on nearly every model here)
 * - a required property that is missing
 * - a wrong primitive type, including `null` where the schema forbids it
 * - a value outside a declared `enum` or `const`
 * - an array item that violates any of the above
 *
 * WHAT IT DOES NOT CATCH
 * ----------------------
 * - a *plausible but wrong* value in a field the schema types as a bare
 *   string. `StructuredAgentResponse.business_capability` and `.status` are
 *   both `str` on the wire, so `"POLICY_EVALUATION"` conforms to this document
 *   while `ResponseSafetyGuard` rejects it outright. That vocabulary lives in
 *   the active schema's `agent_policies`, not in OpenAPI, and is checked
 *   separately by `backend/tests/test_console_mock_speaks_the_agent_policy.py`.
 * - numeric bounds, string formats, patterns and `minItems`. FastAPI emits
 *   them; nothing in the console's mocks has ever drifted on one, and a
 *   half-implemented `format` check that silently passes is worse than an
 *   absent one.
 * - anything about a route no mock serves. Coverage is asserted separately, by
 *   the caller, so that a mock for an endpoint the server does not publish is
 *   itself a failure.
 */

export type JsonSchema = Record<string, unknown>;

export type OpenApiDocument = {
  readonly paths: Record<string, Record<string, unknown>>;
  readonly components: { readonly schemas: Record<string, JsonSchema> };
};

/** One conformance failure, addressed the way a reader can act on it. */
export type Violation = {
  /** JSON-pointer-ish path into the value, e.g. `data.returnRecords[0].status`. */
  readonly path: string;
  readonly message: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function resolve(schema: JsonSchema, document: OpenApiDocument): JsonSchema {
  const reference = schema.$ref;
  if (typeof reference !== "string") return schema;
  const prefix = "#/components/schemas/";
  if (!reference.startsWith(prefix)) {
    throw new Error(`Unsupported $ref: ${reference}`);
  }
  const target = document.components.schemas[reference.slice(prefix.length)];
  if (target === undefined) {
    throw new Error(`Dangling $ref: ${reference}`);
  }
  // A `$ref` sibling to other keywords is legal in OpenAPI 3.1; the generator
  // does not emit one, but resolving into the target and keeping the siblings
  // costs nothing and cannot be wrong.
  const { $ref: _ignored, ...siblings } = schema;
  return { ...target, ...siblings };
}

function typeName(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (Number.isInteger(value)) return "integer";
  return typeof value;
}

function matchesType(value: unknown, declared: string): boolean {
  switch (declared) {
    case "null":
      return value === null;
    case "array":
      return Array.isArray(value);
    case "object":
      return isRecord(value);
    case "integer":
      return typeof value === "number" && Number.isInteger(value);
    case "number":
      return typeof value === "number";
    case "string":
      return typeof value === "string";
    case "boolean":
      return typeof value === "boolean";
    default:
      // An unknown `type` is a generator change, not a mock defect. Refusing to
      // guess is the honest answer.
      throw new Error(`Unsupported schema type: ${declared}`);
  }
}

function check(
  value: unknown,
  rawSchema: JsonSchema,
  document: OpenApiDocument,
  path: string,
): Violation[] {
  const schema = resolve(rawSchema, document);
  const violations: Violation[] = [];

  const anyOf = schema.anyOf ?? schema.oneOf;
  if (Array.isArray(anyOf)) {
    const branches = anyOf.map((branch) =>
      check(value, branch as JsonSchema, document, path),
    );
    if (!branches.some((branch) => branch.length === 0)) {
      // Report the branch that came closest rather than the bare fact that
      // none matched. A union of `X | null` is how every optional block on
      // every projection is spelled, so "matches no branch" would be the only
      // thing this validator ever said about a wrong field three levels down.
      //
      // The `null` arm is skipped when the value is not null: it always
      // reports exactly one violation, always wins on count, and always says
      // the least useful thing there is to say about a present object.
      const interesting = branches.filter((_, index) => {
        if (value === null) return true;
        const branch = resolve(anyOf[index] as JsonSchema, document);
        return branch.type !== "null";
      });
      const closest = (interesting.length > 0 ? interesting : branches).reduce((best, branch) =>
        branch.length < best.length ? branch : best,
      );
      violations.push({
        path,
        message: `matches no branch of ${
          schema.anyOf === undefined ? "oneOf" : "anyOf"
        } (got ${typeName(value)}); nearest branch reports:`,
      });
      violations.push(...closest);
    }
    // A union settles the shape; the sibling keywords below belong to whichever
    // branch matched, and re-applying them here would double-report.
    return violations;
  }

  if (Array.isArray(schema.allOf)) {
    for (const branch of schema.allOf) {
      violations.push(...check(value, branch as JsonSchema, document, path));
    }
  }

  const declaredType = schema.type;
  if (typeof declaredType === "string" && !matchesType(value, declaredType)) {
    return [
      ...violations,
      { path, message: `expected ${declaredType}, got ${typeName(value)}` },
    ];
  }
  if (Array.isArray(declaredType)) {
    const options = declaredType as string[];
    if (!options.some((option) => matchesType(value, option))) {
      return [
        ...violations,
        { path, message: `expected one of ${options.join("|")}, got ${typeName(value)}` },
      ];
    }
  }

  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) {
    violations.push({
      path,
      message: `${JSON.stringify(value)} is not one of ${JSON.stringify(schema.enum)}`,
    });
  }
  if ("const" in schema && schema.const !== value) {
    violations.push({
      path,
      message: `${JSON.stringify(value)} is not ${JSON.stringify(schema.const)}`,
    });
  }

  if (Array.isArray(value) && isRecord(schema.items)) {
    value.forEach((item, index) => {
      violations.push(
        ...check(item, schema.items as JsonSchema, document, `${path}[${String(index)}]`),
      );
    });
  }

  if (isRecord(value)) {
    const properties = isRecord(schema.properties) ? schema.properties : {};
    const required = Array.isArray(schema.required) ? (schema.required as string[]) : [];

    for (const key of required) {
      if (!(key in value)) {
        violations.push({ path: `${path}.${key}`, message: "required property is missing" });
      }
    }

    for (const [key, entry] of Object.entries(value)) {
      const declared = properties[key];
      if (declared !== undefined) {
        violations.push(...check(entry, declared as JsonSchema, document, `${path}.${key}`));
        continue;
      }
      const additional = schema.additionalProperties;
      if (additional === false) {
        violations.push({
          path: `${path}.${key}`,
          message: "property is not declared by the schema",
        });
        continue;
      }
      if (isRecord(additional)) {
        violations.push(...check(entry, additional, document, `${path}.${key}`));
      }
    }
  }

  return violations;
}

/** Validate `value` against a schema (possibly a `$ref`) from `document`. */
export function validateAgainstSchema(
  value: unknown,
  schema: JsonSchema,
  document: OpenApiDocument,
  root = "$",
): Violation[] {
  return check(value, schema, document, root);
}

/**
 * The success schema a route declares, or `null` when the document has no such
 * route -- which is itself the interesting answer, so it is returned rather
 * than thrown.
 */
export function responseSchema(
  document: OpenApiDocument,
  method: string,
  openApiPath: string,
  status = "200",
): JsonSchema | null {
  const entry = document.paths[openApiPath];
  if (entry === undefined) return null;
  const operation = entry[method.toLowerCase()];
  if (!isRecord(operation)) return null;
  const responses = operation.responses;
  if (!isRecord(responses)) return null;
  const response = responses[status];
  if (!isRecord(response)) return null;
  const content = response.content;
  if (!isRecord(content)) return null;
  const json = content["application/json"];
  if (!isRecord(json)) return null;
  const schema = json.schema;
  return isRecord(schema) ? schema : null;
}

/** Render violations as one readable block for an assertion message. */
export function describeViolations(violations: readonly Violation[]): string {
  return violations.map((violation) => `  ${violation.path}: ${violation.message}`).join("\n");
}
