import { sourcesHandlers } from "./handlers/sourcesHandlers";
import { browserHandlers } from "./handlers/browserHandlers";
import { graphHandlers } from "./handlers/graph";
import { associateReturnsHandlers } from "./handlers/associateReturnsHandlers";
import { configurationHandlers } from "./handlers/configurationHandlers";
import { canonicalHandlers } from "./handlers/canonicalHandlers";

export const handlers = [
  ...sourcesHandlers,
  ...browserHandlers,
  ...graphHandlers,
  ...associateReturnsHandlers,
  ...configurationHandlers,
  // Last, so a canonical route is not shadowed by a broader legacy pattern.
  ...canonicalHandlers,
];
