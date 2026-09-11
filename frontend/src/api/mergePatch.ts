/**
 * The RFC 7396 merge patch that turns one JSON document into another.
 *
 * Every configuration screen publishes through `PATCH .../domains/{key}` with
 * a merge patch, and the two editors that came first sent the edited document
 * whole under its key: `{ support_template: document }`. A merge patch merges
 * objects, so that shape can add and change entries but never remove one -- a
 * variant deleted in the editor was still in the release after publishing,
 * silently. The patch a deletion needs is `null` at that key, and computing it
 * from the loaded document and the edited one is the only way an editor that
 * does not know the schema can produce it.
 *
 * Arrays are values, not containers, exactly as the RFC says: an edited list
 * is sent whole.
 *
 * A `null` in `after` is sent as `null`, which the RFC defines as "remove the
 * key". The backend then re-defaults the field, which for every Optional
 * field the release carries is `None` -- the same value. A required field set
 * to `null` is refused by the backend's model, in its own words, as intended.
 */

export type JsonValue = string | number | boolean | null | JsonValue[] | JsonRecord;

// An interface, not a `type`: a recursive alias through a mapped type makes
// typescript-eslint's typed rules report every use as unsafe while `tsc`
// compiles it happily -- the same narrowing `DocumentEditor.JsonObject` uses.
// eslint-disable-next-line @typescript-eslint/consistent-type-definitions
export interface JsonRecord {
  [key: string]: JsonValue;
}

function isRecord(value: JsonValue): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function sameJsonValue(left: JsonValue, right: JsonValue): boolean {
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => sameJsonValue(value, right[index]))
    );
  }
  if (isRecord(left) || isRecord(right)) {
    if (!isRecord(left) || !isRecord(right)) return false;
    const keys = Object.keys(left);
    return (
      keys.length === Object.keys(right).length
      && keys.every((key) => key in right && sameJsonValue(left[key], right[key]))
    );
  }
  return Object.is(left, right);
}

/**
 * The merge patch from `before` to `after`. `{}` when nothing changed.
 *
 * A key present before and absent after becomes `null`; a key whose value is
 * itself an object on both sides recurses, so the patch names only what moved.
 * Anything that is not an object on both sides is replaced whole.
 */
export function mergePatchOf(before: JsonValue, after: JsonValue): JsonValue {
  if (!isRecord(before) || !isRecord(after)) return after;
  const patch: JsonRecord = {};
  for (const key of Object.keys(before)) {
    if (!(key in after)) patch[key] = null;
  }
  for (const [key, value] of Object.entries(after)) {
    if (!(key in before)) {
      patch[key] = value;
      continue;
    }
    const previous = before[key];
    if (isRecord(previous) && isRecord(value)) {
      const inner = mergePatchOf(previous, value);
      if (isRecord(inner) && Object.keys(inner).length > 0) patch[key] = inner;
    } else if (!sameJsonValue(previous, value)) {
      patch[key] = value;
    }
  }
  return patch;
}
