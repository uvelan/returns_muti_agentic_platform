/**
 * `PathPicker`'s autocomplete source for the Discovery screen's
 * `source_resolution.*_paths` fields: the active graph schema, read fresh
 * from `schemaReleasesApi.activeDocument()`.
 *
 * **Best-effort, not authoritative.** `document` (`SchemaDocument.document`)
 * is `Record<string, unknown>` on this client -- the schema-releases surface
 * types the release envelope, not the document's own internal shape, which
 * this domain has no model of. `PathPicker` itself is built for exactly this
 * situation: free text is always accepted regardless of what `paths` lists
 * (see `components/forms/PathPicker.tsx`'s own docstring), so a suggestion
 * list that misses real paths or offers irrelevant ones costs an operator
 * nothing but a worse autocomplete -- never a rejected edit. This walks the
 * document for string leaves that look like a dotted or camelCase field
 * path (`salesOrder.orderNumber`, `shipmentInfoEventData.trkNum`) rather than
 * asserting a schema shape this codebase does not otherwise model, so it
 * degrades to "no suggestions" cleanly if the active schema's document does
 * not happen to carry strings in that shape.
 */

const PATH_LIKE = /^[a-zA-Z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)+$/;

export function extractKnownPaths(document: Record<string, unknown>): string[] {
  const found = new Set<string>();

  function walk(value: unknown): void {
    if (typeof value === "string") {
      if (PATH_LIKE.test(value)) found.add(value);
      return;
    }
    if (Array.isArray(value)) {
      for (const item of value) walk(item);
      return;
    }
    if (typeof value === "object" && value !== null) {
      for (const item of Object.values(value)) walk(item);
    }
  }

  walk(document);
  return [...found].sort();
}
