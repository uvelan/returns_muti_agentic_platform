import type { GraphExplorerPort } from "./graphExplorerPort";
import { createHttpGraphExplorerAdapter } from "./httpGraphExplorerAdapter";

export function createGraphExplorerPort(): GraphExplorerPort {
  return createHttpGraphExplorerAdapter();
}

export const graphExplorerPort = createGraphExplorerPort();
