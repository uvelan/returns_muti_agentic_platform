import { analyzerHandlers } from "./handlers/analyzerHandlers";
import { canonicalHandlers } from "./handlers/canonicalHandlers";
import { casePanelHandlers } from "./handlers/casePanelHandlers";
import { supportHandlers } from "./handlers/supportHandlers";

/**
 * Mock handlers for `npm run dev:mock` and the fixture server.
 *
 * Wave F4 deleted the legacy app, and with it the sources, browser, graph,
 * associate-returns and Data Console configuration handlers -- they mocked
 * routes that no screen calls any more.
 *
 * The case panel's set is separate because it is *stateful* -- its mutations
 * mutate a store the panel then composes from, so the review path can actually
 * be walked in `dev:mock` -- and mixing that into the stateless canonical
 * fixtures would make both harder to reason about. Each has its own contract
 * test.
 *
 * The clarification answer route was briefly a third set, because it was not in
 * the committed OpenAPI and the panel set's contract test asserts every one of
 * its routes is published. The integration pass mounted the router and
 * regeneration published the route, so it is part of the panel set now and is
 * validated against the document like the rest of it.
 */
export const handlers = [
  ...canonicalHandlers,
  ...casePanelHandlers,
  ...supportHandlers,
  ...analyzerHandlers,
];
