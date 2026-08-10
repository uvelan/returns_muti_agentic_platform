import { canonicalHandlers } from "./handlers/canonicalHandlers";

/**
 * Mock handlers for `npm run dev:mock` and the fixture server.
 *
 * One set now. Wave F4 deleted the legacy app, and with it the sources,
 * browser, graph, associate-returns and Data Console configuration handlers --
 * they mocked routes that no screen calls any more.
 */
export const handlers = [...canonicalHandlers];
