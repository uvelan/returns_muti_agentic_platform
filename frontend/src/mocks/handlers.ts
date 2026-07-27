import { sourcesHandlers } from "./handlers/sourcesHandlers";
import { browserHandlers } from "./handlers/browserHandlers";
import { graphHandlers } from "./handlers/graph";
import { associateReturnsHandlers } from "./handlers/associateReturnsHandlers";
import { configurationHandlers } from "./handlers/configurationHandlers";

export const handlers = [
  ...sourcesHandlers,
  ...browserHandlers,
  ...graphHandlers,
  ...associateReturnsHandlers,
  ...configurationHandlers,
];
