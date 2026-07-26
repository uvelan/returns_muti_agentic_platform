import { sourcesHandlers } from "./handlers/sourcesHandlers";
import { browserHandlers } from "./handlers/browserHandlers";
import { graphHandlers } from "./handlers/graph";

export const handlers = [
  ...sourcesHandlers,
  ...browserHandlers,
  ...graphHandlers
];
