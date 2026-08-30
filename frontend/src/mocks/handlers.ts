import { analyzerHandlers } from "./handlers/analyzerHandlers";
import { canonicalHandlers } from "./handlers/canonicalHandlers";
import { caseClarificationHandlers } from "./handlers/caseClarificationHandlers";
import { casePanelHandlers } from "./handlers/casePanelHandlers";

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
 * `caseClarificationHandlers` is a third set for a third reason: its route is
 * **not in the committed OpenAPI yet** -- `api/case_clarifications.py` is
 * written and tested but unmounted until the batched integration pass. The panel
 * set's contract test asserts every one of its routes is published, in both
 * directions, so folding this one in would break a check that is doing its job.
 * `caseClarifications.contract.test.ts` carries the tripwire that fires when the
 * route lands and this separation stops being necessary.
 */
export const handlers = [
  ...canonicalHandlers,
  ...casePanelHandlers,
  ...caseClarificationHandlers,
  ...analyzerHandlers,
];
