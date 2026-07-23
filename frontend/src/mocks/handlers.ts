import { sourcesHandlers } from "./handlers/sourcesHandlers";
import { browserHandlers } from "./handlers/browserHandlers";

export const handlers = [
  ...sourcesHandlers,
  ...browserHandlers
];

