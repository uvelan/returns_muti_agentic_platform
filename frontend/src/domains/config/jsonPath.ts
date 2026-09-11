import type { Json, JsonObject } from "./DocumentEditor";

/**
 * Read/write a dotted or array-indexed path through a `Json` document,
 * immutably -- the small piece of plumbing every CFG-4 typed screen needs to
 * bind one primitive to one nested field of a section it did not design the
 * shape of.
 *
 * Not a general JSON-Pointer library: paths are always known statically (the
 * screens that use this write out `["return_policy", "return_method_derivation",
 * "default_method"]` themselves), so there is no parsing, no escaping, and no
 * need to support anything the four screens do not actually address.
 */

export function getPath(obj: Json, path: readonly (string | number)[]): Json {
  let current: Json = obj;
  for (const key of path) {
    if (typeof key === "number") {
      current = Array.isArray(current) ? (current[key] ?? null) : null;
    } else {
      current =
        typeof current === "object" && current !== null && !Array.isArray(current)
          ? (current[key] ?? null)
          : null;
    }
  }
  return current;
}

export function setPath(obj: Json, path: readonly (string | number)[], value: Json): Json {
  if (path.length === 0) return value;
  const [head, ...rest] = path;
  if (typeof head === "number") {
    const next = Array.isArray(obj) ? [...obj] : [];
    next[head] = rest.length === 0 ? value : setPath(next[head] ?? null, rest, value);
    return next;
  }
  const next: JsonObject =
    typeof obj === "object" && obj !== null && !Array.isArray(obj) ? { ...obj } : {};
  next[head] = rest.length === 0 ? value : setPath(next[head] ?? null, rest, value);
  return next;
}

/** A shallow copy of `source` containing only `keys` that are actually present. */
export function sliceOf(source: JsonObject, keys: readonly string[]): JsonObject {
  const slice: JsonObject = {};
  for (const key of keys) {
    if (key in source) slice[key] = source[key];
  }
  return slice;
}

export function asObject(value: Json | undefined): JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value : {};
}

export function asArray(value: Json | undefined): Json[] {
  return Array.isArray(value) ? value : [];
}

export function asString(value: Json | undefined, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

export function asNumber(value: Json | undefined, fallback = 0): number {
  return typeof value === "number" ? value : fallback;
}

export function asBoolean(value: Json | undefined, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

export function asStringArray(value: Json | undefined): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

/**
 * `"fields[3].priority"` -> `"fields.3.priority"` -- the only path convention
 * this codebase's generated forms understand internally is dot-plus-index,
 * but a backend validator is equally likely to report a pydantic-`loc`-shaped
 * bracket path. Normalising the incoming `errors` path once, before
 * matching, means both spellings reach the same field. Documented in
 * `components/forms/README.md`.
 *
 * Moved here from `DocumentEditor.tsx` (RV round 1, alongside F3): that
 * module is a component file, and `react-refresh/only-export-components`
 * refuses a plain function export alongside a component. `DocumentEditor`
 * and `runtimeSlice.ts`'s `errorsByPath` (RV F3) both import it from here.
 */
export function normalizeErrorPath(path: string): string {
  return path.replace(/\[(\d+)\]/g, ".$1");
}
